r"""folder_picker.py —— 可浏览便携设备（MTP）的 Tkinter 模态文件夹选择对话框。

为什么不能用 ``filedialog.askdirectory()``
------------------------------------------
Windows 上 ``tkinter.filedialog.askdirectory()`` 走的是 Tk 的 Vista 对话框
（``IFileDialog`` + ``FOS_PICKFOLDERS``），它把选中项用
``IShellItem::GetDisplayName(SIGDN_FILESYSPATH)`` 转成路径。便携设备上的文件夹
**没有文件系统路径**，这一步失败，Tk 没有做任何回退（没有
``SIGDN_DESKTOPABSOLUTEPARSING`` 分支），于是 ``askdirectory()`` 返回**空字符串**
（不抛异常）。所以这个按钮永远选不出 Quest 上的文件夹 —— 只能换一个对话框。

本模块提供的对话框特点
----------------------
* 懒加载：只在展开节点时才去枚举子目录，巨大的歌曲目录不会卡死界面
* 手动输入框：可以直接粘贴路径（支持 ``/sdcard/...``、``内部共享存储空间\\...``、
  ``::{GUID}\\...``、真实盘符路径）
* 「改用 adb」提示 + 按钮：一键执行体检并告知 adb 状态 / 安装方法
* 返回值同时给出 **Shell 解析名** 和 **友好路径字符串**
* 纯 tkinter，不依赖 customtkinter；tkinter 不可用时也能优雅降级

快速使用::

    import folder_picker
    result = folder_picker.ask_quest_folder()
    if result is not None:
        print(result.display)      # 给用户看的路径
        print(result.shell_path)   # 交给 Shell COM 用
        print(result.kind, result.usable)
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable

import adb_bridge
import shell_mtp

# ---------------------------------------------------------------------------
# tkinter 惰性导入（缺了也不要在 import 期就炸）
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 取决于运行环境
    import tkinter as tk
    from tkinter import messagebox, ttk

    TK_AVAILABLE = True
    TK_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001  # pragma: no cover
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    TK_AVAILABLE = False
    TK_IMPORT_ERROR = str(_exc)


# ---------------------------------------------------------------------------
# 返回值
# ---------------------------------------------------------------------------


@dataclass
class PickedFolder:
    """用户选中的结果。"""

    display: str
    """给用户看的路径（会写回输入框 / 配置）。"""

    shell_path: str = ""
    """Shell 解析名（MTP 节点才有），如 ``::{20D04FE0-...}\\Quest 3\\...``。"""

    kind: str = "unknown"
    """``"drive"`` | ``"mtp"`` | ``"adb"`` | ``"file"`` | ``"manual"`` | ``"special"``。"""

    usable: bool = False
    """能否直接用 ``os.listdir`` / ``shutil.copy``。仅 ``drive`` 可能为 True。"""

    fs_path: str | None = None
    """真实文件系统路径（如果有）。"""

    remote_path: str = ""
    """adb 路线下的设备内路径。"""

    adb_serial: str = ""

    def __str__(self) -> str:
        return self.display


# ---------------------------------------------------------------------------
# 主对话框
# ---------------------------------------------------------------------------


class QuestFolderPicker:
    """浏览「此电脑」（含便携设备）并选择文件夹的模态对话框。

    Args:
        master: 父窗口；``None`` 时自己创建一个隐藏的根窗口。
        title: 窗口标题。
        initial_path: 初始定位到的路径（友好路径 / Shell 解析名 / 真实路径均可）。
        allow_all_roots: 是否显示所有根节点（默认 True，包含本地盘）。
        on_status: 状态文字回调（可选）。
    """

    #: 展开时的占位符文字
    _PLACEHOLDER = "加载中…"

    def __init__(
        self,
        master: Any = None,
        title: str = "选择 Quest 歌曲文件夹（CustomLevels）",
        initial_path: str = "",
        allow_all_roots: bool = True,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        if not TK_AVAILABLE:
            raise RuntimeError(f"tkinter 不可用：{TK_IMPORT_ERROR}")

        self._owns_root = master is None
        if master is None:
            master = tk.Tk()
            master.withdraw()
        self._master = master
        self._allow_all_roots = allow_all_roots
        self._on_status = on_status

        self.top = tk.Toplevel(master)
        self.top.title(title)
        self.top.geometry("860x580")
        self.top.minsize(640, 420)
        self.top.transient(master)
        try:
            self.top.grab_set()
        except Exception:
            pass

        self.result: PickedFolder | None = None
        self._node_by_item: dict[str, shell_mtp.ShellNode] = {}
        self._loaded_items: set[str] = set()
        self._cancel = threading.Event()

        self._build_ui()
        self._populate_roots()

        if initial_path:
            self.entry_var.set(initial_path)

        self.top.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.top.bind("<Escape>", lambda _e: self._on_cancel())

    # -- 界面搭建 --------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        top_frame = tk.Frame(self.top)
        top_frame.pack(fill="x", **pad)
        tk.Label(top_frame, text="路径：", font=("Microsoft YaHei UI", 10)).pack(side="left")
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(top_frame, textvariable=self.entry_var,
                              font=("Consolas", 10))
        self.entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        tk.Button(top_frame, text="跳转", width=6, command=self._on_goto).pack(side="left")

        # 树 + 滚动条
        mid = tk.Frame(self.top)
        mid.pack(fill="both", expand=True, **pad)
        self.tree = ttk.Treeview(mid, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 状态
        self.status_var = tk.StringVar(
            value=f"Shell COM 后端：{shell_mtp.backend_name() or '不可用'}"
        )
        tk.Label(self.top, textvariable=self.status_var, anchor="w",
                 fg="#444444", font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=10)

        # 按钮
        btns = tk.Frame(self.top)
        btns.pack(fill="x", **pad)
        tk.Button(btns, text="改用 adb（推荐）", command=self._on_adb_help).pack(side="left")
        tk.Button(btns, text="刷新", width=8, command=self._populate_roots).pack(side="left", padx=6)
        tk.Button(btns, text="取消", width=8, command=self._on_cancel).pack(side="right")
        tk.Button(btns, text="确定选择", width=10,
                  command=self._on_ok).pack(side="right", padx=6)

    # -- 树操作 ----------------------------------------------------------

    def _populate_roots(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._node_by_item.clear()
        self._loaded_items.clear()
        self._set_status("正在枚举「此电脑」…")
        self.top.update_idletasks()

        roots = shell_mtp.list_roots()
        if not roots:
            self._set_status(
                "无法枚举「此电脑」（Shell COM 后端不可用）。"
                "请手动输入路径，或改用 adb。"
            )
            return

        shown = 0
        for node in roots:
            if not self._allow_all_roots and node.kind != "mtp":
                continue
            self._insert_node("", node)
            shown += 1
        n_mtp = sum(1 for n in roots if n.kind == "mtp")
        self._set_status(
            f"共 {shown} 个根节点，其中便携设备(MTP) {n_mtp} 个。"
            + ("" if n_mtp else "  未发现 MTP 设备 —— 请确认 Quest 已连接并允许访问文件。")
        )

    def _insert_node(self, parent: str, node: shell_mtp.ShellNode) -> str:
        label = f"{node.name}"
        if node.kind in ("drive", "mtp"):
            label = f"{node.name}  ({'本地盘' if node.kind == 'drive' else '便携设备'})"
        item = self.tree.insert(parent, "end", text=label, open=False)
        self._node_by_item[item] = node
        if node.is_folder:
            # 占位子节点，使节点可展开；真正展开时才枚举
            self.tree.insert(item, "end", text=self._PLACEHOLDER, tags=("placeholder",))
        return item

    def _on_open(self, _event: Any) -> None:
        item = self.tree.focus()
        if not item or item in self._loaded_items:
            return
        node = self._node_by_item.get(item)
        if node is None or not node.is_folder:
            return
        self._loaded_items.add(item)
        self._set_status(f"正在读取 {node.name} …（MTP 设备较慢，请稍候）")
        self.top.update_idletasks()

        children = shell_mtp.list_children(node)

        # 移除占位符
        for child in self.tree.get_children(item):
            if "placeholder" in self.tree.item(child, "tags"):
                self.tree.delete(child)

        for child in children:
            self._insert_node(item, child)
        self._set_status(
            f"{node.name}：{len(children)} 项"
            + ("" if children else "（为空或无法读取）")
        )

    def _on_select(self, _event: Any) -> None:
        item = self.tree.focus()
        node = self._node_by_item.get(item)
        if node is None:
            return
        self.entry_var.set(node.display)
        extra = ""
        if node.kind == "mtp":
            extra = "  ⚠ 便携设备路径无法用 os / shutil 直接访问"
        self._set_status(f"已选中：{node.display}{extra}")

    # -- 动作 ------------------------------------------------------------

    def _on_goto(self) -> None:
        raw = self.entry_var.get().strip()
        if not raw:
            return
        shell = shell_mtp.to_shell_path(raw)

        # 真实文件系统路径：直接定位到其父目录并选中
        if len(shell) >= 2 and shell[1] == ":":
            self._goto_fs(shell)
            return

        # Shell 命名空间：尝试解析并展开
        parts = [p for p in raw.replace("/", "\\").split("\\") if p]
        if parts and parts[0] in ("此电脑", "This PC"):
            parts = parts[1:]
        if not parts:
            self._set_status("请输入更具体的路径。")
            return

        self._set_status(f"正在定位 {raw} …")
        self.top.update_idletasks()

        # 逐级展开（最多 8 层，避免在 MTP 上做无意义的深挖）
        current_parent = ""
        target_item = ""
        for depth, name in enumerate(parts[:8]):
            path_so_far = "\\".join(parts[: depth + 1])
            candidates = (
                self.tree.get_children(current_parent) if current_parent
                else self.tree.get_children("")
            )
            match = ""
            for item in candidates:
                node = self._node_by_item.get(item)
                if node and node.name.strip().lower() == name.strip().lower():
                    match = item
                    break
            if not match:
                # 还没加载过这一层，先加载
                if current_parent:
                    self.tree.item(current_parent, open=True)
                    self.tree.focus(current_parent)
                    self._on_open(None)
                    candidates = self.tree.get_children(current_parent)
                    for item in candidates:
                        node = self._node_by_item.get(item)
                        if node and node.name.strip().lower() == name.strip().lower():
                            match = item
                            break
                if not match:
                    self._set_status(f"未找到「{name}」（在 {path_so_far} 处停止）。")
                    return
            current_parent = match
            target_item = match
            self.tree.item(current_parent, open=True)

        if target_item:
            self.tree.see(target_item)
            self.tree.selection_set(target_item)
            self.tree.focus(target_item)
            self._on_select(None)
            self._set_status(f"已定位：{self.entry_var.get()}")

    def _goto_fs(self, fs_path: str) -> None:
        if not os.path.isdir(fs_path):
            self._set_status(f"本地路径不存在：{fs_path}")
            return
        # 真实路径没有树节点，直接把结果准备好
        self._set_status(f"本地路径：{fs_path}（可直接使用）")
        self.entry_var.set(fs_path)

    def _on_adb_help(self) -> None:
        status = adb_bridge.describe_status()
        found = adb_bridge.find_customlevels()
        extra = ""
        if found:
            extra = os.linesep + os.linesep + "设备上找到的歌曲目录：" + os.linesep + os.linesep.join(
                "  " + f for f in found
            )
            # 让 adb 路径可以直接选
            self.entry_var.set(found[0])
        messagebox.showinfo(
            "改用 adb（推荐）",
            status
            + extra
            + os.linesep
            + os.linesep
            + "为什么推荐 adb：" + os.linesep
            + "  · MTP 传大量小文件时每个文件都要一次协议协商，非常慢" + os.linesep
            + "  · adb 支持 push/pull，能保留目录结构，可脚本化、可显示进度" + os.linesep
            + "  · 便携设备的 Shell 路径（::{GUID}\\...）无法用 os / shutil 访问" + os.linesep
            + os.linesep
            + "开启方法：" + os.linesep
            + "  1) 手机端 Meta Horizon 应用 → 头显设置 → 开发者模式 → 打开" + os.linesep
            + "  2) 电脑安装 Android Platform Tools，把 platform-tools 加入 PATH" + os.linesep
            + "  3) USB 连接后在头显内点「允许 USB 调试」并勾选「一律允许」",
        )

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)
        if self._on_status is not None:
            try:
                self._on_status(text)
            except Exception:
                pass

    def _on_ok(self) -> None:
        raw = self.entry_var.get().strip()
        item = self.tree.focus()
        node = self._node_by_item.get(item)

        # 优先用输入框内容（用户可能手动改了），否则用树里选中的节点
        if raw and (node is None or raw != node.display):
            self.result = self._make_result_from_text(raw)
        elif node is not None:
            self.result = PickedFolder(
                display=node.display,
                shell_path=node.shell_path,
                kind=node.kind,
                usable=node.usable,
                fs_path=node.fs_path,
            )
        else:
            messagebox.showwarning("未选择", "请先在列表中选择一个文件夹，或手动输入路径。")
            return
        self._close()

    def _make_result_from_text(self, raw: str) -> PickedFolder:
        """把用户手输/选中的字符串包装成结果对象。"""
        text = raw.strip()
        # 1) Shell 命名空间解析名（必须先判断：`::{GUID}\...` 的第 2 个字符也是
        #    冒号，若先做盘符判断会被误判成 "C:" 形式的本地路径）
        if shell_mtp.is_shell_parsing_name(text):
            return PickedFolder(
                display=shell_mtp.to_friendly_path(text), shell_path=text,
                kind="mtp", usable=False,
            )
        # 2) adb / 设备内路径
        if text.startswith("/sdcard") or text.startswith("/storage"):
            dev = adb_bridge.quest_device()
            return PickedFolder(
                display=text, kind="adb", usable=False, remote_path=text,
                adb_serial=(dev.serial if dev else ""),
            )
        # 3) 真实盘符路径（"X:\..."）
        if len(text) >= 2 and text[1] == ":":
            return PickedFolder(
                display=text, kind="drive", usable=os.path.isdir(text), fs_path=text,
            )
        # 4) 友好路径（Quest 3\内部共享存储空间\...）
        shell = shell_mtp.to_shell_path(text)
        return PickedFolder(display=text, shell_path=shell, kind="mtp", usable=False)

    def _on_cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        self._cancel.set()
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        if self._owns_root:
            try:
                self._master.destroy()
            except Exception:
                pass

    # -- 阻塞式显示 ------------------------------------------------------

    def show(self) -> PickedFolder | None:
        """显示对话框并阻塞，直到用户确定或取消。

        Returns:
            :class:`PickedFolder` 或 ``None``（取消）。
        """
        self.top.wait_window()
        return self.result


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def ask_quest_folder(
    master: Any = None,
    title: str = "选择 Quest 歌曲文件夹（CustomLevels）",
    initial_path: str = "",
) -> PickedFolder | None:
    """弹出对话框让用户选择 Quest 上的文件夹。

    Returns:
        :class:`PickedFolder`；取消或 tkinter 不可用时返回 ``None``。
    """
    if not TK_AVAILABLE:
        return None
    dlg = QuestFolderPicker(master=master, title=title, initial_path=initial_path)
    return dlg.show()


def ask_quest_folder_smart(master: Any = None) -> PickedFolder | None:
    """先自动探测；只有探测失败时才弹出对话框。

    这是给主程序用的「一键选择 Quest 文件夹」实现：

    1. ``quest_paths.find_quest_customlevels()`` 自动找
    2. 找到就直接返回第一条最优结果
    3. 找不到才弹出可浏览 MTP 的对话框
    """
    import quest_paths

    found = quest_paths.find_quest_customlevels()
    if found:
        best = quest_paths.best_quest_customlevels()
        if best is not None:
            return PickedFolder(
                display=best.display,
                shell_path=best.shell_path,
                kind=best.kind,
                usable=best.usable,
                fs_path=best.display if best.kind == "drive" else None,
                remote_path=best.remote_path,
                adb_serial=best.adb_serial,
            )
    return ask_quest_folder(master=master)


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def selftest(verbose: bool = True) -> int:
    """不打开窗口，仅检查依赖与基本逻辑。"""
    out = print if verbose else (lambda *a, **k: None)
    out("=" * 72)
    out("folder_picker 自检")
    out("=" * 72)
    out(f"tkinter 可用   : {TK_AVAILABLE}" + (f"  ({TK_IMPORT_ERROR})" if not TK_AVAILABLE else ""))
    if TK_AVAILABLE:
        out(f"Tk 版本        : {tk.TkVersion}")
    out(f"Shell COM 后端 : {shell_mtp.backend_name() or '不可用'}")
    out(f"adb 可用       : {adb_bridge.adb_available()}")

    out("")
    out("--- 与 askdirectory 的差异（路径形态自检）---")
    dlg_cls = QuestFolderPicker
    samples = [
        "C:\\Windows",
        "/sdcard/ModData/com.beatgames.beatsbeam/Mods/SongCore/CustomLevels",
        "Quest 3\\内部共享存储空间\\ModData",
        "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\Quest 3",
    ]
    obj = dlg_cls.__new__(dlg_cls)  # 不建窗口，仅借用方法
    for s in samples:
        r = dlg_cls._make_result_from_text(obj, s)
        out(f"  {s[:56]:<58} -> kind={r.kind:<7} usable={r.usable}")

    out("")
    out("--- 真实枚举（不打开窗口）---")
    out(f"  「此电脑」根节点 : {[n.name for n in shell_mtp.list_roots()] or '无'}")
    out(f"  便携设备        : {[n.name for n in shell_mtp.list_portable_devices()] or '无'}")

    if TK_AVAILABLE:
        out("")
        out("--- 创建一次对话框对象（立刻销毁，验证控件构建不报错）---")
        try:
            root = tk.Tk()
            root.withdraw()
            d = dlg_cls(master=root, allow_all_roots=True)
            n_items = len(d.tree.get_children(""))
            out(f"  树根节点数 = {n_items}")
            out(f"  状态栏文字 = {d.status_var.get()}")
            d._on_cancel()
            root.update()
            root.destroy()
            out("  对话框构建 + 销毁：OK")
        except Exception as exc:  # noqa: BLE001
            out(f"  对话框构建失败：{type(exc).__name__}: {exc}")
            return 1
    out("=" * 72)
    return 0


def _main(argv: list[str]) -> int:
    if "--selftest" in argv or "-t" in argv:
        return selftest()
    if "--help" in argv or "-h" in argv:
        print(
            "folder_picker —— 可浏览便携设备（MTP）的 Tkinter 文件夹选择对话框\n"
            "\n"
            "用法:\n"
            "  python folder_picker.py --selftest   # 自检（不弹窗）\n"
            "  python folder_picker.py              # 直接弹出对话框试用\n"
        )
        return 0
    # 无参数：直接弹出对话框，方便手工试用
    picked = ask_quest_folder()
    print(f"选择结果: {picked!r}")
    if picked:
        print(f"  display    = {picked.display!r}")
        print(f"  shell_path = {picked.shell_path!r}")
        print(f"  kind       = {picked.kind}")
        print(f"  usable     = {picked.usable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
