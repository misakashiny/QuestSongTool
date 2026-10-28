"""Explicit, identity-bound PlaylistManager reads and no-overwrite bplist writes."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
import time
import uuid

from ..downloads import DownloadError, check_cancel
from .adb_assets import _binding
from .device_protocol import descriptor
from .operation_records import save_operation
from .playlist_files import PlaylistFileClient, playlist_bytes
from .playlist_store import MAX_FILE, MAX_STORE, normalized


PLAYLIST_ROOT = '/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/PlaylistManager/Playlists'
MAX_DEVICE_LISTS = 100


def playlist_name(value):
    if (not isinstance(value, str) or not 0 < len(value) <= 240 or value.startswith('.')
            or Path(value).suffix.lower() not in ('.bplist', '.json') or value != value.strip()
            or any(ord(c) < 32 or c in '/\\' for c in value)):
        raise DownloadError('playlist_format')
    return value


def root_guard():
    path = shlex.quote(PLAYLIST_ROOT)
    return f'[ -d {path} ] && [ ! -L {path} ] && [ "$(readlink -f {path})" = {path} ]'


class QuestPlaylistClient:
    def __init__(self, provider, sandbox):
        self.provider = provider;self.root = Path(sandbox).resolve()

    def binding(self, selected, cancel, deadline):
        selected = descriptor(selected)
        if selected['kind'] != 'adb':raise DownloadError('device_rejected')
        serial, hardware = json.loads(selected['device_id'])
        bridge, _root = _binding(self.provider, selected, serial, hardware, cancel, deadline)
        reply = self.provider._run(bridge, ['shell', root_guard()+' && printf QPL_ROOT'], cancel, deadline)
        if reply != 'QPL_ROOT':raise DownloadError('playlist_device_root')
        return bridge

    def read_file(self, bridge, name, cancel, deadline):
        path = shlex.quote(PLAYLIST_ROOT+'/'+playlist_name(name))
        # Base64 gives unambiguous framing even when JSON contains line breaks.
        command = ('set -o pipefail || exit 1; '+root_guard()+f' && [ -f {path} ] && [ ! -L {path} ] || exit 1; '
                   f'qpl_size=$(stat -c %s {path}) || exit 1; '
                   'case "$qpl_size" in \'\'|*[!0-9]*) exit 1;; esac; '
                   f'[ "$qpl_size" -le {MAX_FILE} ] || exit 1; '
                   f'head -c {MAX_FILE+1} {path} | base64 || exit 1; '
                   f'[ "$(stat -c %s {path})" = "$qpl_size" ] || exit 1')
        raw = self.provider._run(bridge, ['exec-out', command], cancel, deadline, binary=True)
        if not isinstance(raw, bytes) or len(raw) > MAX_FILE*2:raise DownloadError('playlist_limit')
        try:raw = base64.b64decode(b''.join(raw.splitlines()), validate=True)
        except ValueError:raise DownloadError('playlist_format') from None
        if len(raw) > MAX_FILE:raise DownloadError('playlist_limit')
        return raw

    def search(self, request, _page, _order, cancel):
        if self.provider is None:raise DownloadError('device_rejected')
        selected = descriptor(request['device']);action = request['action']
        deadline = time.monotonic()+120
        with self.provider._operation(cancel):
            bridge = self.binding(selected, cancel, deadline)
            if action == 'list':
                path = shlex.quote(PLAYLIST_ROOT)
                command = root_guard()+f" && find {path} -maxdepth 1 -type f \\( -iname '*.bplist' -o -iname '*.json' \\) -print0"
                listing = self.provider._run(bridge, ['exec-out', command], cancel, deadline, binary=True)
                if not isinstance(listing, bytes) or len(listing) > 1024*1024:raise DownloadError('playlist_limit')
                names = []
                for value in listing.split(b'\0'):
                    if not value:continue
                    text = value.decode('utf-8', 'strict')
                    if not text.startswith(PLAYLIST_ROOT+'/'):raise DownloadError('playlist_format')
                    name = playlist_name(text[len(PLAYLIST_ROOT)+1:])
                    if name in names:raise DownloadError('playlist_format')
                    names.append(name)
                if len(names) > MAX_DEVICE_LISTS:raise DownloadError('playlist_limit')
                rows = [];failed = [];total = 0
                for name in sorted(names, key=str.casefold):
                    check_cancel(cancel)
                    try:
                        raw = self.read_file(bridge, name, cancel, deadline);total += len(raw)
                        if total > MAX_STORE:raise DownloadError('playlist_limit')
                        value = normalized(raw.decode('utf-8-sig'), Path(name).stem[:200])
                        rows.append(dict(record=value, name=name, sha256=hashlib.sha256(raw).hexdigest()))
                    except (ValueError, UnicodeError, DownloadError) as exc:
                        if getattr(exc, 'code', '') in ('cancelled', 'device_disconnected', 'playlist_limit'):raise
                        failed.append(name)
                self.binding(selected, cancel, deadline)
                return dict(action=action, rows=rows, failed=failed, path=PLAYLIST_ROOT, identity=selected['identity'])
            if action == 'pull':
                raw = self.read_file(bridge, request['name'], cancel, deadline)
                if hashlib.sha256(raw).hexdigest() != request['sha256']:raise DownloadError('delete_changed')
                record = normalized(raw.decode('utf-8-sig'))
                self.binding(selected, cancel, deadline)
                result = PlaylistFileClient(self.root).search(dict(action='remember', record=record), 0, '', cancel)
                return dict(action=action, **result)
            if action != 'push':raise DownloadError('playlist_format')
            raw = playlist_bytes(request['record']);digest = hashlib.sha256(raw).hexdigest()
            record = normalized(raw.decode('utf-8'))
            name = 'quest-songs-'+record['id'][:32]+'.bplist'
            target = PLAYLIST_ROOT+'/'+name;qtarget = shlex.quote(target)
            existing = self.provider._run(bridge, ['shell', root_guard()+f' && if [ -e {qtarget} ] || [ -L {qtarget} ]; then printf EXISTS; else printf EMPTY; fi'], cancel, deadline)
            if existing == 'EXISTS':
                if hashlib.sha256(self.read_file(bridge, name, cancel, deadline)).hexdigest() != digest:
                    raise DownloadError('playlist_target_exists')
                return dict(action=action, path=target, exists=True)
            if existing != 'EMPTY':raise DownloadError('device_command')
            help_text = self.provider._run(bridge, ['shell', 'mv --help'], cancel, deadline)
            if '-T' not in help_text or '-n' not in help_text:raise DownloadError('device_command')
            transaction = uuid.uuid4().hex;stage = PLAYLIST_ROOT+'/.quest-playlist-'+transaction
            qstage = shlex.quote(stage);payload = stage+'/playlist.bplist';qpayload = shlex.quote(payload)
            journal_dir = self.root/'playlist-transfers'
            saved = dict(device=selected, stage=stage, target=target, sha256=digest, state='intent')
            save_operation(journal_dir,transaction,saved)
            with tempfile.TemporaryDirectory(prefix='quest-playlist-') as temporary:
                local = Path(temporary)/'playlist.bplist';local.write_bytes(raw)
                if not str(local).isascii():raise DownloadError('playlist_format')
                bridge = self.binding(selected, cancel, deadline)
                created = self.provider._run(bridge, ['shell', root_guard()+f' && mkdir {qstage} && printf CREATED'], cancel, deadline)
                if created != 'CREATED':raise DownloadError('device_command')
                self.provider._run(bridge, ['push', str(local), stage+'/'], cancel, deadline)
                bridge = self.binding(selected, cancel, deadline)
                command = (root_guard()+f' && [ -d {qstage} ] && [ ! -L {qstage} ] && [ "$(readlink -f {qstage})" = {qstage} ] && '
                           f'[ -f {qpayload} ] && [ ! -L {qpayload} ] && [ "$(sha256sum {qpayload} | cut -d \' \' -f1)" = {digest} ] && '
                           f'[ ! -e {qtarget} ] && [ ! -L {qtarget} ] && mv -nT {qpayload} {qtarget} && '
                           f'[ ! -e {qpayload} ] && [ "$(sha256sum {qtarget} | cut -d \' \' -f1)" = {digest} ] && printf PUBLISHED')
                result = self.provider._run(bridge, ['shell', command], cancel, deadline)
                if result != 'PUBLISHED':raise DownloadError('device_command')
                saved['state'] = 'published';save_operation(journal_dir,transaction,saved)
                self.provider._run(bridge, ['shell', root_guard()+f' && rmdir {qstage}'], cancel, deadline)
                self.binding(selected, cancel, deadline)
            return dict(action=action, path=target, exists=False)
