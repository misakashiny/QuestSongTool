"""Compact per-list check toggles; preserve selections outside the active search."""
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QPushButton

from ..messages import tr


class LibrarySelection(QObject):
    def __init__(self, window, rows):
        super().__init__(window)
        self.window = window
        self.items = {}
        self.names = {};self.dirty = set()
        self.timer = QTimer(self);self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.render)
        for key, model, proxy, row in zip(
                ('local', 'quest', 'missing'),
                (window.model, window.quest_model, window.missing_model),
                (window.proxy, window.quest_proxy, window.missing_proxy), rows):
            button = QPushButton();button.setObjectName('selectAll_'+key)
            button.setProperty('role', 'inlineAction')
            row.addWidget(button,0,Qt.AlignmentFlag.AlignTop)
            self.items[key] = (model, proxy, button)
            self.dirty.add(key)
            button.clicked.connect(lambda _checked=False, side=key:self.toggle(side))
            model.dataChanged.connect(self.checks_changed)
            model.layoutChanged.connect(lambda *_,side=key:self.invalidate(side))
            for signal in (proxy.rowsInserted,proxy.rowsRemoved,proxy.modelReset,proxy.layoutChanged):
                signal.connect(lambda *_,side=key:self.invalidate(side))

    def checks_changed(self, _first, _last, roles):
        if not roles or Qt.ItemDataRole.CheckStateRole in roles:self.timer.start(0)

    def invalidate(self, key):
        self.dirty.add(key);self.timer.start(0)

    def visible_names(self, key, proxy):
        if key in self.dirty:
            self.names[key] = {proxy.index(row,0).data(Qt.ItemDataRole.UserRole)['folder']
                               for row in range(proxy.rowCount())}
            self.dirty.discard(key)
        return self.names[key]

    def toggle(self, key):
        model, proxy, _button = self.items[key]
        names = self.visible_names(key,proxy)
        if not names:return
        if names.issubset(model.checked):model.checked.difference_update(names)
        else:model.checked.update(names)
        model.dataChanged.emit(model.index(0,0),model.index(model.rowCount()-1,0),[Qt.ItemDataRole.CheckStateRole])
        self.render()

    def render(self, *_args):
        if self.window.closed:return
        for key,(model,proxy,button) in self.items.items():
            names = self.visible_names(key,proxy)
            all_checked = bool(names) and names.issubset(model.checked)
            text = tr('ui.library.uncheck_all' if all_checked else 'ui.library.check_all')
            button.setText(text);button.setEnabled(bool(names))
            button.setAccessibleName(tr('ui.library.quest_missing' if key=='missing' else 'qt.library.'+key)+' · '+text)
            button.setToolTip(tr('ui.library.visible_checks',visible=len(names),selected=len(model.checked)))
