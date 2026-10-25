r"""quest_paths.py —— Quest 歌曲目录的统一查找入口。

对外只有一个主入口 :func:`find_quest_customlevels`，它按顺序尝试三条路线：

===========  ==================================================  ==================
顺序          路线                                                 是否可直接 os 访问
===========  ==================================================  ==================
(a)          真实盘符路径（``GetLogicalDrives`` + ``GetDriveTypeW``）  是
(b)          adb（``adb shell ls`` / ``adb pull`` / ``adb push``）      否（走 adb）
(c)          Shell COM 枚举便携设备（MTP）                              否（只有解析名）
===========  ==================================================  ==================

每条路线产出一个或多个 :class:`QuestPath`，带 ``.kind`` / ``.display`` / ``.usable``。

即使设备没连接、adb 没安装、pywin32 缺失，本模块也 **不会抛异常**，
只会返回空列表或带说明的结果。
"""

from __future__ import annotations

import ctypes
import os
import string
import sys
from dataclasses import dataclass, field

import adb_bridge
import shell_mtp

# ---------------------------------------------------------------------------
# 候选相对路径
# ---------------------------------------------------------------------------

#: Quest 内部存储里 CustomLevels 的相对路径，**按优先级排列**。
#:
#: 顺序依据（2024-2025 年实测/上游源码）：
#:
#: 1. ``ModData/.../SongCore/CustomLevels``    —— Beat Saber 1.35+ / SongCore 时代
#: 2. ``ModData/.../SongLoader/CustomLevels``  —— 旧 SongLoader
#: 3. ``Android/data/.../files/CustomLevels``  —— 更早的 BMBF 时代，仅作兼容
#:
#: 出处：Quest-SongCore ``include/config.hpp``、QuestSongManager、BMBF 技术 wiki。
CUSTOMLEVELS_RELATIVE: tuple[tuple[str, ...], ...] = (
    ("ModData", "com.beatgames.beatsaber", "Mods", "SongCore", "CustomLevels"),
    ("ModData", "com.beatgames.beatsaber", "Mods", "SongLoader", "CustomLevels"),
    ("Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"),
)

#: 便携设备内部存储的名字（中英）
STORAGE_NAMES: tuple[str, ...] = (
    "内部共享存储空间",
    "内部存储",
    "Internal shared storage",
    "Internal storage",
    "内部存储空间",
)


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------


@dataclass
class QuestPath:
    """一条候选的 Quest 歌曲目录。"""

    kind: str
    """来源：``"drive"`` | ``"adb"`` | ``"mtp"``。"""

    display: str
    """给用户看的路径字符串。

    * ``drive`` —— 真实盘符路径，如 ``F:\\ModData\\...\\CustomLevels``
    * ``adb``   —— 设备内路径，如 ``/sdcard/ModData/.../CustomLevels``
    * ``mtp``   —— Shell 友好路径，如 ``Quest 3\\内部共享存储空间\\ModData\\...``
    """

    usable: bool
    """是否能直接交给 ``os.listdir`` / ``shutil.copy`` 使用。

    只有 ``kind == "drive"`` 才可能为 True。
    """

    shell_path: str = ""
    """Shell 解析名（MTP 路线才有），可用于 Shell COM 操作。"""

    adb_serial: str = ""
    """adb 设备序列号（adb 路线才有）。"""

    remote_path: str = ""
    """设备上的绝对路径（adb 路线才有）。"""

    exists: bool = False
    """是否确认存在。"""

    note: str = ""
    """中文补充说明（例如为什么不可直接用 os）。"""

    source: str = ""
    """更细的来源标记，如 ``"GetLogicalDrives"`` / ``"adb shell ls"`` / ``"Shell.Application"``。"""

    @property
    def is_filesystem(self) -> bool:
        return self.kind == "drive"

    def __str__(self) -> str:
        return f"[{self.kind}] {self.display} (usable={self.usable})"


