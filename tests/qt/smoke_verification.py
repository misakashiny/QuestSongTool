"""Recorded-path verification, bounded reads, UI generations and no audio/device."""
from dataclasses import replace
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def song(folder):
    from quest_tool.downloads import validate_song
    folder.mkdir()
    info = {'_version': '2.0.0', '_songName': 'Fixture', '_songFilename': 'song.egg',
            '_difficultyBeatmapSets': [{'_difficultyBeatmaps': [{'_beatmapFilename': 'Expert.dat'}]}]}
    (folder/'Info.dat').write_text(json.dumps(info), encoding='utf-8')
    (folder/'Expert.dat').write_text('{"_notes":[]}', encoding='utf-8')
    (folder/'song.egg').write_bytes(b'audio fixture, never played')
    return validate_song(folder)[1]


def record(target, installed, digest, identity='one', source='1'):
    return dict(id=identity, target=str(target), installed=str(installed), digest=digest,
                state='done', kind='online', source=source, title='Fixture', progress=1, error='')


def service():
    from quest_tool.downloads import DownloadError, validate_song
    from quest_tool.qt_ui.local_verification import VerificationClient
    from quest_tool.qt_ui.song_model import Song
    from quest_tool.qt_ui.song_status import StatusIndex
    from quest_tool.messages import tr
    with tempfile.TemporaryDirectory(prefix='quest-verification-') as directory:
        base = Path(directory).resolve(); target = base/'songs'; target.mkdir()
        installed = target/'fixture'; digest = song(installed)
        row = record(target, installed, digest)
        cancel = threading.Event(); client = VerificationClient()
        def scan(rows):return client.search({'target': str(target), 'rows': rows}, 0, '', cancel)['records']
        before = {str(p): p.read_bytes() for p in installed.iterdir()}
        remote = Song('1', 'Fixture', '', '', None, None, None, digest)
        values = scan([row])
        assert values['one'] == {'state': 'valid', 'digest': digest}
        assert StatusIndex([row], target, values).label(remote) == tr('qt.verify.same')
        assert StatusIndex([row], target, values).label(replace(remote, digest='f'*40)) == tr('qt.verify.other')
        assert all(p.read_bytes() == before[str(p)] for p in installed.iterdir())
        with patch('quest_tool.qt_ui.local_verification.validate_song', wraps=validate_song) as validate:
            assert len(scan([row, dict(row, id='two')])) == 2 and validate.call_count == 1
        with patch('quest_tool.qt_ui.local_verification.validate_song', side_effect=AssertionError('escaped read')):
            assert scan([dict(row, installed=str(base))])['one']['state'] == 'unknown'
            assert scan([dict(row, installed=str(target/'..'))])['one']['state'] == 'unknown'
        missing = dict(row, installed=str(target/'missing'))
        values = scan([missing]); assert values['one']['state'] == 'missing'
        assert StatusIndex([missing], target, values).label(remote) == tr('qt.verify.missing')
        (installed/'Expert.dat').write_text('{"_notes":[1]}', encoding='utf-8')
        changed = scan([row]); assert changed['one']['digest'] != digest
        assert StatusIndex([row], target, changed).label(remote) == tr('qt.verify.changed')
        (installed/'song.egg').write_bytes(b'')
        assert scan([row])['one']['state'] == 'unknown'
        (installed/'song.egg').write_bytes(b'audio')
        client.MAX_BYTES = 1
        assert scan([row])['one']['state'] == 'unknown'
        client.MAX_BYTES = 256*1024*1024
        client.MAX_RECORDS = 0
        assert scan([row])['one']['reason'] == 'limit'
        client.MAX_RECORDS = 512
        def mutate(folder):
            value = validate_song(folder)
            (folder/'Expert.dat').write_text('{"_notes":[2,3]}', encoding='utf-8')
            return value
        with patch('quest_tool.qt_ui.local_verification.validate_song', side_effect=mutate):
            assert scan([row])['one'] == {'state': 'unknown', 'reason': 'changed'}
        # Reject actual Windows junctions before reading referenced map/audio data.
        outside = base/'outside'; song(outside)
        junction = target/'linked'
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True, timeout=10)
        assert result.returncode == 0
        try:
            with patch('quest_tool.qt_ui.local_verification.validate_song', side_effect=AssertionError('link read')):
                assert scan([dict(row, installed=str(junction))])['one']['state'] == 'unknown'
        finally:junction.rmdir()
        cancel.set()
        try:scan([row])
        except DownloadError as exc:assert exc.code == 'cancelled'
        else:raise AssertionError('cancel ignored')
    print('PASS real filesystem validation, digest change, missing/invalid, path/junction, dedup, budgets, change-during-read and cancellation')


