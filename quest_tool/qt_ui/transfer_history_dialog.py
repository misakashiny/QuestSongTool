"""Searchable, read-only transfer records with summary and raw diagnostics."""
import json

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from .surfaces import heading, style_table, action_icons
from ..messages import tr
from ..theme import SPACE
from ..workers import TaskRunner
from .transfer_history import read_journals


class TransferHistoryDialog(QDialog):
    def __init__(self, directory, parent=None):
        super().__init__(parent);self.directory=directory;self.closed=False;self.tasks=TaskRunner(max_workers=1)
        super().setWindowTitle(tr('qt.transfer.history'));super().resize(1000,640);super().setMinimumSize(620,460)
        layout=QVBoxLayout(self);layout.setContentsMargins(SPACE.L,SPACE.L,SPACE.L,SPACE.L)
        top=QHBoxLayout();top.addWidget(heading('qt.transfer.history'),1)
        self.refresh=QPushButton(tr('qt.transfer.history_refresh'));top.addWidget(self.refresh);layout.addLayout(top)
        counts=QHBoxLayout();self.badges=[]
        for _ in range(3):
            label=QLabel();label.setObjectName('historyBadge');self.badges.append(label);counts.addWidget(label)
        counts.addStretch();layout.addLayout(counts)
        self.hint=QLabel(tr('qt.history.readonly'));self.hint.setWordWrap(True)
        self.hint.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum);layout.addWidget(self.hint)
        tools=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText(tr('qt.history.search'))
        self.filter=QComboBox()
        for key in ('all','committed','review','backup','invalid'):
            self.filter.addItem(tr('qt.history.all' if key=='all' else 'qt.transfer.history_unavailable' if key=='invalid' else 'qt.transfer.history_'+key),key)
        tools.addWidget(self.search,1);tools.addWidget(self.filter);layout.addLayout(tools)
        split=QSplitter(Qt.Orientation.Horizontal);split.setChildrenCollapsible(False);layout.addWidget(split,1)
        self.listing=QTreeWidget();self.listing.setHeaderLabels([tr('qt.transfer.history_state'),tr('qt.transfer.history_folder'),tr('qt.transfer.history_id')])
        self.listing.setColumnWidth(0,145);self.listing.setColumnWidth(1,160)
        style_table(self.listing);self.listing.setRootIsDecorated(False);self.listing.setMinimumWidth(200);split.addWidget(self.listing)
        self.tabs=QTabWidget();self.tabs.setMinimumWidth(200)
        self.summary=QPlainTextEdit();self.summary.setReadOnly(True)
        self.details=QPlainTextEdit();self.details.setReadOnly(True)
        self.tabs.addTab(self.summary,tr('qt.history.summary'));self.tabs.addTab(self.details,tr('qt.history.raw'));split.addWidget(self.tabs);split.setSizes([480,440])
        bottom=QHBoxLayout();self.visible=QLabel();bottom.addWidget(self.visible,1)
        self.copy=QPushButton(tr('qt.logs.copy'));self.copy.setEnabled(False);self.close_button=QPushButton(tr('qt.history.close'))
        bottom.addWidget(self.copy);bottom.addWidget(self.close_button);layout.addLayout(bottom)
        action_icons((self.refresh,'refresh'),(self.copy,'copy'),(self.close_button,'close'))
        self.refresh.clicked.connect(self.reload);self.close_button.clicked.connect(self.close);self.copy.clicked.connect(self.copy_record)
        self.search.textChanged.connect(self.filter_rows);self.filter.currentIndexChanged.connect(self.filter_rows)
        self.listing.currentItemChanged.connect(self.show_record)
        self.timer=QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.poll);self.reload()

    def reload(self):
        if self.closed or self.tasks.busy():return
        self.refresh.setEnabled(False);self.copy.setEnabled(False);self.listing.clear();self.details.clear();self.summary.clear()
        directory=self.directory;tasks=self.tasks
        def worker(token):
            try:result=read_journals(directory)
            except Exception:result=None
            if token.alive():tasks.emit('history',payload=result)
        self.tasks.submit(worker,name='qt_transfer_history');self.timer.start()

    def poll(self):
        if self.closed:return
        for message in self.tasks.drain():
            if message.kind!='history':continue
            self.timer.stop();self.refresh.setEnabled(True);data=message.payload
            if data is None:self.details.setPlainText(tr('qt.transfer.history_failed'));self.summary.setPlainText(tr('qt.transfer.history_failed'));continue
            self.hint.setText(tr('qt.history.readonly')+('\n'+tr('qt.transfer.history_limited') if data['limited'] else ''))
            committed=review=0
            for row in data['rows']:
                record=row['record']
                state=('invalid' if record is None else 'review' if record.get('recovery_required') or record['state']!='committed' else 'backup' if record.get('backup_created') else 'committed')
                committed+=state in ('committed','backup');review+=state in ('invalid','review')
                item=QTreeWidgetItem([tr('qt.transfer.history_unavailable' if state=='invalid' else 'qt.transfer.history_'+state),record['folder'] if record else '',row['file']])
                item.setData(0,Qt.ItemDataRole.UserRole,record);item.setData(0,Qt.ItemDataRole.UserRole+1,state)
                for column in range(3):item.setToolTip(column,item.text(column))
                self.listing.addTopLevelItem(item)
            for label,key,count in zip(self.badges,('total','done','attention'),(len(data['rows']),committed,review)):label.setText(tr('qt.history.'+key,count=count))
            self.details.setPlainText(tr('qt.transfer.history_hint'));self.summary.setPlainText(tr('qt.history.empty'))
            self.filter_rows()

    def filter_rows(self, *_args):
        query=self.search.text().strip().casefold();state=self.filter.currentData();visible=[]
        for index in range(self.listing.topLevelItemCount()):
            item=self.listing.topLevelItem(index)
            match=(state=='all' or state==item.data(0,Qt.ItemDataRole.UserRole+1)) and (not query or query in ' '.join(item.text(i) for i in range(3)).casefold())
            item.setHidden(not match)
            if match:visible.append(item)
        current=self.listing.currentItem()
        if current is None or current.isHidden():self.listing.setCurrentItem(visible[0] if visible else None)
        if not visible:self.show_record(None,None)
        self.visible.setText(tr('qt.history.visible',count=len(visible),total=self.listing.topLevelItemCount()))

    def show_record(self, item, _previous):
        self.copy.setEnabled(item is not None)
        if item is None:self.summary.setPlainText(tr('qt.history.empty'));self.details.clear();return
        record=item.data(0,Qt.ItemDataRole.UserRole)
        self.details.setPlainText(tr('qt.transfer.history_hint')+'\n\n'+(json.dumps(record,ensure_ascii=False,indent=2) if record else tr('qt.transfer.history_invalid')))
        if not record:self.summary.setPlainText(tr('qt.transfer.history_invalid'));return
        state=item.text(0);device=record['device']
        fields=[(tr('qt.transfer.history_state'),state),(tr('qt.transfer.history_folder'),record['folder']),
                (tr('qt.history.device'),device['model']),(tr('qt.history.target'),record['target']),
                (tr('qt.history.transaction'),record['transaction']),(tr('qt.history.stage'),record['state'])]
        self.summary.setPlainText('\n\n'.join(f'{name}\n{value}' for name,value in fields)+'\n\n'+tr('qt.history.readonly'))

    def copy_record(self):
        box=self.summary if self.tabs.currentIndex()==0 else self.details
        QApplication.clipboard().setText(box.toPlainText())

    def closeEvent(self, event):
        self.closed=True;self.tasks.cancel_all();self.timer.stop();super().closeEvent(event)

    def reject(self):self.close()

    def wait_closed(self):
        for thread in tuple(self.tasks._threads):thread.join()
