"""Opt-in ADB metadata snapshots; no discovery or process creation on import."""
from contextlib import contextmanager
import hashlib
import json
import re
import shlex
import subprocess
import threading
import time

from ..adb import AdbBridge, AdbDevice, AdbError, AdbUnavailable
from ..constants import BACKUP_SUFFIX, INFO_FILE_NAMES, QUEST_CUSTOMLEVELS_CANDIDATES, QUEST_PROBE_PROPS
from ..downloads import DownloadError, check_cancel
from ..songs import decode_json_bytes, parse_info_file
from .device_protocol import descriptor, snapshot_rows, report_fields, detail_fields
from .adb_metadata import CAPABILITY, READ_ROOTS, batches, decode_batch, folder_listing
from .adb_duration import remote_duration


class AdbReadOnlyProvider:
    """Only explicit read commands; one in-flight command is bounded to 10 seconds.

    Hardware serial is part of identity, so an IP/transport reused by another
    headset cannot reuse its snapshot. MTP, pairing and write APIs are absent.
    """
    def __init__(self, adb_path=None, *, bridge_factory=AdbBridge):
        self.adb_path = adb_path
        self.bridge_factory = bridge_factory
        self._io_lock = threading.Lock()

    @contextmanager
    def _operation(self, cancel):
        while not self._io_lock.acquire(timeout=.05):check_cancel(cancel)
        try:
            check_cancel(cancel)
            yield
        finally:self._io_lock.release()

    def _bridge(self, serial=''):
        try:return self.bridge_factory(self.adb_path, serial=serial, timeout=10)
        except AdbUnavailable:raise DownloadError('device_adb_path') from None

    @staticmethod
    def _serial(value):
        if not isinstance(value,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,255}',value) is None:
            raise DownloadError('device_rejected')
        return value

    @staticmethod
    def _run(bridge, args, cancel, deadline, *, binary=False):
        check_cancel(cancel)
        remaining = deadline-time.monotonic()
        if remaining<=0:raise DownloadError('device_timeout')
        try:result = bridge.run(args, timeout=min(10,remaining), binary=binary)
        except AdbUnavailable:raise DownloadError('device_adb_path') from None
        except AdbError as exc:
            check_cancel(cancel)
            if isinstance(exc.__cause__, (TimeoutError,subprocess.TimeoutExpired)) or '超时' in str(exc):
                raise DownloadError('device_timeout') from None
            text = str(exc).lower()
            code = 'device_unauthorized' if 'unauthorized' in text else 'device_disconnected' if any(
                word in text for word in ('device offline','device not found','no devices','disconnected')) else 'device_command'
            raise DownloadError(code) from None
        check_cancel(cancel)
        if time.monotonic()>deadline:raise DownloadError('device_timeout')
        return result

    def _devices(self, cancel, deadline):
        text = self._run(self._bridge(),['devices','-l'],cancel,deadline)
        if not isinstance(text,str) or len(text)>1024*1024:raise DownloadError('device_protocol')
        devices = []
        for line in text.splitlines():
            if not line.strip() or line.startswith(('List of devices','*')):continue
            parts = line.split()
            if len(parts)<2:raise DownloadError('device_protocol')
            serial = self._serial(parts[0])
            if any(item.serial==serial for item in devices):raise DownloadError('device_protocol')
            devices.append(AdbDevice(serial,parts[1]))
        if len(devices)>16:raise DownloadError('device_protocol')
        return devices

    def _identify(self, serial, cancel, deadline):
        bridge = self._bridge(self._serial(serial))
        keys = (*QUEST_PROBE_PROPS,'ro.serialno')
        command = '; '.join('getprop '+key for key in keys)
        text = self._run(bridge,['shell',command],cancel,deadline)
        values = text.splitlines()
        # Preserve blank property positions; never substitute devices -l model.
        if len(values)!=len(keys):raise DownloadError('device_rejected')
        props = dict(zip(keys,(value.strip() for value in values)))
        hardware = self._serial(props['ro.serialno'])
        item = AdbDevice(serial,'device',props['ro.product.model'],
                         props['ro.product.device'] or props['ro.product.name'],
                         props['ro.product.brand'] or props['ro.product.manufacturer'])
        if not item.is_quest:raise DownloadError('device_rejected')
        return bridge,item,hardware

    def _root(self, bridge, root, cancel, deadline):
        if root not in READ_ROOTS:raise DownloadError('device_rejected')
        quoted = shlex.quote(root)
        text = self._run(bridge,['shell',f'if [ -d {quoted} ]; then readlink -f {quoted}; fi'],cancel,deadline)
        canonical = text.strip()
        if canonical and canonical not in READ_ROOTS:raise DownloadError('device_rejected')
        return canonical

    @staticmethod
    def _descriptor(item, hardware, root):
        return descriptor(dict(kind='adb',device_id=json.dumps([item.serial,hardware],separators=(',',':')),
            root=root,label=f'{item.model} · {item.serial} · {root}',state=item.state,
            model=item.model,codename=item.codename,brand=item.brand,identity_verified=True))

    def list_devices(self, cancel):
        with self._operation(cancel):return self._list_devices(cancel)

    def _list_devices(self, cancel):
        deadline = time.monotonic()+120
        choices = {};verified = False;rejected = False
        candidates = self._devices(cancel,deadline)
        for candidate in candidates:
            if not candidate.usable:continue
            try:bridge,item,hardware = self._identify(candidate.serial,cancel,deadline)
            except DownloadError as exc:
                if exc.code=='device_rejected':rejected = True;continue
                raise
            verified = True
            for root in QUEST_CUSTOMLEVELS_CANDIDATES:
                canonical = self._root(bridge,root,cancel,deadline)
                if canonical:
                    value = self._descriptor(item,hardware,canonical)
                    choices[value['identity']] = value
        if not choices:
            code = 'device_roots' if verified else 'device_unauthorized' if any(
                item.state=='unauthorized' for item in candidates) else 'device_offline' if any(
                not item.usable for item in candidates) else 'device_rejected' if rejected else None
            if code:raise DownloadError(code)
        return list(choices.values())

    def read_library(self, selected, cancel, *, durations=True):
        with self._operation(cancel):return self._read_library(selected,cancel,durations=durations)

    def read_metadata(self, selected, cancel):
        return self.read_library(selected,cancel,durations=False)

    def read_assets(self, selected, items, cancel):
        from .adb_assets import read_assets

        with self._operation(cancel):return read_assets(self,selected,items,cancel)

    def _read_library(self, selected, cancel, *, durations):
        selected = descriptor(selected)
        if selected['kind']!='adb':raise DownloadError('device_rejected')
        try:identity = json.loads(selected['device_id'])
        except (ValueError,TypeError):raise DownloadError('device_rejected') from None
        if not isinstance(identity,list) or len(identity)!=2:raise DownloadError('device_rejected')
        serial,hardware = identity
        self._serial(serial);self._serial(hardware)
        if selected['root'] not in READ_ROOTS:raise DownloadError('device_rejected')
        deadline = time.monotonic()+120
        if not any(item.serial==serial and item.usable for item in self._devices(cancel,deadline)):
            raise DownloadError('device_disconnected')
        bridge,item,fresh_hardware = self._identify(serial,cancel,deadline)
        root = self._root(bridge,selected['root'],cancel,deadline)
        if not root:raise DownloadError('device_roots')
        fresh = self._descriptor(item,fresh_hardware,root)
        if fresh['identity']!=selected['identity']:raise DownloadError('device_rejected')
        quoted_root = shlex.quote(root)
        listing = self._run(bridge,['shell',f'cd {quoted_root} && ls -1p'],cancel,deadline,binary=True)
        folders,skipped = folder_listing(listing)
        # Validate all names before interpolating even a quoted remote path.
        snapshot_rows([dict(folder=name,title='',author='',error='',duration=0) for name in folders])
        folders = sorted((name for name in folders if not name.startswith('.') and not name.endswith(BACKUP_SUFFIX)),key=str.lower)
        rows = []
        for folder,raw in self._contents(bridge,root,folders,cancel,deadline):
            data = decode_json_bytes(raw) if raw else None
            meta = parse_info_file(data,folder)
            if durations and meta.duration<=0 and not meta.parse_error:
                meta.duration = remote_duration(self._run,bridge,root,folder,data,cancel,deadline)
            rows.append(dict(folder=folder,title=meta.title,author=meta.author,error=meta.parse_error,
                duration=meta.duration,info_hash=hashlib.sha256(raw).hexdigest(),
                **report_fields(meta),**detail_fields(meta)))
        # Recheck transport, hardware and root before publishing any rows.
        if not any(value.serial==serial and value.usable for value in self._devices(cancel,deadline)):
            raise DownloadError('device_disconnected')
        _,item,fresh_hardware = self._identify(serial,cancel,deadline)
        canonical = self._root(bridge,root,cancel,deadline)
        if not canonical:raise DownloadError('device_roots')
        fresh = self._descriptor(item,fresh_hardware,canonical)
        if fresh['identity']!=selected['identity']:raise DownloadError('device_rejected')
        result = dict(device=fresh,connected=True,rows=snapshot_rows(rows))
        if skipped:result['skipped_names'] = skipped
        return result

    def _contents(self, bridge, root, folders, cancel, deadline):
        if len(folders)>=8:
            capability = self._run(bridge,['exec-out',CAPABILITY],cancel,deadline,binary=True)
            if capability==b'QSC_BATCH':
                for names,command in batches(root,folders):
                    raw = self._run(bridge,['exec-out',command],cancel,deadline,binary=True)
                    for pair in decode_batch(raw,names):
                        check_cancel(cancel)
                        yield pair
                return
            if capability!=b'QSC_SINGLE':raise DownloadError('device_protocol')
        quoted_root = shlex.quote(root)
        for folder in folders:
            directory = shlex.quote(root+'/'+folder)
            names = ' '.join(shlex.quote(name) for name in INFO_FILE_NAMES)
            command = (f'[ ! -L {directory} ] && [ -d {directory} ] && '
                f'[ "$(readlink -f {quoted_root})" = {quoted_root} ] && '
                f'for name in {names}; do '
                f'if [ -f {directory}/"$name" ] && [ ! -L {directory}/"$name" ]; then '
                f'head -c 1048577 {directory}/"$name"; exit; fi; done')
            raw = self._run(bridge,['exec-out',command],cancel,deadline,binary=True)
            if not isinstance(raw,bytes) or len(raw)>1048576:raise DownloadError('device_protocol')
            yield folder,raw
