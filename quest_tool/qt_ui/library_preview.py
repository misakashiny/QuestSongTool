"""Local full-song transport using the existing shared audio engine."""
from pathlib import Path
import logging
import random

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .. import audio
from ..downloads import DownloadError, check_cancel
from ..messages import tr
from ..song_search import SearchSession
from ..theme import get_palette
from .icons import line_icon
from .player_widgets import PlayerBar

logger=logging.getLogger('quest_tool.qt_preview')


class LocalPreviewClient:
    def __init__(self, player):self.player=player

    def search(self, request, _page, _order, cancel):
        root=Path(request['root']).resolve();folder=request['folder']
        if not folder or folder in ('.','..') or any(c in folder for c in '/\\'):raise DownloadError('local_audio')
        directory=(root/folder).resolve()
        if directory==root or not directory.is_relative_to(root):raise DownloadError('local_audio')
        check_cancel(cancel);path=audio._find_audio_file(directory)
        if path is None or not path.resolve().is_relative_to(directory):raise DownloadError('local_audio')
        total=audio.probe_duration(path) or 0;check_cancel(cancel)
        if not self.player.play_file(path,seconds=0,start=request['start'],request_id=request['request_id']):raise DownloadError('local_audio')
        check_cancel(cancel)
        return dict(key=request['key'],request_id=request['request_id'],total=total)


