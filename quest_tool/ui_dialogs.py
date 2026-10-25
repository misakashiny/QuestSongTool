"""统一模态对话框集合（确认 / 提示 / 评价编辑 / 文本输入 / 设备路径 / 进度）。

为什么要重写：原版把每个对话框都单独实现了一遍 —— DeleteConfirmDialog、ConfirmDialog、
CommentEditDialog，外加一个内联的帮助窗口，四份几乎相同的 ``transient`` + ``grab_set`` +
``wait_window`` 代码。重复之外，里面还藏着两个**必现**缺陷：

1. **居中算错**：原版在 ``update_idletasks()`` 之后仍然读 ``winfo_width()/winfo_height()``。
   此刻窗口还没被窗口管理器映射，Tk 只会返回 1 —— 于是对话框被摆到屏幕右下角，
   用户感受就是"对话框跑偏了/跑到屏幕外"。本模块统一改用
   ``winfo_reqwidth()/reqheight()``（布局请求尺寸，未映射时也可用）。
2. **grab_set 太早**：窗口未映射就抓取输入，Windows 上会抛
   ``TclError: grab failed: window not viewable``。本模块统一先 ``wait_visibility()`` 再抓。

统一之后所有对话框都是一行式用法::

    if ConfirmDialog(parent, title="确认删除", message="…", confirm_is_danger=True).show():
        …

约定（子类必须遵守）：
* **只构造、不 show() 是安全的**：构造过程不抓取输入、不阻塞、不启动线程，
  因此可以在没有 ``mainloop()`` 的自动化测试里构造全部对话框做冒烟测试；
* ``show()`` 返回 ``self.result``；取消/关闭时保留子类在 ``__init__`` 里设定的默认值
  （``ConfirmDialog`` 为 ``False``，其余为 ``None``）；
* 只用 emoji + 文字，不引用任何外部图片资源（打包后不会丢图）。
"""

from __future__ import annotations

import os
import tkinter as tk
from typing import TYPE_CHECKING, Any, Callable, Sequence

import customtkinter as ctk

from .theme import T
from .constants import resolve_font
from .logging_setup import get_logger
from .messages import tr

if TYPE_CHECKING:  # 仅用于类型标注：运行时在方法内部延迟导入，避免与 ui_devicepicker 循环引用
    from .ui_devicepicker import PickedPath

logger = get_logger("dialogs")

__all__ = [
    "ModalDialog",
    "ConfirmDialog",
    "InfoDialog",
    "CommentEditDialog",
    "TextPromptDialog",
    "DevicePathDialog",
    "ProgressDialog",
]

BODY_FONT_SIZE = 14
TITLE_FONT_SIZE = 20
BUTTON_HEIGHT = 38


