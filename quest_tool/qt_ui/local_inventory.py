"""Bounded current-destination inventory, sharing record validation and byte budget."""
import os
from pathlib import Path

from ..downloads import DownloadError, check_cancel
from .local_verification import ReadBudgetExceeded, VerificationClient, inspect_install
from .song_status import path_key


class InventoryClient(VerificationClient):
    MAX_FOLDERS = 2000
    MAX_ENTRIES = 10000
    MAX_CACHE_ITEMS = 20000
    MAX_PASSES = 3

    def __init__(self):
        self.target = None
        self.memo = {}
        self.seen = set()
        self.operation_seen = set()
        self.budget = [0]
        self.budget_limited = False
        self.fresh_valid = 0

    def inspect(self, path, target, cancel, _budget):
        check_cancel(cancel)
        key = path_key(path)
        if key not in self.operation_seen and len(self.operation_seen) >= self.MAX_FOLDERS:
            return {'state': 'unknown', 'reason': 'limit'}
        self.operation_seen.add(key)
        self.seen.add(key)
        try:value = inspect_install(path, target, cancel, self.budget, self.memo)
        except ReadBudgetExceeded:
            self.budget_limited = True
            raise
        self.fresh_valid += value.get('state') == 'valid' and not value.get('reused')
        while self.memo and sum(1+len(item[0][2]) for item in self.memo.values()) > self.MAX_CACHE_ITEMS:
            self.memo.pop(next(iter(self.memo)))
        return value

    def search(self, payload, page, order, cancel):
        # One SearchSession consumer owns all passes and the same cancellation
        # token. Each pass revalidates fingerprints; never merge stale digests.
        self.operation_seen = set()
        previous_valid = -1
        for number in range(1, self.MAX_PASSES+1):
            check_cancel(cancel)
            result = self._scan(dict(payload, force=bool(payload.get('force')) and number == 1), page, order, cancel)
            stats = result['inventory']
            stats.update(passes=number, max_passes=self.MAX_PASSES, budget_limited=self.budget_limited)
            if (stats.get('error') or stats['complete'] or not self.budget_limited or
                    not self.fresh_valid or stats['valid'] <= previous_valid):
                break
            previous_valid = stats['valid']
        check_cancel(cancel)
        return result

    def _scan(self, payload, page, order, cancel):
        target = Path(payload['target'])
        if self.target != path_key(target) or payload.get('force'):
            self.memo.clear()
        self.target = path_key(target)
        self.seen = set()
        self.budget = [self.MAX_BYTES]
        self.budget_limited = False
        self.fresh_valid = 0
        result = super().search(payload, page, order, cancel)
        stats = dict(digests=[], checked=0, valid=0, unknown=0, reused=0, complete=True)
        digests, folders = set(), []
        try:
            if (not target.is_absolute() or target == Path(target.anchor) or
                    str(target).startswith(('\\\\', '//')) or
                    any(p.is_symlink() or p.is_junction() for p in (target, *target.parents))):
                raise OSError('inventory target')
            root_stamp = target.stat()
            with os.scandir(target) as entries:
                for index, entry in enumerate(entries):
                    check_cancel(cancel)
                    if index >= self.MAX_ENTRIES:
                        stats['complete'] = False;break
                    if entry.name.startswith('.'):continue
                    if entry.is_symlink() or Path(entry.path).is_junction():
                        stats['unknown'] += 1;stats['complete'] = False;continue
                    if entry.is_dir(follow_symlinks=False):
                        if len(folders) >= self.MAX_FOLDERS:
                            stats['complete'] = False;break
                        folders.append(Path(entry.path))
            for folder in sorted(folders, key=lambda path:path.name.casefold()):
                check_cancel(cancel)
                stats['checked'] += 1
                try:value = self.inspect(folder, target, cancel, self.budget)
                except (OSError, ValueError, TypeError, DownloadError):
                    check_cancel(cancel)
                    value = {'state': 'unknown'}
                if value.get('state') == 'valid':
                    digests.add(value['digest']);stats['valid'] += 1
                    stats['reused'] += bool(value.get('reused'))
                else:
                    stats['unknown'] += 1;stats['complete'] = False
            after = target.stat()
            if (root_stamp.st_ino, root_stamp.st_mtime_ns) != (after.st_ino, after.st_mtime_ns):
                stats['complete'] = False
        except (OSError, ValueError):
            stats['complete'] = False
            stats['error'] = True
        check_cancel(cancel)
        self.memo = {key:value for key,value in self.memo.items() if key in self.seen}
        stats['digests'] = sorted(digests)
        result['inventory'] = stats
        return result
