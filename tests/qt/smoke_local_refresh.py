"""Offline burst coalescing, bounded follow-up, target generations and close."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.environ.get('QUEST_REFRESH_BASELINE_ROOT', str(ROOT)))


def child(baseline=False):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.song_search import SearchSession
    from quest_tool.workers import TaskRunner

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value)); sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    def wait(predicate):
        deadline = time.monotonic()+5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10); time.sleep(.002)
        assert predicate() and not errors, errors
    class Manager:
        def __init__(self, root):
            self.history = root/'history.json'
            self.tasks = TaskRunner()
            self.last_keep_zip = False
            self.records = [dict(id=str(i), title='Song', source=str(i), state='downloading', progress=.1,
                                 error='', installed='', target='isolated', kind='online') for i in range(50)]
        def snapshot(self):
            return copy.deepcopy(self.records)
        def shutdown(self):
            pass
    class Session:
        def __init__(self):
            self.tasks = TaskRunner()
            self.calls = []
            self.generation = 0
            self.closed = False
        def request(self, target, *_args):
            self.generation += 1
            self.calls.append(target)
            return self.generation
        def close(self):
            self.closed = True
        def finish(self, target, generation=None, error=False, title='Current'):
            gen = self.generation if generation is None else generation
            payload = {'generation': gen, 'code': 'network'} if error else {
                'generation': gen, 'result': {'target': target, 'rows': [
                    dict(title=title, author='Test', duration=120, folder=title)]}}
            self.tasks.emit('search_error' if error else 'search', payload=payload)

    with tempfile.TemporaryDirectory(prefix='quest-local-refresh-') as folder:
        root = Path(folder)
        manager = Manager(root)
        window = DownloadWindow()
        window.enable_downloads(root, manager)
        queue, panel = window.download_browser, window.download_browser.importer
        wait(lambda: not panel.scanning)
        target = str(queue.target)
        if baseline:
            calls = []
            panel.refresh_local = lambda: calls.append(1)
            for record in manager.records: record.update(state='done', target=target)
            queue.poll()
            assert len(calls) == 50
            window.close(); app.processEvents(); queue.wait_closed()
            print(json.dumps(dict(completed_in_one_poll=50, local_refresh_requests=len(calls))))
            return
        panel.session.close(); panel.wait_closed()
        session = Session(); panel.session = session
        signals = []
        panel.localRefreshed.connect(signals.append)
        for record in manager.records: record.update(state='done', target=target)
        queue.poll()
        assert panel.refresh_pending and panel.scanning and panel.automatic_refresh.isActive()
        assert not session.calls
        wait(lambda: len(session.calls) == 1)
        assert session.calls == [target]
        queue.poll(); assert len(session.calls) == 1
        for _ in range(100): panel.request_refresh()
        assert len(session.calls) == 1 and panel.refresh_pending
        session.finish(target); panel.poll()
        assert panel.scanning and signals == [target]
        wait(lambda: len(session.calls) == 2)
        session.finish(target, title='Final'); panel.poll()
        assert not panel.scanning and not panel.refresh_pending and not panel.automatic_refresh.isActive()
        assert panel.model.rows[0]['title'] == 'Final'
        QTest.qWait(300); assert len(session.calls) == 2
        session.finish(target); panel.poll()
        assert len(signals) == 2  # A duplicate terminal message cannot refresh the main library again.
        # Auto-refresh is deferred; explicit refresh and directory changes supersede it immediately.
        panel.request_refresh()
        calls = len(session.calls)
        panel.refresh_local()
        assert len(session.calls) == calls+1 and not panel.refresh_pending
        old_gen = session.generation
        second = root/'second'; second.mkdir()
        assert queue.set_target(str(second))
        assert session.calls[-1] == str(second.resolve()) and not panel.model.rows
        session.finish(target, generation=old_gen, title='Late old directory')
        panel.poll()
        assert panel.scanning and not panel.model.rows
        session.finish(str(second.resolve()), title='New directory'); panel.poll()
        assert not panel.scanning and panel.model.rows[0]['title'] == 'New directory'
        # Matching-generation but mismatched target is rejected without corrupting the current rows.
        panel.refresh_local(); session.finish(target); panel.poll()
        assert panel.error and not panel.scanning and panel.model.rows[0]['title'] == 'New directory'
        panel.refresh_local(); session.finish(str(second.resolve()), error=True); panel.poll()
        assert panel.error and not panel.scanning
        panel.refresh_local(); session.finish(str(second.resolve())); panel.poll()
        assert not panel.error and not panel.scanning
        # A completed job for another target cannot schedule a scan of this directory.
        manager.records.append(dict(manager.records[0], id='external', target=target))
        queue.poll()
        assert not panel.refresh_pending and not panel.automatic_refresh.isActive()
        panel.request_refresh()
        calls = len(session.calls)
        window.close(); app.processEvents(); QTest.qWait(300)
        assert len(session.calls) == calls and session.closed and not panel.automatic_refresh.isActive()
        assert not panel.refresh_pending and not panel.scanning and not errors
        metrics = dict(completed_in_one_poll=50, burst_scan_requests=1,
                       notifications_during_running_scan=100, follow_up_scans=1)

    # Real message-only worker: automatic requests must not cancel an active read.
    class Client:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.calls = []
        def search(self, target, _page, _order, cancel):
            self.calls.append((target, cancel, threading.get_ident()))
            if len(self.calls) == 1:
                self.entered.set()
                assert self.release.wait(5)
                assert not cancel.is_set(), 'Automatic refresh cancelled the active read'
            return {'target': target, 'rows': []}
    with tempfile.TemporaryDirectory(prefix='quest-local-thread-') as folder:
        window = DownloadWindow(); window.enable_downloads(folder, Manager(Path(folder)))
        queue, panel = window.download_browser, window.download_browser.importer
        wait(lambda: not panel.scanning)
        panel.session.close(); panel.wait_closed()
        client = Client(); panel.session = SearchSession(client)
        mutations = []
        panel.model.modelReset.connect(lambda: mutations.append(threading.get_ident()))
        panel.refresh_local()
        wait(client.entered.is_set)
        for _ in range(100): panel.request_refresh()
        assert len(client.calls) == 1 and not client.calls[0][1].is_set()
        client.release.set()
        wait(lambda: len(client.calls) == 2 and not panel.scanning)
        assert len(client.calls) == 2 and set(mutations) == {threading.get_ident()}
        assert all(ident != threading.get_ident() for _, _, ident in client.calls)
        window.close(); app.processEvents(); queue.wait_closed()
        assert all(not t.is_alive() for t in panel.session.tasks._threads)
    assert 'miniaudio' not in sys.modules and not errors
    destination = os.environ.get('QUEST_QT_SHOTS')
    if destination:
        path = Path(destination); path.mkdir(parents=True, exist_ok=True)
        (path/'local-refresh-metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    print('PASS burst/follow-up/manual/target/late/duplicate/error/close/real worker/UI-thread isolation')
    print(json.dumps(metrics))


def main():
    if '--baseline' in sys.argv:
        child(True)
    elif '--child' in sys.argv:
        child()
    else:
        for scale in ('1', '1.25', '1.5'):
            result = subprocess.run([sys.executable, '-B', __file__, '--child'], cwd=ROOT,
                env=dict(os.environ, QT_SCALE_FACTOR=scale, PYTHONIOENCODING='utf-8'),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            print(scale, result.stdout.decode('utf-8', errors='replace'), flush=True)
            assert result.returncode == 0


if __name__ == '__main__':
    main()
