"""M6A1 real Qt controls, read-only scans, identity checks and download bridge."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog, QWidget
    from quest_tool.qt_ui.download_import import LocalClient
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.song_search import SearchSession
    from smoke_downloads import make_archive

    app = QApplication([]);app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value));sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    main_thread = threading.get_ident()
    class Client(LocalClient):
        def __init__(self):
            self.threads = [];self.fail = False;self.gate = False;self.started = threading.Event();self.cancelled = False

        def search(self, target, page, order, cancel):
            self.threads.append(threading.get_ident());self.started.set()
            while self.gate:
                if cancel.wait(.01):
                    self.cancelled = True
                    raise RuntimeError('cancelled')
            if self.fail:raise OSError('controlled unreadable folder')
            return super().search(target, page, order, cancel)

    def wait(predicate, seconds=10):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            app.processEvents();time.sleep(.01)
            if predicate():return
        raise AssertionError('M6A1 timeout')

    def make_song(root, folder, title, author, duration):
        path = root/folder;path.mkdir()
        (path/'Info.dat').write_text(json.dumps({'_version':'2.0.0','_songName':title,
            '_songAuthorName':author,'_beatsPerMinute':120,'_songDuration':duration}),encoding='utf-8')

    def focus(widget):
        # Verify the active-window precondition used by LibraryActions before
        # sending a key; do not replace or bypass its focus-scoped routing.
        owner=QWidget.window(widget);owner.raise_();owner.activateWindow()
        wait(lambda:QApplication.activeWindow() is owner)
        widget.setFocus();wait(lambda:QApplication.focusWidget() is widget)

    old_dir, old_files = QFileDialog.getExistingDirectory, QFileDialog.getOpenFileNames
    old_search = DownloadWindow.enable_search
    # This test exercises local ZIP import, so opening discovery must stay offline.
    DownloadWindow.enable_search = lambda self: None
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder).resolve();first = base/'中文曲库';first.mkdir();second = base/'second';second.mkdir()
        make_song(first,'a','Zulu [Live]','Artist A',15)
        make_song(first,'b','Alpha','Artist B',120)
        make_song(second,'a','Other library','Artist C',90)
        archive = make_archive(base)
        for language in ('zh','en'):
            client = Client();window = LibraryWindow(base/'sandbox'/language,language,session=SearchSession(client))
            window.show();wait(lambda:window.isVisible())
            try:
                assert window.target is None and not window.model.rows and not window.reload.isEnabled()
                assert not window.set_target('.') and not window.set_target(first.anchor)
                QFileDialog.getExistingDirectory = staticmethod(lambda *args:'')
                window.browse.click();assert window.target is None
                QFileDialog.getExistingDirectory = staticmethod(lambda *args:str(first))
                window.browse.click();wait(lambda:not window.scanning)
                assert len(window.model.rows)==2 and not window.failed
                window.search.setText('[Live]');assert window.proxy.rowCount()==1
                window.select_visible.click();assert window.model.checked=={'a'}
                window.search.setText('Artist B');assert window.proxy.rowCount()==1
                window.select_visible.click();assert window.model.checked=={'a','b'}
                window.search.clear();window.table.sortByColumn(2,Qt.SortOrder.AscendingOrder)
                assert window.proxy.index(0,0).data(Qt.ItemDataRole.UserRole)['folder']=='a'
                window.table.sortByColumn(2,Qt.SortOrder.DescendingOrder)
                assert window.proxy.index(0,0).data(Qt.ItemDataRole.UserRole)['folder']=='b'
                window.reload.click();wait(lambda:not window.scanning)
                assert window.model.checked=={'a','b'}
                # Old path/generation messages cannot replace the newly selected library.
                old_generation = window.generation
                assert window.set_target(str(second));wait(lambda:not window.scanning)
                assert not window.model.checked and window.model.rows[0]['title']=='Other library'
                window.session.tasks.emit('search',payload={'generation':old_generation,'result':{'target':str(first),'rows':[]}})
                window.poll();assert window.model.rows[0]['title']=='Other library'
                client.fail=True;window.reload.click();wait(lambda:not window.scanning)
                assert window.failed
                client.fail=False;window.reload.click();wait(lambda:not window.scanning)
                assert not window.failed
                focus(window.search);window.search.setText('Other');QTest.keyClick(window.search,Qt.Key.Key_Escape)
                assert not window.search.text()
                window.mode.setCurrentIndex(1);window.language.setCurrentIndex(1-window.language.currentIndex())
                app.processEvents();assert window.model.rows[0]['title']=='Other library'
                window.mode.setCurrentIndex(0)
                # The real ZIP queue signals a completed scan; only matching paths refresh.
                window.set_target(str(first));wait(lambda:not window.scanning)
                window.download.click();wait(lambda:window.download_window is not None)
                downloads=window.download_window;queue=downloads.download_browser
                wait(lambda:not queue.importer.scanning)
                QFileDialog.getExistingDirectory=staticmethod(lambda *args:str(first))
                downloads.browse.click();wait(lambda:not queue.importer.scanning and not window.scanning)
                QFileDialog.getOpenFileNames=staticmethod(lambda *args:([str(archive)],'ZIP (*.zip)'))
                queue.importer.zip.click()
                wait(lambda:bool(queue.model.rows) and queue.model.rows[-1]['state']=='done' and len(window.model.rows)==3 and not window.scanning)
                generation=window.generation
                window.download_refreshed(str(second));assert window.generation==generation and window.target==first
                # A normal close/reopen makes a new, operational downloads window.
                gate=threading.Event()
                queue.actions.submit(lambda _token: gate.wait(10),name='controlled-close')
                downloads.close();wait(lambda:window.download_window is None)
                window.download.click();app.processEvents()
                assert window.download_window is None and not window.download.isEnabled()
                gate.set();wait(lambda:window.download_window is not None)
                assert not window.download_window.download_browser.closed
                window.download_window.close();wait(lambda:window.download_window is None)
                window.set_target(str(second));wait(lambda:not window.scanning)
                # Every content panel stays within the screen-constrained client area.
                app.processEvents()
                assert window.table.width()>100 and window.table.height()>100
                assert window.table.mapTo(window,window.table.rect().bottomRight()).x()<window.width()
                assert window.table.mapTo(window,window.table.rect().bottomRight()).y()<window.height()
                if os.environ.get('P006_EVIDENCE_DIR'):
                    window.grab().save(str(Path(os.environ['P006_EVIDENCE_DIR'])/f'library-{language}-{os.environ.get("QT_SCALE_FACTOR","1")}.png'))
                client.started.clear();client.gate=True;window.reload.click();wait(client.started.is_set)
                window.close();window.wait_closed()
                assert client.cancelled and not any(t.is_alive() for t in window.session.tasks._threads)
                assert all(t!=main_thread for t in client.threads)
                print('PASS',language,'local scan/search/literal filter/numeric sort/check identity/stale result/error retry/ZIP bridge/reopen/close')
            finally:
                client.gate=False;window.close();window.wait_closed()
                # Remove the imported fixture so the second language starts identically.
                if language=='zh':
                    import shutil
                    for item in first.iterdir():
                        if item.is_dir() and item.name not in {'a','b'}:shutil.rmtree(item)
        assert not errors,errors
        assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)
    QFileDialog.getExistingDirectory, QFileDialog.getOpenFileNames = old_dir, old_files
    DownloadWindow.enable_search = old_search


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for factor in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=factor),capture_output=True,timeout=120)
            print(factor,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
        code = '''
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from quest_tool.qt_ui.library_window import LibraryWindow
from quest_tool.qt_ui.bootstrap import run
show = LibraryWindow.show
def display(self):
    show(self)
    QTimer.singleShot(100, self.close)
LibraryWindow.show = display
assert run(['--library','--data-dir',sys.argv[1]]) == 0
leaf = Path(sys.argv[1])/'QuestSongCompare-QtPreview'
assert not (leaf/'preview.lock').exists()
assert 'Stopped M7A2C' in (leaf/'preview.log').read_text(encoding='utf-8')
assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)
assert not QApplication.instance().topLevelWidgets() or not any(w.isVisible() for w in QApplication.instance().topLevelWidgets())
print('PASS real --library bootstrap and shutdown')
'''
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run([sys.executable,'-B','-c',code,folder],cwd=ROOT,check=True,timeout=30)
