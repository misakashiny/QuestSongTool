"""Offline bookmark storage, selection isolation and shared review interactions."""
from dataclasses import asdict, replace
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PIL import Image
    from smoke_prefetch import Session
    from PySide6.QtWidgets import QApplication
    from quest_tool.downloads import DownloadError
    from quest_tool.messages import tr
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.favorites_store import FavoritesStore
    from quest_tool.qt_ui.song_model import Song

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    one = Song('abc', 'Alpha 中文', 'Artist', 'Mapper', 120, 100, 90,
               'a'*40, cover_url='https://cdn.beatsaver.com/cover.png', preview_url='https://cdn.beatsaver.com/preview.mp3')
    two = replace(one, key='def', title='Beta', digest='b'*40)
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        store = FavoritesStore(base/'store')
        assert store.load() == {} and not store.path.exists()
        store.update(song=one); store.update(song=two)
        assert FavoritesStore(store.root).load() == {'abc': one, 'def': two}
        store.update(song=replace(one, title='Updated'))
        assert len(store.load()) == 2 and store.load()['abc'].title == 'Updated'
        for bad in (replace(one, key='../outside'), replace(one, digest='bad'),
                    replace(one, seconds=float('nan')), replace(one, rating=True),
                    replace(one, bpm=10**1000), replace(one, diffs=(chr(0xD800),)),
                    replace(one, cover_url='https://example.com/cover.png')):
            raw = store.path.read_bytes()
            try: store.update(song=bad)
            except (ValueError, DownloadError): pass
            else: raise AssertionError('invalid record saved')
            assert store.path.read_bytes() == raw
        full = [asdict(replace(one, key=f'{i:x}')) for i in range(200)]
        store.path.write_text(json.dumps({'version': 1, 'songs': full}), encoding='utf-8')
        raw = store.path.read_bytes()
        try: store.update(song=two)
        except ValueError: pass
        else: raise AssertionError('favorite limit')
        assert store.path.read_bytes() == raw
        for raw in (b'{broken', b'\xff', b'x'*(store.MAX_BYTES+1),
                    b'{"version":2,"songs":[]}', b'{"version":true,"songs":[]}',
                    json.dumps({'version': 1, 'songs': [asdict(one), asdict(one)]}).encode()):
            store.path.write_bytes(raw)
            try: store.update(song=two)
            except (ValueError, DownloadError): pass
            else: raise AssertionError('corrupt/future file overwritten')
            assert store.path.read_bytes() == raw
        for language, theme in (('zh', 'light'), ('en', 'dark')):
            folder = base/language
            window = DownloadWindow(language, theme, songs=(one, two))
            window.enable_downloads(folder)
            favorite = window.favorites_browser
            window.enable_covers(Session())
            data = io.BytesIO(); Image.new('RGB', (64, 64), 'blue').save(data, format='PNG')
            assert window.cover_browser.cache.put(one.cover_url, data.getvalue())
            assert not favorite.store.path.exists()
            window.show_song(one)
            with patch.object(favorite.store, 'update', side_effect=OSError('controlled write error')):
                favorite.toggle_action.trigger()
            assert favorite.write_error and not favorite.store.path.exists()
            favorite.reload()  # Same path as reopening the menu.
            assert favorite.write_error and favorite.list_action.text() == tr('qt.favorites.error')
            assert favorite.toggle_action.isEnabled()
            favorite.toggle_action.trigger()
            assert not favorite.write_error
            window.show_song(two); favorite.toggle_action.trigger()
            assert set(favorite.values) == {'abc', 'def'} and not window.song_model.checked
            favorite.open(); dialog = favorite.dialog
            favorite.open(); assert favorite.dialog is dialog
            dialog.search.setText('Alpha'); app.processEvents()
            assert dialog.proxy.rowCount() == 1
            assert dialog.model.data(dialog.model.index(0, 0), Qt.ItemDataRole.DecorationRole) is not None
            dialog.table.selectRow(0); app.processEvents()
            assert dialog.download.text() == tr('qt.favorites.select', count=1)
            played = []
            window.preview_browser = SimpleNamespace(song=None, render=lambda: None, close=lambda: None,
                                                      play_song=played.append)
            dialog.selection_changed(); dialog.play()
            assert played == [one] and window.current_song == one
            dialog.download.click()
            assert window.song_model.checked == {'abc'} and not window.download_browser.manager.snapshot()
            dialog.remove.click()
            assert set(favorite.values) == {'def'} and window.song_model.checked == {'abc'}
            dialog.search.clear(); app.processEvents(); dialog.table.selectRow(0)
            window.song_model.checked = {f'{i:x}' for i in range(200)}
            before = set(window.song_model.checked)
            dialog.enqueue()
            assert window.song_model.checked == before
            assert tr('qt.favorites.selection_limit') in dialog.hint.text()
            window.show(); window.resize(1000, 800); QTest.qWait(40)
            assert dialog.remove.geometry().right() < dialog.download.geometry().left() or dialog.remove.geometry().bottom() < dialog.download.geometry().top()
            window.retranslate('en' if language == 'zh' else 'zh')
            assert dialog.windowTitle() == tr('qt.favorites.title')
            output = os.environ.get('QUEST_QT_SHOTS')
            if output:
                shots = Path(output); shots.mkdir(parents=True, exist_ok=True)
                assert dialog.grab().save(str(shots/f'favorites-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            window.close(); window.download_browser.wait_closed(); app.processEvents()
            assert favorite.closed and favorite.dialog is None
            reopened = DownloadWindow(language, theme, songs=(one, two))
            reopened.enable_downloads(folder)
            assert set(reopened.favorites_browser.values) == {'def'} and not reopened.song_model.checked
            path = reopened.favorites_browser.store.path
            path.write_bytes(b'{broken')
            reopened.favorites_browser.reload()
            assert reopened.favorites_browser.error and not reopened.favorites_browser.toggle_action.isEnabled()
            reopened.show_song(one); reopened.favorites_browser.toggle()
            assert path.read_bytes() == b'{broken'
            reopened.close(); reopened.download_browser.wait_closed(); app.processEvents()
        assert not errors, errors
    assert 'miniaudio' not in sys.modules
    print('PASS favorites: bounded atomic snapshots, bad files preserved, persistence, shared preview, filtering, independent checks, 200-selection guard and close')


if __name__ == '__main__':
    if '--child' in sys.argv:
        child()
    else:
        for scale in ('1', '1.25', '1.5'):
            subprocess.run([sys.executable, '-B', __file__, '--child'],
                           env=dict(os.environ, QT_SCALE_FACTOR=scale), check=True)
