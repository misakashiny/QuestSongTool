"""BeatSaver downloads and transactional local ZIP installation. No Tk/device access."""
from __future__ import annotations

import hashlib
from email.utils import parsedate_to_datetime
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from queue import Empty, Queue
from typing import Callable

from .constants import APP_VERSION
from .playlist import difficulty_files, song_hash
from .workers import TaskRunner

MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_FILES = 4000
CHUNK = 128 * 1024
TERMINAL = {"done", "exists", "failed", "cancelled", "interrupted"}


class DownloadError(Exception):
    """A translatable error code plus diagnostic detail."""
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise DownloadError("cancelled")


def parse_map_ids(text: str) -> list[str]:
    if len(text) > 100000:
        raise DownloadError("input")
    result = []
    for item in re.split(r"[\s,;，；]+", text.strip()):
        if not item:
            continue
        value = item
        if "://" in item:
            url = urllib.parse.urlsplit(item)
            if url.scheme != "https" or url.netloc.lower() not in {"beatsaver.com", "www.beatsaver.com"}:
                raise DownloadError("input", item)
            match = re.fullmatch(r"/maps/([0-9a-fA-F]{1,12})/?", url.path)
            if not match:
                raise DownloadError("input", item)
            value = match[1]
        if not re.fullmatch(r"[0-9a-fA-F]{1,12}", value):
            raise DownloadError("input", item)
        if value.lower() not in result:
            result.append(value.lower())
    if not result or len(result) > 200:
        raise DownloadError("input")
    return result


def safe_web_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or not (host == "beatsaver.com" or host.endswith(".beatsaver.com"))
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise DownloadError("url", url)
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_web_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class BeatSaverClient:
    def __init__(self):
        self.opener = urllib.request.build_opener(SafeRedirect())

    def fetch(self, url: str, destination: Path, cancel: threading.Event,
              progress: Callable[[int, int], None], limit: int = MAX_ARCHIVE) -> None:
        safe_web_url(url)
        for attempt in range(3):
            check_cancel(cancel)
            try:
                request = urllib.request.Request(url, headers={"User-Agent": f"QuestSongComparator/{APP_VERSION}",
                                                               "Accept-Encoding": "identity"})
                with self.opener.open(request, timeout=15) as response:
                    safe_web_url(response.geturl())
                    length = response.headers.get("Content-Length", "")
                    total = int(length) if length.isdecimal() else 0
                    if total > limit:
                        raise DownloadError("limit")
                    received = 0
                    with destination.open("wb") as out:
                        while True:
                            check_cancel(cancel)
                            block = response.read(CHUNK)
                            if not block:
                                break
                            received += len(block)
                            if received > limit:
                                raise DownloadError("limit")
                            out.write(block)
                            progress(received, total)
                    if not received or (total and received != total):
                        raise DownloadError("incomplete")
                    return
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise DownloadError("auth" if exc.code == 401 else "access") from exc
                if exc.code == 404:
                    raise DownloadError("not_found") from exc
                if exc.code == 429 and attempt == 2:
                    raise DownloadError("rate_limit") from exc
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise DownloadError("network", str(exc)) from exc
                retry = exc.headers.get("Retry-After", "")
                wait = 2 ** (attempt + 1)
                if retry.isdecimal():
                    wait = max(1, int(retry))
                elif retry:
                    try:
                        wait = max(1, parsedate_to_datetime(retry).timestamp() - time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
            except (OSError, urllib.error.URLError) as exc:
                if attempt == 2:
                    raise DownloadError("network", str(exc)) from exc
                wait = 2 ** (attempt + 1)
            if cancel.wait(wait):
                raise DownloadError("cancelled")

    def resolve(self, map_id: str, scratch: Path, cancel: threading.Event) -> dict:
        path = scratch / "metadata.json"
        self.fetch(f"https://api.beatsaver.com/maps/id/{map_id}", path, cancel,
                   lambda _done, _total: None, limit=2 * 1024 * 1024)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            versions = [v for v in data["versions"] if v.get("state") == "Published"]
            version = max(versions, key=lambda v: v.get("createdAt", ""))
            digest = version["hash"].lower()
            if not re.fullmatch(r"[0-9a-f]{40}", digest):
                raise ValueError("invalid map hash")
            meta = data.get("metadata", {})
            return {"map_id": map_id, "hash": digest, "url": safe_web_url(version["downloadURL"]),
                    "title": str(meta.get("songName") or data.get("name") or map_id)[:200],
                    "mapper": str(meta.get("levelAuthorName") or "")[:160]}
        except (ValueError, KeyError, TypeError) as exc:
            raise DownloadError("metadata", str(exc)) from exc


def safe_relative(name: str) -> Path:
    name = name.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or "\x00" in name:
        raise DownloadError("unsafe", name)
    for part in parts:
        if (part in (".", "..") or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " "))
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise DownloadError("unsafe", name)
    return Path(*parts)