def ui():
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from quest_tool.messages import tr
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import sample_songs
    from quest_tool.song_search import SearchSession
    from quest_tool.workers import TaskRunner
    app = QApplication([])
    errors = []
    sys.excepthook = lambda kind, value, tb: errors.append(str(value))
    def wait(predicate):
        deadline = time.monotonic()+6
        while not predicate() and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.003)
        assert predicate() and not errors, errors
    class Manager:
        def __init__(self, root):
            self.history = root/'history.json'; self.tasks = TaskRunner()
            self.last_keep_zip = False; self.rows = []
        def snapshot(self):return copy.deepcopy(self.rows)
        def shutdown(self):pass
    class Controlled:
        def __init__(self):self.tasks = TaskRunner();self.generation = 0;self.calls = []
        def request(self, payload, *_args):
            self.generation += 1;self.calls.append(copy.deepcopy(payload));return self.generation
        def cancel(self):pass
        def close(self):pass
        def finish(self, generation, target, values):
            self.tasks.emit('search', payload={'generation': generation, 'result': {'target': target, 'records': values}})
    with tempfile.TemporaryDirectory(prefix='quest-verification-ui-') as directory:
        root = Path(directory).resolve(); target = root/'songs'; target.mkdir()
        installed = target/'fixture'; digest = song(installed)
        manager = Manager(root); manager.rows = [record(target, installed, digest)]
        remote = replace(sample_songs()[0], key='1', digest=digest)
        window = DownloadWindow(songs=[remote]); window.enable_downloads(root, manager)
        queue = window.download_browser; verification = queue.verification
        window.show()
        wait(lambda: not verification.busy)
        assert window.song_model.status_labels['1'] == tr('qt.verify.same')
        assert verification.checked_at and verification.action.isEnabled()
        window.retranslate('en')
        assert verification.action.text() == tr('qt.verify.action')
        window.retranslate('zh')
        # Deleting externally is detected on explicit recheck, never by titles.
        (installed/'Expert.dat').unlink()
        verification.action.trigger()
        assert verification.busy and not verification.action.isEnabled()
        assert window.song_model.status_labels['1'] == tr('qt.selection.same_version')
        wait(lambda: not verification.busy)
        assert window.song_model.status_labels['1'] == tr('qt.verify.unknown')
        verification.session.close();verification.wait_closed()
        controlled = Controlled(); verification.session = controlled
        verification.request(); wait(lambda: len(controlled.calls) == 1)
        first = verification.generation
        other = root/'other'; other.mkdir(); assert queue.set_target(str(other))
        assert verification.records == {} and not window.song_model.status_labels['1']
        wait(lambda: len(controlled.calls) == 2)
        controlled.finish(first, str(target), {'one': {'state': 'valid', 'digest': digest}})
        verification.poll(); assert verification.busy and verification.records == {}
        controlled.finish(verification.generation, str(other), {})
        verification.poll(); assert not verification.busy and verification.records == {}
        # A slow existing validator cannot keep closeEvent on the UI thread.
        gate, entered = threading.Event(), threading.Event()
        class Slow:
            def search(self, payload, _page, _order, cancel):
                entered.set(); assert gate.wait(5)
                return {'target': payload['target'], 'records': {}}
        verification.session = SearchSession(Slow())
        verification.request(); wait(entered.is_set)
        window.close(); window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        done = threading.Event()
        waiter = threading.Thread(target=lambda: (queue.wait_closed(), done.set()))
        waiter.start()
        try:assert not done.wait(.05)
        finally:gate.set()
        waiter.join(5); assert done.is_set()
        assert not errors and 'miniaudio' not in sys.modules, errors
    print('PASS Qt verified labels, menu recheck, language, target invalidation, stale response, asynchronous close and retained wait')


def main():
    if '--child' in sys.argv:ui();return
    service()
    for scale in ('1', '1.25', '1.5'):
        result = subprocess.run([sys.executable, '-B', __file__, '--child'], cwd=ROOT,
            env=dict(os.environ, QT_SCALE_FACTOR=scale, PYTHONIOENCODING='utf-8'),
            capture_output=True, timeout=40)
        print(scale, result.stdout.decode('utf-8', errors='replace'), result.stderr.decode('utf-8', errors='replace'), flush=True)
        assert result.returncode == 0


if __name__ == '__main__':main()
