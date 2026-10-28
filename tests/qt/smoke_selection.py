"""Offline cross-page review and truthful queue/history status; no audio device."""
from dataclasses import replace
import copy
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]


def child():
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.messages import tr
    from quest_tool.downloads import DownloadError
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import sample_songs
    from quest_tool.qt_ui.song_status import StatusIndex
    from quest_tool.song_search import SearchSession
    from quest_tool.workers import TaskRunner
    from smoke_prefetch import Session
    from smoke_search_browser import ScriptedClient

    app = QApplication([]); app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value)); sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    def wait(predicate):
        deadline = time.monotonic()+6
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10); time.sleep(.002)
        assert predicate() and not errors, errors
    class Manager:
        def __init__(self, root):
            self.history = root/'history.json'; self.tasks = TaskRunner()
            self.last_keep_zip = False; self.records = []; self.calls = []
            self.fail = False
        def snapshot(self): return copy.deepcopy(self.records)
        def enqueue(self, sources, target, **kwargs):
            if self.fail:
                raise DownloadError('network')
            self.calls.append((list(sources), target, kwargs, threading.get_ident()))
            for source in sources:
                self.records.append(dict(id=str(len(self.records)), source=source, target=target, kind='online',
                    state='queued', title='', digest='', progress=0, error='', installed=''))
        def shutdown(self): pass
    class Preview:
        def __init__(self): self.calls = []; self.closed = False; self.song = None
        def render(self): pass
        def play_song(self, song): self.calls.append(song)
        def close(self): self.closed = True

    songs = tuple(replace(song, key=f'{i+1:x}', title=f'Song {i+1}', digest=f'{i+1:040x}',
        cover_url='https://cdn.beatsaver.com/fixture.png', preview_url='https://cdn.beatsaver.com/fixture.mp3')
        for i, song in enumerate(sample_songs()))
    with tempfile.TemporaryDirectory(prefix='quest-selected-') as folder:
        root = Path(folder); manager = Manager(root)
        window = DownloadWindow(songs=songs)
        window.columns.setCurrentText('3');window.rows.setCurrentText('7')
        window.enable_downloads(root, manager)
        queue = window.download_browser
        wait(lambda: not queue.importer.scanning)
        target = str(queue.target)
        def job(source, state, digest='', path=target):
            return dict(id=source+'-'+state, source=source, state=state, digest=digest, target=path,
                        kind='online', title='Song 2', progress=.5, error='', installed='')
        manager.records = [job(songs[0].key, 'downloading'), job(songs[1].key, 'done', songs[1].digest)]
        queue.poll()
        model = window.song_model
        assert model.status_labels[songs[0].key] == tr('dl.state.downloading')
        assert model.status_labels[songs[1].key] == tr('qt.selection.same_version')
        assert not model.status_labels[songs[2].key]  # A matching title is never used as identity.
        assert tr('qt.selection.same_version') in model.data(model.index(1), Qt.ItemDataRole.ToolTipRole)
        assert not StatusIndex(manager.records, str(root/'other')).label(songs[0])
        changed_song = replace(songs[1], digest='f'*40)
        assert StatusIndex(manager.records, target).label(changed_song) == tr('qt.selection.other_version')
        assert not StatusIndex([job('other-id', 'done', '')], target).label(songs[2])
        assert not StatusIndex([dict(job('other-id', 'done'), digest=None), None], target).label(songs[2])
        window.show(); window.activateWindow()
        assert QTest.qWaitForWindowExposed(window, 5000)
        shots = Path(os.environ.get('QUEST_QT_SHOTS', root/'shots')); shots.mkdir(parents=True, exist_ok=True)
        QTest.qWait(20)
        assert window.grab().save(str(shots/'selection-status-cards.png'))
        for row in (0, 1): model.setData(model.index(row), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        window.refresh_page(1)
        model.setData(model.index(0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        chosen = {songs[0].key, songs[1].key, songs[21].key}
        assert model.checked == chosen
        media = Session(); window.enable_covers(media)
        preview = Preview(); window.preview_browser = preview
        QTest.mouseClick(window.selection, Qt.MouseButton.LeftButton)
        controller = window.selection_browser
        wait(lambda: controller.dialog is not None)
        dialog = controller.dialog
        controller.open(); assert controller.dialog is dialog
        assert dialog.model.parent() is dialog and dialog.model.rowCount() == 3
        unsafe = replace(songs[0], title="<img src='file:///must-not-read'>")
        dialog.model.replace((unsafe,))
        tooltip = dialog.model.data(dialog.model.index(0, 1), Qt.ItemDataRole.ToolTipRole)
        assert '&lt;img' in tooltip and '<img' not in tooltip
        controller.refresh()
        assert [song.key for song in dialog.model.songs] == [songs[i].key for i in (0, 1, 21)]
        assert str(queue.target) in dialog.target.text()
        assert not app.activeModalWidget()  # Main page remains usable while reviewing.
        png = io.BytesIO(); Image.new('RGB', (64, 64), '#3574da').save(png, format='PNG')
        cover = window.cover_browser
        media.tasks.emit('cover', payload={'generation': cover.epoch, 'url': songs[0].cover_url, 'data': png.getvalue()})
        media.tasks.emit('search', payload={'generation': cover.generation, 'result': {}})
        cover.poll()
        assert not dialog.model.data(dialog.model.index(0, 0), Qt.ItemDataRole.DecorationRole).isNull()
        assert len(media.calls) == 1  # Opening/searching the selection creates no media request.
        dialog.table.selectRow(0)
        QTest.mouseClick(dialog.preview, Qt.MouseButton.LeftButton)
        assert preview.calls == [songs[0]] and window.current_song == songs[0]
        old_text = dialog.detail.toPlainText()
        cursor = dialog.detail.textCursor(); cursor.select(cursor.SelectionType.Document)
        dialog.detail.setTextCursor(cursor)
        for _ in range(10): queue.refresh()
        assert dialog.detail.textCursor().hasSelection() and dialog.detail.toPlainText() == old_text
        dialog.search.setText('Song 22')
        assert dialog.proxy.rowCount() == 1 and len(model.checked) == 3
        dialog.table.selectRow(0)
        QTest.mouseClick(dialog.remove, Qt.MouseButton.LeftButton)
        assert model.checked == {songs[0].key, songs[1].key} and not dialog.proxy.rowCount()
        assert not list(queue.target.iterdir())
        dialog.search.clear()
        model.setData(model.index(0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        dialog.table.selectRow(0)
        shots = Path(os.environ.get('QUEST_QT_SHOTS', root/'shots')); shots.mkdir(parents=True, exist_ok=True)
        QTest.qWait(30)
        assert dialog.grab().save(str(shots/'selected-light.png'))
        window.retranslate('en'); window.change_theme('dark')
        assert dialog.windowTitle() == tr('qt.selection.title')
        assert tr('qt.selection.same_version') == dialog.model.data(dialog.model.index(1, 3))
        dialog.resize(420, 500); QTest.qWait(30)
        assert dialog.width() == 420
        for button in (dialog.preview, dialog.remove, dialog.download, dialog.close_button):
            assert button.geometry().right() < dialog.width() and button.geometry().bottom() < dialog.height()
            assert button.width() >= button.minimumSizeHint().width(), (button.text(), button.width(), button.minimumSizeHint().width())
        dialog.resize(800, 620)
        QTest.qWait(30); assert dialog.grab().save(str(shots/'selected-dark.png'))
        # A new target discards old status records but keeps the explicit selection.
        other = root/'new-target'; other.mkdir()
        assert queue.set_target(str(other))
        wait(lambda: not queue.importer.scanning)
        assert model.checked == chosen and not any(model.status_labels.values())
        assert str(other.resolve()) in dialog.target.text()
        dialog.search.setText('Song 1')
        assert dialog.proxy.rowCount() < len(model.checked)
        manager.fail = True
        QTest.mouseClick(dialog.download, Qt.MouseButton.LeftButton)
        wait(lambda: queue.error == 'network' and not queue.busy)
        assert dialog.hint.text().startswith(tr('dl.error.network')) and model.checked == chosen
        manager.fail = False
        QTest.mouseClick(dialog.download, Qt.MouseButton.LeftButton)
        wait(lambda: len(manager.calls) == 1 and not queue.busy)
        sources, destination, _options, worker = manager.calls[0]
        assert set(sources) == chosen and destination == str(other.resolve())
        assert worker != threading.get_ident() and not model.checked and dialog.model.rowCount() == 0
        assert not dialog.download.isEnabled()
        # Empty/reopen/close preserve application state and release the per-dialog model.
        dialog.close(); wait(lambda: controller.dialog is None)
        controller.open(); assert controller.dialog is not dialog
        model.select_page(); assert controller.dialog.model.rowCount() == 21
        model.clear_checks(); assert controller.dialog.model.rowCount() == 0
        window.close(); app.processEvents(); queue.wait_closed()
        assert controller.closed and controller.dialog is None and preview.closed and media.closed

    # A committed search change clears selections and the open panel together.
    client = ScriptedClient(); session = SearchSession(client)
    window = DownloadWindow();window.columns.setCurrentText('3');window.rows.setCurrentText('7');window.enable_search(session)
    wait(lambda: window.browser.state == 'ready')
    window.song_model.select_page(); window.selection_browser.open()
    dialog = window.selection_browser.dialog
    assert dialog.model.rowCount() == 21
    window.search.setText('empty'); window.browser.search()
    wait(lambda: window.browser.state == 'empty')
    assert not window.song_model.checked and dialog.model.rowCount() == 0
    window.close(); app.processEvents()
    for thread in tuple(session.tasks._threads): thread.join(3); assert not thread.is_alive()
    assert 'miniaudio' not in sys.modules and not errors, errors
    print('PASS status identity/target/version, cross-page review/search/remove/preview route/all-selected enqueue, cache-only art, singleton/language/close/search reset')


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
