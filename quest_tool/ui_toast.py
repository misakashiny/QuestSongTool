"""轻量通知（Toast / Snackbar）。

原版所有反馈都是一个模态 `messagebox`：
* 每次都要点"确定"，复制 30 首歌的过程里反复打断；
* 模态窗口会抢焦点，自动化/连续操作很烦；
* 而且它只能显示纯文本，没法区分"成功/警告/失败"的语气。

这里做的是**非模态**通知条：
* 从主窗口右下角滑入，几秒后自动淡出（鼠标悬停时暂停）；
* 支持 信息 / 成功 / 警告 / 错误 四种语气（配色 + 图标）；
* 同内容的通知会合并计数，避免刷屏；
* 重要错误可带一个"详情"按钮，需要时再展开（而不是每次都糊一大段文字）。

实现细节：Toplevel + `overrideredirect(True)` 做无边框浮层；
位置计算基于父窗口的实时几何，所以窗口移动/缩放后新通知依然贴在右下角。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass

import customtkinter as ctk

from .constants import resolve_font
from .theme import FONT, RADIUS, SPACE, T


@dataclass
class Notification:
    """一条通知的展示内容。"""

    text: str
    level: str = "info"  # info | success | warning | error
    detail: str = ""
    timeout_ms: int = 3200

    @property
    def icon(self) -> str:
        return {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
        }.get(self.level, "ℹ️")

    def accent_dark(self) -> str:
        return {
            "info": "ACCENT",
            "success": "SUCCESS",
            "warning": "WARNING",
            "error": "DANGER",
        }.get(self.level, "ACCENT")


class ToastHost:
    """主窗口右下角的通知宿主。"""

    MAX_VISIBLE = 3
    MARGIN = 18
    WIDTH = 380

    def __init__(self, master: ctk.CTk) -> None:
        self.master = master
        self.font = resolve_font(master)
        self._stack: list[ctk.CTkToplevel] = []
        self._timers: dict[ctk.CTkToplevel, str] = {}
        self._last_text = ""
        self._repeat = 0

    # ---------------- 对外接口 ----------------
    def info(self, text: str, detail: str = "") -> None:
        self.show(Notification(text, "info", detail))

    def success(self, text: str, detail: str = "") -> None:
        self.show(Notification(text, "success", detail))

    def warning(self, text: str, detail: str = "") -> None:
        self.show(Notification(text, "warning", detail))

    def error(self, text: str, detail: str = "") -> None:
        # 错误停留更久，让人来得及看清
        self.show(Notification(text, "error", detail, timeout_ms=6000))

    def show(self, note: Notification) -> None:
        if not self._alive():
            return

        # 同一条消息连续出现时合并成 "×N"，而不是叠三层
        if note.text == self._last_text and self._stack:
            self._repeat += 1
            try:
                self._stack[-1].body.configure(text=f"{note.icon} {note.text}  ×{self._repeat + 1}")
                self._restart_timer(self._stack[-1], note.timeout_ms)
                return
            except Exception:
                pass
        else:
            self._last_text = note.text
            self._repeat = 0

        while len(self._stack) >= self.MAX_VISIBLE:
            self._dismiss(self._stack[0])

        try:
            window = self._build(note)
        except tk.TclError:
            return
        self._stack.append(window)
        self._relayout()
        self._restart_timer(window, note.timeout_ms)

    def clear(self) -> None:
        for window in list(self._stack):
            self._dismiss(window)

    # ---------------- 内部 ----------------
    def _alive(self) -> bool:
        try:
            return bool(self.master.winfo_exists())
        except tk.TclError:
            return False

    def _build(self, note: Notification) -> ctk.CTkToplevel:
        window = ctk.CTkToplevel(self.master)
        window.overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        window.configure(fg_color=T["BG_ELEVATED"])

        shell = ctk.CTkFrame(
            window,
            corner_radius=RADIUS.PANEL,
            fg_color=T["BG_ELEVATED"],
            border_width=1,
            border_color=T["BORDER_STRONG"],
        )
        shell.pack(fill="both", expand=True)

        # 左侧语义色竖条（比整块着色更克制）
        stripe = ctk.CTkFrame(shell, width=4, corner_radius=2, fg_color=T[note.accent_dark()])
        stripe.pack(side="left", fill="y", padx=(0, 0), pady=0)

        body = ctk.CTkFrame(shell, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True, padx=(SPACE.S, SPACE.M), pady=SPACE.S)

        window.body = ctk.CTkLabel(  # type: ignore[attr-defined]
            body,
            text=f"{note.icon} {note.text}",
            anchor="w",
            justify="left",
            wraplength=self.WIDTH - 70,
            font=(self.font, FONT.BODY),
            text_color=T["TEXT_PRIMARY"],
        )
        window.body.pack(fill="x")  # type: ignore[attr-defined]

        if note.detail:
            detail_label = ctk.CTkLabel(
                body,
                text=note.detail,
                anchor="w",
                justify="left",
                wraplength=self.WIDTH - 70,
                font=(self.font, FONT.SMALL),
                text_color=T["TEXT_SECONDARY"],
            )
            detail_label.pack(fill="x", pady=(SPACE.XXS, 0))

        close = ctk.CTkButton(
            shell,
            text="✕",
            width=22,
            height=22,
            corner_radius=RADIUS.CHIP,
            font=(self.font, FONT.SMALL),
            fg_color="transparent",
            hover_color=T["BG_HOVER"],
            text_color=T["TEXT_MUTED"],
            command=lambda: self._dismiss(window),
        )
        close.pack(side="right", padx=(0, SPACE.S), pady=SPACE.S)

        # 悬停暂停自动消失
        for widget in (shell, body, window.body, close):  # type: ignore[list-item]
            widget.bind("<Enter>", lambda _e, w=window: self._pause(w))
            widget.bind("<Leave>", lambda _e, w=window: self._restart_timer(w, 1600))
        window.bind("<Button-1>", lambda _e: self._dismiss(window))
        return window

    def _relayout(self) -> None:
        """把通知从下往上叠放在主窗口右下角。"""
        if not self._alive():
            return
        try:
            self.master.update_idletasks()
            base_x = self.master.winfo_rootx() + self.master.winfo_width()
            base_y = self.master.winfo_rooty() + self.master.winfo_height()
        except tk.TclError:
            return

        offset = self.MARGIN
        for window in reversed(self._stack):
            try:
                window.update_idletasks()
                height = window.winfo_reqheight()
                width = self.WIDTH
                x = base_x - width - self.MARGIN
                y = base_y - height - offset
                window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
                offset += height + SPACE.S
            except tk.TclError:
                continue

    def _restart_timer(self, window: ctk.CTkToplevel, timeout_ms: int) -> None:
        self._cancel_timer(window)
        try:
            self._timers[window] = window.after(timeout_ms, lambda w=window: self._dismiss(w))
        except tk.TclError:
            pass

    def _pause(self, window: ctk.CTkToplevel) -> None:
        self._cancel_timer(window)

    def _cancel_timer(self, window: ctk.CTkToplevel) -> None:
        timer = self._timers.pop(window, None)
        if timer:
            try:
                window.after_cancel(timer)
            except (tk.TclError, ValueError):
                pass

    def _dismiss(self, window: ctk.CTkToplevel) -> None:
        self._cancel_timer(window)
        if window in self._stack:
            self._stack.remove(window)
        try:
            window.destroy()
        except tk.TclError:
            pass
        self._relayout()
