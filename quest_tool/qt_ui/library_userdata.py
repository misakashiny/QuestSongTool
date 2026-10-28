"""Local ratings in a sandbox database; serialized writes survive selection changes."""
from pathlib import Path
from queue import Queue

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit,
    QPushButton, QSizePolicy, QStyle, QVBoxLayout, QWidget,
)

from ..database import Database
from ..fsapi import Location
from ..messages import tr
from ..workers import TaskRunner
from .surfaces import action_icons


class UserDataQueue:
    def __init__(self, sandbox):
        self.root = Path(sandbox).resolve()
        self.path = self.root / 'qt-library-userdata.sqlite3'
        self.tasks = TaskRunner()
        self.jobs = Queue()
        self.started = False
        self.closed = False
        self.sequence = 0
        self.latest_read = 0
        self.latest_ratings = 0

    def submit(self, key, action='get', value=None):
        if self.closed: raise RuntimeError('closed')
        self.sequence += 1
        job = dict(sequence=self.sequence, key=tuple(key), action=action, value=value)
        if action == 'get': self.latest_read = self.sequence
        if action == 'ratings': self.latest_ratings = self.sequence
        self.jobs.put(job)
        if not self.started:
            self.started = True
            self.tasks.submit(self.run, name='qt-userdata')
        return self.sequence

    def run(self, _token):
        db = None
        try:
            while True:
                job = self.jobs.get()
                if job is None: return
                if ((job['action'] == 'get' and job['sequence'] != self.latest_read)
                        or (job['action'] == 'ratings' and job['sequence'] != self.latest_ratings)):
                    self.tasks.emit('userdata_skip', payload=job)
                    continue
                try:
                    origin, root, folder = job['key']
                    if origin != 'local' or (not folder and job['action'] != 'ratings') or folder in ('.', '..') or any(c in folder for c in '/\\'):
                        raise ValueError('identity')
                    if db is None:
                        self.root.mkdir(parents=True, exist_ok=True)
                        if self.root.resolve() != self.root or self.path.is_symlink(): raise ValueError('sandbox')
                        db = Database(self.path)
                    identity = Location('local', root).identity
                    action, value = job['action'], job['value']
                    if action == 'ratings':
                        ratings = db.all_ratings(identity)
                        if any(type(v) is not int or not 0 <= v <= 5 for v in ratings.values()):raise ValueError('ratings')
                        self.tasks.emit('userdata', payload=dict(job, ratings=ratings))
                        continue
                    if action == 'rating':
                        if type(value) is not int or not 0 <= value <= 5: raise ValueError('rating')
                        db.set_rating(identity, folder, value)
                    elif action == 'comment':
                        if not isinstance(value, str) or len(value.strip()) > 500: raise ValueError('comment')
                        db.set_comment(identity, folder, value.strip())
                    elif action != 'get': raise ValueError('action')
                    rating, comment = db.get_rating(identity, folder)
                    self.tasks.emit('userdata', payload=dict(job, rating=rating, comment=comment))
                except Exception:
                    self.tasks.emit('userdata_error', payload=job)
        finally:
            if db is not None: db.close()

    def close(self):
        if not self.closed:
            self.closed = True
            if self.started: self.jobs.put(None)

    def wait_closed(self):
        for thread in tuple(self.tasks._threads): thread.join()


