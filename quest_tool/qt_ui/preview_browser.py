"""Qt presentation for the existing preview worker and shared audio engine."""
import logging
from PySide6.QtCore import QObject, QTimer

from ..messages import tr
from ..theme import get_palette
from .icons import line_icon

logger=logging.getLogger('quest_tool.qt_preview')


class PreviewBrowser(QObject):
    def __init__(self, window, session=None, player=None):
        super().__init__(window)
        self.window = window
        self.session = session
        self.player = player
        self.song = None
        self.request_id = None
        self.generation = None
        self.pending = False
        self.total = 0
        self.error = ""
        self.closed = False
        self.paused=False;self.position=0
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        window.play.clicked.connect(self.toggle)
        window.results.previewRequested.connect(self.play_song)
        window.seek.seekRequested.connect(self.seek)
        window.volume.valueChanged.connect(self.volume_changed)
        self.render()

    def ensure_services(self):
        if self.player is None:
            from ..audio import player

            self.player = player()
        if self.session is None:
            from ..search_media import MediaClient, PreviewClient
            from ..song_search import SearchSession

            self.session = SearchSession(PreviewClient(MediaClient(), self.player))
        self.volume_changed(self.window.volume.value())

    def volume_changed(self, value):
        if self.player is not None:
            self.player.set_volume(value/100)

    def toggle(self):
        if self.request_id is not None:self.pause()
        elif self.paused and self.song:self.start(self.song,self.position)
        else:self.play_song(self.song or self.window.current_song)

    def play_song(self, song, restart=False):
        if self.closed or song is None:
            return
        if not restart and self.song and self.song.key == song.key and (self.request_id is not None or self.paused):
            self.toggle()
            return
        self.start(song, 0)

    def start(self, song, start):
        if self.closed:
            return
        previous_total=self.total if self.song and self.song.key==song.key else 0
        self.halt();self.total=previous_total;self.position=start;self.paused=False;self.error=''
        self.song = song
        if not song.preview_url:
            self.error = "media.missing"
            self.render()
            return
        self.ensure_services()
        self.player.stop()
        self.request_id = self.player.request_id
        self.pending = True
        self.generation = self.session.request(
            {"id": song.key, "previewURL": song.preview_url}, start, self.request_id)
        self.timer.start()
        logger.info('Online preview requested: id=%s start=%.2fs',song.key,start)
        self.render()

    def halt(self):
        if self.session is not None:
            self.session.cancel()
        if self.player is not None and self.request_id == self.player.request_id:
            self.player.stop()
        self.request_id = self.generation = None
        self.pending = False
        self.timer.stop()

    def stop(self):
        self.halt();self.total=0;self.position=0;self.paused=False;self.error=''
        self.render()

    def pause(self):
        self.position=self.player.position() if not self.pending and self.request_id==self.player.request_id else self.position
        self.halt();self.paused=True;self.render()

    def seek(self, value):
        if self.total and not self.pending and self.song and (self.request_id is not None or self.paused):
            target=value/1000*self.total
            if self.paused:self.position=target;self.render()
            else:self.start(self.song,target)

    def poll(self):
        if self.closed or self.paused or self.request_id is None:
            return
        if self.request_id != self.player.request_id:
            self.stop()
            return
        for message in self.session.tasks.drain():
            data = message.payload
            if not isinstance(data, dict) or data.get("generation") != self.generation:
                continue
            if message.kind == "search_error":
                self.stop()
                self.error = "dl.error." + data["code"]
                logger.warning('Online preview failed: %s',data['code'])
            elif message.kind == "search":
                result = data["result"]
                if result.get("request_id") != self.request_id:
                    continue
                self.pending = False
                self.total = min(30, result["total"])
        if self.request_id is not None and not self.pending and not self.player.is_playing():
            if self.window.repeat.isChecked() and self.song:
                self.start(self.song, 0)
                return
            self.stop()
        self.render()

    def render(self):
        window = self.window
        song = self.song or window.current_song
        active = self.request_id is not None
        window.play.setEnabled(not self.closed and bool(song and song.preview_url))
        window.play.setIcon(line_icon("pause" if active else "play", get_palette(window.mode).ACCENT_TEXT))
        window.play.setAccessibleName(tr("qt.player.pause" if active else "layout.preview"))
        window.seek.setEnabled(not self.closed and self.total > 0 and not self.pending)
        position = min(self.total, self.player.position()) if active and not self.pending else self.position
        if not window.seek.user_seeking():
            window.seek.setValue(round(position/self.total*1000) if self.total else 0)
        status = tr(self.error) if self.error else tr("media.loading") if self.pending else (
            f"{int(position)//60:02}:{int(position)%60:02} / {int(self.total)//60:02}:{int(self.total)%60:02}")
        width = max(window.preview_time.fontMetrics().horizontalAdvance(text) for text in
                    ("00:00", tr("media.loading"), tr("media.failed"))) + 4
        window.preview_time.setFixedWidth(width)
        window.preview_time.setText(tr("media.failed") if self.error else tr('media.loading') if self.pending else f'{int(position)//60}:{int(position)%60:02}')
        window.preview_total.setText(f'{int(self.total)//60}:{int(self.total)%60:02}')
        window.preview_time.setToolTip(status)
        window.play.setToolTip((song.title + " · " + song.artist) if song else tr("media.preview"))
        window.refresh_preview_identity()
        window.refresh_preview_navigation()
        key = self.song.key if self.song and active else None
        if window.results.preview_key != key:
            window.results.preview_key = key
            window.results.viewport().update()
        if window.playlist_browser and window.pages.currentWidget() is window.playlist_browser.page:
            table = window.playlist_browser.table
            if getattr(self, '_row_preview_key', None) != key:
                self._row_preview_key = key;table.viewport().update()

    def close(self):
        if self.closed:
            return
        self.stop()
        self.closed = True
        if self.session is not None:
            self.session.close()
        self.render()
