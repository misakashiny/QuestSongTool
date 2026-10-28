"""Explicit bounded map-content verification; no audio or remote writes."""
import hashlib
import json
from pathlib import PurePosixPath
import re
import shlex
import time

from ..downloads import DownloadError, check_cancel, safe_relative
from ..playlist import difficulty_files
from ..songs import decode_json_bytes
from .adb_assets import _binding, _name
from .adb_metadata import INFO_LIMIT, READ_ROOTS
from .device_protocol import descriptor

MAP_LIMIT = 16*1024*1024
SONG_LIMIT = 32*1024*1024
END = b'\n:QSV_END:\n'


def map_guard(directory, name):
    relative = safe_relative(name)
    parts = PurePosixPath(relative.as_posix()).parts
    if not parts or any(any(ord(c)<32 for c in part) for part in parts):
        raise DownloadError('device_protocol')
    path = directory+'/'+relative.as_posix()
    q = shlex.quote(path);d = shlex.quote(directory)
    guard = f'[ ! -L {d} ] && [ "$(readlink -f {d})" = {d} ] && '
    for index in range(len(parts)):
        guard += f'[ ! -L {shlex.quote(directory+"/"+"/".join(parts[:index+1]))} ] && '
    guard += f'[ -f {q} ] && [ "$(readlink -f {q})" = {q} ] || exit 1; '
    return path, guard


def map_command(directory, name, limit=MAP_LIMIT):
    path, guard = map_guard(directory, name)
    q = shlex.quote(path)
    command = (guard+f'before=$(stat -c "%i:%s:%Y" {q}) || exit 1; '+f'n=$(stat -c %s {q}) || exit 1; '
               'case "$n" in \'\'|*[!0-9]*) exit 1;; esac; '
               f'[ "$n" -gt 0 ] && [ "$n" -le {limit} ] || exit 1; '
               'printf \'QSV:%s\\n\' "$n"; '+f'head -c {limit+1} {q} || exit 1; '+
               guard+f'after=$(stat -c "%i:%s:%Y" {q}) || exit 1; [ "$before" = "$after" ] || exit 1; '+"printf '\\n:QSV_END:\\n'")
    if len(command.encode('utf-8'))>24000:raise DownloadError('device_protocol')
    return path, command


def decode_map(raw):
    head, sep, body = raw.partition(b'\n')
    if not sep or not re.fullmatch(rb'QSV:[1-9][0-9]{0,7}',head):raise DownloadError('device_protocol')
    size = int(head[4:])
    if size>MAP_LIMIT or len(body)!=size+len(END) or not body.endswith(END):raise DownloadError('device_protocol')
    data = body[:size]
    if not isinstance(decode_json_bytes(data),dict):raise DownloadError('device_protocol')
    return data


def read_identities(provider, selected, items, cancel):
    selected = descriptor(selected)
    if selected['kind']!='adb' or selected['root'] not in READ_ROOTS:raise DownloadError('device_rejected')
    identity = json.loads(selected['device_id'])
    if not isinstance(identity,list) or len(identity)!=2:raise DownloadError('device_rejected')
    serial, hardware = identity;provider._serial(serial);provider._serial(hardware)
    if not isinstance(items,list) or len(items)>4:raise DownloadError('device_protocol')
    for item in items:
        if not _name(item.get('folder')) or not re.fullmatch('[0-9a-f]{64}',item.get('info_hash','')):
            raise DownloadError('device_protocol')
    deadline = time.monotonic()+60;budget = SONG_LIMIT*2
    bridge, root = _binding(provider,selected,serial,hardware,cancel,deadline)
    result = []
    for item in items:
        check_cancel(cancel);row = dict(folder=item['folder'],state='unknown')
        try:
            if budget<=2*INFO_LIMIT:raise DownloadError('limit')
            raw = dict(provider._contents(bridge,root,[item['folder']],cancel,deadline))[item['folder']]
            if len(raw)>INFO_LIMIT or hashlib.sha256(raw).hexdigest()!=item['info_hash']:raise DownloadError('device_protocol')
            budget -= 2*len(raw)
            info = decode_json_bytes(raw);names = difficulty_files(info) if isinstance(info,dict) else []
            if not names or len(names)>64:raise DownloadError('device_protocol')
            digest = hashlib.sha1(raw);used = len(raw);hashes = []
            for name in names:
                path, command = map_command(root+'/'+item['folder'],name,min(MAP_LIMIT,SONG_LIMIT-used,budget//2))
                data = decode_map(provider._run(bridge,['exec-out',command],cancel,deadline,binary=True))
                used += len(data);budget -= 2*len(data)
                if used>SONG_LIMIT or budget<0:raise DownloadError('limit')
                digest.update(data);hashes.append((path,name,len(data),hashlib.sha256(data).hexdigest()))
            # Confirm each map is still the content just read before publishing.
            for path, name, size, expected in hashes:
                _path, guard = map_guard(root+'/'+item['folder'],name)
                q = shlex.quote(path)
                command = guard+f'[ "$(stat -c %s {q})" = {size} ] || exit 1; before=$(stat -c "%i:%s:%Y" {q}) || exit 1; sha256sum {q} || exit 1; '+guard+f'after=$(stat -c "%i:%s:%Y" {q}) || exit 1; [ "$before" = "$after" ]'
                output = provider._run(bridge,['shell',command],cancel,deadline)
                if output.split(maxsplit=1)[0]!=expected:raise DownloadError('device_protocol')
            again = dict(provider._contents(bridge,root,[item['folder']],cancel,deadline))[item['folder']]
            if again!=raw:raise DownloadError('device_protocol')
            row.update(state='verified',digest=digest.hexdigest())
        except (DownloadError, ValueError, KeyError, IndexError, TypeError):
            check_cancel(cancel)
        result.append(row)
    _binding(provider,selected,serial,hardware,cancel,deadline);check_cancel(cancel)
    return {'identity':selected['identity'],'items':result,'bytes':SONG_LIMIT*2-budget}