class ModalDialog(ctk.CTkToplevel):
    """模态对话框基类：只负责窗口壳（尺寸、居中、模态、Esc/Enter、返回值）。

    子类实现 ``_create_body()`` 搭内容，必要时覆盖 ``_create_buttons()``、
    ``_on_default()``、``_focus_initial()``。
    """

    ICON = "❓"
    DEFAULT_TEXT = "确定"
    CANCEL_TEXT = "取消"
    PAD = 18

    def __init__(
        self,
        master: tk.Misc | None,
        title: str,
        *,
        size: tuple[int, int] = (520, 300),
        resizable: tuple[bool, bool] = (False, False),
    ) -> None:
        super().__init__(master)

        # 这些属性必须在 _create_body() 之前就位：子类的建体逻辑会读它们
        self.result: Any = None
        self.font: str = resolve_font(self)
        self._size = (max(300, int(size[0])), max(160, int(size[1])))
        self._grabbed = False
        self._destroyed = False
        self._timers: set[str] = set()  # 窗口销毁时要取消的 after 定时器

        self.title(title)
        self.resizable(bool(resizable[0]), bool(resizable[1]))
        self.geometry(f"{self._size[0]}x{self._size[1]}")
        if master is not None:
            try:
                self.transient(master)
            except tk.TclError:  # 父窗口已销毁等极端情况
                pass
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _event: self._on_cancel())
        self.bind("<Return>", lambda _event: self._on_default())

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew", padx=self.PAD, pady=(16, 4))
        self.button_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.button_bar.grid(row=1, column=0, sticky="ew", padx=self.PAD, pady=(8, 16))

        self._create_body(self.body)
        self._create_buttons(self.button_bar)

        # 先把内容排版好，再算真实尺寸 —— 顺序反了就会像原版那样把对话框摆歪
        self.update_idletasks()
        self._center()

    # ------------------------------------------------------------------
    # 位置 / 模态
    # ------------------------------------------------------------------
    def _center(self) -> None:
        """把对话框摆到父窗口正中（父窗口不可见时退回屏幕居中）。

        关键点：窗口此刻尚未映射，``winfo_width()`` 只会返回 1，必须用
        ``winfo_reqwidth()/reqheight()`` 取"内容真实需要的尺寸"。
        """
        self.update_idletasks()
        # CTk 的 geometry() 会把宽高乘上窗口缩放，但不动 x/y —— 所以位置要按缩放后的尺寸算
        try:
            scale = float(self._get_window_scaling())  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - 非 CTk 环境下退化
            scale = 1.0
        scale = scale if scale > 0 else 1.0

        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        phys_w = max(int(round(self._size[0] * scale)), int(self.winfo_reqwidth()), 1)
        phys_h = max(int(round(self._size[1] * scale)), int(self.winfo_reqheight()), 1)
        phys_w = min(phys_w, max(360, screen_w - 40))
        phys_h = min(phys_h, max(240, screen_h - 80))

        origin_x, origin_y = 0, 0
        area_w, area_h = screen_w, screen_h
        parent = self.master
        if parent is not None:
            try:
                if parent.winfo_exists() and parent.winfo_viewable():
                    origin_x, origin_y = parent.winfo_rootx(), parent.winfo_rooty()
                    area_w, area_h = parent.winfo_width(), parent.winfo_height()
            except tk.TclError:
                pass
        if area_w <= 1 or area_h <= 1:  # 父窗口被 withdraw / 尚未映射
            origin_x, origin_y, area_w, area_h = 0, 0, screen_w, screen_h

        x = max(0, min(origin_x + (area_w - phys_w) // 2, screen_w - phys_w))
        y = max(0, min(origin_y + (area_h - phys_h) // 2, screen_h - phys_h))
        self.geometry(f"{int(round(phys_w / scale))}x{int(round(phys_h / scale))}+{x}+{y}")

    def _make_modal(self) -> None:
        """抓取输入并把焦点收回本窗口（``wait_visibility`` 之后再 ``grab_set``）。"""
        if self._grabbed or not self.winfo_exists():
            return
        try:
            self.wait_visibility()  # 未映射就 grab_set，Windows 会抛 window not viewable
            self.lift()
            self.grab_set()
            self._grabbed = True
        except tk.TclError as exc:  # 抓取失败不影响对话框本身可用
            logger.debug("对话框抓取输入失败: %s", exc)
        try:
            self.focus_set()
        except tk.TclError:
            pass

    def _release_grab(self) -> None:
        if not self._grabbed:
            return
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self._grabbed = False

    # ------------------------------------------------------------------
    # 内容 / 按钮（子类实现）
    # ------------------------------------------------------------------
    def _create_body(self, parent: ctk.CTkFrame) -> None:
        """搭内容区。``parent`` 已经在 grid 里，直接 pack/grid 即可。"""
        raise NotImplementedError("子类必须实现 _create_body()")

    def _primary_colors(self) -> tuple[str, str]:
        """主按钮配色（危险操作由子类覆盖成红色）。"""
        return T["ACCENT"], T["ACCENT_HOVER"]

    def _create_buttons(self, parent: ctk.CTkFrame) -> None:
        """默认按钮栏：右侧 [确定][取消]。"""
        fg, hover = self._primary_colors()
        self.cancel_button = ctk.CTkButton(
            parent,
            text=self.CANCEL_TEXT,
            width=120,
            height=BUTTON_HEIGHT,
            font=(self.font, BODY_FONT_SIZE),
            fg_color=T["NEUTRAL"],
            hover_color=T["NEUTRAL_HOVER"],
            command=self._on_cancel,
        )
        self.cancel_button.pack(side="right", padx=(10, 0))
        self.default_button = ctk.CTkButton(
            parent,
            text=self.DEFAULT_TEXT,
            width=150,
            height=BUTTON_HEIGHT,
            font=(self.font, BODY_FONT_SIZE, "bold"),
            fg_color=fg,
            hover_color=hover,
            command=self._on_default,
        )
        self.default_button.pack(side="right")
        self.confirm_button = self.default_button  # 别名：调用方两种叫法都能用

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def _on_default(self) -> None:
        """Enter / 主按钮 = 确定。子类可覆盖成"保存""关闭"等。"""
        self._finish(True)

    def _on_cancel(self) -> None:
        """Esc / 右上角关闭 / 取消按钮 = 放弃。

        不修改 ``self.result`` —— 子类在 ``__init__`` 里设定的默认值（False / None）
        就是"取消"的返回值。
        """
        self._destroy_self()

    def _focus_initial(self) -> None:
        """子类可覆盖：show() 之后把焦点放到最该输入的控件上。"""

    def _schedule(self, delay_ms: int, func: Callable[[], None]) -> str | None:
        """登记一个延时任务；窗口销毁时自动取消。

        否则窗口先被销毁、定时器随后触发时，Tk 会抛 ``invalid command name``。
        """
        if self._destroyed:
            return None
        holder: dict[str, str] = {}

        def runner() -> None:
            self._timers.discard(holder.get("id", ""))  # 已触发的不再留登记
            func()

        try:
            timer = self.after(delay_ms, runner)
        except tk.TclError:  # pragma: no cover
            return None
        holder["id"] = timer
        self._timers.add(timer)
        return timer

    def _cancel_timers(self) -> None:
        for timer in list(self._timers):
            try:
                self.after_cancel(timer)
            except (tk.TclError, ValueError):  # pragma: no cover
                pass
        self._timers.clear()

    def _finish(self, result: Any) -> None:
        """带结果关闭。"""
        self.result = result
        self._destroy_self()

    def _destroy_self(self) -> None:
        """幂等销毁（重复点按钮 / after 回调晚到都不会炸）。"""
        if self._destroyed:
            return
        self._destroyed = True
        self._cancel_timers()
        self._release_grab()
        try:
            self.destroy()
        except tk.TclError:  # pragma: no cover - 已被父窗口连带销毁
            pass

    def close(self) -> None:
        """外部关闭（非阻塞对话框用）。"""
        self._destroy_self()

    def show(self) -> Any:
        """显示 → 进入模态 → 返回 ``self.result``。"""
        self._make_modal()
        try:
            self._focus_initial()
        except tk.TclError:  # pragma: no cover
            pass
        try:
            self.wait_window()
        except tk.TclError:  # 等待期间窗口被销毁
            pass
        return self.result


# --------------------------------------------------------------------------------------
# 确认
# --------------------------------------------------------------------------------------
class ConfirmDialog(ModalDialog):
    """确认对话框：图标 + 标题 + 正文 + 可选详情 + 可选"下次不再询问"。

    ``show()`` 返回 ``bool``（取消/关闭 = False）。破坏性操作用
    ``confirm_is_danger=True`` 把主按钮和标题染成红色。
    """

    def __init__(
        self,
        master: tk.Misc | None,
        title: str = "确认操作",
        message: str = "确定要执行此操作吗？",
        detail: str = "",
        *,
        icon: str | None = None,
        confirm_text: str = "确定",
        cancel_text: str = "取消",
        confirm_is_danger: bool = False,
        show_again_checkbox: bool = False,
        checkbox_text: str = "下次不再询问",
        size: tuple[int, int] = (520, 320),
    ) -> None:
        # 建体之前就得备好：_create_body() 会读它们
        self._message = message
        self._detail = detail
        self._is_danger = bool(confirm_is_danger)
        self._icon = icon if icon is not None else ("⚠️" if self._is_danger else self.ICON)
        self._heading = title
        self._show_again = bool(show_again_checkbox)
        self._checkbox_text = checkbox_text
        self.again_var: ctk.BooleanVar | None = None
        self.DEFAULT_TEXT = confirm_text
        self.CANCEL_TEXT = cancel_text
        self._wrap = max(240, size[0] - 110)

        super().__init__(master, title, size=size, resizable=(False, False))
        self.result = False  # 取消 = False（不是 None）

    # ---------------- 内容 ----------------
    def _primary_colors(self) -> tuple[str, str]:
        return (T["DANGER"], T["DANGER_HOVER"]) if self._is_danger else (
            T["ACCENT"],
            T["ACCENT_HOVER"],
        )

    def _create_body(self, parent: ctk.CTkFrame) -> None:
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", pady=(2, 10))
        ctk.CTkLabel(head, text=self._icon, font=(self.font, 38)).pack(side="left", padx=(2, 12))
        ctk.CTkLabel(
            head,
            text=self._heading,
            font=(self.font, TITLE_FONT_SIZE, "bold"),
            text_color=T["DANGER"] if self._is_danger else None,
            anchor="w",
            justify="left",
        ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            parent,
            text=self._message,
            font=(self.font, BODY_FONT_SIZE),
            wraplength=self._wrap,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=2)
        if self._detail:
            ctk.CTkLabel(
                parent,
                text=self._detail,
                font=(self.font, BODY_FONT_SIZE - 2),
                text_color=T["NEUTRAL"],
                wraplength=self._wrap,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=2, pady=(8, 0))
        if self._show_again:
            self.again_var = ctk.BooleanVar(master=self, value=False)
            ctk.CTkCheckBox(
                parent,
                text=self._checkbox_text,
                variable=self.again_var,
                font=(self.font, BODY_FONT_SIZE - 1),
                checkbox_width=20,
                checkbox_height=20,
                corner_radius=4,
                fg_color=T["ACCENT"],
                hover_color=T["ACCENT_HOVER"],
            ).pack(anchor="w", padx=2, pady=(14, 0))

    # ---------------- 结果 ----------------
    @property
    def dont_ask_again(self) -> bool:
        """用户是否勾选了"下次不再询问"（未显示复选框时恒为 False）。"""
        if self.again_var is None:
            return False
        try:
            return bool(self.again_var.get())
        except tk.TclError:  # pragma: no cover - 已销毁
            return False


# --------------------------------------------------------------------------------------
# 提示
# --------------------------------------------------------------------------------------
class InfoDialog(ModalDialog):
    """提示 / 详情对话框：短文本用标签，长文本自动换成只读滚动文本框并支持一键复制。

    ``show()`` 恒返回 ``None``。
    """

    #: 超过这个字符数（或行数）就改用可滚动文本框，避免长文案把对话框撑爆
    LONG_TEXT_THRESHOLD = 300
    LONG_LINE_THRESHOLD = 8

    def __init__(
        self,
        master: tk.Misc | None,
        title: str = "提示",
        message: str = "",
        *,
        detail: str = "",
        icon: str = "ℹ️",
        size: tuple[int, int] | None = None,
        copy_text: str | None = None,
    ) -> None:
        self._message = message or ""
        self._detail = detail or ""
        self._icon = icon
        self._long = (
            len(self._message) > self.LONG_TEXT_THRESHOLD
            or self._message.count("\n") > self.LONG_LINE_THRESHOLD
            or len(self._detail) > self.LONG_TEXT_THRESHOLD
        )
        self._copy_payload = copy_text if copy_text is not None else "\n\n".join(
            part for part in (self._message, self._detail) if part
        )
        self._copy_button: ctk.CTkButton | None = None
        self.textbox: ctk.CTkTextbox | None = None
        if size is None:
            size = (640, 440) if self._long else (560, 300)
        self._wrap = max(240, size[0] - 70)

        super().__init__(master, title, size=size, resizable=(bool(self._long), bool(self._long)))

    # ---------------- 内容 ----------------
    def _create_body(self, parent: ctk.CTkFrame) -> None:
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(head, text=self._icon, font=(self.font, 26)).pack(side="left", padx=(2, 10))
        ctk.CTkLabel(
            head,
            text=self.title(),
            font=(self.font, TITLE_FONT_SIZE - 2, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        if self._long:
            self.textbox = ctk.CTkTextbox(
                parent,
                wrap="word",
                font=(self.font, BODY_FONT_SIZE),
                activate_scrollbars=True,
            )
            self.textbox.pack(fill="both", expand=True, pady=(2, 6))
            payload = "\n\n".join(part for part in (self._message, self._detail) if part)
            self.textbox.insert("1.0", payload)
            self.textbox.configure(state="disabled")  # 只读：用户仍可选中/复制
        else:
            ctk.CTkLabel(
                parent,
                text=self._message,
                font=(self.font, BODY_FONT_SIZE),
                wraplength=self._wrap,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=2)
            if self._detail:
                ctk.CTkLabel(
                    parent,
                    text=self._detail,
                    font=(self.font, BODY_FONT_SIZE - 2),
                    text_color=T["NEUTRAL"],
                    wraplength=self._wrap,
                    justify="left",
                    anchor="w",
                ).pack(fill="x", padx=2, pady=(8, 0))

    def _create_buttons(self, parent: ctk.CTkFrame) -> None:
        self.cancel_button = ctk.CTkButton(
            parent,
            text=tr("dlg.close_esc"),
            width=130,
            height=BUTTON_HEIGHT,
            font=(self.font, BODY_FONT_SIZE, "bold"),
            fg_color=T["ACCENT"],
            hover_color=T["ACCENT_HOVER"],
            command=self._on_cancel,
        )
        self.cancel_button.pack(side="right")
        self.default_button = self.cancel_button
        self.confirm_button = self.cancel_button
        self._copy_button = ctk.CTkButton(
            parent,
            text=tr("dlg.copy_clipboard"),
            width=150,
            height=BUTTON_HEIGHT,
            font=(self.font, BODY_FONT_SIZE),
            fg_color=T["NEUTRAL"],
            hover_color=T["NEUTRAL_HOVER"],
            command=self.copy_to_clipboard,
        )
        self._copy_button.pack(side="right", padx=(0, 10))

    # ---------------- 行为 ----------------
    def _on_default(self) -> None:
        """Enter = 关闭（不做复制，避免误触）。"""
        self._destroy_self()

    def copy_to_clipboard(self) -> None:
        """把正文复制到剪贴板，并在按钮上给 1.2 秒的视觉反馈。"""
        try:
            self.clipboard_clear()
            self.clipboard_append(self._copy_payload)
            self.update_idletasks()
        except tk.TclError as exc:  # pragma: no cover - 极端情况下剪贴板不可用
            logger.warning("复制到剪贴板失败: %s", exc)
            return
        if self._copy_button is None:
            return
        self._copy_button.configure(text=tr("dlg.copied"), fg_color=T["SUCCESS"])
        self._schedule(1200, self._restore_copy_button)

    def _restore_copy_button(self) -> None:
        if self._copy_button is None or self._destroyed:
            return
        try:
            if self._copy_button.winfo_exists():
                self._copy_button.configure(text=tr("dlg.copy_clipboard"), fg_color=T["NEUTRAL"])
        except tk.TclError:  # pragma: no cover
            pass


# --------------------------------------------------------------------------------------
# 评价 / 评论编辑
# --------------------------------------------------------------------------------------
class CommentEditDialog(ModalDialog):
    """多行评价编辑器：实时字数（默认上限 500）+ Ctrl+Enter 保存。

    ``show()`` 返回 ``str | None``（None = 取消）。
    """

    def __init__(
        self,
        master: tk.Misc | None,
        title: str = "编辑评价",
        initial_text: str = "",
        max_length: int = 500,
        *,
        song_name: str = "",
        size: tuple[int, int] = (560, 420),
    ) -> None:
        self._initial = initial_text or ""
        self._max_length = max(1, int(max_length))
        self._song_name = song_name or ""
        super().__init__(master, title, size=size, resizable=(True, True))
        self.result = None

    # ---------------- 内容 ----------------
    def _create_body(self, parent: ctk.CTkFrame) -> None:
        if self._song_name:
            ctk.CTkLabel(
                parent,
                text=f"🎵 {self._song_name}",
                font=(self.font, BODY_FONT_SIZE, "bold"),
                anchor="w",
            ).pack(fill="x", padx=2, pady=(0, 6))

        self.textbox = ctk.CTkTextbox(parent, wrap="word", font=(self.font, BODY_FONT_SIZE))
        self.textbox.pack(fill="both", expand=True, padx=2, pady=(2, 6))
        self.textbox.insert("1.0", self._initial)

        foot = ctk.CTkFrame(parent, fg_color="transparent")
        foot.pack(fill="x", padx=2)
        self.error_label = ctk.CTkLabel(
            foot, text="", font=(self.font, BODY_FONT_SIZE - 2), text_color=T["DANGER"], anchor="w"
        )
        self.error_label.pack(side="left")
        self.counter_label = ctk.CTkLabel(
            foot, text="", font=(self.font, BODY_FONT_SIZE - 2), text_color=T["NEUTRAL"]
        )
        self.counter_label.pack(side="right")

        # 输入时刷新字数；Ctrl+Enter 保存
        self.textbox.bind("<KeyRelease>", self._on_text_changed)
        self.textbox.bind("<Control-Return>", self._on_ctrl_return)
        # 粘贴/剪切不是逐字输入，等控件更新完再刷新一次计数
        self.textbox.bind("<<Paste>>", lambda _e: self._schedule(1, self._update_counter))
        self.textbox.bind("<<Cut>>", lambda _e: self._schedule(1, self._update_counter))
        # 覆盖基类的"Enter=确定"：在文本框里 Enter 应该换行，而不是保存
        self.bind("<Return>", self._on_return_in_editor)
        self._update_counter()
        self._schedule(60, self._place_cursor_at_end)

    def _create_buttons(self, parent: ctk.CTkFrame) -> None:
        self.cancel_button = ctk.CTkButton(
            parent,
            text=self.CANCEL_TEXT,
            width=120,
            height=BUTTON_HEIGHT,
            font=(self.font, BODY_FONT_SIZE),
            fg_color=T["NEUTRAL"],
            hover_color=T["NEUTRAL_HOVER"],
            command=self._on_cancel,
        )
        self.cancel_button.pack(side="right", padx=(10, 0))
        self.default_button = ctk.CTkButton(
            parent,
            text=tr("dlg.save_enter"),
            width=170,
            height=BUTTON_HEIGHT,
            font=(self.font, BODY_FONT_SIZE, "bold"),
            fg_color=T["ACCENT"],
            hover_color=T["ACCENT_HOVER"],
            command=self._on_default,
        )
        self.default_button.pack(side="right")
        self.confirm_button = self.default_button

    # ---------------- 行为 ----------------
    @property
    def text(self) -> str:
        """当前文本（已去掉首尾空白）。"""
        try:
            return self.textbox.get("1.0", "end-1c").strip()
        except tk.TclError:  # pragma: no cover
            return ""

    def _update_counter(self) -> None:
        length = len(self.text)
        over = length > self._max_length
        try:
            self.counter_label.configure(
                text=f"{length} / {self._max_length}",
                text_color=T["DANGER"] if over else T["NEUTRAL"],
            )
        except tk.TclError:  # pragma: no cover
            pass

    def _on_text_changed(self, _event: tk.Event | None = None) -> None:
        self._update_counter()
        try:  # 一开始输入就把旧的报错清掉
            if self.error_label.cget("text"):
                self.error_label.configure(text="")
        except tk.TclError:  # pragma: no cover
            pass

    def _on_ctrl_return(self, _event: tk.Event | None = None) -> str:
        self._on_default()
        return "break"

    def _on_return_in_editor(self, _event: tk.Event | None = None) -> str | None:
        """Enter 在文本框里换行；焦点在按钮上时才保存。"""
        if isinstance(self.focus_get(), tk.Text):
            return None  # 交给 Text 的类绑定插入换行
        self._on_default()
        return "break"

    def _place_cursor_at_end(self) -> None:
        if self._destroyed:
            return
        try:
            self.textbox.focus_set()
            self.textbox.mark_set("insert", "end-1c")
            self.textbox.see("end")
        except tk.TclError:  # pragma: no cover
            pass

    def _on_default(self) -> None:
        text = self.text
        if len(text) > self._max_length:
            self.error_label.configure(
                text=f"最多 {self._max_length} 字，当前 {len(text)} 字，请精简后再保存"
            )
            return
        self._finish(text)


# --------------------------------------------------------------------------------------
# 单行输入
# --------------------------------------------------------------------------------------
class TextPromptDialog(ModalDialog):
    """单行输入框：支持初值、占位提示与可选校验函数。

    ``validate(value) -> str | None`` 返回错误文案时对话框保持打开。
    ``show()`` 返回 ``str | None``（None = 取消）。
    """

    def __init__(
        self,
        master: tk.Misc | None,
        title: str = "请输入",
        prompt: str = "",
        *,
        initial: str = "",
        placeholder: str = "",
        validate: Callable[[str], str | None] | None = None,
        allow_empty: bool = False,
        hint: str = "",
        confirm_text: str = "确定",
        cancel_text: str = "取消",
        size: tuple[int, int] = (560, 300),
    ) -> None:
        self._prompt = prompt
        self._initial = initial
        self._placeholder = placeholder
        self._validate = validate
        self._allow_empty = bool(allow_empty)
        self._hint = hint
        self.DEFAULT_TEXT = confirm_text
        self.CANCEL_TEXT = cancel_text
        self._wrap = max(240, size[0] - 70)
        super().__init__(master, title, size=size, resizable=(False, False))
        self.result = None

    # ---------------- 内容 ----------------
    def _create_body(self, parent: ctk.CTkFrame) -> None:
        if self._prompt:
            ctk.CTkLabel(
                parent,
                text=self._prompt,
                font=(self.font, BODY_FONT_SIZE),
                wraplength=self._wrap,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=2, pady=(2, 10))

        self.entry = ctk.CTkEntry(
            parent,
            font=(self.font, BODY_FONT_SIZE),
            height=BUTTON_HEIGHT,
            placeholder_text=self._placeholder,
        )
        self.entry.pack(fill="x", padx=2)
        if self._initial:
            self.entry.insert(0, self._initial)
        self.entry.bind("<KeyRelease>", self._on_key)

        self.error_label = ctk.CTkLabel(
            parent,
            text="",
            font=(self.font, BODY_FONT_SIZE - 2),
            text_color=T["DANGER"],
            wraplength=self._wrap,
            justify="left",
            anchor="w",
        )
        self.error_label.pack(fill="x", padx=2, pady=(6, 0))
        if self._hint:
            ctk.CTkLabel(
                parent,
                text=self._hint,
                font=(self.font, BODY_FONT_SIZE - 3),
                text_color=T["NEUTRAL"],
                wraplength=self._wrap,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=2, pady=(4, 0))

    # ---------------- 行为 ----------------
    @property
    def value(self) -> str:
        try:
            return self.entry.get().strip()
        except tk.TclError:  # pragma: no cover
            return ""

    def _focus_initial(self) -> None:
        try:
            self.entry.focus_set()
            self.entry.select_range(0, "end")
            self.entry.icursor("end")
        except tk.TclError:  # pragma: no cover
            pass

    def _on_key(self, _event: tk.Event | None = None) -> None:
        try:
            if self.error_label.cget("text"):
                self.error_label.configure(text="")
        except tk.TclError:  # pragma: no cover
            pass

    def _on_default(self) -> None:
        value = self.value
        if not value and not self._allow_empty:
            self.error_label.configure(text=tr("dlg.content_empty"))
            return
        if self._validate is not None:
            try:
                error = self._validate(value)
            except Exception as exc:  # 校验函数自身出错不能把窗口卡死
                logger.warning("输入校验函数异常: %s", exc)
                error = f"校验失败：{exc}"
            if error:
                self.error_label.configure(text=str(error))
                return
        self._finish(value)


# --------------------------------------------------------------------------------------
# 设备路径
# --------------------------------------------------------------------------------------
class DevicePathDialog(ModalDialog):
    """手动指定 Quest 歌曲目录（自动探测失败时的兜底）。

    ``show()`` 返回**字符串**（取消返回 None），格式与主程序的约定一致：

    * adb 候选   → ``adb:/sdcard/...``（``adb:`` 前缀由本对话框补上）
    * 盘符候选   → ``D:\\Songs\\CustomLevels``
    * MTP 候选   → shell 命名空间路径（``::{GUID}\\...`` 或 ``此电脑\\Quest 3\\...``）
    * 手动输入   → 只做去引号 / 去空白 / 盘符大写（``fsapi.normalize_user_path``）

    ``self.picked_path`` 会额外保留 ``DeviceFolderPicker`` 返回的 ``PickedPath``，
    供需要 kind/usable 的调用方使用。
    """

    def __init__(
        self,
        master: tk.Misc | None,
        title: str = "选择 Quest 歌曲目录",
        candidates: Sequence[object] = (),
        adb_path: str = "",
        *,
        initial: str = "",
        message: str = "",
        size: tuple[int, int] = (780, 540),
    ) -> None:
        self._candidates = list(candidates)
        self._adb_path = adb_path
        self._initial = initial
        self._message = message or (
            "自动探测没有找到可用的歌曲目录。可以从下面的候选里挑一个，"
            "或点「浏览设备…」进去手动选择，也可以直接把路径粘进输入框。"
        )
        self.picked_path: PickedPath | None = None
        self._wrap = max(240, size[0] - 80)
        super().__init__(master, title, size=size, resizable=(False, False))
        self.result = None

    # ---------------- 内容 ----------------
    def _create_body(self, parent: ctk.CTkFrame) -> None:
        ctk.CTkLabel(
            parent,
            text=self._message,
            font=(self.font, BODY_FONT_SIZE),
            wraplength=self._wrap,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=2, pady=(0, 8))

        box = ctk.CTkScrollableFrame(parent, height=170, label_text="检测到的位置")
        box.pack(fill="both", expand=True, padx=2)
        box.grid_columnconfigure(0, weight=1)
        if self._candidates:
            for row_index, candidate in enumerate(self._candidates):
                self._build_candidate_row(box, candidate, row_index)
        else:
            ctk.CTkLabel(
                box,
                text=tr("dlg.no_device"),
                font=(self.font, BODY_FONT_SIZE - 2),
                text_color=T["NEUTRAL"],
                anchor="w",
            ).grid(row=0, column=0, sticky="ew", padx=6, pady=10)

        picker_row = ctk.CTkFrame(parent, fg_color="transparent")
        picker_row.pack(fill="x", padx=2, pady=(10, 0))
        ctk.CTkLabel(picker_row, text=tr("dlg.path_label"), font=(self.font, BODY_FONT_SIZE)).pack(side="left")
        self.path_var = ctk.StringVar(master=self, value=self._initial)
        self.path_entry = ctk.CTkEntry(
            picker_row,
            textvariable=self.path_var,
            font=(self.font, BODY_FONT_SIZE),
            height=BUTTON_HEIGHT,
            placeholder_text=tr("dlg.path_hint"),
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.path_entry.bind("<KeyRelease>", lambda _e: self._refresh_hint())
        ctk.CTkButton(
            picker_row,
            text=tr("dlg.browse_device"),
            width=130,
            height=BUTTON_HEIGHT,
            font=(self.font, BODY_FONT_SIZE),
            fg_color=T["ACCENT"],
            hover_color=T["ACCENT_HOVER"],
            command=self.browse_device,
        ).pack(side="left")

        util_row = ctk.CTkFrame(parent, fg_color="transparent")
        util_row.pack(fill="x", padx=2, pady=(8, 0))
        ctk.CTkButton(
            util_row,
            text=tr("dlg.open_explorer"),
            width=200,
            height=32,
            font=(self.font, BODY_FONT_SIZE - 2),
            fg_color=T["NEUTRAL"],
            hover_color=T["NEUTRAL_HOVER"],
            command=self.open_explorer,
        ).pack(side="left")
        ctk.CTkLabel(
            util_row,
            text=tr("dlg.mtp_no_drive"),
            font=(self.font, BODY_FONT_SIZE - 3),
            text_color=T["NEUTRAL"],
            anchor="w",
            justify="left",
            wraplength=max(200, self._wrap - 220),
        ).pack(side="left", padx=(10, 0))

        self.hint_label = ctk.CTkLabel(
            parent,
            text="",
            font=(self.font, BODY_FONT_SIZE - 2),
            text_color=T["WARNING"],
            wraplength=self._wrap,
            justify="left",
            anchor="w",
        )
        self.hint_label.pack(fill="x", padx=2, pady=(8, 0))
        self._refresh_hint()

    def _build_candidate_row(self, parent: ctk.CTkFrame, candidate: object, row_index: int) -> None:
        kind = str(getattr(candidate, "kind", "") or "")
        kind_label = str(getattr(candidate, "kind_label", "") or kind or "位置")
        display = str(getattr(candidate, "display", "") or "")
        note = str(getattr(candidate, "note", "") or "")
        usable = bool(getattr(candidate, "usable", False))
        exists = bool(getattr(candidate, "exists", True))

        row = ctk.CTkFrame(parent, fg_color=("gray85", "gray20"), corner_radius=6)
        row.grid(row=row_index, column=0, sticky="ew", padx=4, pady=3)
        row.grid_columnconfigure(1, weight=1)  # 中间的信息列负责占满剩余宽度

        marker = "✅" if usable else ("⚠️" if exists else "❌")
        ctk.CTkLabel(row, text=marker, font=(self.font, 18), width=28).grid(
            row=0, column=0, rowspan=2, sticky="w", padx=(8, 4), pady=8
        )
        ctk.CTkLabel(
            row,
            text=f"{kind_label} · {display}",
            font=(self.font, BODY_FONT_SIZE - 1, "bold"),
            anchor="w",
            justify="left",
            wraplength=max(200, self._wrap - 260),
        ).grid(row=0, column=1, sticky="ew", pady=(8, 0))
        if note:
            ctk.CTkLabel(
                row,
                text=note,
                font=(self.font, BODY_FONT_SIZE - 3),
                text_color=T["NEUTRAL"],
                anchor="w",
                justify="left",
                wraplength=max(200, self._wrap - 260),
            ).grid(row=1, column=1, sticky="ew", pady=(0, 8))
        ctk.CTkButton(
            row,
            text=tr("dlg.use_path"),
            width=110,
            height=30,
            font=(self.font, BODY_FONT_SIZE - 2),
            fg_color=T["ACCENT"] if usable else T["WARNING"],
            hover_color=T["ACCENT_HOVER"] if usable else T["WARNING"],
            command=lambda item=candidate: self.use_candidate(item),
        ).grid(row=0, column=2, rowspan=2, sticky="e", padx=(8, 10), pady=8)

    # ---------------- 候选 / 路径 ----------------
    @staticmethod
    def _candidate_path(candidate: object) -> str:
        """把 QuestCandidate 转成主程序约定的路径字符串。"""
        kind = str(getattr(candidate, "kind", "") or "")
        display = str(getattr(candidate, "display", "") or "")
        location = getattr(candidate, "location", None)
        root = ""
        shell_path = ""
        if location is not None:
            root = str(getattr(location, "root", "") or "")
            shell_path = str(getattr(location, "shell_path", "") or "")
        if kind == "adb":
            return "adb:" + (root or display)
        if kind == "drive":
            return root or display
        if kind == "mtp":
            return shell_path or display
        return display

    def use_candidate(self, candidate: object) -> None:
        """点候选行 = 把它的路径填进输入框。"""
        value = self._candidate_path(candidate)
        self.path_var.set(value)
        self._refresh_hint()
        try:
            self.path_entry.icursor("end")
        except tk.TclError:  # pragma: no cover
            pass

    def browse_device(self) -> None:
        """打开设备文件夹选择器（延迟导入，避免与 ui_devicepicker 循环 import）。"""
        from .ui_devicepicker import DeviceFolderPicker

        picker = DeviceFolderPicker(
            self,
            adb_path=self._adb_path,
            candidates=self._candidates,
            initial=self.path_var.get().strip(),
        )
        picked = picker.show()
        if picked is None:
            return
        self.picked_path = picked
        # adb 结果统一补上 "adb:" 前缀（主程序的约定），MTP/本地保持原样
        self.path_var.set(picked.display if picked.kind == "adb" else picked.path)
        self._refresh_hint()

    def open_explorer(self) -> None:
        """打开"此电脑"：MTP 设备没有盘符，用户只能在这里手动复制。"""
        opener = getattr(os, "startfile", None)  # 仅 Windows 有
        if os.name != "nt" or opener is None:
            self.hint_label.configure(text=tr("dlg.no_explorer"))
            return
        try:
            opener("shell:::{20D04FE0-3AEA-1069-A2D8-08002B30309D}")  # ssfDRIVES = 此电脑
        except Exception as exc:  # 用户环境千奇百怪，失败只提示不抛出
            logger.warning("打开资源管理器失败: %s", exc)
            self.hint_label.configure(text=f"打开资源管理器失败：{exc}")

    def _refresh_hint(self) -> None:
        from .device import describe_path_kind  # 延迟导入：device 只依赖 adb/fsapi/constants，无环

        raw = self.path_var.get().strip()
        if not raw:
            self.hint_label.configure(text=tr("dlg.choose_or_type"))
            return
        kind = describe_path_kind(raw)
        if raw.lower().startswith("adb:"):
            text = f"将使用：{raw[4:]}（ADB 路径，可读写）"
        elif raw.startswith("::") or kind.startswith("Windows Shell"):
            text = (
                "⚠️ 这是 MTP 设备的 shell 命名空间路径：本程序无法直接用 os 读写，"
                "建议装 adb，或用 Explorer 复制到本地后再选本地文件夹。"
            )
        else:
            text = f"将使用：{raw}（{kind}）"
        self.hint_label.configure(text=text)

    # ---------------- 结果 ----------------
    def _on_default(self) -> None:
        from .fsapi import normalize_user_path

        value = normalize_user_path(self.path_var.get())
        if not value:
            self.hint_label.configure(text=tr("dlg.pick_candidate"))
            return
        # 裸的 /sdcard/... 也按 adb 处理，补上前缀（主程序按 "adb:" 识别来源）
        if value.startswith("/") and not value.lower().startswith("adb:"):
            value = "adb:" + value
        self._finish(value)


# --------------------------------------------------------------------------------------
# 进度
# --------------------------------------------------------------------------------------
class ProgressDialog(ModalDialog):
    """长任务的进度窗口（**非阻塞**）。

    与其它对话框不同，它不进入 ``wait_window()``：调用方在后台线程干活，
    再通过 ``after()`` 把 ``set_progress()`` / ``append_log()`` / ``finish()``
    排到 Tk 主线程执行。

    **线程约定**：Tk 不是线程安全的，本类的所有方法都只能在 **Tk 主线程** 调用；
    工作线程请用 ``self.after(0, lambda: dialog.append_log(line))`` 转发。

    ``finish(summary)`` 只把窗口切到"已完成"状态（进度 100%、按钮变"关闭"），
    **不会** 自己 destroy —— 由调用方决定何时 ``close()``，这样用户还能看完日志。
    """

    MAX_LOG_LINES = 2000
    TRIM_TO_LINES = 1500

    def __init__(
        self,
        master: tk.Misc | None,
        title: str = "",
        message: str = "",
        cancel_callback: Callable[[], None] | None = None,
        *,
        pause_callback: Callable[[], bool] | None = None,
        size: tuple[int, int] = (620, 420),
        cancel_text: str = "取消",
        closable: bool = True,
    ) -> None:
        self._cancel_callback = cancel_callback
        #: 传了就多一个「暂停/继续」按钮；调用它应返回**切换后**的暂停状态
        self._pause_callback = pause_callback
        self._message = message or "正在处理，请稍候…"
        self._cancel_text = cancel_text
        self._closable = bool(closable)
        self.cancelled = False
        self.finished = False
        self._wrap = max(240, size[0] - 70)
        super().__init__(master, title or "正在处理…", size=size, resizable=(False, False))
        self.result = None
        try:
            self.protocol("WM_DELETE_WINDOW", self.request_cancel)
            self.bind("<Escape>", lambda _event: self.request_cancel())
        except tk.TclError:  # pragma: no cover
            pass

    # ---------------- 内容 ----------------
    def _create_body(self, parent: ctk.CTkFrame) -> None:
        self.status_label = ctk.CTkLabel(
            parent,
            text=self._message,
            font=(self.font, BODY_FONT_SIZE, "bold"),
            wraplength=self._wrap,
            justify="left",
            anchor="w",
        )
        self.status_label.pack(fill="x", padx=2, pady=(2, 8))

        bar_row = ctk.CTkFrame(parent, fg_color="transparent")
        bar_row.pack(fill="x", padx=2)
        self.progress_bar = ctk.CTkProgressBar(bar_row, mode="determinate", height=16)
        self.progress_bar.set(0.0)
        self.progress_bar.pack(side="left", fill="x", expand=True)
        self.percent_label = ctk.CTkLabel(
            bar_row, text="0%", width=52, font=(self.font, BODY_FONT_SIZE - 1)
        )
        self.percent_label.pack(side="left", padx=(10, 0))

        ctk.CTkLabel(
            parent, text=tr("dlg.log"), font=(self.font, BODY_FONT_SIZE - 2), text_color=T["NEUTRAL"], anchor="w"
        ).pack(fill="x", padx=2, pady=(12, 2))
        self.log = ctk.CTkTextbox(parent, wrap="none", font=(self.font, BODY_FONT_SIZE - 2))
        self.log.pack(fill="both", expand=True, padx=2)
        self.log.configure(state="disabled")

    def _create_buttons(self, parent: ctk.CTkFrame) -> None:
        self.cancel_button = ctk.CTkButton(
            parent,
            text=f"❌ {self._cancel_text}",
            width=140,
            height=BUTTON_HEIGHT,
            font=(self.font, BODY_FONT_SIZE),
            fg_color=T["NEUTRAL"],
            hover_color=T["NEUTRAL_HOVER"],
            command=self.request_cancel,
        )
        self.cancel_button.pack(side="right")
        self.default_button = self.cancel_button
        self.confirm_button = self.cancel_button

        # 可暂停的任务（复制队列 B4）多一个「暂停/继续」按钮。
        # 注意暂停是在**两首歌之间**生效的：单首歌的传输没法中途挂起。
        self.pause_button: ctk.CTkButton | None = None
        if self._pause_callback is not None:
            self.pause_button = ctk.CTkButton(
                parent,
                text=tr("dlg.pause"),
                width=120,
                height=BUTTON_HEIGHT,
                font=(self.font, BODY_FONT_SIZE),
                fg_color=T["NEUTRAL"],
                hover_color=T["NEUTRAL_HOVER"],
                command=self.toggle_pause,
            )
            self.pause_button.pack(side="right", padx=(0, 10))

    # ---------------- 暂停 / 继续（B4） ----------------
    def toggle_pause(self) -> None:
        """切换暂停状态并把按钮文案同步过去。"""
        if self._pause_callback is None or self.finished or self.cancelled:
            return
        try:
            paused = bool(self._pause_callback())
        except Exception as exc:  # 暂停回调不能把 UI 拖崩
            logger.warning("暂停回调异常: %s", exc)
            return
        self.update_pause_button(paused)

    def update_pause_button(self, paused: bool) -> None:
        """由主程序在状态变化时调用（例如任务收尾要复位成"未暂停"）。"""
        if self.pause_button is None or self._destroyed:
            return
        try:
            self.pause_button.configure(text="▶ 继续" if paused else "⏸ 暂停")
            self.status_label.configure(
                text="⏸ 已暂停（当前这首传完后停下）" if paused else self._message
            )
        except tk.TclError:  # pragma: no cover
            pass

    # ---------------- 更新（仅 Tk 主线程） ----------------
    def set_progress(self, value: float, text: str = "") -> None:
        """更新进度条。``value`` 为 0~100 的百分比，``text`` 非空时同时更新状态文字。"""
        if self._destroyed:
            return
        percent = max(0.0, min(100.0, float(value)))
        try:
            self.progress_bar.set(percent / 100.0)
            self.percent_label.configure(text=f"{percent:.0f}%")
            if text:
                self.status_label.configure(text=text)
        except tk.TclError:  # pragma: no cover
            pass

    def append_log(self, line: str) -> None:
        """追加一行日志并滚到底（行数超上限时裁掉最旧的部分）。"""
        if self._destroyed:
            return
        try:
            self.log.configure(state="normal")
            self.log.insert("end", str(line).rstrip("\n") + "\n")
            if int(self.log.index("end-1c").split(".")[0]) > self.MAX_LOG_LINES:
                self.log.delete("1.0", f"{self.TRIM_TO_LINES}.0")
            self.log.see("end")
            self.log.configure(state="disabled")
        except (tk.TclError, ValueError):  # pragma: no cover
            pass

    def request_cancel(self) -> None:
        """用户点了取消 / 按了 Esc / 关了窗口。

        只置标志 + 回调通知工作线程，**不销毁窗口**（收尾交给调用方），
        避免工作线程还在跑的时候界面消失。
        """
        if self.finished or self.cancelled:
            return
        self.cancelled = True
        try:
            self.cancel_button.configure(state="disabled", text=tr("dlg.cancelling"))
            self.status_label.configure(text=tr("dlg.cancelling_wait"))
        except tk.TclError:  # pragma: no cover
            pass
        self.append_log("⚠️ 用户取消，正在停止…")
        if self._cancel_callback is None:
            return
        try:
            self._cancel_callback()
        except Exception as exc:  # 取消回调不能把 UI 拖崩
            logger.warning("取消回调异常: %s", exc)

    def finish(self, summary: str = "") -> None:
        """任务结束：进度拉满、写入总结、把按钮换成"关闭"（不销毁窗口）。"""
        if self._destroyed:
            return
        self.finished = True
        try:
            self.progress_bar.set(1.0)
            self.percent_label.configure(text="100%")
            self.status_label.configure(text="已取消" if self.cancelled else "✅ 已完成")
        except tk.TclError:  # pragma: no cover
            pass
        if summary:
            self.append_log(summary)
        if self._closable:
            try:
                self.cancel_button.configure(
                    state="normal", text=tr("dlg.close"), fg_color=T["ACCENT"],
                    hover_color=T["ACCENT_HOVER"], command=self.close,
                )
            except tk.TclError:  # pragma: no cover
                pass
        self._release_grab()  # 任务已结束，主窗口可以重新操作

    # ---------------- 显示 ----------------
    def show_nonblocking(self) -> None:
        """只显示窗口，不进入 wait_window（长任务由调用方用 after 轮询更新）。"""
        self._make_modal()
        try:
            self.focus_set()
        except tk.TclError:  # pragma: no cover
            pass

    def show(self) -> None:
        """兼容 ModalDialog 的用法：显示后阻塞到窗口关闭（一般用 show_nonblocking）。"""
        self.show_nonblocking()
        try:
            self.wait_window()
        except tk.TclError:  # pragma: no cover
            pass
        return None
