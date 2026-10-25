"""Persistent cover cache boundaries, real media fallback and asynchronous controls."""
import io
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


def png(color='blue', size=256):
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (size, size), color).save(output, format='PNG')
    return output.getvalue()


URL = 'https://cdn.beatsaver.com/disk-'


def load_page(base, forbid=False):
    from quest_tool.qt_ui.cover_session import CoverSession, create_media
    calls = []
    raw = png()
    def factory():
        media = create_media()
        def fetch(url, destination, _cancel, _progress, **_kwargs):
            assert not forbid, 'warm restart accessed network'
            calls.append(url)
            destination.write_bytes(raw)
        media.fetch = fetch
        return media
    session = CoverSession(factory)
    disk = session.configure_disk(base)
    start = time.monotonic()
    messages = []
    session.request([{'coverURL': URL+str(i)} for i in range(21)], 1, '')
    deadline = time.monotonic()+10
    while not session.completed and time.monotonic() < deadline:
        session.pump(); messages.extend(session.tasks.drain()); time.sleep(.002)
    session.pump(); messages.extend(session.tasks.drain())
    session.close(); session.wait_closed()
    covers = [m for m in messages if m.kind == 'cover']
    assert session.completed and len(covers) == 21 and all(m.payload['data'] for m in covers)
    return dict(network=len(calls), hits=disk.hits, images=len(covers), seconds=round(time.monotonic()-start, 4))


def boundaries(base):
    from quest_tool.qt_ui.cover_disk import CoverDiskCache
    data = png()
    now = [time.time()]
    cache = CoverDiskCache(base, max_files=2, clock=lambda: now[0])
    assert not cache.root.exists() and cache.get(URL+'a') is None and not cache.root.exists()
    assert cache.put(URL+'a', data, 0)
    first = cache.root/cache.key(URL+'a')
    written = first.stat().st_mtime
    now[0] += 5
    assert cache.get(URL+'a') == data and first.stat().st_mtime == written
    assert cache.put(URL+'b', data, 0)
    now[0] += 5
    assert cache.get(URL+'a') == data
    assert cache.put(URL+'c', data, 0)
    assert cache.get(URL+'b') is None and cache.get(URL+'a') == data
    assert len(list(cache.root.glob('*.png'))) == 2
    with patch('quest_tool.qt_ui.cover_disk.os.replace', side_effect=PermissionError('test replace')):
        assert not cache.put(URL+'a', png('red'), 0)
    assert cache.get(URL+'a') == data and not list(cache.root.glob('*.tmp'))
    with patch.object(cache, '_remove', side_effect=PermissionError('test eviction')):
        assert not cache.put(URL+'d', data, 0)
    assert len(list(cache.root.glob('*.png'))) == 2
    first.write_bytes(b'corrupt')
    assert cache.get(URL+'a') is None and not first.exists()
    assert not cache.put(URL+'small', png(size=8), 0)
    assert not cache.put(URL+'large', b'x'*(cache.MAX_FILE+1), 0)
    unknown = cache.root/'keep.txt'; unknown.write_text('keep')
    nested = cache.root/'nested'; nested.mkdir(); (nested/'song.dat').write_bytes(b'song')
    stale = cache.root/('f'*64+'.'+'a'*32+'.tmp'); stale.write_bytes(b'temp')
    os.utime(stale, (now[0]-7200, now[0]-7200))
    cache._scan(force=True)
    assert not stale.exists()
    epoch = cache.epoch
    assert cache.clear() == 1
    assert not cache.put(URL+'late', data, epoch)
    assert unknown.read_text() == 'keep' and (nested/'song.dat').read_bytes() == b'song'
    assert (cache.root/'format-v1').read_bytes() == cache.MAGIC
    assert cache.put(URL+'new', data, cache.epoch)
    now[0] += cache.ttl+1
    assert cache.get(URL+'new') is None
    tiny = CoverDiskCache(base, max_bytes=len(data)-1)
    assert not tiny.put(URL+'big', data, 0)
    budgetbase = Path(base)/'budget'; budgetbase.mkdir()
    budget = CoverDiskCache(budgetbase, max_bytes=len(data)*2, max_files=20)
    for i in range(8):assert budget.put(URL+str(i), data, 0)
    assert sum(p.stat().st_size for p in budget.root.glob('*.png')) <= len(data)*2
    # A directory lacking the ownership marker must never be adopted or cleared.
    foreign = Path(base)/'foreign'; foreign.mkdir()
    foreign_cache = CoverDiskCache(foreign); foreign_cache.root.mkdir()
    sentinel = foreign_cache.root/'keep'; sentinel.write_bytes(b'outside')
    assert foreign_cache.get(URL+'x') is None and not foreign_cache.put(URL+'x', data, 0)
    try:foreign_cache.clear()
    except OSError:pass
    else:raise AssertionError('foreign directory cleared')
    assert sentinel.read_bytes() == b'outside'
    # Junctions are common on Windows; do not follow them into any song folder.
    outside = Path(base)/'outside'; outside.mkdir()
    (outside/'keep').write_bytes(b'outside')
    linkbase = Path(base)/'junction'; linkbase.mkdir()
    link = linkbase/'qt-cover-cache-v1'
    result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(outside)],
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr
    try:
        linked = CoverDiskCache(linkbase)
        assert linked.get(URL+'x') is None and not linked.put(URL+'x', data, 0)
        try:linked.clear()
        except OSError:pass
        else:raise AssertionError('junction cleared')
        assert (outside/'keep').read_bytes() == b'outside'
    finally:link.rmdir()


