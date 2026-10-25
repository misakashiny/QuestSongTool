"""UI-owned, expiring page snapshots; never stores downloads or audio."""
from collections import OrderedDict
from dataclasses import asdict
import json
import time


class PageCache:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.entries = OrderedDict()
        self.bytes = 0
        self.limit = 3
        self.byte_limit = 8*1024*1024
        self.ttl = 120

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def prune(self):
        for key, (expires, _songs, _next, cost) in tuple(self.entries.items()):
            if expires <= self.clock():
                self.entries.pop(key)
                self.bytes -= cost

    def get(self, key, touch=True):
        self.prune()
        entry = self.entries.get(key)
        if entry is None:
            return None
        if touch:
            self.entries.move_to_end(key)
        return entry[1], entry[2]

    def remaining(self, key):
        self.prune()
        entry = self.entries.get(key)
        return max(0, entry[0]-self.clock()) if entry else 0

    def put(self, key, songs, has_next):
        self.prune()
        cost = len(json.dumps([asdict(song) for song in songs], ensure_ascii=False).encode('utf-8'))
        if key in self.entries:
            self.bytes -= self.entries.pop(key)[3]
        if cost > self.byte_limit:
            return
        self.entries[key] = (self.clock()+self.ttl, tuple(songs), has_next, cost)
        self.bytes += cost
        while len(self.entries) > self.limit or self.bytes > self.byte_limit:
            self.bytes -= self.entries.popitem(last=False)[1][3]
