"""主窗口与业务编排。

界面结构（自上而下）：

    ┌─────────────────────────────────────────────────────────────┐
    │  应用栏：标题 · 状态徽标 · 全局搜索 · 快捷入口                │
    ├──────────────────────────────┬──────────────────────────────┤
    │  路径卡片（本地 / Quest）      │  歌曲详情                     │
    │  三个列表（本地/Quest/缺失）   │  封面 + 元信息 + 难度 + 评分   │
    │  操作按钮（主/危险分组）       │  （固定宽度，不再被列表挤压）  │
    ├──────────────────────────────┴──────────────────────────────┤
    │  状态栏 + 进度条                                              │
    └─────────────────────────────────────────────────────────────┘

设计原则（相对上一版）：
* **不再溢出**：右栏宽度显式限制（`RIGHT_PANEL_WIDTH`），列表列宽可伸缩，
  1600x1000 及最小窗口下都不会把窗口撑出屏幕（上一版实测被撑到 1973px）；
* **语义配色**：所有颜色来自 `theme.py` 的语义令牌，浅色/深色统一；
* **反馈非模态**：状态变化走 Toast（`ui_toast.py`），只有"需要决策"才弹对话框；
* **数据层不变**：扫描/缓存/对比/复制/删除逻辑与上一版一致，只换表现层。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import tkinter as tk

import customtkinter as ctk

from .app_common import (
    RIGHT_PANEL_WIDTH,
    column_identity,
    meta_from_row,
    meta_row,
    shorten_path,
)
from .app_copy import CopyMixin
from .app_delete import DeleteMixin
from .app_detail import DetailMixin
from .app_device import DeviceMixin
from .app_report import ReportMixin
from .audio import available as audio_available
from .audio import stop_all as stop_all_audio
from .ui_settings import SettingsDialog
from .constants import (
    APP_VERSION,
    MIN_SIZE,
    QUEUE_POLL_MS,
    WINDOW_SIZE,
    resolve_font,
)
from .database import Database
from .diagnostics import (
    current_operation,
    install_excepthook,
    slow,
    start_watchdog,
    stop_watchdog,
    watchdog_running,
)
from .fsapi import FSError, FSUnavailable, Location, SongFS, open_fs
from .logging_setup import get_logger
from .messages import tr, set_language
from .songs import SongMeta
from .storage import Settings, database_path, settings_path
from .theme import (
    FONT,
    RADIUS,
    SIZE,
    SPACE,
    T,
    apply_ui_scale,
    color,
    current_mode,
    get_palette,
    warm_emoji_font,
)
from .ui_dialogs import (
    ConfirmDialog,
    InfoDialog,
    ProgressDialog,
    TextPromptDialog,
)
from .ui_coverwall import WallItem
from .ui_songlist import SongItem, SongListWidget
from .ui_toast import ToastHost
from .ui_window import WindowChrome
from .ui_preview import RoundPlayButton
from .ui_icons import LineIcon, IconButton, icon_image
from .workers import GuardError, TaskMessage, TaskRunner, Token, guard_copy

logger = get_logger("app")
#: 低于这个窗口宽度就自动收起详情栏（三列列表 + 详情栏的最小可用宽度约 1560）
AUTO_COLLAPSE_WIDTH = 1420
#: 三个列表/封面墙所在的列号（视图切换时按这个放回原位）
_COLUMN_OF = {"local": 0, "quest": 1, "missing": 2}


# ======================================================================================
# 歌曲库
# ======================================================================================
class Library:
    """一个歌曲库（本地或 Quest）的运行时状态。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind  # "local" | "quest"
        self.location: Location | None = None
        self.fs: SongFS | None = None
        self.folders: list[str] = []
        self.meta: dict[str, SongMeta] = {}
        self.identity: str = ""
        self.label: str = ""
        self.error: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.fs) and bool(self.folders)

    @property
    def name_set(self) -> set[str]:
        return set(self.folders)

    def close(self) -> None:
        if self.fs is not None:
            try:
                self.fs.close()
            except Exception:  # 关闭失败不影响主流程
                pass
        self.fs = None


