"""Bounded local playlist snapshots. No network or song writes."""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import warnings

from PIL import Image, ImageOps
from PySide6.QtCore import QIODevice, QSaveFile

from ..downloads import DownloadError, check_cancel
from ..playlist import parse_playlist
from ..search_media import _COVER_DECODE_LOCK


MAX_FILE = 8*1024*1024
MAX_SONGS = 2000
MAX_LISTS = 20
MAX_STORE = 16*1024*1024
STORE_LOCK = threading.RLock()


def fail():
    raise DownloadError('playlist_format')


def text_value(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        fail()
    value.encode('utf-8')
    return value


def normalized(text, fallback=''):
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get('songs'), list) or len(data['songs']) > MAX_SONGS:
        fail()
    for key in ('playlistTitle', 'playlistAuthor'):
        text_value(data.get(key, ''), 200)
    image = text_value(data.get('image', ''), MAX_FILE)
    # Reuse the existing parser, but reject malformed rows instead of silently
    # dropping them. Identity never falls back to title or mapper.
    songs = []
    for item in data['songs']:
        if not isinstance(item, dict):
            fail()
        for key, limit in (('songName', 300), ('levelAuthorName', 200), ('hash', 40), ('levelId', 160), ('key', 12)):
            text_value(item.get(key, ''), limit)
        digest = item.get('hash', '').strip().lower()
        if digest and not re.fullmatch('[0-9a-f]{40}', digest):
            fail()
        level = item.get('levelId', '')
        derived = level[len('custom_level_'):].lower() if level.lower().startswith('custom_level_') else ''
        if derived:
            if not re.fullmatch('[0-9a-f]{40}', derived) or (digest and derived != digest):
                fail()
            digest = derived
        key = item.get('key', '').strip().lower()
        if key and not re.fullmatch('[0-9a-f]{1,12}', key):
            fail()
        songs.append(dict(songName=item.get('songName', ''), levelAuthorName=item.get('levelAuthorName', ''), hash=digest, key=key))
    data = dict(playlistTitle=data.get('playlistTitle', '') or fallback, playlistAuthor=data.get('playlistAuthor', ''), songs=songs, image=image)
    parsed = parse_playlist(json.dumps(data, ensure_ascii=False))
    cover = ''
    if parsed.cover:
        # An invalid cover does not make the song list unusable.
        try:
            with _COVER_DECODE_LOCK, warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(parsed.cover)) as source:
                    if source.width > 4096 or source.height > 4096:
                        raise ValueError('image size')
                    picture = ImageOps.fit(source.convert('RGB'), (256, 256))
            output = io.BytesIO();picture.save(output, format='PNG')
            cover = base64.b64encode(output.getvalue()).decode('ascii')
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            pass
    value = dict(title=parsed.title, author=parsed.author, songs=songs, cover=cover)
    value['id'] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return value


def read_playlist(path):
    path = Path(path)
    if (not path.is_absolute() or str(path).startswith(('\\\\', '//')) or path.suffix.lower() not in ('.bplist', '.json')
            or any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))):
        fail()
    before = path.stat()
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE+1)
    after = path.stat()
    if len(raw) > MAX_FILE or (before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
        fail()
    return normalized(raw.decode('utf-8-sig'), path.stem[:200])


class PlaylistStore:
    def __init__(self, sandbox):
        self.root = Path(sandbox).resolve()
        self.path = self.root/'qt-playlists-v1.json'

    def check_path(self):
        if any(p.is_symlink() or p.is_junction() for p in (self.path, self.root, *self.root.parents)):
            fail()

    def load(self):
        self.check_path()
        if not self.path.exists():
            return []
        with self.path.open('rb') as stream:
            raw = stream.read(MAX_STORE+1)
        if len(raw) > MAX_STORE:
            fail()
        data = json.loads(raw.decode('utf-8'))
        if (not isinstance(data, dict) or set(data) != {'version', 'lists'} or type(data['version']) is not int
                or data['version'] != 1 or not isinstance(data['lists'], list) or len(data['lists']) > MAX_LISTS):
            fail()
        result = []
        for entry in data['lists']:
            if not isinstance(entry, dict) or set(entry) != {'id', 'title', 'author', 'songs', 'cover'}:
                fail()
            value = normalized(json.dumps(dict(playlistTitle=entry['title'], playlistAuthor=entry['author'], songs=entry['songs'], image=entry['cover'])))
            if value != entry or any(v['id'] == value['id'] for v in result):
                fail()
            result.append(value)
        return result

    def save(self, values, cancel):
        raw = json.dumps(dict(version=1, lists=values), ensure_ascii=False).encode('utf-8')
        if len(values) > MAX_LISTS or len(raw) > MAX_STORE:
            raise DownloadError('playlist_limit')
        check_cancel(cancel);self.check_path();self.root.mkdir(parents=True, exist_ok=True)
        file = QSaveFile(str(self.path))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError('playlist open')
        if file.write(raw) != len(raw):
            file.cancelWriting();raise OSError('playlist write')
        if cancel.is_set():
            file.cancelWriting();check_cancel(cancel)
        if not file.commit():
            raise OSError('playlist commit')

    def search(self, request, _page, _order, cancel):
        with STORE_LOCK:return self._search(request,cancel)

    def _search(self, request, cancel):
        check_cancel(cancel)
        values = self.load()  # A corrupt/future file must never be overwritten.
        action = request['action']
        if action == 'import':
            paths = request['paths']
            if len(paths) > MAX_LISTS:
                raise DownloadError('playlist_limit')
            for path in paths:
                check_cancel(cancel);value = read_playlist(path)
                if not any(v['id'] == value['id'] for v in values):
                    values.append(value)
        elif action == 'remove':
            values = [v for v in values if v['id'] != request['id']]
        elif action != 'load':
            fail()
        if action != 'load':
            self.save(values, cancel)
        return values
