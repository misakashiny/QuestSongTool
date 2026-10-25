"""设备文件夹选择器（MTP / ADB / 本地磁盘）—— "Quest 插上却选不了文件夹"的正解。

背景（为什么不能直接用 ``askdirectory``）：
Quest 3 通过 USB 连接时是 **MTP 便携设备**：资源管理器里能在"此电脑"下看到它，
但 Windows **不给它分配盘符**。于是

* ``tkinter.filedialog.askdirectory`` 走 IFileDialog，只能返回**文件系统路径** ——
  用户点进设备目录再按"选择文件夹"，什么都不会发生；
* ``os.listdir`` / ``shutil`` 对命名空间路径（``::{GUID}\\…`` 或 ``此电脑\\Quest 3\\…``）
  一律失败。

所以这里自己画一个选择器，按可靠性分三条来源：

============  ==============================================  ==================
来源           读取方式                                         能否读写
============  ==============================================  ==================
便携设备 MTP   ``device_shell.ShellBridge``（PowerShell + COM）  否（只能看和选）
ADB 设备       ``adb.AdbBridge``（``ls``/``pull``/``push``）     是（推荐）
本地磁盘       普通盘符路径 + ``os.scandir``                      是（Explorer 兜底）
============  ==============================================  ==================

**性能约束（本文件最容易写错的地方）**：``ShellBridge`` 每次调用要 200~500ms
（起一个 PowerShell 进程 + COM 枚举），放主线程就是"点一下卡半秒"。因此这里的树是
懒加载的：先塞一个占位子节点让展开箭头显示出来，用户真正展开时才在**后台线程**里读这一层，
读完用 ``self.after(0, …)`` 回到 Tk 主线程填树（Tk 不是线程安全的，跨线程碰控件必崩）。

单独运行（调试用，不会影响主程序）::

    python quest_tool/ui_devicepicker.py --selftest   # 只探测并打印，不开窗口
    python quest_tool/ui_devicepicker.py             # 打开选择器手工试
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable, Sequence

import customtkinter as ctk

if __package__ in (None, ""):  # ``python quest_tool/ui_devicepicker.py`` 直接运行
    # 脚本模式下 sys.path[0] 是 quest_tool 目录，父目录不在里面；补上之后相对导入才能用
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "quest_tool"  # 让下面的相对导入在脚本模式下也成立

from . import adb as adb_mod
from . import device as device_mod
from .theme import T
from .constants import (
    APP_NAME,
    CUSTOM_LEVELS_RELATIVE,
    QUEST_FOLDER_HINTS,
)
from .device_shell import ShellBridge
from .logging_setup import get_logger
from .ui_dialogs import ModalDialog
from .messages import tr

logger = get_logger("devicepicker")

__all__ = ["PickedPath", "DeviceFolderPicker", "pick_device_folder", "main"]

# --------------------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------------------
SOURCE_MTP = "mtp"
SOURCE_ADB = "adb"
SOURCE_LOCAL = "local"

SOURCE_LABELS: dict[str, str] = {
    SOURCE_MTP: "📱 便携设备 (MTP)",
    SOURCE_ADB: "🔌 ADB 设备",
    SOURCE_LOCAL: "💾 本地磁盘",
}
LABEL_TO_SOURCE: dict[str, str] = {label: key for key, label in SOURCE_LABELS.items()}
KIND_LABELS: dict[str, str] = {"local": "本地磁盘", "adb": "ADB 设备", "mtp": "MTP 便携设备"}

ADB_ROOT = "/sdcard"
ADB_FILES_DIR = "/sdcard/Android/data/com.beatgames.beatsaber/files"
ADB_CUSTOMLEVELS = ADB_FILES_DIR + "/CustomLevels"

#: MTP 的限制说明（选择 MTP 目录时原样展示给用户）
MTP_NOTE = (
    "MTP 模式下本程序无法直接读写文件（Windows 没有给设备分配盘符）。"
    "建议：① 安装 adb 获得完整功能；② 或用 Explorer 把 CustomLevels 复制到本地文件夹后选择该文件夹。"
)

#: 本地磁盘里不值得展开的目录（太大且与歌曲无关）
LOCAL_SKIP_DIRS = ("$recycle.bin", "system volume information")


def _norm(path: str) -> str:
    """路径比较用归一化：统一分隔符、去尾部斜杠、转小写。"""
    return (path or "").replace("/", "\\").rstrip("\\").strip().lower()


def _next_segment(goal: str, current: str) -> str:
    """goal 相对 current 的下一段名字（归一化），取不到返回空串。"""
    g = (goal or "").replace("\\", "/").strip("/").lower()
    c = (current or "").replace("\\", "/").strip("/").lower()
    if g and c and g.startswith(c + "/"):
        return g[len(c) + 1 :].split("/")[0]
    if g and not c:
        return g.split("/")[0]
    return ""


# --------------------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------------------
@dataclass
class PickedPath:
    """用户选中的文件夹。

    * ``kind == "local"`` → ``path`` 是盘符路径（如 ``D:\\Songs\\CustomLevels``）
    * ``kind == "adb"``   → ``path`` 是设备上的绝对路径（如
      ``/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels``），
      **不带** ``adb:`` 前缀（前缀由主程序按需拼）
    * ``kind == "mtp"``   → ``path`` 是 Windows Shell 命名空间路径，只能浏览/显示，
      交给 ``os``/``shutil`` 一定失败
    """

    kind: str
    path: str
    label: str
    usable: bool
    note: str = ""

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def display(self) -> str:
        """人类可读的完整表示（adb 补上 ``adb:`` 前缀）。"""
        return f"adb:{self.path}" if self.kind == "adb" else self.path


@dataclass
class _TreeNode:
    """树里一个节点对应的信息（iid 由 Tk 生成，这里只做映射）。"""

    iid: str
    kind: str
    path: str
    label: str
    loadable: bool = True
    loaded: bool = False
    loading: bool = False


@dataclass
class _Child:
    """后台线程读到的子目录。"""

    label: str
    path: str
    kind: str
    loadable: bool = True


@dataclass
class _JumpResult:
    """一次"跳到 CustomLevels"的解析结果。"""

    source: str
    goal: str = ""
    message: str = ""


@dataclass
class _RevealState:
    """逐层展开的进度（异步：每层读完继续往下走）。"""

    iid: str
    goal: str
    depth: int = 0


# --------------------------------------------------------------------------------------
# 选择器
# --------------------------------------------------------------------------------------
class DeviceFolderPicker(ModalDialog):
    """MTP / ADB / 本地 三合一的文件夹选择器，返回 :class:`PickedPath`。

    用法::

        picked = DeviceFolderPicker(parent, adb_path=adb_exe).show()
        if picked and picked.usable:
            ...

    ``candidates`` 可以直接把主程序 ``device.scan_quest()`` 的结果传进来：
    既避免重复探测（MTP 探测很慢），也能让"跳到 CustomLevels"一步到位。
    """

    DEFAULT_TEXT = "✅ 选择此文件夹"
    CANCEL_TEXT = "❌ 取消"
    #: 主线程心跳间隔：用来把后台线程读完的结果搬进界面（90ms 人眼无感）
    HEARTBEAT_MS = 90

    def __init__(
        self,
        master: tk.Misc | None,
        *,
        adb_path: str = "",
        candidates: Sequence[object] = (),
        initial: str = "",
        size: tuple[int, int] = (900, 620),
    ) -> None:
        self._adb_path = adb_path
        self._candidates = list(candidates)
        self._initial = (initial or "").strip()

        self._shell: ShellBridge | None = None  # 懒加载：复用缓存，避免重复 COM 枚举
        self._adb: Any = None  # adb.AdbBridge
        self._nodes: dict[str, _TreeNode] = {}
        self._root_iids: list[str] = []
        self._callbacks: dict[str, list[Callable[[], None]]] = {}
        self._results: queue.Queue[tuple[Callable[..., None], tuple[Any, ...]]] = queue.Queue()
        self._heartbeat_id: str | None = None
        self._pending = 0  # 进行中的后台读取任务数（>0 时禁用"确定"）
        self._busy_text = ""
        self._pending_reveal = ""
        self._last_clicked = ""  # 最近一次鼠标点到的节点（<<TreeviewOpen>> 不带节点信息）
        self._jump_targets = self._collect_targets()
        self._source = self._initial_source()

        super().__init__(master, title=tr("picker.title"), size=size, resizable=(True, True))
        self.result: PickedPath | None = None
        try:
            self.minsize(size[0], size[1])
        except tk.TclError:  # pragma: no cover
            pass

    # ==================================================================
    # 初始状态
    # ==================================================================
    def _collect_targets(self) -> dict[str, str]:
        """从候选里挑出各来源的"直达目标"，省掉重复探测。"""
        targets: dict[str, str] = {}
        for candidate in self._candidates:
            kind = str(getattr(candidate, "kind", "") or "")
            location = getattr(candidate, "location", None)
            if kind == "adb" and "adb" not in targets:
                root = str(getattr(location, "root", "") or "")
                if root:
                    targets["adb"] = root
            elif kind == "mtp" and "mtp" not in targets:
                shell_path = str(getattr(location, "shell_path", "") or "")
                if shell_path:
                    targets["mtp"] = shell_path
            elif kind == "drive" and "local" not in targets:
                root = str(getattr(location, "root", "") or "")
                if root:
                    targets["local"] = root
        return targets

    def _initial_source(self) -> str:
        """决定打开时停在哪一页。

        有候选就按"adb → 盘符 → MTP"的可靠性排序；没有任何线索时默认 MTP ——
        这正是本对话框存在的理由（Quest 插上后最常走的路径）。
        """
        if self._initial:
            kind, _path = self._kind_from_text(self._initial)
            if kind == "adb":
                return SOURCE_ADB
            if kind == "mtp":
                return SOURCE_MTP
            if kind == "local":
                return SOURCE_LOCAL
        kinds = {str(getattr(candidate, "kind", "") or "") for candidate in self._candidates}
        if "adb" in kinds:
            return SOURCE_ADB
        if "drive" in kinds:
            return SOURCE_LOCAL
        if "mtp" in kinds:
            return SOURCE_MTP
        return SOURCE_MTP

    # ==================================================================
    # 界面
    # ==================================================================
    def _create_body(self, parent: ctk.CTkFrame) -> None:
        # ---------- 来源切换 + 快捷跳转 ----------
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill="x", pady=(0, 8))
        self.source_button = ctk.CTkSegmentedButton(
            top,
            values=list(SOURCE_LABELS.values()),
            font=(self.font, 13),
            command=self._on_source_change,
        )
        self.source_button.set(SOURCE_LABELS[self._source])
        self.source_button.pack(side="left")
        ctk.CTkButton(
            top,
            text=tr("picker.jump_custom"),
            width=180,
            height=32,
            font=(self.font, 13),
            fg_color=T["WARNING"],
            hover_color="#d68910",
            command=self.jump_to_custom_levels,
        ).pack(side="right")
        self.busy_label = ctk.CTkLabel(top, text="", font=(self.font, 12), text_color=T["WARNING"])
        self.busy_label.pack(side="right", padx=(0, 12))

        # ---------- 左树 + 右侧说明 ----------
        mid = ctk.CTkFrame(parent, fg_color="transparent")
        mid.pack(fill="both", expand=True)

        side = ctk.CTkFrame(mid, fg_color=("gray86", "gray18"), corner_radius=8)
        side.pack(side="right", fill="y", padx=(10, 0))
        ctk.CTkLabel(
            side, text=tr("picker.current"), font=(self.font, 13, "bold"), anchor="w"
        ).pack(fill="x", padx=12, pady=(10, 4))
        self.info_label = ctk.CTkLabel(
            side,
            text="",
            font=(self.font, 12),
            justify="left",
            anchor="nw",
            wraplength=250,
        )
        self.info_label.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        tree_box = ctk.CTkFrame(mid, fg_color="transparent")
        tree_box.pack(side="left", fill="both", expand=True)
        self._configure_tree_style()
        self.tree = ttk.Treeview(
            tree_box, show="tree", selectmode="browse", style="Quest.Treeview", height=14
        )
        self._tree_scroll = ctk.CTkScrollbar(tree_box, command=self.tree.yview)
        self.tree.configure(yscrollcommand=self._tree_scroll.set)
        self._tree_scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Button-1>", self._on_tree_click, add="+")
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # ---------- 底部：路径 + 状态 + 限制说明 ----------
        path_row = ctk.CTkFrame(parent, fg_color="transparent")
        path_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(path_row, text=tr("picker.path_label"), font=(self.font, 13)).pack(side="left")
        self.path_var = ctk.StringVar(master=self, value=self._initial)
        self.path_entry = ctk.CTkEntry(
            path_row,
            textvariable=self.path_var,
            font=(self.font, 13),
            height=34,
            placeholder_text=tr("picker.path_hint"),
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.path_entry.bind("<KeyRelease>", self._on_path_typed)
        ctk.CTkButton(
            path_row,
            text=tr("picker.refresh"),
            width=90,
            height=34,
            font=(self.font, 13),
            fg_color=T["NEUTRAL"],
            hover_color="#616a6b",
            command=self.reload,
        ).pack(side="left")

        self.status_label = ctk.CTkLabel(
            parent,
            text=tr("picker.choose_left"),
            font=(self.font, 12),
            text_color=T["NEUTRAL"],
            anchor="w",
            justify="left",
            wraplength=max(300, self._size[0] - 60),
        )
        self.status_label.pack(fill="x", pady=(8, 0))
        self.note_label = ctk.CTkLabel(
            parent,
            text="",
            font=(self.font, 12),
            text_color=T["WARNING"],
            anchor="w",
            justify="left",
            wraplength=max(300, self._size[0] - 60),
        )
        self.note_label.pack(fill="x", pady=(4, 0))
        self._update_selection_info(None)

    def _configure_tree_style(self) -> None:
        """让 ttk.Treeview 尽量贴近 CTk 的深浅色主题（不切换全局 ttk 主题）。"""
        dark = str(ctk.get_appearance_mode()).lower() == "dark"
        field_bg = "#242424" if dark else "#ffffff"
        text_fg = "#e8e8e8" if dark else "#202020"
        style = ttk.Style(self)
        try:
            style.configure(
                "Quest.Treeview",
                background=field_bg,
                fieldbackground=field_bg,
                foreground=text_fg,
                rowheight=24,
                borderwidth=0,
                relief="flat",
                font=(self.font, 11),
            )
            style.map(
                "Quest.Treeview",
                background=[("selected", T["ACCENT"])],
                foreground=[("selected", "#ffffff")],
            )
        except tk.TclError:  # pragma: no cover - 主题不支持时用默认配色
            logger.debug("Treeview 主题配置失败，使用默认配色")

    def _create_buttons(self, parent: ctk.CTkFrame) -> None:
        super()._create_buttons(parent)
        ctk.CTkLabel(
            parent,
            text=tr("picker.double_click_hint"),
            font=(self.font, 12),
            text_color=T["NEUTRAL"],
        ).pack(side="left")

    # ==================================================================
    # 显示
    # ==================================================================
    def show(self) -> PickedPath | None:
        """显示并进入模态，返回选中的 :class:`PickedPath`（取消 = None）。

        设备读取在窗口映射之后才启动：构造阶段不碰线程/COM，
        因此可以只构造不 show（自动化测试就是这么做的）。
        """
        self._start_heartbeat()
        self._schedule(80, self._initial_load)
        return super().show()

    def _initial_load(self) -> None:
        if self._destroyed:
            return
        if self._initial:
            self._pending_reveal = self._kind_from_text(self._initial)[1]
        self.reload()

    def reload(self) -> None:
        """重新读取当前来源的根节点（插拔设备后点"刷新"）。"""
        if self._destroyed:
            return
        source = self._source
        self._reset_tree()
        self._begin_task("正在读取设备…")
        self._set_status(f"正在读取{SOURCE_LABELS[source]}…")

        def worker() -> None:
            try:
                roots, message = self._fetch_roots(source)
            except Exception as exc:  # 后台线程里的异常必须自己兜住
                logger.warning("读取根节点失败(%s): %s", source, exc)
                roots, message = [], str(exc)
            self._ui_call(self._apply_roots, source, roots, message)

        self._start_worker(worker, "device-picker-roots")

    # ==================================================================
    # 后台线程 / 主线程桥接
    # ==================================================================
    @staticmethod
    def _start_worker(target: Callable[[], None], name: str) -> None:
        """起守护线程读设备：ShellBridge/adb 每次调用几百毫秒，绝不能占 UI 线程。"""
        threading.Thread(target=target, name=name, daemon=True).start()

    def _ui_call(self, func: Callable[..., None], *args: Any) -> None:
        """工作线程 → 主线程：结果先入队，再由主线程取出来执行。

        为什么不直接跨线程 ``self.after(0, …)``：CPython 的 ``_tkinter`` 只有在主线程
        处于 ``mainloop()`` 里（dispatching=1）时才允许别的线程调用 Tcl；用
        ``wait_window()``/``vwait`` 驱动事件循环时会抛
        ``RuntimeError: main thread is not in main loop``（_review/probe_thread_after.py 有实测）。
        入队 + 主线程心跳轮询在两种驱动方式下都成立。
        """
        self._results.put((func, args))
        try:  # 主线程在 mainloop 里 → 立刻唤醒（低延迟）；不在 → 心跳兜底
            self.after(0, self._drain_results)
        except (RuntimeError, tk.TclError):
            pass

    def _drain_results(self) -> None:
        """主线程：把后台线程读完的结果搬到界面上。"""
        while True:
            try:
                func, args = self._results.get_nowait()
            except queue.Empty:
                return
            if self._destroyed:
                continue
            self._invoke(func, *args)

    def _start_heartbeat(self) -> None:
        """周期性地在主线程里搬运结果（只在有任务时真正干活）。"""
        if self._heartbeat_id is not None or self._destroyed:
            return
        self._heartbeat_id = self._schedule(self.HEARTBEAT_MS, self._heartbeat)

    def _heartbeat(self) -> None:
        self._heartbeat_id = None
        if self._destroyed:
            return
        self._drain_results()
        self._start_heartbeat()

    @staticmethod
    def _invoke(func: Callable[..., None], *args: Any) -> None:
        try:
            func(*args)
        except tk.TclError:
            pass  # 控件已销毁
        except Exception as exc:  # pragma: no cover - 保护 UI 线程
            logger.warning("设备选择器回调异常: %s", exc)

    def _alive(self) -> bool:
        try:
            return not self._destroyed and bool(self.winfo_exists())
        except tk.TclError:
            return False

    # ==================================================================
    # 任务计数 / 状态
    # ==================================================================
    def _begin_task(self, message: str) -> None:
        self._pending += 1
        self._start_heartbeat()  # 保证结果一定有人搬（不依赖主线程在 mainloop 里）
        self._update_busy(message)

    def _end_task(self) -> None:
        self._pending = max(0, self._pending - 1)
        self._update_busy()

    def _update_busy(self, message: str = "") -> None:
        """读取期间：状态标签显示"正在读取设备…"，"确定"按钮禁用（防止拿到半截结果）。"""
        if message:
            self._busy_text = message
        if not self._alive():
            return
        busy = self._pending > 0
        try:
            self.busy_label.configure(text=f"⏳ {self._busy_text}" if busy else "")
            button = getattr(self, "default_button", None)
            if button is not None:
                if busy:
                    button.configure(state="disabled", text=tr("picker.reading"))
                else:
                    button.configure(state="normal", text=self.DEFAULT_TEXT)
            self._update_tree_cursor(busy)
        except tk.TclError:  # pragma: no cover
            pass

    def _update_tree_cursor(self, busy: bool) -> None:
        try:
            self.tree.configure(cursor="watch" if busy else "")
        except tk.TclError:  # pragma: no cover
            pass

    def _set_status(self, text: str) -> None:
        if not self._alive():
            return
        try:
            self.status_label.configure(text=text)
        except tk.TclError:  # pragma: no cover
            pass

    def _update_selection_info(self, picked: PickedPath | None) -> None:
        """右侧说明 + MTP 限制提示（只在 kind == "mtp" 时出现）。"""
        if not self._alive():
            return
        try:
            if picked is None:
                self.info_label.configure(text=tr("picker.nothing_selected"))
                self.note_label.configure(text="")
                return
            self.info_label.configure(
                text="\n".join(
                    [
                        f"来源：{picked.kind_label}",
                        f"可读写：{'是' if picked.usable else '否'}",
                        "",
                        picked.path,
                    ]
                )
            )
            self.note_label.configure(text=MTP_NOTE if picked.kind == "mtp" else "")
        except tk.TclError:  # pragma: no cover
            pass

    # ==================================================================
    # 树：读取
    # ==================================================================
    def _fetch_roots(self, source: str) -> tuple[list[_Child], str]:
        """在工作线程里取根节点，返回 (根节点, 提示/错误)。"""
        if source == SOURCE_LOCAL:
            roots = [
                _Child(label=f"💾 {drive}", path=drive, kind=SOURCE_LOCAL)
                for drive in device_mod.drive_letters()
            ]
            if not roots:
                return [], "没有检测到任何磁盘分区"
            return roots, ""

        if source == SOURCE_ADB:
            bridge = self._get_adb()
            if bridge is None:
                return [], "未找到 adb：请安装 Android Platform Tools 并把 adb 加入 PATH"
            try:
                device = bridge.ensure_device()
            except adb_mod.AdbError as exc:
                return [], str(exc)
            if not bridge.is_dir(ADB_ROOT):
                return [], f"设备上没有 {ADB_ROOT}"
            label = f"🔌 {device.model or 'Android 设备'} ({device.serial})"
            return [_Child(label=label, path=ADB_ROOT, kind=SOURCE_ADB)], ""

        bridge = self._get_shell()
        if bridge is None or not bridge.available:
            return [], "找不到 PowerShell，无法浏览便携设备（MTP）"
        try:
            devices = bridge.portable_devices()
        except RuntimeError as exc:
            return [], str(exc)
        if not devices:
            return [], "未检测到便携设备（MTP）：请连接头显、解锁并在头显里允许 USB 访问"
        return [
            _Child(label=f"📱 {item.name}", path=item.shell_path, kind=SOURCE_MTP)
            for item in devices
            if item.shell_path
        ], ""

    def _fetch_children(self, kind: str, path: str) -> list[_Child]:
        """在工作线程里读某一层的子目录。"""
        if kind == SOURCE_LOCAL:
            return self._list_local(path)
        if kind == SOURCE_ADB:
            return self._list_adb(path)
        if kind == SOURCE_MTP:
            return self._list_mtp(path)
        return []

    @staticmethod
    def _list_local(path: str) -> list[_Child]:
        found: list[tuple[str, str]] = []
        with os.scandir(path) as iterator:
            for entry in iterator:
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue  # 单个条目失败不影响整层
                if entry.name.lower() in LOCAL_SKIP_DIRS:
                    continue
                found.append((entry.name, entry.path))
        # CustomLevels 永远排最前，用户一眼就能看到
        found.sort(key=lambda item: (item[0].lower() != "customlevels", item[0].lower()))
        return [_Child(label=name, path=full, kind=SOURCE_LOCAL) for name, full in found]

    def _list_adb(self, path: str) -> list[_Child]:
        bridge = self._get_adb()
        if bridge is None:
            raise RuntimeError("未找到 adb：请安装 Android Platform Tools 并把 adb 加入 PATH")
        names = [name for name in bridge.list_dirs(path) if name and not name.startswith(".")]
        names.sort(key=lambda name: (name.lower() != "customlevels", name.lower()))
        base = path.rstrip("/")
        return [_Child(label=name, path=f"{base}/{name}", kind=SOURCE_ADB) for name in names]

    def _list_mtp(self, path: str) -> list[_Child]:
        bridge = self._get_shell()
        if bridge is None or not bridge.available:
            raise RuntimeError("碰不到 Shell 命名空间（需要 Windows + PowerShell）")
        items = bridge.list_items(path)
        children = [
            _Child(label=item.name, path=item.shell_path, kind=SOURCE_MTP)
            for item in items
            if item.is_folder and item.shell_path
        ]
        children.sort(key=lambda child: (child.label.lower() != "customlevels", child.label.lower()))
        return children

    # ==================================================================
    # 树：填充与懒加载
    # ==================================================================
    def _reset_tree(self) -> None:
        """清空树（切换来源 / 刷新时用）。"""
        if not self._alive():
            return
        try:
            self.tree.delete(*self.tree.get_children(""))
        except tk.TclError:  # pragma: no cover
            pass
        self._nodes.clear()
        self._callbacks.clear()
        self._root_iids = []

    def _apply_roots(self, source: str, roots: list[_Child], message: str) -> None:
        self._end_task()
        if not self._alive() or source != self._source:
            return  # 用户在读取期间切走了，结果作废
        self._reset_tree()
        self._root_iids = [self._insert_node("", child) for child in roots]
        if message:
            self._set_status(f"⚠️ {message}")
            self.info_label.configure(text=message)
            return
        self._set_status(f"已就绪：{len(roots)} 个位置，展开左侧节点开始浏览")
        self._schedule(10, self._after_roots_ready)

    def _after_roots_ready(self) -> None:
        """根节点就绪后：优先展开上次用过的路径，其次 ADB 自动定位 CustomLevels。
        第二分支原先调的是 `self._auto_reveal_adb()` —— **这个方法从来没写过**，
        所以走 ADB 来源时这里必然 `AttributeError`，被 `_schedule` 的调度吞掉，
        表现为"用 adb 打开设备选择器不会自动跳到 CustomLevels"（v17.2 修）。
        现在直接复用按钮那个入口 `jump_to_custom_levels()`（它本来就是为这件事写的）。
        """
        if not self._alive():
            return
        if self._pending_reveal:
            goal, self._pending_reveal = self._pending_reveal, ""
            self._reveal_to(goal)
            return
        if self._source == SOURCE_ADB:
            self.jump_to_custom_levels()

    def _insert_node(self, parent_iid: str, child: _Child) -> str:
        iid = self.tree.insert(parent_iid, "end", text=f" {child.label}", open=False)
        self._nodes[iid] = _TreeNode(
            iid=iid, kind=child.kind, path=child.path, label=child.label, loadable=child.loadable
        )
        if child.loadable:
            # 占位子节点：只为让展开箭头出现，真正的内容等用户展开再读
            self.tree.insert(iid, "end", text=" …")
        return iid

    def _drop_subtree(self, iid: str) -> None:
        for child in list(self.tree.get_children(iid)):
            self._drop_subtree(child)
        self._nodes.pop(iid, None)
        self._callbacks.pop(iid, None)
        try:
            self.tree.delete(iid)
        except tk.TclError:  # pragma: no cover
            pass

    def _clear_children(self, iid: str) -> None:
        for child in list(self.tree.get_children(iid)):
            self._drop_subtree(child)

    def _load_children(self, iid: str, on_done: Callable[[], None] | None = None) -> None:
        """读某一层的子目录（后台线程）。

        * 已加载 → 立刻回调；
        * 加载中 → 把回调挂到这一层，读完一起叫醒（避免同一层读两遍）；
        * 未加载 → 起线程。
        """
        node = self._nodes.get(iid)
        if node is None or not node.loadable:
            if on_done is not None:
                on_done()
            return
        if node.loaded:
            if on_done is not None:
                on_done()
            return
        if on_done is not None:
            self._callbacks.setdefault(iid, []).append(on_done)
        if node.loading:
            return

        node.loading = True
        kind, path, label = node.kind, node.path, node.label
        self._begin_task("正在读取设备…")
        self._set_status(f"正在读取 {label or path} …")

        def worker() -> None:
            try:
                children = self._fetch_children(kind, path)
                error = ""
            except Exception as exc:
                logger.warning("读取 %s 失败: %s", path, exc)
                children, error = [], str(exc)
            self._ui_call(self._apply_children, iid, children, error)

        self._start_worker(worker, "device-picker-load")

    def _apply_children(self, iid: str, children: list[_Child], error: str) -> None:
        self._end_task()
        if not self._alive():
            return
        node = self._nodes.get(iid)
        if node is None:
            return  # 树已被刷新，这次结果作废
        self._clear_children(iid)
        for child in children:
            self._insert_node(iid, child)
        node.loaded = True
        node.loading = False

        if error:
            # 标记成已加载：否则用户反复展开会反复等几百毫秒
            self._set_status(f"⚠️ 读取失败：{error}")
        elif children:
            self._set_status(f"已加载 {len(children)} 个子文件夹（{node.path}）")
        elif "android/data" in node.path.lower():
            self._set_status(
                "该目录在 Android 11+ 上受系统限制（或尚未创建）—— 用 adb 才能可靠访问"
            )
        else:
            self._set_status(f"该目录下没有子文件夹（{node.path}）")

        for callback in self._callbacks.pop(iid, []):
            try:
                callback()
            except Exception as exc:  # pragma: no cover
                logger.warning("子节点加载回调异常: %s", exc)

    # ==================================================================
    # 树：交互
    # ==================================================================
    def _on_tree_click(self, event: tk.Event) -> None:
        """记下鼠标点到的节点：<<TreeviewOpen>> 事件本身不带节点信息。"""
        try:
            row = self.tree.identify_row(event.y)
        except tk.TclError:  # pragma: no cover
            return
        if row:
            self._last_clicked = row

    def _on_tree_open(self, _event: tk.Event | None = None) -> None:
        """用户展开节点：只加载"确实被展开"的那一项。

        ``<<TreeviewOpen>>`` 事件不告诉你是哪一项，而 ``focus()`` 可能停在别的行上 ——
        如果照着 focus 去读，展开一个目录会顺带读另一个目录（白白等几百毫秒，
        期间"确定"还是禁用的）。所以两个候选都要求"当前真的是展开状态"。
        """
        candidates: list[str] = []
        for iid in (self._last_clicked, self.tree.focus()):
            if not iid or iid in candidates:
                continue
            node = self._nodes.get(iid)
            if node is None or node.loaded or node.loading:
                continue
            try:
                if not self.tree.item(iid, "open"):
                    continue
            except tk.TclError:  # pragma: no cover
                continue
            candidates.append(iid)
        for iid in candidates:
            self._load_children(iid)

    def _on_tree_select(self, _event: tk.Event | None = None) -> None:
        picked = self._current_picked()
        if picked is None:
            return
        try:
            self.path_var.set(picked.path)
        except tk.TclError:  # pragma: no cover
            return
        self._set_status(f"已选择：{picked.kind_label} · {picked.path}")
        self._update_selection_info(picked)

    def _on_tree_double_click(self, event: tk.Event) -> None:
        try:
            row = self.tree.identify_row(event.y)
        except tk.TclError:  # pragma: no cover
            return
        if not row:
            return
        try:
            self.tree.selection_set(row)
            self.tree.focus(row)
        except tk.TclError:  # pragma: no cover
            return
        self._on_default()  # 双击 = 直接确定

    def _on_path_typed(self, _event: tk.Event | None = None) -> None:
        """手动粘贴路径时同步提示（选中状态不变）。"""
        kind, path = self._kind_from_text(self.path_var.get())
        if not path:
            return
        picked = self._make_picked(kind, path)
        self._update_selection_info(picked)

    def _on_source_change(self, label: str) -> None:
        source = LABEL_TO_SOURCE.get(label, SOURCE_LOCAL)
        if source == self._source:
            return
        self._source = source
        self._pending_reveal = ""
        self.reload()

    # ==================================================================
    # 选择 / 结果
    # ==================================================================
    def _current_picked(self) -> PickedPath | None:
        try:
            selection = self.tree.selection()
        except tk.TclError:  # pragma: no cover
            return None
        if not selection:
            return None
        node = self._nodes.get(selection[0])
        if node is None:
            return None
        return self._make_picked(node.kind, node.path)

    @staticmethod
    def _make_picked(kind: str, path: str, label: str = "") -> PickedPath:
        kind = kind if kind in (SOURCE_LOCAL, SOURCE_ADB, SOURCE_MTP) else SOURCE_LOCAL
        usable = kind in (SOURCE_LOCAL, SOURCE_ADB)
        note = MTP_NOTE if kind == SOURCE_MTP else ""
        return PickedPath(
            kind=kind,
            path=path,
            label=label or f"{KIND_LABELS[kind]} · {path}",
            usable=usable,
            note=note,
        )

    @staticmethod
    def _kind_from_text(text: str) -> tuple[str, str]:
        """按前缀猜来源，返回 (kind, 规范化后的路径)。

        * ``adb:/sdcard/...`` → adb（去掉前缀）
        * ``/sdcard/...``     → adb
        * ``::{GUID}\\...`` / ``此电脑\\...`` → mtp
        * 其余 → local
        """
        raw = (text or "").strip().strip('"').strip("'")
        if not raw:
            return SOURCE_LOCAL, ""
        lowered = raw.lower()
        if lowered.startswith("adb:"):
            return SOURCE_ADB, raw[4:].strip()
        if raw.startswith("::") or raw.startswith("此电脑") or lowered.startswith("this pc"):
            return SOURCE_MTP, raw
        if raw.startswith("/"):
            return SOURCE_ADB, raw
        return SOURCE_LOCAL, raw

    def _on_default(self) -> None:
        """确定：优先用手动输入的路径，其次用树里选中的节点。"""
        if self._pending > 0:
            self._set_status("正在读取设备，请稍候再确定…")
            return
        text = self.path_var.get().strip()
        picked = self._current_picked()
        if picked is None:
            if not text:
                self._set_status("请先在左侧选择一个文件夹（也可以直接粘贴路径）")
                return
            kind, path = self._kind_from_text(text)
            picked = self._make_picked(kind, path)
        elif text and _norm(text) != _norm(picked.path):
            # 输入框被改过：以输入的为准
            kind, path = self._kind_from_text(text)
            picked = self._make_picked(kind, path)
        self.result = picked
        self._destroy_self()

    # ==================================================================
    # 逐层展开（异步，不阻塞 UI）
    # ==================================================================
    def _match_root(self, goal: str) -> str | None:
        """挑一个路径是 goal 前缀的根节点。"""
        target = _norm(goal)
        best: str | None = None
        best_len = -1
        for iid in self._root_iids:
            node = self._nodes.get(iid)
            if node is None:
                continue
            base = _norm(node.path)
            if base and target.startswith(base) and len(base) > best_len:
                best, best_len = iid, len(base)
        return best

    def _reveal_to(self, goal: str) -> None:
        """展开到 goal 所在的层级并选中它（每一层都可能触发一次后台读取）。"""
        if not self._alive() or not goal:
            return
        root = self._match_root(goal)
        if root is None:
            self._set_status(f"⚠️ {goal} 不在当前来源下，请切换来源后再试")
            return
        self._set_status(f"正在定位 {goal} …")
        self._reveal_step(_RevealState(iid=root, goal=_norm(goal)))

    def _reveal_step(self, state: _RevealState) -> None:
        if not self._alive() or state.depth > 12:  # 防御：层级异常时不无限递归
            return
        node = self._nodes.get(state.iid)
        if node is None:
            return
        if _norm(node.path) == state.goal:
            self._select_node(state.iid)
            self._set_status(f"已定位：{node.path}")
            return
        if not node.loaded:
            self._load_children(state.iid, on_done=lambda: self._reveal_step(state))
            return

        match = self._find_child_for_goal(state.iid, node, state.goal)
        if match is None:
            self._select_node(state.iid)
            self._set_status(f"⚠️ 在 {node.path} 下没有找到目标，已停在上一层")
            return
        child = self._nodes.get(match)
        if child is not None and _norm(child.path) == state.goal:
            # 已经到目标：选中就好，不用再读它自己的内容
            # （否则白等几百毫秒，期间"确定"还是禁用的）
            self._select_node(match)
            self._set_status(f"已定位：{child.path}")
            return
        try:
            self.tree.item(match, open=True)
        except tk.TclError:  # pragma: no cover
            return
        self._select_node(match)
        self._load_children(
            match,
            on_done=lambda: self._reveal_step(
                _RevealState(iid=match, goal=state.goal, depth=state.depth + 1)
            ),
        )

    def _find_child_for_goal(self, iid: str, node: _TreeNode, goal: str) -> str | None:
        """在 node 的子节点里找通往 goal 的那一个。"""
        try:
            children = list(self.tree.get_children(iid))
        except tk.TclError:  # pragma: no cover
            return None
        wanted = _next_segment(goal, node.path)
        if wanted:  # 先按名字精确匹配（避免 "D:\\So" 抢了 "D:\\Songs" 的前缀）
            for child_iid in children:
                child = self._nodes.get(child_iid)
                if child is not None and _norm(child.label) == wanted:
                    return child_iid
        base_len = len(_norm(node.path))
        for child_iid in children:  # 兜底：子路径是 goal 的前缀（不同来源分隔符写法不同）
            child = self._nodes.get(child_iid)
            if child is None:
                continue
            norm = _norm(child.path)
            if norm and len(norm) > base_len and goal.startswith(norm):
                return child_iid
        return None

    def _select_node(self, iid: str) -> None:
        if not self._alive():
            return
        try:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
        except tk.TclError:  # pragma: no cover
            pass

    # ==================================================================
    # 跳到 CustomLevels
    # ==================================================================
    def jump_to_custom_levels(self) -> None:
        """自动走到 CustomLevels 并选中（本地有限深度扫描 / adb 已知路径 / MTP 逐层下钻）。"""
        if self._destroyed:
            return
        if self._pending > 0:
            self._set_status("正在读取设备，请稍候再试…")
            return
        source = self._source
        roots = [self._nodes[iid].path for iid in self._root_iids if iid in self._nodes]
        targets = dict(self._jump_targets)
        self._begin_task("正在定位 CustomLevels…")
        self._set_status("正在定位 CustomLevels…")

        def worker() -> None:
            try:
                result = self._resolve_jump(source, roots, targets)
            except Exception as exc:
                logger.warning("定位 CustomLevels 失败: %s", exc)
                result = _JumpResult(source, "", str(exc))
            self._ui_call(self._apply_jump, result)

        self._start_worker(worker, "device-picker-jump")

    def _apply_jump(self, result: _JumpResult) -> None:
        self._end_task()
        if not self._alive() or result.source != self._source:
            return
        if not result.goal:
            self._set_status(f"⚠️ {result.message}")
            return
        self._set_status(result.message)
        self._reveal_to(result.goal)

    def _resolve_jump(self, source: str, roots: list[str], targets: dict[str, str]) -> _JumpResult:
        """在工作线程里解析"CustomLevels 在哪"。"""
        if source == SOURCE_LOCAL:
            goal = targets.get(SOURCE_LOCAL, "")
            if not goal or not os.path.isdir(goal):
                goal = ""
                for root in roots:
                    found = device_mod.find_custom_levels_under(root, max_depth=4)
                    if found:
                        goal = found[0]
                        break
            if not goal:
                return _JumpResult(source, "", "本地磁盘里没有找到 CustomLevels（可以手动展开磁盘查找）")
            return _JumpResult(source, goal, f"已定位：{goal}")

        if source == SOURCE_ADB:
            bridge = self._get_adb()
            if bridge is None:
                return _JumpResult(source, "", "未找到 adb：请安装 Android Platform Tools 并把 adb 加入 PATH")
            try:
                bridge.ensure_device()
            except adb_mod.AdbError as exc:
                return _JumpResult(source, "", str(exc))
            goal = targets.get(SOURCE_ADB) or ADB_CUSTOMLEVELS
            if goal.lower().startswith("adb:"):
                goal = goal[4:]
            if bridge.is_dir(goal):
                return _JumpResult(source, goal, f"已定位：{goal}")
            if bridge.is_dir(ADB_FILES_DIR):
                return _JumpResult(
                    source,
                    ADB_FILES_DIR,
                    f"设备上还没有 {goal}（先在头显里启动一次 Beat Saber 让它创建）",
                )
            return _JumpResult(source, "", f"设备上不存在：{goal}")

        # MTP：主程序探测到的 shell 路径最准，直接用；否则逐层下钻（每层一次 COM 调用）
        bridge = self._get_shell()
        if bridge is None or not bridge.available:
            return _JumpResult(source, "", "碰不到 Shell 命名空间（需要 Windows + PowerShell）")
        hint = targets.get(SOURCE_MTP, "")
        if hint:
            return _JumpResult(source, hint, f"已定位：{hint}")
        try:
            devices = bridge.portable_devices()
        except RuntimeError as exc:
            return _JumpResult(source, "", str(exc))
        if not devices:
            return _JumpResult(source, "", "未检测到便携设备（MTP）")
        device = devices[0]
        storage = None
        for name in QUEST_FOLDER_HINTS:
            storage = bridge.find_path([device.name, name])
            if storage is not None:
                break
        if storage is None:
            return _JumpResult(
                source, "", "已识别到设备，但无法定位内部存储（设备可能处于锁屏/未解锁状态）"
            )
        segments = [device.name, storage.name]
        goal = storage.shell_path
        for name in CUSTOM_LEVELS_RELATIVE.replace("\\", "/").split("/"):
            segments.append(name)
            item = bridge.find_path(segments)
            if item is None:
                break
            goal = item.shell_path
        if _norm(goal).endswith("customlevels"):
            return _JumpResult(source, goal, f"已定位：{goal}")
        return _JumpResult(
            source, goal, f"只走到 {goal}：CustomLevels 还没创建（先运行一次 Beat Saber）"
        )

    # ==================================================================
    # 桥接对象
    # ==================================================================
    def _get_shell(self) -> ShellBridge | None:
        """懒加载 ShellBridge（进程内共用一个，复用它的目录缓存）。"""
        if self._shell is None:
            try:
                self._shell = ShellBridge()
            except Exception as exc:  # pragma: no cover
                logger.warning("创建 ShellBridge 失败: %s", exc)
                return None
            if not self._shell.available:
                return None
        return self._shell

    def _get_adb(self) -> Any:
        if self._adb is None:
            path = adb_mod.find_adb(self._adb_path)
            if not path:
                return None
            self._adb = adb_mod.AdbBridge(path)
        return self._adb


# --------------------------------------------------------------------------------------
# 便捷函数 / 独立运行
# --------------------------------------------------------------------------------------
def pick_device_folder(
    parent: tk.Misc | None,
    *,
    adb_path: str = "",
    candidates: Sequence[object] = (),
    initial: str = "",
) -> PickedPath | None:
    """一行打开选择器：``picked = pick_device_folder(self, adb_path=adb_exe)``。"""
    return DeviceFolderPicker(
        parent, adb_path=adb_path, candidates=candidates, initial=initial
    ).show()


def _selftest(adb_path: str = "") -> int:
    """``--selftest``：只探测并打印，**不开任何窗口**，没有设备时也干净退出（0）。"""
    print(f"[selftest] {APP_NAME} · 设备文件夹选择器")
    print("[selftest] ① 便携设备（MTP / Shell 命名空间）")
    portable: list[Any] = []
    try:
        bridge = ShellBridge()
        if not bridge.available:
            print("[selftest]    PowerShell 不可用（或不在 Windows 上），跳过 MTP 探测")
        else:
            portable = list(bridge.portable_devices())
            if portable:
                for item in portable:
                    print(f"[selftest]    便携设备：{item.name}    {item.shell_path}")
            else:
                print("[selftest]    未检测到便携设备")
    except Exception as exc:  # 探测失败也只是打印，不影响退出码
        print(f"[selftest]    MTP 探测失败：{exc}")

    print("[selftest] ② ADB 设备")
    adb_devices: list[str] = []
    adb_exec = adb_mod.find_adb(adb_path)
    if not adb_exec:
        print("[selftest]    未找到 adb（安装 Android Platform Tools 可获得完整功能）")
    else:
        print(f"[selftest]    adb：{adb_exec}")
        try:
            adb_bridge = adb_mod.AdbBridge(adb_exec, timeout=10.0)
            for device in adb_bridge.list_devices():
                adb_devices.append(device.serial)
                print(f"[selftest]    设备：{device.label}    状态={device.state}")
            if not adb_devices:
                print("[selftest]    adb 未检测到设备")
        except Exception as exc:
            print(f"[selftest]    adb 探测失败：{exc}")

    drives = device_mod.drive_letters()
    print(f"[selftest] ③ 本地磁盘：{'、'.join(drives) if drives else '无'}")

    if portable or adb_devices:
        print("[selftest] 结论：检测到设备，可以用选择器浏览。")
    else:
        print("[selftest] 结论：未检测到任何 Quest 设备（便携设备 0 个 / adb 设备 0 个）——")
        print("[selftest]       没有 Quest 时这就是预期结果；选择器仍可浏览本地磁盘。")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """独立运行入口（手工调试用）：``python quest_tool/ui_devicepicker.py``。"""
    parser = argparse.ArgumentParser(description="Quest 设备文件夹选择器（独立调试）")
    parser.add_argument("--selftest", action="store_true", help="只探测设备并打印，不打开窗口")
    parser.add_argument("--adb", default="", help="adb.exe 路径（可选）")
    parser.add_argument("--initial", default="", help="初始路径（可选）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return _selftest(args.adb)

    root = ctk.CTk()
    root.withdraw()  # 只需要一个隐藏的父窗口，选择器自己会居中
    root.title(f"{APP_NAME} · 选择器调试")
    try:
        picked = pick_device_folder(root, adb_path=args.adb, initial=args.initial)
    finally:
        root.destroy()

    if picked is None:
        print("[main] 已取消")
        return 0
    print(f"[main] kind={picked.kind} usable={picked.usable}")
    print(f"[main] path={picked.path}")
    if picked.note:
        print(f"[main] note={picked.note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
