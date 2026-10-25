"""Manual-import hashes, bounded cached scans, cancellation and UI isolation."""
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
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]
from smoke_verification import song, record


def service():
    from quest_tool.downloads import DownloadError, validate_song
    from quest_tool.qt_ui.local_inventory import InventoryClient
    from quest_tool.qt_ui.song_status import StatusIndex
    from quest_tool.qt_ui.song_model import sample_songs
    from quest_tool.messages import tr
    with tempfile.TemporaryDirectory(prefix='quest-inventory-') as directory:
        root = Path(directory).resolve();target = root/'songs';target.mkdir()
        manual = target/'Any title, not an ID';digest = song(manual)
        second = target/'Another folder';song(second)
        info = second/'Info.dat';info.write_text(info.read_text().replace('Fixture','Other song'),encoding='utf-8')
        other_digest = validate_song(second)[1]
        hidden = target/'.archives';song(hidden)
        old = record(target, second, other_digest)
        client = InventoryClient();cancel = threading.Event()
        def scan(force=False, rows=None):
            return client.search(dict(target=str(target),rows=[old] if rows is None else rows,force=force),0,'',cancel)
        original = {str(p):p.read_bytes() for folder in (manual,second) for p in folder.iterdir()}
        with patch('quest_tool.qt_ui.local_verification.validate_song',wraps=validate_song) as validate:
            cold = scan();assert validate.call_count == 2, validate.call_count
        assert set(cold['inventory']['digests']) == {digest,other_digest}
        assert cold['inventory']['valid'] == 2 and cold['inventory']['checked'] == 2 and cold['inventory']['complete']
        remote = replace(sample_songs()[0],key='unknown-online-id',digest=digest)
        assert StatusIndex([],target,None,cold['inventory']).label(remote) == tr('qt.verify.same')
        assert not StatusIndex([],target,None,cold['inventory']).label(replace(remote,digest='f'*40))
        with patch('quest_tool.qt_ui.local_verification.validate_song',side_effect=AssertionError('unnecessary rehash')):
            warm = scan()
        assert warm['inventory']['reused'] == 2
        with patch('quest_tool.qt_ui.local_verification.validate_song',wraps=validate_song) as validate:
            scan(True);assert validate.call_count == 2
        client.MAX_CACHE_ITEMS=1
        assert scan(True)['inventory']['valid']==2 and not client.memo
        client.MAX_CACHE_ITEMS=20000
        scan()
        (manual/'Expert.dat').write_text('{"_notes":[1]}',encoding='utf-8')
        with patch('quest_tool.qt_ui.local_verification.validate_song',wraps=validate_song) as validate:
            changed = scan();assert validate.call_count == 1
        assert digest not in changed['inventory']['digests']
        # Rename preserves exact hash identity without assuming a folder-name ID.
        new_digest = validate_song(manual)[1]
        renamed = target/'Renamed';manual.rename(renamed);manual = renamed
        assert new_digest in scan()['inventory']['digests']
        broken = target/'Broken';broken.mkdir();(broken/'Info.dat').write_text('bad')
        partial = scan()['inventory'];assert not partial['complete'] and partial['unknown'] == 1
        assert other_digest in partial['digests']
        client.MAX_FOLDERS = 1
        limited = scan(True,[])['inventory'];assert limited['checked'] == 1 and not limited['complete']
        client.MAX_FOLDERS = 2000;client.MAX_ENTRIES = 1
        assert not scan(True,[])['inventory']['complete']
        client.MAX_ENTRIES = 10000;client.MAX_BYTES = 1
        assert scan(True,[])['inventory']['digests'] == []
        client.MAX_BYTES = 256*1024*1024
        outside = root/'outside';song(outside)
        link = target/'junction'
        result = subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(outside)],capture_output=True,timeout=10)
        assert result.returncode == 0
        try:
            assert not scan(True,[])['inventory']['complete']
        finally:link.rmdir()
        cancel.set()
        try:scan()
        except DownloadError as exc:assert exc.code == 'cancelled'
        else:raise AssertionError('cancellation ignored')
        cancel.clear()
        empty = root/'empty';empty.mkdir()
        fresh = client.search(dict(target=str(empty),rows=[]),0,'',cancel)['inventory']
        assert fresh['complete'] and not fresh['digests'] and not client.memo
        # Ordinary reads never changed any fixture bytes; only explicit fixture
        # mutation and rename above are excluded from this comparison.
        assert all(p.read_bytes() == original[str(p)] for p in second.iterdir())
        print(json.dumps(dict(cold_hashes=2,warm_hashes=0,manual_rescan_hashes=2,changed_hashes=1,
                              manual_version_identified=True,partial_safe=True)))
    print('PASS manual identity, no title inference, shared validation, memory reuse/force/change/rename, limits, junction, cancel and target purge')


