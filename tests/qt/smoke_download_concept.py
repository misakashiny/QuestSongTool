"""Approved layout navigation and preview controls, sealed external I/O."""
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests/qt')]


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.download_navigation import DownloadNavigation
    from quest_tool.qt_ui.song_model import sample_songs
    from smoke_prefetch import Session

    app=QApplication([]);app.setStyle('Fusion');errors=[];external=[]
    def report(kind,value,tb):errors.append(str(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    def forbidden(*args,**kwargs):external.append(args);raise AssertionError('External I/O forbidden')
    def wait(done):
        until=time.monotonic()+10
        while time.monotonic()<until:
            QTest.qWait(20)
            if done():return
        raise AssertionError('Navigation wait')
    class Player:
        request_id=0
        def stop(self):self.request_id+=1
        def set_volume(self,value):self.volume=value
        def is_playing(self):return True
        def position(self):return 12
    with tempfile.TemporaryDirectory(prefix='quest-design-') as folder, patch('subprocess.Popen',forbidden), patch('socket.socket',forbidden):
        base=Path(folder)
        for language in ('zh','en'):
            songs=tuple(replace(song,key=f'{i+1:x}',preview_url=f'https://cdn.beatsaver.com/{i}.mp3',diffs=('Standard · Expert','Standard · ExpertPlus')) for i,song in enumerate(sample_songs()))
            window=DownloadWindow(language,songs=songs);window.columns.setCurrentText('3');window.rows.setCurrentText('7');window.enable_downloads(base/language)
            session=Session();player=Player();window.enable_preview(session,player)
            window.show();assert QTest.qWaitForWindowExposed(window,5000)
            wait(lambda:window.playlist_browser is not None)
            assert window.tabs.count()==4 and window.pages.widget(1) is window.catalog_page
            window.show_tasks();assert window.pages.currentWidget() is window.download_browser.page
            window.tabs.setCurrentIndex(3);assert window.pages.currentWidget() is window.download_browser.importer.page
            window.tabs.setCurrentIndex(0)
            assert window.preview_next.isEnabled() and not window.preview_previous.isEnabled()
            QTest.mouseClick(window.preview_next,Qt.MouseButton.LeftButton)
            assert session.calls[-1][0]['id']==songs[1].key and window.preview_browser.song==songs[1]
            assert window.preview_previous.isEnabled()
            QTest.mouseClick(window.preview_previous,Qt.MouseButton.LeftButton)
            assert session.calls[-1][0]['id']==songs[0].key
            QTest.mouseClick(window.play,Qt.MouseButton.LeftButton)
            assert window.preview_browser.request_id is None
            window.show_song(songs[-1]);window.preview_browser.song=None;window.preview_browser.render()
            # The last item on a page does not silently request the next page.
            window.show_song(songs[20]);window.preview_browser.render()
            assert not window.preview_next.isEnabled()
            navigation=DownloadNavigation(window,base/language)
            assert navigation.library is None and not external
            QTest.mouseClick(window.library_button,Qt.MouseButton.LeftButton)
            library=navigation.library
            assert library is not None and library.download_window is window
            assert library.download_queues==[window.download_browser] and not library.device_browser.busy
            assert not hasattr(library.wireless,'transfer_entry') and not library.transfers.jobs
            assert library.transfers.backend is not None and library.transfers.push_backend is not None and library.transfers.push.isVisible()
            wait(lambda:library.local_ready)
            manager=window.download_browser.manager
            QTest.mouseClick(window.library_button,Qt.MouseButton.LeftButton)
            assert library.download_window.download_browser.manager is manager and len(library.download_queues)==1
            window.close();wait(lambda:library.download_window is None)
            with patch.object(DownloadWindow,'enable_search',lambda *_:None),patch.object(DownloadWindow,'enable_covers',lambda *_:None),patch.object(DownloadWindow,'enable_preview',lambda *_:None):
                library.open_downloads();wait(lambda:library.download_window is not None)
            assert library.download_window.download_browser.manager is not manager
            assert len(library.download_queues)==2 and not external
            library.download_window.library_button.click()
            assert library.isVisible()
            navigation.close();QTest.qWait(30)
    assert not external and not errors,(external,errors)
    assert 'miniaudio' not in sys.modules
    print('PASS concept: four semantic tabs, real preview skip/stop controls, lazy library, one queue owner, close/reopen lifecycle, no external I/O; scale',os.environ.get('QT_SCALE_FACTOR'))


if __name__=='__main__':child()
