"""Read-only provider contract. No default provider and no device I/O here."""
import copy
import hashlib
import json
import math
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Protocol

from ..adb import AdbDevice
from ..downloads import DownloadError, check_cancel


class ReadOnlyDeviceProvider(Protocol):
    def list_devices(self, cancel):
        """Return plain descriptors with verified model/codename/brand fields."""
        ...

    def read_library(self, device, cancel):
        """Return {device: fresh descriptor, connected: bool, rows: plain rows}."""
        ...


def descriptor(value):
    if not isinstance(value, dict):raise DownloadError('device_protocol')
    fields = ('kind','device_id','root','label','state','model','codename','brand')
    if any(not isinstance(value.get(key),str) or len(value[key])>2048 or any(ord(c)<32 for c in value[key]) for key in fields):
        raise DownloadError('device_protocol')
    item = {key:value[key] for key in fields}
    if item['kind'] not in ('adb','mtp','drive') or not item['device_id'].strip() or not item['label'].strip():raise DownloadError('device_protocol')
    if item['state']!='device':raise DownloadError('device_disconnected')
    if value.get('identity_verified') is not True or not AdbDevice(
            item['device_id'],item['state'],item['model'],item['codename'],item['brand']).is_quest:
        raise DownloadError('device_rejected')
    root = item['root']
    if item['kind']=='adb':
        path = PurePosixPath(root)
        if not root.startswith('/') or root.startswith('//') or '\\' in root or '..' in path.parts or str(path)=='/':raise DownloadError('device_protocol')
        root = str(path)
    elif item['kind']=='drive':
        path = PureWindowsPath(root)
        if not path.is_absolute() or root.startswith(('\\\\','//')) or '..' in path.parts or path==PureWindowsPath(path.anchor):raise DownloadError('device_protocol')
        root = str(path).casefold()
    else:
        # MTP identifiers are opaque Shell selectors. Do not resolve on disk.
        if not root.strip() or root!=root.strip():raise DownloadError('device_protocol')
    item['root'] = root;item['identity_verified'] = True
    encoded = json.dumps([item['kind'],item['device_id'],root],ensure_ascii=False,separators=(',',':'))
    item['identity'] = 'quest:'+hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    return item


def optional_metadata(row):
    """Validate optional report fields without changing legacy five-field rows."""
    fields = {}
    if 'bpm' in row:
        value = row['bpm']
        if type(value) not in (int,float):raise DownloadError('device_protocol')
        try:number = float(value)
        except (OverflowError,ValueError):raise DownloadError('device_protocol') from None
        if not math.isfinite(number) or number<0:raise DownloadError('device_protocol')
        fields['bpm'] = number
    if 'difficulties' in row:
        values = row['difficulties']
        if (not isinstance(values,(list,tuple)) or len(values)>128
                or any(not isinstance(value,str) or len(value)>256 or any(ord(c)<32 for c in value) for value in values)):
            raise DownloadError('device_protocol')
        fields['difficulties'] = tuple(values)
    for name,limit in (('subtitle',4096),('version',256),('cover_file',256)):
        if name not in row:continue
        value = row[name]
        if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 for c in value):
            raise DownloadError('device_protocol')
        if name=='cover_file' and (value in ('.','..') or any(c in value for c in '/\\')):
            raise DownloadError('device_protocol')
        fields[name] = value
    if 'info_hash' in row:
        value = row['info_hash']
        if not isinstance(value,str) or re.fullmatch(r'[0-9a-f]{64}',value) is None:
            raise DownloadError('device_protocol')
        fields['info_hash'] = value
    return fields


def report_fields(meta):
    """Invalid optional source metadata stays unknown, without failing the scan."""
    fields = {}
    for key,value in (('bpm',meta.bpm),('difficulties',meta.difficulties)):
        try:fields.update(optional_metadata({key:value}))
        except DownloadError:pass
    return fields


def detail_fields(meta):
    fields = {}
    for name in ('subtitle','version','cover_file'):
        try:fields.update(optional_metadata({name:getattr(meta,name)}))
        except DownloadError:pass
    return fields


def snapshot_rows(values):
    if not isinstance(values,list) or len(values)>100000:raise DownloadError('device_protocol')
    rows = [];names = set()
    for row in values:
        if not isinstance(row,dict) or any(not isinstance(row.get(key),str) for key in ('folder','title','author','error')):raise DownloadError('device_protocol')
        folder = row['folder'];duration = row.get('duration')
        if not folder or folder in ('.','..') or any(c in folder for c in '/\\\x00') or folder in names:raise DownloadError('device_protocol')
        if type(duration) not in (int,float) or not math.isfinite(duration) or duration<0:raise DownloadError('device_protocol')
        names.add(folder);rows.append({key:row[key] for key in ('folder','title','author','error','duration')} | optional_metadata(row))
    return rows


class DeviceClient:
    """Background-only adapter; provider must be explicitly injected."""
    def __init__(self, provider: ReadOnlyDeviceProvider):
        self.provider = provider

    def search(self, request, _page, _order, cancel):
        check_cancel(cancel)
        action = request['action']
        if action=='list':
            raw = self.provider.list_devices(cancel);check_cancel(cancel)
            if not isinstance(raw,list) or len(raw)>1024:raise DownloadError('device_protocol')
            choices = {};rejected = 0
            for value in raw:
                try:item = descriptor(value)
                except DownloadError:
                    rejected += 1;continue
                identity = item['identity']
                if identity in choices and choices[identity]!=item:raise DownloadError('device_protocol')
                choices[identity] = item
            return {'action':'list','devices':sorted(choices.values(),key=lambda item:(item['label'].casefold(),item['identity'])),'rejected':rejected}
        if action!='read':raise DownloadError('device_protocol')
        selected = descriptor(request['device'])
        reader = getattr(self.provider,'read_metadata',None) if request.get('metadata_only') is True else None
        deferred = callable(reader) and callable(getattr(self.provider,'read_assets',None))
        result = (reader if deferred else self.provider.read_library)(copy.deepcopy(selected),cancel);check_cancel(cancel)
        if not isinstance(result,dict) or type(result.get('connected')) is not bool:raise DownloadError('device_protocol')
        if not result['connected']:raise DownloadError('device_disconnected')
        fresh = descriptor(result.get('device'))
        if fresh['identity']!=selected['identity']:raise DownloadError('device_rejected')
        skipped = result.get('skipped_names',0)
        if type(skipped) is not int or not 0<=skipped<=100000:raise DownloadError('device_protocol')
        response = {'action':'read','identity':fresh['identity'],'rows':snapshot_rows(result.get('rows'))}
        if deferred:response['media_deferred'] = True
        if skipped:response['skipped_names'] = skipped
        return response
