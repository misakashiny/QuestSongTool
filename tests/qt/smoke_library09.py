"""Confirmed deletion, bplist round trips and Quest identity guards, offline only."""
import base64
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT),str(ROOT/'tests/qt')]


def service():
    from quest_tool.downloads import DownloadError
    from quest_tool.workers import GuardError
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.playlist_files import PlaylistFileClient, playlist_bytes
    from quest_tool.qt_ui.playlist_store import normalized, PlaylistStore
    from quest_tool.qt_ui.quest_playlists import PLAYLIST_ROOT, QuestPlaylistClient, playlist_name
    from quest_tool.qt_ui.song_delete import SongDeleteClient, local_stamp
    from smoke_usability import device
    selected = device();cancel = threading.Event()
    raw = json.dumps(dict(playlistTitle='精选 / Curated',playlistAuthor='Mapper',songs=[dict(hash='a'*40,key='123',songName='Song')])).encode('utf-8')
    record = normalized(raw.decode('utf-8'));info = b'{"_songName":"Song"}'
    class Provider:
        changed = False
        existing = False
        command_log = None
        pushed = None
        def __init__(self):self.command_log = []
        def _operation(self, cancel):return nullcontext()
        def _devices(self, *_args):return [SimpleNamespace(serial='SIM',usable=True)]
        def _identify(self, *_args):return self,SimpleNamespace(serial='SIM',state='device',model='Quest_3',codename='eureka',brand='Meta'),'OTHER' if self.changed else 'HW'
        def _root(self, bridge, root, *_args):return root
        _descriptor = staticmethod(AdbReadOnlyProvider._descriptor)
        def _contents(self, bridge, root, names, *_args):return [(names[0],info)]
        def _run(self, bridge, args, *_rest, **_kwargs):
            self.command_log.append(args)
            if args[0] == 'push':
                self.pushed = Path(args[1]).read_bytes();return ''
            command = args[1]
            assert not any(word in command for word in ('rm -','tcpip','reboot'))
            if command == 'mv --help':return '-n -T'
            if 'printf QPL_ROOT' in command:return 'QPL_ROOT'
            if ' -print0' in command:return (PLAYLIST_ROOT+'/deck.bplist\0').encode('utf-8')
            if '| base64' in command:return base64.b64encode(self.pushed or raw)+b'\n'
            if 'printf EXISTS' in command:return 'EXISTS' if self.existing else 'EMPTY'
            if 'printf CREATED' in command:return 'CREATED'
            if 'printf PUBLISHED' in command:
                assert self.pushed and hashlib.sha256(self.pushed).hexdigest() in command
                assert 'mv -nT' in command and '[ ! -e' in command
                return 'PUBLISHED'
            if 'printf QSC_REMOVED' in command:
                assert '.quest-deleted-' in command and 'mv -nT' in command
                return 'QSC_REMOVED'
            if 'rmdir ' in command:return ''
            raise AssertionError(args)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve();local = root/'songs';local.mkdir();song = local/'one';song.mkdir();(song/'Info.dat').write_bytes(info)
        calls = [];client = SongDeleteClient(root,trash=lambda path:(calls.append(path) or True,'recycled'))
        request = dict(origin='local',root=str(local),items=[dict(folder='one',stamp=local_stamp(local,'one'))])
        value = client.search(request,0,'',cancel);assert len(value['done']) == 1 and calls == [str(song)]
        request['items'][0]['stamp'] = (0,0,0);calls.clear()
        assert len(client.search(request,0,'',cancel)['failed']) == 1 and not calls
        for name in ('../one','.','a/b','C:\\one'):
            try:local_stamp(local,name)
            except (DownloadError,GuardError,OSError,ValueError):pass
            else:raise AssertionError('unsafe local path')
        provider = Provider();deleter = SongDeleteClient(root,provider)
        request = dict(origin='quest',device=selected,items=[dict(folder='one',info_hash=hashlib.sha256(info).hexdigest())])
        result = deleter.search(request,0,'',cancel);assert len(result['done']) == 1
        journals = list((root/'deleted-songs').glob('*.json'));assert len(journals) == 1 and json.loads(journals[0].read_text('utf-8'))['state'] == 'quarantined'
        provider.command_log.clear();request['items'][0]['info_hash'] = 'b'*64
        assert deleter.search(request,0,'',cancel)['failed'] and not any('mv -nT' in str(c) for c in provider.command_log)
        provider.changed = True;provider.command_log.clear()
        assert deleter.search(request,0,'',cancel)['failed'] and not provider.command_log
        provider.changed = False
        files = PlaylistFileClient(root)
        saved = files.search(dict(action='remember',record=record),0,'',cancel)
        assert Path(saved['path']).suffix == '.bplist' and PlaylistStore(root).load() == [record]
        assert normalized(Path(saved['path']).read_text('utf-8')) == record
        exported = root/'exported.bplist';files.search(dict(action='export',record=record,path=str(exported)),0,'',cancel)
        assert exported.read_bytes() == playlist_bytes(record)
        quest = QuestPlaylistClient(provider,root)
        listed = quest.search(dict(action='list',device=selected),0,'',cancel)
        assert listed['path'] == PLAYLIST_ROOT and listed['rows'][0]['record'] == record
        pulled = quest.search(dict(action='pull',device=selected,name='deck.bplist',sha256=listed['rows'][0]['sha256']),0,'',cancel)
        assert Path(pulled['path']).is_file() and PlaylistStore(root).load() == [record]
        try:quest.search(dict(action='pull',device=selected,name='deck.bplist',sha256='0'*64),0,'',cancel)
        except DownloadError as exc:assert exc.code == 'delete_changed'
        else:raise AssertionError('changed playlist accepted')
        result = quest.search(dict(action='push',device=selected,record=record),0,'',cancel)
        assert result['path'].endswith('.bplist') and not result['exists']
        provider.existing = True;provider.command_log.clear()
        assert quest.search(dict(action='push',device=selected,record=record),0,'',cancel)['exists']
        assert not any(args[0] == 'push' or 'mv -nT' in str(args) for args in provider.command_log)
        provider.pushed = b'{}';provider.command_log.clear()
        try:quest.search(dict(action='push',device=selected,record=record),0,'',cancel)
        except DownloadError as exc:assert exc.code == 'playlist_target_exists'
        else:raise AssertionError('overwrite accepted')
        provider.pushed = None;provider.changed = True;provider.command_log.clear()
        try:quest.search(dict(action='list',device=selected),0,'',cancel)
        except DownloadError:pass
        else:raise AssertionError('changed hardware accepted')
        assert not provider.command_log
        for name in ('../x.bplist','.hidden.bplist','a/b.bplist','x.zip'):
            try:playlist_name(name)
            except DownloadError:pass
            else:raise AssertionError('unsafe playlist filename')
    print('PASS library09 services: recycle adapter, stale and path guards, Quest quarantine journal, bplist store/export, staged no-overwrite publish, identity rejection')


