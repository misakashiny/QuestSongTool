"""UI-thread import coordinator; fixed selection/identity and explicit confirmation."""
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget

from ..messages import tr
from .transfer_queue import TransferPlan, TransferQueue
from .transfer_dialog import TransferDialog
from .transfer_history_dialog import TransferHistoryDialog


class LibraryTransfer(QObject):
    def __init__(self, window, backend=None, push_backend=None):
        super().__init__(window)
        self.window = window
        self.backend = backend
        self.push_backend = push_backend
        self.dialog = None
        self.histories = []
        self.jobs = []
        self.pending = {}
        self.closed = False
        self.page = QWidget()
        layout = QVBoxLayout(self.page);layout.setContentsMargins(0,0,0,0)
        row = QHBoxLayout();layout.addLayout(row)
        self.select = QPushButton()
        self.clear = QPushButton()
        self.pull = QPushButton()
        self.missing = QPushButton()
        self.status = QLabel()
        self.status.setWordWrap(True)
        for control in (self.select,self.clear,self.pull,self.missing):row.addWidget(control)
        row.addWidget(self.status,1)
        for control in (self.select,self.clear,self.pull,self.missing,self.status):control.setVisible(backend is not None)
        push_row = QHBoxLayout();layout.addLayout(push_row)
        self.push = QPushButton();self.push_missing = QPushButton();self.push_hint = QLabel()
        self.push_hint.setWordWrap(True)
        for control in (self.push,self.push_missing,self.push_hint):
            push_row.addWidget(control);control.setVisible(push_backend is not None)
        self.history = QPushButton()
        push_row.addWidget(self.history)
        self.history.setVisible(push_backend is not None and hasattr(push_backend,'journal_dir'))
        self.history.clicked.connect(self.show_history)
        if backend is None and push_backend is None:self.page.hide()
        self.push.clicked.connect(lambda:self.open('local','to_quest'))
        self.push_missing.clicked.connect(lambda:self.open('missing','to_quest'))
        self.select.clicked.connect(self.select_visible)
        self.clear.clicked.connect(self.clear_checks)
        self.pull.clicked.connect(lambda:self.open('quest'))
        self.missing.clicked.connect(lambda:self.open('missing'))
        for model in (window.model,window.quest_model,window.missing_model):
            model.dataChanged.connect(self.render)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.render)
        if backend is not None or push_backend is not None:self.timer.start()
        self.retranslate()

    def binding(self):
        w = self.window
        selected = w.device_browser.selected
        if (self.closed or w.closed or (self.backend is None and self.push_backend is None) or not w.local_ready or not w.quest_ready or w.quest_incomplete
                or w.device_browser.busy or w.device_browser.connection_open or selected is None or selected['identity']!=w.quest_identity
                or w.target is None):return None
        return (str(w.target), w.quest_identity)

    def enable_backends(self, backend, push_backend):
        """Reveal existing guarded actions only after an explicit UI request."""
        if self.closed or self.dialog is not None or any(job.tasks.busy() for job in self.jobs):return
        self.backend = self.backend or backend;self.push_backend = self.push_backend or push_backend
        for control in (self.select,self.clear,self.pull,self.missing,self.status):control.setVisible(self.backend is not None)
        for control in (self.push,self.push_missing,self.push_hint):control.setVisible(self.push_backend is not None)
        self.history.setVisible(self.push_backend is not None and hasattr(self.push_backend,'journal_dir'))
        self.page.setVisible(self.backend is not None or self.push_backend is not None)
        self.timer.start();self.retranslate()

    def names(self, origin, direction='to_local'):
        w = self.window
        if origin=='missing' and w.missing_origin!=('quest' if direction=='to_local' else 'local'):return ()
        model = w.quest_model if origin=='quest' else w.model if origin=='local' else w.missing_model
        return tuple(row['folder'] for row in model.rows if row['folder'] in model.checked and not row['error'])

    def select_visible(self):
        w = self.window
        w.quest_model.checked.update(w.quest_proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder']
            for i in range(w.quest_proxy.rowCount()))
        w.quest_model.layoutChanged.emit()
        self.render()

    def clear_checks(self):
        self.window.quest_model.checked.clear()
        self.window.quest_model.layoutChanged.emit()
        self.render()

    def open(self, origin, direction='to_local'):
        binding = self.binding()
        backend = self.backend if direction=='to_local' else self.push_backend
        if backend is None:return
        if binding is None or self.dialog is not None or any(job.tasks.busy() for job in self.jobs):return
        names = self.names(origin,direction)
        if not names:return
        try:
            plan = TransferPlan.create(direction,binding[0],self.window.device_browser.selected,names)
        except Exception:
            self.status.setText(tr('qt.transfer.blocked'))
            return
        queue = TransferQueue(backend)
        self.jobs.append(queue)
        self.pending[queue] = (binding,direction)
        dialog = TransferDialog(queue,plan,self.window,simulation=False,before_start=lambda:self.binding()==binding)
        self.dialog = dialog
        dialog.attemptStarted.connect(lambda:self.pending.update({queue:(binding,direction)}))
        dialog.resultReady.connect(lambda result:self.finished(queue,binding,direction,result))
        dialog.closedSignal.connect(self.dialog_closed)
        dialog.show()
        self.render()

    def dialog_closed(self):
        dialog = self.dialog
        self.dialog = None
        if dialog:dialog.deleteLater()
        self.render()

    def show_history(self):
        if self.closed or self.push_backend is None or not hasattr(self.push_backend,'journal_dir'):return
        for dialog in self.histories:
            if not dialog.closed:
                dialog.show();dialog.raise_();dialog.activateWindow();return
        dialog = TransferHistoryDialog(self.push_backend.journal_dir,self.window)
        self.histories.append(dialog)
        dialog.show()

    def refresh_after(self, binding, direction, result):
        if self.binding()==binding:
            if direction=='to_quest':self.window.clear_quest_snapshot()
            elif result['ok']:self.window.refresh()

    def finished(self, queue, binding, direction, result):
        self.pending.pop(queue,None)
        self.refresh_after(binding,direction,result)
        self.render()

    def render(self, *_args):
        if self.closed:return
        for job,(binding,direction) in tuple(self.pending.items()):
            if job.closed and not job.tasks.busy():
                self.pending.pop(job,None)
                results = [m.payload for m in job.tasks.drain() if isinstance(m.payload,dict)
                    and m.payload.get('state') in ('done','cancelled','blocked')]
                if results:self.refresh_after(binding,direction,results[-1])
        self.jobs[:] = [job for job in self.jobs if not job.closed or job.tasks.busy()]
        binding = self.binding()
        if self.dialog is not None and binding!=(self.dialog.plan.local,dict(self.dialog.plan.device)['identity']):
            self.dialog.queue.cancel()
        enabled = binding is not None and self.dialog is None and not any(job.tasks.busy() for job in self.jobs)
        self.select.setEnabled(enabled)
        self.clear.setEnabled(enabled and bool(self.window.quest_model.checked))
        self.pull.setEnabled(enabled and self.backend is not None and bool(self.names('quest')))
        self.missing.setEnabled(enabled and self.backend is not None and bool(self.names('missing')))
        self.push.setEnabled(enabled and self.push_backend is not None and bool(self.names('local','to_quest')))
        self.push_missing.setEnabled(enabled and self.push_backend is not None and bool(self.names('missing','to_quest')))
        self.push_hint.setText(tr('qt.transfer.push_hint'))
        self.status.setText(tr('qt.transfer.pull_hint',count=len(self.names('quest'))))

    def retranslate(self):
        for control,key in ((self.select,'select_quest'),(self.clear,'clear_quest'),(self.pull,'pull_selected'),(self.missing,'pull_missing')):
            control.setText(tr('qt.transfer.'+key))
        if self.backend is not None:
            for control,key in self.window.texts:
                if key=='qt.library.scope':control.setText(tr('qt.transfer.pull_library_scope'))
        self.push.setText(tr('qt.transfer.push_selected'));self.push_missing.setText(tr('qt.transfer.push_missing'))
        self.history.setText(tr('qt.transfer.history'))
        if self.push_backend is not None:
            for control,key in self.window.texts:
                if key=='qt.library.scope':control.setText(tr('qt.transfer.push_library_scope'))
        self.render()

    def close(self):
        self.closed = True
        self.timer.stop()
        if self.dialog:self.dialog.close()
        for dialog in self.histories:dialog.close()
        for job in self.jobs:job.close()

    def wait_closed(self):
        for job in self.jobs:job.wait_closed()
        for dialog in self.histories:dialog.wait_closed()
