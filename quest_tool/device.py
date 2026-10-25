"""设备发现：找出 Quest 上 CustomLevels 的可用位置。

原版 `detect_quest_paths()` 的错误前提：它用 `GetLogicalDrives()` + `GetDriveTypeW()==2`
(DRIVE_REMOVABLE) 去找 Quest。**Quest 3 是 MTP 便携设备，根本没有盘符**，
所以这段代码永远找不到设备 —— 这才是"插入 Quest 后选不了文件夹"的第一层原因；
第二层原因是 `filedialog.askdirectory` 也对 MTP 目录无效。

本模块按可靠性从高到低依次探测：
1. **盘符**：Quest 被某些工具（或用了 `MTPdrive` 之类）挂成盘符时可用；
2. **adb**：最推荐 —— 可读可写、批量操作快，还能正确读取 Android 11+ 的 Android/data；
3. **MTP(Shell 命名空间)**：无需装任何东西，能看到目录树，但复制/读取必须另想办法，
   因此只作为"选择与显示"的兜底，并且会明确告知用户。
"""

from __future__ import annotations

import os
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import adb as adb_mod
from .constants import (
    CUSTOM_LEVELS_RELATIVE,
    MTP_CUSTOM_LEVELS_RELATIVE,
    QUEST_FOLDER_HINTS,
)
from .fsapi import Location, is_selector_like
from .logging_setup import get_logger

logger = get_logger("device")


@dataclass
class QuestCandidate:
    """一个候选位置。"""

    kind: str  # "drive" | "adb" | "mtp"
    display: str  # 给用户看的路径
    usable: bool  # 是否可用于读写（对比/复制/删除）
    location: Location | None = None
    note: str = ""
    exists: bool = True

    @property
    def kind_label(self) -> str:
        return {"drive": "盘符", "adb": "ADB", "mtp": "MTP 设备"}.get(self.kind, self.kind)


@dataclass
class DeviceReport:
    """一次完整探测的结果。"""

    candidates: list[QuestCandidate] = field(default_factory=list)
    adb_path: str = ""
    adb_error: str = ""
    adb_hint: str = ""
    shell_available: bool = False
    mtp_devices: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> list[QuestCandidate]:
        return [c for c in self.candidates if c.usable]

    def best(self, prefer: str = "") -> QuestCandidate | None:
        """按可靠性挑一个可用候选。

        默认顺序 **adb > 盘符 > MTP**（adb 可读写、能删、批量最快；MTP 最慢且不能删）。
        `prefer` 是用户在设置里指定的偏好（`drive` / `adb` / `mtp`），给了就把它排到最前。

        注意 `scan_quest` 收集候选的顺序是"盘符 → adb → MTP"，所以调用方**不要**
        直接拿 `candidates[0]` 当"最好的"（那会优先选到盘符）—— 用这个方法。
        """
        order = ["adb", "drive", "mtp"]
        if prefer in order:
            order.remove(prefer)
            order.insert(0, prefer)
        for kind in order:
            for candidate in self.candidates:
                if candidate.kind == kind and candidate.usable:
                    return candidate
        return None


# --------------------------------------------------------------------------------------
# 1) 盘符探测
# --------------------------------------------------------------------------------------
def drive_letters() -> list[str]:
    if os.name != "nt":
        return [f"{m}/" for m in ("/media", "/mnt", "/Volumes") if os.path.isdir(m)]
    import ctypes

    drives: list[str] = []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        return []
    for letter in string.ascii_uppercase:
        if bitmask & 1:
            drives.append(f"{letter}:\\")
        bitmask >>= 1
    return drives


def _looks_like_quest_root(name: str) -> bool:
    lowered = name.lower()
    if any(hint.lower() in lowered for hint in QUEST_FOLDER_HINTS):
        return True
    return any(token in lowered for token in ("quest", "oculus", "meta", "android", "sdcard"))


