"""Portable bplist output and explicit local saves of official online playlists."""
import json
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile

from ..downloads import DownloadError, check_cancel
from .playlist_store import MAX_FILE, MAX_LISTS, STORE_LOCK, PlaylistStore, normalized
from .playlist_download import PlaylistDownloadClient


def playlist_bytes(record):
    value = dict(playlistTitle=record['title'], playlistAuthor=record['author'],
                 songs=record['songs'], image=record['cover'])
    # Keep hash/key identities, order and artwork; no embedded auto-sync URLs.
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    if len(raw) > MAX_FILE:raise DownloadError('playlist_limit')
    normalized(raw.decode('utf-8'))
    return raw


def write_playlist(path, raw, cancel):
    path = Path(path)
    if (not path.is_absolute() or path.suffix.lower() != '.bplist' or str(path).startswith(('\\\\', '//'))
            or any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))):
        raise DownloadError('playlist_format')
    check_cancel(cancel)
    file = QSaveFile(str(path))
    if not file.open(QIODevice.OpenModeFlag.WriteOnly):raise OSError('playlist open')
    if file.write(raw) != len(raw) or cancel.is_set():
        file.cancelWriting();check_cancel(cancel);raise OSError('playlist write')
    if not file.commit():raise OSError('playlist commit')


class PlaylistFileClient:
    def __init__(self, sandbox):
        self.root = Path(sandbox).resolve()
        self.store = PlaylistStore(self.root)
        self.reader = PlaylistDownloadClient(self.root)

    def search(self, request, _page, _order, cancel):
        if request['action'] == 'online':
            identity = request['id']
            if type(identity) is not int or not 0 < identity < 10**12:raise DownloadError('playlist_url')
            value = self.reader.search(dict(kind='url', source=f'https://beatsaver.com/playlists/{identity}'), 0, '', cancel)
            value = normalized(playlist_bytes(value).decode('utf-8'))
        else:value = normalized(playlist_bytes(request['record']).decode('utf-8'))
        raw = playlist_bytes(value)
        if request['action'] == 'export':
            path = Path(request['path'])
        elif request['action'] in ('online', 'remember'):
            with STORE_LOCK:
                records = self.store.load()
                if not any(row['id'] == value['id'] for row in records):records.append(value)
                if len(records) > MAX_LISTS:raise DownloadError('playlist_limit')
                self.store.save(records, cancel)
            directory = self.root/'playlists'
            if directory.is_symlink() or directory.is_junction():raise DownloadError('playlist_format')
            directory.mkdir(parents=True, exist_ok=True)
            path = directory/(value['id']+'.bplist')
        else:raise DownloadError('playlist_format')
        write_playlist(path, raw, cancel)
        return dict(record=value, path=str(path))
