"""Beat Saber 歌单（`.bplist` / `.json`）读写（ROADMAP D2）。

## 格式要点（决定这个模块怎么写）

`.bplist` 就是一份 JSON：`playlistTitle` / `playlistAuthor` / `image`（base64 封面）
+ `songs[]`。每首歌靠这几个字段标识：

| 字段 | 说明 |
|---|---|
| `songName` | 屏幕上显示的标题 |
| `levelAuthorName` | **谱师**（不是歌手！） |
| `hash` | 谱面文件的 SHA-1（40 位十六进制） |
| `levelId` | `custom_level_` + 大写 hash |
| `key` | BeatSaver 上的编号（可缺） |

**关键：游戏是按 `hash` 找歌的，不是按名字。** hash 不对 → 歌单能打开但**一首都不显示**。
这是 `.bplist` 最典型的坑，所以本模块必须**真的算 hash**，绝不能用名字糊过去。

## hash 怎么算

按官方/社区文档的描述：把 `info.dat` 的内容，与它里面按顺序列出的每个难度文件内容，
**依次拼接**后取 SHA-1。

实现上的两个必须注意的点：

1. **按字节拼接，不做任何解析或换行归一** —— 差一个字节 hash 就全错；
2. **顺序取自 `info.dat` 自己的列表顺序**，不是按文件名排序（谱师改过顺序的情况是存在的）。

难度文件读不到时（`_beatmapFilename` 指向的文件不存在）**整个 hash 判为不可计算** ——
宁可让这首歌标记成"无法导出"，也不要给出一个错的 hash（错的 hash 比没有更糟：
它会让歌单看起来正常、实际空白）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .logging_setup import get_logger

logger = get_logger("playlist")

#: `.bplist` 的默认文件名后缀
PLAYLIST_SUFFIX = ".bplist"

#: `levelId` 的前缀（官方约定，写错游戏就找不到歌）
LEVEL_ID_PREFIX = "custom_level_"

#: 40 位十六进制 = SHA-1
_HASH_RE = re.compile(r"^[0-9a-fA-F]{40}$")

#: info.dat 的几种文件名（大小写不一致是常态）
INFO_NAMES = ("info.dat", "Info.dat", "INFO.DAT", "info.json")

#: 难度列表可能出现的 key（v2/v3 用 `_difficultyBeatmapSets`，v4 用 `difficultyBeatmaps`）
_SET_KEYS = ("_difficultyBeatmapSets", "difficultyBeatmapSets")
_MAP_KEYS = ("_difficultyBeatmaps", "difficultyBeatmaps")
_FILE_KEYS = ("_beatmapFilename", "beatmapDataFilename")


@dataclass
class PlaylistSong:
    """歌单里的一首歌。"""

    #: 屏幕上显示的标题
    songName: str = ""
    #: **谱师**（不是歌手）
    levelAuthorName: str = ""
    #: 谱面 SHA-1（40 位十六进制，小写）
    hash: str = ""
    #: `custom_level_<大写hash>`；没有 hash 时为空
    levelId: str = ""
    #: BeatSaver 编号（可能没有）
    key: str = ""
    #: 导入时用来在我们自己的歌库里找歌（不是格式字段，不写进文件）
    folder_hint: str = ""

    def to_dict(self) -> dict[str, str]:
        """转成写进文件的形状（**不包含**内部字段）。"""
        data: dict[str, str] = {
            "songName": self.songName,
            "levelAuthorName": self.levelAuthorName,
        }
        if self.hash:
            data["hash"] = self.hash
            data["levelId"] = self.levelId or f"{LEVEL_ID_PREFIX}{self.hash.upper()}"
        if self.key:
            data["key"] = self.key
        return data


@dataclass
class Playlist:
    """一份歌单。"""

    title: str = ""
    author: str = ""
    songs: list[PlaylistSong] = field(default_factory=list)
    #: 封面（原始图片字节；写文件时转 base64）
    cover: bytes = b""
    #: 读进来的原始 JSON（保留我们不认识的字段，回写时尽量不丢东西）
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.songs)

    @property
    def hashed(self) -> int:
        """有多少首带有效 hash。"""
        return sum(1 for s in self.songs if _HASH_RE.match(s.hash or ""))

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.extra)
        data["playlistTitle"] = self.title
        data["playlistAuthor"] = self.author
        if self.cover:
            data["image"] = base64.b64encode(self.cover).decode("ascii")
        data["songs"] = [s.to_dict() for s in self.songs]
        return data


# ======================================================================================
# hash
# ======================================================================================
def _read_info_bytes(song_dir: Path) -> tuple[bytes, Any] | None:
    """读 info.dat 的**原始字节** + 解析后的 JSON。读不到返回 None。"""
    for name in INFO_NAMES:
        path = song_dir / name
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            logger.debug("读不了 %s: %s", path, exc)
            continue
        try:
            # utf-8-sig：有些 info.dat 带 BOM，直接 utf-8 解析会炸
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.debug("%s 不是合法 JSON: %s", path, exc)
            return None
        return raw, data
    return None


def difficulty_files(data: Mapping[str, Any]) -> list[str]:
    """按 **info.dat 里的顺序** 列出所有难度文件名（去重且保持首次出现的顺序）。

    顺序很关键 —— 拼接顺序变了 hash 就变了，而谱师是有可能改列表顺序的。
    """
    names: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip() and value not in names:
            names.append(value.strip())

    # v2/v3：套在 sets 里
    for set_key in _SET_KEYS:
        for group in data.get(set_key) or []:
            if not isinstance(group, Mapping):
                continue
            for map_key in _MAP_KEYS:
                for item in group.get(map_key) or []:
                    if isinstance(item, Mapping):
                        for file_key in _FILE_KEYS:
                            add(item.get(file_key))
    # v4：顶层就是难度列表
    for map_key in _MAP_KEYS:
        for item in data.get(map_key) or []:
            if isinstance(item, Mapping):
                for file_key in _FILE_KEYS:
                    add(item.get(file_key))
    return names


def song_hash(song_dir: str | Path) -> str:
    """算谱面 SHA-1。**算不出来就返回空串**（绝不返回一个猜的）。

    规则：`info.dat` 原始字节 + 各难度文件原始字节（按 info.dat 里的顺序）依次拼接后 SHA-1。
    """
    directory = Path(song_dir)
    loaded = _read_info_bytes(directory)
    if loaded is None:
        return ""
    info_raw, data = loaded
    if not isinstance(data, Mapping):
        return ""

    digest = hashlib.sha1()          # noqa: S324 - 这是格式规定的算法，不是安全用途
    digest.update(info_raw)
    for name in difficulty_files(data):
        path = directory / name
        if not path.is_file():
            # ★ 宁可判为"算不出"，也不给一个错的 hash
            logger.debug("%s 缺少难度文件 %s，hash 判为不可计算", directory.name, name)
            return ""
        try:
            digest.update(path.read_bytes())
        except OSError as exc:
            logger.debug("读不了难度文件 %s: %s", path, exc)
            return ""
    return digest.hexdigest()


# ======================================================================================
# 导入 / 导出
# ======================================================================================
def parse_playlist(text: str) -> Playlist:
    """解析 `.bplist` / `.json` 的内容。解析失败抛 `ValueError`（调用方给用户提示）。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"不是合法的 JSON：{exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError("歌单文件的顶层应该是一个对象")

    songs: list[PlaylistSong] = []
    raw_songs = data.get("songs")
    if isinstance(raw_songs, list):
        for item in raw_songs:
            if not isinstance(item, Mapping):
                continue
            raw_hash = str(item.get("hash") or "").strip().lower()
            hashed = raw_hash if _HASH_RE.match(raw_hash) else ""
            songs.append(PlaylistSong(
                songName=str(item.get("songName") or ""),
                levelAuthorName=str(item.get("levelAuthorName") or ""),
                hash=hashed,
                levelId=str(item.get("levelId") or ""),
                key=str(item.get("key") or ""),
            ))

    cover = b""
    raw_image = data.get("image")
    if isinstance(raw_image, str) and raw_image:
        # base64 可能是 data URI（`data:image/png;base64,xxx`）也可能是裸串
        payload = raw_image.split(",", 1)[1] if raw_image.startswith("data:") else raw_image
        try:
            cover = base64.b64decode(payload, validate=False)
        except (ValueError, TypeError) as exc:
            logger.debug("封面 base64 解不开: %s", exc)

    known = {"playlistTitle", "playlistAuthor", "songs", "image"}
    return Playlist(
        title=str(data.get("playlistTitle") or ""),
        author=str(data.get("playlistAuthor") or ""),
        songs=songs,
        cover=cover,
        extra={k: v for k, v in data.items() if k not in known},
    )