def clear_during_fetch(base):
    from quest_tool.qt_ui.cover_disk import CoverDiskCache
    from quest_tool.qt_ui.cover_session import create_media
    media = create_media()
    cache = media.disk_cache = CoverDiskCache(base)
    entered, gate = threading.Event(), threading.Event()
    def fetch(_url, destination, _cancel, _progress, **_kwargs):
        entered.set(); assert gate.wait(5); destination.write_bytes(png())
    media.fetch = fetch
    values = []
    thread = threading.Thread(target=lambda: values.append(media.load(URL+'race', 'cover', threading.Event())))
    thread.start()
    try:
        assert entered.wait(2)
        assert cache.clear() == 0
    finally:gate.set()
    thread.join(5)
    assert not thread.is_alive() and values and not cache.root.exists()
    # A genuinely new request after clearing may write again.
    media.covers.clear()
    assert media.load(URL+'next', 'cover', threading.Event())
    assert cache.get(URL+'next') is not None


def ui(base):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.cover_session import CoverSession
    from quest_tool.qt_ui.download_window import DownloadWindow
    app = QApplication([])
    errors = []
    sys.excepthook = lambda kind, value, tb: errors.append(str(value))
    window = DownloadWindow(songs=[])
    window.enable_covers(CoverSession())
    window.enable_downloads(base)
    browser = window.download_browser
    loader = window.cover_browser
    controls = loader.cache_controls
    cache = loader.session.disk_cache
    assert browser.cover_loader is loader and controls is not None
    assert cache.put(URL+'ui', png(), cache.epoch)
    assert loader.cache.put(URL+'ui', png())
    memory_image = loader.cache.sources[URL+'ui'].cacheKey()
    marker = cache.root/'format-v1'
    def wait(predicate):
        deadline = time.monotonic()+5
        while not predicate() and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.003)
        assert predicate()
    controls.action.trigger()
    assert controls.busy and not controls.action.isEnabled()
    wait(lambda: not controls.busy)
    assert controls.result == 1 and marker.exists() and controls.action.isEnabled()
    assert loader.cache.sources[URL+'ui'].cacheKey() == memory_image
    with patch.object(cache, 'clear', side_effect=OSError('test clear')):
        controls.action.trigger(); wait(lambda: not controls.busy)
        assert controls.result == 'error'
    controls.action.trigger(); wait(lambda: not controls.busy)
    assert controls.result == 0
    # Closing remains responsive while a clear is active; writer wait survives
    # destruction of the child window's C++ objects and retains its runtime lock.
    gate, entered = threading.Event(), threading.Event()
    original = cache.clear
    def slow_clear():
        entered.set(); assert gate.wait(5); return original()
    with patch.object(cache, 'clear', side_effect=slow_clear):
        controls.action.trigger(); assert entered.wait(2)
        window.close()
        window.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        done = threading.Event()
        waiter = threading.Thread(target=lambda: (browser.wait_closed(), done.set()))
        waiter.start()
        try:assert not done.wait(.05)
        finally:gate.set()
        waiter.join(5); assert done.is_set()
    assert not errors and 'miniaudio' not in sys.modules, errors


def main():
    if '--warm' in sys.argv:
        print(json.dumps(load_page(sys.argv[-1], True))); return
    with tempfile.TemporaryDirectory(prefix='quest-cover-disk-') as directory:
        base = Path(directory)
        for name in ('bounds', 'media', 'ui', 'fallback', 'race'):(base/name).mkdir()
        boundaries(base/'bounds')
        clear_during_fetch(base/'race')
        cold = load_page(base/'media')
        assert cold['network'] == 21 and cold['hits'] == 0
        child = subprocess.run([sys.executable, '-B', __file__, '--warm', str(base/'media')],
                               capture_output=True, text=True, timeout=30)
        assert child.returncode == 0, child.stdout+child.stderr
        warm = json.loads(child.stdout.strip())
        assert warm['network'] == 0 and warm['hits'] == 21
        from quest_tool.qt_ui.cover_disk import CoverDiskCache
        disk = CoverDiskCache(base/'media')
        (disk.root/disk.key(URL+'4')).write_bytes(b'bad')
        repaired = load_page(base/'media')
        assert repaired['network'] == 1 and repaired['hits'] == 20
        foreign = CoverDiskCache(base/'fallback'); foreign.root.mkdir()
        fallback = load_page(base/'fallback')
        assert fallback['network'] == 21 and fallback['hits'] == 0
        ui(base/'ui')
        metrics = dict(cold=cold, restart=warm, corrupt_repair=repaired, unavailable=fallback)
        destination = os.environ.get('QUEST_QT_SHOTS')
        if destination:
            path = Path(destination); path.mkdir(parents=True, exist_ok=True)
            (path/'cover-disk-metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
        print('PASS disk boundaries, restart, corruption repair, fallback, background clear and retained shutdown')
        print(json.dumps(metrics))


if __name__ == '__main__':main()
