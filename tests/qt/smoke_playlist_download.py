"""Whole-playlist preview and original queue installation, entirely isolated."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]


def child():
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from quest_tool.downloads import DownloadError, DownloadManager, TERMINAL, check_cancel
    from quest_tool.playlist import song_hash
    from quest_tool.qt_ui.playlist_download import PlaylistDownloadClient, playlist_url
    from quest_tool.qt_ui.download_window import DownloadWindow
    from smoke_downloads import make_archive, FakeClient
    app = QApplication([]);errors = []
    def report(kind, value, tb):
        errors.append(repr(value));sys.__excepthook__(kind, value, tb)
    sys.excepthook = report

    def wait(predicate):
        end = time.monotonic()+15
        while not predicate() and time.monotonic()<end:
            QTest.qWait(20)
        assert predicate(), 'playlist timeout'

    assert playlist_url('https://beatsaver.com/playlists/1280881') == 'https://api.beatsaver.com/playlists/id/1280881/download'
    for bad in ('http://beatsaver.com/playlists/1','https://evil.test/playlists/1','https://beatsaver.com@evil.test/playlists/1',
                'https://beatsaver.com/playlists/1?url=bad','https://beatsaver.com:443/playlists/1','https://beatsaver.com/maps/abc',
                'https://beatsaver.com/playlists/0','https://beatsaver.com/playlists/1\nhttps://beatsaver.com/maps/2'):
        try:playlist_url(bad)
        except (ValueError, DownloadError):pass
        else:raise AssertionError('bad URL accepted')
    with tempfile.TemporaryDirectory() as folder, patch('socket.socket',side_effect=AssertionError('network forbidden')):
        root = Path(folder).resolve();archive = make_archive(root);fixture = root/'fixture';fixture.mkdir()
        with zipfile.ZipFile(archive) as zipped:zipped.extractall(fixture)
        source = root/'sample.bplist'
        source.write_text(json.dumps(dict(playlistTitle='整份歌单 Whole playlist',playlistAuthor='Test',
            customData={'syncURL':'https://evil.test/'},songs=[dict(songName='First',key='1'),dict(songName='Duplicate',key='1'),dict(songName='Second',key='2')])),encoding='utf-8')
        original = source.read_bytes();cancel = threading.Event()

        class Remote:
            blocked = False
            def fetch(self, url, destination, token, progress, limit):
                assert url == 'https://api.beatsaver.com/playlists/id/1280881/download' and limit == 8*1024*1024
                while self.blocked and not token.wait(.02):pass
                check_cancel(token);destination.write_bytes(original)

        remote = Remote();client = PlaylistDownloadClient(root/'state',remote)
        value = client.search(dict(kind='file',source=str(source)),0,'',cancel)
        assert value['keys'] == ['1','2'] and not value['missing_keys']
        assert client.search(dict(kind='url',source='https://beatsaver.com/playlists/1280881'),0,'',cancel) == value
        cancel.set()
        try:client.search(dict(kind='file',source=str(source)),0,'',cancel)
        except DownloadError:pass
        else:raise AssertionError('cancel ignored')
        cancel.clear()
        for language, theme in (('zh','light'),('en','dark')):
            sandbox = root/language;sandbox.mkdir()
            manager = DownloadManager(sandbox/'qt-downloads.json',FakeClient(archive,song_hash(fixture)))
            window = DownloadWindow(language, theme, songs=[]);window.enable_downloads(sandbox,manager);window.show()
            queue = window.download_browser;panel = queue.importer
            wait(lambda:not panel.scanning)
            window.tabs.setCurrentIndex(window.pages.indexOf(window.import_page));panel.playlist.click();dialog = panel.playlist_dialog
            dialog.session.client = client
            dialog.load('file',str(source));wait(lambda:dialog.generation is None)
            assert dialog.start.isEnabled() and len(dialog.value['songs'])==3 and not manager.snapshot()
            with patch('quest_tool.qt_ui.playlist_download.QMessageBox.question',return_value=QMessageBox.StandardButton.No):
                dialog.start.click()
            assert not manager.snapshot()
            # Preview only; edited URLs and late cancelled results cannot enqueue.
            dialog.url.setText('https://beatsaver.com/playlists/1280881');remote.blocked=True;dialog.read.click()
            dialog.stop.click();remote.blocked=False;QTest.qWait(100)
            assert dialog.value is None and not dialog.start.isEnabled()
            panel.input.setPlainText('https://beatsaver.com/playlists/1280881');panel.links.click()
            wait(lambda:dialog.generation is None);assert dialog.eligible() and not manager.snapshot()
            for width,height in ((680,520),(900,650)):
                dialog.resize(width,height);QTest.qWait(75)
                for button in (dialog.read,dialog.file,dialog.website,dialog.stop,dialog.start):
                    assert button.width() >= button.sizeHint().width()
                assert dialog.songs.height()>50
            output = os.environ.get('QUEST_QT_SHOTS')
            if output:
                dest = Path(output);dest.mkdir(parents=True,exist_ok=True)
                assert dialog.grab().save(str(dest/f'playlist-download-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            with patch('quest_tool.qt_ui.playlist_download.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):
                dialog.start.click()
            wait(lambda:len(manager.snapshot())==2 and all(j['state'] in TERMINAL for j in manager.snapshot()) and not queue.busy)
            assert sorted(j['state'] for j in manager.snapshot()) == ['done','exists']
            assert window.pages.currentWidget() is window.tasks_page and not dialog.isVisible()
            # Whole means no silent truncation or partial missing-key downloads.
            panel.playlist.click()
            for songs in ([],[dict(songName='No ID')],[dict(key=f'{i:x}') for i in range(201)]):
                path=root/'bad.json';path.write_text(json.dumps(dict(songs=songs)),encoding='utf-8')
                dialog.load('file',str(path));wait(lambda:dialog.generation is None)
                assert not dialog.start.isEnabled()
            remote.blocked=True;dialog.load('url','https://beatsaver.com/playlists/1280881')
            window.close();remote.blocked=False;queue.wait_closed();app.processEvents()
            assert dialog.closed and not dialog.timer.isActive() and not errors
        assert source.read_bytes()==original
        assert hashlib.sha256(source.read_bytes()).digest()==hashlib.sha256(original).digest()
    print('PASS playlist download: URL guards, file/remote preview, cancellation, no automatic queue, whole-list limits, original extraction/duplicate skip, bilingual/scaling/close')


if __name__ == '__main__':
    if '--child' in sys.argv:
        child()
    else:
        for scale in ('1','1.25','1.5'):
            subprocess.run([sys.executable,'-B',__file__,'--child'],env=dict(os.environ,QT_SCALE_FACTOR=scale),check=True,timeout=120)
