"""Read-only journal viewer with bounded I/O outside the GUI thread."""
import json

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from ..messages import tr
from ..theme import SPACE
from ..workers import TaskRunner
from .transfer_history import read_journals


class TransferHistoryDialog(QDialog):
    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.closed = False
        self.tasks = TaskRunner(max_workers=1)
        QDialog.setWindowTitle(self, tr('qt.transfer.history'))
        QDialog.resize(self, 760, 540)
        QDialog.setMinimumSize(self, 620, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.L,SPACE.L,SPACE.L,SPACE.L)
        self.hint = QLabel(tr('qt.transfer.history_hint'))
        self.hint.setTextFormat(Qt.TextFormat.PlainText);self.hint.setWordWrap(True)
        self.hint.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum)
        self.hint.setMinimumHeight(self.hint.fontMetrics().lineSpacing()*4)
        layout.addWidget(self.hint)
        self.listing = QTreeWidget()
        self.listing.setHeaderLabels([tr('qt.transfer.history_state'),tr('qt.transfer.history_folder'),tr('qt.transfer.history_id')])
        self.listing.setColumnWidth(0,220);self.listing.setColumnWidth(1,170)
        layout.addWidget(self.listing,1)
        self.details = QPlainTextEdit();self.details.setReadOnly(True)
        layout.addWidget(self.details,1)
        self.refresh = QPushButton(tr('qt.transfer.history_refresh'));layout.addWidget(self.refresh)
        self.refresh.clicked.connect(self.reload)
        self.listing.currentItemChanged.connect(self.show_record)
        self.timer = QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.poll)
        self.reload()

    def reload(self):
        if self.closed or self.tasks.busy():return
        self.refresh.setEnabled(False)
        self.listing.clear();self.details.clear()
        directory = self.directory
        tasks = self.tasks
        def worker(_token):
            try:result = read_journals(directory)
            except Exception:result = None
            tasks.emit('history',payload=result)
        self.tasks.submit(worker,name='qt_transfer_history')
        self.timer.start()

    def poll(self):
        if self.closed:return
        for message in self.tasks.drain():
            if message.kind!='history':continue
            self.timer.stop();self.refresh.setEnabled(True)
            data = message.payload
            if data is None:
                self.details.setPlainText(tr('qt.transfer.history_failed'));continue
            self.hint.setText(tr('qt.transfer.history_hint')+(
                '\n'+tr('qt.transfer.history_limited') if data['limited'] else ''))
            for row in data['rows']:
                record = row['record']
                state = ('invalid' if record is None else 'review' if record.get('recovery_required') or record['state']!='committed'
                    else 'backup' if record.get('backup_created') else 'committed')
                item = QTreeWidgetItem([tr('qt.transfer.history_unavailable' if state=='invalid' else 'qt.transfer.history_'+state),
                    record['folder'] if record else '',row['file']])
                item.setData(0,Qt.ItemDataRole.UserRole,record)
                self.listing.addTopLevelItem(item)
            self.details.setPlainText(tr('qt.transfer.history_limited' if data['limited'] else 'qt.transfer.history_hint'))
            if self.listing.topLevelItemCount():self.listing.setCurrentItem(self.listing.topLevelItem(0))

    def show_record(self, item, _previous):
        if item is None:return
        record = item.data(0,Qt.ItemDataRole.UserRole)
        self.details.setPlainText(tr('qt.transfer.history_hint')+'\n\n'+(
            json.dumps(record,ensure_ascii=False,indent=2) if record else tr('qt.transfer.history_invalid')))

    def closeEvent(self, event):
        self.closed = True;self.timer.stop()
        super().closeEvent(event)

    def reject(self):self.close()

    def wait_closed(self):
        for thread in tuple(self.tasks._threads):thread.join()
