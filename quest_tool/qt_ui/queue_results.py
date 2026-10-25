"""UI-only historical queue summary; no download or installation decisions."""
from collections import Counter

from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..downloads import TERMINAL
from ..messages import tr
from ..theme import SPACE


class QueueResults(QWidget):
    FILTERS = ('all', 'done', 'exists', 'attention', 'cancelled', 'active')

    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self.signature = None
        self.translations = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE.S)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        row.setSpacing(SPACE.M)
        self.filter = QComboBox()
        for key in self.FILTERS:
            self.filter.addItem('', key)
        self.visible = QLabel()
        self.visible.setTextFormat(Qt.TextFormat.PlainText)
        self.copy_button = QPushButton()
        row.addWidget(self.filter)
        row.addWidget(self.visible, 1)
        row.addWidget(self.copy_button)
        layout.addLayout(row)
        self.filter.currentIndexChanged.connect(self.filter_changed)
        self.copy_button.clicked.connect(self.copy_results)

    def matches(self, row):
        key = self.filter.currentData()
        state = row['state']
        return (key == 'all' or key == state or
                key == 'attention' and state in ('failed', 'interrupted') or
                key == 'active' and state not in TERMINAL)

    def filter_changed(self, *_args):
        # Hidden selections must never be submitted by the existing retry/cancel.
        self.browser.table.clearSelection()
        self.signature = None
        self.refresh()
        self.browser.refresh()

    def refresh(self):
        browser = self.browser
        labels = tuple(tr('qt.results.filter.'+key) for key in self.FILTERS)
        translations = (labels, tr('qt.results.summary'), tr('qt.results.copy'), tr('qt.results.visible'))
        if translations != self.translations:
            self.translations = translations
            for index, text in enumerate(labels):
                self.filter.setItemText(index, text)
            self.filter.setAccessibleName(tr('qt.results.filter_label'))
            self.copy_button.setText(tr('qt.results.copy'))
            self.copy_button.setToolTip(tr('qt.results.copy_hint'))
            self.summary.setToolTip(tr('qt.results.export_hint'))
            browser.clear_history.setToolTip(tr('qt.results.clear_hint'))
            self.signature = None
        rows = browser.model.rows
        signature = (tuple((row['id'], row['state']) for row in rows), self.filter.currentData())
        if signature == self.signature:
            self.copy_button.setEnabled(not browser.closed and any(self.matches(row) for row in rows))
            return
        self.signature = signature
        counts = Counter(row['state'] for row in rows)
        self.summary.setText(tr('qt.results.summary', total=len(rows), done=counts['done'],
            exists=counts['exists'], failed=counts['failed'], interrupted=counts['interrupted'],
            cancelled=counts['cancelled'], active=sum(count for state, count in counts.items() if state not in TERMINAL)))
        visible = 0
        selection = browser.table.selectionModel()
        hidden = QItemSelection()
        for index, row in enumerate(rows):
            shown = self.matches(row)
            browser.table.setRowHidden(index, not shown)
            visible += int(shown)
            if not shown and selection.isSelected(browser.model.index(index, 0)):
                hidden.select(browser.model.index(index, 0), browser.model.index(index, 3))
        self.visible.setText(tr('qt.results.visible', count=visible, total=len(rows)))
        self.copy_button.setEnabled(bool(visible) and not browser.closed)
        if not hidden.isEmpty():
            selection.select(hidden, QItemSelectionModel.SelectionFlag.Deselect | QItemSelectionModel.SelectionFlag.Rows)

    def copy_results(self):
        if self.browser.closed:
            return
        model = self.browser.model
        clean = lambda text: str(text).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')
        headers = [model.headerData(i, Qt.Orientation.Horizontal) for i in range(4)]
        headers.extend((tr('dl.destination'), tr('qt.results.source'), tr('qt.results.job')))
        lines = [tr('qt.results.export_hint'), '\t'.join(headers)]
        for index, row in enumerate(model.rows):
            if self.matches(row):
                values = [model.data(model.index(index, column)) for column in range(4)]
                values.extend((row['target'], row['source'], row['id']))
                lines.append('\t'.join(clean(value) for value in values))
        QApplication.clipboard().setText('\n'.join(lines))
        self.visible.setText(tr('qt.results.copied'))
