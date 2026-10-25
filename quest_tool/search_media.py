"""Small bounded cover/preview cache. Workers emit data; never Tk objects."""
from __future__ import annotations

import io
import tempfile
import threading
import warnings
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageOps

from . import audio
from .downloads import BeatSaverClient, DownloadError, check_cancel, safe_web_url
from .song_search import SearchSession


_COVER_DECODE_LOCK = threading.Lock()


class MediaClient(BeatSaverClient):
    def __init__(self):
        super().__init__()
        self.covers = OrderedDict()
        self.previews = OrderedDict()

    def load(self, url, kind, cancel):
        safe_web_url(url)
        cache = self.covers if kind == "cover" else self.previews
        if url in cache:
            check_cancel(cancel)
            cache.move_to_end(url)
            return cache[url]
        with tempfile.TemporaryDirectory(prefix="quest-media-") as folder:
            path = Path(folder) / "data"
            self.fetch(url, path, cancel, lambda _done, _total: None,
                       limit=(5 if kind == "cover" else 16) * 1024 * 1024)
            data = path.read_bytes()
        check_cancel(cancel)
        if kind == "cover":
            try:
                # Python warning filters are process-global on this runtime.
                # Serialize only validation/normalization, never network I/O.
                with _COVER_DECODE_LOCK, warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(data)) as source:
                        if source.width > 4096 or source.height > 4096:
                            raise ValueError("cover dimensions")
                        image = ImageOps.fit(source.convert("RGB"), (256, 256))
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                data = buffer.getvalue()
            except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise DownloadError("cover", str(exc)) from exc
        cache[url] = data
        while len(cache) > (64 if kind == "cover" else 3):
            cache.popitem(last=False)
        return data


class CoverClient:
    def __init__(self, tasks, media):
        self.tasks, self.media = tasks, media

    def search(self, items, generation, _order, cancel):
        for item in items:
            check_cancel(cancel)
            url = item.get("coverURL", "")
            if not url:
                continue
            try:
                data = self.media.load(url, "cover", cancel)
                check_cancel(cancel)
                self.tasks.emit("cover", payload={"generation": generation, "url": url, "data": data})
            except Exception as exc:
                check_cancel(cancel)
                self.tasks.emit("cover", payload={"generation": generation, "url": url, "data": None})
                if isinstance(exc, DownloadError) and exc.code in {"auth", "access", "rate_limit", "network"}:
                    raise
        return {}


class PreviewClient:
    def __init__(self, media, player=None):
        self.media = media
        self.player = player or audio.player()

    def search(self, item, start, request_id, cancel):
        url = item.get("previewURL", "")
        if not url:
            raise DownloadError("preview_missing")
        if not audio.available():
            raise DownloadError("preview_unavailable")
        data = self.media.load(url, "preview", cancel)
        check_cancel(cancel)
        with tempfile.TemporaryDirectory(prefix="quest-preview-") as folder:
            path = Path(folder) / "preview.ogg"
            path.write_bytes(data)
            total = audio.probe_duration(data)
            if not total or total > 600:
                raise DownloadError("preview")
            check_cancel(cancel)
            span = min(total, 30)
            start = max(0, min(start, span - .01))
            played = self.player.play_file(path, seconds=max(.01, span - start), start=start, request_id=request_id)
        check_cancel(cancel)
        if played is None:
            raise DownloadError("preview")
        return {"request_id": request_id, "total": total, "id": item["id"]}


class MediaSession:
    def __init__(self, player=None):
        # Separate bounded caches/clients keep cover and audio I/O independent.
        self.covers = SearchSession()
        self.covers.client = CoverClient(self.covers.tasks, MediaClient())
        self.preview = SearchSession(PreviewClient(MediaClient(), player))

    def close(self):
        self.covers.close()
        self.preview.close()
