"""UI-only read-completion timestamp; elapsed time never probes a device."""
from datetime import datetime
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QLabel

from ..messages import tr


class SnapshotNotice(QObject):
    RECOMMEND_AFTER = 15*60

    def __init__(self, window, clock=time.monotonic, wall_clock=lambda: datetime.now().astimezone()):
        super().__init__(window)
        self.window = window
        self.clock, self.wall_clock = clock, wall_clock
        self.started = None
        self.completed_at = ''
        self.closed = False
        self.label = QLabel(window.retired_controls)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(self.render)
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Type.Resize:self.render()
        return False

    def captured(self):
        if self.closed:return
        self.started = self.clock()
        self.completed_at = self.wall_clock().strftime('%Y-%m-%d %H:%M:%S %z')
        self.timer.start()
        self.render()

    def clear(self):
        self.started = None
        self.completed_at = ''
        self.timer.stop()
        self.label.clear()
        self.label.hide()
        self.window.quest_summary.setToolTip('')
        self.window.quest_summary.setAccessibleName('')

    def render(self):
        if self.closed:return
        visible = self.window.quest_ready and self.started is not None
        if not visible:
            self.timer.stop();return
        seconds = max(0, self.clock()-self.started)
        age = tr('qt.snapshot.just_now') if seconds < 60 else tr('qt.snapshot.minutes', count=int(seconds//60))
        state = tr('qt.snapshot.partial') if self.window.quest_incomplete else tr('qt.snapshot.old') if seconds >= self.RECOMMEND_AFTER else tr('qt.snapshot.cached')
        compact = self.window.height() <= 720
        if compact:
            age = tr('qt.snapshot.now_short') if seconds < 60 else tr('qt.snapshot.minutes_short', count=int(seconds//60))
            state = tr('qt.snapshot.partial_short' if self.window.quest_incomplete else
                       'qt.snapshot.old_short' if seconds >= self.RECOMMEND_AFTER else 'qt.snapshot.cached_short')
        text = tr('qt.snapshot.compact' if compact else 'qt.snapshot.notice', time=self.completed_at[11:16], age=age, state=state)
        if self.label.text() != text:self.label.setText(text)
        hint = tr('qt.snapshot.hint', time=self.completed_at, action=tr('qt.device.read'))
        self.label.setToolTip(hint)
        self.label.setAccessibleName(text+' '+hint)
        full_summary = tr('qt.library.snapshot', source=self.window.quest_display, count=self.window.quest_model.rowCount())
        summary = tr('ui.library.snapshot_count', count=self.window.quest_model.rowCount())
        # Keep the three list headers aligned. The complete capture time and
        # age remain in the hover/accessibility text, without an extra row.
        if self.window.quest_incomplete:summary += ' · '+tr('qt.snapshot.partial_short')
        elif seconds >= self.RECOMMEND_AFTER:summary += ' · '+tr('qt.snapshot.old_short')
        if self.window.quest_summary.text() != summary:self.window.quest_summary.setText(summary)
        self.window.quest_summary.setToolTip(full_summary+'\n'+text+'\n'+hint)
        self.window.quest_summary.setAccessibleName(full_summary+' '+text+' '+hint)

    def close(self):
        if self.closed:return
        self.clear()
        self.closed = True
