"""Explicit playlist preview; installation remains owned by the original queue."""
import base64
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..downloads import BeatSaverClient, DownloadError, check_cancel
from .surfaces import heading, action_strip, action_icons
from .playlist_widgets import RoundedCover

from ..messages import catalog, tr
from ..song_search import SearchSession
from ..theme import DESKTOP, SPACE
from .playlist_store import MAX_FILE, read_playlist


def playlist_url(text):
    if len(text) > 500:
        raise DownloadError('playlist_url')
    url = urlsplit(text.strip())
    if url.scheme != 'https' or url.query or url.fragment:
        raise DownloadError('playlist_url')
    if url.netloc.lower() in ('beatsaver.com', 'www.beatsaver.com'):
        match = re.fullmatch(r'/playlists/([0-9]{1,12})/?', url.path)
    elif url.netloc.lower() == 'api.beatsaver.com':
        match = re.fullmatch(r'/playlists/id/([0-9]{1,12})/download/?', url.path)
    else:
        match = None
    if not match or int(match[1]) == 0:
        raise DownloadError('playlist_url')
    return f'https://api.beatsaver.com/playlists/id/{int(match[1])}/download'


class PlaylistDownloadClient:
    def __init__(self, sandbox, client=None):
        self.root = Path(sandbox)
        self.client = client or BeatSaverClient()

    def search(self, request, _page, _order, cancel):
        check_cancel(cancel)
        if request['kind'] == 'file':
            value = read_playlist(request['source'])
        else:
            url = playlist_url(request['source'])
            # No request to arbitrary syncURL/image links embedded in a file.
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='playlist-', dir=self.root) as name:
                path = Path(name)/'playlist.bplist'
                self.client.fetch(url, path, cancel, lambda _done, _total: None, limit=MAX_FILE)
                check_cancel(cancel)
                value = read_playlist(path)
        check_cancel(cancel)
        value['keys'] = list(dict.fromkeys(song['key'] for song in value['songs'] if song['key']))
        value['missing_keys'] = sum(not song['key'] for song in value['songs'])
        return value