class LibraryPreview(QObject):
    rendered=Signal()
    def __init__(self, window):
        super().__init__(window);self.window=window
        self.player=None;self.session=None;self.request_id=None;self.generation=None
        self.pending=False;self.closed=False;self.error=False;self.total=0;self.paused=False;self.ended=False
        self.position=0;self.track_key=None;self.track_data=None;self.queue=[];self.history=[];self.art_key=None
        self.play_seconds=0;self.track_epoch=None
        self.page=PlayerBar();self.page.setObjectName('libraryPlayer')
        self.cover=self.page.cover;self.title=self.page.title;self.artist=self.page.artist
        self.play=self.page.play;self.seek=self.page.seek;self.time=self.page.elapsed;self.total_label=self.page.total
        self.volume=self.page.volume;self.volume_label=self.page.volume_icon
        self.previous=self.page.previous;self.next_button=self.page.next;self.shuffle=self.page.shuffle;self.repeat=self.page.repeat
        self.volume.setValue(window.preference_values.get('preview_volume',80))
        self.play.clicked.connect(self.toggle);self.seek.seekRequested.connect(self.commit_seek)
        self.previous.clicked.connect(lambda:self.adjacent(-1));self.next_button.clicked.connect(lambda:self.adjacent(1))
        self.shuffle.toggled.connect(self.render);self.repeat.toggled.connect(self.render)
        self.volume.valueChanged.connect(self.volume_changed);self.page.resized.connect(self.render)
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.poll)
        window.details.selectionChanged.connect(self.selection_changed)
        self.retranslate()

    def candidate(self):
        key=self.window.details.key
        return key if key and ((key[0]=='local' and self.window.local_ready and key[1]==str(self.window.target)) or (key[0]=='quest' and self.window.quest_ready and key[1]==self.window.quest_identity)) else None

    def valid(self):
        if not self.track_key:return False
        if self.track_key[0]=='local':return bool(self.window.local_ready and self.track_key[1]==str(self.window.target))
        w=self.window;selected=w.device_browser.selected
        return bool(w.quest_ready and selected and selected['identity']==w.quest_identity==self.track_key[1] and self.track_epoch==w.device_browser.read_epoch)

    def selectable(self):return bool(self.valid() or self.candidate())

    def owned(self):return self.player is not None and self.request_id is not None and self.player.request_id==self.request_id

    def selection_changed(self):
        if self.track_key and not self.valid():self.stop()
        else:self.render()

    def play_index(self, view, index):
        if self.closed or not index.isValid():return
        row=index.data(Qt.ItemDataRole.UserRole)
        origin=row.get('origin') if view=='missing' else view
        if origin not in ('local','quest') or row.get('error'):return
        if not (self.window.local_ready if origin=='local' else self.window.quest_ready):return
        if origin=='quest':
            browser=self.window.device_browser;provider=browser.session.client.provider if browser.session else None
            if (not browser.selected or browser.selected['identity']!=self.window.quest_identity
                    or not row.get('info_hash') or not callable(getattr(provider,'read_audio',None))):
                self.window.statusBar().showMessage(tr('qt.quest_audio.unavailable'));return
        self.window.details.select(view,index)
        proxy=self.window.missing_proxy if view=='missing' else self.window.proxy if origin=='local' else self.window.quest_proxy
        self.queue=[dict(item) for i in range(proxy.rowCount())
                    if (item:=proxy.index(i,0).data(Qt.ItemDataRole.UserRole)) and not item.get('error')
                    and (view!='missing' or item.get('origin')==origin)]
        if not any(item['folder']==row['folder'] for item in self.queue):self.queue.insert(0,dict(row))
        self.history=[];self.track_data=dict(row);self.track_key=(origin,str(self.window.target) if origin=='local' else self.window.quest_identity,row['folder'])
        self.track_epoch=self.window.device_browser.read_epoch
        self.total=row.get('duration',0);self.start(0)

    def toggle(self):
        if self.closed:return
        if self.request_id is not None:self.pause();return
        if self.valid():self.start(self.position if self.paused and not self.ended else 0);return
        candidate=self.candidate()
        if candidate is None:return
        proxy=self.window.proxy if candidate[0]=='local' else self.window.quest_proxy
        for i in range(proxy.rowCount()):
            index=proxy.index(i,0)
            if index.data(Qt.ItemDataRole.UserRole)['folder']==candidate[2]:self.play_index(candidate[0],index);return
        self.track_key=candidate;self.track_data=dict(self.window.details.data or {})
        self.queue=[dict(self.track_data,folder=candidate[2])];self.total=self.track_data.get('duration',0);self.start(0)

    def halt(self):
        if self.session:self.session.cancel()
        if self.owned():self.player.stop()
        self.request_id=None;self.generation=None;self.pending=False;self.timer.stop()

    def stop(self):
        self.halt();self.track_key=None;self.track_data=None;self.queue=[];self.history=[]
        self.position=0;self.total=0;self.paused=False;self.ended=False;self.error=False;self.render()

    def pause(self):
        self.position=self.player.position() if self.owned() and not self.pending else self.position
        self.halt();self.paused=True;self.ended=False
        logger.info('Library playback paused at %.2fs',self.position);self.render()

    def start(self, offset):
        if self.closed or not self.valid():return
        self.halt()
        if self.player is None:self.player=audio.player()
        if self.session is None:
            from .library_audio_cache import playback_client
            browser=self.window.device_browser
            provider=browser.session.client.provider if browser.session else None
            self.session=SearchSession(playback_client(LocalPreviewClient)(self.player,provider,self.window.sandbox))
        self.player.stop();self.request_id=self.player.request_id;self.player.set_volume(self.volume.value()/100)
        self.position=max(0,float(offset));self.pending=True;self.paused=False;self.ended=False;self.error=False
        key=self.track_key
        request=dict(root=key[1],folder=key[2],key=key,start=self.position,request_id=self.request_id)
        if key[0]=='quest':request.update(device=dict(self.window.device_browser.selected),epoch=self.track_epoch,item=dict(self.track_data))
        self.generation=self.session.request(request,0,'')
        logger.info('Library playback requested: %s at %.2fs; full song; generation=%s',key[0],self.position,self.generation)
        self.timer.start();self.render()

    def adjacent(self, delta, natural=False):
        if not self.valid() or not self.queue:return False
        current=self.track_key[2];index=next((i for i,row in enumerate(self.queue) if row['folder']==current),-1)
        if natural and self.repeat.isChecked():self.start(0);return True
        target=None
        if delta<0 and self.history:
            folder=self.history.pop();target=next((row for row in self.queue if row['folder']==folder),None)
        elif self.shuffle.isChecked():
            choices=[row for row in self.queue if row['folder']!=current]
            if choices:target=random.choice(choices)
        elif 0<=index+delta<len(self.queue):target=self.queue[index+delta]
        if target is None:return False
        if delta>0:self.history=(self.history+[current])[-100:]
        self.track_key=(self.track_key[0],self.track_key[1],target['folder']);self.track_data=dict(target)
        self.total=target.get('duration',0);self.start(0);return True

    def poll(self):
        if self.closed or self.request_id is None:return
        if not self.valid() or not self.owned():self.stop();return
        for message in self.session.tasks.drain():
            payload=message.payload
            if not isinstance(payload,dict) or payload.get('generation')!=self.generation:continue
            if message.kind=='search_error':
                logger.warning('Library playback failed: %s',payload.get('code','unexpected'))
                self.halt();self.error=True;self.render();return
            if message.kind!='search':continue
            result=payload['result']
            if result['request_id']!=self.request_id or tuple(result['key'])!=self.track_key:continue
            self.pending=False;self.total=result['total']
        if not self.pending and not self.player.is_playing():
            if self.adjacent(1,natural=True):return
            self.halt();self.position=self.total;self.ended=True
        self.render()

    def span(self):return (0,self.total)

    def commit_seek(self, value):
        if not self.valid() or self.pending or self.total<=0:return
        target=max(0,min(self.total,self.total*value/1000))
        logger.info('Library playback seek %.2f / %.2fs',target,self.total)
        if target>=self.total-.05:
            self.halt();self.position=self.total;self.ended=True;self.paused=False;self.render()
        elif self.paused or self.ended or self.request_id is None:
            self.position=target;self.paused=True;self.ended=False;self.render()
        else:self.start(target)

    def volume_changed(self, value):
        if self.owned():self.player.set_volume(value/100)

    @staticmethod
    def clock(seconds):return f'{int(max(0,seconds))//60}:{int(max(0,seconds))%60:02}'

    def render(self, *_args):
        active=self.request_id is not None;palette=get_palette(self.window.mode.currentData())
        self.play.setEnabled(not self.closed and self.selectable())
        self.play.setIcon(line_icon('pause' if active else 'play',palette.ACCENT_TEXT if self.play.isEnabled() else palette.TEXT_MUTED))
        action=tr('qt.player.pause' if active else 'qt.local_audio.play')
        self.play.setAccessibleName(action);self.play.setToolTip(action)
        row=self.track_data if self.track_key else self.window.details.data or {}
        text=tr('qt.local_audio.failed') if self.error else tr('qt.detail.loading') if self.pending else row.get('title') or tr('qt.local_audio.choose')
        self.title.setText(self.title.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,max(1,self.title.width())))
        self.title.setToolTip(text);self.title.setAccessibleName(text)
        artist=row.get('author','');self.artist.setText(self.artist.fontMetrics().elidedText(artist,Qt.TextElideMode.ElideRight,max(1,self.title.width())));self.artist.setToolTip(artist)
        image=None
        if self.track_key:
            cache=self.window.covers.cache if self.track_key[0]=='local' else self.window.quest_media.cache
            image=cache.image(self.track_key[2],self.cover.size(),self.cover.devicePixelRatioF())
            if image is None and self.track_key[0]=='quest' and hasattr(self.window,'playlists'):
                image=self.window.playlists.enrichment.artwork(self.track_key[2],self.cover.size(),self.cover.devicePixelRatioF())
        else:
            detail=self.window.details.cover
            if detail.ready:image=detail.cache.image('detail',self.cover.size(),self.cover.devicePixelRatioF())
        art=(self.track_key or self.window.details.key,image.cacheKey() if image else None)
        if art!=self.art_key:
            self.art_key=art;self.cover.clear()
            if image:self.cover.setPixmap(image)
        position=self.player.position() if self.owned() and not self.pending else self.position
        total=self.total if self.track_key else row.get('duration',0)
        self.seek.setEnabled(not self.closed and self.valid() and self.total>0 and not self.pending)
        if not self.seek.user_seeking():self.seek.setValue(round(1000*max(0,min(1,position/self.total))) if self.total else 0)
        self.time.setText(self.clock(position));self.total_label.setText(self.clock(total))
        index=next((i for i,item in enumerate(self.queue) if self.track_key and item['folder']==self.track_key[2]),-1)
        multiple=len(self.queue)>1;random_mode=self.shuffle.isChecked()
        self.previous.setEnabled(not self.closed and self.valid() and (bool(self.history) or (multiple and random_mode) or index>0))
        self.next_button.setEnabled(not self.closed and self.valid() and multiple and (random_mode or 0<=index<len(self.queue)-1))
        self.shuffle.setIcon(line_icon('shuffle',palette.ACCENT if random_mode else palette.TEXT_PRIMARY))
        self.repeat.setIcon(line_icon('repeat',palette.ACCENT if self.repeat.isChecked() else palette.TEXT_PRIMARY))
        self.volume_label.setPixmap(line_icon('volume',palette.TEXT_PRIMARY).pixmap(20,20))
        self.rendered.emit()

    def retranslate(self):
        for widget,key in ((self.previous,'design.previous_song'),(self.next_button,'design.next_song'),(self.shuffle,'qt.player.shuffle'),(self.repeat,'qt.player.repeat'),(self.seek,'qt.local_audio.seek'),(self.volume,'qt.local_audio.volume')):
            widget.setAccessibleName(tr(key));widget.setToolTip(tr(key))
        self.render()

    def close(self):
        self.stop();self.closed=True
        if self.session:self.session.close()
        self.render()

    def wait_closed(self):
        if self.session:
            for thread in tuple(self.session.tasks._threads):thread.join()
            self.session.client.cache.close()
