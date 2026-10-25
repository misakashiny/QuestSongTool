"""歌曲列表控件（ttk.Treeview 版）—— 替换原版"一首歌两个控件"的实现。

为什么这么做（都是本机实测出来的数字，不是风格偏好）：

* **行是数据，不是控件。** 原版 `SelectableListBox` 是 CTkScrollableFrame，每首歌一个
  CTkFrame + 一个 CTkLabel：200 首 = 801 个 Tk 控件 / 1.54 s，1000 首 = 4001 个 / 6.68 s，
  2000 首 = 8001 个 / 15.48 s，光把界面建出来就已经卡死。这里换成一个 `ttk.Treeview`，
  一屏只 insert 当前页（默认 100 行），控件总数是常数，跟歌曲数量无关。
* **搜索不再同步重建。** 原版搜索框的 trace 直接触发全量重画：1000 首连打 5 个字符
  实测 29.82 s（≈6 s/键，期间界面完全冻结）。这里用 `trace_add` + `SEARCH_DEBOUNCE_MS`
  防抖，打字期间只取消/重排一个 after 回调，停手后才过滤一次，并且只重画当前页。
* **高亮只认文件夹名，不认下标。** 原版用"未排序列表的下标"去索引"已排序列表"，
  实测排序后高亮错位：用户看到的是 A，按删除删掉的是 B。这里的 `_highlight` 是
  `set[str]`（文件夹名），排序/分页/过滤/重画都不可能让它指错行。
* **Delete 只绑在 Treeview 上。** 原版把 `<Delete>` 绑到 root，在搜索框里按删除键会
  直接删歌。本模块所有键盘绑定都挂在 Treeview 实例上，搜索框里的任何按键都到不了这里
  （tkinter 的 Entry 有自己的一套绑定，不会冒泡到 Treeview）。
* **不 import fsapi / database。** 本模块只依赖 `constants` 与 `songs`（两者都不依赖 UI），
  从根上避免循环引用；"这首歌能不能在资源管理器里打开"由外部通过 `open_path_provider`
  注入，控件本身不认识设备/本地盘的概念。

主程序只需要用这几个方法：`set_items` / `append_items` / `set_highlight` /
`clear_highlight` / `set_rating` / `remove_item` / `select` / `refresh_theme`。
"""

from __future__ import annotations

import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Iterable, Mapping, Sequence

import customtkinter as ctk

from .constants import SEARCH_DEBOUNCE_MS, resolve_font
from .logging_setup import get_logger
from .theme import FONT, RADIUS, SIZE, SPACE, T, color
from .messages import tr
from .ui_songrows import SongRows
from .ui_icons import LineIcon, IconButton

logger = get_logger("songlist")

#: 输入框高度（与主界面其他输入框保持一致）
SIZE_INPUT_H = SIZE.INPUT_H

# ---------------- 视图常量 ----------------

# 每页行数可选项（默认 100：一屏能看完，单页插入又足够快）
_PAGE_SIZE_CHOICES = (50, 100, 200, 500)
_DEFAULT_PAGE_SIZE = 100

# 底部提示文字自动清除时间
_STATUS_CLEAR_MS = 2000

# ttk 样式名。多个实例共用同一份样式（颜色只取决于全局外观模式，不存在冲突）
_STYLE_TREE = "SongList.Treeview"
_STYLE_HEADING = f"{_STYLE_TREE}.Heading"

# 行 tag：一行只挂一个 tag，避免 ttk "多个 tag 谁覆盖谁"的坑
_TAG_ROW = "row"  # 普通行
_TAG_ALT = "alt"  # 斑马纹行（偶数行淡底，长列表更好扫读）
_TAG_SEL = "sel"  # 选中行
_TAG_HL = "hl"  # 对比结果高亮行
_TAG_HL_SEL = "hlsel"  # 既高亮又被选中
_TAG_HOVER = "hover"  # 鼠标悬停行

#: 标题左侧的语义色竖条可选值（由主程序传入，让三个列表一眼可分辨）
ACCENT_ROLES = ("ACCENT", "INFO", "WARNING", "SUCCESS", "DANGER", "NEUTRAL")

#: 列表上方的筛选档位（v17.7）。
#:
#: **过滤动作发生在主程序，不在控件里**：`matched` / `missing` 要拿"另一侧"的歌单
#: 来比较（本地列表的"缺失"= 不在 Quest 上），列表控件看不到另一半数据。
#: 控件只负责显示档位与计数，真正裁剪由 `app._apply_song_filter()` 完成。
SONG_FILTERS = ("all", "matched", "missing")

# Treeview 行字号：10 号字能让 5 颗 ★ 完整放进 62px 宽的评分列
_ROW_FONT_SIZE = 11

# 排序下拉的文案：**不能在 import 期就调 tr()** —— 那样切语言后不会变
# （和 ui_wizard 的 DEFAULT_TEXT 是同一类坑）。存 key，用到时再取。
_SORT_LABEL_KEYS: dict[str, str] = {
    "default": "sort.default",
    "name": "sort.name",
    "rating": "sort.rating",
}


@dataclass(frozen=True)
class SongItem:
    """列表里的一首歌（只带 UI 需要的字段，与数据库/FS 层解耦）。

    `local_path` 只有"歌在本地磁盘上"时才有值；在 Quest 设备（MTP/adb）上的歌留空，
    于是右键菜单里的"在资源管理器中打开"会自动消失——这正是我们要的行为。
    """

    folder_name: str
    title: str = ""
    author: str = ""
    mtime: float = 0.0
    local_path: str = ""
    cover_path: str = ""

    @property
    def key(self) -> str:
        """列表内的唯一键：文件夹名（高亮/选中/删除都用它，绝不用下标）。"""
        return self.folder_name


def _decode_thumbnail(path: str, results: queue.Queue, folder: str = "", key: str = "") -> None:
    """Read local artwork and optional result-row size without touching Tk/devices."""
    image, size = None, None
    try:
        from PIL import Image, ImageOps
        if path:
            with Image.open(path) as source:
                image = ImageOps.fit(source.convert("RGB"), (36, 36))
    except (OSError, ValueError):
        pass
    if folder:
        try:
            size = sum(item.stat().st_size for item in Path(folder).rglob("*") if item.is_file())
        except OSError:
            pass
    results.put((path, image, key, size))


