"""Small atomic recent-Quest records. History never proves a live connection."""
import json
from pathlib import Path
import re

from PySide6.QtCore import QIODevice, QSaveFile

from .wireless_validation import address, port


def recent_record(value):
    if not isinstance(value,dict):raise ValueError('record')
    if not isinstance(value.get('host'),str):raise ValueError('host')
    hardware=value.get('hardware');model=value.get('model','Quest')
    if not isinstance(hardware,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,255}',hardware) is None:raise ValueError('hardware')
    if not isinstance(model,str) or not model or len(model)>80 or any(ord(c)<32 for c in model):raise ValueError('model')
    host=address(value.get('host'));number=port(str(value.get('port','')))
    return dict(host=host,port=number,hardware=hardware,model=model)


class WirelessHistory:
    LIMIT=8
    MAX_BYTES=16384

    def __init__(self, root):
        self.root=Path(root).resolve();self.path=self.root/'qt-wireless-recent.json'

    def load(self):
        if self.root.resolve()!=self.root or self.path.is_symlink():raise ValueError('path')
        if not self.path.exists():return []
        if self.path.stat().st_size>self.MAX_BYTES:raise ValueError('size')
        with self.path.open('rb') as stream:raw=stream.read(self.MAX_BYTES+1)
        if len(raw)>self.MAX_BYTES:raise ValueError('size')
        value=json.loads(raw.decode('utf-8'))
        if not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1 or set(value)!= {'version','devices'}:raise ValueError('version')
        rows=value['devices']
        if not isinstance(rows,list) or len(rows)>self.LIMIT:raise ValueError('count')
        rows=[recent_record(item) for item in rows]
        if len({row['hardware'] for row in rows})!=len(rows):raise ValueError('duplicate')
        return rows

    def save(self, rows):
        # Read first: corrupt, future-version and linked files are never replaced.
        self.load()
        rows=[recent_record(row) for row in rows]
        if len(rows)>self.LIMIT or len({row['hardware'] for row in rows})!=len(rows):raise ValueError('count')
        raw=json.dumps(dict(version=1,devices=rows),ensure_ascii=False).encode('utf-8')
        if len(raw)>self.MAX_BYTES:raise ValueError('size')
        self.root.mkdir(parents=True,exist_ok=True)
        if self.root.resolve()!=self.root or self.path.is_symlink():raise ValueError('path')
        target=QSaveFile(str(self.path))
        if not target.open(QIODevice.OpenModeFlag.WriteOnly):raise OSError('history open')
        if target.write(raw)!=len(raw):target.cancelWriting();raise OSError('history write')
        if not target.commit():raise OSError('history commit')

    def remember(self, value):
        row=recent_record(value)
        self.save([row]+[old for old in self.load() if old['hardware']!=row['hardware']][:self.LIMIT-1])

    def forget(self, hardware):
        self.save([row for row in self.load() if row['hardware']!=hardware])
