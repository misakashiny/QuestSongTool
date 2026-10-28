"""Dynamic playlist statistics, page completeness and safe queue/snapshot routes."""
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

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))


def playlist(identity=101, dynamic=True, count=None):
    value=dict(playlistId=identity,name='Dynamic selection' if dynamic else 'Fixed selection',
               type='Search' if dynamic else 'Public',owner={'name':'Mapper'},
               playlistImage=f'https://cdn.beatsaver.com/{identity}.jpg',
               config={'type':'Search','mapCount':1000,'searchParams':{'tags':{'true':['anime']}}},
               stats=None if count is None else dict(totalMaps=count,totalDuration=360,avgScore=.95),updatedAt='2026-10-28')
    return value


def song(key):
    return dict(id=key,metadata=dict(songName='Song '+key,songAuthorName='Artist',levelAuthorName='Mapper',bpm=120,duration=180),
                stats={'score':.95},versions=[dict(state='Published',hash='a'*40,downloadURL='https://cdn.beatsaver.com/a.zip',
                coverURL='https://cdn.beatsaver.com/a.jpg',previewURL='https://cdn.beatsaver.com/a.mp3')])


def child():
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QMessageBox
    from quest_tool.downloads import DownloadError,check_cancel
    from quest_tool.messages import tr
    from quest_tool.qt_ui.playlist_catalog import PlaylistCatalogClient,playlist_record,DETAIL_PAGE_SIZE
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.cover_session import CoverSession
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    sys.excepthook=lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    def wait(predicate):
        end=time.monotonic()+10
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError(('dynamic timeout',errors))
    class Client(PlaylistCatalogClient):
        def __init__(self,root):
            super().__init__(root);self.calls=[];self.blocked=False
            self.listing=[playlist(),playlist(102,False,2)]
            self.pages={0:[{'map':song('1')},{'map':song('2')}]}
            self.override=None
        def fetch(self,url,destination,token,progress,limit):
            assert url.startswith('https://api.beatsaver.com/playlists/') and limit==8*1024*1024
            self.calls.append(url)
            while self.blocked and not token.wait(.01):pass
            check_cancel(token)
            if self.override is not None:value=self.override
            elif '/search/' in url:value=dict(docs=self.listing,info=dict(pages=1,total=len(self.listing)))
            elif url.endswith('/download'):
                value=dict(playlistTitle='Dynamic snapshot',playlistAuthor='Mapper',image='',
                           songs=[dict(songName='Song '+key,hash='a'*40,key=key) for key in ('1','2')],
                           customData={'syncURL':'https://evil.test/never-follow'})
            else:
                page=int(url.rsplit('/',1)[-1]);value=dict(playlist=playlist(),maps=self.pages.get(page,[]))
            destination.write_text(json.dumps(value),encoding='utf-8')
    picture=io.BytesIO();Image.new('RGB',(64,64),(50,100,190)).save(picture,'PNG')
    class Media:
        def load(self,url,kind,cancel):check_cancel(cancel);return picture.getvalue()
    with tempfile.TemporaryDirectory(prefix='dynamic-playlists-') as directory,patch('socket.socket',side_effect=AssertionError('network forbidden')):
        root=Path(directory);client=Client(root/'service');token=threading.Event()
        # Null/missing statistics are valid for Search, never fabricated as 0/1000.
        for value in (playlist(),dict(playlist(),stats={})):
            parsed=playlist_record(value);assert parsed['dynamic'] and parsed['total'] is None and parsed['rating'] is None
        missing=playlist();missing.pop('stats');assert playlist_record(missing)['total'] is None
        assert playlist_record(playlist(count=0))['total']==0
        for value in (dict(playlist(),playlistId=True),dict(playlist(),playlistId='101'),dict(playlist(),stats=[]),
                      dict(playlist(),stats=False),dict(playlist(),owner=['bad']),
                      dict(playlist(),stats={'totalMaps':True}),dict(playlist(),stats={'totalMaps':'85'}),
                      dict(playlist(),stats={'totalMaps':-1}),dict(playlist(),stats={'totalMaps':1000001}),playlist(dynamic=False)):
            try:playlist_record(value)
            except DownloadError:pass
            else:raise AssertionError(('invalid accepted',value))
        bad_cover=playlist_record(dict(playlist(),playlistImage='https://evil.test/cover.png'));assert bad_cover['coverURL']==''
        client.listing=[playlist(i,False,2) for i in range(1,14)]+[playlist(i) for i in range(14,21)]
        result=client.search(dict(kind='list',query=''),0,'Latest',token)
        assert len(result['rows'])==20 and result['unavailable']==0 and sum(r['dynamic'] for r in result['rows'])==7
        client.override=dict(docs=[playlist(),dict(playlist(),playlistId=False)],info=dict(pages=1,total=2))
        result=client.search(dict(kind='list',query=''),0,'Latest',token);assert len(result['rows'])==1 and result['unavailable']==1
        client.override=None
        value=client.search(dict(kind='detail',id=101),0,'',token)
        assert value['playlist']['total']==2 and value['complete'] and not value['has_next']
        client.pages[0]=[];value=client.search(dict(kind='detail',id=101),0,'',token)
        assert value['playlist']['total']==0 and value['complete'] and not value['has_next']
        client.pages[0]=[{'map':song(f'{n+1:x}')} for n in range(DETAIL_PAGE_SIZE)]
        value=client.search(dict(kind='detail',id=101),0,'',token)
        assert value['playlist']['total'] is None and value['has_next'] and not value['complete']
        client.pages[1]=[{'map':song('abc')}]
        value=client.search(dict(kind='detail',id=101),1,'',token)
        assert value['playlist']['total'] is None and not value['has_next'] and not value['complete']
        client.pages[0]=[{'map':song('1')},{'map':{'id':'2'}}]
        value=client.search(dict(kind='detail',id=101),0,'',token)
        assert value['playlist']['total']==2 and value['rows'][1] is None and not value['complete']
        client.override=dict(playlist=playlist(999),maps=[])
        try:client.search(dict(kind='detail',id=101),0,'',token)
        except DownloadError:pass
        else:raise AssertionError('wrong playlist identity accepted')
        client.override=None
        token.set()
        try:client.search(dict(kind='detail',id=101),0,'',token)
        except DownloadError:pass
        else:raise AssertionError('cancel ignored')
        for language,mode in (('zh','light'),('en','dark')):
            sandbox=root/language;window=DownloadWindow(language,mode,songs=());window.enable_downloads(sandbox)
            browser=window.playlist_browser;client=Client(sandbox/'api');browser.session.client=client
            browser.covers.close();browser.covers=CoverSession(Media);browser.file_session.client.reader.client=client
            window.show();window.tabs.setCurrentIndex(browser.index);wait(lambda:browser.list_result is not None and browser.generation is None)
            assert browser.card_model.rowCount()==2 and browser.list_result['unavailable']==0
            text=browser.card_model.index(0).data(Qt.ItemDataRole.ToolTipRole)
            assert tr('qt.catalog.dynamic') in text and tr('qt.catalog.count_pending') in text and '1000' not in text and 'None' not in text
            for width in (1000,1600):window.resize(width,850);QTest.qWait(60)
            shots=os.environ.get('QUEST_QT_SHOTS')
            if shots:window.grab().save(str(Path(shots)/f'dynamic-list-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            browser.open_detail(browser.card_model.index(0));wait(lambda:browser.detail_result is not None and browser.generation is None)
            assert browser.badge.isVisible() and browser.badge.text()==tr('qt.catalog.dynamic')
            assert browser.meta_values[0].text()==tr('qt.catalog.songs_count',count=2)
            assert browser.all_keys()==['1','2'] and browser.download_all.isEnabled() and not browser.next.isEnabled()
            with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes),patch.object(window.download_browser,'enqueue') as enqueue,patch.object(browser,'save_playlist') as save:
                browser.enqueue(True);enqueue.assert_called_once_with(['1','2']);save.assert_called_once()
            assert not window.download_browser.manager.snapshot()
            browser.save_button.click();wait(lambda:browser.file_generation is None)
            assert browser.state=='saved';path=Path(browser.save_button.toolTip());saved=json.loads(path.read_text('utf-8'))
            assert [r['key'] for r in saved['songs']]==['1','2'] and set(saved)=={'playlistTitle','playlistAuthor','songs','image'}
            assert all('evil.test' not in url for url in client.calls)
            browser.mark_page();assert browser.checked=={'1','2'}
            browser.back.click();browser.open_detail(browser.card_model.index(0));wait(lambda:browser.generation is None)
            assert browser.checked=={'1','2'}
            client.pages[0]=[{'map':song('1')}];browser.reload();wait(lambda:browser.generation is None)
            assert browser.checked=={'1'} and browser.all_keys()==['1']
            if shots:window.grab().save(str(Path(shots)/f'dynamic-detail-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            # A complete dynamic list still honors the existing 200-ID batch cap.
            client.pages[0]=[{'map':song(f'{n+1:x}')} for n in range(200)];browser.reload();wait(lambda:browser.generation is None)
            assert browser.detail_result['complete'] and len(browser.all_keys())==200 and browser.download_all.isEnabled()
            with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes),patch.object(window.download_browser,'enqueue') as enqueue,patch.object(browser,'save_playlist'):
                browser.enqueue(True);assert len(enqueue.call_args.args[0])==200
            client.pages[0].append({'map':song('c9')});browser.reload();wait(lambda:browser.generation is None)
            assert browser.detail_result['complete'] and browser.detail_result['playlist']['total']==201 and not browser.download_all.isEnabled()
            checked=set(browser.checked);browser.mark_page();assert browser.checked==checked and browser.state=='limit'
            with patch.object(window.download_browser,'enqueue') as enqueue:browser.enqueue(True);enqueue.assert_not_called()
            client.pages[0]=[{'map':song(f'{n+1:x}')} for n in range(500)];client.pages[1]=[{'map':song('abc')}]
            browser.reload();wait(lambda:browser.generation is None)
            assert not browser.download_all.isEnabled() and browser.next.isEnabled()
            assert browser.meta_values[0].text()==tr('qt.catalog.count_pending') and 'None' not in browser.status.text()
            with patch.object(window.download_browser,'enqueue') as enqueue:browser.enqueue(True);enqueue.assert_not_called()
            browser.next.click();wait(lambda:browser.generation is None)
            assert browser.detail_result['page']==1 and not browser.next.isEnabled() and not browser.download_all.isEnabled()
            assert browser.previous.isEnabled() and browser.checked=={'1'}
            client.blocked=True;browser.reload();browser.stop.click();client.blocked=False
            assert browser.generation is None and browser.state=='cancelled'
            window.close();window.download_browser.wait_closed();browser.wait_closed();window.logs.wait_closed();QTest.qWait(20)
            assert not errors,errors
    print('PASS dynamic playlists: 20/20 cards; unknown/zero/real counts; paging/completeness; strict identities; queue guards; snapshot save; selections; cancellation; bilingual rendering')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True);assert result.returncode==0
