"""Informational queue/history labels. Never infer installation from a title."""
import os

from ..downloads import TERMINAL
from ..messages import tr


def path_key(value):
    # Targets already come from the queue's validated absolute paths. No I/O here.
    return os.path.normcase(os.path.normpath(str(value)))


class StatusIndex:
    def __init__(self, rows=(), target='', verified=None, inventory=None):
        self.active = {}
        self.latest = {}
        self.versions = {}
        self.verified = verified or {}
        self.local_versions = set((inventory or {}).get('digests', ()))
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('target'), str) or not isinstance(row.get('state'), str):
                continue
            if path_key(row.get('target', '')) != path_key(target):
                continue
            digest = row.get('digest') or ''
            digest = digest.casefold() if isinstance(digest, str) else ''
            if row.get('state') in ('done', 'exists') and digest:
                self.versions[digest] = row
                actual = self.verified.get(row.get('id'), {})
                if actual.get('state') == 'valid':self.local_versions.add(actual.get('digest', ''))
            if row.get('kind') == 'online' and isinstance(row.get('source'), str):
                key = row.get('source', '').casefold()
                self.latest[key] = row
                if row.get('state') not in TERMINAL:
                    self.active[key] = row

    def label(self, song):
        key = song.key.casefold()
        if key in self.active:
            return tr('dl.state.'+self.active[key]['state'])
        if song.digest and song.digest.casefold() in self.local_versions:
            return tr('qt.verify.same')
        row = self.latest.get(key)
        actual = self.verified.get(row.get('id'), {}) if row else {}
        if actual:
            if actual.get('state') == 'missing':return tr('qt.verify.missing')
            if actual.get('state') != 'valid':return tr('qt.verify.unknown')
            if actual.get('digest') != (row.get('digest') or '').casefold():return tr('qt.verify.changed')
            if song.digest and actual.get('digest') != song.digest.casefold():return tr('qt.verify.other')
        if song.digest and song.digest.casefold() in self.versions:
            row = self.versions[song.digest.casefold()]
            actual = self.verified.get(row.get('id'), {})
            if actual:return tr('qt.verify.missing' if actual.get('state') == 'missing' else 'qt.verify.unknown')
            return tr('qt.selection.same_version')
        row = self.latest.get(key)
        if row:
            if row['state'] in ('failed', 'cancelled', 'interrupted'):
                return tr('qt.selection.last_state', state=tr('dl.state.'+row['state']))
            if isinstance(row.get('digest'), str) and row['digest'] and song.digest and row['digest'].casefold() != song.digest.casefold():
                return tr('qt.selection.other_version')
        return ''
