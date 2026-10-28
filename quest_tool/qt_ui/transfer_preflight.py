"""Read-only transfer estimates used by the confirmation dialog.

The estimate never probes the device and is deliberately not used as an
identity, integrity, or overwrite decision.  The transfer backend remains the
only authority for those decisions.
"""
from pathlib import Path
import os


MAX_FILES = 100_000
MAX_BYTES = 2 * 1024 * 1024 * 1024


def _link(path):
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def _inside(root, folder):
    root = Path(root).resolve(strict=True)
    path = (root / folder).resolve(strict=True)
    if path.parent != root or _link(path) or not path.is_dir():
        raise ValueError('transfer_preflight_path')
    return root, path


def estimate_local_tree(root, folders, *, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    """Return a bounded, read-only byte estimate for selected local folders."""
    total = 0
    files = 0
    unreadable = 0
    unsafe = 0
    limited = False
    seen = set()
    try:
        root_path = Path(root).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return dict(bytes=None, files=0, unreadable=0, unsafe=1, limited=False)
    for name in tuple(folders):
        try:
            _root, folder = _inside(root_path, name)
        except (OSError, ValueError, RuntimeError):
            unsafe += 1
            continue
        pending = [folder]
        while pending and not limited:
            current = pending.pop()
            try:
                entries = tuple(os.scandir(current))
            except OSError:
                unreadable += 1
                continue
            for entry in entries:
                path = Path(entry.path)
                if _link(path):
                    unsafe += 1
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    unreadable += 1
                    continue
                identity = (getattr(stat, 'st_dev', 0), getattr(stat, 'st_ino', 0), str(path).casefold())
                if identity in seen:
                    continue
                seen.add(identity)
                files += 1
                total += max(0, int(stat.st_size))
                if files > max_files or total > max_bytes:
                    limited = True
                    break
    return dict(bytes=total if not limited else None, files=files, unreadable=unreadable,
                unsafe=unsafe, limited=limited)


def format_bytes(value, unknown='unknown'):
    if value is None:
        return unknown
    value = max(0, int(value))
    if value < 1024:
        return f'{value} B'
    if value < 1024 ** 2:
        return f'{value / 1024:.1f} KiB'
    if value < 1024 ** 3:
        return f'{value / 1024 ** 2:.1f} MiB'
    return f'{value / 1024 ** 3:.2f} GiB'
