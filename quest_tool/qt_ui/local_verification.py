"""Read-only verification of recorded direct-child installs; never search by title."""
import json
from pathlib import Path
import re

from ..downloads import DownloadError, check_cancel, safe_relative, validate_song
from ..playlist import difficulty_files
from .song_status import path_key


class ReadBudgetExceeded(DownloadError):
    """A retryable per-scan byte budget; preserve the existing error code."""
    def __init__(self):
        super().__init__('limit')


def tree_state(root, cancel):
    """Bound metadata traversal, reject links, and retain a change fingerprint."""
    pending, entries = [root], []
    while pending:
        check_cancel(cancel)
        folder = pending.pop()
        for path in folder.iterdir():
            check_cancel(cancel)
            if len(entries) >= 512:raise DownloadError('limit')
            if path.is_symlink() or path.is_junction():raise DownloadError('unsafe')
            stat = path.stat()
            entries.append((path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns, stat.st_ino))
            if path.is_dir():pending.append(path)
            elif not path.is_file():raise DownloadError('unsafe')
    return tuple(sorted(entries))


def inspect_install(path, target, cancel, budget, memo=None):
    # History is data, not permission to inspect arbitrary filesystem locations.
    if not path.is_absolute() or path_key(path.parent) != path_key(target):
        return {'state': 'unknown', 'reason': 'path'}
    if any(p.is_symlink() or p.is_junction() for p in (target, *target.parents, path)):
        return {'state': 'unknown', 'reason': 'path'}
    if path.resolve().parent != target.resolve():
        return {'state': 'unknown', 'reason': 'path'}
    try:path.stat()
    except FileNotFoundError:return {'state': 'missing'}
    if not path.is_dir():return {'state': 'unknown', 'reason': 'structure'}
    before = tree_state(path, cancel)
    root_stat = path.stat()
    fingerprint = (root_stat.st_ino, root_stat.st_mtime_ns, before)
    key = path_key(path)
    if memo is not None and key in memo and memo[key][0] == fingerprint:
        return dict(memo[key][1], reused=True)
    infos = [p for p in path.iterdir() if p.name.lower() == 'info.dat' and p.is_file()]
    if len(infos) != 1 or infos[0].stat().st_size > 2*1024*1024:
        return {'state': 'unknown', 'reason': 'structure'}
    info_size = infos[0].stat().st_size
    if budget[0] < info_size:raise ReadBudgetExceeded()
    budget[0] -= info_size
    info = json.loads(infos[0].read_text(encoding='utf-8-sig'))
    if not isinstance(info, dict):return {'state': 'unknown', 'reason': 'structure'}
    names = difficulty_files(info)
    if len(names) > 256:raise DownloadError('limit')
    # Original validator parses each map and then hashes its raw bytes. Reserve
    # both reads, plus its info reads, before delegating; no audio PCM is read.
    cost = 2*info_size
    for name in names:
        check_cancel(cancel)
        ref = path/safe_relative(str(name))
        size = ref.stat().st_size
        if size > 16*1024*1024:raise DownloadError('limit')
        cost += 2*size
    if cost > 32*1024*1024:raise DownloadError('limit')
    if cost > budget[0]:raise ReadBudgetExceeded()
    budget[0] -= cost
    check_cancel(cancel)
    _title, digest = validate_song(path)
    check_cancel(cancel)
    if before != tree_state(path, cancel) or path.stat().st_ino != root_stat.st_ino:
        return {'state': 'unknown', 'reason': 'changed'}
    value = {'state': 'valid', 'digest': digest.lower()}
    if memo is not None:memo[key] = (fingerprint, value)
    return value


class VerificationClient:
    MAX_RECORDS = 512
    MAX_BYTES = 256*1024*1024

    def inspect(self, path, target, cancel, budget):
        return inspect_install(path, target, cancel, budget)

    def search(self, payload, _page, _order, cancel):
        target, rows = payload['target'], payload['rows']
        result, inspected, budget = {}, {}, [self.MAX_BYTES]
        for position, row in enumerate(rows):
            check_cancel(cancel)
            name = row.get('installed')
            if not name:continue
            value = {'state': 'unknown', 'reason': 'limit'}
            digest = row.get('digest')
            if position < self.MAX_RECORDS and isinstance(digest, str) and re.fullmatch(r'[0-9a-fA-F]{40}', digest):
                key = path_key(name)
                if key not in inspected:
                    try:inspected[key] = self.inspect(Path(name), Path(target), cancel, budget)
                    except (OSError, ValueError, TypeError, DownloadError) as exc:
                        check_cancel(cancel)
                        inspected[key] = {'state': 'unknown', 'reason': exc.code if isinstance(exc, DownloadError) else 'read'}
                value = inspected[key]
            result[row['id']] = value
        return {'target': target, 'records': result}
