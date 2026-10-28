"""Qt queue controls with real manager, ZIP validation and sandbox installation."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from quest_tool import downloads as dl
    from quest_tool.playlist import song_hash
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import sample_songs
    from smoke_downloads import make_archive, FakeClient

    app = QApplication([]);app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(repr(value))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    main_thread = threading.get_ident()
    class Client(FakeClient):
        def __init__(self, archive, digest):
            super().__init__(archive,digest)
            self.fail = False
            self.fetch_gate = threading.Event();self.fetch_gate.set()
            self.fetch_entered = threading.Event()
            self.threads = []
        def resolve(self, map_id, scratch, cancel):
            self.threads.append(threading.get_ident())
            if self.fail:raise dl.DownloadError('network')
            return super().resolve(map_id,scratch,cancel)
        def fetch(self,url,destination,cancel,progress):
            self.threads.append(threading.get_ident());self.fetch_entered.set()
            progress(1,2)
            while not self.fetch_gate.is_set():
                dl.check_cancel(cancel);time.sleep(.005)
            return super().fetch(url,destination,cancel,progress)
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);archive=make_archive(root)
        extracted=root/'fixture';extracted.mkdir()
        with zipfile.ZipFile(archive) as zipped:zipped.extractall(extracted)
        client=Client(archive,song_hash(extracted))
        sandbox=root/'preview';sandbox.mkdir()
        manager=dl.DownloadManager(sandbox/'qt-downloads.json',client)
        songs=tuple(replace(s,key=f'{i+1:x}') for i,s in enumerate(sample_songs()))
        window=DownloadWindow(songs=songs);window.columns.setCurrentText('3');window.rows.setCurrentText('7');window.enable_downloads(sandbox,manager);window.show()
        queue=window.download_browser
        def wait(predicate,seconds=10):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                app.processEvents();time.sleep(.01)
                if predicate():return
            raise AssertionError('Queue timed out: '+str(manager.snapshot()))
        def done():
            # Terminal state is published before the staging context exits.
            # Directory assertions must await the real worker's task_done too.
            with manager._queue.all_tasks_done:
                settled = manager._queue.unfinished_tasks == 0
            return bool(manager.snapshot()) and all(r['state'] in dl.TERMINAL for r in manager.snapshot()) and not queue.busy and settled
        def pick(row):
            wait(lambda:queue.model.rowCount()>row)
            queue.table.clearSelection();queue.table.selectRow(row);app.processEvents()
        try:
            assert Path(window.destination.text()).resolve()==(sandbox/'songs').resolve()
            assert window.browse.isEnabled()
            # Invalid input keeps all checked records intact.
            window.song_model.setData(window.song_model.index(0),Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
            queue.enqueue(['../bad']);wait(lambda:bool(queue.error))
            assert window.song_model.checked=={'1'} and not manager.snapshot()
            # Real detail action -> existing queue -> actual validated extraction.
            window.download_one.click();wait(done)
            assert manager.snapshot()[0]['state']=='done'
            installed=Path(manager.snapshot()[0]['installed'])
            assert installed.is_relative_to(queue.target) and (installed/'Info.dat').is_file()
            assert song_hash(installed)==client.digest and not window.song_model.checked
            assert window.pages.currentWidget() is window.tasks_page and queue.model.rowCount()==1
            fetched=client.fetched
            window.download_one.click();wait(lambda:len(manager.snapshot())==2 and done())
            assert manager.snapshot()[-1]['state']=='exists' and client.fetched==fetched
            # Cross-page records submitted by actual bulk button; same hash deduped.
            window.tabs.setCurrentIndex(0);window.song_model.setData(window.song_model.index(1),Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
            window.refresh_page(1);window.song_model.setData(window.song_model.index(0),Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
            window.download.click();wait(lambda:len(manager.snapshot())==4 and done())
            assert not window.song_model.checked and len(list(queue.target.iterdir()))==1
            # Fail + retry through selected table row creates a new managed job.
            client.fail=True;queue.enqueue(['ab']);wait(lambda:len(manager.snapshot())==5 and done())
            assert manager.snapshot()[-1]['state']=='failed'
            pick(4);assert queue.retry.isEnabled()
            client.fail=False;queue.retry.click();wait(lambda:len(manager.snapshot())==6 and done())
            assert manager.snapshot()[-1]['state']=='exists'
            # A new version is retained beside the original, never overwritten.
            changed=make_archive(root,name='v2.zip',title='Version Two')
            other=root/'v2';other.mkdir()
            with zipfile.ZipFile(changed) as zipped:zipped.extractall(other)
            client.archive,client.digest=changed,song_hash(other)
            queue.enqueue(['1']);wait(lambda:len(manager.snapshot())==7 and done())
            assert manager.snapshot()[-1]['state']=='done' and len(list(queue.target.iterdir()))==2
            assert (installed/'Info.dat').is_file()
            # Bad ZIP must leave neither an installation nor a staging directory.
            bad=root/'bad.zip';bad.write_bytes(b'bad');client.archive=bad;client.digest='f'*40
            queue.enqueue(['ac']);wait(lambda:len(manager.snapshot())==8 and done())
            assert manager.snapshot()[-1]['state']=='failed' and len(list(queue.target.iterdir()))==2
            # Active fetch progress, selected cancel and nonblocking window close.
            client.archive=changed;client.digest='e'*40
            client.fetch_gate.clear();client.fetch_entered.clear()
            queue.enqueue(['ad']);wait(client.fetch_entered.is_set)
            wait(lambda:any(r['state']=='downloading' for r in queue.model.rows))
            assert any(r['progress']>0 for r in queue.model.rows)
            pick(8);assert queue.cancel.isEnabled();queue.cancel.click();wait(done)
            assert manager.snapshot()[-1]['state']=='cancelled'
            assert not list(queue.target.glob('.quest-download-*'))
            for language in ('en','zh'):
                window.retranslate(language)
                for mode in ('dark','light'):
                    window.change_theme(mode);app.processEvents()
                    assert queue.table.isVisible() and not errors
            shots=Path(os.environ.get('QUEST_QT_SHOTS',root/'screenshots'));shots.mkdir(parents=True,exist_ok=True)
            window.grab().save(str(shots/('m5a-queue-'+os.environ.get('QT_SCALE_FACTOR','1')+'.png')))
            # Restored history cannot redirect retry outside the preview target.
            row=queue.model.rows[-1];original=row['target'];row['target']=str(root)
            pick(8);queue.act('retry');assert queue.error=='target';row['target']=original
            client.fetch_entered.clear();queue.enqueue(['ae']);wait(client.fetch_entered.is_set)
            start=time.monotonic();window.close();assert time.monotonic()-start<.3
            for t in manager.tasks._threads:t.join(3)
            queue.wait_closed()
            assert not manager.tasks.busy() and not queue.timer.isActive()
            assert len(list(queue.target.iterdir()))==2 and not list(queue.target.glob('.quest-download-*'))
            saved=json.loads(manager.history.read_text(encoding='utf-8'));assert len(saved['jobs'])==10
            assert all(t!=main_thread for t in client.threads) and not errors,errors
            assert not any(n.startswith(('tkinter','customtkinter')) for n in sys.modules)
            print('PASS M5A: real queue/ZIP/hash/install, cross-page bulk, existing/version retention, errors, retry, progress, cancel, sandbox guard, close and persistence')
        finally:
            client.fetch_gate.set();window.close();manager.shutdown()
            for t in manager.tasks._threads:t.join(3)


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for factor in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=factor),capture_output=True,timeout=90)
            print(factor,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
        # Exercise actual startup/finally: history writers must exit before unlock.
        code = '''
from pathlib import Path
import sys,threading,time
sys.path.insert(0,str(Path('tests').resolve()))
from smoke_downloads import make_archive,FakeClient
from quest_tool.playlist import song_hash
from quest_tool import downloads as dl
from quest_tool.qt_ui import download_browser as adapter
from quest_tool.qt_ui.download_window import DownloadWindow
from quest_tool.qt_ui.runtime import PreviewRuntime
from quest_tool.qt_ui.bootstrap import run
from PySide6.QtCore import QTimer
import zipfile
base=Path(sys.argv[1]);archive=make_archive(base);fixture=base/'fixture';fixture.mkdir()
with zipfile.ZipFile(archive) as zipped:zipped.extractall(fixture)
client=FakeClient(archive,song_hash(fixture));client.gate=threading.Event()
managers=[]
def factory(history):
 manager=dl.DownloadManager(history,client);managers.append(manager);return manager
adapter.DownloadManager=factory
DownloadWindow.enable_search=lambda self:None
DownloadWindow.enable_covers=lambda self:None
show=DownloadWindow.show
def closing(window):
 if not client.entered.is_set():QTimer.singleShot(10,lambda:closing(window));return
 window.close()
def shown(self):
 show(self);self.download_browser.enqueue(['abc']);QTimer.singleShot(10,lambda:closing(self))
DownloadWindow.show=shown
close=PreviewRuntime.close
def unlocked(self):
 assert managers and not managers[0].tasks.busy(),'Runtime unlocked while writer active'
 close(self)
PreviewRuntime.close=unlocked
assert run(['--data-dir',str(base)])==0
assert not (base/'QuestSongCompare-QtPreview'/'preview.lock').exists()
print('PASS actual Qt startup: close cancels queue, writers joined before runtime unlock')
'''
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run([sys.executable,'-B','-c',code,folder],cwd=ROOT,check=True,timeout=30)