@dataclass
class QuestDiagnostics:
    """体检结果，用于在界面上给用户解释「为什么找不到」。"""

    drives_scanned: list[str] = field(default_factory=list)
    removable_drives: list[str] = field(default_factory=list)
    adb_path: str | None = None
    adb_devices: list[str] = field(default_factory=list)
    shell_backend: str = ""
    portable_devices: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        lines = ["Quest 路径体检："]
        lines.append(f"  本机盘符       : {', '.join(self.drives_scanned) or '无'}")
        lines.append(f"  可移动盘符     : {', '.join(self.removable_drives) or '无'}")
        lines.append(f"  adb 可执行文件 : {self.adb_path or '未找到'}")
        lines.append(f"  adb 设备       : {', '.join(self.adb_devices) or '无'}")
        lines.append(f"  Shell 后端     : {self.shell_backend or '不可用'}")
        lines.append(f"  便携设备(MTP)  : {', '.join(self.portable_devices) or '无'}")
        for n in self.notes:
            lines.append(f"  备注           : {n}")
        return os.linesep.join(lines)


# ---------------------------------------------------------------------------
# 路线 (a)：真实盘符
# ---------------------------------------------------------------------------

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

_DRIVE_TYPE_NAMES = {
    0: "未知", 1: "无根目录", 2: "可移动", 3: "固定", 4: "网络", 5: "光驱", 6: "内存盘",
}


def list_logical_drives() -> list[str]:
    """返回本机所有盘符，例如 ``['C:\\\\', 'D:\\\\']``。"""
    if os.name != "nt":
        return []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return []
    out = []
    for letter in string.ascii_uppercase:
        if bitmask & 1:
            out.append(f"{letter}:\\")
        bitmask >>= 1
    return out


def drive_type(drive: str) -> int:
    """返回 ``GetDriveTypeW`` 的结果（2 = 可移动）。"""
    if os.name != "nt":
        return 0
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive)))
    except Exception:
        return 0


def drive_type_name(drive: str) -> str:
    return _DRIVE_TYPE_NAMES.get(drive_type(drive), "未知")


def _walk_for_customlevels(root: str, max_depth: int = 3) -> list[str]:
    """在 ``root`` 下按 :data:`CUSTOMLEVELS_RELATIVE` 直接拼路径并验证。

    不做全盘 os.walk（那样在慢速移动盘上会卡死界面），只检查固定候选路径。
    """
    found: list[str] = []
    for rel in CUSTOMLEVELS_RELATIVE:
        cand = os.path.join(root, *rel)
        try:
            if os.path.isdir(cand):
                found.append(cand)
        except OSError:
            continue
    return found


def find_on_drives() -> list[QuestPath]:
    """路线 (a)：在所有盘符（尤其是可移动盘）上找 CustomLevels。

    会扫描：

    * 每个可移动盘（``DRIVE_REMOVABLE``）的根目录
    * 每个可移动盘根目录下的第一层子目录（形如 ``F:\\内部共享存储空间\\...``）
    * 固定盘上不搜索（避免耗时），但会检查环境变量 ``QUEST_DRIVE``。
    """
    results: list[QuestPath] = []

    for drive in list_logical_drives():
        dtype = drive_type(drive)
        if dtype != DRIVE_REMOVABLE:
            continue
        # 1) 盘符根目录直接拼候选
        for hit in _walk_for_customlevels(drive):
            results.append(
                QuestPath(
                    kind="drive", display=hit, usable=True, exists=True,
                    source="GetLogicalDrives/GetDriveTypeW",
                    note="真实文件系统路径，可直接用 os / shutil 访问。",
                )
            )
        # 2) 盘符下的第一层目录（设备名那一层）
        try:
            entries = os.listdir(drive)
        except OSError:
            continue
        for entry in entries:
            sub = os.path.join(drive, entry)
            try:
                if not os.path.isdir(sub):
                    continue
            except OSError:
                continue
            for hit in _walk_for_customlevels(sub):
                results.append(
                    QuestPath(
                        kind="drive", display=hit, usable=True, exists=True,
                        source="GetLogicalDrives/GetDriveTypeW",
                        note="真实文件系统路径，可直接用 os / shutil 访问。",
                    )
                )

    # 3) 用户显式指定的盘符
    forced = os.environ.get("QUEST_DRIVE", "").strip()
    if forced:
        for hit in _walk_for_customlevels(forced):
            results.append(
                QuestPath(
                    kind="drive", display=hit, usable=True, exists=True,
                    source="环境变量 QUEST_DRIVE",
                    note="由环境变量 QUEST_DRIVE 指定。",
                )
            )

    return _dedupe(results)


# ---------------------------------------------------------------------------
# 路线 (b)：adb
# ---------------------------------------------------------------------------


