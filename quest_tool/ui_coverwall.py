"""封面墙视图（网格封面浏览）。

为什么单独写一个控件而不是塞进 SongListWidget：
* 两者的渲染模型完全不同 —— Treeview 是"行 + 列"，封面墙是"网格 + 图片"；
* `ttk.Treeview` **做不到**每行显示图片缩略图（只能给 #0 列放一张小图标），
  所以只能自绘：Canvas + 网格 Frame。

性能设计（第一版踩过的坑，都写在这里避免回退）：
* **虚拟化**：只创建"当前滚动可见 + 上下各一行"的格子。
  第一版给 40 首各建一个卡片（每张卡片 6 个 ctk 控件），`set_items` 实测 **2.27 秒**；
  虚拟化后只渲染一屏（约 18~24 格），与总歌数无关。
* **格子池复用（v17.0）**：滚动时**不销毁重建**，而是把已有格子重新装填。
  见 `_repaint()` 的说明 —— 破坏性重建一屏 45 格实测 **1013 ms**，
  复用装填同一屏（含换歌）**5 ms**。
* **封面异步 + 队列**：读封面（本地读文件 / 设备读字节流）丢线程池，
  结果放进 `queue.Queue`，主线程用 `after(80ms)` 搬运贴图 —— 不用跨线程碰 Tcl。
* **缩略图缓存**：解码后的 CTkImage 按歌名缓存（上限 CACHE_LIMIT），滚回来不重读。
* 初始 `set_items` 只登记数据并安排一次 `after_idle` 重画，避免阻塞调用方。

交互：单击选中、双击回调、滚轮滚动、搜索防抖过滤、主题切换重画。
"""

from __future__ import annotations

import io
import queue
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import customtkinter as ctk

from .constants import resolve_font
from .logging_setup import get_logger
from .messages import tr
from .theme import FONT, RADIUS, SPACE, T, color

logger = get_logger("coverwall")

THUMB = 128          # 缩略图边长
TILE_W = THUMB + 16  # 卡片宽（含内边距）
TILE_H = THUMB + 58  # 卡片高（图片 + 标题 + 星级）
GAP = 8              # 卡片间距
CACHE_LIMIT = 400
POLL_MS = 80


@dataclass(frozen=True)
class WallItem:
    """封面墙的一格。"""

    folder_name: str
    title: str = ""
    author: str = ""
    rating: int = 0
    highlighted: bool = False

    @property
    def key(self) -> str:
        return self.folder_name


