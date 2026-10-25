"""Beat Saber info.dat 解析（兼容 v1/v2/v3/v4 多种写法）。

原版 `_parse_song_info` / `_parse_difficulties` 的问题：
* 难度解析用 4 组 key 轮询，且会**重复添加**：`_difficultyBeatmapSets` 与
  `_difficultyBeatmaps` 同时命中时就会跑两遍（虽然去重了，但逻辑噪音大、无排序保证）。
* 不识别 v4 的 `difficultyBeatmaps[].difficulty`（v4 用的是 `difficulty`，但存放在
  `difficultyBeatmaps` 顶层，而不是 `_difficultyBeatmapSets` 里）。
* 缺字段时返回 "未知"/"未知作者" 这类中文占位，导致数据库里难以区分"真的没有"。
* 没有 duration 字段，UI 无法显示时长。

这里统一成一个纯函数，返回 SongMeta；解析失败/字段缺失用空值表示，由 UI 决定占位文案。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .constants import DIFFICULTY_ORDER

# info.dat 的候选编码。原版只用 utf-8，于是：
#   * 带 UTF-8 BOM 的文件整份失效（"Unexpected UTF-8 BOM"）；
#   * GBK 编码的中文抛 UnicodeDecodeError 并逃出 except，直接打断界面。
# 顺序：utf-8-sig（兼容有/无 BOM）-> utf-8 -> gbk（部分整合包/手工改过的文件）
# -> big5 -> latin-1（最后兜底，保证绝不抛异常）。
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "big5", "latin-1")


def decode_json_bytes(raw: bytes | None) -> dict | None:
    """解码并解析 info.dat 字节流为 dict；失败返回 None（绝不抛异常）。"""
    if not raw:
        return None
    for encoding in TEXT_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 能解码但 JSON 坏了：换编码也没用，直接放弃
            return None
        return data if isinstance(data, dict) else None
    return None


def load_info_file(path: str | os.PathLike[str]) -> dict | None:
    """读取本地 info.dat 并解析（多编码兼容）。"""
    try:
        with open(path, "rb") as fh:
            return decode_json_bytes(fh.read())
    except OSError:
        return None

# 常见难度别名 -> 标准名
_DIFFICULTY_ALIASES = {
    "easy": "Easy",
    "normal": "Normal",
    "hard": "Hard",
    "expert": "Expert",
    "expertplus": "ExpertPlus",
    "expert+": "ExpertPlus",
    "expert_plus": "ExpertPlus",
    "expertplusplus": "ExpertPlus",
    "balanced": "Normal",
}


@dataclass
class SongMeta:
    """一首歌的元数据（UI/数据库直接使用）。"""

    folder_name: str
    mtime: float = 0.0
    title: str = ""
    subtitle: str = ""
    author: str = ""
    version: str = ""
    bpm: float = 0.0
    duration: float = 0.0
    difficulties: tuple[str, ...] = field(default_factory=tuple)
    cover_file: str = ""
    parse_error: str = ""

    # ---------- 展示辅助 ----------
    @property
    def display_title(self) -> str:
        return self.title or self.folder_name

    @property
    def display_author(self) -> str:
        return self.author or "未知作者"

    @property
    def display_bpm(self) -> str:
        if not self.bpm:
            return "未知"
        return str(int(self.bpm)) if float(self.bpm).is_integer() else f"{self.bpm:g}"

    @property
    def display_duration(self) -> str:
        if not self.duration or self.duration <= 0:
            return "未知"
        total = int(round(self.duration))
        return f"{total // 60}:{total % 60:02d}"

    @property
    def difficulty_text(self) -> str:
        return ", ".join(self.difficulties) if self.difficulties else "未知"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_str(data: Mapping[str, Any], *paths: str) -> str:
    """按 "a.b.c" 形式的路径依次取第一个非空字符串。"""
    for path in paths:
        node: Any = data
        for part in path.split("."):
            if not isinstance(node, Mapping):
                node = None
                break
            node = node.get(part)
        if isinstance(node, str) and node.strip():
            return node.strip()
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            return str(node)
    return ""


def _first_float(data: Mapping[str, Any], *paths: str) -> float:
    for path in paths:
        node: Any = data
        for part in path.split("."):
            if not isinstance(node, Mapping):
                node = None
                break
            node = node.get(part)
        if isinstance(node, bool):
            continue
        if isinstance(node, (int, float)):
            return float(node)
        if isinstance(node, str):
            try:
                return float(node.strip())
            except ValueError:
                continue
    return 0.0


def normalize_difficulty(raw: str) -> str:
    key = (raw or "").strip().lower().replace(" ", "")
    return _DIFFICULTY_ALIASES.get(key, raw.strip() if raw else "")


def _collect_difficulties(data: Mapping[str, Any]) -> tuple[str, ...]:
    """从所有已知结构中收集难度，去重并按 Easy→ExpertPlus 排序。"""
    found: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            name = normalize_difficulty(value)
            if name and name not in found:
                found.append(name)

    # v2 / v3：_difficultyBeatmapSets[]._difficultyBeatmaps[]._difficulty
    for sets_key in ("_difficultyBeatmapSets", "difficultyBeatmapSets"):
        for beatmap_set in _as_iterable(data.get(sets_key)):
            for key in ("_difficultyBeatmaps", "difficultyBeatmaps"):
                for beatmap in _as_iterable(_as_mapping(beatmap_set).get(key)):
                    node = _as_mapping(beatmap)
                    add(node.get("_difficulty"))
                    add(node.get("difficulty"))

    # v4：顶层 difficultyBeatmaps[].difficulty
    for beatmap in _as_iterable(data.get("difficultyBeatmaps")):
        node = _as_mapping(beatmap)
        add(node.get("difficulty"))
        add(node.get("_difficulty"))

    # 老版本 v1：_difficultyLevels[].difficulty
    for beatmap in _as_iterable(data.get("_difficultyLevels")):
        add(_as_mapping(beatmap).get("difficulty"))

    ordered = [d for d in DIFFICULTY_ORDER if d in found]
    ordered.extend(d for d in found if d not in ordered)
    return tuple(ordered)


def _as_iterable(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple)):
        return value
    return ()


def parse_info_file(
    data: Mapping[str, Any] | None,
    folder_name: str,
    mtime: float = 0.0,
    cover_file: str = "",
) -> SongMeta:
    if not isinstance(data, Mapping):
        return SongMeta(folder_name=folder_name, mtime=mtime, parse_error="no-info")

    meta = SongMeta(folder_name=folder_name, mtime=mtime)

    # 版本：v2/v3 是 _version，v4 是 version，另有 _version 为 "2.0.0" 等
    meta.version = _first_str(data, "_version", "version", "song.version")

    # 标题 / 副标题 / 作者：兼容 v2/v3 的下划线字段与 v4 的嵌套结构
    meta.title = _first_str(
        data, "_songName", "song.title", "title", "_songNameShort", "songName"
    )
    meta.subtitle = _first_str(data, "_songSubName", "song.subTitle", "subTitle", "song.subtitle")
    meta.author = _first_str(
        data, "_songAuthorName", "song.author", "author", "_levelAuthorName", "levelAuthorName"
    )

    meta.bpm = _first_float(data, "_beatsPerMinute", "audio.bpm", "bpm", "song.bpm")
    meta.duration = _first_float(data, "audio.songDuration", "_songDuration", "song.duration")

    meta.cover_file = _first_str(data, "_coverImageFilename", "coverImageFilename") or cover_file
    meta.difficulties = _collect_difficulties(data)

    if not meta.title:
        meta.title = folder_name
    return meta