class PlaylistDownloadDialog(QDialog):
    def __init__(self, importer, client=None):
        super().__init__(importer.queue.window)
        self.importer = importer
        self.queue = importer.queue
        self.session = SearchSession(client or PlaylistDownloadClient(self.queue.sandbox))
        self.generation = None
        self.value = None
        self.state = 'hint'
        self.closed = False
        self.error_code = ''
        super().resize(760, 590)
        super().setMinimumSize(620, 440)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
        self.heading = heading('qt.playlist_dl.title');root.addWidget(self.heading)
        self.scroll = QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget();layout = QVBoxLayout(content);layout.setContentsMargins(0,0,SPACE.XS,0)
        self.scroll.setWidget(content);root.addWidget(self.scroll,1)
        self.url = QLineEdit()
        layout.addWidget(self.url)
        self.read = QPushButton();self.file = QPushButton();self.website = QPushButton();self.stop = QPushButton()
        layout.addWidget(action_strip(self.read,self.file,self.website,self.stop))
        header = QHBoxLayout()
        self.cover = RoundedCover();self.cover.setFixedSize(DESKTOP.PLAYER_ART*2, DESKTOP.PLAYER_ART*2)
        self.title = QLabel();self.title.setWordWrap(True);self.title.setTextFormat(Qt.TextFormat.PlainText);self.title.setProperty('role','subtitle')
        header.addWidget(self.cover);header.addWidget(self.title, 1);layout.addLayout(header)
        self.songs = QPlainTextEdit();self.songs.setReadOnly(True);layout.addWidget(self.songs, 1)
        self.status = QLabel();self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.status)
        self.hint = QLabel();self.hint.setWordWrap(True);self.hint.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.hint)
        bar = QHBoxLayout();self.start = QPushButton();self.back = QPushButton()
        self.start.setProperty("role","primary")
        action_icons((self.read,'search'),(self.file,'folder'),(self.website,'link'),(self.stop,'stop'),(self.start,'download'),(self.back,'back'))
        bar.addWidget(self.start);bar.addWidget(self.back);root.addLayout(bar)
        self.read.clicked.connect(lambda:self.load('url', self.url.text()))
        self.url.returnPressed.connect(self.read.click)
        self.url.textEdited.connect(self.invalidate)
        self.file.clicked.connect(self.choose_file)
        self.website.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://beatsaver.com/playlists')))
        self.stop.clicked.connect(self.cancel)
        self.start.clicked.connect(self.enqueue)
        self.back.clicked.connect(self.reject)
        self.finished.connect(self.cancel)
        self.timer = QTimer(self);self.timer.setInterval(75);self.timer.timeout.connect(self.poll)
        self.render()

    def invalidate(self, *_args):
        self.session.cancel();self.generation = None;self.value = None;self.state = 'hint'
        self.timer.stop()
        self.cover.clear();self.songs.clear();self.render()

    def load(self, kind, source):
        if self.closed:
            return
        self.invalidate()
        self.generation = self.session.request(dict(kind=kind, source=source), 0, '')
        self.state = 'loading';self.timer.start();self.render()

    def choose_file(self):
        path, _filter = QFileDialog.getOpenFileName(self, tr('qt.playlists.import'), '', tr('qt.playlists.files'))
        if path:
            self.load('file', path)

    def cancel(self, *_args):
        self.session.cancel();self.generation = None;self.value = None
        self.state = 'cancelled';self.timer.stop();self.cover.clear();self.songs.clear();self.render()

    def poll(self):
        for event in self.session.tasks.drain():
            data = event.payload
            if not isinstance(data, dict) or self.generation is None or data.get('generation') != self.generation:
                continue
            if event.kind not in ('search', 'search_error'):
                continue
            self.generation = None;self.timer.stop()
            if event.kind == 'search_error':
                self.state = 'failed';self.error_code = data.get('code', '')
            else:
                self.value = data['result'];self.state = 'ready'
                if self.value['cover']:
                    pixmap = QPixmap();pixmap.loadFromData(base64.b64decode(self.value['cover']))
                    self.cover.setPixmap(pixmap.scaled(self.cover.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                self.songs.setPlainText('\n'.join(f"{i+1}. {song['songName']}  [{song['key'] or '—'}]" for i, song in enumerate(self.value['songs'])))
        self.render()

    def eligible(self):
        return bool(self.value and self.value['keys'] and len(self.value['keys']) <= 200 and not self.value['missing_keys'])

    def enqueue(self):
        if not self.eligible() or self.queue.busy or self.closed:
            return
        keys = list(self.value['keys']);target = str(self.queue.target)
        if QMessageBox.question(self, tr('qt.playlist_dl.title'), tr('qt.playlist_dl.confirm', count=len(keys), path=target)) != QMessageBox.StandardButton.Yes:
            return
        if self.closed or self.queue.closed or self.queue.busy or str(self.queue.target) != target:
            return
        self.queue.enqueue(keys)
        if self.queue.busy:
            self.queue.window.show_tasks()
            super().accept()

    def render(self):
        self.heading.setText(tr('qt.playlist_dl.title'))
        super().setWindowTitle(tr('qt.playlist_dl.title'))
        for button,key in ((self.read,'read'),(self.file,'file'),(self.website,'website'),(self.stop,'stop'),(self.start,'start'),(self.back,'back')):
            button.setText(tr('qt.playlist_dl.'+key))
        self.url.setPlaceholderText('https://beatsaver.com/playlists/1280881')
        self.url.setAccessibleName(tr('qt.playlist_dl.read'));self.songs.setAccessibleName(tr('qt.playlists.songs'))
        busy = self.generation is not None
        self.read.setEnabled(not busy);self.file.setEnabled(not busy);self.stop.setEnabled(busy)
        self.start.setEnabled(not busy and not self.queue.busy and self.eligible())
        value = self.value
        self.title.setText((value['title']+'\n'+value['author']) if value else tr('qt.playlist_dl.title'))
        self.cover.setVisible(bool(value));self.title.setVisible(bool(value))
        self.hint.setText(tr('qt.playlist_dl.hint'))
        status = tr('qt.playlist_dl.'+self.state)
        if value:
            status = tr('qt.playlist_dl.summary', count=len(value['songs']), unique=len(value['keys']), missing=value['missing_keys'])
            if not self.eligible():
                status += '\n'+tr('qt.playlist_dl.limit')
        elif self.state == 'failed' and 'dl.error.'+self.error_code in catalog('zh'):
            status += '\n'+tr('dl.error.'+self.error_code)
        self.status.setText(status)
        self.status.setVisible(self.state != 'hint')

    def shutdown(self):
        self.closed = True;self.cancel();self.session.close();self.reject()

    def wait_closed(self):
        for thread in tuple(self.session.tasks._threads):
            thread.join()
