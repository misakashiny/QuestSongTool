"""Deterministic offline paging, cancellation, cover priority and cache limits."""
import io
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from quest_tool.qt_ui.download_window import DownloadWindow
from quest_tool.qt_ui.page_cache import PageCache
from quest_tool.qt_ui.song_model import sample_songs
from quest_tool.song_search import SearchSession
from quest_tool.workers import TaskRunner


class Session:
    def __init__(self):
        self.tasks = TaskRunner()
        self.calls = []
        self.generation = 0
        self.closed = False

    def request(self, query, page, order, **kwargs):
        self.generation += 1
        self.calls.append((query, page, order, kwargs))
        return self.generation

    def cancel(self):
        pass

    def close(self):
        self.closed = True

    def finish(self, page, generation=None, error=None):
        generation = self.generation if generation is None else generation
        if error:
            self.tasks.emit('search_error', payload={'generation': generation, 'code': error})
        else:
            self.tasks.emit('search', payload={'generation': generation, 'result': {
                'page': page, 'has_next': page < 5, 'rows': [
                    {'id': str(page*21+i), 'title': 'Song', 'artist': 'Artist', 'mapper': 'Mapper',
                     'duration': 120, 'bpm': 100, 'rating': .9, 'hash': 'a'*40,
                     'published': '', 'requirements': [], 'previewURL': '', 'diffs': [], 'description': '',
                     'coverURL': 'https://cdn.beatsaver.com/'+str(page*21+i)+'.png'} for i in range(21)]}})


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    foreground, background, media = Session(), Session(), Session()
    window = DownloadWindow()
    window.columns.setCurrentText('3');window.rows.setCurrentText('7')
    window.enable_search(foreground, background)
    browser, model = window.browser, window.song_model
    foreground.finish(0); browser.poll()
    assert model.page == 0 and model.rowCount() == 21
    window.enable_covers(media)
    covers = window.cover_browser
    model.setData(model.index(0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    selected = set(model.checked)
    browser.prefetch_delay.stop(); browser.start_prefetch()
    assert len(background.calls) == 1
    background.finish(1); browser.poll()
    assert model.page == 0 and set(model.checked) == selected
    assert covers.warm_queue and len(media.calls) == 1
    generation = media.generation
    png = io.BytesIO()
    Image.new('RGB', (32, 32), 'blue').save(png, format='PNG')
    for url in tuple(covers.pending):
        media.tasks.emit('cover', payload={'generation': covers.epoch, 'url': url, 'data': png.getvalue()})
    media.tasks.emit('search', payload={'generation': generation, 'result': {}})
    covers.poll()
    assert covers.warming and len(media.calls) == 2 and len(covers.pending) == 21
    for url in tuple(covers.pending):
        media.tasks.emit('cover', payload={'generation': covers.epoch, 'url': url, 'data': png.getvalue()})
    media.tasks.emit('search', payload={'generation': media.generation, 'result': {}})
    covers.poll()
    assert not covers.warming and len(covers.cache.sources) == 42
    expires = browser.cache.entries[(browser.context, 1)][0]
    start = time.perf_counter(); browser.request(1)
    hit_ms = (time.perf_counter()-start)*1000
    assert model.page == 1 and len(foreground.calls) == 1 and browser.cache_hits == 1
    assert browser.cache.entries[(browser.context, 1)][0] == expires
    assert len(media.calls) == 2 and set(model.checked) == selected
    browser.prefetch_delay.stop(); browser.start_prefetch()
    pending = background.generation
    browser.request(2)
    assert model.page == 1 and browser.waiting_prefetch and len(foreground.calls) == 1
    background.finish(2); browser.poll()
    assert model.page == 2 and browser.state == 'ready'
    browser.prefetch_delay.stop(); browser.start_prefetch()
    background.finish(3, error='rate_limit'); browser.poll()
    assert model.page == 2 and browser.state == 'ready' and not browser.timer.isActive()
    QTest.qWait(350)
    assert len(background.calls) == 3
    browser.request(3)
    assert model.page == 2 and browser.state == 'loading'
    foreground.finish(3); browser.poll()
    browser.prefetch_delay.stop(); browser.start_prefetch()
    browser.request(4)
    background.finish(4, error='network'); browser.poll()
    assert browser.generation is not None and model.page == 3
    foreground.finish(4); browser.poll()
    assert model.page == 4
    browser.request(0, force=True)
    assert model.page == 4 and len(browser.cache.entries) == 0
    foreground.finish(0); browser.poll()
    browser.prefetch_delay.stop(); browser.start_prefetch()
    stale = background.generation
    browser.params = ('new', 'Search', 'Latest')
    browser.request(0)
    assert model.rowCount() == 0
    background.finish(1, generation=stale); browser.poll()
    assert model.rowCount() == 0 and browser.state == 'loading'
    foreground.finish(0); browser.poll()
    browser.cancel()
    assert not browser.timer.isActive() and not browser.prefetch_delay.isActive()
    browser.params = ('', 'Random', 'Random'); browser.request(0)
    foreground.finish(0); browser.poll()
    assert not browser.cache.entries and not browser.prefetch_delay.isActive()
    browser.request(1); assert model.page == 0
    foreground.finish(1); browser.poll()
    assert not browser.cache.entries
    browser.params = ('', 'Latest', 'Latest'); browser.request(5)
    foreground.finish(5); browser.poll()
    assert not browser.prefetch_delay.isActive()
    # Cache keys include page size and canonical filter values.
    browser.request(0)
    foreground.finish(0); browser.poll()
    browser.prefetch_delay.stop(); browser.start_prefetch()
    old_context = browser.context
    browser.filters = {'verified': True}
    browser.request(0)
    assert browser.context != old_context and not browser.cache.entries and not browser.prefetch_generation
    foreground.finish(0); browser.poll()
    old_context = browser.context
    window.columns.blockSignals(True)
    window.columns.setCurrentText('4')
    window.columns.blockSignals(False)
    browser.request(0)
    assert browser.pending_size == 28 and browser.context != old_context and not browser.cache.entries
    foreground.finish(0); browser.poll()
    # Reject mismatched pages instead of showing one page under another number.
    browser.request(2)
    foreground.finish(3); browser.poll()
    assert browser.state == 'error' and browser.error == 'metadata' and model.page == 0
    # Warming a large page is capped, and stale covers after cancellation are ignored.
    from dataclasses import replace
    upcoming = tuple(replace(song, cover_url=f'https://cdn.beatsaver.com/warm-{i}.png')
                     for i, song in enumerate(sample_songs()))
    covers.cancel_warm()
    covers.page(())
    covers.warm(upcoming)
    assert len(covers.pending) == 21
    old_epoch = covers.epoch
    covers.cancel_warm()
    media.tasks.emit('cover', payload={'generation': old_epoch, 'url': upcoming[0].cover_url,
                                     'data': png.getvalue()})
    covers.poll()
    assert upcoming[0].cover_url not in covers.cache.sources
    clock = [0]
    cache = PageCache(lambda: clock[0])
    songs = tuple(sample_songs()[:2])
    for key in range(4): cache.put(key, songs, True)
    assert len(cache.entries) == 3 and cache.get(0) is None
    cache.get(1); cache.put(4, songs, False)
    assert cache.get(2) is None
    clock[0] = 119
    assert cache.get(1) is not None
    clock[0] = 121
    assert cache.get(1) is None and cache.bytes == 0
    cache.byte_limit = 1; cache.put(0, songs, True)
    assert not cache.entries
    window.close(); app.processEvents()
    assert foreground.closed and background.closed and media.closed and not errors, errors
    assert not browser.cache.entries
    assert pending < stale
    print('PASS offline prefetch/cache/promotion/fallback/cancellation/cover priority/close')
    print('Controlled cache-hit UI delivery ms:', round(hit_ms, 3))
    # Exercise real SearchSession threads with controlled latency, never HTTP/audio.
    class Client:
        def __init__(self):
            self.calls = []
        def search(self, query, page, order, cancel, **kwargs):
            self.calls.append((page, threading.get_ident()))
            if cancel.wait(.2):
                raise RuntimeError('cancelled')
            return {'page': page, 'has_next': page < 2, 'rows': [{
                'id': str(page), 'title': 'Threaded', 'artist': '', 'mapper': '',
                'duration': None, 'bpm': None, 'rating': None, 'hash': '', 'published': '',
                'requirements': [], 'previewURL': '', 'coverURL': '', 'diffs': [], 'description': ''}]}
    front_client, back_client = Client(), Client()
    front, back = SearchSession(front_client), SearchSession(back_client)
    threaded = DownloadWindow()
    mutated = []
    threaded.song_model.modelReset.connect(lambda: mutated.append(threading.get_ident()))
    start = time.perf_counter()
    threaded.enable_search(front, back)
    def wait_for(predicate):
        deadline = time.monotonic()+5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(.002)
        assert predicate() and not errors, errors
    wait_for(lambda: threaded.browser.state == 'ready')
    cold_ms = (time.perf_counter()-start)*1000
    wait_for(lambda: threaded.browser.cache.get((threaded.browser.context, 1)) is not None)
    start = time.perf_counter(); threaded.browser.request(1)
    warm_ms = (time.perf_counter()-start)*1000
    assert threaded.song_model.page == 1 and len(front_client.calls) == len(back_client.calls) == 1
    assert set(mutated) == {threading.get_ident()}
    assert all(ident != threading.get_ident() for _, ident in front_client.calls+back_client.calls)
    threaded.browser.prefetch_delay.stop(); threaded.browser.start_prefetch()
    threaded.close(); app.processEvents()
    for session in (front, back):
        for worker in tuple(session.tasks._threads):
            worker.join(3)
            assert not worker.is_alive()
    assert 'miniaudio' not in sys.modules and not errors
    print('PASS real SearchSession threads, UI-only mutation, cancellation and joined shutdown')
    print('Controlled 200ms transport cold/warm UI ms:', round(cold_ms, 3), round(warm_ms, 3))


if __name__ == '__main__':
    main()
