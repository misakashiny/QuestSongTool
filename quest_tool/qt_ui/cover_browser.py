"""Qt cover presentation: bounded pixmaps, existing message-only media worker."""
from collections import OrderedDict
import math

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap

from ..messages import tr
from .cover_session import CoverSession


def create_session():
    # Reuse validation, byte/dimension limits, normalization and HTTP safeguards.
    # Importing the module does not create the shared audio player or device.
    return CoverSession()


class CoverCache:
    SOURCE_LIMIT = 128
    SOURCE_BYTES = 32*1024*1024
    SCALED_LIMIT = 128
    SCALED_BYTES = 16*1024*1024

    def __init__(self):
        self.sources = OrderedDict()
        self.scaled = OrderedDict()
        self.source_bytes = self.scaled_bytes = 0
        self.hits = self.misses = 0

    @staticmethod
    def cost(pixmap):
        return pixmap.width()*pixmap.height()*math.ceil(pixmap.depth()/8)

    def put(self, url, data):
        pixmap = QPixmap()
        if not pixmap.loadFromData(data, "PNG") or pixmap.width() > 256 or pixmap.height() > 256:
            return False
        if url in self.sources:
            self.source_bytes -= self.cost(self.sources.pop(url))
        for key in tuple(self.scaled):
            if key[0] == url:
                self.scaled_bytes -= self.cost(self.scaled.pop(key))
        self.sources[url] = pixmap
        self.source_bytes += self.cost(pixmap)
        while len(self.sources) > self.SOURCE_LIMIT or self.source_bytes > self.SOURCE_BYTES:
            _url, old = self.sources.popitem(last=False)
            self.source_bytes -= self.cost(old)
        return True

    def image(self, url, size, ratio):
        if url not in self.sources or size.isEmpty():
            return None
        self.sources.move_to_end(url)
        width, height = max(1, math.ceil(size.width()*ratio)), max(1, math.ceil(size.height()*ratio))
        resolution = min(1, 2048/max(width, height))
        width, height = max(1, round(width*resolution)), max(1, round(height*resolution))
        key = (url, width, height, ratio)
        if key in self.scaled:
            self.hits += 1
            self.scaled.move_to_end(key)
            return self.scaled[key]
        self.misses += 1
        source = self.sources[url]
        # Crop to the target aspect ratio, rather than stretching square art.
        crop_w, crop_h = source.width(), source.height()
        if crop_w/crop_h > width/height:
            crop_w = max(1, round(crop_h*width/height))
        else:
            crop_h = max(1, round(crop_w*height/width))
        cropped = source.copy(QRect((source.width()-crop_w)//2, (source.height()-crop_h)//2, crop_w, crop_h))
        image = cropped.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        image.setDevicePixelRatio(ratio)
        cost = self.cost(image)
        if cost <= self.SCALED_BYTES:
            self.scaled[key] = image
            self.scaled_bytes += cost
            while len(self.scaled) > self.SCALED_LIMIT or self.scaled_bytes > self.SCALED_BYTES:
                _key, old = self.scaled.popitem(last=False)
                self.scaled_bytes -= self.cost(old)
        return image


class CoverBrowser(QObject):
    imagesChanged = Signal()
    def __init__(self, window, session=None):
        super().__init__(window)
        self.window = window
        self.session = session if session is not None else create_session()
        self.cache = CoverCache()
        self.epoch = 0
        self.generation = None
        self.pending = set()
        self.failed = set()
        self.closed = False
        self.warm_queue = ()
        self.warming = False
        self.current_urls = set()
        self.cache_controls = None
        self.timer = QTimer(window)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.poll)
        window.results.cover_cache = self.cache
        self.priority_timer = QTimer(window)
        self.priority_timer.setSingleShot(True)
        self.priority_timer.setInterval(60)
        self.priority_timer.timeout.connect(self.reprioritize)
        window.results.verticalScrollBar().valueChanged.connect(self.schedule_priority)
        window.results.viewport().installEventFilter(self)
        window.cover.installEventFilter(self)

    def configure_disk(self, sandbox):
        if self.closed or not isinstance(self.session, CoverSession):return
        from .cover_cache_controls import CoverCacheControls

        cache = self.session.configure_disk(sandbox)
        if self.cache_controls is None:self.cache_controls = CoverCacheControls(self.window, cache)

    def retranslate(self):
        if self.cache_controls:self.cache_controls.retranslate()

    def eventFilter(self, watched, event):
        if self.closed:return False
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            if watched is self.window.cover:self.show_detail(self.window.current_song)
            self.schedule_priority()
        return super().eventFilter(watched, event)

    def schedule_priority(self, *_args):
        if not self.closed and isinstance(self.session, CoverSession):
            self.priority_timer.start()

    def reprioritize(self):
        if self.closed or not isinstance(self.session, CoverSession):
            return
        window = self.window
        urls = [window.current_song.cover_url] if window.current_song else []
        viewport = window.results.viewport().rect()
        urls.extend(song.cover_url for row, song in enumerate(window.song_model.visible_songs())
                    if window.results.visualRect(window.song_model.index(row)).intersects(viewport))
        self.session.prioritize(urls)

    def page(self, songs):
        if self.closed:
            return
        self.warm_queue = ()
        self.warming = False
        self.current_urls = {song.cover_url for song in songs if song.cover_url}
        self.epoch += 1
        self.session.cancel()
        self.timer.stop()
        self.generation = None
        self.failed.clear()
        items = {song.cover_url: {"coverURL": song.cover_url} for song in songs
                 if song.cover_url and song.cover_url not in self.cache.sources}
        self.pending = set(items)
        self.window.results.cover_pending = self.pending
        if items:
            self.reprioritize()
            self.generation = self.session.request(list(items.values()), self.epoch, "")
            self.timer.start()
        self.show_detail(self.window.current_song)
        self.window.results.viewport().update()

    def cancel_warm(self):
        self.warm_queue = ()
        if not self.warming:
            return
        self.warming = False
        self.epoch += 1
        self.session.cancel()
        self.pending.clear()
        self.generation = None
        self.timer.stop()

    def warm(self, songs):
        if self.closed:
            return
        count = min(21, max(0, self.cache.SOURCE_LIMIT-len(self.current_urls)))
        self.warm_queue = tuple(dict.fromkeys(song.cover_url for song in songs if song.cover_url))[:count]
        self.start_warm()

    def start_warm(self):
        if self.closed or self.generation is not None or not self.warm_queue:
            return
        urls, self.warm_queue = self.warm_queue, ()
        items = [{"coverURL": url} for url in urls if url not in self.cache.sources and url not in self.failed]
        if not items:
            return
        # Protect the visible page from eviction before adding upcoming artwork.
        for url in self.current_urls:
            if url in self.cache.sources:
                self.cache.sources.move_to_end(url)
        self.epoch += 1
        self.warming = True
        self.pending = {item["coverURL"] for item in items}
        self.generation = self.session.request(items, self.epoch, "")
        self.timer.start()

    def show_detail(self, song):
        if self.closed:
            return
        self.reprioritize()
        self.window.cover.clear()
        side = max(1, min(self.window.cover.width(), self.window.cover.height()))
        image = self.cache.image(song.cover_url, QSize(side, side), self.window.cover.devicePixelRatioF()) if song else None
        if image is not None:
            self.window.cover.setPixmap(image)
        else:
            key = "qt.cover.loading" if song and song.cover_url in self.pending else "media.no_cover"
            self.window.cover.setText(tr(key))

    def poll(self):
        if self.closed:
            return
        if isinstance(self.session, CoverSession):
            self.session.pump()
        view = self.window.results
        images_changed = False
        for message in self.session.tasks.drain():
            data = message.payload
            if not isinstance(data, dict) or self.generation is None:
                continue
            if message.kind == "cover" and data.get("generation") == self.epoch:
                url = data.get("url")
                if url not in self.pending:
                    continue
                self.pending.discard(url)
                if not data.get("data") or not self.cache.put(url, data["data"]):
                    self.failed.add(url)
                else:
                    images_changed = True
                for row, song in enumerate(self.window.song_model.visible_songs()):
                    if song.cover_url == url:
                        view.viewport().update(view.visualRect(self.window.song_model.index(row)))
                if self.window.current_song and self.window.current_song.cover_url == url:
                    self.show_detail(self.window.current_song)
            elif message.kind in ("search", "search_error") and data.get("generation") == self.generation:
                self.failed.update(self.pending)
                self.pending.clear()
                self.generation = None
                self.timer.stop()
                self.show_detail(self.window.current_song)
                view.viewport().update()
                self.warming = False
        self.start_warm()
        if images_changed:
            self.imagesChanged.emit()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.priority_timer.stop()
        if self.cache_controls:self.cache_controls.close()
        self.warm_queue = ()
        self.warming = False
        self.generation = None
        self.pending.clear()
        self.timer.stop()
        self.session.close()

    def wait_closed(self):
        if isinstance(self.session, CoverSession):self.session.wait_closed()
        if self.cache_controls:self.cache_controls.wait_closed()
