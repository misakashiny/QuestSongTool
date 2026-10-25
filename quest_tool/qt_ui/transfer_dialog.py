"""Explicitly injected transfer dialog; all widget updates remain on UI thread."""
from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout
from dataclasses import replace

from ..messages import tr
from ..theme import SPACE


class TransferDialog(QDialog):
    resultReady = Signal(object)
    closedSignal = Signal()
    attemptStarted = Signal()

    def __init__(self, queue, plan, parent=None, *, simulation=True, before_start=None):
        super().__init__(parent)
        self.queue = queue
        self.plan = plan
        self.generation = None
        self.result = None
        self.closed = False
        self.before_start = before_start
        prefix = 'qt.transfer.pull_' if plan.direction=='to_local' else 'qt.transfer.push_'
        QDialog.setWindowTitle(self, tr('qt.transfer.title' if simulation else prefix+'title'))
        QDialog.resize(self, 620, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
        self.scope = QLabel(tr('qt.transfer.offline' if simulation else prefix+'scope'))
        self.scope.setTextFormat(Qt.TextFormat.PlainText)
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        binding = dict(plan.device)
        self.summary = QLabel(tr('qt.transfer.plan', direction=tr('qt.transfer.'+plan.direction),
            count=len(plan.folders), local=plan.local, device=binding['device_id'], root=binding['root']))
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.status = QLabel(tr('qt.transfer.ready' if simulation else prefix+'ready'))
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, len(plan.folders))
        layout.addWidget(self.progress)
        self.failures = QPlainTextEdit()
        self.failures.setReadOnly(True)
        self.failures.setMaximumHeight(130)
        self.failures.hide()
        layout.addWidget(self.failures)
        row = QHBoxLayout()
        self.start = QPushButton(tr('qt.transfer.start' if simulation else prefix+'start'))
        self.pause = QPushButton(tr('qt.transfer.pause'))
        self.cancel_button = QPushButton(tr('qt.transfer.cancel'))
        self.retry = QPushButton(tr('qt.transfer.retry'))
        self.retry.setEnabled(False)
        self.pause.setEnabled(False)
        self.cancel_button.setEnabled(False)
        for button in (self.start, self.pause, self.cancel_button, self.retry):row.addWidget(button)
        layout.addLayout(row)
        self.start.clicked.connect(self.begin)
        self.pause.clicked.connect(self.toggle_pause)
        self.cancel_button.clicked.connect(self.queue.cancel)
        self.retry.clicked.connect(self.prepare_retry)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.poll)

    def begin(self):
        if self.generation is not None or self.closed:return
        try:
            if self.before_start is not None and not self.before_start():
                raise RuntimeError('transfer_binding_changed')
            self.generation = self.queue.start(self.plan)
        except Exception:
            self.status.setText(tr('qt.transfer.blocked'))
            return
        self.attemptStarted.emit()
        self.start.setEnabled(False)
        self.pause.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.failures.clear();self.failures.hide()
        self.timer.start()

    def toggle_pause(self):
        paused = self.queue.toggle_pause()
        self.pause.setText(tr('qt.transfer.resume' if paused else 'qt.transfer.pause'))
        self.status.setText(tr('qt.transfer.paused' if paused else 'qt.transfer.running'))

    def prepare_retry(self):
        # Retry only explicitly failed songs from this attempt. Blocked/unattempted
        # songs are never inferred, and a retry always stops at a new confirmation.
        if self.closed or not self.result or self.queue.tasks.busy():return
        try:
            if self.before_start is not None and not self.before_start():
                raise RuntimeError('transfer_binding_changed')
            names = tuple(name for name,_error in self.result['failures'])
            if not names or any(name not in self.plan.folders for name in names):return
            plan = replace(self.plan,folders=names)
            plan.validate()
        except Exception:
            self.status.setText(tr('qt.transfer.retry_blocked'))
            return
        self.plan = plan
        self.generation = None;self.result = None
        self.retry.setEnabled(False);self.start.setEnabled(True)
        self.progress.setRange(0,len(names));self.progress.setValue(0)
        binding = dict(plan.device)
        self.summary.setText(tr('qt.transfer.plan',direction=tr('qt.transfer.'+plan.direction),
            count=len(names),local=plan.local,device=binding['device_id'],root=binding['root']))
        self.status.setText(tr('qt.transfer.retry_ready'))

    def poll(self):
        if self.closed:return
        for message in self.queue.tasks.drain():
            data = message.payload
            if not isinstance(data, dict) or data.get('generation') != self.generation:continue
            state = data['state']
            if state == 'running':
                self.status.setText(tr('qt.transfer.song', folder=data['folder']))
            elif state == 'progress':
                self.progress.setValue(data['completed'])
            elif state in ('done', 'cancelled', 'blocked'):
                self.result = data
                self.status.setText(tr('qt.transfer.result', state=tr('qt.transfer.'+state),
                    ok=data['ok'], skipped=data['skipped'], failed=len(data['failures'])))
                lines = ([data['error'][:16000]] if data['error'] else [])+[
                    f'{name}: {error}'[:16000] for name,error in data['failures'][:100]]
                self.status.setToolTip('\n'.join(lines)[:16000])
                self.failures.setPlainText('\n'.join(lines)[:16000])
                self.failures.setVisible(bool(lines))
                self.retry.setEnabled(bool(data['failures']))
                self.pause.setEnabled(False)
                self.cancel_button.setEnabled(False)
                self.timer.stop()
                self.resultReady.emit(data)

    def closeEvent(self, event):
        if self.closed:
            super().closeEvent(event)
            return
        self.closed = True
        self.timer.stop()
        self.queue.close()
        self.closedSignal.emit()
        super().closeEvent(event)

    def reject(self):
        self.close()
