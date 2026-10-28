"""Playlist navigation, guarded queue and lifecycle with isolated public-shaped fixtures."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]


def child():
    from PIL import Image
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from quest_tool.downloads import DownloadError, DownloadManager, TERMINAL, check_cancel
    from quest_tool.playlist import song_hash
    from quest_tool.qt_ui.cover_session import CoverSession
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.playlist_catalog import PlaylistCatalogClient
    from smoke_downloads import FakeClient, make_archive
    app = QApplication([]);errors = []
    def report(kind, value, tb):
        errors.append(repr(value));sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    def wait(predicate):
        end = time.monotonic()+15
        while not predicate() and time.monotonic()<end:QTest.qWait(20)
        assert predicate(), 'catalog timeout'
    art = io.BytesIO();Image.new('RGB',(256,256),(40,100,180)).save(art,format='PNG')
    class Media:
        def load(self, url, kind, cancel):
            check_cancel(cancel);return art.getvalue()
    def playlist(identity, total=2):
        return dict(playlistId=identity,name='精选歌单 / Curated playlist '+str(identity),description='歌单简介 / Playlist description',
                    owner={'name':'BeastSaber'},playlistImage=f'https://cdn.beatsaver.com/{identity}.jpg',
                    stats=dict(totalMaps=total,totalDuration=360,avgScore=.95),updatedAt='2026-10-25')
    def song(key):
        return dict(id=key,metadata=dict(songName='示例歌曲 / Example '+key,songAuthorName='Artist',levelAuthorName='Mapper',bpm=120,duration=180),
                    stats={'score':.95},versions=[dict(state='Published',hash='a'*40,downloadURL='https://cdn.beatsaver.com/a.zip',
                    coverURL='https://cdn.beatsaver.com/a.jpg',previewURL='https://cdn.beatsaver.com/a.mp3')])
    class Client(PlaylistCatalogClient):
        blocked = False
        override = None
        def fetch(self, url, destination, token, progress, limit):
            assert url.startswith('https://api.beatsaver.com/playlists/') and limit==8*1024*1024
            while self.blocked and not token.wait(.01):pass
            check_cancel(token)
            if self.override is not None:data=self.override
            elif '/search/' in url:
                data=dict(docs=[playlist(i) for i in range(1,21)],info=dict(pages=2,total=40))
            else:
                identity=int(url.split('/')[-2]);data=dict(playlist=playlist(identity),maps=[{'map':song('1')},{'map':song('2')}])
            destination.write_text(json.dumps(data),encoding='utf-8')
    with tempfile.TemporaryDirectory() as folder, patch('socket.socket',side_effect=AssertionError('network forbidden')):
        root=Path(folder).resolve();client=Client(root/'metadata');token=threading.Event()
        assert client.search(dict(kind='list',query='test'),0,'Curated',token)['has_next']
        client.override = dict(docs=[playlist(99),dict(playlistId=100,name='Legacy without stats',owner={})],info=dict(pages=1,total=2))
        value = client.search(dict(kind='list',query='legacy'),0,'Relevance',token)
        assert len(value['rows']) == 1 and value['rows'][0]['id'] == 99 and value['unavailable'] == 1
        client.override = None
        value=client.search(dict(kind='detail',id=1),0,'',token);assert value['complete'] and len(value['rows'])==2
        for data in (dict(playlist=playlist(2),maps=[]),dict(docs=[],info={'pages':True,'total':1})):
            client.override=data
            try:client.search(dict(kind='detail',id=1) if 'playlist' in data else dict(kind='list',query=''),0,'Curated',token)
            except DownloadError:pass
            else:raise AssertionError('invalid metadata accepted')
        client.override=dict(playlist=playlist(1,501),maps=[{'map':song('1')}]*500)
        value=client.search(dict(kind='detail',id=1),0,'',token);assert value['has_next'] and not value['complete']
        client.override=dict(playlist=playlist(1),maps=[{'map':song('1')},{'map':{}}])
        value=client.search(dict(kind='detail',id=1),0,'',token);assert value['rows'][1] is None and not value['complete']
        client.override=None
        archive=make_archive(root);fixture=root/'fixture';fixture.mkdir()
        with zipfile.ZipFile(archive) as zipped:zipped.extractall(fixture)
        for language,theme in (('zh','light'),('en','dark')):
            sandbox=root/language;sandbox.mkdir();manager=DownloadManager(sandbox/'qt-downloads.json',FakeClient(archive,song_hash(fixture)))
            window=DownloadWindow(language,theme,songs=[]);window.enable_downloads(sandbox,manager)
            browser=window.playlist_browser;browser.session.client=client;browser.covers.close();browser.covers=CoverSession(Media)
            played=[];window.preview_browser=SimpleNamespace(song=None,play_song=played.append,close=lambda:None,render=lambda:None)
            window.search.setText('preserved song draft');window.show();QTest.qWait(100)
            assert browser.list_result is None and not manager.snapshot()
            window.tabs.setCurrentIndex(browser.index);wait(lambda:browser.list_result is not None and browser.generation is None)
            assert browser.card_model.rowCount()==20 and not window.search.isVisible()
            browser.next.click();wait(lambda:browser.list_result['page']==1 and browser.generation is None)
            browser.cards.verticalScrollBar().setValue(100);scroll=browser.cards.verticalScrollBar().value()
            browser.open_detail(browser.card_model.index(0));wait(lambda:browser.detail_result is not None and browser.generation is None)
            assert browser.songs.rowCount()==2 and browser.download_all.isEnabled()
            assert browser.download_all.parentWidget() is not browser.download_all_top.parentWidget()
            assert browser.download_all.isVisible() and browser.download_all_top.isVisible()
            assert not browser.author_handle.text().startswith('@'), 'Do not invent an author account'
            assert browser.songs.rows[0].rating_text == '95.0%', 'Approval data must remain a percentage'
            assert browser.songs.rows[0].bpm==120 and browser.songs.rows[0].seconds==180
            wait(lambda: browser.songs.rows[0].cover_url in browser.cache.sources)
            window.preview_browser.song = browser.songs.rows[0];window.refresh_preview_identity()
            assert window.preview_cover.pixmap() is not None and not window.preview_cover.pixmap().isNull(), 'Player must reuse the playlist artwork cache'
            window.preview_browser.song = None
            browser.table.selectRow(0);browser.preview.click();assert len(played)==1 and played[0].key=='1'
            play_cell = browser.table.visualRect(browser.songs.index(0,9))
            browser.table.scrollTo(browser.songs.index(0,9));QTest.qWait(20)
            play_cell = browser.table.visualRect(browser.songs.index(0,9))
            QTest.mouseClick(browser.table.viewport(),Qt.MouseButton.LeftButton,
                             pos=browser.table.itemDelegate().play_rect(play_cell).center())
            assert len(played)==2 and played[-1].key=='1'
            browser.songs.setData(browser.songs.index(0,0),Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
            assert browser.checked=={'1'}
            browser.table.horizontalScrollBar().setValue(0)
            header = browser.table.horizontalHeader()
            QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(header.sectionSize(0)//2,header.height()//2))
            assert browser.checked == {'1','2'}, 'Header checkbox must select all available rows'
            QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(header.sectionSize(0)//2,header.height()//2))
            assert not browser.checked
            browser.songs.setData(browser.songs.index(0,0),Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
            with patch.object(window.download_browser,'enqueue') as enqueue, patch('quest_tool.qt_ui.playlist_browser.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):
                browser.download_selected.click();enqueue.assert_called_once_with(['1'])
            browser.detail_result['complete']=False;browser.render();assert not browser.download_all.isEnabled()
            browser.detail_result['complete']=True;browser.render()
            with patch('quest_tool.qt_ui.playlist_browser.QMessageBox.question',return_value=QMessageBox.StandardButton.No):browser.download_all.click()
            assert not manager.snapshot()
            for width,height in ((1000,700),(1280,800)):
                window.resize(width,height);QTest.qWait(100)
                assert browser.table.height()>90
                for button in (browser.back,browser.check_page,browser.preview,browser.download_selected,browser.download_all):
                    assert button.width()>=button.sizeHint().width(), (language,button.text(),button.width(),button.sizeHint().width())
            output=os.environ.get('QUEST_QT_SHOTS')
            if output:
                dest=Path(output);dest.mkdir(parents=True,exist_ok=True)
                window.grab().save(str(dest/f'catalog-detail-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            browser.back.click();assert browser.list_result['page']==1 and browser.cards.verticalScrollBar().value()==scroll
            browser.open_detail(browser.card_model.index(0));wait(lambda:browser.detail_result is not None and browser.generation is None)
            assert browser.checked=={'1'},'Returning to the same playlist lost selected songs'
            browser.checked.add('stale-key');browser.reload();wait(lambda:browser.generation is None)
            assert browser.checked=={'1'},'Complete refreshed playlist retained a removed song'
            browser.back.click()
            if output:window.grab().save(str(dest/f'catalog-list-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            client.blocked=True;browser.refresh.click();browser.stop.click();client.blocked=False;QTest.qWait(100)
            assert browser.generation is None and browser.state=='cancelled'
            client.blocked=True;browser.query.setText('cancelled new query');browser.search.click();browser.stop.click();client.blocked=False;QTest.qWait(100)
            assert browser.list_query=='' and browser.list_result['page']==1
            browser.open_detail(browser.card_model.index(0));wait(lambda:browser.detail_result is not None and browser.generation is None)
            with patch('quest_tool.qt_ui.playlist_browser.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):browser.download_all.click()
            wait(lambda:len(manager.snapshot())==2 and all(j['state'] in TERMINAL for j in manager.snapshot()) and not window.download_browser.busy)
            assert sorted(j['state'] for j in manager.snapshot())==['done','exists']
            assert window.search.text()=='preserved song draft'
            assert not window.search_host.isVisible() and not window.footer.isVisible(), 'Task page should show its own controls'
            window.tabs.setCurrentIndex(0);QTest.qWait(20)
            assert window.search.isVisible() and window.selection.isVisible() and window.download.isVisible()
            window.tabs.setCurrentIndex(browser.index);client.blocked=True;browser.reload();window.close();client.blocked=False
            window.download_browser.wait_closed();app.processEvents()
            assert browser.closed and not browser.timer.isActive() and not errors
    print('PASS catalog: paging, validated metadata, complete-list guards, cards/detail/back, selection, preview routing, original queue, cancellation, bilingual layout and close')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            subprocess.run([sys.executable,'-B',__file__,'--child'],env=dict(os.environ,QT_SCALE_FACTOR=scale),check=True,timeout=120)