def find_via_adb(serial: str | None = None) -> list[QuestPath]:
    """路线 (b)：用 adb 在设备上查找 CustomLevels。"""
    results: list[QuestPath] = []
    if not adb_bridge.adb_available():
        return results

    dev = adb_bridge.quest_device() if serial is None else None
    ser = serial or (dev.serial if dev else None)
    if ser is None:
        return results

    for remote in adb_bridge.QUEST_CUSTOMLEVELS:
        if not adb_bridge.is_dir(ser, remote):
            continue
        results.append(
            QuestPath(
                kind="adb",
                display=remote,
                usable=False,
                remote_path=remote,
                adb_serial=ser,
                exists=True,
                source="adb shell ls",
                note=(
                    "这是设备内路径，不能交给 os / shutil；"
                    "请用 adb_bridge.pull() / push() 读写。"
                ),
            )
        )
    return _dedupe(results)


# ---------------------------------------------------------------------------
# 路线 (c)：Shell COM（MTP）
# ---------------------------------------------------------------------------


def find_via_shell() -> list[QuestPath]:
    """路线 (c)：用 Shell COM 枚举便携设备里的 CustomLevels。"""
    results: list[QuestPath] = []
    for node in shell_mtp.find_customlevels():
        results.append(
            QuestPath(
                kind="mtp",
                display=node.display,
                usable=False,
                shell_path=node.shell_path,
                exists=True,
                source=f"Shell COM ({node.backend})",
                note=(
                    "Shell 命名空间路径（::{GUID}\\...），"
                    "os / shutil / open 都无法使用；只能用 Shell COM 或资源管理器操作。"
                ),
            )
        )
    return _dedupe(results)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def find_quest_customlevels(
    include_adb: bool = True, include_shell: bool = True
) -> list[QuestPath]:
    """按 (a) 盘符 -> (b) adb -> (c) Shell COM 的顺序查找 Quest 的 CustomLevels。

    Args:
        include_adb: 是否尝试 adb 路线（adb 调用较慢，界面可按需关闭）。
        include_shell: 是否尝试 Shell COM 路线。

    Returns:
        :class:`QuestPath` 列表，按路线优先级排列。每条都有 ``.kind`` /
        ``.display`` / ``.usable``。找不到时返回 ``[]``（不抛异常）。
    """
    results: list[QuestPath] = []
    try:
        results.extend(find_on_drives())
    except Exception:
        pass
    if include_adb:
        try:
            results.extend(find_via_adb())
        except Exception:
            pass
    if include_shell:
        try:
            results.extend(find_via_shell())
        except Exception:
            pass
    return _dedupe(results)


def best_quest_customlevels() -> QuestPath | None:
    """返回优先级最高的一条结果（优先 ``usable`` 的，其次 adb，最后 mtp）。"""
    found = find_quest_customlevels()
    if not found:
        return None
    order = {"drive": 0, "adb": 1, "mtp": 2}
    found.sort(key=lambda q: (0 if q.usable else 1, order.get(q.kind, 9)))
    return found[0]


def diagnose() -> QuestDiagnostics:
    """体检：说明为什么找得到 / 找不到。"""
    diag = QuestDiagnostics()
    diag.drives_scanned = list_logical_drives()
    diag.removable_drives = [
        f"{d} ({drive_type_name(d)})" for d in diag.drives_scanned
        if drive_type(d) == DRIVE_REMOVABLE
    ]
    diag.adb_path = adb_bridge.find_adb()
    diag.adb_devices = [d.description for d in adb_bridge.list_devices()]
    diag.shell_backend = shell_mtp.backend_name()
    diag.portable_devices = [n.name for n in shell_mtp.list_portable_devices()]

    if not diag.removable_drives:
        diag.notes.append(
            "没有任何可移动盘符 —— 这是正常的：MTP 便携设备不会分配盘符，"
            "所以「路线 a」注定找不到 Quest。"
        )
    if not diag.adb_path:
        diag.notes.append(
            "未安装 adb。adb 是最推荐的路线（快、支持写入、可脚本化）。"
        )
    elif not diag.adb_devices:
        diag.notes.append(
            "adb 已安装但没有设备。请确认已在头显上开启开发者模式，"
            "并在 USB 连接时于头显内点「允许 USB 调试」。"
        )
    if diag.shell_backend:
        diag.notes.append(
            f"Shell COM 后端可用（{diag.shell_backend}），可以枚举便携设备，"
            "但拿到的路径无法交给 os / shutil。"
        )
    else:
        diag.notes.append("Shell COM 后端不可用，无法枚举便携设备。")
    if not diag.portable_devices:
        diag.notes.append("当前没有检测到任何便携设备（MTP）。")
    return diag