def ui():
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.playlist_store import PlaylistStore, normalized
    from quest_tool.qt_ui.song_delete import confirm_removal
    from quest_tool.messages import tr
    from smoke_verification import song
    app = QApplication.instance() or QApplication([]);app.setStyle('Fusion');errors = []
    sys.excepthook = lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    def wait(predicate):
        end = time.monotonic()+10
        while not predicate() and time.monotonic()<end:QTest.qWait(20)
        assert predicate(),('timeout',errors)
    with tempfile.TemporaryDirectory() as folder, patch('socket.socket',side_effect=AssertionError('offline only')):
        root = Path(folder).resolve();local = root/'songs';local.mkdir();song(local/'first');song(local/'second')
        record = normalized(json.dumps(dict(playlistTitle='本地歌单 / Local playlist',songs=[dict(hash='a'*40,key='123')],playlistAuthor='Mapper')))
        PlaylistStore(root).save([record],threading.Event())
        factory_calls = []
        def factory():factory_calls.append(1);return object(),SimpleNamespace(journal_dir=root/'transfers')
        w = LibraryWindow(root,transfer_factory=factory);w.show();w.set_target(str(local));wait(lambda:w.local_ready)
        assert factory_calls == [1] and w.transfers.push.isVisible() and w.transfers.push_missing.isVisible()
        assert not hasattr(w.wireless,'transfer_button') and not hasattr(w.wireless,'transfer_entry')
        w.activateWindow();w.raise_();w.table.setFocus();w.table.selectRow(0);QTest.qWait(100)
        attempts = []
        with patch('quest_tool.qt_ui.song_delete.confirm_removal',return_value=False) as confirmation:
            QTest.keyClick(w.table,Qt.Key.Key_Delete);assert confirmation.call_count == 1
            assert w.deletion.generation is None
            index = w.table.currentIndex()
            w.actions.show_menu(w.table,'local',w.table.visualRect(index).center())
            menu = w.actions.menu;assert menu is not None
            next(action for action in menu.actions() if action.text() == tr('songs.delete.action')).trigger()
            menu.close();assert confirmation.call_count == 2 and not attempts
        (root/'test-recycle').mkdir()
        def trash(path):
            attempts.append(path);target = root/'test-recycle'/Path(path).name
            os.rename(path,target);return True,str(target)
        w.deletion.session.client.trash = trash
        with patch('quest_tool.qt_ui.song_delete.confirm_removal',return_value=True):
            QTest.keyClick(w.table,Qt.Key.Key_Delete);wait(lambda:w.deletion.generation is None and not w.scanning)
        assert len(attempts) == 1 and len(w.model.rows) == 1 and not Path(attempts[0]).exists()
        w.table.selectRow(0)
        with patch('quest_tool.qt_ui.song_delete.confirm_removal',side_effect=lambda *_args:(w.refresh() or True)):
            QTest.keyClick(w.table,Qt.Key.Key_Delete)
            assert w.deletion.generation is None and len(attempts) == 1
        wait(lambda:w.local_ready)
        # Text editing never invokes song deletion.
        w.search.setFocus();w.search.setText('abc');QTest.keyClick(w.search,Qt.Key.Key_Delete);assert len(attempts) == 1
        w.search.clear()
        # Actual modal keyboard behavior, without deleting any file.
        def enter():
            box = QApplication.activeModalWidget();assert isinstance(box,QMessageBox)
            QTest.keyClick(box,Qt.Key.Key_Return)
        QTimer.singleShot(50,enter);assert confirm_removal(w,'Test','Confirm')
        QTimer.singleShot(50,lambda:QTest.keyClick(QApplication.activeModalWidget(),Qt.Key.Key_Escape))
        assert not confirm_removal(w,'Test','Cancel')
        w.playlists.show();wait(lambda:w.playlists.store_generation is None and w.playlists.match_generation is None)
        d = w.playlists.dialog;assert d.sources.count() == 2 and d.model.rowCount() == 1
        assert d.send_quest.isVisible() and not d.send_quest.isEnabled()
        d.sources.setCurrentIndex(1);assert w.playlists.source == 'quest' and not d.add.isVisible() and d.read_quest.isVisible()
        d.sources.setCurrentIndex(0);assert w.playlists.source == 'local' and len(w.playlists.records) == 1
        for width,height in ((700,500),(1100,720)):
            d.resize(width,height);QTest.qWait(80)
            assert d.table.height() > 60 and d.table.width() > 200
        d.close();w.close();w.wait_closed()
        download = DownloadWindow();assert download.columns.currentText() == '6' and download.rows.currentText() == '8'
        download.show();download.close()
        assert not errors,errors
    print('PASS library09 UI: default transfer actions, Delete focus/cancel/confirm, modal Enter/Esc, local/Quest tabs, responsive layout, 6x8 default')


if __name__ == '__main__':
    service();ui()
