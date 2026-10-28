"""Local-only playlist persistence, hash identity and real Qt interactions."""
import base64
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
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]
from smoke_verification import song


def fixture(path, digest):
    from PIL import Image
    image = io.BytesIO();Image.new('RGB', (32, 32), '#3578dc').save(image, format='PNG')
    value = dict(playlistTitle='测试歌单 Test', playlistAuthor='Mapper', image=base64.b64encode(image.getvalue()).decode(), songs=[
        dict(songName='First', levelAuthorName='Mapper', levelId='custom_level_'+digest.upper(), key='abc'),
        dict(songName='Missing', hash='f'*40, key='def'),
        dict(songName='First', levelAuthorName='Mapper'),
    ])
    path.write_text(json.dumps(value), encoding='utf-8-sig')
    return value


def service():
    from quest_tool.downloads import DownloadError
    from quest_tool.qt_ui.playlist_store import PlaylistStore, normalized, read_playlist, MAX_FILE
    from quest_tool.qt_ui.playlist_match import PlaylistMatchClient
    with tempfile.TemporaryDirectory() as name:
        root = Path(name).resolve();songs = root/'songs';songs.mkdir()
        digest = song(songs/'arbitrary folder');source = root/'中文.bplist';data = fixture(source, digest)
        original = source.read_bytes();cancel = threading.Event();store = PlaylistStore(root/'state')
        assert not store.path.exists() and store.load() == []
        value = read_playlist(source)
        assert value['songs'][0]['hash'] == digest and value['cover'] and len(value['songs']) == 3
        values = store.search(dict(action='import', paths=[str(source), str(source)]), 0, '', cancel)
        assert values == store.load() and len(values) == 1 and source.read_bytes() == original
        assert normalized(json.dumps(dict(songs=[])))['songs'] == []
        assert not normalized(json.dumps(dict(songs=[], image='not a picture')))['cover']
        for wrong in ({}, dict(songs='bad'), dict(songs=[None]), dict(songs=[{'hash':'bad'}]),
                      dict(songs=[{'hash':'a'*40, 'levelId':'custom_level_'+'b'*40}]),
                      dict(songs=[{'key':'https://bad'}]), dict(songs=[{'songName':[]}])):
            try:normalized(json.dumps(wrong))
            except (ValueError, TypeError, DownloadError):pass
            else:raise AssertionError('bad playlist accepted')
        huge = root/'huge.json';huge.write_bytes(b' '*(MAX_FILE+1))
        before = store.path.read_bytes()
        try:store.search(dict(action='import',paths=[str(source),str(huge)]),0,'',cancel)
        except DownloadError:pass
        else:raise AssertionError('oversize accepted')
        assert store.path.read_bytes() == before
        for broken in (b'{broken', b'{"version":99,"lists":[]}'):
            store.path.write_bytes(broken)
            try:store.search(dict(action='import',paths=[str(source)]),0,'',cancel)
            except (ValueError, DownloadError):pass
            else:raise AssertionError('bad storage overwritten')
            assert store.path.read_bytes() == broken
        store.path.write_bytes(before)
        matcher = PlaylistMatchClient();request = dict(root=str(songs),folders=['arbitrary folder'])
        result = matcher.search(request,0,'',cancel)
        assert result['matches'] == {digest:'arbitrary folder'}
        with patch('quest_tool.qt_ui.local_verification.validate_song',side_effect=AssertionError('unnecessary hash')):
            assert matcher.search(request,0,'',cancel)['matches'] == result['matches']
        (songs/'arbitrary folder/Expert.dat').write_text('{"_notes":[1]}')
        assert digest not in matcher.search(request,0,'',cancel)['matches']
        matcher.MAX_BYTES = 1
        assert not matcher.search(dict(request,force=True),0,'',cancel)['matches']
        assert not matcher.search(dict(request,folders=['../escape']),0,'',cancel)['matches']
        cancel.set()
        try:store.search(dict(action='remove',id=value['id']),0,'',cancel)
        except DownloadError:pass
        else:raise AssertionError('cancel ignored')
        assert store.path.read_bytes() == before
        cancel.clear();assert store.search(dict(action='remove',id=value['id']),0,'',cancel) == []
        assert source.read_bytes() == original
    print('PASS playlist services: bounded import, hashes/levelId, atomic storage, corrupt/future guard, cancellation and changing files')