def find_custom_levels_under(root: str | Path, max_depth: int = 6) -> list[str]:
    """在给定根目录下寻找 CustomLevels —— 只做有限深度扫描，避免卡死。

    ``max_depth`` 默认 6 是**按真实设备树量出来的**，不能随意调小：
    设备根 → 内部共享存储空间 → Android/data → 包名 → files → CustomLevels
    （ModData/SongCore 那条同样 6 层）正好是 6 层，设成 4 会一层都到不了，
    表现为"插着设备却什么也找不到"。
    """
    root_path = Path(root)
    found: list[str] = []
    if not root_path.is_dir():
        return found

    def probe(path: Path) -> None:
        if path.name == "CustomLevels" and path.is_dir():
            if str(path) not in found:
                found.append(str(path))

    try:
        # 常见位置优先，直接命中时无需扫描
        direct: list[Path] = [
            root_path / CUSTOM_LEVELS_RELATIVE,
            root_path / "CustomLevels",
            root_path / "ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels",
        ]
        # 设备根下通常还有一层"内部共享存储空间"（各系统语言叫法不同），
        # 补上带该前缀的写法，命中时不必再走深度扫描。
        for hint in QUEST_FOLDER_HINTS:
            direct.append(root_path / hint / CUSTOM_LEVELS_RELATIVE)
            direct.append(
                root_path / hint / "ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels"
            )
        for candidate in direct:
            probe(candidate)
        if found:
            return found

        base_depth = len(root_path.parts)
        for current, dirs, _files in os.walk(root_path):
            depth = len(Path(current).parts) - base_depth
            if depth >= max_depth:
                dirs[:] = []
                continue
            # 跳过明显无关的大目录，避免在 U 盘/手机上遍历整个存储
            dirs[:] = [
                d
                for d in dirs
                if d.lower() not in ("windows", "$recycle.bin", "system volume information")
            ]
            if "CustomLevels" in dirs:
                probe(Path(current) / "CustomLevels")
                dirs.remove("CustomLevels")
    except (OSError, PermissionError) as exc:
        logger.debug("扫描 %s 失败: %s", root, exc)
    return found


def probe_drives() -> list[QuestCandidate]:
    candidates: list[QuestCandidate] = []
    for drive in drive_letters():
        try:
            entries = os.listdir(drive)
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if not _looks_like_quest_root(entry):
                continue
            for path in find_custom_levels_under(os.path.join(drive, entry)):
                candidates.append(
                    QuestCandidate(
                        kind="drive",
                        display=path,
                        usable=True,
                        location=Location(kind="local", root=path, label=f"Quest（盘符 {drive}）"),
                        note="以盘符方式挂载，可用",
                    )
                )
    return candidates


# --------------------------------------------------------------------------------------
# 2) adb 探测
# --------------------------------------------------------------------------------------
def probe_adb(adb_path: str = "", serial: str = "", extra_roots: Iterable[str] = ()) -> tuple[
    list[QuestCandidate], str, str
]:
    """返回 (候选列表, 错误信息, 提示)。"""
    path = adb_mod.find_adb(adb_path)
    if not path:
        return [], "未找到 adb", "安装 Android Platform Tools 后可获得最快最稳的连接方式"
    bridge = adb_mod.AdbBridge(path, serial)
    try:
        device = bridge.ensure_device()
    except adb_mod.AdbError as exc:
        return [], str(exc), "在头显中允许 USB 调试，并确认使用的是数据线而非仅充电线"

    candidates: list[QuestCandidate] = []
    roots = list(adb_mod.QUEST_CUSTOMLEVELS_CANDIDATES) + list(extra_roots)
    # 再用 find 兜一层：不同整合包/版本会把 CustomLevels 放在别处。
    #
    # 两个必须踩过的坑（真机 Quest 3 / Android 14 / toybox 0.8.9 实测）：
    # 1. **不能从 `/sdcard` 起搜** —— 它是符号链接（-> /storage/self/primary），
    #    toybox find 不跟随后返回 0 行（加 `-H` 也一样）。v14.0 就是这么写的，
    #    等于这段兜底从来没生效过，注释里承诺的"放在别处也能找到"是空话。
    #    改从真实路径 `/storage/emulated/0` 起搜（实测 0.74s / 3 条结果）。
    # 2. 用 `cd <根> && find .` 更稳也更快（0.32s），且不受符号链接影响；
    #    结果形如 `./ModData/...`，再补回根前缀即可。
    for search_root in ("/storage/emulated/0", "/sdcard"):
        try:
            found = bridge.shell(
                f"cd {search_root} && find . -maxdepth 7 -type d "
                "\\( -name CustomLevels -o -name CustomWIPLevels \\) 2>/dev/null",
                timeout=60,
            )
        except adb_mod.AdbError as exc:
            logger.debug("find 搜索 CustomLevels 失败（%s）: %s", search_root, exc)
            continue
        for line in found.splitlines():
            line = line.strip()
            if not line.startswith("./"):
                continue
            absolute = f"{search_root}/{line[2:]}"
            if absolute not in roots:
                roots.append(absolute)
        if len(roots) > len(adb_mod.QUEST_CUSTOMLEVELS_CANDIDATES) + len(extra_roots):
            break  # 这一轮已经搜到东西了，不必再试下一个根

    for root in roots:
        try:
            if not bridge.is_dir(root):
                continue
        except adb_mod.AdbError:
            continue
        label = f"{device.model or 'Quest'} · ADB"
        candidates.append(
            QuestCandidate(
                kind="adb",
                display=root,
                usable=True,
                location=Location(
                    kind="adb",
                    root=root,
                    label=label,
                    serial=device.serial,
                ),
                note=f"通过 adb 连接（{device.serial}），读写与批量复制速度最快",
            )
        )
    if not candidates:
        # 设备在，但没找到目录：给出明确提示，并让用户手填
        candidates.append(
            QuestCandidate(
                kind="adb",
                display="/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels",
                usable=False,
                exists=False,
                note="设备已连接，但未在常见位置找到 CustomLevels —— 请先在头显里启动一次 Beat Saber",
            )
        )
    return candidates, "", ""


