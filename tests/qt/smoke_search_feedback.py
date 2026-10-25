"""Offline cache feedback, expiry, real F5 and refresh button interactions."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QSignalSpy, QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.messages import tr
    from quest_tool.qt_ui.download_window import DownloadWindow
    from smoke_prefetch import Session

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    for language, theme in (('zh', 'light'), ('en', 'dark')):
        front, back = Session(), Session()
        window = DownloadWindow(language=language, mode=theme)
        window.enable_search(front, back)
        browser, model = window.browser, window.song_model
        browser.prefetch_delay.setInterval(5000)
        window.show()
        window.activateWindow()
        assert QTest.qWaitForWindowExposed(window, 5000)
        assert QTest.qWaitForWindowActive(window, 5000)
        front.finish(0); browser.poll()
        assert window.note.text() == tr('qt.search.next_planned')
        browser.prefetch_delay.stop(); browser.start_prefetch()
        assert window.note.text() == tr('qt.search.next_loading')
        back.finish(1); browser.poll()
        assert window.note.text() == tr('qt.search.next_ready') and browser.status_expiry.isActive()
        key = (browser.context, 1)
        order = tuple(browser.cache.entries)
        expiry = browser.cache.entries[key][0]
        browser.refresh_status()
        assert tuple(browser.cache.entries) == order and browser.cache.entries[key][0] == expiry
        # Drive the real single-shot expiry callback with a fake monotonic clock.
        now = browser.cache.clock()
        browser.cache.clock = lambda: now+121
        expiry_signal = QSignalSpy(browser.status_expiry.timeout)
        browser.status_expiry.start(5)
        QTest.qWait(30)
        # A long paint can consume qWait's budget before the due timer is
        # dispatched. Drain pending events without adding another sleep.
        app.processEvents()
        assert expiry_signal.count() == 1
        assert tr('qt.search.next_ready') not in window.note.text()
        assert not browser.status_expiry.isActive() and len(back.calls) == 1
        browser.cache.clock = __import__('time').monotonic
        browser.cache.put(key, tuple(model.visible_songs()), True)
        model.setData(model.index(0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        checked = set(model.checked)
        browser.request(1)
        browser.prefetch_delay.stop()
        assert tr('qt.search.cached') in window.note.text() and model.page == 1
        window.search.setText('unsubmitted draft')
        window.search.setFocus()
        calls = len(front.calls)
        QTest.keyClick(window.search, Qt.Key.Key_F5)
        QTest.qWait(15)
        assert len(front.calls) == calls+1 and front.calls[-1][0] == '' and front.calls[-1][1] == 1
        assert model.page == 1 and model.rowCount() == 21 and set(model.checked) == checked
        assert not browser.cache.entries and not browser.refresh_button.isEnabled()
        QTest.keyClick(window.search, Qt.Key.Key_F5)
        assert len(front.calls) == calls+1
        front.finish(1); browser.poll(); browser.prefetch_delay.stop()
        assert tr('qt.search.cached') not in window.note.text()
        assert window.search.text() == 'unsubmitted draft' and set(model.checked) == checked
        calls = len(front.calls)
        window.tabs.setCurrentIndex(1)
        QTest.keyClick(window, Qt.Key.Key_F5)
        assert not browser.refresh_shortcut.isEnabled() and len(front.calls) == calls
        window.tabs.setCurrentIndex(0)
        browser.open_filters()
        QTest.qWait(30)
        QTest.keyClick(browser.filter_window, Qt.Key.Key_F5)
        assert len(front.calls) == calls
        browser.filter_window.reject(); QTest.qWait(20)
        window.activateWindow()
        assert QTest.qWaitForWindowActive(window, 5000)
        QTest.mouseClick(browser.refresh_button, Qt.MouseButton.LeftButton)
        assert len(front.calls) == calls+1 and front.calls[-1][1] == 1
        front.finish(1); browser.poll(); browser.prefetch_delay.stop()
        window.resize(1000, 760)
        QTest.qWait(40)
        controls = (window.category_title, window.select_page, browser.refresh_button, window.columns, window.rows)
        assert all(widget.width() >= widget.minimumSizeHint().width() for widget in controls)
        assert browser.refresh_button.isVisible() and 'F5' in browser.refresh_button.toolTip()
        for left, right in zip(controls, controls[1:]):
            assert left.geometry().right() < right.geometry().left()
        shots = Path(os.environ.get('QUEST_QT_SHOTS', ROOT/'tests/qt/artifacts'))
        shots.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(shots/f'feedback-{language}-{theme}.png'))
        browser.request(2)
        front.finish(2, error='network'); browser.poll()
        assert window.note.text() == tr('dl.error.network') and not browser.status_expiry.isActive()
        browser.cancel()
        assert window.note.text() == tr('qt.search.cancelled')
        window.close(); app.processEvents()
        assert not browser.status_expiry.isActive() and not browser.refresh_shortcut.isEnabled()
        assert front.closed and back.closed and not errors, errors
    assert 'miniaudio' not in sys.modules
    print('PASS feedback/expiry/no-LRU-touch/F5/button/drafts/checks/modal/tab guards/close; zh-light/en-dark')


def main():
    if '--child' in sys.argv:
        child()
        return
    for scale in ('1', '1.25', '1.5'):
        env = dict(os.environ, QT_SCALE_FACTOR=scale, PYTHONIOENCODING='utf-8')
        result = subprocess.run([sys.executable, '-B', __file__, '--child'], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        print(scale, result.stdout.decode('utf-8', errors='replace'), flush=True)
        assert result.returncode == 0


if __name__ == '__main__':
    main()