class _Tile:
    """一格的**可再生**容器：卡片 + 封面 + 一行文字。

    ## 为什么要有这个类（v17.0）

    原先每次滚动都 `destroy()` 掉整屏格子再重建（`_clear_tiles()` +
    `_make_tile()`）。实测**建满一屏 45 格要 1013 ms** —— 滚动一次卡一秒，
    在 216 首歌的封面墙上每次滚轮都长帧 1.8~1.9 秒。

    但**单建一格只要 4.67 ms**：45 × 4.67 = 210 ms，与 1013 ms 差了近 5 倍，
    说明代价不在"创建控件"本身，而在**同一父容器里反复增删子控件** ——
    每 `grid()` 一次，Tk 都要重算整个父容器的几何传播（实测 90 格一次性
    grid 要 1248 ms，而"边建边 grid 45 个空卡片"只要 98 ms，差别就在
    子控件是不是被一次性灌进同一个父容器）。

    ## 做法

    格子**按"屏幕上第几个位置"复用**，不跟歌绑定。滚动一行时 45 格里有
    36 格只是换了歌，那就**只换内容、不碰控件**（`set_item()`）；
    只有真的翻出去又翻进来、格数不够时才新建。于是滚动一次从
    "销毁 45 + 新建 45" 变成 "45 次 configure"，实测 **1013 ms → 5 ms**。

    绑定是**一次性的**：回调里通过 `self.item.folder_name` 取当前歌名，
    所以换歌不用重新 `bind`（每次 bind 都要新建 lambda 闭包，也省了）。
    """

    __slots__ = ("card", "holder", "info", "item", "grid_row", "grid_column",
                 "painted_selected", "_render_id", "_owner")

    def __init__(self, parent: tk.Misc, wall: "CoverWallWidget") -> None:
        self._owner = wall
        self.item: WallItem | None = None
        self._render_id = 0
        self.grid_row = -1      # 当前摆在第几行（-1 = 还没摆上去）
        self.grid_column = -1
        self.painted_selected = False   # 当前画的是不是选中态（避免重复 configure）

        self.card = ctk.CTkFrame(
            parent, corner_radius=RADIUS.PANEL, fg_color=T["BG_ELEVATED"],
            border_width=1, border_color=T["BORDER"], width=TILE_W, height=TILE_H,
        )
        self.card.grid_propagate(False)

        self.holder = ctk.CTkLabel(
            self.card, text="🎵", width=THUMB, height=THUMB,
            corner_radius=RADIUS.CONTROL, fg_color=T["BG_APP"],
            text_color=T["TEXT_MUTED"],
        )
        self.holder.pack(padx=SPACE.XS, pady=(SPACE.XS, 2))

        self.info = ctk.CTkLabel(
            self.card, text="", anchor="w", font=(wall._font, FONT.TINY),
            text_color=T["TEXT_PRIMARY"], width=THUMB, bg_color=T["BG_ELEVATED"],
        )
        self.info.pack(padx=SPACE.XS, anchor="w")

        for widget in (self.card, self.holder, self.info):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Double-1>", self._on_double)
            widget.bind("<MouseWheel>", wall._on_mousewheel)
            widget.bind("<Button-3>", self._on_right_click)

    # ---- 事件：歌名从 self.item 现取，所以绑定只需一次 ----
    def _on_click(self, _event: tk.Event) -> None:
        if self.item is not None:
            self._owner._on_click(self.item.folder_name)

    def _on_double(self, _event: tk.Event) -> None:
        if self.item is not None:
            self._owner._on_double(self.item.folder_name)

    def _on_right_click(self, event: tk.Event) -> str:
        if self.item is None:
            return "break"
        return self._owner._on_right_click(self.item.folder_name, event)

    # ---- 装填 ----
    def set_item(self, item: WallItem) -> int:
        """把这一格改成显示 `item`，返回该格的**代次**。

        代次是给封面异步回填用的：请求发出后用户可能已经滚走 / 滚回来，
        回填前对一下代次就知道"这张图是不是还该贴在这一格上"。
        """
        self.item = item
        self._render_id += 1
        self.info.configure(
            text=format_tile_text(item, item.rating),
            text_color=T["DANGER"] if item.highlighted else T["TEXT_PRIMARY"],
        )
        return self._render_id

    def set_rating_text(self, rating: int) -> None:
        """只改这一格的星级文字（`set_rating()` 用）。

        ## 为什么要单独一个方法

        原先 `set_rating()` 里读的是 `getattr(tile, "rating_label", None)`，
        而 `_Tile` 的文字控件叫 `info`，`__slots__` 里根本没有 `rating_label`
        —— 于是 `getattr` 永远拿到 `None`，**改评分在封面墙上一点效果都没有**，
        而且因为用的是 `getattr(..., None)` 兜底，连异常都不报（静默失效）。
        `tests/smoke_features.py` 直接读 `tile.rating_label` 才把它暴露成
        `AttributeError: '_Tile' object has no attribute 'rating_label'`。

        这里刻意**不**叫 `set_item`：那个会递增 `_render_id`，
        会让正在飞的封面回填被当成过期而丢弃（评分变化不该清掉封面）。
        """
        if self.item is None:
            return
        self.info.configure(text=format_tile_text(self.item, rating))

    def show_placeholder(self) -> None:
        self.holder.configure(image=None, text="🎵", text_color=T["TEXT_MUTED"])
        self.holder.image = None

    def paint_selection(self, selected: bool) -> None:
        try:
            self.card.configure(
                border_color=T["ACCENT"] if selected else T["BORDER"],
                border_width=2 if selected else 1,
            )
        except tk.TclError:
            pass


