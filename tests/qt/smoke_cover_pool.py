"""Controlled real-media transport: lane cap, priority, cancellation and timing."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PIL import Image
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.downloads import DownloadError, check_cancel
    from quest_tool.qt_ui.cover_session import CoverSession, create_media
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import sample_songs
    from quest_tool.search_media import CoverClient
    from quest_tool.song_search import SearchSession

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value)); sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    empty = DownloadWindow(songs=[])
    empty.enable_covers()
    assert isinstance(empty.cover_browser.session, CoverSession)
    assert len(empty.cover_browser.session.media) == 4 and not empty.cover_browser.session.workers.busy()
    empty.close(); app.processEvents()
    image = io.BytesIO()
    Image.new('RGB', (512, 256), 'blue').save(image, format='JPEG')
    raw = image.getvalue()
    ui_thread = threading.get_ident()
    urls = ['https://cdn.beatsaver.com/pool-'+str(i) for i in range(90)]

    class Transport:
        def __init__(self, delay=.035):
            self.delay = delay
            self.lock = threading.Lock()
            self.active = self.peak = 0
            self.calls = []
            self.gate = threading.Event()
            self.blocked = set()
            self.ignore_cancel = False
            self.fail = {}
            self.clients = []

        def factory(self):
            media = create_media()
            self.clients.append(media)
            def fetch(url, destination, cancel, _progress, **kwargs):
                assert threading.get_ident() != ui_thread and kwargs['limit'] == 5*1024*1024
                with self.lock:
                    self.calls.append(url); self.active += 1; self.peak = max(self.peak, self.active)
                try:
                    if url in self.blocked:
                        deadline = time.monotonic()+5
                        while not self.gate.wait(.002):
                            assert time.monotonic() < deadline
                            if not self.ignore_cancel:check_cancel(cancel)
                    cancel.wait(self.delay)
                    check_cancel(cancel)
                    if url in self.fail:raise DownloadError(self.fail[url])
                    destination.write_bytes(b'bad' if url.endswith('bad') else raw)
                finally:
                    with self.lock:self.active -= 1
            media.fetch = fetch
            return media

    def items(values):return [{'coverURL': url} for url in values]
    def wait(predicate, tick=lambda: None, timeout=8):
        deadline = time.monotonic()+timeout
        while not predicate() and time.monotonic() < deadline:
            tick(); app.processEvents(); time.sleep(.002)
        assert predicate(), 'controlled condition timed out'
    def stop(session):
        session.close()
        reaper = session.reaper if isinstance(session, CoverSession) else session._reaper
        if reaper:reaper.join(5); assert not reaper.is_alive()

    metrics = {}
    # Same original HTTP/media validation path, with controlled per-cover latency.
    for parallel in (False, True):
        transport = Transport()
        session = CoverSession(transport.factory) if parallel else SearchSession()
        if not parallel:session.client = CoverClient(session.tasks, transport.factory())
        start = time.monotonic()
        session.request(items(urls[:21]), 1, '')
        messages = []
        def tick():
            if parallel:session.pump()
            messages.extend(session.tasks.drain())
        wait(lambda: any(message.kind == 'search' for message in messages), tick)
        elapsed = time.monotonic()-start
        covers = [message for message in messages if message.kind == 'cover']
        assert len(covers) == 21 and all(message.payload['data'] for message in covers)
        assert transport.peak == (4 if parallel else 1)
        metrics['parallel' if parallel else 'serial'] = dict(seconds=round(elapsed, 4), peak=transport.peak, images=21)
        stop(session)
    metrics['speedup'] = round(metrics['serial']['seconds']/metrics['parallel']['seconds'], 2)
    assert metrics['parallel']['seconds'] < metrics['serial']['seconds']*.8

    # First four lanes are occupied; priority changes affect the next dispatch only.
    transport = Transport(.005); transport.blocked = set(urls[:4])
    session = CoverSession(transport.factory)
    session.request(items(urls[:21]+urls[:2]), 10, '')
    wait(lambda: len(transport.calls) == 4)
    session.prioritize([urls[20], urls[19]])
    assert session.pending[:2] == [urls[20], urls[19]]
    transport.gate.set()
    wait(lambda: session.completed, session.pump)
    assert set(transport.calls[4:8]) >= {urls[20], urls[19]}
    assert len(transport.calls) == 21 and transport.peak == 4
    # Aggregate encoded cache stays bounded at 64, QPixmap cache remains separate.
    session.request(items(urls), 11, '')
    wait(lambda: session.completed, session.pump)
    assert sum(len(client.covers) for client in transport.clients) <= 64
    stop(session)

    transport = Transport(.002); transport.blocked = set(urls[:4])
    session = CoverSession(transport.factory); session.request(items(urls[:21]), 32, '')
    wait(lambda: len(transport.calls) == 4)
    stop(session)
    assert not session.pending and not session.workers.busy()
    try:session.request(items(urls[:1]), 33, '')
    except DownloadError as exc:assert exc.code == 'closed'
    else:raise AssertionError('closed session accepted new work')

    # A slow first URL cannot hold back all other images.
    transport = Transport(.002); transport.blocked = {urls[0]}
    session = CoverSession(transport.factory); session.request(items(urls[:12]), 20, '')
    received = []
    def collect():
        session.pump(); received.extend(session.tasks.drain())
    wait(lambda: sum(message.kind == 'cover' for message in received) == 11, collect)
    assert urls[0] not in [message.payload.get('url') for message in received]
    transport.gate.set(); wait(lambda: session.completed, collect); stop(session)

    # Cancelling a blocked batch cannot create eight simultaneous requests.
    transport = Transport(.002); transport.blocked = set(urls[:4]); transport.ignore_cancel = True
    session = CoverSession(transport.factory); session.request(items(urls[:12]), 30, '')
    wait(lambda: len(transport.calls) == 4)
    session.cancel(); session.request(items(urls[30:34]), 31, '')
    for _ in range(10):session.pump()
    assert len(transport.calls) == 4 and len(session.active) == 4
    transport.gate.set(); wait(lambda: session.completed, session.pump)
    received = session.tasks.drain()
    assert {message.payload['url'] for message in received if message.kind == 'cover'} == set(urls[30:34])
    assert transport.peak == 4
    stop(session)

    # Ordinary network/bad-image failures are isolated, rejected batches pause globally.
    transport = Transport(.002); transport.fail[urls[0]] = 'network'
    session = CoverSession(transport.factory); session.request(items([*urls[:7], urls[0]+'bad']), 40, '')
    wait(lambda: session.completed, session.pump)
    messages = session.tasks.drain()
    assert sum(message.kind == 'cover' and not message.payload['data'] for message in messages) == 2
    assert len(transport.calls) == 8
    stop(session)
    for code in ('rate_limit', 'auth', 'access'):
        now = [100.0]; transport = Transport(.002); transport.fail[urls[0]] = code
        session = CoverSession(transport.factory, clock=lambda: now[0])
        session.request(items(urls[:21]), 50, '')
        wait(lambda: session.completed, session.pump)
        assert len(transport.calls) <= 4
        assert any(message.kind == 'search_error' and message.payload['code'] == code for message in session.tasks.drain())
        before = len(transport.calls)
        session.request(items(urls[30:34]), 51, '')
        assert session.completed and len(transport.calls) == before
        now[0] += 31
        session.request(items(urls[30:34]), 52, '')
        wait(lambda: session.completed, session.pump)
        assert len(transport.calls) == before+4
        stop(session)

    # Real Qt wiring: selected detail, scroll/resize reprioritization, UI-only pixmaps.
    base = sample_songs()[0]
    records = [replace(base, key=str(i), cover_url=urls[i]) for i in range(30)]
    transport = Transport(.002); transport.blocked = set(urls[:4])
    session = CoverSession(transport.factory)
    window = DownloadWindow(songs=records);window.columns.setCurrentText('3');window.rows.setCurrentText('7');window.show()
    window.enable_covers(session)
    covers = window.cover_browser
    wait(lambda: len(transport.calls) == 4)
    window.show_song(records[20])
    assert session.pending[0] == urls[20]
    window.results.scrollTo(window.song_model.index(15)); QTest.qWait(90)
    assert session.pending[0] == urls[20] and session.priorities[0] == urls[20]
    assert any(url != urls[20] for url in session.priorities)
    mutations = []
    original = covers.cache.put
    def put(url, data):
        mutations.append(threading.get_ident()); return original(url, data)
    covers.cache.put = put
    transport.gate.set()
    wait(lambda: covers.generation is None)
    assert len(covers.cache.sources) == 21 and all(thread == ui_thread for thread in mutations)
    upcoming = [replace(base, key=str(i), cover_url=urls[i]) for i in range(30, 35)]
    covers.warm(upcoming)
    assert covers.warming
    wait(lambda: covers.generation is None)
    assert all(song.cover_url in covers.cache.sources for song in upcoming)
    transport.blocked = set(urls[40:44]); transport.gate.clear()
    covers.warm([replace(base, key=str(i), cover_url=urls[i]) for i in range(40, 50)])
    wait(lambda: len(session.active) == 4)
    covers.page(records[:21])
    assert covers.generation is None and not covers.warming
    assert all(url not in covers.cache.sources for url in urls[40:50])
    window.close(); app.processEvents(); stop(session)
    assert not covers.priority_timer.isActive()
    assert not errors and 'miniaudio' not in sys.modules, errors
    destination = os.environ.get('QUEST_QT_SHOTS')
    if destination:
        path = Path(destination); path.mkdir(parents=True, exist_ok=True)
        (path/'cover-pool-metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
        (path/('cover-pool-metrics-'+os.environ.get('QT_SCALE_FACTOR', '1')+'.json')).write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    print('PASS four lanes, selected/visible priority, 64 encoded cache cap, slow-image isolation, cancel/stale/global cap, cooldown, validation, UI pixmaps and close')
    print(json.dumps(metrics))


def main():
    if '--child' in sys.argv:
        child(); return
    for scale in ('1', '1.25', '1.5'):
        result = subprocess.run([sys.executable, '-B', __file__, '--child'], cwd=ROOT,
            env=dict(os.environ, QT_SCALE_FACTOR=scale, PYTHONIOENCODING='utf-8'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        print(scale, result.stdout.decode('utf-8', errors='replace'), flush=True)
        assert result.returncode == 0


if __name__ == '__main__': main()
