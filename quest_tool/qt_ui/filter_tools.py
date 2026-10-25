"""Native applied-filter summary and explicit personal preset actions."""
import copy

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..downloads import DownloadError
from ..messages import tr
from ..search_filters import browse_params
from ..theme import SPACE
from .filter_presets import FilterPresets, conditions, without


class SummaryLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.full_text = ''

    def update_text(self, text):
        self.full_text = text
        super().setText(super().fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, self.width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_text(self.full_text)


class FilterTools(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.store = None
        self.dialog = None
        self.row = QWidget()
        layout = QHBoxLayout(self.row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE.M)
        self.summary = QPushButton()
        self.menu = QMenu(self.summary)
        self.summary.setMenu(self.menu)
        self.description = SummaryLabel()
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.description.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.clear = QPushButton()
        self.presets = QPushButton()
        layout.addWidget(self.summary)
        layout.addWidget(self.description, 1)
        layout.addWidget(self.clear)
        layout.addWidget(self.presets)
        browser.window.discovery.layout().insertWidget(2, self.row)
        self.clear.clicked.connect(lambda: self.commit({}))
        self.presets.clicked.connect(self.open_presets)
        self.refresh()

    def configure(self, sandbox):
        self.store = FilterPresets(sandbox)
        self.refresh()

    def refresh(self):
        items = conditions(self.browser.filters)
        self.summary.setText(tr('qt.filters.summary', count=len(items)))
        self.summary.setEnabled(bool(items))
        self.menu.clear()
        for keys, text in items:
            action = self.menu.addAction(tr('qt.filters.remove', condition=text).replace('&', '&&'))
            action.triggered.connect(lambda _checked=False, keys=keys: self.commit(without(self.browser.filters, keys)))
        text = ' · '.join(text for _keys, text in items) if items else tr('qt.filters.none')
        self.description.update_text(text)
        self.description.setToolTip(text+'\n'+tr('qt.filters.category_hint'))
        self.clear.setText(tr('qt.filters.clear'))
        self.clear.setEnabled(bool(self.browser.filters))
        self.presets.setText(tr('qt.filters.presets'))
        self.presets.setEnabled(self.store is not None)
        self.presets.setToolTip(tr('qt.filters.preset_hint'))

    def commit(self, values):
        if self.browser.closed:
            return False
        try:
            browse_params(self.browser.category(), values)
        except DownloadError:
            self.description.update_text(tr('qt.filters.conflict'))
            self.description.setToolTip(tr('qt.filters.conflict'))
            return False
        self.browser.apply_filters(copy.deepcopy(values))
        return True

    def open_presets(self):
        if self.store is None or self.browser.closed:
            return
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        dialog = PresetDialog(self)
        self.dialog = dialog
        dialog.finished.connect(self.finish)
        dialog.open()

    def finish(self, _result):
        dialog, self.dialog = self.dialog, None
        if dialog is not None:
            dialog.deleteLater()

    def close(self):
        if self.dialog is not None:
            self.dialog.reject()


class PresetDialog(QDialog):
    def __init__(self, tools):
        super().__init__(tools.browser.window)
        self.tools = tools
        self.values = {}
        super().setWindowTitle(tr('qt.filters.presets'))
        super().setWindowModality(Qt.WindowModality.WindowModal)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
        root.setSpacing(SPACE.M)
        hint = QLabel(tr('qt.filters.preset_hint'))
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.list = QListWidget()
        root.addWidget(self.list, 1)
        self.name = QLineEdit()
        self.name.setMaxLength(40)
        self.name.setPlaceholderText(tr('qt.filters.name'))
        self.name.setAccessibleName(tr('qt.filters.name'))
        root.addWidget(self.name)
        row = QHBoxLayout()
        self.save = QPushButton(tr('qt.filters.save'))
        self.load = QPushButton(tr('qt.filters.load'))
        self.delete = QPushButton(tr('qt.filters.delete'))
        self.delete.setProperty('role', 'danger')
        for button in (self.save, self.load, self.delete):
            button.setAutoDefault(False)
            row.addWidget(button)
        root.addLayout(row)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr('qt.filters.close'))
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.save.clicked.connect(self.save_current)
        self.load.clicked.connect(self.load_selected)
        self.delete.clicked.connect(self.delete_selected)
        self.list.itemSelectionChanged.connect(self.selection_changed)
        self.reload()

    def selection_changed(self):
        enabled = self.list.currentItem() is not None
        self.load.setEnabled(enabled)
        self.delete.setEnabled(enabled)

    def reload(self):
        try:
            self.values = self.tools.store.load()
        except (OSError, ValueError, TypeError, DownloadError):
            self.error.setText(tr('qt.filters.storage_error'))
            self.save.setEnabled(False)
            self.values = {}
        self.list.clear()
        self.list.addItems(sorted(self.values, key=str.casefold))
        self.selection_changed()

    def save_current(self):
        try:
            self.tools.store.save(self.name.text(), self.tools.browser.filters)
        except (OSError, ValueError, TypeError, DownloadError):
            self.error.setText(tr('qt.filters.save_error'))
            return
        self.name.clear()
        self.error.setText(tr('qt.filters.saved'))
        self.reload()

    def load_selected(self):
        item = self.list.currentItem()
        if item is None:
            return
        if not self.tools.commit(self.values[item.text()]):
            self.error.setText(tr('qt.filters.conflict'))
            return
        super().accept()

    def delete_selected(self):
        item = self.list.currentItem()
        if item is None:
            return
        try:
            self.tools.store.delete(item.text())
        except (OSError, ValueError, TypeError, DownloadError):
            self.error.setText(tr('qt.filters.storage_error'))
            return
        self.error.clear()
        self.reload()