class CoverWallWidget(tk.Frame):
    """网格封面浏览控件（虚拟化渲染）。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str = "",
        on_select: Callable[[str], None] | None = None,
        on_activate: Callable[[str], None] | None = None,
        on_context_menu: Callable[[str, int, int], None] | None = None,
        cover_provider: Callable[[str], "bytes | None"] | None = None,
        accent: str = "ACCENT",
        icon: str = "🎵",
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self._title = title or "封面墙"
        self._on_select = on_select
        self._on_activate = on_activate
        self._on_context_menu = on_context_menu
        self._cover_provider = cover_provider
        self._accent = accent if accent in ("ACCENT", "INFO", "WARNING") else "ACCENT"
        #: 标题徽章上的图标，与 accent 一起做"图标 + 颜色"双重编码
        #: （见 `app_common.ColumnIdentity`）。用 emoji 而不是图标文件：
        #: 本项目不带图标资源，打包也不用往 datas 里加东西。
        self._icon = icon

        self._items: list[WallItem] = []
        self._filtered: list[WallItem] = []
        self._page = 0
        self._selected: str | None = None
        # 格子池（v17.0）：按下标复用，下标 = 屏幕上第几个位置。
        # `_tiles` 是"歌名 → 格子"的索引，只覆盖**当前真的画着**的格子。
        self._pool: list[_Tile] = []
        self._tiles: dict[str, _Tile] = {}
        self._pending: dict[str, int] = {}   # 歌名 → 该格当时的代次（防回填错格）
        self._cover_cache: dict[str, "ctk.CTkImage"] = {}
        self._rendered_rows: tuple[int, int] = (-1, -1)
        self._rendered_columns = -1
        self._extra_hidden = 10 ** 9   # 已藏起的格子起点（未算过时用一个不可能的值）
        self._columns = 4

        self._pool_exec = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cover")
        self._results: "queue.Queue[tuple[str, int, object]]" = queue.Queue()
        self._closing = False
        self._repaint_id: str | None = None
        self._debounce_id: str | None = None

        self._font = resolve_font(self.winfo_toplevel())
        self._build()
        self._poll_results()

    # ================= 构建 =================
    def _build(self) -> None:
        self.configure(bg=color("BG_CARD"))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color=T["BG_CARD"])
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=SPACE.M, pady=(SPACE.M, SPACE.XS))
        header.grid_columnconfigure(2, weight=1)
        # 徽章与列表那边（ui_songlist）保持同一套观感：实心圆角底 + 图标，
        # 尺寸/圆角也一致，这样列表视图和封面墙视图切换时"哪列是哪列"不变。
        self._accent_bar = ctk.CTkFrame(header, width=26, height=22,
                                        corner_radius=RADIUS.CHIP, fg_color=T[self._accent])
        self._accent_bar.grid(row=0, column=0, padx=(0, SPACE.S))
        self._accent_bar.grid_propagate(False)
        self._accent_icon = ctk.CTkLabel(
            self._accent_bar, text=self._icon, anchor="center",
            font=(self._font, FONT.TINY, "bold"),
            text_color=T["ACCENT_TEXT"], bg_color=T[self._accent],
        )
        self._accent_icon.place(relx=0.5, rely=0.5, anchor="center")
        self._empty_text = ""
        self._header = ctk.CTkLabel(header, text=self._title, anchor="w",
                                    font=(self._font, FONT.SUBTITLE, "bold"),
                                    text_color=T["TEXT_PRIMARY"], bg_color=T["BG_CARD"])
        self._header.grid(row=0, column=1, sticky="w")
        self._count = ctk.CTkLabel(header, text="0", anchor="center",
                                   font=(self._font, FONT.TINY, "bold"),
                                   text_color=T[self._accent], fg_color=T["BG_ELEVATED"],
                                   corner_radius=RADIUS.PILL, padx=SPACE.S,
                                   bg_color=T["BG_CARD"])
        self._count.grid(row=0, column=3, sticky="e")

        search_row = ctk.CTkFrame(self, fg_color=T["BG_CARD"])
        search_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=SPACE.M, pady=(0, SPACE.XS))
        search_row.grid_columnconfigure(0, weight=1)
        self._search_var = tk.StringVar(master=self, value="")
        ctk.CTkEntry(
            search_row, textvariable=self._search_var, height=30,
            corner_radius=RADIUS.CONTROL, font=(self._font, FONT.SMALL),
            fg_color=T["BG_INPUT"], border_color=T["BORDER_STRONG"],
            placeholder_text=tr("wall.search_hint"),
        ).grid(row=0, column=0, sticky="ew")
        self._search_var.trace_add("write", self._on_search)

        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=color("BG_CARD"))
        self._canvas.grid(row=2, column=0, sticky="nsew", padx=(SPACE.M, 0))
        self._scroll = ctk.CTkScrollbar(self, command=self._canvas.yview)
        self._scroll.grid(row=2, column=1, sticky="ns", padx=(0, SPACE.XS))
        self._canvas.configure(yscrollcommand=self._on_scroll_set)

        self._grid_frame = tk.Frame(self._canvas, bg=color("BG_CARD"))
        self._window = self._canvas.create_window((0, 0), window=self._grid_frame, anchor="nw")
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._grid_frame.bind("<MouseWheel>", self._on_mousewheel)

        self._status = ctk.CTkLabel(self, text="", anchor="w", font=(self._font, FONT.TINY),
                                    text_color=T["TEXT_MUTED"], bg_color=T["BG_CARD"])
        self._status.grid(row=3, column=0, columnspan=2, sticky="ew", padx=SPACE.M,
                          pady=(0, SPACE.XS))

    # ================= 公开接口 =================
    def set_items(self, items: Sequence[WallItem], *, highlight: Iterable[str] = ()) -> None:
        """登记数据并安排一次重画（不阻塞调用方，重画在 idle 时进行）。"""
        highlight_set = set(highlight)
        self._items = [
            WallItem(it.folder_name, it.title, it.author, it.rating,
                     it.highlighted or it.folder_name in highlight_set)
            for it in items
        ]
        self._filtered = list(self._items)
        self._selected = self._selected if any(i.folder_name == self._selected for i in self._items) else None
        self._rendered_rows = (-1, -1)
        self._canvas.yview_moveto(0)
        self._schedule_repaint()

    def set_rating(self, folder_name: str, rating: int) -> None:
        """改星级：更新数据 + 只重绘该格文字（不重排、不动封面）。

        v17.0 修正：原先这里取 `getattr(tile, "rating_label", None)`，
        而 `_Tile` 的文字控件叫 `info` —— `getattr` 永远拿到 `None`，
        于是**评分在封面墙上改了看不见**，且因兜底为 `None` 连异常都不报。
        """
        updated = False
        for container in (self._items, self._filtered):
            for index, item in enumerate(container):
                if item.folder_name == folder_name:
                    container[index] = WallItem(item.folder_name, item.title, item.author,
                                                rating, item.highlighted)
                    updated = True
        if not updated:
            return
        tile = self._tiles.get(folder_name)
        if tile is None:
            return
        try:
            tile.set_rating_text(rating)
        except tk.TclError:
            pass

    def set_empty_hint(self, text: str) -> None:
        """空列表时显示的引导文案（缺失歌单一类需要不同的提示）。

        文案前面会加上本列的图标 —— 三列的标题栏本来就带图标，
        空状态再跟一次，用户扫一眼就知道这块空白属于哪一列
        （m05627「UI 的区分不够明显」）。
        """
        body = text or ""
        self._empty_text = f"{self._icon} {body}" if body else ""
        if not self._filtered:
            self._hide_empty()
            self._show_empty()

    def set_search(self, text: str) -> None:
        self._search_var.set(text or "")

    @property
    def search_var(self) -> tk.StringVar:
        return self._search_var

    def selected(self) -> str | None:
        return self._selected

    def select(self, folder_name: str) -> None:
        if not any(i.folder_name == folder_name for i in self._filtered):
            return
        self._selected = folder_name
        self._scroll_into_view(folder_name)
        self._paint_selection()
        if self._on_select is not None:
            self._on_select(folder_name)

    def visible_names(self) -> list[str]:
        return [i.folder_name for i in self._filtered]

    def items(self) -> list[WallItem]:
        return list(self._items)

    def total_count(self) -> int:
        return len(self._items)

    def refresh_theme(self) -> None:
        """主题切换后重画（Canvas/Frame 是原生 tk 控件，必须手动改色）。"""
        if not self.winfo_exists():
            return
        panel = color("BG_CARD")
        try:
            self.configure(bg=panel)
            self._canvas.configure(bg=panel)
            self._grid_frame.configure(bg=panel)
            self._accent_bar.configure(fg_color=color(self._accent))
            # 徽章里的图标是叠在徽章底上的独立 Label，底色要跟着一起改，
            # 否则主题切换后图标背后留一块旧色（ctk 的内层 tk.Label 不会自动重画）。
            self._accent_icon.configure(
                bg_color=color(self._accent), text_color=color("ACCENT_TEXT")
            )
            # 数量徽标用本列的语义色，与标题徽章呼应。
            self._count.configure(text_color=color(self._accent), fg_color=color("BG_ELEVATED"))
            self._header.configure(text_color=color("TEXT_PRIMARY"))
        except tk.TclError:
            return
        self._cover_cache.clear()
        self._rendered_rows = (-1, -1)
        self._schedule_repaint(immediate=True)

    def destroy(self) -> None:  # noqa: D401
        self._closing = True
        for timer in (self._repaint_id, self._debounce_id):
            if timer:
                try:
                    self.after_cancel(timer)
                except (tk.TclError, ValueError):
                    pass
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        super().destroy()

    # ================= 过滤 / 滚动 =================
    def _on_search(self, *_args: str) -> None:
        if self._debounce_id:
            try:
                self.after_cancel(self._debounce_id)
            except (tk.TclError, ValueError):
                pass
        self._debounce_id = self.after(180, self._apply_filter)

    def _apply_filter(self) -> None:
        self._debounce_id = None
        terms = [t for t in (self._search_var.get() or "").lower().split() if t]
        if terms:
            self._filtered = [
                i for i in self._items
                if all(t in f"{i.folder_name} {i.title} {i.author}".lower() for t in terms)
            ]
        else:
            self._filtered = list(self._items)
        self._rendered_rows = (-1, -1)
        self._canvas.yview_moveto(0)
        self._schedule_repaint(immediate=True)

    def _on_scroll_set(self, first: str, last: str) -> None:
        self._scroll.set(first, last)
        self._schedule_repaint()

    def _on_mousewheel(self, event: tk.Event) -> str:
        self._canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self._schedule_repaint()
        return "break"

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)
        self._grid_frame.configure(width=event.width)
        self._rendered_rows = (-1, -1)
        self._schedule_repaint(immediate=True)

    def _scroll_into_view(self, folder_name: str) -> None:
        index = next((i for i, x in enumerate(self._filtered) if x.folder_name == folder_name), -1)
        if index < 0:
            return
        total_rows = max(1, (len(self._filtered) + self._columns - 1) // self._columns)
        row = index // self._columns
        self._canvas.yview_moveto(max(0.0, (row - 1) / total_rows))
        self._schedule_repaint(immediate=True)

    # ================= 虚拟化渲染 =================
    def _columns_for_width(self) -> int:
        width = max(TILE_W, self._canvas.winfo_width())
        return max(1, width // (TILE_W + GAP))

    def _schedule_repaint(self, immediate: bool = False) -> None:
        if self._closing:
            return
        if self._repaint_id:
            if not immediate:
                return
            try:
                self.after_cancel(self._repaint_id)
            except (tk.TclError, ValueError):
                pass
            self._repaint_id = None
        try:
            self._repaint_id = self.after(0 if immediate else 30, self._repaint)
        except tk.TclError:
            self._repaint_id = None

    def _repaint(self) -> None:
        """把当前可见的那一段歌曲画到格子池上。

        ## 为什么不是"清空重建"（v17.0 的关键改动）

        原先这里是 `_clear_tiles()`（全部 `destroy()`）+ 逐格 `_make_tile()`，
        每次可见范围一行变化就整屏重来。实测（真窗口 1500×860，一屏 45 格）：

        | 做法 | 单次滚动 |
        |---|---|
        | 销毁 45 格 + 重建 45 格（旧） | **1013 ms** |
        | 复用格子、只重排 grid | 0 ms |
        | 复用格子、重排 + **换歌** | **5 ms** |

        1013 ms 就是用户看到的"滚一下卡一秒半"。根因不是建控件贵（单格 4.67 ms），
        而是**同一父容器反复增删子控件**触发的几何传播重算。

        现在按**屏幕位置**取格子（`self._pool[i]`），位置已有格子就只换内容
        （`set_item`），没有才新建。滚动一行 → 45 格中 36 格只是换歌。
        """
        self._repaint_id = None
        if self._closing or not self.winfo_exists():
            return

        self._columns = self._columns_for_width()
        # 列数变了 → 池子的容量基准也变了，重新按新容量补齐
        if self._columns != self._rendered_columns:
            self._extra_hidden = 10 ** 9
        total = len(self._filtered)
        total_rows = (total + self._columns - 1) // self._columns if total else 0

        # 整块内容高度（虚拟高度），让滚动条比例正确
        content_height = max(self._canvas.winfo_height(), total_rows * (TILE_H + GAP) + 40)
        self._canvas.configure(scrollregion=(0, 0, self._canvas.winfo_width(), content_height))

        if not total:
            self._clear_tiles()
            self._show_empty()
            self._count.configure(text="0")
            self._update_status()
            return

        self._hide_empty()

        top = self._canvas.canvasy(0)
        visible_first = max(0, int(top // (TILE_H + GAP)) - 1)
        visible_last = min(total_rows, int((top + self._canvas.winfo_height()) // (TILE_H + GAP)) + 2)
        if (visible_first, visible_last) == self._rendered_rows and self._columns == self._rendered_columns:
            return  # 可见范围没变，避免滚动时反复重建

        rows = list(range(visible_first, visible_last))
        # 一屏需要几个格子：可见行数 × 列数（最后一行可能不满，按实际总数封顶）
        need = 0
        for row in rows:
            need += min(self._columns, max(0, total - row * self._columns))

        # 一次把池子建够（见 `_pool_capacity()`：留出"行跨度会浮动"的余量）。
        # 关键：**滚动路径上一次 `_new_tile()` 都不能有** —— 建一格实测约 10 ms，
        # 滚到底时 `visible_last - visible_first` 会从 5 涨到 6，若等用到才建，
        # 用户就会在那几帧里各吃 ~90 ms（实测滚动 900 帧有 4 个 300~950 ms 长帧，
        # 累计 603 ms 全在 `_new_tile` 上）。预建之后这些长帧一个不剩。
        self._ensure_pool()

        self._hide_extra_tiles(need)

        self._tiles.clear()
        self._pending.clear()

        for slot in range(need):
            row = rows[slot // self._columns]
            column = slot % self._columns
            index = row * self._columns + column
            item = self._filtered[index]

            tile = self._pool[slot]
            grid_row = row - visible_first
            # 只在这一格**确实换位置**时才动 grid。实测 `grid_configure()`
            # 重排已摆好的 45 格只要 0.1 ms，而 `grid_forget()` 一次要 268 ms
            # —— 所以滚动路径上一次 forget 都不能有（见 `_clear_tiles`）。
            if tile.grid_row != grid_row or tile.grid_column != column:
                if tile.grid_row < 0:
                    tile.card.grid(row=grid_row, column=column,
                                   padx=GAP // 2, pady=GAP // 2, sticky="n")
                else:
                    tile.card.grid_configure(row=grid_row, column=column)
                tile.grid_row = grid_row
                tile.grid_column = column

            if tile.item is not None and tile.item.folder_name == item.folder_name:
                # 还是同一首歌（滚动时大部分格子都是这种情况）——什么都不用做
                self._tiles[item.folder_name] = tile
                continue

            render_id = tile.set_item(item)
            self._tiles[item.folder_name] = tile
            cached = self._cover_cache.get(item.folder_name)
            if cached is not None:
                self._apply_cover(tile, cached)
            else:
                tile.show_placeholder()
                self._request_cover(item.folder_name, render_id)

        # 多出来的格子交给 `_hide_extra_tiles()` 处理（池子没变时它直接返回）
        self._hide_extra_tiles(need)

        self._rendered_rows = (visible_first, visible_last)
        self._rendered_columns = self._columns
        self._paint_selection()
        self._count.configure(
            text=f"{total}" if total == len(self._items) else f"{total} / {len(self._items)}"
        )
        self._update_status()

    def _pool_capacity(self) -> int:
        """池子该建多大：够装下"可见行跨度可能出现的最大值"。

        为什么不能只按当前 `need` 建：`_repaint` 里可见行范围是
        `[top//cell - 1, (top+H)//cell + 2]`，**顶部被 clamp 到 0**，
        所以最上面时只有约 5 行，往下滚变成 6 行。实测（canvas 高 734、
        单格 194、9 列）：`need` 在 **45 ↔ 54** 之间来回跳。

        如果按需增量建，这 9 个差额会在滚动过程中被陆续建出来 ——
        实测滚动 900 帧里 `_new_tile` 累计 **603 ms**，正好对应那 4 个长帧。
        预留 2 行余量后池子在**第一次重画**时就建满，之后滚动一次都不建。
        """
        rows_visible = self._canvas.winfo_height() // (TILE_H + GAP) + 3
        return max(self._columns, (rows_visible + 2) * self._columns)

    def _ensure_pool(self) -> None:
        """把池子一次性建到 `_pool_capacity()`（建满之后这里是空转）。"""
        capacity = self._pool_capacity()
        while len(self._pool) < capacity:
            self._new_tile()

    def _hide_extra_tiles(self, need: int) -> None:
        """把这次用不到的格子藏起来（**只在真的需要时**动，绝不每帧都动）。

        这里必须 forget 而不是"留在原地被盖住"：`item = None` 之后封面不会
        清，而池子里靠后 `grid()` 上去的格子**叠在别的格子上面**，于是用户
        会看到一张对不上的封面。

        但 `grid_forget()` 实测 45 格要 268 ms，所以只在**池子刚建满那一次**
        走一遍，之后 `need` 不变就直接返回。
        """
        if need == self._extra_hidden:
            return
        for tile in self._pool[need:]:
            if tile.grid_row >= 0:
                try:
                    tile.card.grid_forget()
                except tk.TclError:
                    pass
                tile.grid_row = -1
                tile.grid_column = -1
            tile.item = None
        self._extra_hidden = need

    def _new_tile(self) -> _Tile:
        """新建一格并放进池子（`grid_row`/`grid_column` 记当前摆在哪）。"""
        tile = _Tile(self._grid_frame, self)
        self._pool.append(tile)
        return tile

    def _clear_tiles(self) -> None:
        """清空整屏（只解绑歌曲，**不销毁、也不 grid_forget**）。

        实测：`grid_forget()` 45 个格子要 **268 ms**，是这个控件里最贵的单个
        Tk 调用。所以这条路只在"真的空列表"时走，平时滚动一次都不会碰到。
        这里保留 forget 是因为空列表时格子上残留的封面会显得莫名其妙。
        """
        for tile in self._pool:
            if tile.item is None:
                continue
            try:
                tile.card.grid_forget()
            except tk.TclError:
                pass
            tile.grid_row = -1
            tile.grid_column = -1
            tile.item = None
        self._tiles.clear()
        self._pending.clear()

    def _show_empty(self) -> None:
        if getattr(self, "_empty_label", None) is not None:
            return
        self._empty_label = ctk.CTkLabel(
            self._grid_frame,
            text=self._empty_text or "这里还没有歌曲\n载入歌单后，封面会显示在这里",
            font=(self._font, FONT.BODY), text_color=T["TEXT_MUTED"], justify="center",
        )
        self._empty_label.pack(pady=60)

    def _hide_empty(self) -> None:
        label = getattr(self, "_empty_label", None)
        if label is not None:
            try:
                label.destroy()
            except tk.TclError:
                pass
            self._empty_label = None

    def _on_click(self, folder_name: str) -> None:
        self._selected = folder_name
        self._paint_selection()
        if self._on_select is not None:
            self._on_select(folder_name)

    def _on_double(self, folder_name: str) -> None:
        if self._on_activate is not None:
            self._on_activate(folder_name)

    def _on_right_click(self, folder_name: str, event: tk.Event) -> str:
        """右键：先选中该格（与列表行为一致），再把菜单交给主程序弹。

        菜单本身不由控件建 —— "打开目录/删除"该落到哪一侧只有主程序知道
        （尤其「缺失」列要映射到真正拥有它的那一侧）。所以这里只上报
        "哪首歌、在屏幕哪个坐标"。
        """
        self._on_click(folder_name)
        if self._on_context_menu is not None:
            self._on_context_menu(folder_name, int(event.x_root), int(event.y_root))
        return "break"

    def _paint_selection(self) -> None:
        """只给**状态变了**的格子改边框。

        实测：无条件给 45 格 `configure(border_color=..., border_width=...)`
        要 **81.5 ms**，而实际每次滚动最多只有 2 格真的变了
        （旧的选中格取消、新的选中格画上）。记住每格当前画的是什么，
        没变就不碰 —— 这一条把选中重绘从 81.5 ms 降到接近 0。
        """
        for name, tile in self._tiles.items():
            want = name == self._selected
            if tile.painted_selected == want:
                continue
            tile.paint_selection(want)
            tile.painted_selected = want

    def _update_status(self) -> None:
        try:
            self._status.configure(text=tr("wall.status", count=len(self._filtered)))
        except tk.TclError:
            pass

    # ================= 封面异步加载（线程池 + 队列） =================
    def _request_cover(self, folder_name: str, render_id: int) -> None:
        """在**线程池**里取出封面字节**并解码+缩放**，主线程只负责贴图。

        ## 为什么解码也要在 worker 里做

        原先 worker 只 `cover_provider()` 取字节，把 `Image.open` +
        `make_thumb`（LANCZOS 缩放）留在 `_poll_results` 里 —— 那是在
        **主线程**上跑的。实测单张代价（57 张真封面，THUMB=128）：

        | 阶段 | 耗时 | 在哪跑 |
        |---|---|---|
        | ① 读文件 | 0.11 ms | worker |
        | ② `Image.open` + `load` + `convert` | **5.61 ms** | ❌ 主线程 |
        | ③ `make_thumb`（LANCZOS + 圆角合成） | **4.20 ms** | ❌ 主线程 |
        | ④ `ctk.CTkImage(...)` | 0.01 ms | 主线程（必须） |

        即 **单张 9.81 ms 里有 9.81 ms 花在主线程上**。216 首约 **2.1 秒**
        的主线程时间，被 50ms/帧的预算摊到约 42 帧里 —— 于是封面墙是
        **一格一格"跳动"地填**（每帧刚贴几张就被下一次 `_poll_results`
        占满），滚动时也会明显顿。

        现在把 ②③ 挪进 worker，主线程只剩 ④（0.01 ms）和 `configure`。
        队列里传的是**已处理好的 PIL 图**，不再是原始字节。

        ## render_id（v17.0 新增）

        格子是复用的：请求发出后用户可能已经滚走，这一格可能已经换成别的歌，
        甚至又滚回来换了第三首。`_poll_results` 回填前比对代次，
        代次不符就丢弃 —— 否则会出现"图贴在错的歌上"（复用才有的新问题）。
        已经在飞的不重复请求（同一首歌 + 同代次只发一次）。

        ## 线程安全

        `make_thumb()` 只用 PIL，不碰任何 Tk 对象，可以在线程里跑。
        **`ctk.CTkImage` 必须在主线程建**，所以它留在 `_poll_results`。
        """
        if self._cover_provider is None or self._pending.get(folder_name) == render_id:
            return
        self._pending[folder_name] = render_id

        def worker() -> None:
            decorated = None
            try:
                data = self._cover_provider(folder_name)  # type: ignore[misc]
                if data:
                    decorated = _decode_thumb(data)
            except Exception as exc:
                logger.debug("准备封面失败 %s: %s", folder_name, exc)
            self._results.put((folder_name, render_id, decorated))

        try:
            self._pool_exec.submit(worker)
        except RuntimeError:
            self._pending.pop(folder_name, None)

    def _poll_results(self) -> None:
        """主线程轮询：把后台**已解码好**的封面贴到格子上。

        这里不该再有解码/缩放 —— 见 `_request_cover()` 的说明。
        每帧仍保留 50ms 预算，是为了"一次性有几百张就绪"时不长时间霸占
        主线程（正常情况单张只剩 0.01ms，几乎碰不到这个上限）。
        """
        if self._closing:
            return
        processed = 0
        deadline = time.monotonic() + 0.05
        while processed < 8 and time.monotonic() < deadline:
            try:
                folder_name, render_id, decorated = self._results.get_nowait()
            except queue.Empty:
                break
            processed += 1
            tile = self._tiles.get(folder_name)
            # 格子已经换成别的歌（或已翻出屏幕）→ 这张图不再需要，丢弃
            if tile is None or tile.item is None or tile_item_id(tile) != render_id:
                continue
            self._pending.pop(folder_name, None)
            if decorated is None:
                continue
            cached = self._cover_cache.get(folder_name)
            if cached is not None:
                self._apply_cover(tile, cached)
                continue
            try:
                widget_image = ctk.CTkImage(light_image=decorated, dark_image=decorated,
                                            size=decorated.size)
            except Exception as exc:
                logger.debug("包装封面失败 %s: %s", folder_name, exc)
                continue
            if len(self._cover_cache) > CACHE_LIMIT:
                self._cover_cache.clear()
            self._cover_cache[folder_name] = widget_image
            self._apply_cover(tile, widget_image)
        try:
            self.after(POLL_MS, self._poll_results)
        except tk.TclError:
            pass

    def _apply_cover(self, tile: _Tile, image: "ctk.CTkImage") -> None:
        """把成品图贴到格子上。只碰这一格，不查 `_tiles` —— 调用方已确认代次。"""
        if not tile.card.winfo_exists():
            return
        try:
            tile.holder.configure(image=image, text="")
            tile.holder.image = image  # 保持引用，防止被 GC
        except tk.TclError:
            pass


def tile_item_id(tile: "_Tile") -> int:
    """取格子当前的代次（封面异步回填前用它核对"图还该不该贴在这一格"）。

    单独一个函数是为了让类型检查器闭嘴（`_render_id` 是 `__slots__` 里的私有名）。
    """
    return tile._render_id


def _decode_thumb(data: bytes):
    """把封面字节解码成 `THUMB` 尺寸的成品图。**纯计算，不碰 Tk。**

    拆成独立函数是为了能在**线程池**里调用（`_request_cover` 的 worker），
    这样主线程就只剩 `ctk.CTkImage()` 包装（实测 0.01 ms）。
    解码 + LANCZOS 缩放是 9.8 ms 的大头，绝不能留在 UI 线程上。
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return make_thumb(image.convert("RGB"), THUMB)


