"""Read-only, identity-bound audio and playlist lookup; never writes to Quest."""
import hashlib
import json
import re
import shlex
import subprocess
import time

from ..constants import INFO_FILE_NAMES
from ..downloads import DownloadError, check_cancel
from ..playlist import difficulty_files
from ..songs import decode_json_bytes
from ..song_duration import audio_filename
from .adb_assets import _binding, _name
from .adb_identity import END, MAP_LIMIT, SONG_LIMIT, map_command, map_guard
from .device_protocol import descriptor

AUDIO_LIMIT = 64*1024*1024
INDEX_BUDGET = 256*1024*1024


def bound(provider, selected, cancel, deadline):
    selected = descriptor(selected)
    if selected['kind'] != 'adb':raise DownloadError('device_rejected')
    serial, hardware = json.loads(selected['device_id'])
    provider._serial(serial);provider._serial(hardware)
    bridge, root = _binding(provider,selected,serial,hardware,cancel,deadline)
    return selected, serial, hardware, bridge, root


def valid_item(item):
    if not isinstance(item,dict) or not _name(item.get('folder')) or not re.fullmatch('[0-9a-f]{64}',item.get('info_hash','')):
        raise DownloadError('device_protocol')


def read_audio(provider, selected, item, cancel):
    valid_item(item);deadline=time.monotonic()+90
    selected,serial,hardware,bridge,root=bound(provider,selected,cancel,deadline)
    raw=dict(provider._contents(bridge,root,[item['folder']],cancel,deadline))[item['folder']]
    if hashlib.sha256(raw).hexdigest()!=item['info_hash']:raise DownloadError('device_protocol')
    name=audio_filename(decode_json_bytes(raw))
    if not name:raise DownloadError('local_audio')
    _path,command=map_command(root+'/'+item['folder'],name,AUDIO_LIMIT)
    payload=provider._run(bridge,['exec-out',command],cancel,deadline,binary=True)
    head,sep,body=payload.partition(b'\n')
    if not sep or not re.fullmatch(rb'QSV:[1-9][0-9]{0,8}',head):raise DownloadError('device_protocol')
    size=int(head[4:])
    if size>AUDIO_LIMIT or len(body)!=size+len(END) or not body.endswith(END):raise DownloadError('device_protocol')
    again=dict(provider._contents(bridge,root,[item['folder']],cancel,deadline))[item['folder']]
    if again!=raw:raise DownloadError('device_protocol')
    _binding(provider,selected,serial,hardware,cancel,deadline);check_cancel(cancel)
    return body[:size],name


def index_command(root, item, info):
    """Hash on the device, transferring only digests. This is NOT write verification."""
    directory=root+'/'+item['folder'];d=shlex.quote(directory)
    names=difficulty_files(decode_json_bytes(info))
    if not names or len(names)>64:raise DownloadError('device_protocol')
    # Match the same first Info variant used by _contents and bind its exact bytes.
    variants=' '.join(shlex.quote(n) for n in INFO_FILE_NAMES)
    code=f'for name in {variants}; do if [ -f {d}/"$name" ] && [ ! -L {d}/"$name" ]; then break; fi; done; '
    code+=f'info={d}/"$name"; [ ! -L {d} ] && [ "$(readlink -f {d})" = {d} ] || exit 1; '
    code+=f'[ "$(sha256sum "$info" | cut -d " " -f1)" = {item["info_hash"]} ] || exit 1; '
    paths=[];stats=[];code+=f'total={len(info)}; '
    for i,name in enumerate(names):
        path,guard=map_guard(directory,name);q=shlex.quote(path);paths.append(q);stats.append((i,q,guard))
        code+=guard+f's{i}=$(stat -c "%i:%s:%Y" {q}) || exit 1; n=$(stat -c %s {q}) || exit 1; '
        code+='case "$n" in \'\'|*[!0-9]*) exit 1;; esac; '
        code+=f'[ "$n" -gt 0 ] && [ "$n" -le {MAP_LIMIT} ] || exit 1; total=$((total+n)); '
    code+=f'[ "$total" -le {SONG_LIMIT} ] || exit 1; '
    code+='digest=$(cat "$info" '+' '.join(paths)+' | sha1sum | cut -d " " -f1) || exit 1; '
    for i,q,guard in stats:code+=guard+f'[ "$s{i}" = "$(stat -c "%i:%s:%Y" {q})" ] || exit 1; '
    code+=f'[ "$(sha256sum "$info" | cut -d " " -f1)" = {item["info_hash"]} ] || exit 1; '
    code+='printf "%s:%s\\n" "$total" "$digest"'
    if len(code.encode())>24000:raise DownloadError('device_protocol')
    return code


def read_playlist_index(provider, selected, items, cancel):
    if not isinstance(items,list) or len(items)>2000:raise DownloadError('device_protocol')
    for item in items:valid_item(item)
    deadline=time.monotonic()+120;used=0;result={};unknown=0
    selected,serial,hardware,bridge,root=bound(provider,selected,cancel,deadline)
    raw_info=dict(provider._contents(bridge,root,[v['folder'] for v in items],cancel,deadline))
    pending=[]
    def flush():
        nonlocal used,unknown
        if not pending:return
        code='; '.join('('+command.replace('printf "%s:%s\\n"',f'printf "IDX:{i}:%s:%s\\n"')+'); true' for i,(_item,command) in enumerate(pending))
        # CreateProcess counts the quoted Windows command line, not UTF-8 bytes.
        # Leave room for the configured adb executable and transport arguments.
        if len(subprocess.list2cmdline(['adb','-s',serial,'shell',code]))>30000:
            unknown+=len(pending);pending.clear();return
        try:
            output=provider._run(bridge,['shell',code],cancel,deadline)
        except DownloadError as exc:
            check_cancel(cancel)
            if exc.code in ('device_disconnected','device_timeout','device_rejected'):raise
            output=''
        seen=set()
        if not isinstance(output,str) or len(output)>4096:raise DownloadError('device_protocol')
        for line in output.splitlines():
            if not line:continue
            match=re.fullmatch(r'IDX:([0-3]):([1-9][0-9]{0,7}):([0-9a-f]{40})',line)
            if not match:raise DownloadError('device_protocol')
            index,size,digest=int(match[1]),int(match[2]),match[3]
            if index>=len(pending) or index in seen or size>SONG_LIMIT:raise DownloadError('device_protocol')
            seen.add(index);used+=size;result.setdefault(digest,pending[index][0]['folder'])
        unknown+=len(pending)-len(seen);pending.clear()
    for item in items:
        check_cancel(cancel)
        if used+(len(pending)+1)*SONG_LIMIT>INDEX_BUDGET:
            flush()
            if used+SONG_LIMIT>INDEX_BUDGET:unknown+=1;continue
        try:
            raw=raw_info[item['folder']]
            if hashlib.sha256(raw).hexdigest()!=item['info_hash']:raise DownloadError('device_protocol')
            command=index_command(root,item,raw)
            if pending and (len(pending)==4 or sum(len(v[1].encode()) for v in pending)+len(command.encode())>12000):flush()
            pending.append((item,command))
        except DownloadError:
            check_cancel(cancel);unknown+=1
    flush()
    _binding(provider,selected,serial,hardware,cancel,deadline)
    return dict(identity=selected['identity'],matches=result,unknown=unknown,bytes=used)