# --------------------------------------------------------------------------------------
# 3) MTP（Shell 命名空间）探测
# --------------------------------------------------------------------------------------
def probe_mtp(deep: bool = True) -> tuple[list[QuestCandidate], list[str], str]:
    """返回 (候选列表, 设备名列表, 错误信息)。"""
    from .device_shell import ShellBridge

    bridge = ShellBridge()
    if not bridge.available:
        return [], [], "PowerShell 不可用，无法枚举便携设备"
    try:
        devices = bridge.portable_devices()
    except RuntimeError as exc:
        return [], [], str(exc)

    names = [d.name for d in devices]
    if not devices:
        return [], names, ""

    candidates: list[QuestCandidate] = []
    for device in devices:
        if not deep:
            candidates.append(
                QuestCandidate(
                    kind="mtp",
                    display=device.name,
                    usable=False,
                    note="MTP 设备：只能浏览，无法直接读写（请安装 adb 以获得完整功能）",
                )
            )
            continue
        # 逐层下钻到内部共享存储（不同系统语言下名字不同，逐个试）
        node = None
        for hint in QUEST_FOLDER_HINTS:
            try:
                node = bridge.find_path([device.name, hint])
            except RuntimeError:
                node = None
            if node is not None:
                break

        if node is None:
            candidates.append(
                QuestCandidate(
                    kind="mtp",
                    display=device.name,
                    usable=False,
                    note="已识别到设备，但无法自动定位内部存储（设备可能处于锁屏/未解锁状态）",
                )
            )
            continue

        base = node.name
        levels = None
        tried: list[str] = []
        # 现版 SongCore 的 ModData 路径优先，其次历史 Android/data 路径
        for relative in MTP_CUSTOM_LEVELS_RELATIVE:
            segments = [device.name, base, *relative]
            tried.append("/".join(relative))
            try:
                levels = bridge.find_path(segments)
            except RuntimeError:
                levels = None
            if levels is not None:
                break

        # 兜底：直接相对设备根找（有些系统内部存储根名不同）
        if levels is None:
            for relative in MTP_CUSTOM_LEVELS_RELATIVE:
                try:
                    levels = bridge.find_path([device.name, *relative])
                except RuntimeError:
                    levels = None
                if levels is not None:
                    break

        if levels is None:
            path_text = f"{device.name}\\{base}"
            candidates.append(
                QuestCandidate(
                    kind="mtp",
                    display=path_text,
                    usable=False,
                    note=(
                        "已进入设备，但没有找到 CustomLevels / CustomWIPLevels。"
                        "请先在头显里启动一次 Beat Saber（并安装 SongCore），让它创建目录。"
                    ),
                )
            )
        else:
            candidates.append(
                QuestCandidate(
                    kind="mtp",
                    display=f"{device.name}\\{base}\\{levels.shell_path.rsplit(chr(92), 1)[-1]}",
                    usable=True,
                    exists=True,
                    location=Location(
                        kind="mtp",
                        root=levels.shell_path,
                        label=f"{device.name}（MTP）",
                        shell_path=levels.shell_path,
                    ),
                    note=(
                        "MTP 模式：可用（程序会把歌曲目录镜像到本地再解析）。"
                        "首次镜像较慢；装 adb 可以显著加速并支持在设备上直接删除。"
                    ),
                )
            )
    return candidates, names, ""


# --------------------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------------------
def scan_quest(adb_path: str = "", serial: str = "", include_mtp: bool = True, deep_mtp: bool = True) -> DeviceReport:
    """完整探测一次，供 UI 在后台线程调用。"""
    report = DeviceReport()
    report.adb_path = adb_mod.find_adb(adb_path) or ""

    try:
        report.candidates.extend(probe_drives())
    except Exception as exc:  # 探测失败不应阻塞其它方式
        logger.warning("盘符探测异常: %s", exc)

    adb_candidates, adb_error, adb_hint = probe_adb(adb_path, serial)
    report.candidates.extend(adb_candidates)
    report.adb_error = adb_error
    report.adb_hint = adb_hint

    if include_mtp:
        from .device_shell import ShellBridge

        report.shell_available = ShellBridge().available
        mtp_candidates, names, mtp_error = probe_mtp(deep=deep_mtp)
        report.candidates.extend(mtp_candidates)
        report.mtp_devices = names
        if mtp_error:
            report.notes.append(f"MTP 枚举: {mtp_error}")

    if not report.candidates:
        report.notes.append(
            "没有找到 Quest 位置。常见原因：设备未连接/未解锁、未开启开发者模式、"
            "或在 Beat Saber 里还没有下载过自定义歌曲。"
        )
    return report