class CommentDialog(QDialog):
    def __init__(self, parent, initial):
        super().__init__(parent)
        super().setWindowTitle(tr('qt.userdata.edit'))
        super().setWindowModality(Qt.WindowModality.WindowModal)
        super().resize(500, 340)
        box = QVBoxLayout(self)
        self.editor = QPlainTextEdit(); self.editor.setPlainText(initial); box.addWidget(self.editor)
        self.counter = QLabel(); box.addWidget(self.counter)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr('qt.userdata.save'))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('qt.userdata.cancel'))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setProperty('role','primary')
        action_icons((self.buttons.button(QDialogButtonBox.StandardButton.Save),'save'),(self.buttons.button(QDialogButtonBox.StandardButton.Cancel),'close'))
        self.buttons.accepted.connect(self.save); self.buttons.rejected.connect(self.reject)
        box.addWidget(self.buttons)
        self.editor.textChanged.connect(self.update_count)
        self.shortcut = QShortcut(QKeySequence('Ctrl+Return'), self); self.shortcut.activated.connect(self.save)
        self.update_count()

    def update_count(self):
        length = len(self.editor.toPlainText().strip())
        self.counter.setText(f'{length} / 500')
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(length <= 500)

    def save(self):
        if len(self.editor.toPlainText().strip()) <= 500: super().accept()


