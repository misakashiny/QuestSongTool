"""Absolute seek gestures, local full-song queue and shared player ownership."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import QPoint,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QComboBox
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import sample_songs
    from quest_tool.song_search import SearchSession
    from quest_tool.downloads import check_cancel
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    sys.excepthook=lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    def wait(predicate):
        end=time.monotonic()+8
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError(('timeout',errors))
    class Player:
        def __init__(self):self.request_id=0;self.active=False;self.start_offset=0;self.value=0;self.gain=1;self.starts=[]
        def stop(self):self.request_id+=1;self.active=False
        def set_volume(self,value):self.gain=value
        def position(self):return self.value
        def is_playing(self):return self.active
        def play_file(self,path,*,seconds,start,request_id):
            if request_id!=self.request_id:return None
            self.active=True;self.start_offset=start;self.value=start;self.starts.append((path.parent.name,seconds,start));return path
    class Preview:
        def __init__(self,player):self.player=player
        def search(self,item,start,request_id,cancel):
            check_cancel(cancel)
            self.player.play_file(Path('online/song.ogg'),seconds=30-start,start=start,request_id=request_id)
            return dict(request_id=request_id,total=30,id=item['id'])
    with tempfile.TemporaryDirectory(prefix='player-transport-') as temporary:
        root=Path(temporary);library=root/'songs';library.mkdir()
        for number in range(2):
            folder=library/f'song-{number}';folder.mkdir();(folder/'song.wav').write_bytes(b'fixture')
            (folder/'Info.dat').write_text(json.dumps(dict(_songName=f'Track {number}',_songAuthorName='Artist',_songDuration=90)),encoding='utf-8')
        with patch('quest_tool.audio.probe_duration',return_value=90),patch('socket.socket',side_effect=AssertionError('network forbidden')),patch('subprocess.Popen',side_effect=AssertionError('process forbidden')):
            for language in ('zh','en'):
                window=LibraryWindow(root/language,language);window.show();window.set_target(library);wait(lambda:window.local_ready)
                c=window.preview;player=Player();c.player=player
                assert not hasattr(c,'length') and c.play_seconds==0 and not c.page.findChildren(QComboBox)
                def index(folder):return next(window.proxy.index(i,0) for i in range(window.proxy.rowCount()) if window.proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder']==folder)
                def playing():return not c.pending and player.active
                item=index('song-0');window.table.setCurrentIndex(item);wait(lambda:not window.details.loading)
                assert not player.starts
                rect=window.table.visualRect(item);point=QPoint(min(rect.right()-5,rect.left()+120),rect.center().y())
                QTest.mouseClick(window.table.viewport(),Qt.MouseButton.LeftButton,pos=point)
                QTest.mouseDClick(window.table.viewport(),Qt.MouseButton.LeftButton,pos=point)
                wait(playing);assert player.starts[-1][0]=='song-0' and player.starts[-1][1:]==(0,0)
                window.view_mode.setCurrentIndex(window.view_mode.findData('covers'));QTest.qWait(50)
                second=index('song-1');window.grid.scrollTo(second);QTest.qWait(30)
                tile=window.grid.visualRect(second);tile_point=QPoint(tile.center().x(),tile.bottom()-12)
                QTest.mouseClick(window.grid.viewport(),Qt.MouseButton.LeftButton,pos=tile_point)
                QTest.mouseDClick(window.grid.viewport(),Qt.MouseButton.LeftButton,pos=tile_point)
                wait(lambda:playing() and c.track_key[2]=='song-1')
                window.view_mode.setCurrentIndex(window.view_mode.findData('list'));c.play_index('local',index('song-0'));wait(playing)
                # Repeated 75% seeks always target the same absolute time.
                for _ in range(3):
                    before=len(player.starts);c.seek.setSliderDown(True);c.seek.setSliderPosition(750);c.render()
                    assert len(player.starts)==before and c.seek.value()==750
                    c.seek.setSliderDown(False);wait(playing);assert player.start_offset==67.5 and c.span()==(0,90)
                QTest.keyClick(c.seek,Qt.Key.Key_Home);wait(lambda:playing() and player.start_offset==0)
                # A physical mouse gesture commits once at release, even when polling renders mid-drag.
                before=len(player.starts);y=c.seek.height()//2
                QTest.mousePress(c.seek,Qt.MouseButton.LeftButton,pos=QPoint(round(c.seek.width()*.2),y))
                QTest.mouseMove(c.seek,QPoint(round(c.seek.width()*.8),y));c.render()
                assert len(player.starts)==before and c.seek.value()>750
                QTest.mouseRelease(c.seek,Qt.MouseButton.LeftButton,pos=QPoint(round(c.seek.width()*.8),y))
                wait(playing);assert len(player.starts)==before+1 and abs(player.start_offset-72)<2
                # Clicking the trough is absolute, not a page-step jump.
                QTest.mouseClick(c.seek,Qt.MouseButton.LeftButton,pos=QPoint(round(c.seek.width()*.6),c.seek.height()//2))
                wait(playing);assert abs(player.start_offset-54)<2,(player.start_offset,c.seek.width())
                c.play.click();assert c.paused and not player.active
                c.seek.setSliderDown(True);c.seek.setSliderPosition(250);c.seek.setSliderDown(False)
                assert c.position==22.5 and c.paused
                c.play.click();wait(playing);assert player.start_offset==22.5
                # Detail selection does not change the playing song.
                window.table.setCurrentIndex(index('song-1'));QTest.qWait(30);assert c.track_key[2]=='song-0' and player.active
                c.next_button.click();wait(playing);assert c.track_key[2]=='song-1'
                c.previous.click();wait(playing);assert c.track_key[2]=='song-0'
                c.shuffle.setChecked(True);c.next_button.click();wait(playing);assert c.track_key[2]=='song-1'
                c.repeat.setChecked(True);count=len(player.starts);player.active=False;c.poll();wait(playing)
                assert len(player.starts)==count+1 and player.start_offset==0 and c.track_key[2]=='song-1'
                c.repeat.setChecked(False);c.shuffle.setChecked(False);player.active=False;c.poll()
                assert c.ended and not c.paused and c.position==90
                c.toggle();wait(playing)
                # The online player takes the same engine; local playback cannot
                # advance or stop that new owner in its next poll.
                online=DownloadWindow(language=language,songs=tuple(replace(s,preview_url='https://cdn.beatsaver.com/fixture') for s in sample_songs()))
                online.enable_preview(SearchSession(Preview(player)),player);online.show()
                p=online.preview_browser;p.play_song(online.song_model.visible_songs()[0]);wait(lambda:not p.pending and player.active)
                count=len(player.starts);c.poll();assert c.request_id is None and player.active and len(player.starts)==count
                for _ in range(2):
                    online.seek.setSliderDown(True);online.seek.setSliderPosition(500);online.seek.setSliderDown(False)
                    wait(lambda:not p.pending and player.start_offset==15)
                online.play.click();assert p.paused and not player.active
                online.play.click();wait(lambda:not p.pending and player.active);assert player.start_offset==15
                records=online.preview_records();p.play_song(records[-1]);wait(lambda:not p.pending)
                online.shuffle.setChecked(True);assert online.preview_previous.isEnabled() and online.preview_next.isEnabled()
                previous=p.song.key;online.preview_next.click();wait(lambda:not p.pending);assert p.song.key!=previous
                online.shuffle.setChecked(False)
                # A double click restarts an already playing song, rather than pausing it.
                p.play_song(p.song,restart=True);wait(lambda:not p.pending);assert player.start_offset==0 and not p.paused
                for width,height in ((1000,760),(1600,950)):
                    window.resize(width,max(640,height-100));online.resize(width,height);QTest.qWait(80)
                    for bar in (c.page,online.player):
                        for button in (bar.shuffle,bar.previous,bar.play,bar.next,bar.repeat):
                            point=button.mapTo(bar,QPoint());assert bar.rect().contains(point) and point.x()+button.width()<=bar.width()
                online.close();online.preview_browser.session.close();window.close();window.wait_closed()
                for thread in online.preview_browser.session.tasks._threads:thread.join()
                assert not errors,errors
    print('PASS full-song/double-click/absolute mouse-drag-keyboard seek/pause/queue/shuffle/repeat/ownership/compact players')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True);assert result.returncode==0
