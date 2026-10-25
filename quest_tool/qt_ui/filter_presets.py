"""Bounded Qt-only presets and summaries of committed user filters."""
import copy
import json
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile

from ..messages import tr
from ..search_filters import FLAGS, RANGES, filter_params


def conditions(values):
    """Return removable groups; ignore empty/default selectors, retain false/zero."""
    filter_params(values)
    result = []
    for key in FLAGS:
        if type(values.get(key)) is bool:
            result.append(((key,), tr('filter.explain.'+key).split('\n')[0]+': '+tr('filter.yes' if values[key] else 'filter.no')))
    ai = values.get('automapper')
    if ai in ('human', 'ai'):
        result.append((('automapper',), tr('filter.ai')+': '+tr('filter.human' if ai == 'human' else 'filter.ai_only')))
    board = values.get('leaderboard')
    if board and board != 'All':
        result.append((('leaderboard',), tr('filter.leaderboard')+': '+tr('filter.board.'+board)))
    for key, prefix in (('tags', 'tag'), ('environments', 'env'), ('characteristics', 'mode')):
        for item in values.get(key, ()):
            text = tr('filter.'+prefix+'.'+item)
            if key == 'tags':
                text += ': '+tr('filter.yes' if values[key][item] else 'filter.no')
            result.append(((key, item), text))
    for name in RANGES:
        keys = tuple(bound+name for bound in ('min', 'max') if values.get(bound+name) not in (None, ''))
        if keys:
            text = ' / '.join(('≥ ' if key.startswith('min') else '≤ ')+str(values[key]) for key in keys)
            result.append((keys, tr('filter.metric.'+name)+': '+text))
    for key in ('from', 'to'):
        if values.get(key):
            result.append(((key,), tr('filter.'+key)+': '+values[key]))
    return result


def without(values, keys):
    result = copy.deepcopy(values)
    if len(keys) == 2 and keys[0] in ('tags', 'environments', 'characteristics'):
        group, item = keys
        if isinstance(result[group], dict):
            result[group].pop(item, None)
        else:
            result[group] = [value for value in result[group] if value != item]
    else:
        for key in keys:
            result.pop(key, None)
    filter_params(result)
    return result


class FilterPresets:
    """Atomic small local file; refuse corrupt/future files rather than overwrite."""
    LIMIT = 20
    MAX_BYTES = 65536

    def __init__(self, sandbox):
        self.root = Path(sandbox).resolve()
        self.path = self.root/'qt-filter-presets.json'

    @staticmethod
    def name(value):
        if not isinstance(value, str):
            raise ValueError('name')
        value = value.strip()
        if not value or len(value) > 40 or any(ord(c) < 32 for c in value):
            raise ValueError('name')
        return value

    def load(self):
        if self.path.is_symlink() or self.root.resolve() != self.root:
            raise ValueError('path')
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > self.MAX_BYTES:
            raise ValueError('size')
        with self.path.open('rb') as stream:
            raw = stream.read(self.MAX_BYTES+1)
        if len(raw) > self.MAX_BYTES:
            raise ValueError('size')
        data = json.loads(raw.decode('utf-8'))
        if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or not isinstance(data.get('presets'), dict):
            raise ValueError('format')
        presets = data['presets']
        if len(presets) > self.LIMIT:
            raise ValueError('limit')
        for name, values in presets.items():
            if self.name(name) != name:
                raise ValueError('name')
            filter_params(values)
        return presets

    def save(self, name, values):
        name = self.name(name)
        filter_params(values)
        presets = self.load()
        if name.casefold() in {key.casefold() for key in presets} or len(presets) >= self.LIMIT:
            raise ValueError('duplicate or limit')
        presets[name] = copy.deepcopy(values)
        self.write(presets)

    def delete(self, name):
        presets = self.load()
        if name not in presets:
            raise ValueError('missing')
        del presets[name]
        self.write(presets)

    def write(self, presets):
        data = json.dumps({'version': 1, 'presets': presets}, ensure_ascii=False).encode('utf-8')
        if len(data) > self.MAX_BYTES or self.path.is_symlink() or self.root.resolve() != self.root:
            raise ValueError('size or path')
        self.root.mkdir(parents=True, exist_ok=True)
        file = QSaveFile(str(self.path))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError('open')
        if file.write(data) != len(data):
            file.cancelWriting()
            raise OSError('write')
        if not file.commit():
            raise OSError('commit')
