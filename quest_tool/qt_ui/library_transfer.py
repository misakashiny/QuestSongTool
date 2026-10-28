"""UI-thread import coordinator; fixed selection/identity and explicit confirmation."""
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget

from .surfaces import surface

from ..messages import tr
from ..theme import SPACE, get_palette
from .icons import line_icon
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
        self.results = []
        self.result_sequence = 0
        self.page = surface(QWidget(), 'footer')
        layout = QVBoxLayout(self.page);layout.setContentsMargins(0,0,0,0)
        self.pull_controls = QWidget(window.retired_controls)
        row = QHBoxLayout(self.pull_controls)
        self.select = QPushButton()
        self.clear = QPushButton()
        self.pull = QPushButton()
        self.missing = QPushButton()
        self.status = QLabel()
        self.status.setWordWrap(True)
        for control in (self.select,self.clear,self.pull,self.missing):row.addWidget(control)
        row.addWidget(self.status,1)
        for control in (self.select,self.clear,self.pull,self.missing,self.status):control.setVisible(backend is not None)
        self.push_actions = surface(QWidget(), 'toolbar')
        column = QVBoxLayout(self.push_actions)
        column.setContentsMargins(SPACE.L,SPACE.M,SPACE.L,SPACE.M);column.setSpacing(SPACE.XS)
        layout.addWidget(self.push_actions)
        push_row = QHBoxLayout();push_row.setSpacing(8);self.push_row=push_row;column.addLayout(push_row)
        self.push = QPushButton();self.push_missing = QPushButton();self.push_hint = QLabel()
        self.pull.setProperty('role','primary');self.push.setProperty('role','primary')
        self.push_hint.setWordWrap(True)
        push_row.addWidget(self.push);push_row.addWidget(self.push_missing);push_row.addStretch()
        self.push_hint.setProperty('role','muted');column.addWidget(self.push_hint)
        self.push_actions.setVisible(push_backend is not None)
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

    def binding(self, *, selected_pull=False):
        w = self.window
        deletion = getattr(w,'deletion',None);playlists = getattr(w,'playlists',None)
        if (deletion is not None and deletion.generation is not None) or (playlists is not None and playlists.device_generation is not None):return None
        selected = w.device_browser.selected
        if (self.closed or w.closed or (self.backend is None and self.push_backend is None) or not w.local_ready or not w.quest_ready or (w.quest_incomplete and not selected_pull)
                or w.device_browser.busy or w.device_browser.connection_open or selected is None or selected['identity']!=w.quest_identity
                or w.target is None):return None
        return (str(w.target), w.quest_identity)

    def mount_header(self, layout):
        """Move the existing guarded actions; retain their slots and identities."""
        self.push_hint.hide()
        self.push_actions.layout().setContentsMargins(0,0,0,0)
        self.push_actions.layout().setSpacing(0)
        # The former footer stretch is unnecessary inside the compact header.
        if self.push_row.count()==4:self.push_row.takeAt(2)
        layout.setSpacing(6)
        self.page.setObjectName('headerTransfers');self.push_actions.setObjectName('headerTransferActions')
        layout.insertWidget(0,self.page)

    def enable_backends(self, backend, push_backend):
        """Expose guarded actions; constructing adapters starts no operation."""
        if self.closed or self.dialog is not None or any(job.tasks.busy() for job in self.jobs):return
        self.backend = self.backend or backend;self.push_backend = self.push_backend or push_backend
        for control in (self.select,self.clear,self.pull,self.missing,self.status):control.setVisible(self.backend is not None)
        self.push_actions.setVisible(self.push_backend is not None)
        self.history.setVisible(self.push_backend is not None and hasattr(self.push_backend,'journal_dir'))
        self.page.setVisible(self.backend is not None or self.push_backend is not None)
        self.timer.start();self.retranslate()

    def names(self, origin, direction='to_local'):
        w = self.window
        if origin=='missing' and (not hasattr(w,'identity_checks') or not w.identity_checks.complete()):return ()
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

    def open(self, origin, direction='to_local', *, folders=None):
        selected_pull = origin == 'quest' and direction == 'to_local'
        binding = self.binding(selected_pull=selected_pull)
        backend = self.backend if direction=='to_local' else self.push_backend
        if backend is None:return
        if binding is None or self.dialog is not None or any(job.tasks.busy() for job in self.jobs):return
        names = self.names(origin,direction) if folders is None else tuple(dict.fromkeys(folders))
        model=self.window.model if origin=='local' else self.window.quest_model if origin=='quest' else self.window.missing_model
        eligible={r['folder'] for r in model.rows if not r['error']}
        if origin=='missing':
            if not self.window.identity_checks.complete() or self.window.missing_origin!=('local' if direction=='to_quest' else 'quest'):return
        if not set(names).issubset(eligible):return
        if not names:return
        try:
            plan = TransferPlan.create(direction,binding[0],self.window.device_browser.selected,names)
        except Exception:
            self.status.setText(tr('qt.transfer.blocked'))
            self.window.statusBar().showMessage(tr('qt.transfer.blocked'))
            return
        queue = TransferQueue(backend)
        self.jobs.append(queue)
        target = plan.local if direction=='to_local' else dict(plan.device)['root']
        self.pending[queue] = (binding,direction,target)
        revision = self.window.identity_checks.epoch if hasattr(self.window,'identity_checks') else None
        def still_valid():
            if self.binding(selected_pull=selected_pull)!=binding:return False
            if origin=='missing':
                checks = self.window.identity_checks
                if not checks.complete() or checks.epoch!=revision:return False
                if folders is None and not set(plan.folders).issubset(self.names(origin,direction)):return False
            eligible={r['folder'] for r in model.rows if not r['error']}
            return set(plan.folders).issubset(eligible)
        dialog = TransferDialog(queue,plan,self.window,simulation=False,before_start=still_valid)
        dialog.selected_pull = selected_pull
        self.dialog = dialog
        dialog.attemptStarted.connect(lambda:self.pending.update({queue:(binding,direction,target)}))
        dialog.resultReady.connect(lambda result:self.finished(queue,binding,direction,result,target))
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

    def refresh_after(self, binding, direction, result, target):
        self.result_sequence += 1
        self.results.append(dict(result, id=self.result_sequence, direction=direction, target=target, local_target=binding[0]))
        self.results[:] = self.results[-100:]
        if self.binding(selected_pull=direction=='to_local')==binding:
            if direction=='to_quest':self.window.clear_quest_snapshot()
            elif result['ok']:self.window.refresh()

    def finished(self, queue, binding, direction, result, target):
        self.pending.pop(queue,None)
        self.refresh_after(binding,direction,result,target)
        if direction == 'to_quest':
            self.window.statusBar().showMessage(tr('qt.transfer.reread_after_push'))
        self.render()

    def render(self, *_args):
        if self.closed:return
        for job,(binding,direction,target) in tuple(self.pending.items()):
            if job.closed and not job.tasks.busy():
                self.pending.pop(job,None)
                results = [m.payload for m in job.tasks.drain() if isinstance(m.payload,dict)
                    and m.payload.get('state') in ('done','cancelled','blocked')]
                if results:self.refresh_after(binding,direction,results[-1],target)
        self.jobs[:] = [job for job in self.jobs if not job.closed or job.tasks.busy()]
        binding = self.binding(selected_pull=True)
        strict_binding = self.binding()
        dialog_binding = self.binding(selected_pull=getattr(self.dialog, 'selected_pull', False))
        if self.dialog is not None and dialog_binding!=(self.dialog.plan.local,dict(self.dialog.plan.device)['identity']):
            self.dialog.queue.cancel()
        enabled = binding is not None and self.dialog is None and not any(job.tasks.busy() for job in self.jobs)
        self.select.setEnabled(enabled)
        self.clear.setEnabled(enabled and bool(self.window.quest_model.checked))
        self.pull.setEnabled(enabled and self.backend is not None and bool(self.names('quest')))
        self.missing.setEnabled(enabled and strict_binding is not None and self.backend is not None and bool(self.names('missing')))
        self.push.setEnabled(enabled and strict_binding is not None and self.push_backend is not None and bool(self.names('local','to_quest')))
        self.push_missing.setEnabled(enabled and strict_binding is not None and self.push_backend is not None and bool(self.names('missing','to_quest')))
        reason = self.blocked_reason()
        for button in (self.pull,self.missing,self.push,self.push_missing):
            button.setToolTip(reason if not button.isEnabled() else '')
        if self.window.local_ready and self.window.quest_ready and not self.window.identity_checks.complete():
            self.missing.setToolTip(tr('ux.verify_needed'));self.push_missing.setToolTip(tr('ux.verify_needed'))
        self.push.setText(tr('ui.library.push',count=len(self.names('local','to_quest'))))
        self.push_missing.setText(tr('ui.library.push_missing',count=len(self.names('missing','to_quest'))))
        self.push_hint.setText(tr('ui.library.push_hint') if self.push.isEnabled() or self.push_missing.isEnabled() else reason)
        self.push_hint.setToolTip(tr('qt.transfer.push_hint'))
        self.status.setText(tr('qt.transfer.partial_pull_hint' if self.window.quest_incomplete else
            'qt.transfer.pull_hint',count=len(self.names('quest'))))

    def blocked_reason(self):
        w = self.window
        deletion = getattr(w,'deletion',None);playlists = getattr(w,'playlists',None)
        if deletion is not None and deletion.generation is not None:return tr('songs.delete.working')
        if playlists is not None and playlists.device_generation is not None:return tr('qt.playlists.device_working')
        if self.dialog is not None or any(job.tasks.busy() for job in self.jobs):return tr('ux.wait_task')
        if not w.local_ready:return tr('ux.choose_local')
        if w.device_browser.busy or w.device_browser.connection_open:return tr('ux.wait_device')
        if not w.quest_ready:return tr('ux.read_first')
        if w.quest_incomplete:return tr('qt.transfer.partial_pull_hint',count=len(self.names('quest')))
        return tr('ux.select_first')

    def retranslate(self):
        for control,key in ((self.select,'select_quest'),(self.clear,'clear_quest'),(self.pull,'pull_selected'),(self.missing,'pull_missing')):
            control.setText(tr('qt.transfer.'+key))
        if self.backend is not None:
            for control,key in self.window.texts:
                if key=='qt.library.scope':control.setText(tr('qt.transfer.pull_library_scope'))
        self.push.setText(tr('qt.transfer.push_selected'));self.push_missing.setText(tr('qt.transfer.push_missing'))
        self.history.setText(tr('ui.library.history'))
        self.apply_icons()
        if self.push_backend is not None:
            for control,key in self.window.texts:
                if key=='qt.library.scope':control.setText(tr('qt.transfer.push_library_scope'))
        self.render()

    def apply_icons(self):
        palette = get_palette(self.window.mode.currentData())
        self.push.setIcon(line_icon('transfer',palette.ACCENT_TEXT))
        self.push_missing.setIcon(line_icon('transfer',palette.TEXT_PRIMARY))
        self.history.setIcon(line_icon('list',palette.TEXT_PRIMARY))

    def close(self):
        self.closed = True
        self.timer.stop()
        if self.dialog:self.dialog.close()
        for dialog in self.histories:dialog.close()
        for job in self.jobs:job.close()

    def wait_closed(self):
        for job in self.jobs:job.wait_closed()
        for dialog in self.histories:dialog.wait_closed()
