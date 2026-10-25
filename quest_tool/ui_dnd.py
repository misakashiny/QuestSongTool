"""Windows 拖放支持（把资源管理器里的歌曲文件夹拖进窗口）。

为什么自己用 ctypes 实现而不装 `windnd` / `tkinterdnd2`：
* 本项目的原则是"装 customtkinter + pillow 就能跑"，为了拖放再引入一个第三方包不值得；
* Windows 的 `WM_DROPFILES` 机制本身很简单：`DragAcceptFiles(hwnd, True)` 声明接收，
  然后在窗口过程里处理 `WM_DROPFILES`、用 `DragQueryFileW` 取路径、`DragFinish` 收尾。

实现要点（都是踩过的坑）：
1. **必须子类化窗口过程**：Tk 自己也有一个 wndproc，我们要用 `SetWindowLongPtrW`
   换成自己的，处理完非拖放消息再转交回原过程，最后 `RestoreWindowProc`。
2. **回调必须常驻引用**：`WNDPROC` 是 C 回调，Python 侧对象被 GC 后调用会直接崩溃。
3. **取 hwnd 要等窗口真正创建**：Tk 的 `winfo_id()` 在 map 之后才稳定，所以要 `after` 一下。
4. 非 Windows 平台直接静默降级（本函数返回 False），调用方据此跳过提示。

用法：
    dnd = DropTarget(root, on_drop=handler, on_hover=set_hint)
    handler(paths: list[str]) -> None      # 只会在 UI 线程被调用
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Callable

from .logging_setup import get_logger

logger = get_logger("dnd")

IS_WINDOWS = os.name == "nt"

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

# 64 位下必须用 LONG_PTR 版本，否则会截断窗口过程地址导致崩溃
if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _shell32 = ctypes.windll.shell32

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )
    # ★ 必须声明 restype，WNDPROC(...) 才会返回"函数指针"而不是整数；
    #   否则 ctypes.cast(proc, c_void_p) 会报 "cast() argument 2 must be a pointer type"。
    WNDPROC.restype = LRESULT

    _user32.SetWindowLongPtrW.restype = LRESULT
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LRESULT]
    _user32.CallWindowProcW.restype = LRESULT
    _user32.CallWindowProcW.argtypes = [
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _shell32.DragQueryFileW.restype = wintypes.UINT
    _shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT
    ]
else:  # pragma: no cover - 非 Windows 直接不启用
    LRESULT = ctypes.c_ssize_t
    WNDPROC = None  # type: ignore[assignment]


class DropTarget:
    """把一个 Tk 顶层窗口变成"可接收文件拖放"的目标。"""

    def __init__(
        self,
        widget,
        on_drop: Callable[[list[str]], None],
        on_enter: Callable[[], None] | None = None,
        on_leave: Callable[[], None] | None = None,
    ) -> None:
        self.widget = widget
        self.on_drop = on_drop
        self.on_enter = on_enter
        self.on_leave = on_leave
        self.enabled = False
        self._hwnd: int | None = None
        self._old_proc: int | None = None
        # ★ 必须保留 Python 引用，否则回调对象被回收后 Windows 调用会崩溃
        self._proc = None

    # ---------------- 安装 / 卸载 ----------------
    def install(self) -> bool:
        if self.enabled:
            return True
        if not IS_WINDOWS:
            return False
        try:
            self.widget.update_idletasks()
            hwnd = int(self.widget.winfo_id())
            # winfo_id 给的是子窗口；拖放要挂在顶层窗口（框架）上
            root = self.widget.winfo_toplevel()
            parent = _user32.GetParent(hwnd)
            if parent:
                hwnd = int(parent)
            self._hwnd = hwnd

            self._proc = WNDPROC(self._wndproc)
            address = ctypes.cast(self._proc, ctypes.c_void_p).value or 0
            self._old_proc = int(
                _user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, LRESULT(address))
            )
            if not self._old_proc:
                logger.warning("SetWindowLongPtrW 失败，拖放不可用")
                return False
            _shell32.DragAcceptFiles(hwnd, True)
            self.enabled = True
            logger.info("已启用文件拖放（hwnd=%s）", hwnd)
            return True
        except Exception as exc:  # 任何失败都只是"没这个功能"，不能影响主流程
            logger.warning("启用拖放失败: %s", exc)
            self.enabled = False
            return False

    def uninstall(self) -> None:
        if not IS_WINDOWS or not self.enabled or self._hwnd is None:
            return
        try:
            _shell32.DragAcceptFiles(self._hwnd, False)
            if self._old_proc:
                _user32.SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC, self._old_proc)
        except Exception as exc:
            logger.debug("卸载拖放失败: %s", exc)
        finally:
            self.enabled = False
            self._proc = None
            self._old_proc = None

    # ---------------- 窗口过程 ----------------
    def _wndproc(self, hwnd, msg, wparam, lparam):  # noqa: ANN001 - C 回调签名固定
        if msg == WM_DROPFILES:
            try:
                paths = self._extract_paths(wparam)
                if paths:
                    # 在 Tk 事件循环里执行，避免在 Windows 消息回调里直接操作控件
                    self.widget.after(0, lambda p=paths: self._deliver(p))
            except Exception as exc:
                logger.error("处理拖放失败: %s", exc, exc_info=True)
            finally:
                try:
                    _shell32.DragFinish(wparam)
                except Exception:
                    pass
            return 0
        return _user32.CallWindowProcW(
            self._old_proc, hwnd, msg, wparam, lparam
        )

    @staticmethod
    def _extract_paths(hdrop) -> list[str]:  # noqa: ANN001
        count = _shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        paths: list[str] = []
        for index in range(count):
            length = _shell32.DragQueryFileW(hdrop, index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            _shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
            if buffer.value:
                paths.append(buffer.value)
        return paths

    def _deliver(self, paths: list[str]) -> None:
        try:
            self.on_drop(paths)
        except Exception as exc:
            logger.error("拖放回调失败: %s", exc, exc_info=True)


def describe_drop(paths: list[str]) -> tuple[list[str], str]:
    """把拖进来的路径整理成"歌曲文件夹列表"，并给一句人话说明。

    资源管理器里可能拖进来：文件夹本身、文件夹里的某个文件、多个混合项。
    文件的情况取它的父目录（用户往往直接拖 info.dat 或 .egg）。
    """
    folders: list[str] = []
    for path in paths:
        candidate = path
        if os.path.isfile(candidate):
            candidate = os.path.dirname(candidate)
        candidate = os.path.normpath(candidate)
        if os.path.isdir(candidate) and candidate not in folders:
            folders.append(candidate)
    if not folders:
        return [], "拖进来的内容里没有文件夹"
    return folders, f"收到 {len(folders)} 个文件夹"