def describe_path_kind(path: str) -> str:
    """给用户输入的路径判断类型，用于友好提示。"""
    if not path:
        return "空"
    if is_selector_like(path):
        return "Windows Shell 命名空间（MTP 设备，程序无法用 os 直接读写）"
    if path.startswith("adb:"):
        return "ADB 路径"
    if os.name == "nt" and len(path) > 1 and path[1] == ":":
        return "本地盘符路径"
    if path.startswith("/"):
        return "类 Unix 路径（可能是 adb 目标）"
    return "相对路径（不建议）"


# --------------------------------------------------------------------------------------
# 4) 设备端 shell 能力探测（--doctor 用）
# --------------------------------------------------------------------------------------
@dataclass
class Capability:
    """一项设备端 shell 能力的探测结果。"""

    name: str
    ok: bool
    why: str = ""
    detail: str = ""


#: (能力名, 命令模板, 这项能力用来干什么)。全部只读。
#: `{root}` = 歌曲根目录，`{probe}` = 一定有文件的目录。
#:
#: 为什么要探测：真机上已经踩过两次"**设备端命令本身是坏的**"——
#:   * v14.4：`find -printf '%y'` 坏掉（单独用输出 0 行、组合时把整个输出截成 1 行），
#:     导致每个目录只看到 1 个文件 → 封面随机不显示；
#:   * v14.2：`find /sdcard ...` 因为 `/sdcard` 是符号链接而返回 0 行 → 兜底搜索从未生效。
#: 这两类问题**离线测试发现不了**（假 adb 是我们自己用 Python 写的，实现得"太正确"），
#: 所以让 `--doctor` 把设备能力直接摊开，用户/接手人一眼就能看到哪条命令在这台设备上不可用。
_CAPABILITY_PROBES: tuple[tuple[str, str, str], ...] = (
    ("ls -1p 列目录", "cd {root} && ls -1p 2>/dev/null", "扫描歌单 / 列歌曲文件"),
    ("find -printf %f", "cd {probe} && find . -maxdepth 2 -type f -printf '%f\\n' 2>/dev/null", "文件名"),
    ("find -printf %T@", "cd {probe} && find . -maxdepth 2 -type f -printf '%T@\\n' 2>/dev/null", "mtime（增量扫描）"),
    ("find -printf %s", "cd {probe} && find . -maxdepth 2 -type f -printf '%s\\n' 2>/dev/null", "文件大小（复制后校验）"),
    ("find -printf %y", "cd {probe} && find . -maxdepth 2 -printf '%y\\n' 2>/dev/null", "文件类型（v14.4 实测坏过）"),
    ("find -maxdepth", "cd {probe} && find . -maxdepth 1 -type d 2>/dev/null", "限制扫描深度"),
    ("find 从 /sdcard 起搜", "find /sdcard -maxdepth 2 -type d 2>/dev/null", "兜底搜索（v14.2 实测 0 行）"),
    ("du -sk", "du -sk {root} 2>/dev/null", "占用统计"),
)


def probe_capabilities(adb_path: str = "", root: str = "") -> list[Capability]:
    """逐项探测设备端 shell 的能力（只读）。没有设备时返回空列表。"""
    path = adb_mod.find_adb(adb_path)
    if not path:
        return []
    bridge = adb_mod.AdbBridge(path)
    try:
        bridge.ensure_device()
    except adb_mod.AdbError:
        return []

    if not root:
        candidates, _err, _hint = probe_adb(adb_path)
        usable = [c for c in candidates if c.usable]
        root = usable[0].display if usable else "/sdcard"

    # 探测目录必须**有内容**，否则"命令坏了"和"目录是空的"分不出来
    probe = root
    try:
        if not bridge.shell(f"ls -1 {adb_mod._quote(root)} 2>/dev/null | head -1", check=False).strip():
            probe = "/sdcard/Android"
    except adb_mod.AdbError:
        probe = "/sdcard/Android"

    results: list[Capability] = []
    for name, template, why in _CAPABILITY_PROBES:
        command = template.format(root=adb_mod._quote(root), probe=adb_mod._quote(probe))
        try:
            out = bridge.shell(command, timeout=30, check=False).strip()
            detail = out.splitlines()[0][:48] if out else ""
            results.append(Capability(name=name, ok=bool(out), why=why, detail=detail))
        except adb_mod.AdbError as exc:
            results.append(Capability(name=name, ok=False, why=why, detail=str(exc)[:48]))
    return results

