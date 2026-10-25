"""Bounded on-demand Quest artwork/duration; plain data only, no Qt objects.

The provider owns the I/O lock. Call this function while holding that lock.
"""
import hashlib
import io
import json
import re
import shlex
import time

from PIL import Image, ImageOps

from ..downloads import DownloadError, check_cancel
from ..song_duration import audio_filename
from ..songs import decode_json_bytes, parse_info_file
from .adb_duration import remote_duration
from .adb_metadata import COMMAND_LIMIT, INFO_LIMIT, READ_ROOTS
from .device_protocol import descriptor, snapshot_rows


COVER_LIMIT = 5*1024*1024
ASSET_COUNT = 4
_COVER_END = b'\n:QSA_END:\n'
_LOCAL_FAILURES = ('device_command', 'device_protocol')


def _name(value):
    return (isinstance(value, str) and 0<len(value)<=2048 and value not in ('.', '..')
            and not any(char in value for char in '/\\')
            and not any(ord(char)<32 for char in value))


def _binding(provider, selected, serial, hardware, cancel, deadline):
    """Revalidate transport, hardware identity and canonical root each time."""
    check_cancel(cancel)
    if not any(item.serial==serial and item.usable for item in provider._devices(cancel, deadline)):
        raise DownloadError('device_disconnected')
    bridge, item, fresh_hardware = provider._identify(serial, cancel, deadline)
    root = provider._root(bridge, selected['root'], cancel, deadline)
    if not root:raise DownloadError('device_roots')
    fresh = descriptor(provider._descriptor(item, fresh_hardware, root))
    if fresh_hardware!=hardware or fresh['identity']!=selected['identity']:
        raise DownloadError('device_rejected')
    return bridge, root


def _cover_command(root, folder, name):
    """At most COVER_LIMIT+1 image bytes; no remote files or directory walk."""
    directory=shlex.quote(root+'/'+folder)
    path=shlex.quote(root+'/'+folder+'/'+name)
    quoted_root=shlex.quote(root)
    guard=(f'[ "$(readlink -f {quoted_root})" = {quoted_root} ] && '
           f'[ ! -L {directory} ] && [ -d {directory} ] && '
           f'[ ! -L {path} ] && [ -f {path} ] || exit 1; ')
    command = (guard+f'qsa_size=$(stat -c %s {path}) || exit 1; '
            'case "$qsa_size" in \'\'|*[!0-9]*) exit 1;; esac; '
            '[ "${#qsa_size}" -le 7 ] || exit 1; '
            f'[ "$qsa_size" -gt 0 ] && [ "$qsa_size" -le {COVER_LIMIT} ] || exit 0; '
            'printf \'QSA1:%s\\n\' "$qsa_size" || exit 1; '+
            guard+f'head -c {COVER_LIMIT+1} {path} || exit 1; '+
            guard+f'qsa_after=$(stat -c %s {path}) || exit 1; '
            '[ "$qsa_after" = "$qsa_size" ] || exit 1; '
            "printf '\\n:QSA_END:\\n'")
    if len(command.encode('utf-8'))>COMMAND_LIMIT:raise DownloadError('device_protocol')
    return command


def _decode_cover(raw):
    if not isinstance(raw, bytes) or len(raw)>COVER_LIMIT+13+len(_COVER_END):return None
    header, separator, payload = raw.partition(b'\n')
    match = re.fullmatch(rb'QSA1:([1-9][0-9]{0,6})', header)
    if not separator or match is None:return None
    size = int(match[1])
    if size>COVER_LIMIT or len(payload)!=size+len(_COVER_END) or not payload.endswith(_COVER_END):return None
    return payload[:size]


