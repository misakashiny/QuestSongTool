"""设计令牌（Design Tokens）：配色、间距、圆角、字号的唯一来源。

为什么要单独一层：
* 之前的颜色是散落的十六进制字面量，同一个"蓝色"在 6 个地方各写一遍，
  想调主题只能全文搜索替换，而且浅色/深色两套值没有统一管理，容易出现
  "深色下这个控件的对比度不够"这种零散问题；
* Material/iOS 这类设计系统的做法是：**先定义语义色（role），控件只引用语义**，
  例如 `SURFACE` / `SURFACE_RAISED` / `TEXT_PRIMARY` / `ACCENT`，
  而不是 `#2b2b2b`。这样换肤、加高对比度模式都只改这一个文件。

本文件提供：
* `Palette`：一套浅色 + 一套深色，字段是**语义名**；
* `get_palette(mode)`：按 ctk 当前外观取色；
* `pair(light, dark)`：给 ctk 用的 (浅色值, 深色值) 元组，省得每处都写两遍；
* `SPACE` / `RADIUS` / `FONT`：8pt 间距体系与字号阶梯。

注意：ctk 控件接受 `("light_value", "dark_value")` 形式的颜色元组并自动随主题切换，
所以大部分场景直接用 `TOKEN` 这种元组即可，无需监听主题变化。
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from .constants import FONT_FAMILY


# ======================================================================================
# 间距 / 圆角 / 字号（8pt 栅格）
# ======================================================================================
class DOWNLOAD:
    CONTROL = 32
    GAP = 8
    ART_HEIGHT = 150
    RATING_ROW_H = 24
    RATING_STAR_SIZE = 12
    RATING_STAR_GAP = 2
    CARD_HEIGHT = 250
    LIST_HEIGHT = 68
    CATEGORY_W = 96
    TOOLBAR_W = 210
    DETAIL_W = 250
    PLAYER_NAME_W = 200
    WINDOW_W = 1280
    WINDOW_H = 824
    TAB_GAP = 6


class DESKTOP:
    """Qt desktop density; shared palettes and geometry remain authoritative."""
    BODY_PX = 13
    CONTROL_PADDING = 10
    COMBO_ARROW_W = 24
    SCROLLBAR_W = 8
    SCROLLBAR_MIN = 28
    TAB_H = 32
    TITLE_PX = 18
    PANEL_RADIUS = 8
    CATEGORY_RADIUS = 14
    PLAYER_ART = 40
    PLAYER_NAME_W = 160
    PLAYER_VOLUME_W = 110
    DOWNLOAD_DETAIL_W = 300
    DOWNLOAD_DETAIL_ART = 256
    LIBRARY_ROW_H = 48
    LIBRARY_ROW_ART = 32
    LIBRARY_CARD_MIN = 200
    LIBRARY_COLUMNS = (180,80,60)
    LIBRARY_RATING_W = 72
    LIBRARY_COVER_MIN = 192
    LIBRARY_TEXT_H = 24
    LIBRARY_DETAIL_ART = 192
    LIBRARY_HELP_W = 640
    LIBRARY_HELP_H = 620
    LIBRARY_HELP_MIN_W = 360
    LIBRARY_HELP_MIN_H = 320
    SELECTED_W = 800
    SELECTED_H = 620
    SELECTED_MIN_W = 420
    SELECTED_MIN_H = 380
    SELECTED_ART = 48
    SELECTED_DETAIL_H = 96


class SPACE:
    """间距阶梯：全部为 4 的倍数，避免"3px 和 5px 混用"造成的视觉噪音。"""

    XXS = 2
    XS = 4
    S = 6
    M = 8
    L = 12
    XL = 16
    XXL = 24
    XXXL = 32


class RADIUS:
    """圆角阶梯：容器大圆角、控件中圆角、标签小圆角。"""

    PILL = 999
    CARD = 14
    PANEL = 10
    CONTROL = 8
    CHIP = 6


class FONT:
    """字号阶梯（相对 12pt 正文）。"""

    FAMILY = FONT_FAMILY
    TINY = 11
    SMALL = 12
    BODY = 13
    SUBTITLE = 15
    TITLE = 17
    HEADLINE = 20
    DISPLAY = 24


class SIZE:
    """常用控件尺寸。"""

    BUTTON_H = 34
    BUTTON_H_SMALL = 26
    INPUT_H = 30
    ROW_H = 26
    HEADER_H = 30
    ICON_BUTTON_W = 34
    COVER = (300, 250)


class FORM:
    """Opt-in form metrics; existing pages keep their current geometry."""

    CONTROL_H = 34
    CHECK_SIZE = 20
    MENU_W = 200
    STACK_AT = 580
    CHECK_COLUMNS_AT = 640
    FOCUS_WIDTH = 2
    REFLOW_MS = 80


# ======================================================================================
# 语义色板
# ======================================================================================
@dataclass(frozen=True)
class Palette:
    """一套完整的语义配色。

    命名规则（沿用 Material 3 / Fluent 的语义命名）：
        BG_*       背景层：窗口底 -> 卡片 -> 悬浮
        SURFACE_*  容器层
        TEXT_*     文字层级：主/次/弱/反白
        BORDER_*   描边
        ACCENT     品牌主色（主要动作）
        *_FG       对应色的前景/文字色
    """

    name: str
    # 背景层
    BG_APP: str            # 窗口底色
    BG_CARD: str           # 卡片/分区底色
    BG_ELEVATED: str       # 悬浮层（下拉、菜单）
    BG_INPUT: str          # 输入框底色
    BG_HOVER: str          # 悬停底色
    BG_SELECTED: str       # 选中行底色
    BG_ZEBRA: str          # 斑马纹（偶数行）
    # 文字
    TEXT_PRIMARY: str
    TEXT_SECONDARY: str
    TEXT_MUTED: str
    TEXT_ON_ACCENT: str
    #: 深色主题的主色偏亮，白字压不住（实测 3.28:1），所以主按钮文字单独给色。
    ACCENT_TEXT: str
    # 描边
    BORDER: str
    BORDER_STRONG: str
    # 语义色
    ACCENT: str            # 主色（主要按钮/焦点）
    ACCENT_HOVER: str
    ACCENT_SOFT: str       # 主色的浅底（chip 背景）
    SUCCESS: str
    SUCCESS_HOVER: str
    WARNING: str
    WARNING_HOVER: str
    DANGER: str
    DANGER_HOVER: str
    DANGER_SOFT: str       # 高亮"缺失"行用的浅底
    INFO: str
    NEUTRAL: str
    NEUTRAL_HOVER: str
    # 列表行
    ROW_SELECTED: str
    ROW_DIVIDER: str
    STAR: str
    SUCCESS_SOFT: str
    SUCCESS_TEXT: str
    WARNING_SOFT: str
    WARNING_TEXT: str
    ROW_TEXT: str


DARK = Palette(
    name="dark",
    BG_APP="#111317",
    BG_CARD="#1c1f26",
    BG_ELEVATED="#272b34",
    BG_INPUT="#121418",
    BG_HOVER="#2d323d",
    BG_SELECTED="#2b4a78",
    BG_ZEBRA="#20242c",
    TEXT_PRIMARY="#eef1f6",
    TEXT_SECONDARY="#a9b2c0",
    TEXT_MUTED="#8a93a3",
    TEXT_ON_ACCENT="#d7e4ff",
    ACCENT_TEXT="#0e1420",
    BORDER="#3a4150",
    BORDER_STRONG="#626d80",
    ACCENT="#4b8cf7",
    ACCENT_HOVER="#3a78e0",
    ACCENT_SOFT="#1f3a63",
    SUCCESS="#22c55e",
    SUCCESS_HOVER="#1ea34d",
    WARNING="#f59e0b",
    WARNING_HOVER="#d1830a",
    DANGER="#ef4444",
    DANGER_HOVER="#cf3a3a",
    DANGER_SOFT="#4a2027",
    INFO="#8b5cf6",
    NEUTRAL="#565e6d",
    NEUTRAL_HOVER="#656e7f",
    ROW_SELECTED="#233e61", ROW_DIVIDER="#292e39", STAR="#ffbc38",
    SUCCESS_SOFT="#173c2d", SUCCESS_TEXT="#68dfae",
    WARNING_SOFT="#463516", WARNING_TEXT="#ffc45b",
    ROW_TEXT="#dfe4ec",
)

#: 浅色主题的**分层色阶**：一张卡片是白的，但它坐在灰底上，而层次之间
#: 必须有肉眼可辨的落差。改动这些值前先跑 `tests/theme_contrast.py`。
LIGHT = Palette(
    name="light",
    BG_APP="#e6eef7",
    BG_CARD="#ffffff",
    BG_ELEVATED="#ffffff",
    BG_INPUT="#eef1f6",
    BG_HOVER="#e4e9f1",
    BG_SELECTED="#bcd4ff",
    BG_ZEBRA="#f5f7fa",
    TEXT_PRIMARY="#161a20",
    TEXT_SECONDARY="#414a57",
    TEXT_MUTED="#616b7a",
    TEXT_ON_ACCENT="#ffffff",
    ACCENT_TEXT="#ffffff",
    BORDER="#ccd3e0",
    BORDER_STRONG="#8793a9",
    ACCENT="#2563eb",
    ACCENT_HOVER="#1d4fd0",
    ACCENT_SOFT="#dbe9ff",
    SUCCESS="#16a34a",
    SUCCESS_HOVER="#128a3e",
    WARNING="#d97706",
    WARNING_HOVER="#b86505",
    DANGER="#dc2626",
    DANGER_HOVER="#b91c1c",
    DANGER_SOFT="#fde8e8",
    INFO="#7c3aed",
    NEUTRAL="#6b7280",
    NEUTRAL_HOVER="#565d6b",
    ROW_SELECTED="#e4f2ff", ROW_DIVIDER="#edf2f8", STAR="#f4a900",
    SUCCESS_SOFT="#dcf8ec", SUCCESS_TEXT="#176b4b",
    WARNING_SOFT="#fff0cf", WARNING_TEXT="#9a5707",
    ROW_TEXT="#232830",
)


def current_mode() -> str:
    """当前外观（"Light" / "Dark"）。ctk 在 System 模式下会返回解析后的结果。"""
    try:
        import customtkinter as ctk
        return ctk.get_appearance_mode()  # type: ignore[return-value]
    except Exception:
        return "Dark"


def get_palette(mode: str | None = None) -> Palette:
    """按当前外观取色板。"""
    resolved = (mode or current_mode()).lower()
    return LIGHT if resolved.startswith("l") else DARK


def pair(dark_hex: str, light_hex: str) -> tuple[str, str]:
    """ctk 需要 (浅色, 深色) 顺序的元组 —— 这里统一封装，避免顺序写反。"""
    return (light_hex, dark_hex)


# ======================================================================================
# 语义色令牌（ctk 元组形式：跟随主题自动切换，无需手动刷新）
# ======================================================================================
def _make_tokens() -> dict[str, tuple[str, str]]:
    tokens: dict[str, tuple[str, str]] = {}
    for field in fields(Palette):
        if field.name == "name":
            continue
        light_value = getattr(LIGHT, field.name)
        dark_value = getattr(DARK, field.name)
        tokens[field.name] = (light_value, dark_value)
    return tokens


#: 语义令牌，例如 `T["BG_CARD"]` 可直接传给 ctk 的 fg_color。
#: 之所以不直接用 `from .theme import BG_CARD`，是因为显式表查更容易被 IDE/类型检查发现拼写错误。
T: dict[str, tuple[str, str]] = _make_tokens()

# Opt-in control states. Dark primary hover keeps normal-size text above 4.5:1.
FORM_COLORS = {
    "PRIMARY_HOVER": (LIGHT.ACCENT_HOVER, DARK.ACCENT),
    "FOCUS": T["ACCENT"],
    "DISABLED_TEXT": T["TEXT_MUTED"],
}


def color(role: str, mode: str | None = None) -> str:
    """取某个语义色在当前外观下的**具体值**（ttk / tk 控件需要真实色值）。"""
    palette = get_palette(mode)
    if role == "name" or not hasattr(palette, role):
        raise KeyError(f"未知的颜色语义: {role}")
    return str(getattr(palette, role))


def transparent() -> str:
    """ctk 的"透明"取值。"""
    return "transparent"


# ======================================================================================
# 高 DPI / 界面缩放（ROADMAP C1）
# ======================================================================================
#: 可选档位。"auto" = 按系统 DPI 自动推测。
UI_SCALE_CHOICES: tuple[str, ...] = ("auto", "1.0", "1.25", "1.5", "2.0")

#: 缩放倍率的合理区间（挡住手改坏的值）
_MIN_SCALE, _MAX_SCALE = 1.0, 3.0


def detect_ui_scale(root=None) -> float:
    """按系统 DPI 推测界面缩放（96 DPI = 100%）。

    Windows 上常见 125% / 150% / 200%。**必须传 root** ——
    单独造一个临时 Tk 来量 DPI 会在某些环境下多闪一个窗口，不值得。
    拿不到 DPI 就老老实实返回 1.0。
    """
    if root is None:
        return 1.0
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        return 1.0
    if dpi <= 0:
        return 1.0
    return max(_MIN_SCALE, min(_MAX_SCALE, round(dpi / 96.0, 2)))


def apply_ui_scale(setting: str, root=None) -> float:
    """按设置应用界面缩放，返回**实际**使用的倍率。

    ⚠️ `customtkinter` 的缩放是**全局状态**，只影响**之后创建**的控件 ——
    所以必须在建界面之前调用，否则会出现"一半控件按新倍率、一半按旧倍率"。

    `setting` 为 `"auto"` 或空/非法值时按系统 DPI 自动推测。
    """
    if setting in UI_SCALE_CHOICES and setting != "auto":
        factor = float(setting)
    else:
        factor = detect_ui_scale(root)
    factor = max(_MIN_SCALE, min(_MAX_SCALE, factor))
    try:
        import customtkinter as ctk
        ctk.set_widget_scaling(factor)
        ctk.set_window_scaling(factor)
    except Exception:  # 缩放设置失败不该让程序起不来
        return 1.0
    return factor


# ======================================================================================
# emoji 字体预热（v17.9 启动耗时优化）
# ======================================================================================
#: 界面启动阶段会用到的 emoji 图标。
#:
#: ⚠️ **必须逐个列全**：实测字体回退是**按码点分别查找**的，每个**不同的**
#: emoji 首次渲染都要各付一次 ~270 ms（🎵/💻/🥽/🔌/📥/🗑 实测都是 261~279 ms），
#: 只有 ⚠️ 稍便宜（110 ms，因为它是 BMP 码点 + 变体选择符）。
#: 所以只预热一个字符是**不够的** —— 那只是把"第一个 emoji"的账提前付了。
#:
#: 实测（独立进程，13 个图标挨个建 label 的总耗时）：
#:   不预热 414.6 ms ｜ 只预热 🎵 123.7 ms ｜ **预热全部 54.7 ms**
#: 预热自身的成本：全部 377 ms vs 单个 297 ms（一次多付 80 ms，换来 290 ms）。
#:
#: 这份清单要和 `app_common.COLUMN_IDENTITIES` 与 `messages.py` 的按钮图标保持一致；
#: 漏掉的不算错（只是那一个仍要现场付），多列的也不算错（代价只是一次 measure）。
EMOJI_WARMUP_TEXT = "🎵💻🥽⚠️🔌📶🔄⚙️📥📱🔍📤🗑"


def warm_emoji_font(root, family: str | None = None, size: int | None = None) -> float:
    """预热 emoji 字形，返回**本次预热实际耗时（毫秒）**。

    ## 为什么需要这个（实测数据，不是推测）

    启动时**第一个** emoji 要 **277~315 ms**，而第二个 emoji 只要 **6~8 ms**；
    同一时刻中文只要 3~11 ms、ASCII 只要 1~3 ms。也就是说这是一次性成本：
    Tk 要为 emoji 定位字体（Windows 上是 Segoe UI Emoji）并做字形回退匹配。
    cProfile 显示这 300 ms 里 305/306 是 **Tcl 往返**（30 次），
    连裸 `tk.Label` 带 🎵 也要 275 ms —— 是 Tk 的行为，不是 customtkinter 的。

    它原先全落在 `_build_appbar` 里那个带 🎵 的标题 label 上
    （实测中位 **461.9 ms → 165.8 ms**，5/5 轮全为正）。

    ## ⚠️ 结论：这是"把成本搬到看不见的地方"，**不是**加速总启动

    实测（交替采样、独立进程、每臂 4 次，中位数）：

    | 阶段 | 不预热 | 预热 | 差 |
    |---|---|---|---|
    | `_build_ui` | 1220 ms | **906 ms** | **−314 ms（−26%）** |
    | 建界面之前（含预热自身） | 232 ms | 601 ms | +369 ms |
    | `__init__` 总计 | 1452 ms | 1512 ms | **+60 ms（略慢）** |

    也就是说：**预热并没有让程序启动得更快**（总耗时甚至略增，因为预热要走
    一遍 Font 构造 + 13 次 measure）。它换来的是 ——
    **建界面那 314 ms 的停顿被挪到了"还没有任何控件"的阶段**。

    ## 那为什么还要做？（诚实评估）

    收益是**局部且可测**的：`_build_appbar` 从 425 ms 降到 141 ms（−284 ms，−67%），
    这是界面上第一个"看得见内容"的步骤。挪走的成本发生在 `withdraw()` 之前、
    还没建任何控件时，用户看到的是更短的"白屏"，而不是"标题卡一下"。

    ⚠️ 但要如实说明：**本机无法测出真正的首帧延迟**。
    窗口是 `withdraw()` 的，真正的绘制被 Tk 推迟到 `deiconify()` 之后，
    所以"用户感受到的等待"**没有**被直接量到（`update_idletasks` 恒为 0.0 ms）。
    因此不要把这里说成"启动快了 300 ms" —— 没有证据支持那个说法。
    要达到那个效果，必须继续动 `_build_lists`（1220 ms 里的大头），本轮没做。

    ## 实现约束

    ⚠️ 只做量测（`measure`），不创建控件、不改任何全局状态，因此**不影响界面外观**。
    失败时静默返回 0.0 —— 预热是优化，绝不能因为它让程序起不来。
    """
    import time as _time

    if root is None:
        return 0.0
    started = _time.perf_counter()
    try:
        import tkinter.font as tkfont

        # 字体族由调用方传（app 里已经 `resolve_font(root)` 算好了）；
        # 没传就交给 Tk 用当前默认族 —— 这里**不**从 constants 取 FONT_FAMILY，
        # 因为 theme 是最底层模块，不该反向依赖 constants 的字体策略。
        font = tkfont.Font(root=root, size=size or FONT.HEADLINE, weight="bold",
                           **({"family": family} if family else {}))
        # 关键：把每个 emoji 都量一次。这一步触发字体回退查找，正是那 ~270ms/个 的来源。
        for char in EMOJI_WARMUP_TEXT:
            font.measure(char)
    except Exception:  # 预热失败不影响任何功能
        return 0.0
    return (_time.perf_counter() - started) * 1000.0