class LibraryUserData(QObject):
    ratingsResult = Signal(object)
    ratingConfirmed = Signal(object, int)
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.queue = UserDataQueue(window.sandbox)
        self.key = None
        self.sequence = None
        self.pending = False
        self.failed = False
        self.rating_value = 0
        self.comment_value = ''
        self.failures = {}
        self.active = {}
        self.dialog = None
        self.closed = False
        self.page = QWidget(); box = QVBoxLayout(self.page); box.setContentsMargins(0, 0, 0, 0)
        self.caption = QLabel(); box.addWidget(self.caption)
        self.rating = QComboBox()
        self.rating.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.rating.setMinimumContentsLength(6)
        for value in range(6): self.rating.addItem('', value)
        self.rating.currentIndexChanged.connect(self.set_rating); box.addWidget(self.rating)
        self.comment = QLabel(); self.comment.setWordWrap(True); self.comment.setTextFormat(Qt.TextFormat.PlainText)
        self.comment.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); box.addWidget(self.comment)
        self.edit = QPushButton(); self.edit.clicked.connect(self.edit_comment); box.addWidget(self.edit)
        self.state = QLabel(); self.state.setWordWrap(True); box.addWidget(self.state)
        for label in (self.caption, self.comment, self.state):
            label.setWordWrap(True)
            policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            policy.setHeightForWidth(True); label.setSizePolicy(policy)
        self.retry = QPushButton(); self.retry.clicked.connect(self.retry_current); box.addWidget(self.retry)
        action_icons((self.edit,'edit'),(self.retry,'refresh'))
        window.details.extras.addWidget(self.page)
        window.details.selectionChanged.connect(self.selection_changed)
        self.timer = QTimer(self); self.timer.setInterval(50); self.timer.timeout.connect(self.poll)
        self.retranslate()

    def selection_changed(self):
        if self.closed: return
        if self.dialog is not None: self.dialog.reject()
        key = self.window.details.key
        self.key = tuple(key) if key is not None and key[0] == 'local' else None
        self.rating_value = 0; self.comment_value = ''; self.failed = False
        self.pending = self.key is not None
        self.sequence = self.request(self.key) if self.key is not None else None
        if self.pending: self.timer.start()
        self.render()

    def request(self, key, action='get', value=None):
        sequence = self.queue.submit(key, action, value)
        self.active[sequence] = (tuple(key), action, value); self.timer.start()
        return sequence

    def send(self, action, value):
        if self.closed or self.key is None or self.pending: return
        self.sequence = self.request(self.key, action, value)
        self.pending = True; self.failed = False; self.timer.start(); self.render()

    def set_rating(self, _index):
        self.send('rating', self.rating.currentData())

    def edit_comment(self):
        if self.closed or self.key is None or self.pending or self.failed: return
        key = self.key
        failed = self.failures.get((key, 'comment'))
        dialog = CommentDialog(self.window, failed['value'] if failed else self.comment_value)
        self.dialog = dialog
        def finished(result):
            self.dialog = None
            if result == QDialog.DialogCode.Accepted and not self.closed and self.key == key:
                self.send('comment', dialog.editor.toPlainText().strip())
            dialog.deleteLater()
        dialog.finished.connect(finished); dialog.open()

    def retry_current(self):
        if self.closed or self.key is None or self.pending: return
        jobs = sorted((job for (key, _action), job in self.failures.items() if key == self.key), key=lambda job:job['sequence'])
        if jobs:
            for job in jobs: self.sequence = self.request(self.key, job['action'], job['value'])
        else: self.sequence = self.request(self.key)
        self.pending = True; self.failed = False; self.timer.start(); self.render()

    def poll(self):
        if self.closed: return
        messages = self.queue.tasks.drain()
        if not messages: return
        for message in messages:
            if message.kind not in ('userdata', 'userdata_error', 'userdata_skip'): continue
            job = message.payload
            expected = self.active.get(job.get('sequence'))
            if expected != (tuple(job.get('key', ())), job.get('action'), job.get('value')): continue
            self.active.pop(job['sequence'])
            if job['action'] == 'ratings':
                self.ratingsResult.emit(dict(job,failed=message.kind=='userdata_error',skipped=message.kind=='userdata_skip'))
                continue
            if message.kind == 'userdata_skip': continue
            job = message.payload; key = tuple(job['key']); identity = (key, job['action'])
            failed = message.kind == 'userdata_error'
            if job['action'] != 'get':
                if failed:
                    self.failures[identity] = job
                    self.window.statusBar().showMessage(tr('qt.userdata.save_failed', song=key[2]), 10000)
                else: self.failures.pop(identity, None)
            if not failed and job['action'] in ('get','rating'):
                self.ratingConfirmed.emit(key,job['rating'])
            if key != self.key or job['sequence'] != self.sequence: continue
            self.pending = False; self.failed = failed
            if not failed: self.rating_value = job['rating']; self.comment_value = job['comment']
        if not self.active: self.timer.stop()
        self.render()

    def render(self):
        enabled = self.key is not None and not self.pending and not self.failed and not self.closed
        self.rating.setEnabled(enabled); self.edit.setEnabled(enabled)
        self.rating.blockSignals(True); self.rating.setCurrentIndex(self.rating_value); self.rating.blockSignals(False)
        self.comment.setText(self.comment_value or tr('qt.userdata.empty'))
        retry = self.failed or any(key == self.key for key, _action in self.failures)
        state = 'qt.userdata.unavailable' if self.key is None else 'qt.userdata.pending' if self.pending else 'qt.userdata.failed' if retry else 'qt.userdata.ready'
        self.state.setText(tr(state)); self.retry.setVisible(retry); self.retry.setEnabled(not self.pending and not self.closed)

    def retranslate(self):
        self.caption.setText(tr('qt.userdata.rating')); self.edit.setText(tr('qt.userdata.edit'))
        self.retry.setText(tr('qt.userdata.retry'))
        for value in range(6): self.rating.setItemText(value, tr('qt.userdata.none') if value == 0 else f'{value} / 5')
        self.rating.setAccessibleName(tr('qt.userdata.rating')); self.rating.setToolTip(tr('qt.userdata.clear_hint'))
        self.fit_width(); self.render()

    def fit_width(self):
        for control in (self.rating, self.edit): control.ensurePolished()
        detail = self.window.details
        outer = detail.page.layout().contentsMargins()
        inner = detail.scroll.widget().layout().contentsMargins()
        width = max(self.rating.minimumSizeHint().width(), self.edit.minimumSizeHint().width())
        width += outer.left()+outer.right()+inner.left()+inner.right()
        width += 2*(detail.page.frameWidth()+detail.scroll.frameWidth())
        width += detail.scroll.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        detail.page.setMinimumWidth(width)

    def close(self):
        self.closed = True; self.timer.stop()
        if self.dialog is not None: self.dialog.reject()
        self.queue.close()

    def wait_closed(self):
        self.queue.wait_closed()
