"""One explicitly selected PC library shared by the Qt entry points."""
import json
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile


def valid_directory(value):
    if not isinstance(value, str) or value.startswith(('\\\\', '//')):
        return None
    path = Path(value)
    try:
        if (not path.is_absolute() or not path.is_dir() or path.is_symlink()
                or path.is_junction() or path.resolve() == Path(path.anchor)):
            return None
        return path.resolve()
    except OSError:
        return None


class WorkspaceDirectory:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root/'qt-workspace.json'

    def load(self):
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 16384:
                return None
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return valid_directory(data.get('local')) if isinstance(data, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def save(self, value):
        path = valid_directory(str(value))
        if path is None:
            return False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if self.root.resolve() != self.root or self.path.is_symlink():
                return False
            data = json.dumps({'local': str(path)}).encode('utf-8')
            output = QSaveFile(str(self.path))
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                return False
            if output.write(data) != len(data):
                output.cancelWriting()
                return False
            return output.commit()
        except OSError:
            return False
