"""Offline result counts, hidden selection safety and existing retry routing."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.downloads import TERMINAL
    from quest_tool.messages import tr
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.workers import TaskRunner

    class Manager:
        def __init__(self, folder):
            self.history = folder/'history.json'
            self.tasks = TaskRunner()
            self.calls = []
            self.stopped = False
            self.last_keep_zip = False
            states = ('done', 'exists', 'failed', 'interrupted', 'cancelled', 'downloading', 'queued', 'failed')
            self.records = [dict(id=str(index), title='Same title\twith\nlines', source=str(index),
                state=state, progress=1 if state in ('done', 'exists') else .2, error='network' if state == 'failed' else '',
                installed='', target=str(folder/('other' if index == 7 else 'songs')), kind='online')
                for index, state in enumerate(states)]

        def snapshot(self): return copy.deepcopy(self.records)
        def retry(self, key):
            self.calls.append(('retry', key))
            row = dict(next(row for row in self.records if row['id'] == key))
            row.update(id='new-'+key, state='queued', error='', progress=0)
            self.records.append(row)
        def cancel(self, key):
            self.calls.append(('cancel', key))
            next(row for row in self.records if row['id'] == key)['state'] = 'cancelled'
        def clear_finished(self):
            self.calls.append(('clear',))
            self.records = [row for row in self.records if row['state'] not in TERMINAL]
        def shutdown(self): self.stopped = True

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value)); sys.__excepthook__(kind, value, tb)
    sys.excepthook = report

    def settle(queue):
        deadline = time.monotonic()+5
        while (queue.busy or queue.importer.scanning) and time.monotonic() < deadline:
            queue.poll(); app.processEvents(); time.sleep(.005)
        assert not queue.busy and not queue.importer.scanning

    for language, theme in (('zh', 'light'), ('en', 'dark')):
        with tempfile.TemporaryDirectory(prefix='quest-results-') as folder:
            manager = Manager(Path(folder))
            window = DownloadWindow(language=language, mode=theme)
            window.enable_downloads(folder, manager)
            queue = window.download_browser
            results = queue.results
            settle(queue)
            window.show_tasks()
            window.show(); window.resize(1000, 760)
            QTest.qWait(30)
            expected = tr('qt.results.summary', total=8, done=1, exists=1, failed=2,
                          interrupted=1, cancelled=1, active=2)
            assert results.summary.text() == expected
            # Idle queue polls must not rewrite the summary or filter every time.
            notifications = []
            original = results.summary.setText
            results.summary.setText = lambda text: (notifications.append(text), original(text))[-1]
            for _ in range(100): queue.poll()
            assert not notifications and not manager.calls
            queue.table.selectRow(6)
            assert queue.cancel.isEnabled()
            results.filter.setCurrentIndex(results.filter.findData('attention'))
            assert not queue.table.selectionModel().selectedRows() and not queue.cancel.isEnabled()
            shown = [i for i in range(8) if not queue.table.isRowHidden(i)]
            assert shown == [2, 3, 7] and not manager.calls
            QTest.mouseClick(results.copy_button, Qt.MouseButton.LeftButton)
            copied = app.clipboard().text()
            assert len(copied.splitlines()) == 5
            assert tr('dl.state.failed') in copied and tr('dl.state.done') not in copied
            assert all(len(line.split('\t')) == 7 for line in copied.splitlines()[1:])
            assert {line.split('\t')[-1] for line in copied.splitlines()[2:]} == {'2', '3', '7'}
            assert str(Path(folder)/'other') in copied and tr('dl.error.network') in copied
            # An imported history job for another target still hits the original guard.
            queue.table.selectRow(7); queue.retry.click()
            assert queue.error == 'target' and not manager.calls
            queue.table.selectRow(2); queue.retry.click(); settle(queue)
            assert manager.calls == [('retry', '2')]
            assert queue.table.isRowHidden(8) and not queue.table.isRowHidden(2)
            assert queue.table.selectionModel().selectedRows()[0].data(Qt.ItemDataRole.UserRole) == '2'
            results.filter.setCurrentIndex(results.filter.findData('active'))
            queue.table.selectRow(6)
            manager.records[6]['state'] = 'failed'
            queue.poll()
            assert queue.table.isRowHidden(6) and not queue.table.selectionModel().selectedRows()
            assert not queue.cancel.isEnabled()
            # Cancelling still uses selected IDs through the original worker path.
            queue.table.selectRow(5); queue.cancel.click(); settle(queue)
            assert manager.calls[-1] == ('cancel', '5') and queue.table.isRowHidden(5)
            results.filter.setCurrentIndex(0)
            window.retranslate('en' if language == 'zh' else 'zh')
            assert results.filter.currentData() == 'all' and results.summary.text().startswith(tr('qt.results.summary').split('{')[0])
            window.retranslate(language)
            assert results.filter.geometry().right() < results.copy_button.geometry().left()
            assert results.summary.geometry().bottom() < queue.table.geometry().top()
            shots = os.environ.get('QUEST_QT_SHOTS')
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                window.grab().save(str(Path(shots)/('queue-results-'+theme+'.png')))
            # Clearing history retains the original all-terminal semantics, not just visible rows.
            results.filter.setCurrentIndex(results.filter.findData('attention'))
            queue.clear_history.click(); settle(queue)
            assert [row['id'] for row in manager.records] == ['new-2']
            assert not results.copy_button.isEnabled() and not queue.clear_history.isEnabled()
            assert results.visible.text() == tr('qt.results.visible', count=0, total=1)
            window.close(); app.processEvents(); queue.wait_closed()
            clipboard = app.clipboard().text()
            results.copy_results()
            assert app.clipboard().text() == clipboard and manager.stopped
    assert not errors and 'miniaudio' not in sys.modules, errors
    print('PASS result counts/history, state filters, no idle summary updates, copied visible errors/targets, hidden selection, retry/cancel guards, clear semantics/language/scale/close')


def main():
    if '--child' in sys.argv:
        child(); return
    for scale in ('1', '1.25', '1.5'):
        result = subprocess.run([sys.executable, '-B', __file__, '--child'], cwd=ROOT,
            env=dict(os.environ, QT_SCALE_FACTOR=scale, PYTHONIOENCODING='utf-8'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        print(scale, result.stdout.decode('utf-8', errors='replace'), flush=True)
        assert result.returncode == 0


if __name__ == '__main__': main()
