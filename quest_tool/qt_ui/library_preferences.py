"""Small, validated Qt-only preferences; no old settings/database access."""
import json
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile


class LibraryPreferences:
    def __init__(self, sandbox):
        self.root = Path(sandbox).resolve()
        self.path = self.root/'qt-library-ui.json'

    def load(self):
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 16384:return {}
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return self.validate(value)
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def validate(value):
        if not isinstance(value, dict):return {}
        result = {}
        if value.get('view') in ('list', 'covers'):result['view'] = value['view']
        widths = value.get('widths')
        if isinstance(widths, list) and len(widths) == 3 and all(type(x) is int and 60 <= x <= 1200 for x in widths):
            result['widths'] = widths
        seconds = value.get('preview_seconds');volume = value.get('preview_volume')
        if type(seconds) is int and seconds in (0,30,60,120):result['preview_seconds'] = seconds
        if type(volume) is int and 0 <= volume <= 100:result['preview_volume'] = volume
        rating_width = value.get('rating_width')
        if type(rating_width) is int and 60 <= rating_width <= 1200:result['rating_width'] = rating_width
        return result

    def save(self, value):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if self.root.resolve() != self.root or self.path.is_symlink():return False
            data = json.dumps(self.validate(value)).encode('utf-8')
            file = QSaveFile(str(self.path))
            if not file.open(QIODevice.OpenModeFlag.WriteOnly):return False
            if file.write(data) != len(data):file.cancelWriting();return False
            return file.commit()
        except OSError:
            return False