# ======================================================================================
# 主应用
# ======================================================================================
# 这个类一度有 2400 多行，按职责拆成了 5 个 mixin（v15.2）：
#   CopyMixin   复制/传输（含耗时估算与实测速率学习）
#   DeleteMixin 删除（「缺失」列表要删对侧 —— 踩过坑）
#   DeviceMixin 路径与设备检测、最近路径
#   DetailMixin 歌曲详情栏
#   ReportMixin 对比与报告导出
# mixin 里只用 `self.*`，不反向 import 本模块，所以没有循环依赖。
class QuestComparatorApp(CopyMixin, DeleteMixin, DeviceMixin, DetailMixin, ReportMixin):
    """应用主控制器（界面骨架在本类，业务逻辑在 5 个 mixin 里）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()
        self.db = Database(database_path())
        self.tasks = TaskRunner()
        self.libraries: dict[str, Library] = {"local": Library("local"), "quest": Library("quest")}
        self.missing: list[str] = []
        self.missing_side = "quest"
        self.current_song: tuple[str, str] | None = None
        self.selected_songs: dict[str, list[str]] = {"local": [], "quest": [], "missing": []}
        self._missing_bytes: dict[str, int] = {}
        self._cover_cache: dict[str, Any] = {}
        self._last_cover_source: tuple[str, str, bytes | None, str] | None = None
        self._rating_map: dict[str, int] = {}
        self._title_map: dict[str, str] = {}
        self._scan_tokens: dict[str, Token] = {}
        self._closing = False
        self._progress_dialog: ProgressDialog | None = None
        self._progress_token: str = ""
        self._busy = False

        ctk.set_appearance_mode(self.settings.appearance)
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        # DPI 缩放：必须在**建任何控件之前**应用（customtkinter 的缩放是全局的，
        # 只影响之后创建的控件）。见 theme.apply_ui_scale。
        self.ui_scale = apply_ui_scale(self.settings.ui_scale, self.root)
        # 语言同理：越早设越好，免得先建出来的控件还是旧语言
        set_language(self.settings.language)
        self.root.title(tr("app.title", version=APP_VERSION))
        self.root.geometry(self._initial_geometry())
        self.root.minsize(*self._scaled_min_size())
        self.root.configure(fg_color=T["BG_APP"])
        self.window_chrome = WindowChrome(self.root)
        self.font_family = resolve_font(self.root)
        # v17.9 启动优化：把"第一个 emoji 的字体回退查找"提前付掉。
        # 实测这笔一次性成本 **277 ms**（第二个 emoji 只要 6 ms），原先全落在
        # `_build_appbar` 的标题 label 上（424 → 304 ms）。
        # 放在这里是因为：必须 already 有 root、且字体族已定，但**还没建任何控件**。
        warmed_ms = warm_emoji_font(self.root, self.font_family)
        if warmed_ms >= 50:
            logger.info("emoji 字体预热用了 %.0f ms（这笔开销原先落在标题 label 上）", warmed_ms)
        self._details_visible = True
        self._details_auto_hidden = False
        self._drop_target: Any = None
        self._download_manager = None
        self._download_window = None
        self._download_refresh_job = None
        self._download_focus = ""
        self._download_refresh_target = ""
        self._importing = False
        self._wizard: Any = None  # 首次使用向导（打开时才有值）
        self._view_mode = self.settings.view_mode if self.settings.view_mode in ("list", "grid") else "list"
        # 试听进度条的状态。**必须在这里初始化**：`_refresh_preview_progress` 在
        # `root.after` 回调里跑，而按钮回调与 after 回调抛的异常都会被 Tk 吞掉，
        # 少了这几个属性只会表现为「点了没反应」而不是报错。
        self._preview_total = 0.0          # 真实总时长（秒），探测不到时为 0
        self._preview_progress_job: str | None = None
        self._seeking = False              # 正在拖动进度条
        #: 拖动时的位置**比例**（0~1）。存比例而不是秒数：秒数要等松手时
        #: 结合本次片段的起点才能算，而起点在松手那一刻才变。
        self._seek_target = 0.0
        #: 拖动开始时**冻结**的量程（秒）。拖动期间不能重取 —— 松手后播放器
        #: 就换成"新的一遍"，量程随之改变，同一个比例会被换算成不同的秒数。
        self._seek_span = 0.0
        #: 已知可用的 adb 设备 serial 缓存。**只能由后台线程写、界面线程读**：
        #: 真去调 adb 可能要好几秒（冷启动实测 5.2 秒），界面线程碰不得。
        self._adb_serials: list[str] = []

        self.toast = ToastHost(self.root)
        # 诊断：接管未捕获异常 + 起看门狗。必须赶在建界面之前做，
        # 这样启动阶段的卡顿也会被记下来（历史上"启动慢"一直查不到原因，
        # 就是因为启动阶段的日志里只有一条"启动完成"）。
        install_excepthook()
        start_watchdog(self.root)
        with slow("建立主界面"):
            self._build_ui()
        self._build_menu()
        self._bind_shortcuts()
        self._restore_paths()
        if self._view_mode == "grid":
            # 上次用的是封面墙，启动就切过去（不弹提示，避免一启动就刷通知）
            self.set_view_mode("grid")
            self._suppress_view_toast = False
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # 第一次启动时弹一次向导（ROADMAP C4）。
        # 延后 500ms：先让主窗口画出来，否则向导会盖在一片白上出现。
        if not self.settings.wizard_done and not (
            self.settings.local_path and self.settings.quest_path
        ):
            self.root.after(500, self.show_wizard)
        self.root.bind("<Configure>", self._on_root_configure)
        self._install_dnd()
        self.root.after(QUEUE_POLL_MS, self._pump_queue)
        logger.info("应用初始化完成，数据目录: %s", database_path().parent)

    def _scaled_min_size(self) -> tuple[int, int]:
        """最小窗口尺寸要跟着缩放走（C1）。

        不缩放的话，150% 下 MIN_SIZE 还是 1180×720 的**物理**像素，
        内容按 1.5 倍画出来就装不下 → 控件被裁掉、互相重叠。
        """
        scale = float(getattr(self, "ui_scale", 1.0) or 1.0)
        return (int(MIN_SIZE[0] * scale), int(MIN_SIZE[1] * scale))

    # ==================================================================================
    # 界面构建
    # ==================================================================================
    def _build_ui(self) -> None:
        # 版式（v16.0 起）：应用栏一行，下面是**可拖动分割**的左右两栏（B5）。
        # 拖动条的位置记在 settings.detail_width，下次启动沿用。
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)

        self._build_appbar()
        self._build_source_toolbar()

        #: 左右两栏的分割器。用经典 tk.PanedWindow 而不是 grid 固定列宽 ——
        #: 固定列宽时用户没法自己调"列表 / 详情栏"的比例。
        self.paned = tk.PanedWindow(
            self.root,
            orient="horizontal",
            sashwidth=6,
            sashrelief="flat",
            borderwidth=0,
            # 经典 tk 控件只认单色，不能直接用 T[...] 的 (浅,深) 元组
            background=color("BG_APP"),
        )
        self.paned.grid(row=2, column=0, sticky="nsew", padx=(SPACE.L, SPACE.L),
                        pady=(SPACE.S, SPACE.S))

        left = ctk.CTkFrame(self.paned, corner_radius=0, fg_color=T["BG_APP"])
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)
        self.left_card = left

        self.right_card = ctk.CTkFrame(self.paned, corner_radius=RADIUS.CARD, fg_color=T["BG_CARD"],
                                       border_width=1, border_color=T["BORDER"],
                                       width=RIGHT_PANEL_WIDTH)
        self.right_card.grid_columnconfigure(0, weight=1)
        self.right_card.grid_rowconfigure(0, weight=1)

        self.paned.add(left, minsize=560, stretch="always")
        self.paned.add(self.right_card, minsize=280, width=self._detail_width(),
                       stretch="never")
        self._bind_sash_memory()

        # 底部操作栏**横跨左右两栏**（v17.7 改）。
        #
        # 改动前它长在左栏里（path / lists / buttons 三行），于是左栏被 "路径卡片
        # + 三个列表 + 四个操作分组" 四段内容抢高度：1700x950 下操作栏要 100px、
        # 路径卡 120px，留给列表的只剩约 730px，而四个操作分组挤在左栏里还只能
        # 排 2x2。横跨到最下面之后，列表能拿到全部剩余高度，操作栏也能一行排开。
        self._build_lists(left)
        self._build_info_panel(self.right_card)
        self._build_action_bar()          # 横跨左右，必须排在左栏/右栏之后
        self._build_statusbar()

    # ---------------- 视图模式：列表 / 封面墙 ----------------
    def set_view_mode(self, mode: str) -> None:
        """切换"列表"与"封面墙"两种浏览方式（记住选择）。"""
        mode = "grid" if mode == "grid" else "list"
        self._view_mode = mode
        self.settings.view_mode = mode
        self.settings.save()
        try:
            self._view_switch.set(tr("view.switch.grid") if mode == "grid" else tr("view.switch.list"))
        except Exception:
            pass
        for kind in ("local", "quest", "missing"):
            try:
                if mode == "grid":
                    self._list_for(kind).grid_remove()
                    self._wall_for(kind).grid(row=0, column=_COLUMN_OF[kind], padx=SPACE.XS,
                                              pady=SPACE.XS, sticky="nsew")
                    self._refresh_wall(kind)
                else:
                    self._wall_for(kind).grid_remove()
                    self._list_for(kind).grid(row=0, column=_COLUMN_OF[kind], padx=SPACE.XS,
                                              pady=SPACE.XS, sticky="nsew")
            except Exception as exc:
                logger.debug("切换视图失败 %s: %s", kind, exc)
        if not getattr(self, "_suppress_view_toast", False):
            self.toast.info("已切换到封面墙视图" if mode == "grid" else "已切换到列表视图",
                            detail="封面墙下双击封面可查看详情" if mode == "grid" else "")
        self._suppress_view_toast = False

    def _set_column_view(self, kind: str, mode: str) -> None:
        widget, wall = self._list_for(kind), self._wall_for(kind)
        if mode == "grid":
            self._refresh_wall(kind)
            widget.grid_remove()
            wall.grid(row=0, column=_COLUMN_OF[kind], padx=SPACE.XS, pady=SPACE.XS, sticky="nsew")
        else:
            wall.grid_remove()
            widget.grid(row=0, column=_COLUMN_OF[kind], padx=SPACE.XS, pady=SPACE.XS, sticky="nsew")

    def _toggle_view(self) -> None:
        self.set_view_mode("list" if self._view_mode == "grid" else "grid")

    def _refresh_wall(self, kind: str) -> None:
        """把当前库的数据喂给封面墙（只在切到封面墙/数据变化时调用）。"""
        wall = self._wall_for(kind)
        sample_kind = self._sample_kind(kind)
        library = self.libraries.get(sample_kind)
        if library is None:
            return
        names = library.folders if kind != "missing" else list(self.missing)
        ratings = self._ratings_for(sample_kind)
        items = []
        for name in names:
            meta = library.meta.get(name)
            items.append(
                WallItem(
                    folder_name=name,
                    title=(meta.display_title if meta else "") or self._title_map.get(name, "") or name,
                    author=meta.display_author if meta else "",
                    rating=int(ratings.get(name, 0)),
                    highlighted=name in self.missing,
                )
            )
        wall.set_items(items, highlight=self.missing)

    def _cover_bytes_for(self, kind: str, folder: str) -> bytes | None:
        """给封面墙用的封面读取（在工作线程里被调用，只做 IO，不碰控件）。"""
        library = self.libraries.get(kind)
        if library is None or library.fs is None or library.location is None:
            return None
        try:
            return library.fs.read_cover_bytes(folder)
        except Exception:
            return None

    # ---------------- 详情栏折叠 ----------------
    def _detail_width(self) -> int:
        """详情栏宽度：用上次拖出来的，没有或离谱就用默认（B5）。"""
        saved = int(self.settings.detail_width or 0)
        if 280 <= saved <= 1200:
            return saved
        return RIGHT_PANEL_WIDTH

    def _bind_sash_memory(self) -> None:
        """记住用户拖出来的分割位置（松手时存一次，不要拖动过程中反复写盘）。"""

        def on_release(_event: Any = None) -> None:
            try:
                _x, width = self.paned.sash_coord(0)
            except Exception:  # 只有一个窗格时 sash_coord 会报错
                return
            total = self.paned.winfo_width()
            detail = max(0, total - int(width) - int(self.paned.cget("sashwidth")))
            if 280 <= detail <= 1200:
                self.settings.detail_width = detail
                self.settings.save()

        self.paned.bind("<ButtonRelease-1>", on_release, add="+")

    def toggle_details(self) -> None:
        """折叠/展开右侧详情栏：小屏或想专注列表时很有用。"""
        if self._details_visible:
            self._forget_details()
            self._details_visible = False
            self.toast.info("已收起详情栏", detail=tr("app.detail_collapsed"))
        else:
            self._show_details()
            self._details_visible = True
            self.toast.info("已展开详情栏")
        for widget in (self.list_local, self.list_quest, self.list_missing):
            widget.refresh_theme()

    def _forget_details(self) -> None:
        """把详情栏从分割器里摘掉（拖动分割器不能整个隐藏，必须摘窗格）。"""
        try:
            self.paned.forget(self.right_card)
        except Exception:  # 已经摘掉了
            pass

    def _show_details(self) -> None:
        try:
            self.paned.add(self.right_card, minsize=280, width=self._detail_width(),
                           stretch="never")
        except Exception:  # 已经在里面了
            pass

    def _on_root_configure(self, event: Any = None) -> None:
        """窗口变窄时自动收起详情栏，避免主区域被挤到裁切。

        实测：三列列表 + 详情栏的最小可用宽度约 1560px。比这更窄时，
        与其让右侧内容被窗口边缘切掉，不如自动折叠详情栏（用户仍可 Ctrl+D 展开）。
        """
        if event is not None and getattr(event, "widget", None) is not self.root:
            return
        try:
            width = self.root.winfo_width()
        except Exception:
            return
        if width <= 1:  # 尚未映射时的占位尺寸
            return
        if width < AUTO_COLLAPSE_WIDTH and self._details_visible:
            self._forget_details()
            self._details_visible = False
            self._details_auto_hidden = True
        elif width >= AUTO_COLLAPSE_WIDTH + 60 and self._details_auto_hidden:
            self._show_details()
            self._details_visible = True
            self._details_auto_hidden = False

    # ---------------- 应用栏 ----------------
    def _build_appbar(self) -> None:
        bar = ctk.CTkFrame(self.root, corner_radius=0, fg_color=T["BG_APP"], height=48)
        bar.grid(row=0, column=0, sticky="ew", padx=SPACE.L, pady=(SPACE.S, SPACE.S))
        bar.grid_columnconfigure(1, weight=1)
        title = ctk.CTkLabel(bar, text=tr("layout.app_title"),
                             font=(self.font_family, 17, "bold"), text_color=T["TEXT_PRIMARY"])
        title.grid(row=0, column=0, sticky="w")
        self.window_chrome.attach_drag(bar)
        self.window_chrome.attach_drag(title)
        tagline = ctk.CTkLabel(bar, text=tr("layout.tagline"),
                               font=(self.font_family, 11), text_color=T["TEXT_MUTED"])
        tagline.grid(row=0, column=1, sticky="w", padx=SPACE.L)
        self.window_chrome.attach_drag(tagline)
        self.device_badge = ctk.CTkLabel(bar, text=tr("device.none"),
                                        font=(self.font_family, FONT.SMALL), text_color=T["TEXT_MUTED"])
        self.device_badge.grid(row=0, column=2, padx=SPACE.S)
        actions = ctk.CTkFrame(bar, fg_color=T["BG_APP"])
        actions.grid(row=0, column=3, sticky="e")
        for key, command in (("btn.detect", lambda: self.detect_device("quest")),
                             ("btn.wireless", self.connect_wireless),
                             ("btn.rescan", self.reload_both)):
            IconButton(actions, text=tr(key), width=92, height=30,
                          font=(self.font_family, FONT.SMALL), fg_color=T["BG_CARD"],
                          text_color=T["TEXT_PRIMARY"], border_width=1, border_color=T["BORDER"],
                          hover_color=T["BG_HOVER"], command=command).pack(side="left", padx=SPACE.XS)
        IconButton(actions, text="⚙", width=32, height=30, fg_color=T["BG_CARD"],
                      text_color=T["TEXT_PRIMARY"], command=self.open_settings).pack(side="left", padx=4)
        # The full original menu remains available from the application heading.
        title.bind("<Button-3>", self._popup_application_menu, add="+")
        # Existing global view API remains available; each column now exposes its own switches.
        self._view_switch = IconButton(actions, text=tr("view.switch.grid"), command=self._toggle_view)
        for symbol, command in (("—", self.window_chrome.minimize),
                                 ("□", self.window_chrome.maximize), ("×", self.on_close)):
            IconButton(actions, text=symbol, width=32, height=30,
                          fg_color=T["BG_APP"], text_color=T["TEXT_SECONDARY"],
                          hover_color=T["BG_HOVER"], command=command).pack(side="left", padx=3)

    def open_downloads(self) -> None:
        from .downloads import DownloadManager
        from .ui_downloads import DownloadWindow
        from .storage import data_dir
        if self._download_window is not None and self._download_window.winfo_exists():
            self._download_window.present()
            return
        try:
            if self._download_manager is None:
                self._download_manager = DownloadManager(data_dir() / "downloads.json")
            self._download_window = DownloadWindow(self.root, self._download_manager,
                                                   self.path_vars["local"].get().strip(),
                                                   preferences=(self.settings.download_columns, self.settings.download_rows),
                                                   on_grid=self._save_download_grid)
        except Exception as exc:
            self.toast.error(tr("dl.error.unexpected"), detail=str(exc))

    def _save_download_grid(self, columns, rows) -> None:
        self.settings.download_columns = columns
        self.settings.download_rows = rows
        self.settings.save()

    def _refresh_download_library(self) -> None:
        self._download_refresh_job = None
        if not self._closing and self.path_vars["local"].get().strip() and Path(self.path_vars["local"].get()).resolve() == Path(self._download_refresh_target):
            self.list_local.set_filter("all")
            self.list_local.set_search("")
            self.load_library("local", silent=True)
            self.set_status(tr("dl.refreshed"))

    def _popup_application_menu(self, event) -> None:
        try:
            self.application_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.application_menu.grab_release()

    def _build_source_toolbar(self) -> None:
        region = ctk.CTkFrame(self.root, fg_color=T["BG_APP"])
        region.grid(row=1, column=0, sticky="ew", padx=SPACE.L, pady=(SPACE.M, 0))
        region.grid_columnconfigure(0, weight=1)
        self._build_path_section(region)
        tools = ctk.CTkFrame(region, fg_color=T["BG_CARD"], corner_radius=RADIUS.PANEL)
        tools.grid(row=0, column=1, sticky="nsew", padx=(SPACE.M, 0))
        for column, (key, command) in enumerate((
            ("dl.title", self.open_downloads),
            ("toolbar.duplicates", lambda: self.find_duplicates("local")),
            ("toolbar.import_playlist", lambda: self.import_playlist("local")),
            ("toolbar.export_playlist", lambda: self.export_playlist("local")),
            ("toolbar.export_report", lambda: self.export_report("csv")),
        )):
            IconButton(
                tools, text=tr(key), icon={"dl.title": "import", "toolbar.duplicates": "duplicate", "toolbar.import_playlist": "import",
                             "toolbar.export_playlist": "export", "toolbar.export_report": "report"}[key], compound="top", command=command, width=76, height=70,
                corner_radius=RADIUS.CONTROL, font=(self.font_family, FONT.SMALL),
                fg_color=T["BG_INPUT"], text_color=T["TEXT_PRIMARY"],
                hover_color=T["BG_HOVER"],
            ).grid(row=0, column=column, padx=SPACE.XS, pady=SPACE.M, sticky="nsew")
            tools.grid_columnconfigure(column, weight=1)

    # ---------------- 路径卡片 ----------------
    def _build_path_section(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(parent, corner_radius=RADIUS.PANEL, fg_color=T["BG_ELEVATED"],
                            border_width=1, border_color=T["BORDER"])
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_columnconfigure(1, weight=1)

        self.path_vars: dict[str, ctk.StringVar] = {
            "local": ctk.StringVar(value=self.settings.local_path),
            "quest": ctk.StringVar(value=self.settings.quest_path),
        }
        self.path_entries: dict[str, ctk.CTkEntry] = {}
        self.path_status: dict[str, ctk.CTkLabel] = {}
        self.recent_buttons: dict[str, ctk.CTkButton] = {}
        labels = {"local": tr("path.local"), "quest": tr("path.quest")}
        for row, kind in enumerate(("local", "quest"), start=0):
            label_box = ctk.CTkFrame(card, fg_color=T["BG_CARD"])
            label_box.grid(row=row, column=0, padx=(SPACE.L, SPACE.XS), pady=SPACE.XS, sticky="w")
            LineIcon(label_box, kind, "ACCENT" if kind == "local" else "INFO").pack(side="left", padx=(0, SPACE.S))
            ctk.CTkLabel(label_box, text=labels[kind], width=72, anchor="w",
                         font=(self.font_family, FONT.BODY, "bold"), text_color=T["TEXT_PRIMARY"]).pack(side="left")
            entry = ctk.CTkEntry(
                card,
                textvariable=self.path_vars[kind],
                height=SIZE.INPUT_H,
                corner_radius=RADIUS.CONTROL,
                font=(self.font_family, FONT.SMALL),
                fg_color=T["BG_INPUT"],
                border_color=T["BORDER_STRONG"],
                placeholder_text=(tr("path.placeholder.local") if kind == "local"
                                  else tr("path.placeholder.quest")),
            )
            entry.grid(row=row, column=1, padx=SPACE.XS, pady=SPACE.XS, sticky="ew")
            # 回车 = 载入 + **立刻记住**。
            # 原先只调 load_library，路径要等到 on_close() 才写盘 —— 中途崩溃/强杀
            # 就白输了。用户报的"路径重启后没了"有一半是这里。
            entry.bind("<Return>", lambda _e, k=kind: self.commit_path(k))
            # 失焦也存一次：用户输完直接点别处（很常见）同样要记住
            entry.bind("<FocusOut>", lambda _e, k=kind: self.commit_path(k, load=False))
            entry.bind("<Button-3>", lambda _e, k=kind: self._show_recent_menu(k))
            self.path_entries[kind] = entry

            status = ctk.CTkLabel(card, text="○", width=18, font=(self.font_family, FONT.SMALL),
                                  text_color=T["TEXT_MUTED"])
            status.grid(row=row, column=2, padx=(0, SPACE.XS))
            self.path_status[kind] = status

            buttons = ctk.CTkFrame(card, fg_color="transparent")
            buttons.grid(row=row, column=3, padx=(0, SPACE.L), pady=SPACE.XS, sticky="e")
            recent = IconButton(
                buttons, text="🕘", width=36, height=SIZE.INPUT_H,
                corner_radius=RADIUS.CONTROL, font=(self.font_family, FONT.SMALL),
                fg_color=T["NEUTRAL"], hover_color=T["NEUTRAL_HOVER"],
                command=lambda k=kind: self._show_recent_menu(k),
            )
            # Recent paths stay available from the input context menu.
            self.recent_buttons[kind] = recent
            IconButton(
                buttons, text=tr("path.browse"), width=76, height=SIZE.INPUT_H,
                corner_radius=RADIUS.CONTROL, font=(self.font_family, FONT.SMALL),
                fg_color=T["BG_INPUT"], hover_color=T["BG_HOVER"],
                text_color=T["TEXT_PRIMARY"],
                command=lambda k=kind: self.choose_folder(k),
            ).pack(side="left", padx=(0, SPACE.XS))
            IconButton(
                buttons, text=tr("path.load"), width=76, height=SIZE.INPUT_H,
                corner_radius=RADIUS.CONTROL, font=(self.font_family, FONT.SMALL),
                fg_color=T["ACCENT"], hover_color=T["ACCENT_HOVER"],
                command=lambda k=kind: self.load_library(k),
            ).pack(side="left")


    # ---------------- 三个列表 ----------------
    def _build_lists(self, parent: ctk.CTkFrame) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")
        for col in range(3):
            frame.grid_columnconfigure(col, weight=1, uniform="lists")
        frame.grid_rowconfigure(0, weight=1)

        def make(kind: str, title: str) -> SongListWidget:
            # 列的语义色与图标都取自 ColumnIdentity（唯一来源），
            # 不再在三处各写一遍字面量 —— 那样改一处必然漂移。
            ident = column_identity(kind)
            widget = SongListWidget(
                frame,
                title=title,
                rich_rows=True,
                result_column=kind == "missing",
                accent=ident.accent,
                icon=ident.icon,
                on_select=lambda name, k=kind: self._on_row_selected(k, name),
                on_select_multi=lambda names, k=kind: self._on_multi_selected(k, names),
                on_delete=lambda name, k=kind: self._on_row_delete(k, name),
                on_sort_changed=lambda mode, reverse, k=kind: self.on_sort(k, mode, reverse),
                on_open_in_explorer=lambda name, k=kind: self.open_in_explorer(k, name),
                on_columns_changed=lambda widths, k=kind: self._save_column_widths(k, widths),
                on_filter_changed=lambda value, k=kind: self._apply_song_filter(k, value),
                column_widths=self.settings.column_widths.get(kind),
            )
            widget._view_callback = lambda mode, k=kind: self._set_column_view(k, mode)
            return widget

        self.list_local = make("local", tr("label.local"))
        self.list_local.grid(row=0, column=0, padx=SPACE.XS, pady=SPACE.XS, sticky="nsew")

        self.list_quest = make("quest", tr("label.quest"))
        self.list_quest.grid(row=0, column=1, padx=SPACE.XS, pady=SPACE.XS, sticky="nsew")

        self.list_missing = make("missing", tr("label.missing"))
        self.list_missing.grid(row=0, column=2, padx=SPACE.XS, pady=SPACE.XS, sticky="nsew")
        self.list_missing.set_empty_hint(tr("hint.missing_list"))

        for widget in (self.list_local, self.list_quest):
            widget.set_empty_hint(tr("hint.no_library"))

        # ---- 封面墙（同一格子里叠放，按视图模式切换显示） ----
        self.wall_local = self._make_wall(frame, "local", tr("label.local"))
        self.wall_quest = self._make_wall(frame, "quest", tr("label.quest"))
        self.wall_missing = self._make_wall(frame, "missing", tr("label.missing"))
        self.wall_missing.set_empty_hint(tr("hint.missing_wall"))
        self.wall_local.set_empty_hint(tr("hint.local_wall"))
        self.wall_quest.set_empty_hint(tr("hint.quest_wall"))

    def _make_wall(self, parent: ctk.CTkFrame, kind: str, title: str) -> Any:
        from .ui_coverwall import CoverWallWidget

        ident = column_identity(kind)
        wall = CoverWallWidget(
            parent,
            title=title,
            accent=ident.accent,
            icon=ident.icon,
            cover_provider=lambda folder, k=kind: self._cover_bytes_for(
                k if k != "missing" else self.missing_side, folder
            ),
            on_select=lambda folder, k=kind: self._on_row_selected(k, folder),
            on_activate=lambda folder, k=kind: self._on_row_selected(k, folder),
            on_context_menu=lambda folder, x, y, k=kind: self._wall_context_menu(k, folder, x, y),
        )
        return wall

    def _wall_context_menu(self, kind: str, name: str, x: int, y: int) -> None:
        """封面墙的右键菜单 —— 与列表保持一致（B6）。

        列表那边由 `SongListWidget._build_menu` 自己建；墙这边只上报"哪首歌、在哪个坐标"，
        菜单由主程序建 —— 因为"打开目录 / 删除"该落到哪一侧只有主程序知道
        （尤其「缺失」列要映射到真正拥有它的那一侧，见 `_real_kind`）。
        """
        real = self._real_kind(kind)
        library = self.libraries[real]
        meta = library.meta.get(name)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=tr("ctx.copy_folder"),
                         command=lambda: self._copy_text(name, "文件夹名"))
        menu.add_command(
            label=tr("ctx.copy_title"),
            command=lambda: self._copy_text(meta.display_title if meta else name, "歌曲标题"),
        )
        # 只有本地磁盘上的歌才能"在资源管理器中打开"
        if library.location is not None and library.location.kind == "local":
            menu.add_command(label=tr("ctx.open_explorer"),
                             command=lambda: self.open_in_explorer(real, name))
        menu.add_separator()
        menu.add_command(label=tr("ctx.delete"), foreground=color("DANGER"),
                         command=lambda: self._on_row_delete(kind, name))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _copy_text(self, text: str, what: str) -> None:
        """复制到剪贴板并回显（封面墙右键菜单用；列表那边是控件自己实现的）。"""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError as exc:
            self.toast.error("复制失败", detail=str(exc))
            return
        self.set_status(f"已复制{what}：{text}")

    def _wall_for(self, kind: str) -> Any:
        return {"local": self.wall_local, "quest": self.wall_quest, "missing": self.wall_missing}[kind]

    # ---------------- 操作按钮 ----------------
    #: 操作分组的语义色（m05627「每个功能要区分开来」）：
    #: 载入 = 蓝（只读、无副作用）、对比 = 紫（计算、只改变"缺什么"这一视图）、
    #: 补齐 = 绿（**真的往设备/本地写文件**）、删除 = 红（不可逆）。
    #: 按"会不会改动数据、改动哪一边"分色，而不是随便挑好看的：
    #: 用户在手机上扫一眼颜色就知道哪颗按钮会动文件。
    _GROUP_ROLES: tuple[tuple[str, str, str], ...] = (
        ("group.load", "ACCENT", "📥"),
        ("group.compare", "INFO", "🔍"),
        ("group.fill", "SUCCESS", "📤"),
        ("group.delete", "DANGER", "🗑"),
    )

    def _build_action_bar(self) -> None:
        """按载入、对比、补齐排列；危险操作独立显示。"""
        bar = ctk.CTkFrame(self.root, fg_color=T["BG_CARD"], corner_radius=RADIUS.PANEL)
        bar.grid(row=3, column=0, sticky="ew", padx=SPACE.L, pady=(SPACE.XS, 0))
        for col, weight in enumerate((2, 2, 2, 1)):
            bar.grid_columnconfigure(col, weight=weight)
        groups = (
            (tr("group.load"), "ACCENT", (
                (tr("btn.load.local"), lambda: self.load_library("local")),
                (tr("btn.load.quest"), lambda: self.load_library("quest")))),
            (tr("group.compare"), "INFO", (
                (tr("btn.compare.quest_missing"), lambda: self.compare("quest")),
                (tr("btn.compare.local_missing"), lambda: self.compare("local")))),
            (tr("group.fill"), "SUCCESS", (
                (tr("btn.copy.to_quest"), lambda: self.copy_missing("quest")),
                (tr("btn.copy.to_local"), lambda: self.copy_missing("local")))),
            (tr("group.delete"), "DANGER", (
                (tr("btn.delete.selected"), self.delete_selected_song),)),
        )
        for index, (title, role, specs) in enumerate(groups):
            box = ctk.CTkFrame(bar, fg_color=T["BG_CARD"])
            box.grid(row=0, column=index, sticky="ew", padx=SPACE.M, pady=SPACE.M)
            # Reference layout: label and controls share a single horizontal row.
            if index < 3:
                ctk.CTkLabel(box, text=title, anchor="w",
                             font=(self.font_family, FONT.SMALL, "bold"),
                             text_color=T["TEXT_PRIMARY"]).pack(side="left", padx=(0, SPACE.S))
            row = ctk.CTkFrame(box, fg_color=T["BG_CARD"])
            row.pack(side="left", fill="x", expand=True)
            for action_index, (text, command) in enumerate(specs):
                primary = role == "ACCENT" or (role == "SUCCESS" and action_index == 0)
                IconButton(
                    row, text=text, command=command, width=86, height=SIZE.BUTTON_H,
                    corner_radius=RADIUS.CONTROL, font=(self.font_family, FONT.SMALL),
                    fg_color=T[role] if primary else T["BG_CARD"],
                    text_color=T["ACCENT_TEXT"] if primary else T[role],
                    border_width=0 if primary else 1,
                    border_color=T[role] if role == "DANGER" else T["BORDER"],
                    hover_color=T.get(f"{role}_HOVER", T["BG_HOVER"]) if primary else T["BG_HOVER"],
                ).pack(side="left", fill="x", expand=True, padx=SPACE.XXS)

    # ---------------- 右侧详情 ----------------
    def _build_info_panel(self, parent: ctk.CTkFrame) -> None:
        shell = ctk.CTkFrame(parent, fg_color="transparent")
        shell.grid(row=0, column=0, padx=SPACE.L, pady=SPACE.L, sticky="nsew")
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, SPACE.S))
        ctk.CTkLabel(header, text=tr("detail.heading"), font=(self.font_family, FONT.TITLE, "bold"),
                     text_color=T["TEXT_PRIMARY"]).pack(side="left")
        self.detail_source = ctk.CTkLabel(
            header, text="", font=(self.font_family, FONT.TINY),
            text_color=T["TEXT_SECONDARY"], fg_color=T["BG_ELEVATED"],
            corner_radius=RADIUS.CHIP, padx=SPACE.S,
        )
        # Source badge is placed alongside artist below the title.

        body = ctk.CTkScrollableFrame(shell, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        # 封面（保持比例，容器固定高度，避免图片把右栏撑宽）
        self.cover_holder = ctk.CTkFrame(body, corner_radius=RADIUS.PANEL,
                                         fg_color=T["BG_CARD"], height=246,
                                         border_width=0, border_color=T["BORDER"])
        self.cover_holder.grid(row=0, column=0, sticky="ew")
        self.cover_holder.grid_propagate(False)
        self.cover_label = ctk.CTkLabel(
            self.cover_holder, text=tr("detail.no_selection"), font=(self.font_family, FONT.BODY),
            text_color=T["TEXT_MUTED"],
        )
        self.cover_label.pack(fill="both", expand=True, padx=SPACE.S, pady=SPACE.S)

        # 标题区（大字号标题 + 副标题 + 作者）
        title_box = ctk.CTkFrame(body, fg_color="transparent")
        title_box.grid(row=1, column=0, sticky="ew", pady=(SPACE.S, SPACE.XS))
        self.song_title = ctk.CTkLabel(
            title_box, text="—", anchor="w", justify="left", wraplength=RIGHT_PANEL_WIDTH - 90,
            font=(self.font_family, FONT.HEADLINE, "bold"), text_color=T["TEXT_PRIMARY"],
        )
        self.song_title.pack(fill="x")
        self.song_subtitle = ctk.CTkLabel(
            title_box, text="", anchor="w", justify="left", wraplength=RIGHT_PANEL_WIDTH - 90,
            font=(self.font_family, FONT.BODY), text_color=T["TEXT_SECONDARY"],
        )
        self.song_subtitle.pack(fill="x", pady=(SPACE.XXS, 0))
        author_row = ctk.CTkFrame(title_box, fg_color=T["BG_CARD"])
        author_row.pack(fill="x", pady=SPACE.XXS)
        self.song_author = ctk.CTkLabel(
            author_row, text="", anchor="w", font=(self.font_family, FONT.SMALL),
            text_color=T["TEXT_MUTED"],
        )
        self.song_author.pack(side="left")
        self.detail_source = ctk.CTkLabel(author_row, text="", corner_radius=RADIUS.CHIP,
                                        fg_color=T["ACCENT_SOFT"], text_color=T["ACCENT"],
                                        font=(self.font_family, FONT.TINY), padx=SPACE.S)
        self.detail_source.pack(side="right")

        # 指标行（BPM / 时长 / 格式 / 大小）—— 用 chip 排版，比"标签: 值"更好扫读
        self.metrics_row = ctk.CTkFrame(body, fg_color="transparent")
        self.metrics_row.grid(row=2, column=0, sticky="ew", pady=(0, SPACE.S))
        self.metric_labels: dict[str, ctk.CTkLabel] = {}

        # 难度
        diff_box = ctk.CTkFrame(body, fg_color="transparent")
        diff_box.grid(row=3, column=0, sticky="ew", pady=(0, SPACE.S))
        ctk.CTkLabel(diff_box, text=tr("detail.difficulties"), font=(self.font_family, FONT.TINY),
                     text_color=T["TEXT_MUTED"], height=22).pack(side="left", padx=(0, SPACE.S))
        self.difficulty_frame = ctk.CTkFrame(diff_box, fg_color="transparent")
        self.difficulty_frame.pack(side="left")

        # 评分
        rating_box = ctk.CTkFrame(body, fg_color="transparent")
        rating_box.grid(row=4, column=0, sticky="ew", pady=(0, SPACE.S))
        ctk.CTkLabel(rating_box, text=tr("detail.rating"), font=(self.font_family, FONT.TINY),
                     text_color=T["TEXT_MUTED"]).pack(side="left", padx=(0, SPACE.S))
        rating_row = ctk.CTkFrame(rating_box, fg_color="transparent")
        rating_row.pack(side="left", pady=(SPACE.XXS, 0))
        self.star_buttons: list[ctk.CTkButton] = []
        for value in range(1, 6):
            button = IconButton(
                rating_row, text="☆", width=26, height=30,
                corner_radius=RADIUS.CONTROL, font=("Segoe UI Symbol", 20),
                fg_color=T["BG_ELEVATED"], hover_color=T["BG_HOVER"],
                text_color=T["WARNING"],
                command=lambda v=value: self.set_rating(v),
            )
            button.pack(side="left", padx=(0, SPACE.XS))
            self.star_buttons.append(button)
        IconButton(
            rating_row, text=tr("detail.rating.clear"), width=64, height=28,
            corner_radius=RADIUS.CONTROL, font=(self.font_family, FONT.SMALL),
            fg_color="transparent", hover_color=T["BG_HOVER"], text_color=T["TEXT_MUTED"],
            command=lambda: self.set_rating(0),
        ).pack(side="left", padx=(SPACE.XS, 0))

        # 试听（P2 / D1）—— 只有装了 miniaudio 才显示，
        # 没装就把按钮做成"灰的 + 说明"，而不是干脆不画
        # （用户看不到按钮会以为功能不存在，比灰着更糟）。
        #
        # 行号必须跟**视觉顺序**一致：早先这里写的是 row=7，
        # 而下面的评价在 row=5、文件夹路径在 row=6 —— 于是试听被排到了
        # 评价和路径的下面，"评分 → 试听 → 评价 → 路径"的阅读顺序断了。
        # tk 的 grid 行号就是纵向位置，跟代码先后无关，改行号时要连带看清。
        preview_box = ctk.CTkFrame(body, fg_color="transparent")
        preview_box.grid(row=6, column=0, sticky="ew", pady=(0, SPACE.L))
        preview_header = ctk.CTkFrame(preview_box, fg_color=T["BG_CARD"])
        preview_header.pack(fill="x")
        ctk.CTkLabel(preview_header, text=tr("layout.preview"), height=18,
                     font=(self.font_family, FONT.TINY), text_color=T["TEXT_MUTED"]).pack(side="left")
        self.preview_hint = ctk.CTkLabel(preview_header, text=tr("layout.local_preview"), height=18,
                                        font=(self.font_family, FONT.TINY), text_color=T["TEXT_MUTED"])
        self.preview_hint.pack(side="left", padx=SPACE.S)
        self.preview_progress_row = ctk.CTkFrame(preview_box, fg_color=T["BG_CARD"])
        self.preview_progress_row.pack(fill="x", pady=SPACE.XS)
        self.preview_button = RoundPlayButton(self.preview_progress_row, command=self.toggle_preview)
        self.preview_button.pack(side="left", padx=(0, SPACE.XS))
        if not audio_available():
            self.preview_button.configure(state="disabled")
            self.preview_hint.configure(text=tr("detail.preview.unavailable"))
        self.preview_progress = ctk.CTkSlider(
            self.preview_progress_row, from_=0, to=1, height=16, width=110,
            button_length=0, button_corner_radius=6, button_color=T["ACCENT"], button_hover_color=T["ACCENT_HOVER"],
            progress_color=T["ACCENT"], fg_color=T["BG_HOVER"], command=self._on_seek_drag,
        )
        self.preview_progress.pack(side="left", fill="x", expand=True)
        self.preview_progress.set(0)
        self.preview_progress.bind("<ButtonRelease-1>", self._on_seek_release)
        self.preview_time = ctk.CTkLabel(
            self.preview_progress_row, text="00:00 / 00:30", anchor="e", width=76,
            font=(self.font_family, 9), text_color=T["TEXT_MUTED"],
        )
        self.preview_time.pack(side="left", padx=(SPACE.S, 0))
        volume_row = ctk.CTkFrame(preview_box, fg_color=T["BG_CARD"])
        volume_row.pack(fill="x", padx=(36, 0))
        ctk.CTkLabel(volume_row, text="", image=icon_image("volume", T["TEXT_SECONDARY"], master=volume_row), width=28, font=(self.font_family, FONT.SMALL),
                     text_color=T["TEXT_SECONDARY"]).pack(side="left")
        self.preview_volume = ctk.CTkSlider(
            volume_row, from_=0, to=1, height=14, width=110,
            button_length=0, button_corner_radius=5, button_color=T["ACCENT"], progress_color=T["ACCENT"],
            fg_color=T["BG_HOVER"], command=self._on_preview_volume,
        )
        self.preview_volume.set(1.0)
        self.preview_volume.pack(side="left", fill="x", expand=True, padx=SPACE.XS)

        # 评价
        comment_box = ctk.CTkFrame(body, fg_color="transparent")
        comment_box.grid(row=5, column=0, sticky="ew", pady=(0, SPACE.S))
        head = ctk.CTkFrame(comment_box, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=tr("detail.comment"), font=(self.font_family, FONT.TINY), height=20,
                     text_color=T["TEXT_MUTED"]).pack(side="left")
        IconButton(
            head, text=tr("detail.comment.edit"), width=74, height=22,
            corner_radius=RADIUS.CHIP, font=(self.font_family, FONT.SMALL),
            fg_color="transparent", hover_color=T["BG_HOVER"], text_color=T["ACCENT"],
            command=self.edit_comment,
        ).pack(side="right")
        self.comment_label = ctk.CTkLabel(
            comment_box, text=tr("detail.comment.none"), anchor="w", justify="left",
            wraplength=RIGHT_PANEL_WIDTH - 90, font=(self.font_family, FONT.SMALL),
            fg_color=T["BG_INPUT"], corner_radius=RADIUS.CONTROL, height=52,
            text_color=T["TEXT_SECONDARY"],
        )
        self.comment_label.pack(fill="x", pady=(SPACE.XXS, 0))

        self.folder_label = ctk.CTkLabel(
            body, text="", anchor="w", justify="left", wraplength=RIGHT_PANEL_WIDTH - 90,
            font=(self.font_family, FONT.TINY), text_color=T["TEXT_MUTED"],
        )
        # Folder identity remains available through details and context actions.

    def _build_statusbar(self) -> None:
        # row=4：上面依次是应用栏(0) / 左右两栏(1) / 底部操作栏(2)。
        bar = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=SPACE.XL, pady=(0, SPACE.M))
        bar.grid_columnconfigure(1, weight=1)

        self.status_label = ctk.CTkLabel(
            bar, text=tr("status.bar.ready"), anchor="w", font=(self.font_family, FONT.SMALL),
            text_color=T["TEXT_SECONDARY"],
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        self.progress_bar = ctk.CTkProgressBar(
            bar, height=6, corner_radius=3, width=220,
            progress_color=T["ACCENT"], fg_color=T["BG_ELEVATED"],
        )
        self.progress_bar.grid(row=0, column=2, sticky="e", padx=(SPACE.L, 0))
        self.progress_bar.set(0)

    # ---------------- 菜单与快捷键 ----------------
    def _build_menu(self) -> None:
        """原生菜单栏：把次要功能收进去，主界面保持干净。"""
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label=tr("menu.file.open_local"), accelerator="Ctrl+O", command=lambda: self.choose_folder("local"))
        file_menu.add_command(label=tr("menu.file.open_quest"), accelerator="Ctrl+Shift+O", command=lambda: self.choose_folder("quest"))
        file_menu.add_command(label=tr("dl.title"), command=self.open_downloads)
        file_menu.add_separator()
        file_menu.add_command(label=tr("menu.file.rescan"), accelerator="F5", command=self.reload_both)
        file_menu.add_command(label=tr("menu.file.wireless"), command=self.connect_wireless)
        file_menu.add_separator()
        file_menu.add_command(label=tr("menu.file.export_csv"),
                              command=lambda: self.export_report("csv"))
        file_menu.add_command(label=tr("menu.file.export_md"),
                              command=lambda: self.export_report("md"))
        file_menu.add_separator()
        file_menu.add_command(label=tr("menu.file.export_playlist"),
                              command=lambda: self.export_playlist("local"))
        file_menu.add_command(label=tr("menu.file.import_playlist"),
                              command=lambda: self.import_playlist("local"))
        file_menu.add_separator()
        file_menu.add_command(label=tr("menu.file.logs"), command=self._open_logs)
        file_menu.add_separator()
        file_menu.add_command(label=tr("menu.file.exit"), accelerator="Alt+F4", command=self.on_close)
        menubar.add_cascade(label=tr("menu.file"), menu=file_menu)

        act_menu = tk.Menu(menubar, tearoff=0)
        act_menu.add_command(label=tr("menu.action.load_local"), command=lambda: self.load_library("local"))
        act_menu.add_command(label=tr("menu.action.load_quest"), command=lambda: self.load_library("quest"))
        act_menu.add_separator()
        act_menu.add_command(label=tr("menu.action.compare_quest"), accelerator="Ctrl+1", command=lambda: self.compare("quest"))
        act_menu.add_command(label=tr("menu.action.compare_local"), accelerator="Ctrl+2", command=lambda: self.compare("local"))
        act_menu.add_separator()
        act_menu.add_command(label=tr("menu.action.copy_to_quest"), command=lambda: self.copy_missing("quest"))
        act_menu.add_command(label=tr("menu.action.copy_to_local"), command=lambda: self.copy_missing("local"))
        act_menu.add_separator()
        act_menu.add_command(label=tr("menu.action.duplicates"),
                             command=lambda: self.find_duplicates("local"))
        act_menu.add_command(label=tr("menu.action.duplicates.loose"),
                             command=lambda: self.find_duplicates("local", include_loose=True))
        act_menu.add_separator()
        act_menu.add_command(label=tr("menu.action.delete"), accelerator="Delete", command=self.delete_selected_song)
        menubar.add_cascade(label=tr("menu.action"), menu=act_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        self._appearance_var = ctk.StringVar(value=self.settings.appearance)
        for label, value in (("跟随系统", "System"), ("浅色", "Light"), ("深色", "Dark")):
            view_menu.add_radiobutton(label=label, value=value, variable=self._appearance_var,
                                      command=self._on_appearance_changed)
        menubar.add_cascade(label=tr("menu.view"), menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label=tr("menu.help.manual"), accelerator="F1", command=self.show_help)
        help_menu.add_command(label=tr("menu.help.wizard"), command=self.show_wizard)
        help_menu.add_command(label=tr("menu.help.diagnostics"), command=self.show_diagnostics)
        menubar.add_cascade(label=tr("menu.help"), menu=help_menu)

        try:
            self.application_menu = menubar
            self.root.configure(menu="")
        except Exception as exc:  # 某些平台/主题不支持菜单栏
            logger.warning("菜单栏创建失败: %s", exc)

    def _bind_shortcuts(self) -> None:
        bindings = {
            "<Control-o>": lambda: self.choose_folder("local"),
            "<Control-O>": lambda: self.choose_folder("local"),
            "<Control-Shift-O>": lambda: self.choose_folder("quest"),
            "<F5>": self.reload_both,
            "<F1>": self.show_help,
            "<Control-1>": lambda: self.compare("quest"),
            "<Control-2>": lambda: self.compare("local"),
            "<Control-f>": lambda: self._focus_search(),
            "<Control-F>": lambda: self._focus_search(),
            "<Control-d>": self.toggle_details,
            "<Control-D>": self.toggle_details,
            "<Control-comma>": self.open_settings,
            "<Escape>": self._on_escape,
        }
        for sequence, handler in bindings.items():
            try:
                self.root.bind(sequence, lambda _e, h=handler: (h(), "break")[1])
            except Exception:
                continue

    def _on_escape(self) -> None:
        """Esc：清空三个列表的搜索框（很常用的"回到全量视图"）。"""
        cleared = False
        for widget in (self.list_local, self.list_quest, self.list_missing):
            if widget.search_var.get():
                widget.set_search("")
                cleared = True
        if not cleared:
            self.list_missing.clear_highlight()

    def _focus_search(self) -> None:
        self.list_local.focus_search()

    # ==================================================================================
    # 启动 / 关闭
    # ==================================================================================
    def _initial_geometry(self) -> str:
        """窗口初始尺寸：不超过屏幕可用区域的 92%，并居中。

        为什么要夹一下：默认 1620x1000 在 1366x768 这类屏幕上会超出屏幕边界，
        Windows 会把窗口顶到 (0,0)，右侧/底部被裁掉（实测过一次：内容被切掉 400px）。
        """
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        target_w, target_h = (int(v) for v in WINDOW_SIZE.split("x"))
        width = min(target_w, int(screen_w * 0.92))
        height = min(target_h, int(screen_h * 0.92))
        width = max(width, MIN_SIZE[0])
        height = max(height, MIN_SIZE[1])
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 20)

        saved = self.settings.window_geometry
        if saved:
            try:
                size, _x, _y = saved.split("+", 2)
                saved_w, saved_h = (int(v) for v in size.split("x"))
                # 上限：换显示器后保存的尺寸可能已经超出屏幕，超了就回落默认。
                # 下限：必须和上面算默认尺寸时一样夹到 MIN_SIZE —— 测试跑完会把
                # 测试窗口的 200x200 写进真实的 settings.json，不夹下限的话
                # 用户下次启动程序窗口就只有 200x200，等于不可用。
                saved_w = max(saved_w, MIN_SIZE[0])
                saved_h = max(saved_h, MIN_SIZE[1])
                if saved_w <= screen_w and saved_h <= screen_h:
                    return f"{saved_w}x{saved_h}+{x}+{y}"
            except (ValueError, AttributeError):
                pass
        return f"{width}x{height}+{x}+{y}"

    def _restore_paths(self) -> None:
        for kind in ("local", "quest"):
            self._update_path_indicator(kind)

        # 恢复上次用的本地路径，并且**真的把它载进来**。
        #
        # 为什么必须载：`_restore_paths` 原先只更新那个小圆点，列表始终是空的 ——
        # 用户看到路径框里有字、目录却是空，"路径没记住"。只填框不加载等于没恢复。
        #
        # 为什么要延迟：`load_library` 会真的读磁盘/设备，在 `__init__` 里同步做
        # 会让窗口晚出来好几百毫秒。放到 after 里，先让界面出来再填。
        saved_local = self.settings.local_path.strip()
        if saved_local and os.path.isdir(saved_local):
            # 路径失效（盘符变了/目录删了）不当作错误，只把框清空并提示
            self.path_vars["local"].set(saved_local)
            self.root.after(300, lambda: self._auto_load("local", saved_local))
        elif saved_local:
            logger.info("上次的本地路径已不存在，忽略: %s", saved_local)
            self.toast.warning("上次的本地曲库找不到了", detail=shorten_path(saved_local))

        if self.settings.device_poll_on_start and not self.settings.quest_path:
            self.root.after(800, lambda: self.detect_device("quest", silent=True))

    def _auto_load(self, kind: str, path: str) -> None:
        """启动时自动载入上次的路径（失败只提示，不打断）。"""
        if self._closing:
            return
        current = self.path_vars[kind].get().strip()
        if current != path:
            return  # 用户已经手动改成别的了，别覆盖他的操作
        try:
            self.load_library(kind)
        except Exception as exc:  # 载入失败不该让程序起不来
            logger.warning("自动载入 %s 失败: %s", kind, exc)
            self.toast.warning("曲库载入失败", detail=str(exc)[:80])

    def run(self) -> None:
        # 控件标注器（调试用，可选）：Ctrl+Alt+I 开关，Ctrl+Alt+E 导出报告。
        # 任何异常都不影响主程序启动，所以整块包在 try 里。
        self._attach_inspector()
        self.root.mainloop()

    def _attach_inspector(self) -> None:
        """挂载 tk_inspector（控件标注）。环境变量 QI_INSPECTOR=0 可关闭。

        另有调试用开关：
            QI_AUTO_EXPORT=<毫秒>   启动后延迟 N 毫秒自动导出一次报告（默认不触发）
            QI_EXPORT_DIR=<目录>    报告输出目录（默认 ./tk_reports）
        """
        import os as _os

        self._inspector = None
        if _os.environ.get("QI_INSPECTOR", "1") == "0":
            return
        try:
            from . import tk_inspector

            out_dir = _os.environ.get("QI_EXPORT_DIR") or None
            self._inspector = tk_inspector.attach(self.root, out_dir=out_dir)
            logger.info("tk_inspector 已挂载（Ctrl+Alt+I 开关，Ctrl+Alt+E 导出）")

            # 调试用：延迟自动导出一次（等界面稳定、数据加载完）
            auto_ms = _os.environ.get("QI_AUTO_EXPORT", "").strip()
            if auto_ms.isdigit():
                inspector = self._inspector

                def _auto_export() -> None:
                    try:
                        inspector.export()
                        logger.info("tk_inspector 自动导出完成")
                    except Exception as exc:
                        logger.warning("tk_inspector 自动导出失败: %s", exc)
                    finally:
                        # 只在调试场景下用语，导出后就退出，避免留下进程
                        if _os.environ.get("QI_EXPORT_EXIT") == "1":
                            try:
                                self.root.after(200, self.root.destroy)
                            except Exception:
                                pass

                self.root.after(int(auto_ms), _auto_export)
                logger.info("tk_inspector：%s ms 后自动导出一次", auto_ms)
        except Exception as exc:  # 标注器是辅助工具，绝不能拖累主程序
            logger.warning("tk_inspector 挂载失败（不影响使用）: %s", exc)

    def on_close(self) -> None:
        self._closing = True
        if self._download_manager is not None:
            self._download_manager.shutdown()
        if self._download_window is not None and self._download_window.winfo_exists():
            self._download_window.close()
        try:
            self.settings.window_geometry = self.root.geometry()
            self.settings.local_path = self.path_vars["local"].get().strip()
            self.settings.quest_path = self.path_vars["quest"].get().strip()
            self.settings.save()
        except Exception as exc:
            logger.warning("保存设置失败: %s", exc)
        try:
            if self._drop_target is not None:
                self._drop_target.uninstall()
        except Exception:
            pass
        self.toast.clear()
        # 试听要先停：音频设备留到 GC 才关的话，那个收尾可能发生在
        # 解释器关闭期间（那时起线程会抛 PythonFinalizationError），
        # 表现为"关窗口卡一下"。
        stop_all_audio()
        stop_watchdog()
        self.tasks.cancel_all()
        for library in self.libraries.values():
            library.close()
        self.db.close()
        try:
            self.root.destroy()
        except Exception:
            pass

    # ==================================================================================
    # 消息泵（唯一的 UI 更新入口）
    # ==================================================================================
    def _pump_queue(self) -> None:
        if self._closing:
            return
        try:
            if self._download_manager is not None:
                for event in self._download_manager.tasks.drain():
                    data = event.payload
                    if isinstance(data, dict) and data.get("state") == "done":
                        raw = self.path_vars["local"].get().strip()
                        if raw and Path(raw).resolve() == Path(data["target"]):
                            self._download_focus = Path(data["installed"]).name
                            self._download_refresh_target = data["target"]
                            if self._download_refresh_job is not None:
                                self.root.after_cancel(self._download_refresh_job)
                            self._download_refresh_job = self.root.after(600, self._refresh_download_library)
            for message in self.tasks.drain(self.tasks.alive_token_values()):
                self._handle_message(message)
        except Exception as exc:  # 消息处理异常不能杀死轮询
            logger.error("处理后台消息失败: %s", exc, exc_info=True)
        finally:
            if not self._closing and self.root.winfo_exists():
                self.root.after(QUEUE_POLL_MS, self._pump_queue)

    def _handle_message(self, message: TaskMessage) -> None:
        kind = message.kind
        if message.percent >= 0:
            self.progress_bar.set(max(0, min(100, message.percent)) / 100)
            if message.percent >= 100:
                self.root.after(1400, lambda: self.progress_bar.set(0))
        if message.text and kind in ("progress", "done", "error", "status"):
            self.set_status(message.text)
        # 长任务的独立进度窗口：所有更新只在这里发生（UI 线程）
        if self._progress_dialog is not None and (not message.token or message.token == self._progress_token):
            try:
                if message.percent >= 0:
                    self._progress_dialog.set_progress(message.percent, message.text)
                elif message.text:
                    self._progress_dialog.append_log(message.text)
            except Exception:
                self._close_progress_dialog()

        if kind == "status":
            pass
        elif kind == "progress":
            pass
        elif kind == "folders":
            payload = message.payload or {}
            library = self.libraries[payload["kind"]]
            library.folders = payload["folders"]
            library.location = payload["location"]
            library.fs = payload["fs"]
            library.identity = library.location.identity
            library.error = ""
            # 走 `_fill_list` 而不是直接 set_items：它会按当前筛选档位裁剪，
            # 并且回填档位计数（"全部 85 / 已匹配 21"就是这里算出来的）。
            self._fill_list(payload["kind"])
            self._fill_list("quest" if payload["kind"] == "local" else "local")
            widget = self._list_for(payload["kind"])
            songs = widget.items()
            if not songs:
                widget.set_empty_hint(tr("app.empty_folder"))
            self._update_path_indicator(payload["kind"])
            self.set_status(tr("status.found",
                               label=self._label_for(payload["kind"]),
                               count=len(songs)))
        elif kind == "meta":
            payload = message.payload or {}
            library = self.libraries[payload["kind"]]
            library.meta.update(payload["meta"])
            if payload.get("done"):
                self._fill_list(payload["kind"])
        elif kind == "cover":
            self._apply_cover(message.payload)
        elif kind == "copy":
            payload = message.payload or {}
            if payload.get("status") == "song_done":
                self.list_missing.remove_item(payload["song"])
            elif payload.get("summary"):
                failed = int(payload.get("failed") or 0)
                self._finish_progress_dialog(payload["summary"])
                self.set_status(payload["summary"])
                if failed:
                    self.toast.warning(f"复制完成，{failed} 首失败", detail=payload["summary"])
                else:
                    self.toast.success("复制完成", detail=payload["summary"].splitlines()[0])
                if payload.get("reload"):
                    self.load_library(payload["reload"], silent=True)
        elif kind == "delete":
            payload = message.payload or {}
            self.set_status(payload.get("message", "已删除"))
            if payload.get("ok"):
                self._after_delete(payload)
                self.toast.success(f"已删除：{payload.get('folder', '')}")
            else:
                self.toast.error(payload.get("message", "删除失败"))
        elif kind == "device":
            self._apply_device_report(message.payload)
        elif kind == "adb_serials":
            # 后台线程查到的 adb 设备列表，缓存起来给界面线程用。
            # 界面线程自己去调 `adb devices` 最坏要 5.2 秒（冷启动），
            # 那就是用户说的"卡死"。
            payload = message.payload or {}
            self.note_adb_devices(list(payload.get("serials") or []))
        elif kind == "preview":
            self._on_preview_result(message.payload or {})
        elif kind == "toast":
            # 后台线程**不能**直接调 `self.toast.*` —— `ToastHost.show()` 会直接操作 Tk
            # （configure / 新建 Toplevel / after），没有线程编组。
            # 想让后台任务弹通知，就发这条消息，由主线程来弹。
            payload = message.payload or {}
            shower = {
                "info": self.toast.info,
                "success": self.toast.success,
                "warning": self.toast.warning,
                "error": self.toast.error,
            }.get(str(payload.get("level", "info")), self.toast.info)
            shower(payload.get("text") or message.text, detail=payload.get("detail", ""))
        elif kind == "done":
            self.set_status(message.text)
        elif kind == "error":
            self._finish_progress_dialog("任务失败")
            self.set_status(message.text)
            self.toast.error(message.text)
            if not (message.payload or {}).get("silent"):
                logger.error("任务错误: %s", message.text)

    # ---------------- 进度窗口 ----------------
    def _finish_progress_dialog(self, summary: str) -> None:
        dialog = self._progress_dialog
        self._progress_dialog = None
        self._progress_token = ""
        if dialog is None:
            return
        try:
            dialog.finish(summary)
            self.root.after(2500, self._close_progress_dialog)
        except Exception:
            self._close_progress_dialog()

    def _close_progress_dialog(self) -> None:
        dialog = self._progress_dialog
        self._progress_dialog = None
        self._progress_token = ""
        if dialog is None:
            return
        try:
            dialog.close()
        except Exception:
            try:
                dialog.destroy()
            except Exception:
                pass

    # ==================================================================================
    # 路径选择
    # ==================================================================================


    # ---------------- 最近用过的路径 ----------------


    # ==================================================================================
    # 加载歌曲库
    # ==================================================================================


    def load_library(self, kind: str, silent: bool = False) -> None:
        raw = self.path_vars[kind].get()
        try:
            location = self._resolve_location(kind, raw)
        except FSUnavailable as exc:
            self.set_status(str(exc))
            if not silent:
                self.toast.warning("路径不可用", detail=str(exc))
            self._update_path_indicator(kind, ok=False)
            return

        # 手动输入的路径也要记进"最近路径"（不然只有点浏览/检测才记）
        if raw.strip():
            self.settings.remember(kind, raw.strip())
            self.settings.save()

        previous = self._scan_tokens.get(kind)
        if previous:
            previous.cancel()

        library = self.libraries[kind]
        library.close()
        library.folders = []
        library.meta = {}
        widget = self._list_for(kind)
        widget.clear()
        widget.set_empty_hint("正在扫描歌曲…")
        self._refresh_maps()
        self.set_status(f"正在加载{self._label_for(kind)}…")
        self._update_path_indicator(kind, pending=True)

        def worker(token: Token) -> None:
            try:
                fs = open_fs(location, adb_path=self.settings.adb_path)
                if not fs.available():
                    raise FSUnavailable(f"路径不可访问: {location.root}")
                folders = fs.list_song_folders(
                    progress=lambda text, percent: self.tasks.emit("progress", text, percent, token=token)
                )
            except (FSUnavailable, FSError) as exc:
                self.tasks.emit("error", str(exc), token=token)
                return
            self.tasks.emit(
                "folders",
                f"已发现 {len(folders)} 首歌曲",
                30,
                payload={"kind": kind, "folders": folders, "location": location, "fs": fs},
                token=token,
            )

            # ---- 增量扫描：先**批量**取 mtime，再**批量**查缓存 ----
            # 必须批量，而且必须一次往返（v14.5 修的）：
            #   * 原先对每首歌调一次 `fs.stat_mtime`，在 adb 上就是**每首一次 shell 往返**
            #     —— 真机 Quest 3 / 217 首实测 **17.6 秒**；一次 `find -printf` 只要
            #     **0.20 秒（90 倍）**；
            #   * 原先逐首 `db.get_meta` 是 N 次查询，现改成一次 `bulk_get_meta`；
            #   * 更要命的是原先**从不写缓存**（只有点开某首歌的 `show_song` 会写），
            #     所以 `cached` 永远是空的、每次都全量重解析 ——
            #     "增量扫描"这个功能实际上从未生效，那 17.6 秒是纯开销。
            #     现在解析完的结果会 `save_meta_bulk` 落库。
            # mtime 取不到（例如 MTP）时值为 0，会退回全量扫描。
            mtimes = fs.stat_mtimes(folders)
            keys = [(folder, mtimes.get(folder, 0.0)) for folder in folders if mtimes.get(folder)]
            rows = self.db.bulk_get_meta(keys) if keys else {}
            cached: dict[str, SongMeta] = {}
            to_scan: list[str] = []
            for folder in folders:
                row = rows.get(folder)
                if row is not None:
                    cached[folder] = meta_from_row(folder, row)
                else:
                    to_scan.append(folder)

            if cached:
                self.tasks.emit(
                    "meta", f"缓存命中 {len(cached)} 首，需解析 {len(to_scan)} 首", 55,
                    payload={"kind": kind, "meta": cached, "done": False}, token=token,
                )
            logger.info("%s：缓存命中 %d 首，需重新解析 %d 首", kind, len(cached), len(to_scan))

            if to_scan:
                fresh = fs.scan_metadata(
                    to_scan,
                    progress=lambda text, percent: self.tasks.emit(
                        "progress", text, 55 + int(percent * 0.4), token=token
                    ),
                )
            else:
                fresh = {}

            # 把刚解析出来的落库 —— 这一步原先缺失，导致缓存永远是空的、
            # 下次载入又全量重解析（"增量扫描"从未生效）。一次批量提交。
            if fresh:
                try:
                    self.db.save_meta_bulk([meta_row(meta) for meta in fresh.values()])
                    # 顺手清掉同一首歌的旧 mtime 行（缓存查找是按 mtime 精确匹配的，
                    # 旧行永远不会命中，留着只会让库越来越大）
                    self.db.prune_meta()
                except Exception as exc:  # 缓存写失败不该让歌单加载失败
                    logger.warning("写入元数据缓存失败: %s", exc)

            metas = dict(cached)
            metas.update(fresh)
            self.tasks.emit("meta", tr("status.meta_done"), 100,
                            payload={"kind": kind, "meta": metas, "done": True}, token=token)
            detail = tr("status.cache_hits", count=len(cached)) if cached else ""
            self.tasks.emit("done",
                            tr("status.load_done",
                               label=self._label_for(kind),
                               count=len(folders),
                               detail=detail),
                            token=token)

        self._scan_tokens[kind] = self.tasks.submit(worker, name=f"load_{kind}")

    def reload_both(self) -> None:
        loaded = False
        for kind in ("local", "quest"):
            if self.path_vars[kind].get().strip():
                self.load_library(kind, silent=True)
                loaded = True
        if not loaded:
            self.toast.warning("还没有设置路径", detail=tr("app.pick_folder_first"))
        else:
            self.toast.info("已重新扫描", detail=tr("app.refreshing"))

    def _refresh_maps(self) -> None:
        self._rating_map = self.db.all_ratings()
        self._title_map = self.db.all_titles()

    def _make_item(self, kind: str, folder: str) -> SongItem:
        # 「缺失」不是一份曲库，而是**某一侧的视图** —— 真正拥有这些歌的是
        # `missing_side` 指的那一侧。以前这里直接 `self.libraries["missing"]`
        # 取不到，会把「对比」整条链路打成 KeyError。
        sample_kind = self._sample_kind(kind)
        library = self.libraries[sample_kind]
        meta = library.meta.get(folder)
        local_path = ""
        if library.location and library.location.kind == "local":
            local_path = str(Path(library.location.root) / folder)
        if meta is None:
            return SongItem(folder_name=folder, title=self._title_map.get(folder, ""), local_path=local_path)
        return SongItem(
            folder_name=folder,
            title=meta.display_title,
            author=meta.display_author,
            mtime=meta.mtime,
            local_path=local_path,
            cover_path=str(Path(local_path) / meta.cover_file) if local_path and meta.cover_file else "",
        )

    def _sample_kind(self, kind: str) -> str:
        """把「缺失」映射回真正拥有这些歌的那一侧。

        「本地缺的」= Quest 上有、本地没有 → 歌在 Quest 侧；
        「Quest 缺的」= 本地有、Quest 没有 → 歌在本地侧。
        """
        if kind == "missing":
            return "local" if self.missing_side == "quest" else "quest"
        return kind

    def _ratings_for(self, kind: str) -> dict[str, int]:
        library = self.libraries[self._sample_kind(kind)]
        if not library.identity:
            return dict(self._rating_map)
        ratings = self.db.all_ratings(library.identity)
        merged = dict(self._rating_map)
        merged.update(ratings)
        return merged

    def _list_for(self, kind: str) -> SongListWidget:
        return {"local": self.list_local, "quest": self.list_quest, "missing": self.list_missing}[kind]

    @staticmethod
    def _label_for(kind: str) -> str:
        return {
            "local": tr("label.local"),
            "quest": tr("label.quest"),
            "missing": tr("label.missing"),
        }.get(kind, kind)

    # ==================================================================================
    # 列表筛选（v17.7）：全部 / 已匹配 / 缺失
    # ==================================================================================
    def _sample_names(self, kind: str) -> list[str]:
        """某一侧当前的歌曲名集合（"missing" 列不参与比对，返回空）。"""
        if kind == "missing":
            return []
        library = self.libraries.get(kind)
        return list(library.folders) if library is not None else []

    def _filter_counts(self, kind: str) -> dict[str, int]:
        """算出某列三个档位各有多少首。

        "已匹配 / 缺失"是**相对另一侧**说的：

        * 本地列：这首歌在 Quest 上也有 → 已匹配；否则缺失；
        * Quest 列：这首歌在本地也有 → 已匹配；否则本地没有它。

        两侧名字都可能是重名，所以用集合运算而不是顺序比较。
        """
        if kind == "missing":
            total = len(self.missing)
            return {"all": total, "matched": 0, "missing": total}
        if kind == "local":
            mine = set(self._sample_names("local"))
            other = set(self._sample_names("quest"))
        else:
            mine = set(self._sample_names("quest"))
            other = set(self._sample_names("local"))
        matched = len(mine & other)
        return {"all": len(mine), "matched": matched, "missing": len(mine) - matched}

    def _apply_song_filter(self, kind: str, value: str) -> None:
        """按筛选档位重灌某一列的内容（在界面线程里跑，只做集合运算不碰磁盘）。"""
        widget = self._list_for(kind)
        widget.set_filter(value)
        counts = self._filter_counts(kind)
        widget.set_filter_counts(counts)
        self._fill_list(kind)

    def _fill_list(self, kind: str) -> None:
        """按当前筛选档位把某列的数据喂给列表控件。"""
        widget = self._list_for(kind)
        value = widget.current_filter()
        # 计数必须先更新：`set_items` 之后用户马上就能看到徽标上的数字，
        # 而"全部 85 / 已匹配 21"是判断"还要不要对比"的直接依据。
        widget.set_filter_counts(self._filter_counts(kind))
        sample_kind = self._sample_kind(kind)
        both_loaded = all(lib.location is not None for lib in self.libraries.values())
        if both_loaded:
            other_kind = "quest" if sample_kind == "local" else "local"
            widget.set_presence(self.libraries[sample_kind].name_set - self.libraries[other_kind].name_set)
        else:
            widget.set_presence(None)
        if kind == "missing":
            widget._title_text = tr("layout.quest_missing" if self.missing_side == "quest" else "layout.local_missing")
        names = self._visible_names(kind, value)
        items = [self._make_item(kind, name) for name in names]
        widget.set_items(
            items,
            title_map=self._title_map,
            rating_map=self._ratings_for(kind),
            highlight=self.missing if kind == "missing" else (),
        )
        if not items:
            widget.set_empty_hint(self._empty_hint_for(kind))
        if kind == "local" and self._download_focus and any(item.folder_name == self._download_focus for item in items):
            widget.select(self._download_focus)
            self._download_focus = ""

    def _visible_names(self, kind: str, value: str) -> list[str]:
        """按档位裁剪后的歌曲名列表（保持原顺序）。"""
        if kind == "missing":
            return list(self.missing) if value != "matched" else []
        names = self._sample_names(kind)
        if value == "all":
            return names
        other_kind = "quest" if kind == "local" else "local"
        other = set(self._sample_names(other_kind))
        if value == "matched":
            return [name for name in names if name in other]
        return [name for name in names if name not in other]

    @staticmethod
    def _empty_hint_for(kind: str) -> str:
        """空列表时的提示 —— 筛选档位下的"空"和"没载入"不是一回事。"""
        return {
            "missing": "hint.missing_list",
            "local": "hint.local_wall",
            "quest": "hint.quest_wall",
        }.get(kind, "hint.no_library")


    # ==================================================================================
    # 拖放导入
    # ==================================================================================
    def _install_dnd(self) -> None:
        """启用"把歌曲文件夹从资源管理器拖进来"（Windows WM_DROPFILES，无第三方依赖）。"""
        try:
            from .ui_dnd import DropTarget
        except Exception as exc:  # pragma: no cover
            logger.debug("拖放模块不可用: %s", exc)
            return

        def on_drop(paths: list[str]) -> None:
            self._handle_drop(paths)

        target = DropTarget(self.root, on_drop=on_drop)
        if target.install():
            self._drop_target = target
            logger.info("拖放已启用")
        else:
            logger.debug("拖放未启用（非 Windows 或初始化失败）")

    def _drop_target_kind(self, x: int | None = None, y: int | None = None) -> str | None:
        """拖放落在哪个列表上 —— 用**落点坐标**判断（B2）。

        `WM_DROPFILES` 不带坐标，但拖放必然发生在鼠标位置，而 `WM_DROPFILES` 是在
        UI 线程里处理的，所以 `winfo_pointerxy()` 拿到的就是落点。
        这样就不必再弹"输入 1 或 2"了。

        `x` / `y` 是给测试用的显式坐标（不传就用当前鼠标位置）。
        """
        if x is None or y is None:
            try:
                x, y = self.root.winfo_pointerxy()
            except tk.TclError:  # pragma: no cover - 窗口正被销毁
                return None
        for kind in ("local", "quest"):
            widget = self._list_for(kind)
            if widget is None or not widget.winfo_exists():
                continue
            try:
                wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
                width, height = widget.winfo_width(), widget.winfo_height()
            except tk.TclError:  # pragma: no cover
                continue
            if wx <= x <= wx + width and wy <= y <= wy + height:
                return kind
        return None

    def _flash_drop_target(self, kind: str, milliseconds: int = 1200) -> None:
        """点亮目标列一会儿，让用户看清导入到了哪一侧。"""
        widget = self._list_for(kind)
        if widget is None:
            return
        widget.set_drop_active(True)

        def restore() -> None:
            widget.set_drop_active(False)

        try:
            self.root.after(milliseconds, restore)
        except tk.TclError:  # pragma: no cover
            pass

    def _handle_drop(self, paths: Sequence[str]) -> None:
        """整理拖入的路径：识别 CustomLevels 根目录、过滤掉非文件夹，然后导入到**落点那一列**。"""
        from .ui_dnd import describe_drop

        folders, message = describe_drop(paths)
        if not folders:
            self.toast.warning("拖入的内容里没有文件夹", detail=message)
            return

        # 如果拖进来的是 CustomLevels 根目录（里面全是歌曲文件夹），就展开成歌曲列表
        songs: list[str] = []
        for folder in folders:
            entries = [e for e in Path(folder).iterdir() if e.is_dir() and not e.name.startswith(".")]
            has_info = any((e / n).is_file() for e in entries for n in ("info.dat", "Info.dat"))
            if entries and has_info:
                songs.extend(str(e) for e in entries)
            else:
                songs.append(folder)

        deduped: list[str] = []
        for item in songs:
            if item not in deduped:
                deduped.append(item)
        if not deduped:
            self.toast.warning("这些文件夹里没有歌曲", detail=tr("app.drop_hint"))
            return

        local_ready = bool(self.path_vars["local"].get().strip())
        quest_ready = bool(self.path_vars["quest"].get().strip())
        if not local_ready and not quest_ready:
            self.toast.warning(
                "还没有可用目标",
                detail=tr("app.drop_no_path"),
            )
            return

        # 优先按"拖到了哪一列"决定目标（B2）。只有落点不在任何列表上时，
        # 才退回问一句 —— 这样 99% 的情况不用再输"1 或 2"。
        dropped_on = self._drop_target_kind()
        if dropped_on is not None and (
            (dropped_on == "local" and local_ready) or (dropped_on == "quest" and quest_ready)
        ):
            target_kind = dropped_on
        elif local_ready and quest_ready:
            choice = TextPromptDialog(
                self.root,
                title=tr("import.where.title"),
                prompt=(
                    f"收到 {len(deduped)} 首歌曲。没有拖到具体的列表上 —— "
                    "输入 1 复制到本地歌单，输入 2 复制到 Quest。"
                    "（也可以直接把歌曲拖到目标列表上，就不必输这个了）"
                ),
                placeholder="1 或 2",
                initial="1",
                validate=lambda v: None if v.strip() in ("1", "2") else "请输入 1 或 2",
            ).show()
            if choice is None:
                self.toast.info("已取消导入")
                return
            target_kind = "local" if choice.strip() == "1" else "quest"
        else:
            target_kind = "local" if local_ready else "quest"

        self._flash_drop_target(target_kind)
        self.import_songs(deduped, target_kind)

    def import_songs(self, paths: Sequence[str], target_kind: str) -> None:
        """把外部文件夹复制进指定歌单（后台执行 + 进度窗口）。"""
        if self._importing:
            self.toast.warning("上一次导入还没结束")
            return
        library = self.libraries[target_kind]
        if library.location is None or library.fs is None:
            self.toast.warning("目标歌单还没载入", detail=f"请先载入{self._label_for(target_kind)}，再拖放")
            return

        items = [Path(p) for p in paths]
        if not items:
            return
        if not ConfirmDialog(
            self.root,
            title=tr("import.confirm.title"),
            message=tr("import.confirm.message", count=len(items),
                       target=self._label_for(target_kind)),
            detail=tr("import.confirm.detail", path=library.location.root),
            confirm_text=tr("import.confirm.ok"),
        ).show():
            return

        total = len(items)
        token = self.tasks.new_token("import")
        progress = ProgressDialog(
            self.root, title=tr("import.progress.title"), message=tr("import.progress.message", count=total),
            cancel_callback=lambda: token.cancel(),
        )
        self._progress_dialog = progress
        self._progress_token = token.value
        progress.show_nonblocking()
        self._importing = True

        def worker(task_token: Token) -> None:
            ok = skipped = 0
            failures: list[str] = []
            target_fs = library.fs
            target_location = library.location
            for index, source in enumerate(items, start=1):
                if not task_token.alive() or not token.alive():
                    break
                try:
                    if not source.is_dir():
                        raise FSError(f"源目录不存在: {source}")
                    name = source.name
                    assert target_fs is not None
                    if not self.settings.copy_overwrite and target_fs.local_folder_exists(name):
                        # 设置里关掉"覆盖已存在"：目标上已有的直接跳过
                        skipped += 1
                        self.tasks.emit("import", f"已存在，跳过 {name}", -1,
                                        payload={"name": name}, token=task_token)
                    elif target_location is not None and target_location.kind == "local":
                        target_root = Path(target_location.root)
                        guard_copy(source.parent, target_root, name)  # 复用护栏：源==目标等
                        target_fs.copy_into(name, source, progress=None)
                        ok += 1
                        self.tasks.emit("import", f"已导入 {name}", -1,
                                        payload={"name": name}, token=task_token)
                    else:
                        target_fs.copy_into(name, source, progress=None)
                        ok += 1
                        self.tasks.emit("import", f"已导入 {name}", -1,
                                        payload={"name": name}, token=task_token)
                except (GuardError, FSError, FSUnavailable, OSError) as exc:
                    failures.append(f"{source.name}: {exc}")
                    logger.warning("导入失败 %s: %s", source, exc)
                self.tasks.emit("progress", f"导入中 {index}/{total}：{source.name[:26]}",
                                int(index / total * 100), token=task_token)

            summary = f"导入完成：成功 {ok}，失败 {len(failures)}"
            if skipped:
                summary += f"，跳过 {skipped}（目标已存在，设置里关了覆盖）"
            if failures:
                summary += "\n" + "\n".join(failures[:5])
            self.tasks.emit("copy", summary, 100,
                            payload={"summary": summary, "failed": len(failures),
                                     "reload": target_kind},
                            token=task_token)
            self._importing = False
            if target_location is not None and target_location.kind == "mtp":
                # 注意：这里是后台线程，**不能**直接调 self.toast.*
                self.tasks.emit(
                    "toast", "", -1,
                    payload={
                        "level": "info",
                        "text": "MTP 模式下设备上的旧同名歌曲会留在 <名字>__旧版本_questbak",
                        "detail": "确认新版本正常后，可在资源管理器里删掉这些备份文件夹",
                    },
                    token=task_token,
                )

        self.tasks.submit(worker, name="import_songs")

    # ==================================================================================
    # 歌曲详情
    # ==================================================================================
    def _save_column_widths(self, kind: str, widths: dict[str, int]) -> None:
        """记住用户拖出来的列宽（下次启动沿用）。"""
        try:
            self.settings.column_widths[kind] = dict(widths)
            self.settings.save()
        except Exception as exc:
            logger.debug("保存列宽失败: %s", exc)

    def _on_row_selected(self, kind: str, folder: str) -> None:
        """列表选中回调：主列表用 kind(missing 需要判断真正在哪一侧)。"""
        if kind == "missing":
            self.show_missing_song(folder)
        else:
            self.show_song(kind, folder)

    def _on_multi_selected(self, kind: str, names: Sequence[str]) -> None:
        """多选变化：记录当前选择集，供"批量删除/批量复制"使用。

        `names` 现在是**跨页**选择集（B1），所以翻页后累计选择也会带过来。
        """
        self.selected_songs[kind] = list(names)
        total = sum(len(v) for v in self.selected_songs.values())
        if total > 1:
            self.set_status(f"已选中 {total} 首（Ctrl+A 当前页 · Ctrl+Shift+A 全部 · Esc 取消）")


    # ---------------- 供外部/测试读取当前状态 ----------------


    # ==================================================================================
    # 评分 / 评价
    # ==================================================================================


    # ==================================================================================
    # 对比
    # ==================================================================================


    # ==================================================================================
    # 复制
    # ==================================================================================

    #: 还没测过时的兜底速率（MB/s）。
    #: * local 150 —— 本地磁盘，基本不构成瓶颈；
    #: * adb 12 —— 真机实测 Quest 3 / USB 上 pull 是 **17.4 MB/s**，留点余量取 12；
    #: * mtp 4 —— MTP 协议开销大得多（且本机没条件实测），保守取值。
    #: 只要成功复制过一次，就会被实测值取代（见 `_record_transfer`）。
    _DEFAULT_RATES = {"local": 150.0, "adb": 12.0, "mtp": 4.0}


    # ==================================================================================
    # 删除
    # ==================================================================================


    # ==================================================================================
    # 其他
    # ==================================================================================
    def _open_logs(self) -> None:
        from .storage import logs_dir

        try:
            os.startfile(str(logs_dir()))  # type: ignore[attr-defined]
        except Exception as exc:
            self.toast.error("无法打开日志目录", detail=str(exc))

    def _log_tail_section(self, limit: int = 40) -> list[str]:
        """挑出日志尾部的 WARNING/ERROR，供诊断对话框展示。

        为什么不直接说"你自己去 logs 目录看"：日志有几千行、混着 INFO 噪声，
        而用户报"卡死"时最有用的恰恰是**卡在哪一步**和**最近的报错**。
        只读尾部 4000 行（日志会轮转，读整个文件没意义）。
        """
        from .storage import logs_dir

        log_file = logs_dir() / "quest_comparator.log"
        interesting: list[str] = []
        if log_file.exists():
            try:
                with log_file.open("r", encoding="utf-8", errors="replace") as handle:
                    tail_lines = handle.readlines()[-4000:]
                for line in tail_lines:
                    if " - WARNING - " in line or " - ERROR - " in line \
                            or " - CRITICAL - " in line:
                        interesting.append(line.rstrip())
            except OSError as exc:
                interesting.append(f"（读取日志失败：{exc}）")
        else:
            interesting.append(f"（还没有日志文件：{log_file}）")

        return [
            f"最近的警告与错误（共 {len(interesting)} 条，显示最后 {limit} 条）：",
            "",
            *(interesting[-limit:] or ["（没有警告或错误）"]),
            "",
            f"完整日志：{log_file}",
        ]

    def open_in_explorer(self, kind: str, folder: str) -> None:
        library = self.libraries[kind]
        if library.location is None or library.location.kind != "local":
            self.toast.info(tr("detail.only_local"))
            return
        target = Path(library.location.root) / folder
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            self.toast.error("打开失败", detail=str(exc))

    def _on_appearance_changed(self) -> None:
        mode = self._appearance_var.get()
        self.settings.appearance = mode
        self.settings.save()
        ctk.set_appearance_mode(mode)
        self._refresh_theme()

    def _refresh_theme(self) -> None:
        for widget in (self.list_local, self.list_quest, self.list_missing):
            widget.refresh_theme()
        self.root.configure(fg_color=T["BG_APP"])
        self.paned.configure(background=color("BG_APP"))
        self.right_card.configure(bg_color=T["BG_APP"])
        if self._last_cover_source is not None:
            self._render_cover(*self._last_cover_source)
        self.root.after(50, self._force_repaint)

    def _force_repaint(self) -> None:
        """切换主题后使整个窗口及子窗口失效重绘，保留焦点和窗口所属层级。"""
        try:
            self.window_chrome.repaint()
        except Exception:  # pragma: no cover - 窗口已关闭
            pass

    def open_settings(self) -> None:
        SettingsDialog(self.root, self.settings, on_saved=self._refresh_theme).show()

    # ---------------- 首次使用向导（ROADMAP C4） ----------------
    def show_wizard(self) -> None:
        """打开首次使用向导。三步：选本地文件夹 → 检测 Quest → 开始对比。

        向导自己不干活，三个动作都回调本类 —— 选目录/检测设备/对比都涉及后台任务与
        状态机，只有主程序知道该怎么做；向导只负责显示"到哪一步了"。
        """
        from .ui_wizard import FirstRunWizard

        if self._wizard is not None:
            try:
                self._wizard.focus_force()
                return
            except tk.TclError:
                self._wizard = None
        wizard = FirstRunWizard(
            self.root,
            local_path=self.path_vars["local"].get().strip(),
            quest_path=self.path_vars["quest"].get().strip(),
            on_pick_local=lambda: self.choose_folder("local"),
            on_detect_quest=lambda: self.detect_device("quest"),
            on_start_compare=lambda: self.compare("quest"),
        )
        self._wizard = wizard
        try:
            wizard.show()
        finally:
            self._wizard = None
            # 看过一次就不再自动弹（手动仍可从「帮助」打开）
            if not self.settings.wizard_done:
                self.settings.wizard_done = True
                self.settings.save()

    def _refresh_wizard(self) -> None:
        """向导开着时，把最新路径刷进去（选完文件夹 / 检测完设备之后调用）。"""
        if self._wizard is None:
            return
        try:
            self._wizard.refresh(
                self.path_vars["local"].get().strip(),
                self.path_vars["quest"].get().strip(),
            )
        except tk.TclError:
            self._wizard = None

    def show_help(self) -> None:
        palette = get_palette()
        InfoDialog(
            self.root,
            title=tr("help.title"),
            message=tr("help.usage"),
        ).show()

    def show_diagnostics(self) -> None:
        from .adb import adb_status
        from .device_shell import ShellBridge
        from .storage import data_dir

        lines = [
            f"版本：V{APP_VERSION}",
            f"Python：{sys.version.split()[0]}",
            f"外观：{current_mode()}",
            f"界面缩放：{self.settings.ui_scale}（实际 {getattr(self, 'ui_scale', 0):.2f}）",
            f"语言：{self.settings.language}",
            f"看门狗：{'运行中' if watchdog_running() else '未运行'}"
            f"（当前操作：{current_operation() or '无'}）",
            f"数据目录：{data_dir()}",
            f"配置：{settings_path()}",
            f"缓存库：{database_path()}",
            f"字体：{self.font_family}",
            "",
        ]
        try:
            status = adb_status(self.settings.adb_path)
            lines.append(f"adb：{status['adb'] or '未找到'}")
            if status["ok"]:
                for device in status["devices"]:  # type: ignore[union-attr]
                    lines.append(f"  · {device.get('model')} ({device.get('serial')}) {device.get('state')}")
            else:
                lines.append(f"  · {status['reason']}")
        except Exception as exc:
            lines.append(f"adb 检测异常：{exc}")
        try:
            bridge = ShellBridge()
            lines.append(f"Shell 命名空间桥：{'可用' if bridge.available else '不可用'}")
            if bridge.available:
                devices = bridge.portable_devices()
                lines.append(f"  便携设备：{[d.name for d in devices] or '未检测到'}")
        except Exception as exc:
            lines.append(f"Shell 桥异常：{exc}")
        for kind in ("local", "quest"):
            library = self.libraries[kind]
            lines.append(
                f"{self._label_for(kind)}：{len(library.folders)} 首"
                + (f"（{library.location.kind}）" if library.location else "（未载入）")
            )
        lines.append(f"缓存：{self.db.stats()}")
        # 最近的警告/错误：用户报"卡死"时这一屏就能看出个大概
        lines.append("")
        lines.extend(self._log_tail_section())
        InfoDialog(self.root, title=tr("help.diagnostics.title"), message="\n".join(lines)).show()

    def set_status(self, text: str, error: bool = False) -> None:
        role = "DANGER" if error else "TEXT_SECONDARY"
        try:
            self.status_label.configure(text=text, text_color=T[role])
        except Exception:
            pass
        if error:
            logger.error("状态: %s", text)
        else:
            logger.info("状态: %s", text)