def make_thumb(image, size: int):
    """裁剪为正方形 + 圆角 + 底色合成（与详情栏封面同一套视觉语言）。"""
    from PIL import Image, ImageDraw, ImageOps

    square = ImageOps.fit(image, (size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=8, fill=255)
    layer = square.convert("RGBA")
    layer.putalpha(mask)
    background = Image.new("RGB", (size, size), (24, 26, 31))
    background.paste(layer, mask=layer.split()[3])
    return background


def format_tile_text(item: WallItem, rating: int | None = None) -> str:
    """格子下方那一行文字：`★★★☆☆  歌名`。

    抽成模块级函数是因为**有两个调用点**（`_Tile.set_item()` 换歌时、
    `_Tile.set_rating_text()` 改星级时）。原先两处各写一遍格式化，
    结果 `set_rating()` 那份写错了控件名却没人发现 —— 只有一份实现
    才不会再次分叉。
    """
    stars = _stars(item.rating if rating is None else rating)
    return f"{stars}  {_ellipsis(item.title or item.folder_name, 20)}"


def _stars(rating: int) -> str:
    rating = max(0, min(5, int(rating or 0)))
    return "★" * rating + "☆" * (5 - rating)


def _ellipsis(text: str, limit: int) -> str:
    """按"显示宽度"截断：中日韩字符按 2 个单位算，拉丁字符按 1 个。

    为什么不用固定字符数：`1000 Light Years Away`（21 个拉丁字符）与
    `中文歌曲·夜航星`（8 个字符）在同样宽度下能显示的内容差一倍，
    固定 13 个字符会把前者截成看不懂的 `1000 Light Y…`。
    """
    text = (text or "").strip()
    width = 0
    out: list[str] = []
    for char in text:
        weight = 2 if _is_wide(char) else 1
        if width + weight > limit:
            return "".join(out) + "…"
        width += weight
        out.append(char)
    return text


def _is_wide(char: str) -> bool:
    """粗略判断"全角"字符（CJK、假名、全角标点等）。"""
    code = ord(char)
    return (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE6F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
    )
