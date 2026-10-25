"""Bounded read-only shell batches and strictly framed metadata responses."""
import base64
import binascii
import shlex

from ..constants import INFO_FILE_NAMES, QUEST_CUSTOMLEVELS_CANDIDATES
from ..downloads import DownloadError

INFO_LIMIT = 1024*1024
BATCH_COUNT = 16
COMMAND_LIMIT = 12000
RESPONSE_LIMIT = 24*1024*1024
CAPABILITY = "if command -v base64 >/dev/null 2>&1 && (set -o pipefail) 2>/dev/null; then printf QSC_BATCH; else printf QSC_SINGLE; fi"
READ_ROOTS = frozenset(path for root in QUEST_CUSTOMLEVELS_CANDIDATES
                      for path in (root,root.replace('/sdcard/','/storage/emulated/0/',1)))


def folder_listing(raw):
    """Keep UTF-8 names exact; never turn undecodable bytes into another path."""
    if not isinstance(raw,(bytes,str)) or len(raw)>16*1024*1024:
        raise DownloadError('device_protocol')
    lines = raw.splitlines()
    suffix = b'/' if isinstance(raw,bytes) else '/'
    directories = [line[:-1] for line in lines if line.endswith(suffix)]
    if len(directories)>100000:raise DownloadError('device_protocol')
    names = [];skipped = 0
    for value in directories:
        try:name = value.decode('utf-8','strict') if isinstance(value,bytes) else value
        except UnicodeDecodeError:
            skipped += 1;continue
        if any(ord(c)<32 for c in name):raise DownloadError('device_protocol')
        names.append(name)
    return names,skipped


def _block(root, folder, index):
    directory = shlex.quote(root+'/'+folder)
    quoted_root = shlex.quote(root)
    names = ' '.join(shlex.quote(name) for name in INFO_FILE_NAMES)
    return (f'[ ! -L {directory} ] && [ -d {directory} ] && '
        f'[ "$(readlink -f {quoted_root})" = {quoted_root} ] || exit 1; '
        f"printf 'QSC1:{index}\\n'; "
        f'for name in {names}; do '
        f'if [ -f {directory}/"$name" ] && [ ! -L {directory}/"$name" ]; then '
        f'head -c {INFO_LIMIT+1} {directory}/"$name" | base64 || exit 1; break; fi; done; '
        "printf '\\n:QSC_END:\\n'")


def batches(root, folders):
    """Both file count and UTF-8 command bytes are bounded; no remote temp files."""
    prefix = 'set -o pipefail || exit 1; '
    current = [];blocks = []
    for folder in folders:
        block = _block(root,folder,len(current))
        proposed = prefix+'; '.join([*blocks,block])
        if current and (len(current)>=BATCH_COUNT or len(proposed.encode('utf-8'))>COMMAND_LIMIT):
            yield current,prefix+'; '.join(blocks)
            current = [];blocks = [];block = _block(root,folder,0)
        if len((prefix+block).encode('utf-8'))>COMMAND_LIMIT:raise DownloadError('device_protocol')
        current.append(folder);blocks.append(block)
    if current:yield current,prefix+'; '.join(blocks)


def decode_batch(raw, folders):
    if not isinstance(raw,bytes) or len(raw)>RESPONSE_LIMIT:raise DownloadError('device_protocol')
    frames = raw.split(b'\n:QSC_END:\n')
    if len(frames)!=len(folders)+1 or frames[-1]!=b'':raise DownloadError('device_protocol')
    result = []
    for index,(folder,frame) in enumerate(zip(folders,frames)):
        header,sep,body = frame.partition(b'\n')
        if not sep or header!=f'QSC1:{index}'.encode() or len(body)>2*INFO_LIMIT:
            raise DownloadError('device_protocol')
        try:payload = base64.b64decode(body.replace(b'\r',b'').replace(b'\n',b''),validate=True)
        except (ValueError,binascii.Error):raise DownloadError('device_protocol') from None
        if len(payload)>INFO_LIMIT:raise DownloadError('device_protocol')
        result.append((folder,payload))
    return result