def child():
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from quest_tool.messages import tr
    from quest_tool.song_search import SearchSession
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.download_window import DownloadWindow
    app = QApplication([]);errors = [];sys.excepthook = lambda kind, value, tb:(errors.append(str(value)),sys.__excepthook__(kind,value,tb))

    def wait(predicate):
        until = time.monotonic()+12
        while not predicate() and time.monotonic()<until:
            QTest.qWait(20)
        assert predicate(), 'UI timeout'

    class Local:
        def search(self, target, _page, _order, cancel):
            return dict(target=target,rows=[dict(folder='fixture',title='First',author='Mapper',duration=0,error='')])

    with tempfile.TemporaryDirectory() as name, patch('subprocess.Popen',side_effect=AssertionError('process forbidden')), patch('socket.socket',side_effect=AssertionError('network forbidden')):
        root = Path(name).resolve();songs = root/'songs';songs.mkdir();digest = song(songs/'fixture')
        source = root/'input.bplist';fixture(source,digest);source_bytes = source.read_bytes()
        for language, theme in (('zh','light'),('en','dark')):
            window = LibraryWindow(root/language,language,theme,session=SearchSession(Local()))
            window.show();assert QTest.qWaitForWindowExposed(window,5000)
            assert not (root/language/'qt-playlists-v1.json').exists()
            assert window.set_target(str(songs));wait(lambda:window.local_ready)
            window.playlists_button.click();controller = window.playlists;dialog = controller.dialog
            wait(lambda:controller.store_generation is None)
            assert not controller.records and tr('qt.playlists.empty') in dialog.title.text()
            with patch('quest_tool.qt_ui.library_playlists.QFileDialog.getOpenFileNames',return_value=([str(source)],'')):
                dialog.add.click()
            wait(lambda:bool(controller.records) and controller.store_generation is None and controller.match_generation is None)
            assert dialog.model.rowCount() == 3 and controller.matches == {digest:'fixture'}
            assert dialog.model.index(2,2).data() == tr('qt.playlists.unmatched')
            dialog.search.setText('Missing');assert dialog.proxy.rowCount()==1
            dialog.table.selectRow(0);assert dialog.download.isEnabled() and not dialog.play.isEnabled()
            assert controller.download_text() == 'https://beatsaver.com/maps/def'
            download_window = DownloadWindow(language, theme, songs=())
            download_window.enable_downloads(root/(language+'-downloads'))
            draft = download_window.download_browser.importer.input
            draft.setPlainText('existing import draft')
            with patch.object(window,'open_downloads',lambda:None), patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes), patch.object(download_window.download_browser,'enqueue') as enqueue:
                window.download_window = download_window;dialog.download.click();window.download_window = None
            enqueue.assert_called_once_with(['def'])
            assert draft.toPlainText() == 'existing import draft'
            assert download_window.pages.currentWidget() is download_window.tasks_page and download_window.pages.currentIndex() == download_window.tabs.currentIndex()
            assert not download_window.download_browser.manager.snapshot()
            download_window.close();download_window.download_browser.wait_closed()
            dialog.show();dialog.search.clear();dialog.table.selectRow(0)
            calls = []
            with patch.object(window.preview,'start',calls.append):
                dialog.play.click();wait(lambda:controller.match_generation is None)
            assert calls == [0] and window.details.key == ('local',str(songs),'fixture')
            assert window.preview.track_key==('local',str(songs),'fixture')
            assert dialog.isVisible()
            controller.show();wait(lambda:controller.store_generation is None and controller.match_generation is None)
            dialog.table.selectRow(0)
            with patch.object(window.preview,'start',calls.append):
                dialog.table.doubleClicked.emit(dialog.proxy.index(0,0));wait(lambda:controller.match_generation is None)
            assert calls==[0,0] and dialog.isVisible()
            controller.show();wait(lambda:controller.store_generation is None and controller.match_generation is None)
            assert len(controller.records)==1 and dialog.model.rowCount()==3
            for width,height in ((680,480),(940,620)):
                dialog.resize(width,height);QTest.qWait(75)
                assert dialog.table.width()>250 and dialog.table.height()>50
                for button in (dialog.add,dialog.detail,dialog.play,dialog.download,dialog.chrome.close_button):
                    assert button.width() >= button.sizeHint().width()
                    assert dialog.rect().contains(button.mapTo(dialog,button.rect().center()))
            dialog.table.selectRow(0);assert dialog.play.isEnabled()
            # Refresh invalidates every old match immediately, before worker results.
            window.refresh();assert not controller.matches and not dialog.play.isEnabled()
            wait(lambda:window.local_ready and controller.match_generation is None)
            assert controller.matches == {digest:'fixture'}
            output = os.environ.get('QUEST_QT_SHOTS')
            if output:
                folder=Path(output);folder.mkdir(parents=True,exist_ok=True)
                assert dialog.grab().save(str(folder/f'playlists-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            window.language.setCurrentIndex(window.language.findData('en' if language=='zh' else 'zh'))
            assert dialog.windowTitle() == tr('qt.playlists.title')
            dialog.close_button.click();window.close();window.wait_closed();app.processEvents()
            assert controller.closed and not controller.timer.isActive()
            assert source.read_bytes()==source_bytes
        assert not errors, errors
    assert 'miniaudio' not in sys.modules
    print('PASS playlists UI: import/reopen, cover, matching, search, shared preview, direct enqueue without replacing draft, language/layout and close')


if __name__ == '__main__':
    if '--child' in sys.argv:
        child()
    else:
        service()
        for scale in ('1','1.25','1.5'):
            subprocess.run([sys.executable,'-B',__file__,'--child'],env=dict(os.environ,QT_SCALE_FACTOR=scale),check=True)