def load_playlist(path: str | Path) -> Playlist:
    """从文件读一份歌单。"""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"读不了歌单文件：{exc}") from exc
    return parse_playlist(text)


def dump_playlist(playlist: Playlist) -> str:
    """把歌单序列化成写文件的文本（**不转义非 ASCII**，中文标题要能直接看）。"""
    return json.dumps(playlist.to_dict(), ensure_ascii=False, indent=2)


def save_playlist(playlist: Playlist, path: str | Path) -> Path:
    """写歌单文件（自动补 `.bplist` 后缀，UTF-8）。"""
    file = Path(path)
    if not file.suffix:
        file = file.with_suffix(PLAYLIST_SUFFIX)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(dump_playlist(playlist), encoding="utf-8")
    logger.info("歌单已导出：%s（%d 首，其中 %d 首有 hash）",
                file, playlist.count, playlist.hashed)
    return file


def build_playlist(
    title: str,
    author: str,
    entries: Iterable[tuple[str, str, str, str]],
    *,
    cover: bytes = b"",
) -> Playlist:
    """由 `(folder_name, song_name, level_author, hash)` 构造歌单。

    `hash` 传空串表示"算不出来" —— 这种歌**仍然会写进歌单**，
    但会在 `songName` 后面保留原名并在结果里计数，让用户知道有几首可能不显示。
    """
    songs: list[PlaylistSong] = []
    for folder, song_name, level_author, digest in entries:
        songs.append(PlaylistSong(
            songName=song_name or folder,
            levelAuthorName=level_author,
            hash=digest.lower() if _HASH_RE.match(digest or "") else "",
            levelId=f"{LEVEL_ID_PREFIX}{digest.upper()}" if _HASH_RE.match(digest or "") else "",
            folder_hint=folder,
        ))
    return Playlist(title=title, author=author, songs=songs, cover=cover)


