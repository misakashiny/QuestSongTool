"""Hash-only playlist lookup, reusing bounded installation verification."""
from pathlib import Path

from ..downloads import DownloadError, check_cancel
from .local_verification import inspect_install, ReadBudgetExceeded


class PlaylistMatchClient:
    MAX_FOLDERS = 2000
    MAX_BYTES = 256*1024*1024

    def __init__(self):
        self.root = None
        self.memo = {}

    def search(self, request, _page, _order, cancel):
        root = Path(request['root'])
        if (not root.is_absolute() or root == Path(root.anchor) or str(root).startswith(('\\\\', '//'))
                or any(p.is_symlink() or p.is_junction() for p in (root, *root.parents)) or not root.is_dir()):
            raise DownloadError('playlist_root')
        if self.root != root or request.get('force'):
            self.memo.clear()
        self.root = root
        budget = [self.MAX_BYTES];found = {};unknown = 0;checked = 0;seen = set()
        folders = request['folders']
        for name in folders[:self.MAX_FOLDERS]:
            check_cancel(cancel)
            if not isinstance(name, str) or not name or name.startswith('.') or any(c in name for c in '/\\:'):
                unknown += 1;continue
            path = root/name;seen.add(str(path).casefold());checked += 1
            try:
                value = inspect_install(path, root, cancel, budget, self.memo)
            except ReadBudgetExceeded:
                unknown += len(folders)-checked+1;break
            except (OSError, ValueError, TypeError, DownloadError):
                check_cancel(cancel);value = {'state': 'unknown'}
            if value.get('state') == 'valid':
                found.setdefault(value['digest'], name)
            else:
                unknown += 1
            if sum(1+len(v[0][2]) for v in self.memo.values()) > 20000:
                self.memo.clear()
        self.memo = {k:v for k,v in self.memo.items() if k in seen}
        check_cancel(cancel)
        return dict(root=str(root), matches=found, checked=checked, unknown=unknown,
                    limited=len(folders) > self.MAX_FOLDERS or checked < len(folders))