def ui():
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import sample_songs
    from quest_tool.messages import tr
    from quest_tool.workers import TaskRunner
    app = QApplication([]);errors=[]
    sys.excepthook=lambda kind,value,tb:errors.append(str(value))
    def wait(predicate):
        deadline=time.monotonic()+6
        while not predicate() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.003)
        assert predicate() and not errors,errors
    class Manager:
        def __init__(self,root):
            self.history=root/'history.json';self.tasks=TaskRunner();self.last_keep_zip=False;self.rows=[]
        def snapshot(self):return copy.deepcopy(self.rows)
        def shutdown(self):pass
    with tempfile.TemporaryDirectory(prefix='quest-inventory-ui-') as directory:
        root=Path(directory).resolve();target=root/'songs';target.mkdir()
        digest=song(target/'Manual import')
        remote=replace(sample_songs()[0],key='12345',digest=digest)
        window=DownloadWindow(songs=[remote]);window.enable_downloads(root,Manager(root));window.show()
        queue=window.download_browser;controller=queue.verification
        wait(lambda:not controller.busy)
        assert window.song_model.status_labels['12345']==tr('qt.verify.same')
        assert controller.inventory['valid']==1 and controller.inventory['complete']
        assert not controller.summary.isEnabled() and not controller.cancel_action.isEnabled()
        count=controller.session.generation
        for _ in range(5):window.refresh_page(0);queue.refresh()
        QTest.qWait(300)
        assert controller.session.generation==count,'page changes restarted scan'
        for language in ('en','zh'):
            window.retranslate(language)
            assert controller.cancel_action.text()==tr('qt.inventory.cancel')
            assert tr('qt.inventory.complete') in controller.summary.text()
        controller.action.trigger()
        assert controller.busy and controller.cancel_action.isEnabled()
        controller.cancel_action.trigger()
        assert not controller.busy and controller.cancelled and not controller.inventory
        assert controller.action.isEnabled() and not window.song_model.status_labels['12345']
        QTest.qWait(300);assert controller.session.generation==count
        controller.action.trigger();wait(lambda:not controller.busy)
        assert controller.inventory['valid']==1 and not controller.cancelled
        # A running worker may ignore cancel temporarily. Its return and an
        # explicitly late message must not repopulate cancelled UI state.
        delegate=controller.session.client;entered=threading.Event();gate=threading.Event()
        class Slow:
            first=True
            def search(self,payload,page,order,cancel):
                if self.first:
                    self.first=False;entered.set();assert gate.wait(5)
                    return dict(target=payload['target'],records={},inventory={'digests':['f'*40]})
                return delegate.search(payload,page,order,cancel)
        controller.session.client=Slow()
        controller.action.trigger();wait(entered.is_set)
        stale=controller.generation
        controller.cancel_action.trigger()
        controller.session.tasks.emit('search',payload={'generation':stale,'result':dict(target=str(target),records={},inventory={'digests':['f'*40]})})
        controller.poll();assert not controller.inventory
        controller.action.trigger()
        gate.set();wait(lambda:not controller.busy)
        assert controller.inventory['digests']==[digest]
        other=root/'other';other.mkdir();assert queue.set_target(str(other))
        assert not controller.inventory and not window.song_model.status_labels['12345']
        wait(lambda:not controller.busy)
        assert controller.inventory['complete'] and controller.inventory['valid']==0
        window.close();queue.wait_closed();app.processEvents()
        assert not errors and 'miniaudio' not in sys.modules,errors
    print('PASS Qt manual-song labels, no scan on paging, summary/translation, cancel debounce/rescan, target isolation and close')


def main():
    if '--child' in sys.argv:ui();return
    service()
    for scale in ('1','1.25','1.5'):
        result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
            env=dict(os.environ,QT_SCALE_FACTOR=scale,PYTHONIOENCODING='utf-8'),capture_output=True,timeout=40)
        print(scale,result.stdout.decode('utf-8',errors='replace'),result.stderr.decode('utf-8',errors='replace'),flush=True)
        assert result.returncode==0


if __name__=='__main__':main()