def match_local(
    playlist: Playlist,
    available: Sequence[tuple[str, str]],
    *,
    titles: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[list[str], list[PlaylistSong]]:
    """把歌单里的歌对到本地歌库，返回 `(命中的文件夹名, 没命中的条目)`。

    匹配**优先按 hash**（那是游戏自己的判据）；hash 对不上时，如果调用方给了
    `titles`（`{文件夹名: (标题, 谱师)}`），再退回"标题 + 谱师都相同"。

    **不做"只按标题"的匹配** —— 重名歌太多了，只按标题会把歌单对到错误的歌上，
    那比"没对上"更糟（用户以为导入成功了，实际是别的歌）。
    """
    by_hash: dict[str, str] = {}
    by_title_author: dict[tuple[str, str], str] = {}
    for folder, digest in available:
        if _HASH_RE.match(digest or ""):
            by_hash.setdefault(digest.lower(), folder)
    for folder, (title, author) in (titles or {}).items():
        if title.strip() and author.strip():
            by_title_author.setdefault(
                (title.strip().casefold(), author.strip().casefold()), folder
            )

    matched: list[str] = []
    unmatched: list[PlaylistSong] = []
    for song in playlist.songs:
        folder = by_hash.get(song.hash, "") if song.hash else ""
        if not folder and song.songName and song.levelAuthorName:
            folder = by_title_author.get(
                (song.songName.strip().casefold(), song.levelAuthorName.strip().casefold()), ""
            )
        if folder:
            matched.append(folder)
        else:
            unmatched.append(song)
    return matched, unmatched


def hash_report(songs: Iterable[PlaylistSong]) -> str:
    """给用户看的一句话：有多少首带可用 hash。

    **必须让用户知道这件事** —— hash 缺失的歌在游戏里不会显示，
    如果导出时不说，用户会以为是"歌单坏了"。
    """
    items = list(songs)
    if not items:
        return "歌单里没有歌曲"
    bad = [s for s in items if not _HASH_RE.match(s.hash or "")]
    if not bad:
        return f"全部 {len(items)} 首都能被游戏识别"
    return (f"{len(items) - len(bad)}/{len(items)} 首能被游戏识别；"
            f"另有 {len(bad)} 首算不出 hash，导入游戏后**不会显示**"
            f"（例如：{'、'.join(s.songName for s in bad[:3])}）")
