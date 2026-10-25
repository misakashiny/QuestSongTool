"""Qt presentation for the existing preview worker and shared audio engine."""
from PySide6.QtCore import QObject, QTimer

from ..messages import tr
from ..theme import get_palette
from .icons import line_icon


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
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        window.play.clicked.connect(self.toggle)
        window.results.previewRequested.connect(self.play_song)
        window.seek.sliderReleased.connect(self.seek)
        window.seek.actionTriggered.connect(self.seek_action)
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
        self.play_song(self.song or self.window.current_song)

    def play_song(self, song):
        if self.closed or song is None:
            return
        if self.song and self.song.key == song.key and self.request_id is not None:
            self.stop()
            return
        self.start(song, 0)

    def start(self, song, start):
        if self.closed:
            return
        self.stop()
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
        self.render()

    def stop(self):
        if self.session is not None:
            self.session.cancel()
        if self.player is not None and self.request_id == self.player.request_id:
            self.player.stop()
        self.request_id = self.generation = None
        self.pending = False
        self.total = 0
        self.error = ""
        self.timer.stop()
        self.render()

    def seek_action(self, _action):
        # Mouse drags commit once on release; keyboard/trough actions commit here.
        if not self.window.seek.isSliderDown():
            self.seek()

    def seek(self):
        if self.total and not self.pending and self.song and self.request_id is not None:
            self.start(self.song, self.window.seek.sliderPosition()/1000*self.total)

    def poll(self):
        if self.closed:
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
            elif message.kind == "search":
                result = data["result"]
                if result.get("request_id") != self.request_id:
                    continue
                self.pending = False
                self.total = min(30, result["total"])
        if self.request_id is not None and not self.pending and not self.player.is_playing():
            self.stop()
        self.render()

    def render(self):
        window = self.window
        song = self.song or window.current_song
        active = self.request_id is not None
        window.play.setEnabled(not self.closed and bool(song and song.preview_url))
        window.play.setIcon(line_icon("stop" if active else "play", get_palette(window.mode).ACCENT_TEXT))
        window.play.setAccessibleName(tr("qt.preview.stop" if active else "layout.preview"))
        window.seek.setEnabled(not self.closed and self.total > 0 and not self.pending)
        position = min(self.total, self.player.position()) if active and not self.pending else 0
        if not window.seek.isSliderDown():
            window.seek.setValue(round(position/self.total*1000) if self.total else 0)
        status = tr(self.error) if self.error else tr("media.loading") if self.pending else (
            f"{int(position)//60:02}:{int(position)%60:02} / {int(self.total)//60:02}:{int(self.total)%60:02}")
        width = max(window.preview_time.fontMetrics().horizontalAdvance(text) for text in
                    ("00:00 / 00:00", tr("media.loading"), tr("media.failed"))) + 8
        window.preview_time.setFixedWidth(width)
        window.preview_time.setText(tr("media.failed") if self.error else status)
        window.preview_time.setToolTip(status)
        window.play.setToolTip((song.title + " · " + song.artist) if song else tr("media.preview"))
        window.refresh_preview_identity()
        key = self.song.key if self.song and active else None
        if window.results.preview_key != key:
            window.results.preview_key = key
            window.results.viewport().update()

    def close(self):
        if self.closed:
            return
        self.stop()
        self.closed = True
        if self.session is not None:
            self.session.close()
        self.render()
