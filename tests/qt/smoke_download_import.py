"""M5B real Qt import/path/history/local refresh with existing queue and LocalFS."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]


def child():
    from PySide6.QtWidgets import QApplication, QFileDialog
    from quest_tool import downloads as dl
    from quest_tool.fsapi import LocalFS
    from quest_tool.playlist import song_hash
    from quest_tool.qt_ui import download_import
    from quest_tool.qt_ui.download_window import DownloadWindow
    from smoke_downloads import make_archive, FakeClient

    app = QApplication([]);app.setStyle('Fusion')
    errors = []
    def report(kind,value,tb):
        errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook = report
    worker_threads = []
    class LoggedFS(LocalFS):
        def scan_metadata(self,*args,**kwargs):
            worker_threads.append(threading.get_ident())
            return super().scan_metadata(*args,**kwargs)
    download_import.LocalFS = LoggedFS
    main_thread = threading.get_ident()
    with tempfile.TemporaryDirectory() as folder:
        base=Path(folder);sandbox=base/'preview';sandbox.mkdir()
        first=base/'中文歌曲';first.mkdir();second=base/'other';second.mkdir()
        archive=make_archive(base);fixture=base/'fixture';fixture.mkdir()
        with zipfile.ZipFile(archive) as zipped:zipped.extractall(fixture)
        client=FakeClient(archive,song_hash(fixture))
        manager=dl.DownloadManager(sandbox/'qt-downloads.json',client)
        window=DownloadWindow(songs=[]);window.enable_downloads(sandbox,manager);window.show()
        queue=window.download_browser;panel=queue.importer
        original_dir,original_files=QFileDialog.getExistingDirectory,QFileDialog.getOpenFileNames
        def wait(predicate,seconds=10):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                app.processEvents();time.sleep(.01)
                if predicate():return
            raise AssertionError('M5B timeout: '+str((queue.error,panel.error,manager.snapshot())))
        def settled(count):
            return len(queue.model.rows)==count and not queue.busy and all(r['state'] in dl.TERMINAL for r in queue.model.rows) and not panel.scanning
        def hashes():
            return {str(p.relative_to(first)):hashlib.sha256(p.read_bytes()).hexdigest() for p in first.rglob('*') if p.is_file()}
        try:
            wait(lambda:not panel.scanning)
            assert not queue.set_target('.') and not queue.set_target(str(Path(first.anchor)))
            assert not queue.set_target(r'\\server\share') and not queue.set_target(str(base/'missing'))
            QFileDialog.getExistingDirectory=staticmethod(lambda *args: '')
            target=queue.target;window.browse.click();assert queue.target==target
            QFileDialog.getExistingDirectory=staticmethod(lambda *args: str(first))
            window.browse.click();wait(lambda:not panel.scanning)
            assert queue.target==first.resolve() and panel.model.rowCount()==0
            window.tabs.setCurrentIndex(2)
            panel.input.setPlainText('https://evil.example/maps/abc');panel.links.click()
            wait(lambda:bool(queue.error));assert panel.input.toPlainText() and not manager.snapshot()
            panel.keep.setChecked(True)
            panel.input.setPlainText('1\nhttps://beatsaver.com/maps/1\n2');panel.links.click()
            wait(lambda:settled(2))
            assert [r['state'] for r in queue.model.rows].count('done')==1
            assert panel.model.rowCount()==1 and panel.model.rows[0]['title']=='Download Test'
            assert not panel.input.toPlainText() and list((first/'.quest-download-archives').glob('*.zip'))
            # Failed ZIP may be repaired and retried through the real history row.
            bad=base/'bad.zip';bad.write_bytes(b'broken')
            QFileDialog.getOpenFileNames=staticmethod(lambda *args: ([str(bad)],'ZIP'))
            window.tabs.setCurrentIndex(2);panel.zip.click();wait(lambda:settled(3))
            assert queue.model.rows[-1]['state']=='failed'
            shutil.copy2(archive,bad)
            queue.table.selectRow(2);queue.retry.click();wait(lambda:settled(4))
            assert queue.model.rows[-1]['state']=='exists'
            # Traversal ZIP rejected by the unchanged extraction guard.
            hostile=base/'hostile.zip'
            with zipfile.ZipFile(hostile,'w') as zipped:zipped.writestr('../escape.txt','unsafe')
            QFileDialog.getOpenFileNames=staticmethod(lambda *args: ([str(hostile)],'ZIP'))
            panel.zip.click();wait(lambda:settled(5))
            assert queue.model.rows[-1]['state']=='failed' and not (base/'escape.txt').exists()
            before=hashes()
            queue.clear_history.click();wait(lambda:queue.model.rowCount()==0 and not queue.busy)
            assert hashes()==before and panel.model.rowCount()==1
            # Do not erase text edited after submit; target is fixed per queued job.
            client.gate=threading.Event();client.entered.clear()
            panel.input.setPlainText('3');panel.links.click();panel.input.setPlainText('new draft')
            wait(lambda:client.entered.is_set() and not queue.busy)
            assert panel.input.toPlainText()=='new draft'
            assert queue.set_target(str(second));client.gate.set()
            wait(lambda:settled(1))
            assert Path(queue.model.rows[0]['target']).resolve()==first.resolve()
            assert panel.model.rowCount()==0
            # Invalid history cannot retry to a target that was not selected.
            queue.model.rows[0]['state']='failed';queue.table.selectRow(0)
            queue.act('retry');assert queue.error=='target'
            queue.poll()
            panel.input.setPlainText('4');panel.links.click();wait(lambda:settled(2))
            assert panel.model.rowCount()==1 and Path(queue.model.rows[-1]['target']).resolve()==second.resolve()
            panel.session.tasks.emit('search',payload={'generation':-1,'result':{'target':str(first),'rows':[]}})
            panel.poll();assert panel.model.rowCount()==1
            for language in ('en','zh'):
                window.retranslate(language)
                for mode in ('dark','light'):
                    window.change_theme(mode);window.tabs.setCurrentIndex(2);app.processEvents()
                    assert panel.input.isVisible() and panel.table.isVisible() and not errors
                    assert panel.refresh_button.mapTo(window,panel.refresh_button.rect().bottomRight()).y()<window.footer.y()
            shots=Path(os.environ.get('QUEST_QT_SHOTS',ROOT/'tests/.tmp/qt-import'));shots.mkdir(parents=True,exist_ok=True)
            window.grab().save(str(shots/('m5b-import-'+os.environ.get('QT_SCALE_FACTOR','1')+'.png')))
            window.close();queue.wait_closed()
            # A saved external last_target never becomes the startup write target.
            restored=DownloadWindow(songs=[])
            restored.enable_downloads(sandbox)
            try:
                assert restored.download_browser.target==(sandbox/'songs').resolve()
                assert restored.download_browser.manager.last_target==str(second.resolve())
            finally:
                restored.close();restored.download_browser.wait_closed()
            assert worker_threads and all(t!=main_thread for t in worker_threads)
            assert not errors and not any(n.startswith(('tkinter','customtkinter')) for n in sys.modules)
            print('PASS M5B: actual path/import/history controls, links dedup, ZIP guards/retry, retained ZIP, immutable job targets, local refresh, stale scans, language/theme, safe startup')
        finally:
            QFileDialog.getExistingDirectory=original_dir;QFileDialog.getOpenFileNames=original_files
            if client.gate:client.gate.set()
            window.close();queue.wait_closed()


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for factor in ('1','1.25','1.5'):
            p=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=factor),capture_output=True,timeout=90)
            print(factor,p.stdout.decode('utf-8','replace'),p.stderr.decode('utf-8','replace'))
            assert p.returncode==0