def validate_song(root: Path) -> tuple[str, str]:
    infos = [p for p in root.iterdir() if p.is_file() and p.name.lower() == "info.dat"]
    if len(infos) != 1 or infos[0].stat().st_size > 2 * 1024 * 1024:
        raise DownloadError("structure")
    try:
        info = json.loads(infos[0].read_text(encoding="utf-8-sig"))
        if not isinstance(info, dict):
            raise ValueError("info.dat must be an object")
        files = difficulty_files(info)
        audio = info.get("_songFilename") or info.get("songFilename") or (info.get("audio") or {}).get("songFilename")
        if not audio or not files:
            raise DownloadError("structure")
        if Path(str(audio)).suffix.lower() not in {".egg", ".ogg", ".wav", ".mp3", ".flac"}:
            raise DownloadError("structure", str(audio))
        required = files + [audio]
        cover = info.get("_coverImageFilename") or info.get("coverImageFilename")
        if cover:
            required.append(cover)
        for item in info.get("difficultyBeatmaps") or []:
            if isinstance(item, dict) and item.get("lightshowDataFilename"):
                required.append(item["lightshowDataFilename"])
        audio_data = (info.get("audio") or {}).get("audioDataFilename")
        if audio_data:
            required.append(audio_data)
        for name in required:
            ref = root / safe_relative(str(name))
            if not ref.resolve().is_relative_to(root.resolve()) or not ref.is_file() or not ref.stat().st_size:
                raise DownloadError("structure", str(name))
        for filename in files:
            path = root / safe_relative(filename)
            if path.stat().st_size > 64 * 1024 * 1024:
                raise DownloadError("limit", filename)
            with path.open("r", encoding="utf-8-sig") as source:
                if not isinstance(json.load(source), dict):
                    raise DownloadError("structure", filename)
        title = str(info.get("_songName") or (info.get("song") or {}).get("songName") or info.get("songName") or root.name)
        digest = song_hash(root)
        if not digest:
            raise DownloadError("structure")
        return title[:200], digest
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise DownloadError("structure", str(exc)) from exc


def unpack_song(archive: Path, scratch: Path, cancel: threading.Event,
                progress: Callable[[int, int], None]) -> Path:
    if archive.stat().st_size > MAX_ARCHIVE:
        raise DownloadError("limit")
    try:
        with zipfile.ZipFile(archive) as zipped:
            entries = zipped.infolist()
            total = sum(entry.file_size for entry in entries)
            if not entries or len(entries) > MAX_FILES or total > MAX_EXPANDED:
                raise DownloadError("limit")
            if shutil.disk_usage(scratch).free < total + 16 * 1024 * 1024:
                raise DownloadError("space")
            seen = set()
            paths = []
            for entry in entries:
                relative = safe_relative(entry.filename.rstrip("/"))
                key = relative.as_posix().casefold()
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if key in seen or mode not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                    raise DownloadError("unsafe", entry.filename)
                if entry.file_size > MAX_ARCHIVE or entry.file_size > max(1, entry.compress_size) * 2000:
                    raise DownloadError("limit")
                seen.add(key)
                paths.append((entry, scratch / relative))
            done = 0
            for entry, destination in paths:
                check_cancel(cancel)
                if entry.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, destination.open("xb") as out:
                    while block := source.read(CHUNK):
                        check_cancel(cancel)
                        done += len(block)
                        if done > total or done > MAX_EXPANDED:
                            raise DownloadError("limit")
                        out.write(block)
                        progress(done, total)
            roots = {p.parent for p in scratch.rglob("*") if p.is_file() and p.name.lower() == "info.dat"}
            if len(roots) != 1:
                raise DownloadError("structure")
            root = roots.pop()
            validate_song(root)
            return root
    except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError) as exc:
        raise DownloadError("zip", str(exc)) from exc


