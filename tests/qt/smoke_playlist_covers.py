"""Playlist cover cooldown and retry do not spin or restart active batches."""
import io
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main():
    from PIL import Image
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.downloads import DownloadError
    from quest_tool.qt_ui.cover_session import CoverSession
    from quest_tool.qt_ui.download_window import DownloadWindow
    app = QApplication([]);errors = []
    sys.excepthook = lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    image = io.BytesIO();Image.new('RGB',(256,256),'blue').save(image,'PNG')
    state = dict(fail=True,calls=0,now=0)
    class Media:
        def load(self,*_args):
            state['calls'] += 1
            if state['fail']:raise DownloadError('rate_limit')
            return image.getvalue()
    class Client:
        def search(self,*_args):
            return dict(kind='list',page=0,has_next=False,total=1,rows=[dict(id=1,title='Playlist',author='Mapper',description='',
                coverURL='https://cdn.beatsaver.com/test.jpg',total=4,duration=180,rating=.9,updated='',curated=True)])
    def wait(predicate):
        end = time.monotonic()+10
        while not predicate() and time.monotonic()<end:QTest.qWait(20)
        assert predicate(),errors
    with tempfile.TemporaryDirectory() as tmp, patch('socket.socket',side_effect=AssertionError('offline only')):
        w = DownloadWindow(songs=[]);w.enable_downloads(Path(tmp))
        browser = w.playlist_browser;browser.session.client = Client()
        browser.covers.close();browser.covers = CoverSession(Media,clock=lambda:state['now'])
        try:
            w.show();w.tabs.setCurrentIndex(browser.index)
            wait(lambda:bool(browser.failed_covers))
            generation = browser.covers.generation;calls = state['calls']
            QTest.qWait(600)
            assert browser.covers.generation == generation and state['calls'] == calls
            state.update(fail=False,now=31);browser.reload()
            wait(lambda:bool(browser.cache.sources))
            assert len(browser.cache.sources) == 1 and not browser.failed_covers and not errors
        finally:w.close();w.download_browser.wait_closed();app.processEvents()
    print('PASS playlist covers: bounded concurrent service, no cooldown retry loop, refresh after cooldown, cache and close')


if __name__ == '__main__':main()
