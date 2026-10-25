"""Explicit startup category preference; no search history or implicit learning."""
import json
from pathlib import Path

from PySide6.QtCore import QIODevice, QObject, QSaveFile

from ..messages import tr
from ..search_filters import MODES


class BrowsePreferences(QObject):
    ALLOWED = tuple(mode for mode in MODES if mode not in ('Search', 'Random'))

    def __init__(self, browser, sandbox):
        super().__init__(browser)
        self.browser = browser
        self.root = Path(sandbox).resolve()
        self.path = self.root/'qt-browse-preferences.json'
        self.saved = 'Curated'
        self.error = False
        try:
            self.saved = self.load()
        except (OSError, ValueError, TypeError):
            self.error = True
        menu = browser.window.options.menu()
        menu.addSeparator()
        self.title = menu.addAction('')
        self.title.setEnabled(False)
        self.save = menu.addAction('')
        self.reset = menu.addAction('')
        self.save.triggered.connect(lambda: self.commit(browser.category()))
        self.reset.triggered.connect(lambda: self.commit('Curated'))
        menu.aboutToShow.connect(self.refresh)
        browser.window.categories.buttons[self.saved].setChecked(True)
        browser.window.update_category()
        self.refresh()

    def load(self):
        if self.path.is_symlink() or self.root.resolve() != self.root:
            raise ValueError('path')
        if not self.path.exists():
            return 'Curated'
        with self.path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError('size')
        data = json.loads(raw.decode('utf-8'))
        if (not isinstance(data, dict) or set(data) != {'version', 'category'}
                or type(data['version']) is not int or data['version'] != 1
                or data['category'] not in self.ALLOWED):
            raise ValueError('format')
        return data['category']

    def commit(self, category):
        if self.browser.closed or category not in self.ALLOWED:
            return False
        try:
            self.load()  # Refuse corrupt/future files, including external changes.
            self.root.mkdir(parents=True, exist_ok=True)
            data = json.dumps({'version': 1, 'category': category}).encode('utf-8')
            file = QSaveFile(str(self.path))
            if not file.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError('open')
            if file.write(data) != len(data):
                file.cancelWriting()
                raise OSError('write')
            if not file.commit():
                raise OSError('commit')
            self.saved, self.error = category, False
        except (OSError, ValueError, TypeError):
            self.error = True
            self.refresh()
            return False
        self.refresh()
        return True

    def refresh(self):
        self.title.setText(tr('qt.browse_pref.error') if self.error else
                           tr('qt.browse_pref.saved', category=tr('browse.'+self.saved)))
        self.save.setText(tr('qt.browse_pref.save'))
        self.save.setEnabled(not self.browser.closed and self.browser.category() in self.ALLOWED)
        self.reset.setText(tr('qt.browse_pref.reset'))
        self.reset.setEnabled(not self.browser.closed)
        for action in (self.title, self.save, self.reset):
            action.setToolTip(tr('qt.browse_pref.hint'))