def normalize_cover(data):
    """Pure Pillow normalization matching local/online 256px RGB policy.

    No process-global warning-filter mutation: oversized dimensions are rejected
    before convert/load. Root may move this pure helper to a common module.
    """
    if not isinstance(data, bytes) or not data or len(data)>COVER_LIMIT:
        raise ValueError('cover bytes')
    with Image.open(io.BytesIO(data)) as source:
        if not 0<source.width<=4096 or not 0<source.height<=4096:
            raise ValueError('cover dimensions')
        image = ImageOps.fit(source.convert('RGB'), (256, 256))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def _info(provider, bridge, root, item, cancel, deadline):
    contents = iter(provider._contents(bridge, root, [item['folder']], cancel, deadline))
    pair = next(contents, None)
    if (not isinstance(pair, tuple) or len(pair)!=2 or pair[0]!=item['folder']
            or not isinstance(pair[1], bytes) or len(pair[1])>INFO_LIMIT
            or next(contents, None) is not None):
        raise DownloadError('device_protocol')
    check_cancel(cancel)
    raw = pair[1]
    if hashlib.sha256(raw).hexdigest()!=item['info_hash']:return None
    data = decode_json_bytes(raw) if raw else None
    if not isinstance(data, dict):return None
    return data


def _asset(provider, bridge, root, item, cancel, deadline):
    need_cover, need_duration = item['need_cover'], item['need_duration']
    result = dict(folder=item['folder'], image=None, cover_done=need_cover, duration_done=need_duration)
    if not need_cover and not need_duration:return result
    try:
        data = _info(provider, bridge, root, item, cancel, deadline)
    except DownloadError as exc:
        if exc.code not in _LOCAL_FAILURES:raise
        check_cancel(cancel)
        result['error']='metadata'
        return result
    if data is None:
        result['error']='changed'
        return result
    meta = parse_info_file(data, item['folder'])
    if need_duration:
        # Fresh Info still wins over an audio-header read.
        result['duration'] = meta.duration
        if result['duration']<=0 and not meta.parse_error and _name(audio_filename(data)):
            result['duration'] = remote_duration(provider._run, bridge, root, item['folder'], data, cancel, deadline)
        check_cancel(cancel)
    if need_cover and _name(meta.cover_file):
        try:
            raw = provider._run(bridge, ['exec-out', _cover_command(root, item['folder'], meta.cover_file)],
                                cancel, deadline, binary=True)
            check_cancel(cancel)
            image = _decode_cover(raw)
            if image is not None:result['image'] = normalize_cover(image)
        except DownloadError as exc:
            if exc.code not in _LOCAL_FAILURES:raise
        except (OSError, ValueError, RuntimeError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            pass
        check_cancel(cancel)
    return result


def read_assets(provider, selected, items, cancel):
    """Read up to four snapshot-bound assets; caller serializes provider I/O."""
    check_cancel(cancel)
    selected = descriptor(selected)
    if selected['kind']!='adb' or selected['root'] not in READ_ROOTS:
        raise DownloadError('device_rejected')
    try:identity = json.loads(selected['device_id'])
    except (ValueError, TypeError):raise DownloadError('device_rejected') from None
    if not isinstance(identity, list) or len(identity)!=2:raise DownloadError('device_rejected')
    serial, hardware = identity
    provider._serial(serial)
    provider._serial(hardware)
    if not isinstance(items, (list, tuple)) or len(items)>ASSET_COUNT:
        raise DownloadError('device_protocol')
    # Validate all items before the first remote command, including legacy rows.
    snapshot_rows(list(items))
    for item in items:
        if (not _name(item['folder']) or type(item.get('need_cover')) is not bool
                or type(item.get('need_duration')) is not bool
                or not isinstance(item.get('info_hash'), str)
                or re.fullmatch(r'[0-9a-f]{64}', item['info_hash']) is None):
            raise DownloadError('device_protocol')
    if not items:return {'identity':selected['identity'], 'items':[]}
    deadline = time.monotonic()+45
    bridge, root = _binding(provider, selected, serial, hardware, cancel, deadline)
    results = []
    for item in items:
        check_cancel(cancel)
        results.append(_asset(provider, bridge, root, item, cancel, deadline))
    _binding(provider, selected, serial, hardware, cancel, deadline)
    check_cancel(cancel)
    return {'identity':selected['identity'], 'items':results}