def describe_search() -> str:
    """给用户看的中文汇总（适合直接弹窗或打印）。"""
    lines = ["正在查找 Quest 歌曲目录（CustomLevels）…", ""]
    results = find_quest_customlevels()
    if not results:
        lines.append("未找到可用路径。")
        lines.append("")
        lines.append(diagnose().as_text())
        lines.append("")
        lines.append("建议：")
        lines.append("  1) 推荐：开启 Quest 开发者模式 + 安装 adb，然后用 adb 方式传输。")
        lines.append("  2) 或者：手动在资源管理器中打开设备，用「浏览 Shell 路径」对话框选择。")
        lines.append("  3) 注意：当前 Beat Saber 的歌曲目录通常在 ModData 下，")
        lines.append("     即 /sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels。")
        return os.linesep.join(lines)

    lines.append(f"共找到 {len(results)} 条候选：")
    for i, r in enumerate(results, 1):
        mark = "✔ 可直接使用" if r.usable else "△ 需要专用接口"
        lines.append(f"  {i}. [{r.kind}] {r.display}")
        lines.append(f"     {mark}   来源: {r.source}")
        if r.note:
            lines.append(f"     说明: {r.note}")
    best = best_quest_customlevels()
    if best:
        lines.append("")
        lines.append(f"建议使用: {best.display}")
    return os.linesep.join(lines)


def _dedupe(items: list[QuestPath]) -> list[QuestPath]:
    seen: set[tuple[str, str]] = set()
    out: list[QuestPath] = []
    for it in items:
        key = (it.kind, it.display)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def selftest(verbose: bool = True) -> int:
    """打印三条路线的探测结果。返回 0 表示模块工作正常（即使没找到 Quest）。"""
    out = print if verbose else (lambda *a, **k: None)
    out("=" * 72)
    out("quest_paths 自检")
    out("=" * 72)

    out("--- (a) 盘符路线 ---")
    drives = list_logical_drives()
    out(f"  本机盘符: {drives or '无'}")
    for d in drives:
        out(f"    {d:<5} 类型={drive_type_name(d)} ({drive_type(d)})")
    a = find_on_drives()
    out(f"  命中 {len(a)} 条")
    for r in a:
        out(f"    {r}")

    out("")
    out("--- (b) adb 路线 ---")
    out(f"  adb 可用: {adb_bridge.adb_available()}  ({adb_bridge.find_adb()})")
    for d in adb_bridge.list_devices():
        out(f"    设备: {d.description}")
    b = find_via_adb()
    out(f"  命中 {len(b)} 条")
    for r in b:
        out(f"    {r}")

    out("")
    out("--- (c) Shell COM (MTP) 路线 ---")
    out(f"  后端: {shell_mtp.backend_name() or '不可用'}")
    devs = shell_mtp.list_portable_devices()
    out(f"  便携设备: {[n.name for n in devs] or '无'}")
    c = find_via_shell()
    out(f"  命中 {len(c)} 条")
    for r in c:
        out(f"    {r}")

    out("")
    out("--- 汇总 ---")
    total = find_quest_customlevels()
    out(f"  find_quest_customlevels() -> {len(total)} 条")
    for r in total:
        out(f"    kind={r.kind:<6} usable={r.usable!s:<5} display={r.display}")
    out("")
    out(diagnose().as_text())

    out("")
    out("--- 人类可读汇总 ---")
    out(describe_search())
    out("")
    out(f"--- 结论：模块正常（找到 {len(total)} 条候选）---")
    out("=" * 72)
    return 0


def _main(argv: list[str]) -> int:
    if "--selftest" in argv or "-t" in argv:
        return selftest()
    if "--help" in argv or "-h" in argv:
        print(
            "quest_paths —— Quest 歌曲目录统一查找入口\n"
            "\n"
            "用法:\n"
            "  python quest_paths.py --selftest   # 自检（三条路线 + 体检）\n"
            "\n"
            "作为库使用:\n"
            "  import quest_paths\n"
            "  for p in quest_paths.find_quest_customlevels():\n"
            "      print(p.kind, p.display, p.usable)\n"
        )
        return 0
    return selftest()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
