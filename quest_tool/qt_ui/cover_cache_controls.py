"""One explicit background disk-cache clear action, retained through shutdown."""
import threading

from PySide6.QtCore import QObject, QTimer

from ..messages import tr
from ..workers import TaskRunner


def clear_cache(tasks, cache):
    try:
        count = cache.clear()
        tasks.emit('cover_cache_clear', payload={'count': count})
    except Exception:
        tasks.emit('cover_cache_clear', payload={'error': True})


class CoverCacheControls(QObject):
    def __init__(self, window, cache):
        super().__init__(window)
        self.cache = cache
        self.tasks = TaskRunner()
        self.closed = self.busy = False
        self.result = None
        self.reaper = None
        menu = window.options.menu()
        menu.addSeparator()
        self.action = menu.addAction('')
        self.action.triggered.connect(self.clear)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.poll)
        self.retranslate()

    def retranslate(self):
        key = 'qt.cover_disk.busy' if self.busy else ('qt.cover_disk.failed' if self.result == 'error' else
              'qt.cover_disk.cleared' if self.result is not None else 'qt.cover_disk.clear')
        self.action.setText(tr(key, count=self.result if type(self.result) is int else 0))
        self.action.setToolTip(tr('qt.cover_disk.hint'))
        self.action.setEnabled(not self.closed and not self.busy)

    def clear(self):
        if self.closed or self.busy:return
        self.busy = True
        self.retranslate()
        tasks, cache = self.tasks, self.cache
        self.tasks.submit(lambda _token: clear_cache(tasks, cache), name='qt-clear-cover-cache')
        self.timer.start()

    def poll(self):
        if self.closed:return
        for message in self.tasks.drain():
            if message.kind == 'cover_cache_clear':
                self.busy = False
                self.result = 'error' if message.payload.get('error') else message.payload['count']
                self.timer.stop()
                self.retranslate()

    def close(self):
        if self.closed:return
        self.closed = True
        self.timer.stop()
        self.action.setEnabled(False)
        threads = tuple(self.tasks._threads)
        if threads:
            self.reaper = threading.Thread(target=lambda: [thread.join() for thread in threads],
                                           name='cover-cache-clear-shutdown', daemon=False)
            self.reaper.start()

    def wait_closed(self):
        if self.reaper is not None:self.reaper.join()
