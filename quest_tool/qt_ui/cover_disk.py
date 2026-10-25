"""Bounded, disposable normalized PNG cache; all methods are worker-side I/O."""
import hashlib
import io
import os
from pathlib import Path
import re
import threading
import time
import uuid

from PIL import Image

from ..downloads import safe_web_url


class CoverDiskCache:
    MAGIC = b'QuestCoverCache-v1\n'
    MAX_FILE = 256*1024
    PNG = re.compile(r'[0-9a-f]{64}\.png\Z')
    TEMP = re.compile(r'[0-9a-f]{64}\.[0-9a-f]{32}\.tmp\Z')

    def __init__(self, sandbox, max_bytes=128*1024*1024, max_files=2000,
                 ttl=30*86400, clock=time.time):
        self.base = Path(sandbox).absolute()
        self.root = self.base/'qt-cover-cache-v1'
        self.max_bytes, self.max_files, self.ttl = max_bytes, max_files, ttl
        self.clock = clock
        self.lock = threading.RLock()
        self.epoch = 0
        self.entries = {}
        self.scanned = None
        self.hits = self.misses = self.errors = self.writes = 0

    @staticmethod
    def key(url):
        safe_web_url(url)
        return hashlib.sha256(url.encode('utf-8')).hexdigest()+'.png'

    @staticmethod
    def valid(data):
        if not 24 <= len(data) <= CoverDiskCache.MAX_FILE or data[:8] != b'\x89PNG\r\n\x1a\n':
            return False
        if int.from_bytes(data[16:20], 'big') != 256 or int.from_bytes(data[20:24], 'big') != 256:
            return False
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != 'PNG' or image.mode != 'RGB' or image.size != (256, 256):return False
                image.verify()
            with Image.open(io.BytesIO(data)) as image:image.load()
            return True
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            return False

    def _ready(self, create=False):
        # Windows TEMP may use an 8.3 alias. Canonical spelling differences are
        # legitimate; links/junctions in any ancestor still refuse cache I/O.
        if (any(path.is_symlink() or path.is_junction() for path in (self.base, *self.base.parents))
                or not self.base.is_dir() or self.root.is_symlink() or self.root.is_junction()
                or self.root.resolve() != self.base.resolve()/self.root.name):
            raise OSError('cache path')
        marker = self.root/'format-v1'
        if not self.root.exists():
            if not create:return False
            self.root.mkdir()
            with marker.open('xb') as stream:stream.write(self.MAGIC)
        if (not self.root.is_dir() or marker.is_symlink() or not marker.is_file()
                or marker.stat().st_size != len(self.MAGIC) or marker.read_bytes() != self.MAGIC):
            raise OSError('cache marker')
        return True

    def _file(self, path):
        return (path.parent == self.root and not path.is_symlink() and not path.is_junction()
                and path.resolve().parent == self.root.resolve() and path.is_file())

    def _remove(self, name):
        path = self.root/name
        if self._file(path):
            self._ready()
            path.unlink()
        self.entries.pop(name, None)

    def _scan(self, force=False):
        now = self.clock()
        if not force and self.scanned is not None and now-self.scanned < 60:return
        entries = {}
        for path in self.root.iterdir():
            if not self._file(path):continue
            if self.PNG.fullmatch(path.name):
                stat = path.stat()
                if stat.st_size > self.MAX_FILE or now-stat.st_mtime > self.ttl or stat.st_mtime > now+300:
                    self._remove(path.name)
                else:entries[path.name] = (stat.st_size, stat.st_mtime, stat.st_atime)
            elif self.TEMP.fullmatch(path.name) and now-path.stat().st_mtime > 3600:
                self._remove(path.name)
        self.entries = entries
        self.scanned = now
        self._trim()

    def _trim(self):
        size = sum(entry[0] for entry in self.entries.values())
        while self.entries and (len(self.entries) > self.max_files or size > self.max_bytes):
            name = min(self.entries, key=lambda key: self.entries[key][2])
            cost = self.entries[name][0]
            self._remove(name)
            size -= cost

    def get(self, url):
        name = self.key(url)
        with self.lock:
            try:
                if not self._ready():
                    self.misses += 1;return None
                self._scan()
                path = self.root/name
                if not self._file(path):
                    self.entries.pop(name, None);self.misses += 1;return None
                stat = path.stat()
                if stat.st_size > self.MAX_FILE or self.clock()-stat.st_mtime > self.ttl or stat.st_mtime > self.clock()+300:
                    self._remove(name);self.misses += 1;return None
                with path.open('rb') as stream:data = stream.read(self.MAX_FILE+1)
                if not self.valid(data):
                    self._remove(name);self.misses += 1;return None
                now = self.clock()
                os.utime(path, (now, stat.st_mtime))
                self.entries[name] = (len(data), stat.st_mtime, now)
                self.hits += 1
                return data
            except (OSError, ValueError):
                self.errors += 1;self.misses += 1
                return None

    def put(self, url, data, epoch):
        name = self.key(url)
        if not self.valid(data) or len(data) > self.max_bytes or self.max_files < 1:return False
        with self.lock:
            temp = None
            try:
                if epoch != self.epoch:return False
                self._ready(create=True)
                self._scan()
                # Reserve before writing: an eviction permission failure must not
                # allow repeated writes to grow beyond the configured budget.
                while (len(self.entries)+(name not in self.entries) > self.max_files or
                       sum(value[0] for key, value in self.entries.items() if key != name)+len(data) > self.max_bytes):
                    candidates = [key for key in self.entries if key != name]
                    if not candidates:return False
                    self._remove(min(candidates, key=lambda key: self.entries[key][2]))
                path = self.root/name
                if path.is_symlink() or path.is_junction():raise OSError('cache link')
                temp = self.root/(name[:-4]+'.'+uuid.uuid4().hex+'.tmp')
                with temp.open('xb') as stream:stream.write(data)
                self._ready()
                os.replace(temp, path)
                now = self.clock()
                os.utime(path, (now, now))
                self.entries[name] = (len(data), now, now)
                self._trim()
                self.writes += 1
                return True
            except (OSError, ValueError):
                self.errors += 1
                return False
            finally:
                if temp is not None:
                    try:
                        if temp.exists() and self._file(temp):
                            self._ready();temp.unlink()
                    except (OSError, ValueError):pass

    def clear(self):
        with self.lock:
            self.epoch += 1
            if not self._ready():return 0
            names = [path.name for path in self.root.iterdir()
                     if (self.PNG.fullmatch(path.name) or self.TEMP.fullmatch(path.name)) and self._file(path)]
            for name in names:self._remove(name)
            self.entries.clear()
            self.scanned = None
            return len(names)
