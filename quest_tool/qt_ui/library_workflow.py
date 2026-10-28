"""Navigation and a read-only view over existing task owners."""
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from .surfaces import surface, heading, style_table, action_strip, action_icons

from ..messages import tr


class LibraryWorkflow(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.dialog = None
        self.page = surface(QWidget(), 'toolbar')
        layout = QVBoxLayout(self.page);layout.setContentsMargins(4,2,4,2)
        row = QHBoxLayout();self.navigation_row = row;layout.addLayout(row)
        self.verification_row = QHBoxLayout();layout.addLayout(self.verification_row)
        self.view = QComboBox()
        for key in ('compare','local','quest'):self.view.addItem('',key)
        self.tasks = QPushButton();self.view_local = QPushButton();self.open_folder = QPushButton()
        row.addWidget(self.view);row.addStretch();row.addWidget(self.view_local);row.addWidget(self.open_folder);row.addWidget(self.tasks)
        self.view.currentIndexChanged.connect(self.change_view)
        self.tasks.clicked.connect(self.show_tasks)
        self.view_local.clicked.connect(lambda:self.view.setCurrentIndex(self.view.findData('local')))
        self.open_folder.clicked.connect(self.reveal_folder)
        self.timer = QTimer(window);self.timer.setInterval(500);self.timer.timeout.connect(self.render_tasks)
        self.compact = None;window.installEventFilter(self)
        self.retranslate()

    def eventFilter(self, watched, event):
        if watched is self.window and event.type()==QEvent.Type.Resize:self.reflow()
        return False

    def reflow(self):
        compact = self.window.height()<=720
        if compact==self.compact:return
        self.compact = compact
        self.window.device_browser.render()

    def retranslate(self):
        for i in range(self.view.count()):self.view.setItemText(i,tr('ux.view.'+self.view.itemData(i)))
        self.tasks.setText(tr('ux.tasks'));self.view_local.setText(tr('ux.view_local'));self.open_folder.setText(tr('ux.open_folder'))
        if self.dialog:
            self.dialog.setWindowTitle(tr('ux.tasks'));self.task_heading.setText(tr('ux.tasks'))
            self.tree.setHeaderLabels([tr('ux.task_type'),tr('ux.task_state'),tr('ux.task_count'),tr('ux.task_target')])
            for key,button in self.task_buttons.items():button.setText(tr(key))
            self.last_entries = None;self.render_tasks()

    def change_view(self):
        mode = self.view.currentData();w = self.window
        w.local_card.setVisible(mode in ('local','compare'));w.quest_card.setVisible(mode in ('quest','compare'))
        w.missing_card.setVisible(mode=='compare');w.library_split.setSizes([350,350,350,280])

    def reveal_folder(self):
        root = self.window.target
        if root is not None and Path(root).is_dir():QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def show_local(self, root):
        if self.window.closed:return
        if self.window.target is None or str(self.window.target)!=str(root):self.window.set_target(str(root))
        else:self.window.refresh()
        # The main window no longer exposes a view-mode selector. Returning from
        # a download must not leave its Quest and missing lists inaccessible.
        self.view.setCurrentIndex(self.view.findData('compare'))
        self.change_view()
        self.window.show();self.window.raise_();self.window.activateWindow()

    def show_tasks(self):
        if self.dialog is not None:self.dialog.show();self.dialog.raise_();return
        dialog = QDialog(self.window);self.dialog = dialog;dialog.resize(760,420)
        dialog.setWindowTitle(tr('ux.tasks'));layout = QVBoxLayout(dialog)
        self.task_heading = heading('ux.tasks');layout.addWidget(self.task_heading)
        self.task_note = QLabel(tr('ux.task_note'));self.task_note.setWordWrap(True);layout.addWidget(self.task_note)
        self.last_entries = None
        self.tree = QTreeWidget();self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([tr('ux.task_type'),tr('ux.task_state'),tr('ux.task_count'),tr('ux.task_target')]);layout.addWidget(self.tree)
        style_table(self.tree)
        self.tree.setRootIsDecorated(False)
        buttons = []
        self.task_buttons = {}
        for key,callback in (('ux.download_tasks',self.show_download_tasks),('ux.transfer_details',self.show_transfer),('ux.view_local',self.show_task_result)):
            button = QPushButton(tr(key));button.clicked.connect(callback);buttons.append(button);self.task_buttons[key] = button
        self.task_buttons['ux.download_tasks'].setProperty('role','primary')
        action_icons((self.task_buttons['ux.download_tasks'],'download'),(self.task_buttons['ux.transfer_details'],'transfer'),(self.task_buttons['ux.view_local'],'folder'))
        layout.addWidget(action_strip(*buttons))
        dialog.finished.connect(self.tasks_closed);self.render_tasks();self.timer.start();dialog.show()

    def render_tasks(self):
        if self.dialog is None:return
        self.task_buttons['ux.download_tasks'].setEnabled(not self.window.closed)
        self.task_buttons['ux.transfer_details'].setEnabled(self.window.transfers.dialog is not None)
        current = self.tree.currentItem();key = current.data(0,Qt.ItemDataRole.UserRole) if current else None
        w = self.window;entries = []
        seen = set()
        # Reopened windows share one history. Only its newest manager is
        # authoritative, including removals made through Clear finished.
        for owner in w.download_queues[-1:]:
            for item in owner.manager.snapshot():
                if item['id'] in seen:continue
                seen.add(item['id'])
                entries.append(('download:'+item['id'],tr('ux.download_tasks'),tr('dl.state.'+item['state']),item.get('title') or item['source'],item['target']))
        for item in w.transfers.results:
            entries.append(('transfer:'+str(item['id']),tr('qt.transfer.'+item['direction']),tr('qt.transfer.'+item['state']) if item['state'] in ('done','cancelled','blocked') else item['state'],str(item.get('ok',0))+'/'+str(item.get('total',0)),item['target']))
        for index,job in enumerate(w.transfers.jobs):
            if job.tasks.busy():
                pending = w.transfers.pending.get(job)
                entries.append(('running:'+str(index),tr('qt.transfer.'+pending[1]) if pending else tr('ux.transfer_details'),tr('qt.transfer.running'),'—',pending[2] if pending else ''))
        if entries==getattr(self,'last_entries',None):return
        self.last_entries = entries
        scroll = self.tree.verticalScrollBar().value();self.tree.clear()
        for identity,kind,state,count,target in entries[-200:]:
            item = QTreeWidgetItem([kind,state,count,target]);item.setData(0,Qt.ItemDataRole.UserRole,identity)
            self.tree.addTopLevelItem(item)
            if identity==key:self.tree.setCurrentItem(item)
        self.tree.verticalScrollBar().setValue(scroll)
        self.task_note.setText(tr('ux.task_note') if entries else tr('ux.no_tasks'))

    def show_task_result(self):
        item = self.tree.currentItem()
        if item is None:return
        key = item.data(0,Qt.ItemDataRole.UserRole)
        if key.startswith('transfer:'):
            record = next((record for record in self.window.transfers.results if record['id']==int(key.split(':')[1])),None)
            if record:self.show_local(record['local_target'])
        elif key.startswith('download:') and item.text(3):self.show_local(item.text(3))

    def show_download_tasks(self):
        self.window.download_requested_tab = 'tasks'
        self.window.open_downloads()
        window = self.window.download_window
        if window is not None:window.show_tasks();window.show();window.raise_();window.activateWindow()

    def show_transfer(self):
        dialog = self.window.transfers.dialog
        if dialog is not None:dialog.show();dialog.raise_();dialog.activateWindow()

    def tasks_closed(self, *_args):
        self.timer.stop();dialog = self.dialog;self.dialog = None
        if dialog:dialog.deleteLater()

    def close(self):
        self.timer.stop()
        if self.dialog:self.dialog.close()