class SongListWidget(tk.Frame):
    """带搜索 / 排序 / 分页 / 高亮的歌曲列表。

    设计要点：
    * 全量数据在 `self._items`（插入顺序），另外维护 `_filtered`（搜索后）与
      `_sorted`（排序后）两个"视图"，Treeview 里永远只有当前页那几十~几百行。
    * 默认排序 + 无搜索时 `_filtered` / `_sorted` 直接**引用** `_items`（不复制），
      所以流式 `append_items` 是 O(1)，3000 首也不会每次拷贝列表。
    * 所有用户回调（on_select / on_activate / on_delete …）都在 UI 线程同步调用，
      回调里再操作本控件是安全的（内部有重入保护）。
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str = "",
        rich_rows: bool = False,
        result_column: bool = False,
        width: int = 320,
        height: int = 480,
        on_select: Callable[[str], None] | None = None,
        on_select_multi: Callable[[list[str]], None] | None = None,
        on_activate: Callable[[str], None] | None = None,
        on_delete: Callable[[str], None] | None = None,
        on_sort_changed: Callable[[str, bool], None] | None = None,
        on_open_in_explorer: Callable[[str], None] | None = None,
        open_path_provider: Callable[[str], str | None] | None = None,
        on_columns_changed: Callable[[dict[str, int]], None] | None = None,
        on_filter_changed: Callable[[str], None] | None = None,
        column_widths: Mapping[str, int] | None = None,
        accent: str = "ACCENT",
        icon: str = "🎵",
        page_size: int = _DEFAULT_PAGE_SIZE,
        **kwargs,
    ) -> None:
        """参数说明（全部回调都传文件夹名，不传下标）：

        title               标题栏文字，最终显示成 "📂 {title} (1234)"
        on_select(name)     选中变化时触发
        on_activate(name)   双击触发
        on_delete(name)     按 Delete / 右键删除时触发（是否弹确认框由回调自己决定）
        on_sort_changed(key, reverse)  排序变化，key ∈ {"default","name","rating"}
        on_open_in_explorer(name)      右键"在资源管理器中打开"
        open_path_provider(name)       返回该歌的本地路径，None = 不能打开（设备上的歌）
        on_filter_changed(value)       筛选标签变化，value ∈ SONG_FILTERS
        accent              标题徽章的语义色名（见 ACCENT_ROLES），用来区分三个列表
        icon                标题徽章上的图标，与 accent 一起做"图标 + 颜色"双重编码
        page_size           每页行数，不在 50/100/200/500 里也会照用并加进下拉框
        """
        # 允许调用方覆盖底色，但默认跟随主题（ctk 的透明控件会读 master 的 bg）
        kwargs.pop("bg", None)
        super().__init__(master, width=width, height=height, **kwargs)

        # ---------------- 数据状态 ----------------
        self._search_hint_label = None
        self._row_canvas: SongRows | None = None
        self._result_column = result_column
        self._rich_rows = rich_rows
        self._style_tree = "SongCard.Treeview" if rich_rows else _STYLE_TREE
        self._presence_missing: set[str] | None = None
        self._row_sizes: dict[str, int] = {}
        self._thumb_results: queue.Queue = queue.Queue()
        self._thumb_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="song-thumb") if rich_rows else None
        self._thumb_cache: dict[str, object] = {}
        self._thumb_pending: set[str] = set()
        self._thumb_job: str | None = None
        self._items: list[SongItem] = []
        self._title_map: dict[str, str] = {}
        self._rating_map: dict[str, int] = {}
        self._filtered: list[SongItem] = []
        self._sorted: list[SongItem] = []  # 见 _recompute：无搜索时与 _items 同一个对象
        self._terms: list[str] = []
        self._highlight: set[str] = set()
        self._sort_key = "default"
        self._reverse = False
        self._page = 0
        self._page_size = max(1, int(page_size))
        #: 当前筛选档位（见 SONG_FILTERS）。
        #: **筛选不是在这里算的** —— "已匹配 / 缺失"要拿另一侧的曲库来比，而列表
        #: 控件不知道也不该知道另一半数据。所以它只是个"当前选中哪一档"的状态，
        #: 真正的裁剪由主程序在 `_apply_song_filter()` 里做。
        #: 这里只负责：① 显示正确的选中档；② 被拉高的 `_filter_counts` 计数。
        self._filter = SONG_FILTERS[0]
        self._filter_counts: dict[str, int] = {}
        #: 档位 -> 按钮，用来切选中样式
        self._filter_buttons: dict[str, ctk.CTkButton] = {}

        # ---------------- 行 <-> 文件夹名 映射（只含当前页） ----------------
        self._iid_name: dict[str, str] = {}
        self._name_iid: dict[str, str] = {}
        self._visible_iids: list[str] = []
        self._hl_rows: set[str] = set()  # 当前挂着高亮 tag 的行 id
        self._sel_iid = ""  # 主选中行 id（focus，用于 hlsel 样式与详情栏）
        self._sel_rows: set[str] = set()  # 当前所有选中行 id（支持多选）
        self._last_selected: str | None = None  # 最后一次真正通知出去的歌曲
        #: **跨页选择集**（B1）。Treeview 只持有当前页的行，翻页就会丢选择，
        #: 所以"哪些歌被选中"必须由我们自己按文件夹名维护；本页的 Treeview 选中状态
        #: 只是它在当前页的投影（见 `_sync_selected_set` / `_restore_selection`）。
        self._selected: set[str] = set()

        # ---------------- 回调 / 定时器 ----------------
        self._on_select = on_select
        self._on_select_multi = on_select_multi
        self._on_activate = on_activate
        self._on_delete = on_delete
        self._on_sort_changed = on_sort_changed
        self._on_open_in_explorer = on_open_in_explorer
        self._open_path_provider = open_path_provider
        self._on_columns_changed = on_columns_changed
        self._on_filter_changed = on_filter_changed
        self._pending_widths = dict(column_widths) if column_widths else None
        self._hover_iid = ""
        self._last_widths: dict[str, int] = {}
        self._debounce_id: str | None = None
        self._status_id: str | None = None
        self._suppress_select = False  # 重画期间屏蔽 <<TreeviewSelect>>
        self._menu: tk.Menu | None = None

        self._font_family = resolve_font(self.winfo_toplevel())
        self._title_text = title or "歌曲"
        self._accent_role = accent if accent in ACCENT_ROLES else "ACCENT"
        #: 标题徽章上的图标（m05627）：由调用方按列身份传进来，
        #: 与 accent 一起构成"图标 + 颜色"双重编码（色盲用户靠图标也能分列）。
        self._icon = icon
        self._empty_hint = "还没有数据"
        self._empty_visible = False

        self.configure(bg=self._panel_bg())
        self._build_widgets()
        if self._pending_widths:
            self.set_column_widths(self._pending_widths)
        self._last_widths = self.get_column_widths()
        self._apply_ttk_style()
        self._apply_panel_colors()
        self._update_sort_buttons()
        self._update_labels()
        self._update_empty_state()
        if self._rich_rows:
            self._redraw_rows()
            self._thumb_job = self.after(100, self._pump_thumbnails)

    # ================= 控件构建（只在这里创建控件，数量恒定） =================

    def _build_widgets(self) -> None:
        """建好标题栏 / 搜索框 / 排序工具栏 / Treeview / 翻页条 / 状态栏。

        整个控件总共几十个 Tk widget，与歌曲数量无关——这是性能修复的关键。
        """
        family = self._font_family
        panel_bg = self._panel_bg()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)  # 只有表格那一行跟着窗口拉伸

        # 标题栏：左侧语义色竖条 + 标题 + 数量徽标（三个列表一眼可区分）
        # 注意 fg_color 不能写 "transparent"：ctk 对透明 Frame 的处理是把主题里
        # CTkFrame 的默认底色（#3a3a3a）画上，外观切换后就会在标题上下露出深色带
        # （实测洪水填充定位到的深色矩形正是这里，宽 52px 与标题同宽）。
        header = ctk.CTkFrame(self, fg_color=T["BG_CARD"])
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=SPACE.M, pady=(SPACE.M, SPACE.XS))
        header.grid_columnconfigure(2, weight=1)

        # 竖条换成**实心胶囊 + 列图标**（m05627「UI 的区分不够明显」）：
        # 原来只有一根 3px 的细线，扫一眼分不出哪列是哪列；
        # 现在是一枚带底色的圆角徽章，图标 + 颜色双重编码（色盲用户也能靠图标区分）。
        self._accent_bar = ctk.CTkFrame(
            header, width=26, height=22, corner_radius=RADIUS.CHIP,
            fg_color=T[self._accent_role],
        )
        self._accent_bar.grid(row=0, column=0, padx=(0, SPACE.S))
        self._accent_bar.grid_propagate(False)
        self._accent_icon = ctk.CTkLabel(
            self._accent_bar, text=self._icon, anchor="center",
            font=(family, FONT.TINY, "bold"),
            text_color=T["ACCENT_TEXT"], bg_color=T[self._accent_role],
        )
        self._accent_icon.place(relx=0.5, rely=0.5, anchor="center")
        if self._rich_rows:
            self._accent_bar.configure(width=32, height=32, fg_color=T["BG_INPUT"])
            self._accent_icon.place_forget()
            kind = "local" if self._accent_role == "ACCENT" else "quest" if self._accent_role == "INFO" else "missing"
            LineIcon(self._accent_bar, kind, self._accent_role).place(relx=.5, rely=.5, anchor="center")

        self._header = ctk.CTkLabel(
            header, text="", anchor="w", font=(family, FONT.SUBTITLE, "bold"),
            text_color=T["TEXT_PRIMARY"],
            # bg_color 必须显式给：ctk 的透明标签在"深色→浅色"切换后，内层 tk.Label 会残留
            # 旧的深色底，画面上就是标题栏一块黑块（实测用洪水填充定位到的正是这个内层 Label）。
            bg_color=T["BG_CARD"],
        )
        self._header.grid(row=0, column=1, sticky="w")

        self._count_badge = ctk.CTkLabel(
            header, text="0", anchor="center", font=(family, FONT.TITLE, "bold"),
            # 数量徽标用**本列的语义色浅底 + 语义色文字**，不再是统一的灰底 ——
            # 这是"这列有多少首"最显眼的地方，也是三列区分度最强的一处。
            text_color=T[self._accent_role], fg_color=T["BG_ELEVATED"],
            corner_radius=RADIUS.PILL, padx=SPACE.S,
            bg_color=T["BG_CARD"],
        )
        self._count_badge.grid(row=0, column=2, sticky="w", padx=SPACE.L)

        self._search_var = tk.StringVar(master=self, value="")
        self._search = ctk.CTkEntry(
            self,
            textvariable=self._search_var,
            height=SIZE_INPUT_H,
            corner_radius=RADIUS.CONTROL,
            font=(family, FONT.SMALL),
            fg_color=T["BG_INPUT"],
            border_color=T["BORDER_STRONG"],
            placeholder_text=tr("songlist.search_hint"),
        )
        self._search.grid(row=2, column=0, columnspan=2, sticky="ew", padx=SPACE.M, pady=(0, SPACE.XS))
        # trace_add 而不是 3.13 起废弃的 trace("w", ...)
        self._search_var.trace_add("write", self._on_search_changed)
        if self._rich_rows:
            # CTkEntry intentionally disables its native placeholder with a textvariable.
            # A visual hint leaves the actual search variable empty and searchable.
            self._search_hint_label = ctk.CTkLabel(
                self._search, text=tr("layout.search", title=self._title_text),
                height=20, font=(family, FONT.TINY), text_color=T["TEXT_MUTED"],
                fg_color=T["BG_INPUT"], bg_color=T["BG_INPUT"], anchor="w",
            )
            self._search_hint_label.bind("<Button-1>", lambda _event: self._search.focus_set())
            self._search.bind("<FocusIn>", lambda _event: self._search_hint_label.place_forget(), add="+")
            self._search.bind("<FocusOut>", lambda _event: self._refresh_search_hint(), add="+")
            self._refresh_search_hint()

        # 筛选档位一行（v17.7）：全部 / 已匹配 / 缺失。
        # **三个列表各有一份**，而不是全局一个开关 —— 用户想看的往往是"本地列表里
        # 哪些已经传过去了"，而不是把三个列表一起裁掉。计数由主程序算好灌进来
        # （见 `set_filter_counts`），控件自己不去猜"另一侧有什么"。
        self._filter_row = ctk.CTkFrame(self, corner_radius=RADIUS.CHIP, fg_color=T["BG_CARD"])
        self._filter_row.grid(row=1, column=0, columnspan=2, sticky="ew",
                              padx=SPACE.M, pady=(0, SPACE.XS))
        for column, value in enumerate(SONG_FILTERS):
            chip = ctk.CTkButton(
                self._filter_row,
                text=tr(f"filter.{value}"),
                width=58,
                height=24,
                corner_radius=RADIUS.CHIP,
                font=(family, FONT.TINY),
                border_width=1,
                command=lambda v=value: self.set_filter(v, notify=True),
            )
            chip.grid(row=0, column=column, padx=2, pady=4, sticky="ew")
            self._filter_row.grid_columnconfigure(column, weight=1)
            self._filter_buttons[value] = chip
        self._filter_row.grid_columnconfigure(len(SONG_FILTERS), weight=1)
        self._filter_count = ctk.CTkLabel(
            self._filter_row, text="", anchor="e", font=(family, FONT.TINY),
            text_color=T["TEXT_MUTED"], bg_color=T["BG_CARD"],
        )
        self._filter_count.grid(row=0, column=len(SONG_FILTERS) + 1, sticky="e",
                                padx=(0, SPACE.XS))
        self._result_selection = None
        if self._rich_rows and self._result_column:
            for value in ("matched", "missing"):
                self._filter_buttons[value].grid_remove()
            self._filter_count.grid_remove()
            self._result_selection = ctk.CTkLabel(self._filter_row, text=tr("layout.selected", count=0),
                                                 font=(family, FONT.TINY), text_color=T["TEXT_SECONDARY"])
            self._result_selection.grid(row=0, column=1, columnspan=4, sticky="e", padx=SPACE.L)
        self._paint_filter_chips()

        # 表格与空状态提示共用同一个格子（空状态用 place 覆盖在表格上）
        self._table_holder = ctk.CTkFrame(self, fg_color=T["BG_CARD"])
        self._table_holder.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=(SPACE.M, 0), pady=(0, SPACE.XS))
        self._table_holder.grid_columnconfigure(0, weight=1)
        self._table_holder.grid_rowconfigure(0, weight=1)

        self._toolbar = ctk.CTkFrame(self, corner_radius=RADIUS.CHIP, fg_color=T["BG_CARD"])
        self._toolbar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=SPACE.M, pady=(0, SPACE.XS))
        self._sort_buttons: dict[str, ctk.CTkButton] = {}
        for column, key in enumerate(("default", "name", "rating")):
            button = ctk.CTkButton(
                self._toolbar,
                text=tr(_SORT_LABEL_KEYS[key]),
                width=54,
                height=24,
                corner_radius=RADIUS.CHIP,
                font=(family, FONT.TINY),
                border_width=1,
                command=lambda sort_key=key: self._on_sort_clicked(sort_key),
            )
            button.grid(row=0, column=column, padx=(4 if column == 0 else 2, 0), pady=4)
            # 右键 = 直接降序（CTkButton.bind 内部绑到画布/文字标签上，不会覆盖左键）
            button.bind("<Button-3>", lambda _event, sort_key=key: self._on_sort_clicked(
                sort_key, force_reverse=True
            ))
            self._sort_buttons[key] = button
        self._toolbar.grid_columnconfigure(3, weight=1)  # 弹簧：把页大小选择器顶到右边
        sizes = sorted(set(_PAGE_SIZE_CHOICES) | {self._page_size})
        self._size_menu = ctk.CTkOptionMenu(
            self._toolbar,
            values=[str(size) for size in sizes],
            width=62,
            height=24,
            corner_radius=RADIUS.CHIP,
            font=(family, FONT.TINY),
            dropdown_font=(family, FONT.SMALL),
            command=self._on_page_size_changed,
        )
        self._size_menu.set(str(self._page_size))  # set() 不会触发 command
        self._size_menu.grid(row=0, column=4, padx=SPACE.XS, pady=SPACE.XS)

        # 列只配置一次：列宽/对齐/展示列都与"当前有几首歌"无关
        # height=1 很关键：它只是"请求高度"，设成 12 行会让控件的请求高度超出
        # 可用垂直空间，grid 就会让表格压到翻页条上（实测重叠 269px）。
        # 设成 1 行后请求高度最小，表格行带 weight 会自己占满剩余空间。
        self._tree = ttk.Treeview(
            self._table_holder,
            columns=("rating", "title"),
            show="tree headings",
            # extended = 支持 Ctrl/Shift 多选（批量复制/删除的前提）
            selectmode="extended",
            style=self._style_tree,
            height=1,
            takefocus=True,
        )
        self._tree.heading("#0", text=tr("songlist.col.folder"), anchor="w")
        self._tree.heading("rating", text=tr("songlist.col.rating"), anchor="center")
        self._tree.heading("title", text=tr("songlist.col.title"), anchor="w")
        # 文件夹名最长，给它伸缩权；minwidth 保证窄窗口下也不会缩成一条缝。
        # 注意：这些宽度直接决定控件的"最小请求宽度"，而三个列表是并排的，
        # 三者相加不能超过窗口 —— 所以这里刻意压紧（实测 3×471px 会把窗口撑到 1885px）。
        # 全部 stretch=False：让用户拖拽调宽能"拉得动"（stretch=True 的列会被自动拉伸覆盖）。
        self._tree.column("#0", width=176, minwidth=60, anchor="w", stretch=False)
        self._tree.column("rating", width=58, minwidth=44, anchor="center", stretch=False)
        self._tree.column("title", width=118, minwidth=60, anchor="w", stretch=False)
        # displaycolumns 只列**数据列**（#0 是 tree 列，写进去 Tk 会报 Invalid column index）
        self._tree.configure(displaycolumns=("rating", "title"))
        if self._rich_rows:
            self._tree.heading("#0", text=tr("songlist.col.song"))
            self._tree.heading("title", text=tr("songlist.col.status"))
            self._tree.column("#0", width=200, minwidth=100, stretch=True)
            self._tree.column("rating", width=70, minwidth=60, stretch=False)
            self._tree.column("title", width=64, minwidth=50, stretch=False)
        self._tree.grid(row=0, column=0, sticky="nsew")

        self._scroll = ttk.Scrollbar(self._table_holder, orient="vertical", command=self._tree.yview)
        self._scroll.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=self._scroll.set)
        if self._rich_rows:
            self._row_canvas = SongRows(self._table_holder, self)
            self._row_canvas.grid(row=0, column=0, sticky="nsew")
            self._scroll.configure(command=self._row_canvas.scroll)
            self._tree.configure(yscrollcommand=self._on_scroll_position)
            # Search, compact sorting and per-column view buttons share one row.
            self._search.grid_configure(row=2, column=0, columnspan=1)
            self.grid_columnconfigure(1, weight=0)
            self._search.configure(width=100)
            self._toolbar.grid_configure(row=2, column=1, columnspan=1, padx=(0, SPACE.M))
            for button in self._sort_buttons.values():
                button.grid_remove()
            self._size_menu.grid_remove()
            self._sort_menu = ctk.CTkOptionMenu(
                self._toolbar, values=[tr(_SORT_LABEL_KEYS[k]) for k in _SORT_LABEL_KEYS],
                width=76, height=30, font=(family, FONT.TINY),
                fg_color=T["BG_INPUT"], text_color=T["TEXT_PRIMARY"],
                button_color=T["BG_INPUT"], button_hover_color=T["BG_HOVER"],
                command=self._on_compact_sort,
            )
            self._sort_menu.grid(row=0, column=0, padx=2)
            self._view_callback = None
            for index, (symbol, mode) in enumerate((("☷", "list"), ("▦", "grid")), start=1):
                IconButton(self._toolbar, text=symbol, width=27, height=30,
                             fg_color=T["ACCENT"] if mode == "list" else T["BG_INPUT"],
                             text_color=T["ACCENT_TEXT"] if mode == "list" else T["TEXT_SECONDARY"],
                             command=lambda value=mode: self._request_view(value)).grid(row=0, column=index, padx=2)


        # 空状态提示（没有歌曲/还没对比时显示，避免一个空白表格）
        self._empty_label = ctk.CTkLabel(
            self._table_holder, text="", anchor="center", justify="center",
            font=(family, FONT.BODY), text_color=T["TEXT_MUTED"], wraplength=260,
        )

        # 键盘/鼠标绑定**只挂在 Treeview 上**：搜索框里按 Delete 不会删歌
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Button-3>", self._on_right_click)
        self._tree.bind("<Up>", lambda _event: self._move_selection(-1))
        self._tree.bind("<Down>", lambda _event: self._move_selection(1))
        self._tree.bind("<Return>", self._on_key_activate)
        self._tree.bind("<space>", self._on_key_activate)
        self._tree.bind("<Delete>", self._on_key_delete)
        self._tree.bind("<Control-c>", self._on_key_copy)
        self._tree.bind("<Control-C>", self._on_key_copy)
        self._tree.bind("<Control-a>", self._on_key_select_all)
        self._tree.bind("<Control-A>", self._on_key_select_all)
        # Ctrl+Shift+A 全选所有页（Tk 里 Control-Shift-A 比 Control-A 更具体，会优先命中）
        self._tree.bind("<Control-Shift-A>", self._on_key_select_all_pages)
        self._tree.bind("<Control-Shift-a>", self._on_key_select_all_pages)
        self._tree.bind("<Escape>", self._on_key_clear_selection)
        # 列宽拖拽：交给主程序做持久化
        self._tree.bind("<ButtonRelease-1>", self._on_column_release, add="+")
        # 普通单击要清空**整个**跨页选择（Ctrl/Shift 单击才是追加）。
        # Treeview 自己只按当前页处理选择，所以"其它页也一起清掉"必须我们来做 ——
        # 否则用户跨页选了 50 首之后随手点一下，别的页那 40 首还留着，
        # 再按 Delete 就会删掉他以为已经取消的东西。
        self._tree.bind("<Button-1>", self._on_tree_click, add="+")
        # 行悬停高亮
        self._tree.bind("<Motion>", self._on_tree_motion, add="+")
        self._tree.bind("<Leave>", lambda _e: self._set_hover(""), add="+")

        # 行号必须与 _build_widgets 顶部的 grid_rowconfigure 一致：
        # 0 标题 / 1 搜索 / 2 工具栏 / 3 表格(伸缩) / 4 翻页 / 5 提示
        # （曾经表格和翻页都写在 row=4，导致表格压住翻页条，实测重叠 269px）
        self._pager = ctk.CTkFrame(self, corner_radius=6, fg_color=T["BG_CARD"])
        self._pager.grid(row=5, column=0, columnspan=2, sticky="ew", padx=SPACE.M, pady=(0, SPACE.XXS))
        self._first_btn = self._pager_button(self._pager, 0, "⏮", self._goto_first)
        self._prev_btn = self._pager_button(self._pager, 1, "◀", self._goto_prev)
        self._page_label = ctk.CTkLabel(
            self._pager, text="", anchor="center", font=(family, FONT.TINY),
            text_color=T["TEXT_SECONDARY"], bg_color=T["BG_CARD"],
        )
        self._page_label.grid(row=0, column=2, sticky="ew", padx=SPACE.XS, pady=SPACE.XS)
        self._pager.grid_columnconfigure(2, weight=1)
        self._next_btn = self._pager_button(self._pager, 3, "▶", self._goto_next)
        self._last_btn = self._pager_button(self._pager, 4, "⏭", self._goto_last)

        self._status = ctk.CTkLabel(
            self, text="", anchor="w", font=(family, FONT.TINY), text_color=T["TEXT_MUTED"],
            bg_color=T["BG_CARD"],
        )
        self._status.grid(row=6, column=0, columnspan=2, sticky="ew", padx=SPACE.M, pady=(0, SPACE.XS))

    def _pager_button(
        self, parent: ctk.CTkFrame, column: int, text: str, command: Callable[[], None]
    ) -> ctk.CTkButton:
        """翻页小按钮（宽度很小，320px 宽的侧栏也放得下 4 个 + 页码）。"""
        button = ctk.CTkButton(
            parent,
            text=text,
            width=26,
            height=22,
            corner_radius=RADIUS.CHIP,
            font=(self._font_family, FONT.TINY),
            fg_color=T["BG_ELEVATED"],
            hover_color=T["BG_HOVER"],
            text_color=T["TEXT_SECONDARY"],
            command=command,
        )
        button.grid(row=0, column=column, padx=SPACE.XXS, pady=SPACE.XS)
        return button

    def _on_scroll_position(self, first, last) -> None:
        self._scroll.set(first, last)
        if self._row_canvas is not None:
            self._row_canvas.redraw()

    def _request_view(self, mode: str) -> None:
        if self._view_callback is not None:
            self._view_callback(mode)

    def _on_compact_sort(self, label: str) -> None:
        key = next((key for key, value in _SORT_LABEL_KEYS.items() if tr(value) == label), "default")
        self._on_sort_clicked(key)

    def _redraw_rows(self) -> None:
        if self._row_canvas is not None:
            self._row_canvas.redraw()

    def _update_numbered_pager(self) -> None:
        if not self._rich_rows:
            return
        if not hasattr(self, "_numbered_pager"):
            self._numbered_pager = ctk.CTkFrame(self, fg_color=T["BG_CARD"])
            self._numbered_pager.grid(row=5, column=0, columnspan=2, sticky="ew", padx=SPACE.M, pady=SPACE.S)
            self._pager.grid_remove()
        for child in self._numbered_pager.winfo_children():
            child.destroy()
        pages = self._page_count()
        values = sorted({0, pages - 1, *range(max(0, self._page - 1), min(pages, self._page + 3))})
        ctk.CTkButton(self._numbered_pager, text="‹", width=26, height=28,
                     fg_color=T["BG_INPUT"], text_color=T["TEXT_PRIMARY"], command=self._goto_prev).pack(side="left", padx=2)
        previous = -1
        for page in values:
            if previous >= 0 and page - previous > 1:
                ctk.CTkLabel(self._numbered_pager, text="…", width=20).pack(side="left")
            ctk.CTkButton(self._numbered_pager, text=str(page + 1), width=26, height=28,
                         fg_color=T["ACCENT"] if page == self._page else T["BG_CARD"],
                         text_color=T["ACCENT_TEXT"] if page == self._page else T["TEXT_SECONDARY"],
                         command=lambda value=page: self._goto_page(value)).pack(side="left", padx=2)
            previous = page
        ctk.CTkButton(self._numbered_pager, text="›", width=26, height=28,
                     fg_color=T["BG_INPUT"], text_color=T["TEXT_PRIMARY"], command=self._goto_next).pack(side="left", padx=2)
        ctk.CTkLabel(self._numbered_pager, text=tr("layout.total", count=len(self._sorted)),
                     font=(self._font_family, FONT.TINY)).pack(side="right")

    # ================= 主题 / 样式 =================

    def _panel_bg(self) -> str:
        """面板底色：直接取语义令牌 BG_CARD，与主界面的卡片同色。"""
        return color("BG_CARD")

    def _apply_ttk_style(self) -> None:
        """配置 Treeview 样式（颜色/tag/行高）。

        Windows 默认的 vista 主题会忽略 background 与 tag 颜色（只能用 clam 画），
        所以这里在检测到不可着色的主题时切到 clam；ctk 控件不走 ttk，不受影响。
        """
        style = ttk.Style(self)
        try:
            if style.theme_use() in ("vista", "winnative", "xpnative"):
                style.theme_use("clam")
        except tk.TclError:  # pragma: no cover - 极简 Tk 环境
            pass

        family = self._font_family
        tree_bg = color("BG_CARD")
        tree_fg = color("ROW_TEXT")
        style.configure(
            self._style_tree,
            background=tree_bg,
            fieldbackground=tree_bg,
            foreground=tree_fg,
            rowheight=self._row_height(),
            font=(family, _ROW_FONT_SIZE),
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            f"{self._style_tree}.Heading",
            background=color("BG_ELEVATED"),
            foreground=color("TEXT_SECONDARY"),
            font=(family, _ROW_FONT_SIZE, "bold"),
            relief="flat",
            borderwidth=0,
            padding=(SPACE.S, SPACE.XS),
        )
        style.map(f"{self._style_tree}.Heading", background=[("active", color("BG_HOVER"))])
        style.map(
            self._style_tree,
            background=[("selected", color("BG_SELECTED"))],
            foreground=[("selected", color("TEXT_PRIMARY"))],
        )
        # 滚动条也跟着主题走（默认的 Windows 滚动条在深色下很扎眼）
        try:
            style.configure(
                "SongList.Vertical.TScrollbar",
                background=color("BORDER_STRONG"),
                troughcolor=color("BG_CARD"),
                bordercolor=color("BG_CARD"),
                arrowcolor=color("TEXT_MUTED"),
                relief="flat",
                borderwidth=0,
                width=10,
            )
            style.map(
                "SongList.Vertical.TScrollbar",
                background=[("active", color("TEXT_MUTED"))],
            )
        except tk.TclError:  # pragma: no cover
            pass

        # 选中态同时用 style.map 和 tag 表达：ttk 各主题里二者的优先级并不一致，
        # 两边都配上才能保证"选中一定看得见"，同时高亮行也不会因为被选中就消失。
        tree = self._tree
        tree.tag_configure(_TAG_ROW, background=tree_bg, foreground=tree_fg)
        tree.tag_configure(_TAG_ALT, background=color("BG_ZEBRA"), foreground=tree_fg)
        tree.tag_configure(
            _TAG_SEL, background=color("BG_SELECTED"), foreground=color("TEXT_PRIMARY")
        )
        tree.tag_configure(_TAG_HL, background=color("ACCENT_SOFT") if self._rich_rows else color("DANGER_SOFT"), foreground=tree_fg)
        tree.tag_configure(
            _TAG_HL_SEL,
            background=color("BG_SELECTED"),
            foreground=color("DANGER"),
        )
        tree.tag_configure(_TAG_HOVER, background=color("BG_HOVER"), foreground=tree_fg)

    def _row_height(self) -> int:
        """按当前字体算行高（字体缺失时退回 24）。"""
        if self._rich_rows:
            return 56
        try:
            metrics = tkfont.Font(root=self, font=(self._font_family, _ROW_FONT_SIZE))
            return max(22, int(metrics.metrics("linespace")) + 8)
        except Exception:  # pragma: no cover - 字体服务异常
            return 24

    def _apply_panel_colors(self) -> None:
        """刷新那些"手写了颜色"的地方（ctk 控件自己会跟随主题，不用管）。"""
        if not self.winfo_exists():
            return
        panel_bg = self._panel_bg()
        try:
            self.configure(bg=panel_bg)
        except tk.TclError:  # pragma: no cover - 已销毁
            return
        self._toolbar.configure(fg_color="transparent")
        self._pager.configure(fg_color="transparent")
        self._header.configure(text_color=color("TEXT_PRIMARY"))
        # 数量徽标用本列的语义色（与标题徽章呼应）；主题切换后要重新解析，
        # 不能用 `T[...]`（那是 (深,浅) 二元组，必须过 `color()`）。
        self._count_badge.configure(
            text_color=color(self._accent_role), fg_color=color("BG_ELEVATED")
        )
        self._empty_label.configure(text_color=color("TEXT_MUTED"))
        self._redraw_rows()
        self._accent_bar.configure(fg_color=color(self._accent_role))
        # 徽章里的图标是叠在徽章底上的独立 Label，底色要跟着一起改，
        # 否则主题切换后图标背后留一块旧色（ctk 的内层 tk.Label 不会自动重画）。
        self._accent_icon.configure(
            bg_color=color(self._accent_role), text_color=color("ACCENT_TEXT")
        )
        self._scroll.configure(style="SongList.Vertical.TScrollbar")

    def refresh_theme(self) -> None:
        """主题切换后由主程序调用：重新解析颜色并重配 ttk 样式/tag。

        只改样式，不重建行，所以调用它是廉价的，也不会丢选中位置。
        """
        if not self.winfo_exists():
            return
        self._apply_ttk_style()
        self._apply_panel_colors()
        self._update_sort_buttons()
        self._redraw_canvases()

    def _redraw_canvases(self) -> None:
        """强制重绘 ctk 控件的内层画布/标签，让主题切换**立刻**生效。

        背景：ctk 的控件内部是 tk.Canvas + tk.Label，外观模式变化时它们**不会**自动重画
        背景色。实测"深色→浅色"后，列表标题栏会残留一块深色矩形 —— 用洪水填充定位到的
        正是 CTkLabel 里那个内层 `Label`（它的 `bg` 还停在旧主题的深色上）。
        这里显式给它设成当前主题的面板底色并触发重画，问题即消失。
        """
        labels = [
            self._header, self._count_badge, self._page_label, self._status,
            *self._sort_buttons.values(),
            self._first_btn, self._prev_btn, self._next_btn, self._last_btn,
        ]
        for widget in labels:
            # Let CTk redraw its own canvas and label from the current semantic
            # foreground. Painting every child white erases selected button text.
            widget.configure(bg_color=T["BG_CARD"], fg_color=widget.cget("fg_color"))
        # 按钮/下拉/输入框自身也重画一次，避免残留旧主题的描边
        for widget in (
            *self._sort_buttons.values(), self._first_btn, self._prev_btn,
            self._next_btn, self._last_btn, self._size_menu, self._search,
            self._toolbar, self._pager, self._table_holder, self._tree, self._scroll,
        ):
            try:
                widget.update_idletasks()
            except tk.TclError:
                continue

    # ================= 空状态 / 便捷方法 =================

    def set_empty_hint(self, text: str) -> None:
        """设置"列表为空时"显示的引导文案（比一个空白表格友好得多）。

        文案前面会加上本列的图标 —— 三列的标题栏本来就带图标，
        空状态再跟一次，用户扫一眼就知道这块空白属于哪一列
        （m05627「UI 的区分不够明显」）。
        已经带图标前缀的文案不重复加（回调可能传自己写好的文案）。
        """
        body = text or ""
        if not body:
            self._empty_hint = ""
        elif body.startswith(self._icon):
            self._empty_hint = body
        else:
            self._empty_hint = f"{self._icon}  {body}"
        self._update_empty_state()

    def focus_search(self) -> None:
        """把焦点放到搜索框（Ctrl+F）。"""
        try:
            self._search.focus_set()
            self._search.select_range(0, "end")
        except tk.TclError:
            pass

    def _update_empty_state(self) -> None:
        """没有可显示的行时，用提示文字覆盖表格区域。"""
        if not self.winfo_exists():
            return
        empty = not self._sorted
        if empty:
            self._empty_label.configure(text=self._empty_hint)
            self._empty_label.place(relx=0.5, rely=0.42, anchor="center")
            self._empty_visible = True
        elif self._empty_visible:
            self._empty_label.place_forget()
            self._empty_visible = False

    # ================= 公开数据接口 =================

    def set_items(
        self,
        items: Sequence[SongItem],
        *,
        title_map: Mapping[str, str] | None = None,
        rating_map: Mapping[str, int] | None = None,
        highlight: Iterable[str] = (),
    ) -> None:
        """整表替换（扫描完/切数据源时调用）。

        只重画当前页（≤ page_size 行），所以 3000 首也只是插入 100 行的事。
        注意：`title_map` / `rating_map` 传空 dict 与不传一样，都会清空旧映射——
        原版 `set_songs(..., {}, {})` 因为 `or` 判断而保留了上一次的评分，属于 bug。
        """
        self._items = list(items)
        self._title_map = dict(title_map) if title_map else {}
        self._rating_map = dict(rating_map) if rating_map else {}
        self._highlight = set(highlight)
        # 整表替换意味着"这些歌已经不是原来那批了"，跨页选择集必须清掉，
        # 否则会残留指向已不存在歌曲的名字
        self._selected.clear()
        self._recompute(reset_page=True)
        self._repaint()

    def append_items(self, items: Sequence[SongItem]) -> None:
        """流式追加（扫描过程中分批喂数据）。

        默认排序且无搜索时 `_filtered` / `_sorted` 与 `_items` 是同一个列表对象，
        追加是 O(1) 且不会全表拷贝；只有"当前正在看末页"或"非默认排序"时才重画，
        其余情况只更新计数标签。插入永远先全部 insert，再更新页码标签。
        """
        if not items:
            return
        was_last_page = self._page >= self._page_count() - 1
        self._items.extend(items)
        self._recompute()
        if was_last_page or self._sort_key != "default" or self._reverse:
            self._repaint()
        else:
            self._update_labels()

    def clear(self) -> None:
        """清空列表数据、搜索框与高亮（保留排序方式、页大小等用户设置）。"""
        self._cancel_debounce()
        if self._search_var.get():
            self._search_var.set("")  # 会触发 trace 再排一个防抖，下面一并取消
        self._terms = []
        self._cancel_debounce()
        self.set_items(())

    def set_search(self, text: str) -> None:
        """以编程方式设置搜索词（会正常触发防抖过滤）。"""
        self._search_var.set(text or "")

    @property
    def search_var(self) -> tk.StringVar:
        """供外部绑定/读取搜索词的变量。"""
        return self._search_var

    # ---------------- 筛选档位（v17.7） ----------------

    def current_filter(self) -> str:
        """当前筛选档位（"all" / "matched" / "missing"）。"""
        return self._filter

    def set_filter(self, value: str, *, notify: bool = False) -> None:
        """切换筛选档位。`notify=True` 时才回调主程序去重算列表内容。

        计数（`set_filter_counts`）与选中样式在这里一并刷新，**无论是否 notify** ——
        主程序在灌完数据后会回填计数，那时不应再回调一次，否则会绕成环。
        """
        self._filter = value if value in SONG_FILTERS else SONG_FILTERS[0]
        self._paint_filter_chips()
        if notify and self._on_filter_changed is not None:
            self._on_filter_changed(self._filter)

    def set_filter_counts(self, counts: Mapping[str, int] | None = None) -> None:
        """更新每个档位的计数徽标，例如 `{"all": 85, "matched": 21, "missing": 64}`。

        计数由主程序算（它才知道另一侧有什么），控件只负责显示。
        """
        self._filter_counts = {k: self._as_int(v) for k, v in (counts or {}).items()}
        self._paint_filter_chips()

    def _paint_filter_chips(self) -> None:
        """把档位按钮画成"选中的是实色、其余是描边"两种状态，并写上计数。"""
        for value, button in self._filter_buttons.items():
            selected = value == self._filter
            count = self._filter_counts.get(value)
            label = tr(f"filter.{value}")
            button.configure(
                text=f"{label} {count}" if count is not None else label,
                fg_color=T[self._accent_role] if selected else "transparent",
                hover_color=T[self._accent_role] if selected else T["BG_HOVER"],
                text_color=T["ACCENT_TEXT"] if selected else T["TEXT_SECONDARY"],
                border_color=T[self._accent_role] if selected else T["BORDER"],
            )
        if self._filter_counts:
            total = self._filter_counts.get("all")
            shown = self._filter_counts.get(self._filter)
            self._filter_count.configure(
                text="" if total is None or shown is None or shown == total
                else tr("filter.showing", shown=shown, total=total)
            )
        else:
            self._filter_count.configure(text="")

    def select(self, folder_name: str) -> None:
        """选中指定歌曲：必要时自动翻页、滚动到可见，并**无条件**触发 on_select。

        歌曲不在当前搜索结果里时静默返回（不抛异常、不触发回调）。
        """
        index = self._index_in_sorted(folder_name)
        if index < 0:
            return
        page = index // self._page_size
        if page != self._page:
            self._page = page
            self._repaint()
        self._apply_selection(folder_name)
        self._last_selected = folder_name
        self._emit_select(folder_name)

    def selected(self) -> str | None:
        """当前"主选中"的文件夹名（多选时是最后点中的那首）；没有选中返回 None。"""
        iids = self._tree.selection()
        if not iids:
            return None
        focused = self._tree.focus()
        if focused in self._iid_name:
            return self._iid_name[focused]
        return self._iid_name.get(iids[0])

    def selected_all(self) -> list[str]:
        """**跨页**所有被选中的文件夹名（B1）。

        v15.2 之前这里读的是 Treeview 的选中行，而 Treeview 只持有当前页 ——
        翻一页选择就没了，`Ctrl+A` 也只选当前页。现在按文件夹名维护独立选择集，
        翻页/搜索/排序都不会丢；顺序按整表顺序，方便用户核对。
        """
        return [item.key for item in self._items if item.key in self._selected]

    def selection_count(self) -> int:
        return len(self._selected)

    def select_all_visible(self) -> None:
        """全选**当前页**（Ctrl+A）。"""
        iids = list(self._visible_iids)
        if not iids:
            return
        for iid in iids:
            name = self._iid_name.get(iid)
            if name:
                self._selected.add(name)
        self._suppress_select = True
        try:
            self._tree.selection_set(iids)
            self._tree.focus(iids[0])
        finally:
            self._suppress_select = False
        for iid in iids:
            self._tag_row(iid)
        self._sel_iid = iids[0]
        self._sel_rows = set(iids)
        names = self.selected_all()
        self._last_selected = self.selected()
        if names:
            self._emit_select_multi(names)
            self._show_status(f"已选中 {len(names)} 首（当前页）", "ACCENT")
        self._update_selection_badge()

    def select_all(self) -> None:
        """全选**所有页**（Ctrl+Shift+A）—— 跨页选择集的意义就在这里。"""
        if not self._items:
            return
        self._selected = {item.key for item in self._items}
        self._repaint()  # 让当前页把选中态画出来
        names = self.selected_all()
        self._emit_select_multi(names)
        self._show_status(f"已选中全部 {len(names)} 首", "ACCENT")
        self._update_selection_badge()

    def clear_selection(self) -> None:
        """清空跨页选择集（Escape）。"""
        self._selected.clear()
        self._suppress_select = True
        try:
            self._tree.selection_remove(*self._tree.selection())
        except tk.TclError:
            pass
        finally:
            self._suppress_select = False
        self._sync_selection_tags()
        self._update_selection_badge()
        self._emit_select_multi([])

    def _sync_selected_set(self) -> None:
        """把**当前页** Treeview 的选中状态合并进跨页选择集。

        只处理本页的行：不在本页的歌保持原样（它们不在 Treeview 里，无从判断）。
        **本页没有行时直接返回** —— 那时 Treeview 的选中必然为空，
        拿它去对账会把整个跨页选择误清掉（重画/翻页的空窗期会走到这里）。
        """
        if not self._iid_name:
            return
        selected_iids = set(self._tree.selection())
        for iid, name in self._iid_name.items():
            if iid in selected_iids:
                self._selected.add(name)
            else:
                self._selected.discard(name)

    def _on_tree_click(self, event: tk.Event) -> str | None:
        """普通单击 → 清空整个跨页选择；Ctrl/Shift 单击 → 保留（追加/区间）。

        紧接着 Treeview 会自己更新本页选择，`<<TreeviewSelect>>` 再把本页同步回来，
        所以这里只管"把别的页也一起清掉"。
        """
        # Tk 的 event.state 位：Shift=0x1、Control=0x4（Windows 与 X11 的抽象值一致）
        if self._rich_rows and self._tree.identify_column(event.x) == "#0":
            iid = self._tree.identify_row(event.y)
            if iid and event.x < 90:
                if iid in self._tree.selection():
                    self._tree.selection_remove(iid)
                else:
                    self._tree.selection_add(iid)
                self._tree.focus(iid)
                self._on_tree_select()
                return "break"
        additive = bool(event.state & 0x0004) or bool(event.state & 0x0001)
        if not additive:
            self._selected.clear()

    def set_drop_active(self, active: bool) -> None:
        """把这一列"点亮"，让用户看清拖进来的歌会落到哪里（B2）。

        WM_DROPFILES 只在**松开鼠标那一刻**才通知我们，没有"拖拽悬停"事件，
        所以真正的"拖拽中高亮"做不了；退而求其次：落点确定后立刻点亮目标列
        （并保留到导入开始），这样用户至少能看清目标对不对。
        """
        try:
            if active:
                self._header.configure(text=f"⬇ {self._title_text}", text_color=color("ACCENT"))
                self._table_holder.configure(border_width=2, border_color=color("ACCENT"))
            else:
                self._header.configure(text=self._title_text, text_color=color("TEXT_PRIMARY"))
                self._table_holder.configure(border_width=0)
        except tk.TclError:  # 控件已销毁（拖放回调晚到）
            pass

    def set_highlight(self, names: Iterable[str]) -> None:
        """设置高亮集合（参数是**文件夹名**，不是下标）。只改 tag，不重建行。"""
        self._highlight = set(names)
        self._retag_rows()

    def clear_highlight(self) -> None:
        """清掉所有高亮。"""
        if not self._highlight:
            return
        self._highlight = set()
        self._retag_rows()

    def set_rating(self, folder_name: str, rating: int) -> None:
        """就地更新一行的评分（只改一个单元格）；当前按评分排序时才重排。"""
        value = self._as_int(rating)
        self._rating_map[folder_name] = value
        self._redraw_rows()
        iid = self._name_iid.get(folder_name)
        if iid and self._tree.exists(iid):
            self._tree.set(iid, "rating", self._stars(value))
        if self._sort_key == "rating":
            self._recompute()
            self._repaint()

    def remove_item(self, folder_name: str) -> None:
        """从列表里去掉一首歌（删除成功后由主程序调用）；不触发任何回调。"""
        before = len(self._items)
        self._items = [item for item in self._items if item.key != folder_name]
        if len(self._items) == before:
            return
        self._title_map.pop(folder_name, None)
        self._rating_map.pop(folder_name, None)
        self._highlight.discard(folder_name)
        if self._last_selected == folder_name:
            self._last_selected = None
        self._recompute()
        self._repaint()

    def items(self) -> list[SongItem]:
        """全部歌曲的副本（插入顺序，不受排序/搜索/分页影响）。"""
        return list(self._items)

    def visible_names(self) -> list[str]:
        """当前"搜索过滤 + 排序"后的全部文件夹名（不只是当前页）。"""
        return [item.key for item in self._sorted]

    def total_count(self) -> int:
        """库里一共多少首（不受搜索/分页影响）。"""
        return len(self._items)

    # ================= 视图计算（纯 Python，不碰控件） =================

    def _recompute(self, *, reset_page: bool = False) -> None:
        """重算"过滤 + 排序"结果并夹紧页码。"""
        if self._terms:
            terms = self._terms
            self._filtered = [item for item in self._items if self._matches(item, terms)]
        else:
            # 不复制：无搜索时三个视图共享同一个列表对象，追加/读取都是 O(1)
            self._filtered = self._items
        if self._sort_key == "default" and not self._reverse:
            self._sorted = self._filtered
        else:
            self._sorted = sorted(self._filtered, key=self._sort_value, reverse=self._reverse)
        pages = self._page_count()
        self._page = 0 if reset_page else min(max(0, self._page), pages - 1)

    def _sort_value(self, item: SongItem):
        """排序键。

        name   -> 标题小写（空标题回落到文件夹名）
        rating -> (评分取负, 名称)：默认高分在前；右键"降序"再整体反转。
        default-> 不排序（插入顺序），此函数不会被调用。
        """
        if self._sort_key == "rating":
            return (-self._rating_of(item.key), self._name_key(item))
        return self._name_key(item)

    def _name_key(self, item: SongItem) -> str:
        """名称排序/评分同分时的比较键。"""
        return self._title_of(item).lower()

    def _title_of(self, item: SongItem) -> str:
        """展示与排序用的标题：title_map（数据库）> item.title > 文件夹名。"""
        return self._title_map.get(item.key) or item.title or item.folder_name

    def _rating_of(self, name: str) -> int:
        """某首歌的评分（缺失或脏数据都算 0）。"""
        return self._as_int(self._rating_map.get(name, 0))

    @staticmethod
    def _as_int(value) -> int:
        """尽量把值转成 int，转不了就 0（列表不能因为一个脏评分就崩）。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _stars(rating: int) -> str:
        """评分显示：★ × n（0 分留空，超过 5 按 5 算）。"""
        return "★" * max(0, min(5, int(rating)))

    @staticmethod
    def _parse_terms(text: str) -> list[str]:
        """空格分隔的多个关键词（多个空格/Tab 都算分隔符），统一小写。"""
        return text.lower().split()

    def _matches(self, item: SongItem, terms: Sequence[str]) -> bool:
        """关键词全中才算命中（AND）；文件夹名/标题/作者任一字段包含即可。"""
        blob = f"{item.key}\n{self._title_of(item)}\n{item.author}".lower()
        return all(term in blob for term in terms)

    def _page_count(self) -> int:
        """总页数（永远 ≥ 1，空列表时也是 1 页，避免各种除零/越界）。"""
        return max(1, (len(self._sorted) + self._page_size - 1) // self._page_size)

    def _page_items(self) -> list[SongItem]:
        """当前页要显示的那一段（切片，不复制对象）。"""
        start = self._page * self._page_size
        return self._sorted[start : start + self._page_size]

    def _index_in_sorted(self, folder_name: str) -> int:
        """歌曲在"过滤+排序"结果里的下标；找不到返回 -1（只在 select 里用，很少调用）。"""
        for index, item in enumerate(self._sorted):
            if item.key == folder_name:
                return index
        return -1

    def _item_by_name(self, folder_name: str) -> SongItem | None:
        """按文件夹名找原始条目（只有右键菜单用，线性找一次无所谓）。"""
        for item in self._items:
            if item.key == folder_name:
                return item
        return None

    def _rich_text(self, item: SongItem, selected: bool) -> str:
        return self._title_of(item) + ("\n" + item.author if item.author else "")

    def set_presence(self, missing: set[str] | None) -> None:
        """接收实际两侧差集；尚未载入两侧时不宣称已匹配。"""
        self._presence_missing = missing
        self._redraw_rows()
        if self._rich_rows:
            for iid, name in self._iid_name.items():
                self._tree.set(iid, "title", self._presence_text(name))

    def _presence_text(self, name: str) -> str:
        if self._presence_missing is None:
            return "—"
        return tr("songlist.state.missing" if name in self._presence_missing else "songlist.state.present")

    def _request_thumbnail(self, iid: str, path: str, folder: str = "", key: str = "") -> None:
        cached = self._thumb_cache.get(path)
        if cached is not None:
            self._tree.item(iid, image=cached)
        job_key = path or folder
        if (cached is None or (folder and key not in self._row_sizes)) and job_key not in self._thumb_pending and self._thumb_pool is not None:
            self._thumb_pending.add(job_key)
            # The worker only decodes PIL data and queues it; all Tk objects stay on the UI thread.
            self._thumb_pool.submit(_decode_thumbnail, path, self._thumb_results, folder, key)

    def _pump_thumbnails(self) -> None:
        from PIL import ImageTk

        changed = False
        for _ in range(24):
            try:
                path, pixels, key, size = self._thumb_results.get_nowait()
            except queue.Empty:
                break
            self._thumb_pending.discard(path)
            item = self._item_by_name(key)
            if item is not None:
                self._thumb_pending.discard(item.local_path)
                if size is not None and item.cover_path == path:
                    self._row_sizes[key] = size
                    changed = True
            if pixels is None:
                continue
            if len(self._thumb_cache) >= 300:
                # Keep all images currently referenced by visible rows alive.
                visible = {item.cover_path for item in self._page_items()}
                self._thumb_cache = {key: value for key, value in self._thumb_cache.items() if key in visible}
            image = ImageTk.PhotoImage(pixels, master=self)
            self._thumb_cache[path] = image
            changed = True
            for item in self._page_items():
                iid = self._name_iid.get(item.key)
                if item.cover_path == path and iid and self._tree.exists(iid):
                    self._tree.item(iid, image=image)
        if changed:
            self._redraw_rows()
        self._thumb_job = self.after(100, self._pump_thumbnails)

    # ================= 重画（性能关键路径） =================

    def _repaint(self) -> None:
        """只重画当前页：删一批、插一批，最后才更新标签。

        这是整个控件唯一的"重建"路径，成本与 `page_size` 成正比（默认 100 行），
        与库里有多少首歌无关。
        """
        if not self.winfo_exists():
            return
        tree = self._tree
        page_items = self._page_items()
        highlight = self._highlight
        self._suppress_select = True  # 重画期间 Treeview 会发假的选择事件，先屏蔽
        try:
            if self._visible_iids:
                tree.delete(*self._visible_iids)  # 一次 Tcl 调用删完整页
            self._visible_iids = []
            self._iid_name = {}
            self._name_iid = {}
            self._hl_rows = set()
            self._sel_iid = ""
            self._sel_rows = set()
            insert = tree.insert
            iids: list[str] = []
            for position, item in enumerate(page_items):
                # 行 id 与文件夹名解耦：重名文件夹、奇怪字符都不会让 Tcl 报错
                iid = f"r{position}"
                name = item.key
                if name in highlight:
                    tag = _TAG_HL
                    self._hl_rows.add(iid)
                else:
                    # 斑马纹：偶数行淡底，长列表更好扫读（深色下也更接近现代 IDE 观感）
                    tag = _TAG_ALT if position % 2 else _TAG_ROW
                insert(
                    "",
                    "end",
                    iid,
                    text=self._rich_text(item, name in self._selected) if self._rich_rows else name,
                    values=(self._stars(self._rating_of(name)), self._presence_text(name) if self._rich_rows else self._title_of(item)),
                    tags=(tag,),
                )
                if self._rich_rows and (item.cover_path or (self._result_column and item.local_path)):
                    self._request_thumbnail(iid, item.cover_path, item.local_path if self._result_column else "", item.key)
                self._iid_name[iid] = name
                self._name_iid[name] = iid
                iids.append(iid)
            self._visible_iids = iids
            self._restore_selection()
        finally:
            self._suppress_select = False
        self._update_labels()  # 行全部插完才更新标题/页码
        self._redraw_rows()

    def _restore_selection(self) -> None:
        """重画后恢复选中：跨页选择集里落在本页的那些行 + 上次的主选中行。

        重画会把整页删掉重建，所以"选中态"必须重新画上去 ——
        这也是跨页选择能成立的前提（翻页后回到本页，选中还在）。
        """
        iids = [self._name_iid[name] for name in self._name_iid if name in self._selected]
        focus_iid = self._name_iid.get(self._last_selected) if self._last_selected else None
        if focus_iid and focus_iid not in iids:
            iids.append(focus_iid)
        if not iids:
            self._sel_iid = ""
            self._sel_rows = set()
            self._update_selection_badge()
            return
        self._tree.selection_set(iids)
        target = focus_iid or iids[0]
        self._tree.focus(target)
        self._tree.see(target)
        self._sel_iid = target
        self._sel_rows = set(iids)
        for iid in iids:
            self._tag_row(iid)
        self._update_selection_badge()

    def _apply_selection(self, folder_name: str) -> None:
        """把选中状态落到控件上（不翻页、不回调）——select/键盘/右键共用。

        同时**同步**更新跨页选择集：这里用 `_suppress_select` 屏掉了 Treeview 事件，
        若只靠那个事件去同步，就会依赖"事件什么时候送达"——测试里已经因此偶发过
        （选中状态要等一个迟到的 `<<TreeviewSelect>>` 才生效）。
        语义上这里是"换成这一首"（与普通单击一致），所以是覆盖而不是追加。
        """
        iid = self._name_iid.get(folder_name)
        if not iid or not self._tree.exists(iid):
            return
        self._selected = {folder_name}
        self._suppress_select = True
        try:
            self._tree.selection_set(iid)
            self._tree.focus(iid)
            self._tree.see(iid)
        finally:
            self._suppress_select = False
        self._sync_selection_tags()

    def _sync_selection_tags(self) -> None:
        """按当前选择集刷新 tag。

        多选（selectmode="extended"）下可能有几十行同时选中，所以这里改成
        维护"选中行集合"，只对状态真正变化的行发 Tcl 调用；
        以"主选中行"（focus）决定哪个用 hlsel（高亮+选中）样式。
        """
        current = set(self._tree.selection())
        focus = self._tree.focus()
        self._sel_iid = focus if focus in current else (next(iter(current)) if current else "")
        changed = current ^ self._sel_rows
        self._sel_rows = current
        for iid in changed:
            self._tag_row(iid)

    def _tag_row(self, iid: str) -> None:
        """按"高亮 > 选中 > 悬停 > 斑马纹"的优先级给一行换 tag。

        优先级是刻意的：悬停只是"鼠标在哪"的提示，绝不能盖掉"这首歌缺失"或"已选中"
        这类有语义的状态（否则鼠标一划过，用户就看不见哪些是缺失歌曲了）。
        """
        if not self._tree.exists(iid):
            return
        highlighted = self._iid_name.get(iid, "") in self._highlight
        if highlighted and iid in self._sel_rows:
            tag = _TAG_HL_SEL
        elif highlighted:
            tag = _TAG_HL
        elif iid in self._sel_rows:
            tag = _TAG_SEL
        elif iid == self._hover_iid:
            tag = _TAG_HOVER
        else:
            # 斑马纹取决于行在当前页里的位置，从行 id 反推（id 形如 "r12"）
            try:
                position = int(iid[1:])
            except ValueError:
                position = 0
            tag = _TAG_ALT if position % 2 else _TAG_ROW
        self._tree.item(iid, tags=(tag,))
        self._redraw_rows()
        if self._rich_rows:
            item = self._item_by_name(self._iid_name.get(iid, ""))
            if item is not None:
                self._tree.item(iid, text=self._rich_text(item, iid in self._sel_rows))
        if highlighted:
            self._hl_rows.add(iid)
        else:
            self._hl_rows.discard(iid)

    def _retag_rows(self) -> None:
        """按新的高亮集合刷 tag；只有状态真的变了的行才发 Tcl 调用。"""
        for iid, name in self._iid_name.items():
            if (name in self._highlight) == (iid in self._hl_rows):
                continue
            self._tag_row(iid)

    # ---------------- 悬停高亮 / 列宽 ----------------

    def _on_tree_motion(self, event: tk.Event) -> None:
        """鼠标移动：给指针所在行加悬停底色（ttk 原生不支持 hover）。"""
        self._set_hover(self._tree.identify_row(event.y))

    def _set_hover(self, iid: str) -> None:
        if iid == self._hover_iid:
            return
        previous = self._hover_iid
        self._hover_iid = iid
        for target in (previous, iid):
            if target and self._tree.exists(target):
                self._tag_row(target)

    def _on_column_release(self, _event: tk.Event | None = None) -> None:
        """松开鼠标后检查列宽有没有变化，变了就通知主程序保存。"""
        widths = self.get_column_widths()
        if widths == self._last_widths:
            return
        self._last_widths = dict(widths)
        if self._on_columns_changed is not None:
            try:
                self._on_columns_changed(widths)
            except Exception as exc:  # 持久化失败不该影响界面
                logger.debug("列宽回调失败: %s", exc)

    def get_column_widths(self) -> dict[str, int]:
        """当前列宽 {列名: 宽度}，列名用语义名（folder/rating/title）便于跨版本兼容。"""
        if self._rich_rows:
            return {"song": int(self._tree.column("#0", "width")),
                    "rating": int(self._tree.column("rating", "width")),
                    "status": int(self._tree.column("title", "width"))}
        return {
            "folder": int(self._tree.column("#0", "width")),
            "rating": int(self._tree.column("rating", "width")),
            "title": int(self._tree.column("title", "width")),
        }

    def set_column_widths(self, widths: Mapping[str, int] | None) -> None:
        """应用保存的列宽（缺项/非法值忽略）。"""
        if not widths:
            return
        mapping = ({"song": "#0", "rating": "rating", "status": "title"} if self._rich_rows
                   else {"folder": "#0", "rating": "rating", "title": "title"})
        for name, column in mapping.items():
            value = widths.get(name)
            try:
                value = int(value) if value is not None else 0
            except (TypeError, ValueError):
                continue
            if value >= 40:
                self._tree.column(column, width=value)
        self._last_widths = self.get_column_widths()

    # ================= 标签 / 翻页 =================

    def _update_labels(self) -> None:
        """刷新标题栏与页码标签（每页最多调一次，绝不在插入循环里调）。"""
        if not self.winfo_exists():
            return
        total = len(self._items)
        shown = len(self._sorted)
        self._header.configure(text=self._title_text)
        self._update_selection_badge()
        self._page_label.configure(
            text=tr("list.page", page=self._page + 1, total=self._page_count(),
                    count=shown)
        )
        self._update_pager_buttons()
        self._update_empty_state()

    def _update_pager_buttons(self) -> None:
        self._update_numbered_pager()
        """到首页/末页时把对应按钮置灰（空列表时四个全灰）。"""
        pages = self._page_count()
        self._first_btn.configure(state=tk.NORMAL if self._page > 0 else tk.DISABLED)
        self._prev_btn.configure(state=tk.NORMAL if self._page > 0 else tk.DISABLED)
        self._next_btn.configure(state=tk.NORMAL if self._page < pages - 1 else tk.DISABLED)
        self._last_btn.configure(state=tk.NORMAL if self._page < pages - 1 else tk.DISABLED)

    def _update_sort_buttons(self) -> None:
        """激活的排序按钮用语义色实底，其余用描边空心；▲/▼ 标出升降序。"""
        for key, button in self._sort_buttons.items():
            active = key == self._sort_key
            if active:
                button.configure(
                    fg_color=T[self._accent_role],
                    hover_color=T[self._accent_role],
                    text_color=T["ACCENT_TEXT"],
                    border_width=0,
                )
            else:
                button.configure(
                    fg_color="transparent",
                    hover_color=T["BG_HOVER"],
                    text_color=T["TEXT_SECONDARY"],
                    border_width=1,
                    border_color=T["BORDER_STRONG"],
                )
            label = tr(_SORT_LABEL_KEYS[key])
            if active:
                label = f"{label} {'▼' if self._reverse else '▲'}"
            button.configure(text=label)

    def _goto_first(self) -> None:
        self._goto_page(0)

    def _goto_prev(self) -> None:
        self._goto_page(self._page - 1)

    def _goto_next(self) -> None:
        self._goto_page(self._page + 1)

    def _goto_last(self) -> None:
        self._goto_page(self._page_count() - 1)

    def _goto_page(self, page: int) -> None:
        """翻页：只重画新的一页；越界夹紧，空列表也不会 IndexError。"""
        page = min(max(0, page), self._page_count() - 1)
        if page == self._page:
            return
        self._page = page
        self._repaint()

    # ================= 搜索 =================

    def _refresh_search_hint(self) -> None:
        if self._search_hint_label is None:
            return
        if self._search_var.get():
            self._search_hint_label.place_forget()
        else:
            self._search_hint_label.configure(text=tr("layout.search", title=self._title_text))
            self._search_hint_label.place(x=10, rely=.5, anchor="w")

    def _on_search_changed(self, *_args: str) -> None:
        """搜索框每次变动：只更新条件 + 重排一个 after 回调（打字期间零重画）。"""
        self._refresh_search_hint()
        self._terms = self._parse_terms(self._search_var.get())
        self._cancel_debounce()
        if not self.winfo_exists():
            return
        self._debounce_id = self.after(SEARCH_DEBOUNCE_MS, self._apply_search)

    def _apply_search(self) -> None:
        """防抖到期：过滤一次、回到第一页、只重画当前页。"""
        self._debounce_id = None
        if not self.winfo_exists():
            return
        self._recompute(reset_page=True)
        self._repaint()

    def _cancel_debounce(self) -> None:
        """取消挂起的搜索防抖（控件销毁时也必须调用）。"""
        if self._debounce_id is None:
            return
        try:
            self.after_cancel(self._debounce_id)
        except (tk.TclError, ValueError):  # pragma: no cover - 已触发/已销毁
            pass
        self._debounce_id = None

    # ================= 排序 =================

    def _on_sort_clicked(self, key: str, *, force_reverse: bool = False) -> None:
        """左键切换排序方式，右键（force_reverse）直接降序。

        点当前按钮 = 升/降序互切；换按钮 = 用该键的默认方向（右键则直接降序）。
        排序只重排 Python 列表并重画当前页，不会重建整表。
        """
        if key != self._sort_key:
            self._sort_key = key
            self._reverse = force_reverse
        elif force_reverse:
            self._reverse = True
        else:
            self._reverse = not self._reverse
        self._recompute(reset_page=True)
        self._repaint()
        self._update_sort_buttons()
        if self._on_sort_changed is not None:
            self._on_sort_changed(self._sort_key, self._reverse)

    def _on_page_size_changed(self, value: str) -> None:
        """每页行数变化：回到第一页，只重画新的一页。"""
        size = self._as_int(value)
        if size <= 0 or size == self._page_size:
            return
        self._page_size = size
        self._recompute(reset_page=True)
        self._repaint()

    # ================= 选择 / 键盘 / 右键 =================

    def _on_tree_select(self, _event: tk.Event | None = None) -> None:
        """<<TreeviewSelect>>：只有"真的换了歌"才回调；多选变化走 on_select_multi。

        重画/恢复选中会让 Treeview 把事件排进队列，等 _suppress_select 复位之后才送达，
        所以这里必须再用 _last_selected 比对一次，否则每次重画都会多回调一次。
        """
        if self._suppress_select:
            return
        self._sync_selected_set()  # 把本页的选中变化并进跨页选择集
        self._sync_selection_tags()
        self._update_selection_badge()
        names = self.selected_all()
        if self._on_select_multi is not None:
            self._on_select_multi(names)
        name = self.selected()
        if name is None or name == self._last_selected:
            return
        self._last_selected = name
        self._emit_select(name)

    def _on_double_click(self, event: tk.Event) -> str:
        """双击：先确保点中的那行是选中行，再触发 on_activate。"""
        name = self._iid_name.get(self._tree.identify_row(event.y))
        if not name:
            return "break"
        if name != self.selected():
            self.select(name)  # 里面会触发一次 on_select
        if self._on_activate is not None:
            self._on_activate(name)
        return "break"

    def _move_selection(self, delta: int) -> str:
        """上/下移动选中行；自己实现并 return "break"，避免类绑定再动一次（跳两行）。"""
        names = [self._iid_name.get(iid, "") for iid in self._visible_iids]
        if not names:
            return "break"
        current = self.selected()
        if current in names:
            index = names.index(current) + delta
        else:
            index = 0 if delta > 0 else len(names) - 1
        name = names[min(max(0, index), len(names) - 1)]
        self._apply_selection(name)
        self._last_selected = name
        self._emit_select(name)
        return "break"

    def _on_key_activate(self, _event: tk.Event | None = None) -> str:
        """回车/空格：对当前选中触发 on_select（没选中就什么都不做）。"""
        name = self.selected()
        if name:
            self._last_selected = name
            self._emit_select(name)
        return "break"

    def _on_key_delete(self, _event: tk.Event | None = None) -> str:
        """Delete：触发 on_delete 并吃掉按键。

        这个绑定只存在于 Treeview 上——搜索框（或别的输入框）获得焦点时按 Delete
        根本到不了这里，原版"边打字边删歌"的事故因此不可能再发生。
        """
        name = self.selected()
        if name and self._on_delete is not None:
            self._on_delete(name)
        return "break"

    def _on_key_copy(self, _event: tk.Event | None = None) -> str:
        """Ctrl+C：复制选中的文件夹名（多选时每行一个），并在底部提示 2 秒。"""
        names = self.selected_all()
        if not names:
            # 兜底：跨页集合为空时看 Treeview 自己的选中 —— 免得因为一个迟到的
            # 同步事件就"复制不到东西"（这种时序问题在混合跑测试时真实发生过）
            names = [self._iid_name[i] for i in self._tree.selection() if i in self._iid_name]
        if not names:
            name = self.selected()
            names = [name] if name else []
        if not names:
            self._show_status("未选中任何歌曲", "WARNING")
        else:
            self._copy_to_clipboard("\n".join(names))
        return "break"

    def _on_key_select_all(self, _event: tk.Event | None = None) -> str:
        """Ctrl+A：全选当前页。"""
        self.select_all_visible()
        return "break"

    def _on_key_select_all_pages(self, _event: tk.Event | None = None) -> str:
        """Ctrl+Shift+A：全选**所有页**（跨页选择集的意义就在这里）。"""
        self.select_all()
        return "break"

    def _on_key_clear_selection(self, _event: tk.Event | None = None) -> str:
        """Esc：清空选择（不动搜索框，搜索框由主程序处理）。"""
        self.clear_selection()
        return "break"

    def _on_right_click(self, event: tk.Event) -> str:
        """右键菜单：空白处不弹；点中的那行会先被选中（并同步通知外面）。"""
        name = self._iid_name.get(self._tree.identify_row(event.y))
        if not name:
            return "break"
        self._apply_selection(name)
        if name != self._last_selected:
            self._last_selected = name
            self._emit_select(name)
        menu = self._build_menu(name)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _build_menu(self, name: str) -> tk.Menu:
        """每次弹出前重建菜单项（菜单控件只建一次，右键一万次也不会泄漏 widget）。"""
        if self._menu is None:
            self._menu = tk.Menu(self, tearoff=0)
        menu = self._menu
        menu.delete(0, "end")
        menu.add_command(label=tr("songlist.ctx.copy_folder"), command=lambda: self._copy_to_clipboard(name))
        title = self._title_of(self._item_by_name(name) or SongItem(name))
        menu.add_command(label=tr("songlist.ctx.copy_title"), command=lambda: self._copy_to_clipboard(title))
        # 只有本地磁盘上的歌才能"在资源管理器中打开"（设备上的歌 provider 会返回 None）
        if self._on_open_in_explorer is not None and self._resolve_open_path(name):
            menu.add_command(
                label=tr("songlist.ctx.open_explorer"), command=lambda: self._on_open_in_explorer(name)
            )
        # 危险操作放最后并标红，避免误点
        menu.add_separator()
        menu.add_command(
            label=tr("songlist.ctx.delete"),
            foreground=color("DANGER"),
            command=lambda: self._request_delete(name),
        )
        return menu

    def _resolve_open_path(self, name: str) -> str | None:
        """该歌的本地路径：优先问 open_path_provider（主程序知道数据源是本地还是设备）。"""
        if self._open_path_provider is not None:
            try:
                return self._open_path_provider(name) or None
            except Exception:  # pragma: no cover - provider 抛错不该拖垮菜单
                return None
        item = self._item_by_name(name)
        return (item.local_path or None) if item is not None else None

    def _request_delete(self, name: str) -> None:
        """把删除请求交给主程序（是否弹确认框由 on_delete 回调决定）。"""
        if self._on_delete is not None:
            self._on_delete(name)
        else:
            self._show_status("未配置删除回调", "WARNING")

    def _emit_select(self, name: str) -> None:
        """触发 on_select（调用点已经更新过 _last_selected，回调里再操作本控件是安全的）。"""
        if self._on_select is not None:
            self._on_select(name)

    def _emit_select_multi(self, names: list[str]) -> None:
        """多选变化通知：先把"主选中"给 on_select，再整体给 on_select_multi。"""
        if not names:
            return
        primary = self.selected() or names[0]
        if primary != self._last_selected:
            self._last_selected = primary
            self._emit_select(primary)
        if self._on_select_multi is not None:
            self._on_select_multi(names)

    def _update_selection_badge(self) -> None:
        """标题右侧显示"已选 N 首"，让批量操作的规模一眼可见。"""
        count = self.selection_count()
        if self._rich_rows:
            self._count_badge.configure(text=str(self._filter_counts.get("all", len(self._items))),
                                        fg_color=T["BG_CARD"], text_color=T[self._accent_role])
            if self._result_selection is not None:
                self._result_selection.configure(text=tr("layout.selected", count=count))
            self._redraw_rows()
            return
        if not self.winfo_exists():
            return
        try:
            if count > 1:
                self._count_badge.configure(
                    text=f"已选 {count}", fg_color=T["ACCENT"], text_color=T["ACCENT_TEXT"]
                )
            else:
                shown = len(self._sorted)
                total = len(self._items)
                # 常态下用**本列的语义色**做文字色（m05627 的区分度），
                # 底仍是中性浅底 —— 三列排在一起时一眼能看出各有多少首。
                self._count_badge.configure(
                    text=f"{shown} / {total}" if shown != total else f"{total}",
                    fg_color=T["BG_ELEVATED"],
                    text_color=T[self._accent_role],
                )
        except tk.TclError:
            pass

    # ================= 剪贴板 / 状态提示 =================

    def _copy_to_clipboard(self, text: str) -> None:
        """写系统剪贴板；失败也不抛（剪贴板被别的程序占用时 Tk 会报 TclError）。"""
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:  # pragma: no cover - 剪贴板不可用
            self._show_status("复制失败：剪贴板不可用", "DANGER")
            return
        self._show_status(f"已复制：{text}", "SUCCESS")

    def _show_status(self, text: str, role: str = "TEXT_MUTED") -> None:
        """底部临时提示，2 秒后自动清掉；连续调用只按最后一次计时。

        role 是语义色名（见 theme.Palette），保证深浅色下都有足够对比度。
        """
        if not self.winfo_exists():
            return
        self._status.configure(text=text, text_color=T.get(role, T["TEXT_MUTED"]))
        self._cancel_status_timer()
        self._status_id = self.after(_STATUS_CLEAR_MS, self._clear_status)

    def _clear_status(self) -> None:
        """定时器回调：控件已销毁就直接返回（不能在销毁后再碰 Tcl）。"""
        self._status_id = None
        if not self.winfo_exists():
            return
        try:
            self._status.configure(text="")
        except tk.TclError:  # pragma: no cover - 竞态销毁
            pass

    def _cancel_status_timer(self) -> None:
        if self._status_id is None:
            return
        try:
            self.after_cancel(self._status_id)
        except (tk.TclError, ValueError):  # pragma: no cover
            pass
        self._status_id = None

    # ================= 收尾 =================

    def destroy(self) -> None:
        """销毁前取消所有挂起的 after 回调，避免回调在控件消失后触发 TclError。"""
        self._cancel_debounce()
        self._cancel_status_timer()
        if self._thumb_job is not None:
            self.after_cancel(self._thumb_job)
            self._thumb_job = None
        if self._thumb_pool is not None:
            self._thumb_pool.shutdown(wait=False, cancel_futures=True)
        super().destroy()
