"""Local audio presentation using the shared engine; no GUI access in workers."""
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider

from .. import audio
from ..downloads import DownloadError, check_cancel
from ..messages import tr
from ..song_search import SearchSession
from ..theme import DESKTOP, SPACE, get_palette
from .icons import line_icon


class LocalPreviewClient:
    def __init__(self, player):
        self.player = player

    def search(self, request, _page, _order, cancel):
        root = Path(request['root']).resolve()
        folder = request['folder']
        if not folder or folder in ('.', '..') or any(c in folder for c in '/\\'):
            raise DownloadError('local_audio')
        directory = (root / folder).resolve()
        if directory == root or not directory.is_relative_to(root):
            raise DownloadError('local_audio')
        check_cancel(cancel)
        path = audio._find_audio_file(directory)
        if path is None or not path.resolve().is_relative_to(directory):
            raise DownloadError('local_audio')
        total = audio.probe_duration(path) or 0
        check_cancel(cancel)
        if not self.player.play_file(path, seconds=request['seconds'], start=request['start'], request_id=request['request_id']):
            raise DownloadError('local_audio')
        check_cancel(cancel)
        return dict(key=request['key'], request_id=request['request_id'], total=total)


class LibraryPreview(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.player = None
        self.session = None
        self.request_id = None
        self.generation = None
        self.pending = False
        self.closed = False
        self.total = 0
        self.error = False
        self.drag_range = None
        self.page = QFrame(); self.page.setObjectName('card')
        top = QHBoxLayout(self.page)
        top.setContentsMargins(SPACE.L, SPACE.S, SPACE.L, SPACE.S)
        top.setSpacing(SPACE.L)
        self.cover = QLabel(); self.cover.setObjectName('coverPlaceholder')
        self.cover.setFixedSize(DESKTOP.PLAYER_ART, DESKTOP.PLAYER_ART)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter); top.addWidget(self.cover)
        self.art_key = None
        self.title = QLabel(); self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setFixedWidth(DESKTOP.PLAYER_NAME_W)
        top.addWidget(self.title)
        self.play = QPushButton(); self.play.setProperty('role', 'playerAction')
        self.play.setFixedSize(DESKTOP.PLAYER_ART, DESKTOP.PLAYER_ART)
        self.play.clicked.connect(self.toggle); top.addWidget(self.play)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setRange(0, 1000)
        self.seek.sliderPressed.connect(self.begin_drag); self.seek.sliderReleased.connect(self.end_drag)
        self.seek.actionTriggered.connect(self.seek_action); top.addWidget(self.seek, 1)
        self.time = QLabel(); self.time.setMinimumWidth(105); top.addWidget(self.time)
        self.length = QComboBox()
        for seconds in audio.PREVIEW_LENGTH_CHOICES: self.length.addItem('', seconds)
        self.length.setCurrentIndex(self.length.findData(window.preference_values.get('preview_seconds',30)))
        self.length.currentIndexChanged.connect(self.length_changed); top.addWidget(self.length)
        self.volume_label = QLabel(); top.addWidget(self.volume_label)
        self.volume = QSlider(Qt.Orientation.Horizontal); self.volume.setRange(0, 100); self.volume.setValue(window.preference_values.get('preview_volume',80))
        self.volume.setMinimumWidth(70); self.volume.setMaximumWidth(DESKTOP.PLAYER_VOLUME_W)
        self.volume.valueChanged.connect(self.volume_changed); top.addWidget(self.volume)
        self.timer = QTimer(self); self.timer.setInterval(100); self.timer.timeout.connect(self.poll)
        window.details.selectionChanged.connect(self.selection_changed)
        self.retranslate()

    def selectable(self):
        key = self.window.details.key
        return key is not None and key[0] == 'local' and self.window.local_ready and key[1] == str(self.window.target)

    def owned(self):
        return self.player is not None and self.request_id is not None and self.player.request_id == self.request_id

    def selection_changed(self):
        self.stop()

    def toggle(self):
        if self.request_id is not None: self.stop()
        elif self.selectable(): self.start(0)

    def start(self, offset):
        if self.closed or not self.selectable(): return
        self.stop()
        if self.player is None: self.player = audio.player()
        if self.session is None: self.session = SearchSession(LocalPreviewClient(self.player))
        self.player.stop(); self.request_id = self.player.request_id
        self.player.set_volume(self.volume.value() / 100)
        key = self.window.details.key
        self.pending = True; self.error = False
        self.generation = self.session.request(dict(root=key[1], folder=key[2], key=key,
            start=offset, seconds=self.length.currentData(), request_id=self.request_id), 0, '')
        self.timer.start(); self.render()

    def stop(self):
        if self.session is not None: self.session.cancel()
        if self.owned(): self.player.stop()
        self.request_id = None; self.generation = None; self.pending = False
        self.total = 0; self.error = False; self.drag_range = None
        self.timer.stop(); self.render()

    def poll(self):
        if self.closed: return
        if not self.owned(): self.stop(); return
        for message in self.session.tasks.drain():
            payload = message.payload
            if not isinstance(payload, dict) or payload.get('generation') != self.generation: continue
            if message.kind == 'search_error':
                self.stop(); self.error = True; self.render(); return
            if message.kind != 'search': continue
            result = payload['result']
            if result['request_id'] != self.request_id or tuple(result['key']) != self.window.details.key: continue
            self.pending = False; self.total = result['total']
        if not self.pending and not self.player.is_playing(): self.stop(); return
        self.render()

    def span(self):
        if not self.owned() or self.pending: return (0, 0)
        begin = self.player.start_offset
        limit = self.player.limit_seconds()
        end = begin + limit if limit else self.total
        if self.total: end = min(end, self.total)
        return begin, max(begin, end)

    def begin_drag(self):
        self.drag_range = self.span()

    def end_drag(self):
        limits = self.drag_range
        self.drag_range = None
        self.commit_seek(limits or self.span())

    def seek_action(self, _action):
        if not self.seek.isSliderDown(): self.commit_seek(self.span())

    def commit_seek(self, limits):
        if not self.owned() or self.pending: return
        begin, end = limits
        if end <= begin: return
        target = begin + (end - begin) * self.seek.sliderPosition() / 1000
        if self.total and target >= self.total - .05: self.stop()
        else: self.start(target)

    def length_changed(self):
        self.stop()

    def volume_changed(self, value):
        if self.owned(): self.player.set_volume(value / 100)

    @staticmethod
    def clock(seconds):
        return f'{int(seconds)//60}:{int(seconds)%60:02}'

    def render(self):
        self.play.setEnabled(self.selectable() and not self.closed)
        palette = get_palette(self.window.mode.currentData())
        self.play.setIcon(line_icon('stop' if self.request_id is not None else 'play', palette.ACCENT_TEXT if self.play.isEnabled() else palette.TEXT_MUTED))
        action = tr('qt.preview.stop' if self.request_id is not None else 'qt.local_audio.play')
        self.play.setToolTip(action); self.play.setAccessibleName(action)
        title = (self.window.details.data or {}).get('title', '')
        text = tr('qt.local_audio.failed') if self.error else tr('qt.detail.loading') if self.pending else title or tr('qt.local_audio.choose')
        self.title.setText(self.title.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, self.title.width()))
        self.title.setToolTip(text); self.title.setAccessibleName(text)
        detail_cover = self.window.details.cover
        image = detail_cover.cache.image('detail', self.cover.size(), self.cover.devicePixelRatioF()) if detail_cover.ready else None
        key = image.cacheKey() if image is not None else None
        if key != self.art_key:
            self.art_key = key; self.cover.clear()
            if image is not None:self.cover.setPixmap(image)
        self.volume_label.setPixmap(line_icon('volume', palette.TEXT_SECONDARY).pixmap(20,20))
        begin, end = self.span()
        position = self.player.position() if self.owned() and not self.pending else 0
        self.seek.setEnabled(end > begin and not self.closed)
        if not self.seek.isSliderDown():
            self.seek.setValue(round(1000 * max(0, min(1, (position-begin)/(end-begin)))) if end > begin else 0)
        self.time.setText(self.clock(position) + ' / ' + (self.clock(self.total) if self.total else '--:--'))

    def retranslate(self):
        for i, seconds in enumerate(audio.PREVIEW_LENGTH_CHOICES):
            self.length.setItemText(i, tr('qt.local_audio.full') if not seconds else tr('qt.local_audio.seconds', seconds=seconds))
        self.volume_label.setText(tr('qt.local_audio.volume'))
        self.volume.setAccessibleName(tr('qt.local_audio.volume'))
        self.seek.setAccessibleName(tr('qt.local_audio.seek'))
        self.length.setAccessibleName(tr('qt.local_audio.length'))
        self.render()

    def close(self):
        self.stop(); self.closed = True
        if self.session is not None: self.session.close()
        self.render()

    def wait_closed(self):
        if self.session is not None:
            for thread in tuple(self.session.tasks._threads): thread.join()
