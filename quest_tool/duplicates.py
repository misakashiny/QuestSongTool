"""重复歌曲检测（ROADMAP D3）。

清歌单用过：一堆歌里经常混着"同一首歌装了两遍"——作者下了两次、
整合包自带一份自己又装一份、改了个名字又进来一份。

**为什么单独成模块**：这是纯逻辑（给定一批 `SongMeta` → 给出重复分组），
不碰文件系统也不碰 UI，所以可以测得很彻底 —— 而"判重规则"恰恰是最容易
凭感觉写错、又最难在界面上发现的地方。

## 判重规则

按"严 → 松"分三档，**默认只用最严的两档**（宁可漏报，不要误报 ——
误报会让用户删掉其实不同的歌，那是数据损失）：

| 档位 | 条件 | 说明 |
|---|---|---|
| `exact` | **标题 + 作者都相同**（忽略大小写/空白） | 最可靠。基本就是「同一首歌装了两遍」 |
| `same_title` | 标题相同、作者不同 | 常见于"不同版本的同一首歌"（不同 mapper）。**要提示用户别乱删** |
| `same_author_bpm` | 作者 + BPM 都相同 | 最松，误报率明显更高，**默认关闭**，需要显式开启 |

标题比对会做几项归一化，全是实测会遇到的情况：

* **忽略大小写与首尾空白**；
* **全角/半角统一**（`（）` vs `()`、全角空格）；
* **去掉常见后缀噪音**：`(feat. xxx)`、`[HD]`、`- Expert+` 这类；
  否则"同一首歌的两个版本"会因为标题尾巴不同而漏掉。

BPM 比对留 0.5 的容差 —— info.dat 里 `_beatsPerMinute` 有写成
`174` / `174.0` / `173.99` 的，精确相等会漏。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .logging_setup import get_logger
from .songs import SongMeta

logger = get_logger("duplicates")

#: BPM 比对容差（见模块注释）
BPM_TOLERANCE = 0.5

#: 可用的匹配档位（从严到松）
MATCH_LEVELS: tuple[str, ...] = ("exact", "same_title", "same_author_bpm")

#: 默认启用的档位：**不含**最松的那档
DEFAULT_LEVELS: tuple[str, ...] = ("exact", "same_title")

#: 标题尾部要剥掉的噪音。都是实测里真的会出现的东西：
#: 谱师/整合包习惯往标题里塞难度、画质、(feat. xxx) 之类。
_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[\(\[（【]\s*(feat|ft)\.?[^)\]）】]*[\)\]）】]", re.I),
    re.compile(r"[\(\[（【]\s*(hd|4k|uhd|高清)\s*[\)\]）】]", re.I),
    re.compile(r"[\(\[（【]\s*(expert\+?|easy|normal|hard|expertplus)\s*[\)\]）】]", re.I),
    # 版本标记：(Full)、[完整版]、- Full Version 之类。
    # 同一首歌的「完整版 / 剪辑版」经常被当成两首歌装着，标题差异只有这一截。
    re.compile(
        r"[\(\[（【]\s*(full|full ?version|完整版|剪辑版|short ?version|short|tv ?size)"
        r"\s*[\)\]）】]",
        re.I,
    ),
    re.compile(r"\s*[-–—]\s*(full|full ?version|完整版|short ?version)\s*$", re.I),
    re.compile(r"\s*[-–—]\s*(expert\+?|easy|normal|hard)\s*$", re.I),
    re.compile(r"[\(\[（【]\s*\)\]）】]"),          # 空括号
)


def normalize_title(title: str) -> str:
    """把标题归一化到"可比较"的形式。

    做四件事：全角转半角 → 剥掉尾巴噪音 → 去掉所有空白与标点 → 转小写。
    去掉标点是有意的：`Song - Title` 与 `Song Title` 经常是同一首歌。
    """
    if not title:
        return ""
    # NFKC 会把全角字母/数字/括号/空格统一成半角
    text = unicodedata.normalize("NFKC", title)
    for pattern in _NOISE_PATTERNS:
        text = pattern.sub(" ", text)
    # 只留下字母、数字与中日韩字符 —— 标点差异不该造成漏判
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text, flags=re.UNICODE)
    return text.casefold()


def normalize_author(author: str) -> str:
    """作者归一化：同样做全角/大小写/空白归一（作者名里很少有意义标点）。"""
    if not author:
        return ""
    text = unicodedata.normalize("NFKC", author)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _bpm_close(first: float, second: float) -> bool:
    if not first or not second:
        return False
    return abs(float(first) - float(second)) <= BPM_TOLERANCE


@dataclass
class DuplicateGroup:
    """一组被判为"重复"的歌。"""

    #: 命中的档位（`exact` / `same_title` / `same_author_bpm`）
    level: str
    #: 这一组的代表键（标题或"作者+BPM"，用来在界面上显示）
    label: str
    #: 组内成员（按文件夹名排序）
    members: list[SongMeta] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def folder_names(self) -> list[str]:
        return [m.folder_name for m in self.members]

    @property
    def risky(self) -> bool:
        """这一组是不是"别急着删"。

        `exact` 基本可以放心（同标题同作者几乎就是同一首歌）；
        `same_title` 可能是不同 mapper 的版本、或同名不同曲 —— **要人看一眼**。
        """
        return self.level != "exact"


@dataclass
class DuplicateReport:
    """一次检测的完整结果。"""

    groups: list[DuplicateGroup] = field(default_factory=list)
    #: 参与检测的歌数
    scanned: int = 0
    #: 没有元数据、无法参与判重的歌数（解析失败或还没解析）
    skipped: int = 0

    @property
    def duplicate_count(self) -> int:
        """有多少首"多余"（每组留一首，其余算多余）。"""
        return sum(g.count - 1 for g in self.groups)

    @property
    def group_count(self) -> int:
        return len(self.groups)

    def summary(self) -> str:
        if not self.groups:
            return f"没有发现重复歌曲（检查了 {self.scanned} 首）"
        text = (f"发现 {self.group_count} 组重复，共 {self.duplicate_count} 首多余"
                f"（检查了 {self.scanned} 首）")
        if self.skipped:
            text += f"，另有 {self.skipped} 首没有元数据、未参与判重"
        return text


def _title_key(meta: SongMeta) -> str:
    """判重用标题：优先 info.dat 里的 `_songName`，没有就退回文件夹名。

    退回文件夹名是必要的 —— 有些老谱的 info.dat 里标题是空的，
    但文件夹名往往就是歌名（用户自己整理过）。
    """
    return normalize_title(meta.title) or normalize_title(meta.folder_name)


def find_duplicates(
    songs: Iterable[SongMeta],
    *,
    levels: Sequence[str] = DEFAULT_LEVELS,
) -> DuplicateReport:
    """找出重复歌曲。

    `levels` 默认只含 `exact` 与 `same_title`（不含最松的 `same_author_bpm`，
    见模块注释）。**同一首歌只会出现在一组里** —— 按 levels 的顺序，
    先命中的先分组，避免"一组歌被拆进多个组"造成用户重复劳动。
    """
    active = [level for level in levels if level in MATCH_LEVELS]
    report = DuplicateReport()

    metas: list[SongMeta] = []
    for meta in songs:
        # 没有标题也没有文件夹名的没法判重；解析失败但标题读出来的仍然可以
        if not _title_key(meta):
            report.skipped += 1
            continue
        metas.append(meta)
    report.scanned = len(metas)

    #: 已经分过组的文件夹名 —— 保证"一首歌只出现在一组里"
    assigned: set[str] = set()

    for level in active:
        buckets: dict[str, list[SongMeta]] = {}
        for meta in metas:
            if meta.folder_name in assigned:
                continue
            if level == "exact":
                # 标题 + 作者都相同
                key = f"{_title_key(meta)}\x00{normalize_author(meta.author)}"
                if not normalize_author(meta.author):
                    # 作者读不出来时，"exact" 退化成"同标题" —— 那属于下一档，
                    # 不该在这里冒充"铁证"（很多歌的 _songAuthorName 是空的）
                    continue
            elif level == "same_title":
                key = _title_key(meta)
            elif level == "same_author_bpm":
                author = normalize_author(meta.author)
                if not author or not meta.bpm:
                    continue
                key = f"{author}\x00{round(float(meta.bpm) / BPM_TOLERANCE)}"
            else:  # pragma: no cover - active 已过滤
                continue
            buckets.setdefault(key, []).append(meta)

        for key, members in buckets.items():
            if len(members) < 2:
                continue
            # same_author_bpm 的分桶是按 BPM 取整分的，桶内还要再按容差细分，
            # 否则 174 与 174.4 会被分到不同桶、而 174 与 175 又可能同桶
            if level == "same_author_bpm":
                for cluster in _cluster_by_bpm(members):
                    if len(cluster) >= 2:
                        _append_group(report, level, cluster, assigned)
            else:
                _append_group(report, level, members, assigned)

    report.groups.sort(key=lambda g: (-g.count, g.label))
    logger.info("重复检测：%s", report.summary())
    return report


def _cluster_by_bpm(members: list[SongMeta]) -> list[list[SongMeta]]:
    """按 BPM 容差把成员细分（单链聚类：相邻差的都算一组）。"""
    ordered = sorted(members, key=lambda m: float(m.bpm))
    clusters: list[list[SongMeta]] = []
    for meta in ordered:
        if clusters and _bpm_close(float(clusters[-1][-1].bpm), float(meta.bpm)):
            clusters[-1].append(meta)
        else:
            clusters.append([meta])
    return clusters


def _append_group(report: DuplicateReport, level: str, members: list[SongMeta],
                  assigned: set[str]) -> None:
    if len(members) < 2:
        return
    ordered = sorted(members, key=lambda m: m.folder_name)
    if level == "same_author_bpm":
        label = f"{ordered[0].author or '未知作者'} · {ordered[0].display_bpm} BPM"
    else:
        label = ordered[0].display_title
    report.groups.append(DuplicateGroup(level=level, label=label, members=ordered))
    assigned.update(m.folder_name for m in ordered)


def level_label(level: str) -> str:
    """档位的中文说明（给界面用）。"""
    return {
        "exact": "完全相同（标题 + 作者都一样）",
        "same_title": "标题相同（作者不同，可能是不同版本）",
        "same_author_bpm": "作者与 BPM 相同（较宽松，可能有误判）",
    }.get(level, level)
