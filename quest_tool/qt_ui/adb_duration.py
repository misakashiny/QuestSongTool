"""Bounded remote audio parts for duration; never decode, play or download a song."""
import re
import shlex

from ..downloads import DownloadError, check_cancel
from ..song_duration import HEAD_LIMIT, TAIL_LIMIT, audio_filename, duration_from_parts
from .adb_metadata import READ_ROOTS


_END = b'\n:QSD_END:\n'
_PARTS_SIZE = HEAD_LIMIT+TAIL_LIMIT


def _parts_command(root, folder, name):
    """Keep each path guard, but share a single bounded remote invocation."""
    directory=shlex.quote(root+'/'+folder);path=shlex.quote(root+'/'+folder+'/'+name);qr=shlex.quote(root)
    guard=(f'[ "$(readlink -f {qr})" = {qr} ] && '
           f'[ ! -L {directory} ] && [ -d {directory} ] && '
           f'[ ! -L {path} ] && [ -f {path} ] || exit 1; ')
    return (guard+f'qsd_size=$(stat -c %s {path}) || exit 1; '
            'case "$qsd_size" in \'\'|*[!0-9]*) exit 1;; esac; '
            '[ "${#qsd_size}" -le 18 ] || exit 1; '
            f'[ "$qsd_size" -gt {_PARTS_SIZE} ] || exit 0; '
            'printf \'QSD1:%s\\n\' "$qsd_size" || exit 1; '+
            guard+f'head -c {HEAD_LIMIT} {path} || exit 1; '+
            guard+f'tail -c {TAIL_LIMIT} {path} || exit 1; '+
            guard+f'qsd_after=$(stat -c %s {path}) || exit 1; '
            '[ "$qsd_after" = "$qsd_size" ] || exit 1; '
            "printf '\\n:QSD_END:\\n'")


def _decode_parts(raw):
    """Slice fixed-length binary fields; markers within audio are just bytes."""
    if not isinstance(raw,bytes) or len(raw)>_PARTS_SIZE+len(_END)+24:return None
    header,separator,payload=raw.partition(b'\n')
    match=re.fullmatch(rb'QSD1:([1-9][0-9]{0,17})',header)
    if not separator or match is None or int(match[1])<=_PARTS_SIZE:return None
    if len(payload)!=_PARTS_SIZE+len(_END) or not payload.endswith(_END):return None
    return payload[:HEAD_LIMIT],payload[HEAD_LIMIT:_PARTS_SIZE]


def remote_duration(run, bridge, root, folder, data, cancel, deadline):
    check_cancel(cancel)
    name=audio_filename(data) if isinstance(data,dict) else ''
    if root not in READ_ROOTS or any(not isinstance(value,str) or not value or value in ('.','..') or
            any(c in value for c in '/\\') or any(ord(c)<32 for c in value) for value in (folder,name)):
        return 0.0
    try:
        raw=run(bridge,['exec-out',_parts_command(root,folder,name)],cancel,deadline,binary=True)
        check_cancel(cancel)
        parts=_decode_parts(raw)
        return duration_from_parts(*parts) if parts is not None else 0.0
    except (ValueError,TypeError):return 0.0
    except DownloadError as exc:
        if exc.code in ('device_command','device_protocol'):return 0.0
        raise
