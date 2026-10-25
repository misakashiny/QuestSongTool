"""Bounded BeatSaver search and cancelable requests; no Tk or library writes."""
from __future__ import annotations

import json
import math
import re
import tempfile
import threading
import urllib.parse
import copy
from pathlib import Path

from .downloads import BeatSaverClient, DownloadError, check_cancel, safe_web_url
from .workers import TaskRunner
from .search_filters import browse_params

PAGE_SIZE = 20
ORDERS = ("Relevance", "Latest", "Rating", "Curated", "Random", "Duration")


def number(value, maximum=1000000):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if 0 <= value <= maximum and math.isfinite(value) else None


def search_map(data: dict) -> dict | None:
    """Reduce untrusted website metadata to a small, validated display record."""
    if not isinstance(data, dict) or not re.fullmatch(r"[0-9a-fA-F]{1,12}", str(data.get("id", ""))):
        return None
    versions = data.get("versions")
    if not isinstance(versions, list):
        return None
    published = [v for v in versions if isinstance(v, dict) and v.get("state") == "Published"]
    if not published:
        return None
    version = max(published, key=lambda v: str(v.get("createdAt", "")))
    digest = str(version.get("hash", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", digest):
        return None
    try:
        safe_web_url(str(version.get("downloadURL", "")))
    except (DownloadError, ValueError):
        return None
    meta = data.get("metadata") or {}
    stats = data.get("stats") or {}
    if not isinstance(meta, dict) or not isinstance(stats, dict):
        return None
    raw_diffs = version.get("diffs") or []
    if not isinstance(raw_diffs, list):
        return None
    diffs = []
    requirements = set()
    for diff in raw_diffs[:100]:
        if not isinstance(diff, dict):
            continue
        diffs.append({"difficulty": str(diff.get("difficulty", ""))[:40],
                      "characteristic": str(diff.get("characteristic", ""))[:40],
                      "nps": number(diff.get("nps"))})
        for key, label in (("chroma", "Chroma"), ("ne", "Noodle Extensions"),
                           ("me", "Mapping Extensions"), ("cinema", "Cinema"), ("vivify", "Vivify")):
            if diff.get(key) is True:
                requirements.add(label)
    media = {}
    duration = number(meta.get("duration"))
    if not duration:
        durations = [number(diff.get("seconds")) for diff in raw_diffs if isinstance(diff, dict)]
        duration = max((value for value in durations if value), default=None)
    for key in ("coverURL", "previewURL"):
        try:
            media[key] = safe_web_url(str(version.get(key, "")))
        except (DownloadError, ValueError):
            media[key] = ""
    return {"id": str(data["id"]).lower(), "hash": digest, **media,
            "title": str(meta.get("songName") or data.get("name") or data["id"])[:200],
            "artist": str(meta.get("songAuthorName") or "")[:160],
            "mapper": str(meta.get("levelAuthorName") or "")[:160],
            "bpm": number(meta.get("bpm")), "duration": duration,
            "rating": number(stats.get("score"), 1), "diffs": diffs,
            "requirements": sorted(requirements),
            "published": str(version.get("createdAt") or "")[:40],
            "description": str(data.get("description") or "")[:4000]}


class SearchClient(BeatSaverClient):
    def search(self, query: str, page: int, order: str, cancel: threading.Event,
               filters=None, mode="Search", page_size=PAGE_SIZE) -> dict:
        query = query.strip()
        if (not query and mode == "Search") or len(query) > 200 or any(ord(ch) < 32 for ch in query):
            raise DownloadError("query")
        if type(page) is not int or not 0 <= page <= 1000 or order not in ORDERS:
            raise DownloadError("query")
        if type(page_size) is not int or not 1 <= page_size <= 90:
            raise DownloadError("query")
        extra, preset_order = browse_params(mode, filters or {})
        # Existing callers retain their former default semantics; new browse UI
        # explicitly supplies an AI filter along with other website filters.
        if filters is None:
            extra.pop("automapper", None)
        params = urllib.parse.urlencode({"q": query, "order": preset_order or order, "pageSize": page_size, **extra})
        with tempfile.TemporaryDirectory(prefix="quest-search-") as folder:
            path = Path(folder) / "results.json"
            self.fetch(f"https://api.beatsaver.com/search/text/{page}?{params}", path,
                       cancel, lambda _done, _total: None, limit=4 * 1024 * 1024)
            check_cancel(cancel)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                docs = data["docs"]
                if not isinstance(docs, list) or len(docs) > page_size:
                    raise ValueError("invalid search results")
                rows = {}
                for doc in docs:
                    item = search_map(doc)
                    if item:
                        rows[item["id"]] = item
                return {"rows": list(rows.values()), "has_next": len(docs) == page_size,
                        "page": page, "query": query, "order": order}
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise DownloadError("metadata", str(exc)) from exc


class SearchSession:
    """One request at a time; newer UI requests cancel and supersede older ones.

    A single consumer also bounds connections during fast typing or repeated clicks.
    Worker sees plain values, and sends only TaskRunner messages.
    """
    def __init__(self, client=None):
        self.client = client or SearchClient()
        self.tasks = TaskRunner()
        self._lock = threading.Lock()
        self._pending = None
        self._cancel = threading.Event()
        self._running = False
        self._closed = False
        self._reaper = None
        self.generation = 0

    def request(self, query, page, order, filters=None, mode="Search", page_size=PAGE_SIZE):
        with self._lock:
            if self._closed:
                raise DownloadError("closed")
            self.generation += 1
            self._cancel.set()
            self._cancel = threading.Event()
            self._pending = (self.generation, copy.deepcopy(query), page, order, self._cancel, copy.deepcopy(filters), mode, page_size)
            if not self._running:
                self._running = True
                self.tasks.submit(self._consume, name="song-search")
            return self.generation

    def _consume(self, _token):
        while True:
            with self._lock:
                pending = self._pending
                self._pending = None
                if pending is None or self._closed:
                    self._running = False
                    return
            generation, query, page, order, cancel, filters, mode, page_size = pending
            try:
                if page_size != PAGE_SIZE:
                    result = self.client.search(query, page, order, cancel, filters=filters, mode=mode, page_size=page_size)
                elif filters is None and mode == "Search":
                    result = self.client.search(query, page, order, cancel)
                else:
                    result = self.client.search(query, page, order, cancel, filters=filters, mode=mode)
                check_cancel(cancel)
                self.tasks.emit("search", payload={"generation": generation, "result": result})
            except Exception as exc:
                if not cancel.is_set():
                    code = exc.code if isinstance(exc, DownloadError) else "unexpected"
                    self.tasks.emit("search_error", payload={"generation": generation, "code": code})

    def close(self):
        with self._lock:
            self._closed = True
            self._cancel.set()
            self._pending = None
            if self._reaper is None and self.tasks.busy():
                self._reaper = threading.Thread(target=self._join, args=(tuple(self.tasks._threads),),
                                                name="search-shutdown", daemon=False)
                self._reaper.start()

    def cancel(self):
        with self._lock:
            self._cancel.set()
            self._pending = None

    @staticmethod
    def _join(threads):
        for thread in threads:
            thread.join()
