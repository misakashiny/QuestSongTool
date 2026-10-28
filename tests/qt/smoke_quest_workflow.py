"""Quest hash-only playlists, shared cached playback, chrome and guarded drag; fakes only."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import wave
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests/qt')]


def service():
    from quest_tool.qt_ui import adb_playback as module
    from quest_tool.qt_ui.playlist_enrichment import PlaylistMetadataClient
    from quest_tool.downloads import DownloadError
    from smoke_usability import device
    selected=device();cancel=threading.Event()
    info=json.dumps({'_songFilename':'song.egg','_difficultyBeatmapSets':[{'_difficultyBeatmaps':[{'_beatmapFilename':'Expert.dat'}]}]}).encode()
    item=dict(folder="中文 ' $(no);",info_hash=hashlib.sha256(info).hexdigest())
    class Provider:
        changed=False;frame=b'QSV:5\naudio'+module.END
        def _serial(self,v):return v
        def _contents(self,*_):return [(item['folder'],b'changed' if self.changed else info)]
        def _run(self,bridge,args,token,deadline,**kwargs):
            assert args[0]=='exec-out' and 'head -c 67108865' in args[1]
            assert 'readlink -f' in args[1] and 'stat -c' in args[1]
            self.changed=self.swap;return self.frame
        swap=False
    p=Provider()
    with patch.object(module,'_binding',return_value=(object(),selected['root'])):
        data,name=module.read_audio(p,selected,item,cancel);assert data==b'audio' and name=='song.egg'
        for frame,swap in ((b'QSV:5\nwrong'+module.END+b'extra',False),(b'QSV:5\naudio'+module.END,True)):
            p.changed=False;p.frame=frame;p.swap=swap
            try:module.read_audio(p,selected,item,cancel)
            except DownloadError:pass
            else:raise AssertionError('changed/truncated audio accepted')
    code=module.index_command(selected['root'],item,info)
    assert 'sha1sum' in code and 'sha256sum' in code and 'readlink -f' in code
    assert 'total=$((total+n))' in code and 'rm ' not in code and 'push' not in code
    # Long Windows commands previously lost whole batches. Exercise the actual
    # chunker with long file names and assert CreateProcess's quoted size.
    import re
    import subprocess
    long_info=json.dumps({'_difficultyBeatmapSets':[{'_difficultyBeatmaps':[
        {'_beatmapFilename':'chart-'+str(n)+'x'*160+'.dat'} for n in range(5)]}]}).encode()
    long_items=[dict(folder='long-folder-'+str(n),info_hash=hashlib.sha256(long_info).hexdigest()) for n in range(12)]
    class IndexProvider:
        calls=0
        def _serial(self,v):return v
        def _contents(self,*_):return [(v['folder'],long_info) for v in long_items]
        def _run(self,bridge,args,*a,**kw):
            self.calls+=1
            assert len(subprocess.list2cmdline(['adb','-s','SIM','shell',args[-1]]))<=30000
            ids=re.findall(r'printf "IDX:([0-3]):',args[-1])
            return '\n'.join('IDX:'+n+':100:'+str(self.calls*4+int(n)).zfill(40) for n in ids)
    index_provider=IndexProvider()
    with patch.object(module,'_binding',return_value=(object(),selected['root'])):
        indexed=module.read_playlist_index(index_provider,selected,long_items,cancel)
        assert len(indexed['matches'])==12 and indexed['unknown']==0 and index_provider.calls>=3
    for name in ('../escape.dat','/absolute.dat','symlink/../../escape.dat'):
        bad=json.dumps({'_difficultyBeatmapSets':[{'_difficultyBeatmaps':[{'_beatmapFilename':name}]}]}).encode()
        try:module.index_command(selected['root'],item,bad)
        except DownloadError:pass
        else:raise AssertionError('unsafe map accepted')
    requested='1'*40;other='2'*40
    metadata=dict(id='abc',metadata={'songName':'Hash-only song','songAuthorName':'Artist'},versions=[dict(state='Published',hash=requested,downloadURL='https://cdn.beatsaver.com/map.zip',coverURL='https://cdn.beatsaver.com/cover.jpg')])
    client=PlaylistMetadataClient()
    with patch.object(client,'fetch',side_effect=lambda url,path,*a,**kw:path.write_text(json.dumps({requested:metadata}),encoding='utf-8')):
        result=client.search(dict(hashes=[requested,other]),0,'',cancel)
        assert result[requested]['title']=='Hash-only song' and other not in result
        metadata['versions'][0]['hash']=other
        assert not client.search(dict(hashes=[requested]),0,'',cancel)
    print('PASS service: audio frames/change detection, guarded remote checksum, exact-version metadata')


def ui():
    from PIL import Image
    from PySide6.QtCore import Qt, QModelIndex, QMimeData, QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool import audio
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.playlist_store import normalized
    from quest_tool.qt_ui.cover_session import CoverSession
    from quest_tool.qt_ui.library_drag import MIME
    from quest_tool.qt_ui.wireless_form import WirelessForm
    from smoke_usability import device
    from smoke_verification import song
    app=QApplication([]);errors=[]
    sys.excepthook=lambda kind,value,tb:(errors.append(''.join(__import__('traceback').format_exception(kind,value,tb))),sys.__excepthook__(kind,value,tb))
    def wait(predicate):
        end=time.monotonic()+12
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError(('timeout',errors))
    image=io.BytesIO();Image.new('RGB',(64,64),'#3366ff').save(image,format='PNG');png=image.getvalue()
    wav=io.BytesIO()
    with wave.open(wav,'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000);out.writeframes(b'\x00\x01'*8000*4)
    class Player:
        request_id=0;active=False;offset=0;file=None
        def stop(self):self.request_id+=1;self.active=False
        def play_file(self,path,seconds,start,request_id):
            if request_id!=self.request_id:return False
            self.file=Path(path);assert self.file.read_bytes()==wav.getvalue();self.active=True;self.offset=start;return True
        def is_playing(self):return self.active
        def position(self):return self.offset
        def set_volume(self,v):self.level=v
    player=Player();digest='1'*40;selected=device()
    rows=[dict(folder='arbitrary-quest-folder',title='Installed Quest song',author='Artist',duration=4,error='',info_hash='a'*64)]
    class Provider:
        reads=0;indexes=0
        def list_devices(self,_):return [selected]
        def read_metadata(self,*_):return dict(device=selected,connected=True,rows=rows)
        read_library=read_metadata
        def read_audio(self,device,item,cancel):self.reads+=1;return wav.getvalue(),'song.wav'
        def read_playlist_index(self,device,items,cancel):
            self.indexes+=1;return dict(identity=selected['identity'],matches={digest:rows[0]['folder']},unknown=0,bytes=20)
    provider=Provider()
    class Media:
        disk_cache=None
        def load(self,*_):return png
    def lookup(_self,request,*_):
        return {v:dict(id='abc',hash=v,title='Resolved hash title',artist='Artist',mapper='Mapper',bpm=130,duration=4,coverURL='https://cdn.beatsaver.com/cover.jpg') for v in request['hashes']}
    with tempfile.TemporaryDirectory() as tmp,patch('quest_tool.qt_ui.playlist_enrichment.PlaylistMetadataClient.search',lookup),patch('quest_tool.qt_ui.playlist_enrichment.CoverSession',lambda:CoverSession(lambda:Media())),patch.object(audio,'player',return_value=player),patch('socket.socket',side_effect=AssertionError('network forbidden')),patch('subprocess.Popen',side_effect=AssertionError('process forbidden')),ExitStack() as cleanup:
        root=Path(tmp);local=root/'songs';local.mkdir();song(local/'local-song')
        w=LibraryWindow(root/'state',device_provider=provider);cleanup.callback(lambda:(w.close(),w.wait_closed()) if not w.closed else None)
        w.show();w.set_target(local);wait(lambda:w.local_ready)
        browser=w.device_browser;browser.discover();wait(lambda:not browser.busy);browser.choices.setCurrentIndex(1)
        c=w.playlists;c.show();wait(lambda:c.store_generation is None)
        record=normalized(json.dumps(dict(playlistTitle='Quest 哈希歌单',songs=[dict(hash=digest),dict(hash='2'*40)])))
        c.device.client.search=lambda *args:dict(action='list',rows=[dict(record=record,name='fixture.bplist',sha256='e'*64)],failed=0)
        c.dialog.sources.setCurrentIndex(1);c.dialog.read_quest.click()
        try:wait(lambda:w.quest_ready and c.enrichment.installed(digest) and c.dialog.model.index(0,0).data(Qt.ItemDataRole.DecorationRole))
        except AssertionError:
            state=(w.quest_ready,c.source,c.device_generation,len(c.records),c.enrichment.index_context,c.enrichment.quest_matches,c.enrichment.metadata,tuple(errors))
            w.close();w.wait_closed();raise AssertionError(state)
        d=c.dialog;assert d.model.rowCount()==2 and 'Installed Quest song' in d.model.index(0,0).data()
        assert c.quest_records[0]['record']['songs'][0]['songName']=='' # Display enrichment never rewrites the bplist.
        assert d.windowFlags() & Qt.WindowType.FramelessWindowHint
        d.table.selectRow(0);wait(lambda:d.play.isEnabled());d.play.click()
        wait(lambda:player.active and not w.preview.pending)
        assert d.isVisible() and w.preview.track_key==('quest',selected['identity'],rows[0]['folder'])
        assert provider.reads==1 and d.player.play.isEnabled() and d.player.title.text()
        d.player.seek.seekRequested.emit(500);wait(lambda:not w.preview.pending and player.offset==2)
        assert provider.reads==1 and d.player.total.text()=='0:04'
        d.player.play.click();assert w.preview.paused and not player.active
        d.player.play.click();wait(lambda:player.active and not w.preview.pending);assert provider.reads==1
        d.table.selectRow(1);assert not d.play.isEnabled() and d.download.isEnabled()
        assert c.download_text()=='https://beatsaver.com/maps/abc'
        for size in ((700,520),(1120,760)):
            d.resize(*size);QTest.qWait(120)
            for widget in (d.chrome.controls,d.table,d.player.play,d.player.seek):
                point=widget.mapTo(d,QPoint());assert d.rect().contains(point) and point.x()+widget.width()<=d.width()
        if os.environ.get('QUEST_QT_SHOTS'):d.grab().save(str(Path(os.environ['QUEST_QT_SHOTS'])/'playlist-manager.png'))
        d.reject();c.show();wait(lambda:c.store_generation is None)
        assert c.dialog is d and not d.chrome.closed
        # Copy-only, in-app drag; exact names and stale/forged payload rejection.
        mime=w.model.mimeData([w.model.index(0,0)]);assert mime.hasFormat(MIME)
        assert w.quest_model.canDropMimeData(mime,Qt.DropAction.CopyAction,-1,-1,QModelIndex())
        assert not w.model.canDropMimeData(mime,Qt.DropAction.CopyAction,-1,-1,QModelIndex())
        calls=[]
        def opened(origin,direction,**kwargs):calls.append((origin,direction,kwargs));w.transfers.dialog=SimpleNamespace()
        with patch.object(w.transfers,'open',opened):
            assert w.quest_model.dropMimeData(mime,Qt.DropAction.CopyAction,-1,-1,QModelIndex())
        assert calls==[('local','to_quest',dict(folders=('local-song',)))]
        w.transfers.dialog=None
        assert not w.quest_model.canDropMimeData(mime,Qt.DropAction.CopyAction,-1,-1,QModelIndex())
        forged=QMimeData();forged.setData(MIME,b'forged');assert not w.quest_model.canDropMimeData(forged,Qt.DropAction.CopyAction,-1,-1,QModelIndex())
        mime=w.model.mimeData([w.model.index(0,0)]);browser.read_epoch+=1
        assert not w.quest_model.canDropMimeData(mime,Qt.DropAction.CopyAction,-1,-1,QModelIndex())
        wait(lambda:w.preview.track_key is None) # Snapshot change invalidates cached playback.
        # A wireless result must survive the old USB selection while discovery
        # hands off; returning on the same verified transport does not rescan.
        owner=w.wireless;recovery=owner.recovery
        endpoint='192.168.1.2:5555';preferred=json.dumps([endpoint,'HW'],separators=(',',':'))
        recovery.active=dict(serial=endpoint,hardware='HW',host='192.168.1.2',port='5555',model='Quest_3')
        recovery.state='online';browser.preferred_transport=preferred;browser.busy=True
        recovery.browser_rendered();assert recovery.active and recovery.active['serial']==endpoint
        from quest_tool.qt_ui.device_protocol import descriptor
        wifi=dict(selected);wifi.pop('identity',None);wifi['device_id']=preferred
        browser.selected=descriptor(wifi);browser.busy=False;browser.connection_open=True
        owner.result=dict(endpoint=endpoint,serial=endpoint,hardware='HW')
        with patch.object(browser,'discover',side_effect=AssertionError('verified wireless transport must survive return')):
            owner.release()
        assert not browser.connection_open and browser.selected['device_id']==preferred and recovery.active
        browser.selected=selected;recovery.active=None
        d.reject();form=WirelessForm(w);form.show();QTest.qWait(120)
        assert form.windowFlags() & Qt.WindowType.FramelessWindowHint
        if os.environ.get('QUEST_QT_SHOTS'):form.grab().save(str(Path(os.environ['QUEST_QT_SHOTS'])/'device-manager.png'))
        form.close();w.close();w.wait_closed();assert not errors
        assert not list((root/'state').glob('quest-audio-*'))
    print('PASS UI: hash-only Quest playlist, artwork, installed/uninstalled states, in-dialog playback/seek cache, chrome reuse, stale/forged drag rejection')


if __name__=='__main__':service();ui()
