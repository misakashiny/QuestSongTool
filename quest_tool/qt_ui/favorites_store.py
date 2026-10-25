"""Bounded Qt-only map bookmarks, with validated metadata snapshots."""
from dataclasses import asdict, fields
import json
import math
from pathlib import Path
import re

from PySide6.QtCore import QIODevice, QSaveFile

from ..downloads import safe_web_url
from .song_model import Song


class FavoritesStore:
    LIMIT = 200
    MAX_BYTES = 2*1024*1024

    def __init__(self, sandbox):
        self.root = Path(sandbox).resolve()
        self.path = self.root/'qt-favorites-v1.json'

    @staticmethod
    def song(data):
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(Song)}:
            raise ValueError('record')
        for key, limit in (('key', 12), ('title', 200), ('artist', 160), ('mapper', 160),
                           ('digest', 40), ('published', 40), ('cover_url', 2048),
                           ('preview_url', 2048), ('description', 4000)):
            if not isinstance(data[key], str) or len(data[key]) > limit:
                raise ValueError(key)
            data[key].encode('utf-8')
        if not re.fullmatch('[0-9a-f]{1,12}', data['key']) or not re.fullmatch('[0-9a-f]{40}', data['digest']):
            raise ValueError('identity')
        for key in ('cover_url', 'preview_url'):
            if data[key]:
                safe_web_url(data[key])
        for key, maximum in (('seconds', 86400), ('bpm', 10000), ('rating', 100)):
            value = data[key]
            if value is not None and (type(value) not in (int, float) or not 0 <= value <= maximum or not math.isfinite(value)):
                raise ValueError(key)
        values = dict(data)
        for key, limit, width in (('requirements', 16, 64), ('diffs', 100, 128)):
            value = data[key]
            if not isinstance(value, (list, tuple)) or len(value) > limit or any(not isinstance(v, str) or len(v) > width for v in value):
                raise ValueError(key)
            for item in value:
                item.encode('utf-8')
            values[key] = tuple(value)
        return Song(**values)

    def check_path(self):
        if self.path.is_symlink() or self.path.is_junction() or self.root.resolve() != self.root:
            raise ValueError('path')

    def load(self):
        self.check_path()
        if not self.path.exists():
            return {}
        with self.path.open('rb') as stream:
            raw = stream.read(self.MAX_BYTES+1)
        if len(raw) > self.MAX_BYTES:
            raise ValueError('size')
        data = json.loads(raw.decode('utf-8'))
        if (not isinstance(data, dict) or set(data) != {'version', 'songs'}
                or type(data['version']) is not int or data['version'] != 1
                or not isinstance(data['songs'], list) or len(data['songs']) > self.LIMIT):
            raise ValueError('format')
        result = {}
        for raw_song in data['songs']:
            song = self.song(raw_song)
            if song.key in result:
                raise ValueError('duplicate')
            result[song.key] = song
        return result

    def update(self, song=None, remove=()):
        values = self.load()  # Never overwrite a corrupt or future file.
        for key in remove:
            values.pop(key, None)
        if song is not None:
            value = self.song(asdict(song))
            values[value.key] = value
        if len(values) > self.LIMIT:
            raise ValueError('limit')
        raw = json.dumps({'version': 1, 'songs': [asdict(v) for v in values.values()]}, ensure_ascii=False).encode('utf-8')
        if len(raw) > self.MAX_BYTES:
            raise ValueError('size')
        self.check_path()
        self.root.mkdir(parents=True, exist_ok=True)
        file = QSaveFile(str(self.path))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError('open')
        if file.write(raw) != len(raw):
            file.cancelWriting()
            raise OSError('write')
        if not file.commit():
            raise OSError('commit')
        return values