def archive_identity(root: Path) -> str:
    """Byte identity for local ZIP imports, including audio and every map file."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix().casefold()):
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root.resolve()):
            raise DownloadError("unsafe", str(path))
        if path.is_file():
            name = path.relative_to(root).as_posix().casefold().encode("utf-8")
            digest.update(len(name).to_bytes(4, "big") + name)
            digest.update(path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as source:
                while block := source.read(CHUNK):
                    digest.update(block)
    return digest.hexdigest()


@dataclass
class DownloadJob:
    id: str
    source: str
    target: str
    kind: str = "online"
    keep_zip: bool = False
    state: str = "queued"
    title: str = ""
    mapper: str = ""
    digest: str = ""
    progress: float = 0.0
    error: str = ""
    detail: str = ""
    installed: str = ""
    created: float = 0.0


class DownloadManager:
    """Two queue consumers, persisted history, and message-only UI notifications."""
    def __init__(self, history: Path, client: BeatSaverClient | None = None):
        self.history = Path(history)
        self.client = client or BeatSaverClient()
        self.tasks = TaskRunner()
        self.jobs: dict[str, DownloadJob] = {}
        self._lock = threading.RLock()
        self._install_lock = threading.Lock()
        self._queue: Queue[str] = Queue()
        self._cancel: dict[str, threading.Event] = {}
        self._stopping = threading.Event()
        self._started = False
        self._reaper = None
        self.last_target = ""
        self.last_keep_zip = False
        if self.history.is_file():
            try:
                saved = json.loads(self.history.read_text(encoding="utf-8"))
                self.last_target = str(saved.get("last_target") or "")
                self.last_keep_zip = bool(saved.get("keep_zip", False))
                for value in saved.get("jobs", [])[-500:]:
                    job = DownloadJob(**value)
                    if job.state not in TERMINAL:
                        job.state, job.error = "interrupted", "interrupted"
                    self.jobs[job.id] = job
            except (OSError, ValueError, TypeError, AttributeError):
                shutil.copy2(self.history, self.history.with_suffix(f".corrupt-{uuid.uuid4().hex}.json"))
                self.jobs.clear()

    def _save(self):
        self.history.parent.mkdir(parents=True, exist_ok=True)
        temp = self.history.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as out:
            json.dump({"version": 1, "last_target": self.last_target, "keep_zip": self.last_keep_zip, "jobs": [asdict(j) for j in self.jobs.values()]}, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, self.history)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [asdict(j) for j in self.jobs.values()]

    def _update(self, job: DownloadJob, **values):
        with self._lock:
            for key, value in values.items():
                setattr(job, key, value)
            try:
                self._save()
            except OSError as exc:
                if job.state not in {"done", "exists"}:
                    raise DownloadError("history", str(exc)) from exc
                job.error, job.detail = "history", str(exc)
            self.tasks.emit("download", payload=asdict(job))

    def enqueue(self, sources: list[str], target: str, *, kind="online", keep_zip=False) -> list[str]:
        if self._stopping.is_set():
            raise DownloadError("closed")
        if not target.strip() or not Path(target).is_absolute() or target.startswith(("\\\\", "//")):
            raise DownloadError("target")
        destination = Path(target).resolve()
        if not destination.is_dir() or destination == Path(destination.anchor):
            raise DownloadError("target")
        if kind not in {"online", "zip"} or not sources or len(sources) > 200:
            raise DownloadError("input")
        if kind == "online" and any(not re.fullmatch(r"[0-9a-f]{1,12}", source) for source in sources):
            raise DownloadError("input")
        ids, pending = [], []
        with self._lock:
            previous_preferences = self.last_target, self.last_keep_zip
            self.last_target, self.last_keep_zip = str(destination), bool(keep_zip)
            if len(self.jobs) + len(sources) > 500:
                raise DownloadError("history_full")
            for source in sources:
                active = next((j for j in self.jobs.values() if j.source == source and j.kind == kind
                               and Path(j.target) == destination and j.state not in TERMINAL), None)
                if active:
                    ids.append(active.id)
                    continue
                job = DownloadJob(uuid.uuid4().hex, source, str(destination), kind, keep_zip,
                                  title=Path(source).name if kind == "zip" else source, created=time.time())
                self.jobs[job.id] = job
                self._cancel[job.id] = threading.Event()
                ids.append(job.id)
                pending.append(job.id)
            try:
                self._save()
            except OSError as exc:
                for job_id in pending:
                    self.jobs.pop(job_id, None)
                    self._cancel.pop(job_id, None)
                self.last_target, self.last_keep_zip = previous_preferences
                raise DownloadError("history", str(exc)) from exc
            for job_id in pending:
                self._queue.put(job_id)
            if not self._started:
                self._started = True
                for _ in range(2):
                    self.tasks.submit(self._worker_loop, name="download_queue")
        return ids

    def retry(self, job_id: str):
        with self._lock:
            job = self.jobs[job_id]
            if job.state not in {"failed", "cancelled", "interrupted"}:
                return
            self.enqueue([job.source], job.target, kind=job.kind, keep_zip=job.keep_zip)

    def cancel(self, job_id: str):
        with self._lock:
            event = self._cancel.get(job_id)
            if event:
                event.set()
                job = self.jobs.get(job_id)
                if job is not None and job.state == "queued":
                    self._update(job, state="cancelled", error="cancelled")

    def clear_finished(self):
        with self._lock:
            self.jobs = {key: job for key, job in self.jobs.items() if job.state not in TERMINAL}
            self._save()

    def shutdown(self):
        self._stopping.set()
        with self._lock:
            for event in self._cancel.values():
                event.set()
            # Keep Python alive until cancelled I/O closes its file handles and
            # TemporaryDirectory finishes cleanup. This thread never touches Tk.
            if self._started and self._reaper is None:
                threads = tuple(self.tasks._threads)
                self._reaper = threading.Thread(target=self._join_workers, args=(threads,),
                                               name="download-shutdown", daemon=False)
                self._reaper.start()

    @staticmethod
    def _join_workers(threads):
        for thread in threads:
            thread.join()

    def _worker_loop(self, _token):
        while not self._stopping.is_set():
            try:
                job_id = self._queue.get(timeout=.2)
            except Empty:
                continue
            with self._lock:
                job = self.jobs.get(job_id)
            if job is None:
                self._queue.task_done()
                continue
            try:
                self._execute(job, self._cancel[job_id])
            except DownloadError as exc:
                self._failure(job, "cancelled" if exc.code == "cancelled" else "failed", exc.code, exc.detail)
            except Exception as exc:
                self._failure(job, "failed", "unexpected", str(exc))
            finally:
                self._queue.task_done()

    def _failure(self, job, state, error, detail):
        try:
            self._update(job, state=state, error=error, detail=detail)
        except DownloadError:
            with self._lock:
                job.state, job.error, job.detail = state, error, detail
                self.tasks.emit("download", payload=asdict(job))

    def _existing(self, target: Path, digest: str, identity: str = "", cancel=None) -> Path | None:
        for folder in target.iterdir():
            if cancel is not None:
                check_cancel(cancel)
            if folder.is_dir() and not folder.name.startswith(".") and not folder.is_symlink() and not folder.is_junction():
                try:
                    _, existing_hash = validate_song(folder)
                    if digest and existing_hash.lower() == digest:
                        if not identity or archive_identity(folder) == identity:
                            return folder
                except DownloadError:
                    continue
        return None

    def _execute(self, job: DownloadJob, cancel: threading.Event):
        check_cancel(cancel)
        target = Path(job.target)
        # Hidden staging is excluded by LocalFS; same volume permits a final rename.
        with tempfile.TemporaryDirectory(prefix=".quest-download-", dir=target) as work:
            scratch = Path(work)
            archive = scratch / "package.zip"
            meta = {}
            if job.kind == "online":
                self._update(job, state="resolving", error="", detail="")
                meta = self.client.resolve(job.source, scratch, cancel)
                self._update(job, title=meta["title"], mapper=meta["mapper"], digest=meta["hash"])
                with self._install_lock:
                    found = self._existing(target, meta["hash"], cancel=cancel)
                if found:
                    self._update(job, state="exists", installed=str(found), progress=1)
                    return
                self._update(job, state="downloading")
                last = [0.0]
                def report(done, total):
                    if time.monotonic() - last[0] > .2:
                        last[0] = time.monotonic()
                        self._update(job, progress=done / total * .75 if total else 0)
                self.client.fetch(meta["url"], archive, cancel, report)
            elif job.kind == "zip":
                source = Path(job.source)
                if not source.is_file() or source.suffix.lower() != ".zip":
                    raise DownloadError("zip")
                if source.stat().st_size > MAX_ARCHIVE:
                    raise DownloadError("limit")
                with source.open("rb") as stream, archive.open("wb") as out:
                    while block := stream.read(CHUNK):
                        check_cancel(cancel)
                        out.write(block)
            else:
                raise DownloadError("input")
            self._update(job, state="extracting", progress=.75)
            unpacked = scratch / "unpacked"
            unpacked.mkdir()
            song = unpack_song(archive, unpacked, cancel, lambda _a, _b: check_cancel(cancel))
            title, digest = validate_song(song)
            if meta and digest.lower() != meta["hash"]:
                raise DownloadError("hash")
            identity = archive_identity(song) if job.kind == "zip" else ""
            mapper = job.mapper
            if job.kind == "zip":
                info_path = next(p for p in song.iterdir() if p.name.lower() == "info.dat")
                info = json.loads(info_path.read_text(encoding="utf-8-sig"))
                mapper = str(info.get("_levelAuthorName") or info.get("levelAuthorName") or "")[:160]
            self._update(job, title=title, mapper=mapper, digest=digest, state="installing", progress=.95)
            with self._install_lock:
                check_cancel(cancel)
                found = self._existing(target, digest, identity, cancel=cancel)
                if found:
                    self._update(job, state="exists", installed=str(found), progress=1)
                    return
                prefix = job.source if job.kind == "online" else digest[:10]
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", f"{prefix} ({title} - {job.mapper})")[:110].rstrip(" .")
                destination = target / name
                count = 1
                while destination.exists():
                    destination = target / f"{name} ({count})"
                    count += 1
                retained = None
                if job.keep_zip:
                    retained = target / ".quest-download-archives"
                    if retained.is_symlink() or retained.is_junction():
                        raise DownloadError("unsafe", str(retained))
                    retained.mkdir(exist_ok=True)
                    retained = retained / f"{job.id}.zip"
                    if shutil.disk_usage(target).free < archive.stat().st_size + 16 * 1024 * 1024:
                        raise DownloadError("space")
                    try:
                        shutil.copy2(archive, retained)
                    except Exception:
                        retained.unlink(missing_ok=True)
                        raise
                try:
                    check_cancel(cancel)
                    song.rename(destination)
                except Exception:
                    if retained:
                        retained.unlink(missing_ok=True)
                    raise
                self._update(job, state="done", installed=str(destination), progress=1)
