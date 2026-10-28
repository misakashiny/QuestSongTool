"""Real media normalization with controlled transport; Qt cache/lifecycle/perf."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]


def child():
    from PIL import Image, ImageDraw
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool import audio
    from quest_tool.messages import tr
    from quest_tool.qt_ui.cover_browser import CoverCache
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import sample_songs
    from quest_tool.search_media import CoverClient, MediaClient
    from quest_tool.song_search import SearchSession
    from smoke_search_browser import ScriptedClient

    def picture(size):
        buffer = io.BytesIO()
        with Image.new('RGB', size, '#1967d2') as image:
            draw = ImageDraw.Draw(image)
            draw.rectangle((size[0]//4, size[1]//4, size[0]*3//4, size[1]*3//4), fill='#f5b840')
            image.save(buffer, format='JPEG')
        return buffer.getvalue()
    normal, large = picture((1024, 512)), picture((4096, 4096))
    too_wide = picture((4097, 1))
    class ControlledMedia(MediaClient):
        def __init__(self):
            super().__init__()
            self.calls = []
            self.entered, self.release = threading.Event(), threading.Event()
        def fetch(self, url, destination, cancel, progress, **kwargs):
            assert kwargs['limit'] == 5*1024*1024
            assert url.startswith('https://cdn.beatsaver.com/') and not url.endswith(('.zip', '.ogg', '.mp3'))
            self.calls.append((url, threading.get_ident()))
            if url.endswith('/slow'):
                self.entered.set()
                assert self.release.wait(5)
            destination.write_bytes(b'broken image' if url.endswith('/bad') else
                                    too_wide if url.endswith('/too-wide') else
                                    large if url.endswith('/large') else normal)

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, traceback):
        errors.append(repr(value));sys.__excepthook__(kind, value, traceback)
    sys.excepthook = report
    main_thread = threading.get_ident()
    media = ControlledMedia()
    session = SearchSession()
    session.client = CoverClient(session.tasks, media)
    window = DownloadWindow()
    window.enable_search(SearchSession(ScriptedClient()))
    window.show()
    assert QTest.qWaitForWindowExposed(window, 5000)
    assert window.minimumHeight() <= window.screen().availableGeometry().height()
    def wait_for(predicate, seconds=10):
        end = time.monotonic()+seconds
        while not predicate() and time.monotonic() < end:
            QTest.qWait(10)
            time.sleep(.002)  # Yield Python execution between native event pumps.
        assert predicate() and not errors, (len(media.calls),
                                           len(window.cover_browser.pending) if window.cover_browser else None, errors)
    wait_for(lambda: window.browser.state == 'ready')
    window.enable_covers(session)
    covers, model = window.cover_browser, window.song_model
    mutations = []
    original_put = covers.cache.put
    def put(url, data):
        mutations.append(threading.get_ident())
        return original_put(url, data)
    covers.cache.put = put
    wait_for(lambda: covers.generation is None)
    assert len(media.calls) == 1, media.calls  # Shared URL deduplicated.
    assert not window.cover.pixmap().isNull()
    assert audio._player is None and 'miniaudio' not in sys.modules

    base = sample_songs()[0]
    def song(key):
        return replace(base, key=key, cover_url='https://cdn.beatsaver.com/'+key)
    def page(songs):
        window.browser.pending_size = len(songs) or 21
        window.browser.replace_rows(tuple(songs), 0, False)
    # Current page supports 90 distinct covers, not only the backend's 64 cache.
    records = [song(str(i)) for i in range(90)]
    page(records)
    wait_for(lambda: covers.generation is None, 15)
    assert all(item.cover_url in covers.cache.sources for item in records)
    assert len(media.covers) == 64
    fetched = len(media.calls)
    hits = covers.cache.hits
    page(records)
    QTest.qWait(30)
    assert len(media.calls) == fetched and covers.generation is None
    window.results.scrollTo(model.index(89))
    QTest.qWait(15)
    assert covers.cache.hits > hits
    assert all(value == main_thread for value in mutations)
    assert all(thread != main_thread for _url, thread in media.calls)
    window.results.scrollTo(model.index(0))
    QTest.qWait(10)
    rect = window.results.visualRect(model.index(0)).adjusted(4, 4, -4, -4)
    check_rect, _play_rect = window.results.itemDelegate().controls(rect)
    QTest.mouseClick(window.results.viewport(), Qt.MouseButton.LeftButton, pos=check_rect.center())
    assert model.checked == {'0'}
    QTest.mouseClick(window.results.viewport(), Qt.MouseButton.LeftButton, pos=check_rect.center())
    assert not model.checked and len(media.calls) == fetched
    # Exact divisibility must still show all requested columns in one row.
    for columns in range(1, 7):
        window.results.configure_view(True, columns)
        for width in (1000, 1340, 1600):
            window.resize(width, 900)
            app.processEvents()
            window.results.scrollTo(model.index(0))
            app.processEvents()
            first = window.results.visualRect(model.index(0))
            last = window.results.visualRect(model.index(columns-1))
            after = window.results.visualRect(model.index(columns))
            assert first.top() == last.top() and after.top() > first.top(), (columns, width, first, last, after)
    window.results.configure_view(True, 3)

    # Error/missing art falls back without altering metadata or song selection.
    bad = (song('bad'), song('too-wide'), replace(base, key='missing', cover_url=''),
           replace(base, key='unsafe', cover_url='http://127.0.0.1/not-requested'), song('large'))
    page(bad)
    wait_for(lambda: covers.generation is None)
    assert bad[0].cover_url in covers.failed and bad[1].cover_url in covers.failed
    assert bad[3].cover_url in covers.failed and bad[4].cover_url in covers.cache.sources
    assert len(media.calls) == fetched+3  # Unsafe and empty URLs never fetched.
    assert window.cover.pixmap().isNull() and window.cover.text() == tr('media.no_cover')
    assert model.rowCount() == 5 and model.checked == set()
    window.results.setCurrentIndex(model.index(4))
    QTest.qWait(5)
    assert not window.cover.pixmap().isNull()
    assert covers.cache.sources[bad[4].cover_url].size() == QSize(256, 256)

    # Supersede blocked I/O; both emitted and manually injected stale data rejected.
    page([song('slow')])
    wait_for(media.entered.is_set)
    assert window.cover.text() == tr('qt.cover.loading')
    assert song('slow').cover_url in window.results.cover_pending
    old_epoch = covers.epoch
    page([song('new')])
    media.release.set()
    wait_for(lambda: covers.generation is None)
    session.tasks.emit('cover', payload={'generation': old_epoch, 'url': song('slow').cover_url,
                                         'data': media.covers['https://cdn.beatsaver.com/new']})
    covers.poll()
    assert song('slow').cover_url not in covers.cache.sources
    assert song('new').cover_url in covers.cache.sources and window.current_song.key == 'new'

    # Native repaint/resize with real normalized images, cold versus warm cache.
    page(records)
    wait_for(lambda: covers.generation is None, 15)
    measurements = []
    fetch_count = len(media.calls)
    available = window.screen().availableGeometry()
    frame_width = max(0, window.frameGeometry().width()-window.width())
    frame_height = max(0, window.frameGeometry().height()-window.height())
    max_width, max_height = available.width()-frame_width, available.height()-frame_height
    base_height = min(760, max(window.minimumHeight(), max_height-40))
    window.move(available.topLeft())
    for language in ('zh', 'en'):
        window.retranslate(language)
        for theme in ('light', 'dark'):
            window.change_theme(theme)
            for view in ('covers', 'list'):
                window.views[view].click()
                for temperature in ('cold', 'warm'):
                    timings = []
                    sizes = []
                    for number in range(40):
                        if temperature == 'cold':
                            covers.cache.scaled.clear();covers.cache.scaled_bytes = 0
                        start = time.perf_counter()
                        target = (min(max_width, 1000+(number % 5)*40),
                                  min(max_height, base_height+(number % 3)*20))
                        window.resize(*target)
                        app.processEvents()
                        window.results.viewport().repaint()
                        timings.append((time.perf_counter()-start)*1000)
                        assert (window.width(), window.height()) == target
                        sizes.append(target)
                    measurements.append({'language': language, 'theme': theme, 'view': view,
                                         'cache': temperature, 'median_ms': statistics.median(timings),
                                         'p95_ms': sorted(timings)[37], 'max_ms': max(timings),
                                         'timings_ms': timings, 'sizes': sizes,
                                         'work_area': [available.width(), available.height()]})
    assert len(media.calls) == fetch_count
    cache = CoverCache()
    data = next(iter(media.covers.values()))
    for index in range(160):
        assert cache.put(str(index), data)
        assert cache.image(str(index), QSize(300+index % 10*20, 180), window.devicePixelRatioF()) is not None
        assert len(cache.sources) <= 128 and cache.source_bytes <= cache.SOURCE_BYTES
        assert len(cache.scaled) <= 128 and cache.scaled_bytes <= cache.SCALED_BYTES
    assert not cache.put('invalid', b'not png')
    huge = cache.image('159', QSize(100000, 100000), 4)
    assert huge.width() <= 2048 and huge.height() <= 2048
    assert cache.scaled_bytes <= cache.SCALED_BYTES
    if os.environ.get('QUEST_QT_SHOTS'):
        folder = Path(os.environ['QUEST_QT_SHOTS']);folder.mkdir(parents=True, exist_ok=True)
        window.retranslate('zh');window.change_theme('light');window.views['covers'].click()
        window.resize(min(1340, max_width), min(900, max_height))
        app.processEvents()
        assert window.grab().save(str(folder/f'covers-{os.environ.get("QT_SCALE_FACTOR")}.png'))
        (folder/f'covers-perf-{os.environ.get("QT_SCALE_FACTOR")}.json').write_text(json.dumps(measurements,indent=2),encoding='utf-8')

    # A blocked media request cannot block window close or update a closed UI.
    media.entered.clear();media.release.clear()
    page([song('slow')])
    wait_for(media.entered.is_set)
    start = time.monotonic();window.close()
    assert time.monotonic()-start < .3
    assert covers.closed and not covers.timer.isActive()
    media.release.set()
    QTest.qWait(30)
    assert not errors and audio._player is None
    assert not any(name in sys.modules for name in ('tkinter', 'customtkinter', 'quest_tool.app', 'miniaudio'))
    print('PASS M4A covers: original worker/limits, 90 images, warm reuse, invalid/missing, stale/close, main-thread pixmaps, bounded cache and cold/warm resize metrics')


if __name__ == '__main__':
    if '--child' in sys.argv:
        child()
    else:
        for factor in ('1', '1.25', '1.5'):
            subprocess.run([sys.executable, '-B', __file__, '--child'], cwd=ROOT,
                           env=dict(os.environ, QT_SCALE_FACTOR=factor), check=True, timeout=180)
