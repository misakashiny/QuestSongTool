"""
tk_inspector —— Tkinter 控件标注工具

用途：给自己的 Tkinter 程序做「控件标注」，用于向他人说明"要改哪个控件、在哪里、多大"。

与 UIA 工具的区别：
    UIA 工具从程序外部读取，Tkinter 只暴露 TkTopLevel/TkChild 这类通用容器，拿不到名称。
    本工具在程序内部通过 winfo_children() 读取真实控件树，
    因此能拿到控件类名、变量名、坐标、尺寸、文本等完整信息。

两种用法：

  用法一（推荐，改一行代码）：在你的程序里调用
      import tk_inspector
      tk_inspector.attach(root)          # root 是 Tk 实例（或任意 Toplevel）
      # 程序正常运行；按 Ctrl+Alt+I 打开标注模式

  用法二（独立扫描，不改代码）：
      python tk_inspector.py --scan                 # 扫描当前所有 Tk 窗口并导出
      python tk_inspector.py --scan --watch         # 每 2 秒刷新一次

标注模式下：
    · 鼠标悬停控件 → 高亮框（细边框 + 四角标记）+ 紧凑信息卡片
    · 信息卡片默认显示三项：控件名、大小、代码位置
    · 左键点击 → 选中该控件，信息固定显示
    · Tab / F2 → 在浮层里切换简略 / 详细（检查器优先处理按键）
    · Ctrl+Alt+D → 打开可滚动、可复制的只读完整属性窗口
    · Ctrl+Alt+I → 开关标注模式
    · Ctrl+Alt+E → 导出当前结果（JSON + Markdown）
    · Esc → 取消选中

浮层不挡鼠标：两个浮层窗口都设了 WS_EX_TRANSPARENT（Windows 扩展样式），
鼠标事件直接穿透到下面真正的控件。实测 -alpha 半透明与 -transparentcolor 抠色
都无法让"实心卡片"穿透（见 README「鼠标穿透实测」）。
"""

from __future__ import annotations

import json
import math
import sys
import time
import traceback
import weakref
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import ttk

try:  # Tk 的字体度量模块（标准库 tkinter 自带，不是第三方）
    from tkinter import font as tkfont
except Exception:  # pragma: no cover - 极少数裁剪过的 Tk 构建
    tkfont = None  # type: ignore[assignment]

__version__ = "1.2.0"


def _inspector_text(key: str) -> str:
    if __package__:
        from .messages import tr
        return tr("inspector." + key)
    return {"title": "Widget properties", "copy": "Copy properties", "copied": "Copied",
            "hint": "Tab/F2: compact or detailed | Ctrl+Alt+D: all properties",
            "manager": "Layout", "layout": "Layout options", "parent": "Parent",
            "class": "Python class", "options": "Style options", "empty": "Hover a widget first"}[key]


def _property_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (tuple, list)):
        return [_property_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _property_value(item) for key, item in value.items()}
    return str(value)


def _setup_console_encoding() -> None:
    """Windows 控制台默认可能是 GBK，打印中文/emoji 会报错或乱码。尽量切到 UTF-8。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_setup_console_encoding()

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class WidgetInfo:
    """一个控件的可读描述。字段名尽量与 WinUI Inspector 导出保持一致，便于对照。"""

    index: int
    path: str                       # 控件在树中的路径，如 root.frame_main.btn_save
    widget_class: str               # Tk 控件类，如 Button / Label / Frame
    tk_name: str                    # Tk 内部名，如 .!frame2.!button3
    var_name: str | None = None     # 在 Python 里的变量名（能追踪到时）
    code_location: str | None = None  # 创建该控件的代码位置 file:line
    text: str | None = None         # 控件文本
    x: int = 0                      # 相对父容器
    y: int = 0
    width: int = 0
    height: int = 0
    abs_root_x: int = 0             # 相对根窗口
    abs_root_y: int = 0
    screen_x: int = 0               # 屏幕物理像素
    screen_y: int = 0
    is_visible: bool = True
    is_enabled: bool = True
    state: str | None = None
    font: str | None = None
    fg: str | None = None
    bg: str | None = None
    depth: int = 0
    python_class: str = ""
    parent: str = ""
    manager: str = ""
    layout: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)

    def display_label(self) -> str:
        """树节点 / 浮层标题用的简短标签。"""
        name = self.text.strip() if self.text and self.text.strip() else "（无文本）"
        if len(name) > 28:
            name = name[:28] + "…"
        return f"{name} — {self.widget_class}"

    def geometry_text(self) -> str:
        return f"{self.width}×{self.height} @ ({self.x}, {self.y})"


@dataclass
class WindowAudit:
    """一个顶层窗口的完整审计结果。"""

    window_title: str
    window_class: str
    root_geometry: str
    screen_width: int
    screen_height: int
    dpi_scale: float
    captured_at: str
    widgets: list[WidgetInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def widget_count(self) -> int:
        return len(self.widgets)


# ---------------------------------------------------------------------------
# 遍历与信息采集
# ---------------------------------------------------------------------------


def _safe(obj: Any, getter, default=None):
    """安全调用 Tk 方法：控件可能已销毁或属性不可用。"""
    try:
        value = getter()
        return default if value is None else value
    except Exception:
        return default


def _widget_text(widget: tk.Misc) -> str | None:
    """尽力读取控件文本。不同控件用的属性和方法不一样。"""
    # 先用 cget('text')（Button/Label/Checkbutton/Radiobutton 等）
    for option in ("text", "label", "name"):
        value = _safe(widget, lambda o=option: widget.cget(o))
        if isinstance(value, str) and value:
            return value

    # Entry/Text 用 get()
    getter = getattr(widget, "get", None)
    if callable(getter):
        value = _safe(widget, getter)
        if isinstance(value, str) and value:
            return value if len(value) <= 200 else value[:200] + "…"

    # ttk.Notebook 的 tab 标题
    tabs = getattr(widget, "tabs", None)
    if callable(tabs):
        titles = _safe(widget, lambda: [widget.tab(t, "text") for t in widget.tabs()], [])
        if titles:
            return " / ".join(str(t) for t in titles)

    return None


def _widget_path(widget: tk.Misc, root: tk.Misc) -> str:
    """把 Tk 内部名（.!frame2.!button3）转成可读路径（root.frame2.button3）。

    有变量名时用变量名，让路径更接近代码里的样子。
    无变量名时用"父级变量名 + 类名"，例如：
        iconbutton.ctkcanvas → 挂在 list_local 下就是 list_local.shape1
    这样即使控件是循环批量创建的（没有独立变量名），路径也能定位到大致位置。
    """
    parts: list[str] = []
    node: tk.Misc | None = widget
    while node is not None and node is not root:
        parts.append(_node_segment(node))
        parent = getattr(node, "master", None)
        node = parent if isinstance(parent, tk.Misc) else None
    parts.reverse()
    root_seg = _safe(root, lambda: root.winfo_class(), "root").lower()
    return ".".join([root_seg, *parts]) if parts else root_seg


def _node_segment(widget: tk.Misc) -> str:
    var = _find_var_name(widget)
    if var:
        return var

    tk_name = _safe(widget, lambda: widget.winfo_name(), "widget") or "widget"
    seg = str(tk_name).lstrip(".").lstrip("!")

    # 内部名常是 button3 / ctkcanvas 这类，加上控件类更容易看懂
    cls = _safe(widget, lambda: widget.winfo_class(), "")
    if cls and cls.lower() not in seg.lower():
        seg = f"{seg}[{cls}]"
    return seg


_var_cache: dict[int, str] = {}

# 挂载时从调用栈抓到的 id(widget) → 变量名，作为兜底来源
_registry_var_names: dict[int, str] = {}

# 挂载时从调用栈抓到的 id(widget) → 源码文件路径
_registry_frames: dict[int, str] = {}

_registry_source_files: list[str] = []

# 是否允许扫描调用栈来推断变量名。
# 遍历控件树时必须关闭：栈上的循环变量（for child in children）会被误认成控件变量名，
# 实测会让所有 ctk 内部 Canvas/Label 都被"赋名"，导致过滤失效、报告混入 600 个噪声项。
_STACK_SCAN_ENABLED = True


def snapshot_caller_vars(depth: int = 10) -> int:
    """在挂载/导出时扫描调用栈，把「控件对象 → 变量名」记录下来。

    必须显式调用：Tkinter 控件对象不保存变量名，只能在仍有栈帧引用它的时候抓。

    覆盖两类最常见写法：
        局部变量：  btn = ttk.Button(...)
        实例属性：  self.btn = ttk.Button(...)      ← 类封装应用的主力写法
        全局变量：  模块级 btn = ttk.Button(...)

    返回本帧新发现的数量。
    """
    found = 0
    frame = sys._getframe(1)
    levels = 0
    while frame is not None and levels < depth:
        try:
            items = list(frame.f_locals.items())
        except Exception:
            items = []

        for name, value in items:
            if name.startswith("_"):
                continue

            # 情况 1：变量本身就是控件
            if _is_widget(value):
                found += _register(id(value), name, frame)

            # 情况 2：变量是对象（多半是 self / app），扫描它的属性找控件
            elif _looks_like_owner(value):
                found += _scan_owner_attributes(value, frame)

        # 全局作用域也要扫（模块级控件）
        try:
            gitems = list(frame.f_globals.items())
        except Exception:
            gitems = []
        for name, value in gitems:
            if name.startswith("_") or _is_widget(value):
                if _is_widget(value):
                    found += _register(id(value), name, frame)
                continue
            if _looks_like_owner(value) and getattr(value, "__name__", None) is None:
                found += _scan_owner_attributes(value, frame)

        # 记录源码文件，供后续定位代码行
        filename = frame.f_code.co_filename
        if filename and not _is_tool_file(filename) and filename not in _registry_source_files:
            _registry_source_files.append(filename)

        frame = frame.f_back
        levels += 1
    return found


def _register(widget_id: int, name: str, frame) -> int:
    """登记一个控件变量名；已登记的不覆盖（先到先得，通常是最外层作用域）。"""
    if widget_id in _registry_var_names:
        return 0
    _registry_var_names[widget_id] = name
    _registry_frames.setdefault(widget_id, frame.f_code.co_filename)
    return 1


def _looks_like_owner(value: Any) -> bool:
    """判断对象是否值得扫描属性（排除模块、类型、函数、字符串等）。"""
    if value is None or isinstance(value, (str, bytes, int, float, bool, tuple, list, dict, set)):
        return False
    if isinstance(value, type) or callable(value):
        return False
    if _is_widget(value):
        return False
    return hasattr(value, "__dict__")


def _scan_owner_attributes(owner: Any, frame) -> int:
    """扫描对象属性，找出里面的控件（self.btn / self.frame 等）。"""
    found = 0
    try:
        attrs = list(vars(owner).items())
    except Exception:
        return 0

    for name, value in attrs:
        if name.startswith("__"):
            continue
        if _is_widget(value):
            found += _register(id(value), name, frame)
        # 一层嵌套：self.panel.btn 这类（只往下走一层，避免遍历整个对象图）
        elif _looks_like_owner(value) and not _is_tk_variable(value):
            try:
                for sub_name, sub_value in list(vars(value).items()):
                    if sub_name.startswith("__"):
                        continue
                    if _is_widget(sub_value):
                        found += _register(id(sub_value), f"{name}.{sub_name}", frame)
            except Exception:
                pass
    return found


def _is_tk_variable(value: Any) -> bool:
    """Tk 变量对象（StringVar / IntVar 等）不是控件，也没有我们要的属性。"""
    return isinstance(value, tk.Variable)


def _is_widget(value: Any) -> bool:
    """判断一个对象是不是 Tk 控件（排除 Tk 变量类等）。"""
    try:
        return isinstance(value, tk.Misc)
    except Exception:
        return False


def _find_var_name(widget: tk.Misc) -> str | None:
    """尝试找到控件在调用方代码中的变量名。

    优先级：
      1. 挂载时登记的变量名（最可靠，能覆盖创建后不再出现在栈里的控件）
      2. 当前调用栈扫描（仅在未锁定栈扫描时使用）

    ⚠️ 栈扫描必须能关闭：遍历控件树时，栈上必然有 ``for child in ...`` 这类循环变量，
    它也会命中 ``value is widget``，把内部控件误认成"用户变量"。
    """
    key = id(widget)
    if key in _registry_var_names:
        return _registry_var_names[key]

    cached = _var_cache.get(key)
    if cached is not None:
        return cached

    if not _STACK_SCAN_ENABLED:
        return None

    frame = sys._getframe(1)
    while frame is not None:
        try:
            for name, value in list(frame.f_locals.items()):
                if value is widget and not name.startswith("_"):
                    _var_cache[key] = name
                    return name
        except Exception:
            pass
        frame = frame.f_back

    return None


def _assigns_name(line: str, var: str) -> bool:
    """判断这一行是否把值赋给了 var。

    支持：
        var = ...
        self.var = ...
        obj.var = ...
        obj.sub.var = ...
    不接受 var2 = ... 这类前缀相同但不是同一变量的情况。
    """
    for prefix in ("", "self.", "obj.", "app.", "cls."):
        head = f"{prefix}{var}"
        if line.startswith(head):
            rest = line[len(head):]
            if rest.startswith("=") or rest.startswith(" :="):
                return True
            if rest.startswith(" ") and rest.lstrip().startswith("="):
                return True
            if "=" in rest and rest.lstrip().startswith("="):
                return True
    # 通用：任意 xxx.var = ...
    if "." in line:
        head, sep, rest = line.partition("=")
        if sep and head.strip().endswith(f".{var}"):
            return True
    return False


def _code_location(widget: tk.Misc) -> str | None:
    """尝试定位创建该控件的代码位置。

    Tkinter 控件对象不记录创建位置。做法：
      1. 拿到变量名（来自挂载时登记，最可靠）
      2. 在已知源码文件里搜索赋值给该变量的行
      3. 优先选"该行明显在构造控件"的匹配；没有的话退而求其次找第一次赋值

    真实代码里控件可能这样创建：
        btn = ttk.Button(...)              # 直接构造
        btn = make_button(...)             # 工厂函数
        self.buttons[i] = tk.Button(...)   # 索引赋值（变量名无法追踪，靠 tk_name）
    所以"含 tk./ttk." 不能作为硬性条件。
    """
    var = _find_var_name(widget)
    if not var:
        return None

    # 顺序很重要：先查用户程序的文件，工具自身文件与无关文件都不参与。
    # 踩过的坑：扫目录下所有 .py 会把别的测试脚本/库文件也当候选，产生错误行号。
    # 错误行号比没有行号更糟，所以候选文件必须严格限定。
    candidates: list[str] = []

    # 1. 登记到该控件的源码文件（最可靠：来自实际持有该控件的栈帧）
    owner_file = _registry_frames.get(id(widget))
    if owner_file and not _is_tool_file(owner_file):
        candidates.append(owner_file)

    # 2. 主脚本文件
    main_file = _main_script_file()
    if main_file and main_file not in candidates and not _is_tool_file(main_file):
        candidates.append(main_file)

    if not candidates:
        return None

    # 1 = 明确构造控件；2 = 普通赋值（可能来自工厂函数）
    strong: list[tuple[str, int]] = []
    weak: list[tuple[str, int]] = []

    widget_markers = (
        "tk.", "ttk.", "Widget", "Canvas", "Text(", "Frame(", "Label(", "Button(",
        "Entry(", "Listbox(", "Menu(", "Scrollbar(", "Toplevel(", "Combobox(",
        "Checkbutton(", "Radiobutton(", "Spinbox(", "Treeview(", "Notebook(",
        "PanedWindow(", "Progressbar(", "Scale(", "Message(", "Labelframe(",
        "LabelFrame(", "Pane(",
    )

    for path in candidates[:80]:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue

                    # 支持三种写法：
                    #   btn = ttk.Button(...)         局部变量
                    #   self.btn = ttk.Button(...)    实例属性（类封装的主力）
                    #   panel.btn = ...               嵌套属性
                    if not (_assigns_name(stripped, var)):
                        continue
                    if any(marker in stripped for marker in widget_markers):
                        strong.append((path, lineno))
                    else:
                        weak.append((path, lineno))
                    if len(strong) > 5 and len(weak) > 5:
                        break
        except OSError:
            continue

    def render(matches: list[tuple[str, int]]) -> str | None:
        if not matches:
            return None
        if len(matches) == 1:
            path, lineno = matches[0]
            return f"{Path(path).name}:{lineno}"
        # 多处同名赋值：给出第一处并说明还有几处，不假装唯一
        path, lineno = matches[0]
        return f"{Path(path).name}:{lineno} (+{len(matches) - 1})"

    return render(strong) or render(weak)


_SCRIPT_CACHE: list[str] | None = None


def _is_tool_file(path: str) -> bool:
    """判断是否为本工具自身的文件，或根本不应参与搜索的文件。

    注意：只排除"本工具模块自身"和 Python 标准库/第三方库。
    不能排除 test_*.py —— 那正是调用本工具的验证脚本，用户也可能用这种方式组织代码。
    """
    try:
        name = Path(path).name.lower()
    except Exception:
        return True

    # 本工具模块自身（其中也有形如 btn = ttk.Button(...) 的演示代码，会误导定位）
    if name == "tk_inspector.py":
        return True

    # Python 标准库 / 第三方包
    if name == "__init__.py":
        return True
    lowered = str(path).lower().replace("/", "\\")
    if "site-packages" in lowered or "\\python3" in lowered or "\\programs\\python" in lowered:
        return True

    return False


def _main_script_file() -> str | None:
    """主脚本的绝对路径，如 D:\\proj\\app.py。"""
    try:
        if not sys.argv or not sys.argv[0]:
            return None
        path = Path(sys.argv[0])
        if path.suffix.lower() != ".py":
            return None
        resolved = path.resolve()
        return str(resolved) if resolved.is_file() else None
    except Exception:
        return None


def _dpi_scale(root: tk.Misc) -> float:
    """估算窗口 DPI 缩放（用于把 Tk 逻辑像素换算到屏幕物理像素）。"""
    try:
        return float(root.winfo_fpixels("1i")) / 96.0
    except Exception:
        return 1.0


def _layout_ready(root: tk.Misc) -> bool:
    """判断布局是否已经算完（可以信任 winfo_width/height）。

    刚创建的窗口在 update()/mainloop 之前不会映射，所有控件都是 1x1。
    这种情况下必须告知使用者"几何不可信"，而不是输出一堆 1x1 当真实尺寸。
    """
    try:
        # 任何控件被映射过，说明布局跑过一轮
        if not bool(root.winfo_ismapped()):
            return False
        width = int(root.winfo_width())
        height = int(root.winfo_height())
        return width > 1 and height > 1
    except Exception:
        return False


def collect_widgets(
    root: tk.Misc,
    max_depth: int = 30,
    include_internal: bool = False,
    include_ctk_internal: bool = False,
) -> list[WidgetInfo]:
    """递归遍历控件树，采集每个控件的信息。

    采集前会尝试 update_idletasks() 让布局跑完。若窗口尚未映射（未 update/mainloop），
    几何值会是 1x1，调用方可通过 WindowAudit.warnings 得知几何不可信。

    参数：
        include_internal     包含 Tk 内部辅助控件（Menu/Toplevel，默认不含）
        include_ctk_internal 包含 CustomTkinter 内部实现控件（Canvas/Label 等，
                             默认不含——实测会让报告从 29 个变成 629 个）
    """
    _var_cache.clear()

    # 禁用栈扫描：遍历时栈上有 for/循环变量，会被误认成控件变量名，
    # 导致 ctk 内部控件全部"有名字"从而过滤失效（实测 629 vs 29）。
    global _STACK_SCAN_ENABLED
    _prev_scan = _STACK_SCAN_ENABLED
    _STACK_SCAN_ENABLED = False

    # 尽力让布局跑完。
    # update_idletasks() 只算"待处理空闲任务"，在窗口尚未映射时拿不到真实几何。
    # 因此再做一次补救：若窗口还没映射，调用 update() 让它真正显示出来并完成一轮布局。
    # 注意：update() 会处理事件队列，是 Tk 的标准用法；只在未映射时调用，
    # 避免在 mainloop 运行中反复触发重入导致卡顿。
    try:
        root.update_idletasks()
    except Exception:
        pass

    try:
        if not bool(root.winfo_ismapped()):
            root.update()
            root.update_idletasks()
    except Exception:
        pass

    results: list[WidgetInfo] = []
    scale = _dpi_scale(root)
    root_x = _safe(root, lambda: root.winfo_rootx(), 0) or 0
    root_y = _safe(root, lambda: root.winfo_rooty(), 0) or 0

    def walk(widget: tk.Misc, depth: int) -> None:
        if depth > max_depth or len(results) > 20000:
            return

        # 过滤 Tkinter 内部控件（如绑定浮层的 widget）
        if not include_internal and _is_internal(widget):
            return

        # 先把变量名查到并缓存，供后面的 ctk 内部过滤使用
        var_name = _find_var_name(widget)
        if var_name:
            _var_cache[id(widget)] = var_name

        # 过滤 CustomTkinter 内部实现控件（Canvas/内部 Label 等）。
        # 注意：即使跳过该控件本身，也要继续往下走它的子控件，
        # 因为 ctk 的业务控件常挂在这类容器的子树里。
        skip_self = (not include_ctk_internal) and _is_ctk_internal(widget)
        if skip_self:
            for child in _safe(widget, lambda: list(widget.winfo_children()), []) or []:
                walk(child, depth)
            return

        w = _safe(widget, lambda: int(widget.winfo_width()), 0) or 0
        h = _safe(widget, lambda: int(widget.winfo_height()), 0) or 0
        x = _safe(widget, lambda: int(widget.winfo_x()), 0) or 0
        y = _safe(widget, lambda: int(widget.winfo_y()), 0) or 0
        abs_x = _safe(widget, lambda: int(widget.winfo_rootx()), root_x) or root_x
        abs_y = _safe(widget, lambda: int(widget.winfo_rooty()), root_y) or root_y
        mapped = bool(_safe(widget, lambda: widget.winfo_ismapped(), False))

        info = WidgetInfo(
            index=len(results),
            path=_widget_path(widget, root),
            widget_class=_safe(widget, lambda: widget.winfo_class(), "?"),
            tk_name=str(_safe(widget, lambda: widget.winfo_name(), "?")),
            var_name=var_name,
            code_location=_code_location(widget),
            text=_widget_text(widget),
            x=x, y=y, width=w, height=h,
            abs_root_x=abs_x - root_x,
            abs_root_y=abs_y - root_y,
            screen_x=int(round(abs_x)),
            screen_y=int(round(abs_y)),
            is_visible=mapped and w > 0 and h > 0,
            is_enabled=not bool(_safe(widget, lambda: "disabled" in str(widget.cget("state")), False)),
            state=_safe(widget, lambda: str(widget.cget("state")), None),
            font=_safe(widget, lambda: str(widget.cget("font")), None),
            fg=_safe(widget, lambda: str(widget.cget("foreground")), None),
            bg=_safe(widget, lambda: str(widget.cget("background")), None),
            depth=depth,
        )
        results.append(info)

        info.python_class = type(widget).__module__ + "." + type(widget).__name__
        info.parent = str(getattr(widget, "master", ""))
        info.manager = _safe(widget, widget.winfo_manager, "")
        layout_reader = (getattr(widget, info.manager + "_info", None)
                         if info.manager in ("grid", "pack", "place") else None)
        if layout_reader:
            info.layout = _property_value(_safe(widget, layout_reader, {}))
        for option in ("text_color", "text_color_disabled", "fg_color", "bg_color", "hover_color",
                       "border_color", "border_width", "corner_radius", "width", "height",
                       "anchor", "justify", "wraplength", "cursor", "takefocus", "font",
                       "checkbox_width", "checkbox_height", "button_color", "progress_color"):
            value = _safe(widget, lambda key=option: widget.cget(key))
            if value is not None:
                info.options[option] = _property_value(value)
        if "font" in info.options:
            actual = _safe(widget, lambda: widget.tk.call("font", "actual", info.options["font"]))
            if actual:
                info.options["font_actual"] = _property_value(actual)
        info.fg = str(info.options.get("text_color", info.fg or ""))
        info.bg = str(info.options.get("fg_color", info.bg or ""))

        for child in _safe(widget, lambda: list(widget.winfo_children()), []) or []:
            walk(child, depth + 1)

    walk(root, 0)

    _STACK_SCAN_ENABLED = _prev_scan
    return results


_INTERNAL_CLASSES = {"Menu", "Toplevel"}


def _is_internal(widget: tk.Misc) -> bool:
    """判断是否是 Tkinter 内部辅助控件（不应出现在标注结果里）。"""
    try:
        cls = widget.winfo_class()
    except Exception:
        return True
    # ttk 的弹出列表、下拉等内部控件
    if cls in _INTERNAL_CLASSES:
        return True
    if isinstance(widget, tk.Toplevel):
        return True
    return False


# ---------------------------------------------------------------------------
# CustomTkinter 内部控件过滤
#
# CustomTkinter 的每个 CTkButton/CTkLabel/CTkFrame 都由"外层 Frame + Canvas + 内部 Label"
# 组成，这些内部子控件的变量名是 ctk 库自己的，对使用者毫无意义。
# 实测 Quest 工具：629 个元素里有 600 个是这种内部实现细节，只有 29 个是真正的业务控件。
# 不过滤的话报告完全没法看。
# ---------------------------------------------------------------------------

_CTK_CLASS_PREFIX = "ctk"
_CTK_INTERNAL_HINT = "customtkinter"


def _is_ctk_internal(widget: tk.Misc) -> bool:
    """判断是否是 CustomTkinter 内部实现控件（不是用户创建的控件）。

    CustomTkinter 的实际结构（实测 ctk 5.2.2）：

        CTkButton   Frame  (mod = ...widgets.ctk_button)    ← 用户创建的，要保留
          └ Canvas         (mod = ...core_rendering.ctk_canvas)  ← 内部画的，要过滤
          └ Label          (mod = tkinter)                        ← 内部画的，要过滤

    关键：Canvas 自己的 module 也带 customtkinter，所以**不能**用"是不是 ctk 类型"来判断。
    唯一可靠的判据是：**是不是用户亲手创建并赋给变量**。

    规则：
      1. 有用户变量名（registry 里有）→ 用户创建的，保留
      2. 父级是 ctk 控件且自己没有变量名 → 内部实现，过滤
      3. 其他情况保留（保守，宁可多显示也不误删用户的控件）
    """
    if _has_user_var_name(widget):
        return False

    parent = getattr(widget, "master", None)
    if parent is None or not _is_ctk_widget(parent):
        return False

    # 父级是 ctk、自己没有变量名 → ctk 内部画的 Canvas/Label
    return True


def _is_ctk_widget(widget: Any) -> bool:
    try:
        mod = getattr(type(widget), "__module__", "") or ""
        return _CTK_INTERNAL_HINT in mod.lower()
    except Exception:
        return False


def _has_user_var_name(widget: tk.Misc) -> bool:
    """该控件是否有用户可读的变量名（有就说明是用户自己创建的）。

    只查挂载时登记的结果，不扫调用栈——遍历发生在 attach 的后续流程里，
    栈上已经没有用户的 self/局部变量了，扫栈只会得到 None。
    """
    name = _registry_var_names.get(id(widget))
    if not name:
        name = _var_cache.get(id(widget))
    return bool(name) and not name.startswith("_")


def audit_window(root: tk.Misc, include_ctk_internal: bool = False) -> WindowAudit:
    """采集一个顶层窗口的完整审计信息。

    include_ctk_internal=True 时会包含 CustomTkinter 的内部实现控件（Canvas/内部 Label 等），
    一般用于排查 ctk 的渲染问题；日常标注不需要。
    """
    widgets = collect_widgets(root, include_ctk_internal=include_ctk_internal)
    scale = _dpi_scale(root)

    warnings: list[str] = []

    # 几何是否可信：窗口未映射时所有控件都是 1x1，必须如实告知而不是输出假数据
    if not _layout_ready(root):
        warnings.append(
            "窗口尚未完成布局（未进入 mainloop 或窗口未显示），本次采集的 "
            "width/height/坐标不可信（通常全为 1x1 / (0,0)）。"
            "请在窗口显示后再采集，例如用 root.after(300, ...) 延迟调用。"
        )

    no_name = sum(1 for w in widgets if not w.var_name)
    if no_name:
        warnings.append(
            f"{no_name}/{len(widgets)} 个控件没能追踪到 Python 变量名。"
            "若控件是临时创建的（未赋值给变量），这是预期结果。"
        )
    no_code = sum(1 for w in widgets if not w.code_location)
    if no_code:
        warnings.append(
            f"{no_code}/{len(widgets)} 个控件没能定位到代码行。"
            "可尝试把脚本和工作目录设为同一个，或在程序内直接调用 attach()。"
        )

    return WindowAudit(
        window_title=str(_safe(root, lambda: root.title(), "") or ""),
        window_class=_safe(root, lambda: root.winfo_class(), "?"),
        root_geometry=str(_safe(root, lambda: root.winfo_geometry(), "") or ""),
        screen_width=int(_safe(root, lambda: root.winfo_screenwidth(), 0) or 0),
        screen_height=int(_safe(root, lambda: root.winfo_screenheight(), 0) or 0),
        dpi_scale=round(scale, 3),
        captured_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        widgets=widgets,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def to_json(audit: WindowAudit) -> str:
    payload = {
        "schemaVersion": "1.0",
        "tool": {"name": "tk_inspector", "version": __version__},
        "capturedAt": audit.captured_at,
        "target": {
            "windowTitle": audit.window_title,
            "windowClass": audit.window_class,
            "rootGeometry": audit.root_geometry,
            "screenWidth": audit.screen_width,
            "screenHeight": audit.screen_height,
            "dpiScale": audit.dpi_scale,
        },
        "capture": {
            "widgetCount": audit.widget_count,
            "coordinateSpace": "tkLogicalPixels",
            "warnings": audit.warnings,
        },
        "widgets": [asdict(w) for w in audit.widgets],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Markdown 报告辅助：按区域分组 + ASCII 布局示意图
# ---------------------------------------------------------------------------

# 报告表格里各列的文本截断长度（超出用 "…"，避免一张表横向撑爆）
_MD_TEXT_MAX = 22
_MD_NAME_MAX = 24
_MD_CLASS_MAX = 16
_MD_CODE_MAX = 22

# 布局示意图的宽度（列数）。任务要求"约 60 列"。
_ASCII_DIAGRAM_COLS = 58


def _md_cell(value: str | None, limit: int, empty: str = "—") -> str:
    """把任意值转成安全的表格单元格文本：压成一行、转义竖线、按长度截断。"""
    if value is None:
        return empty
    text = str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()
    if not text:
        return empty
    if len(text) > limit:
        text = text[: max(1, limit - 1)] + "…"
    return text


def _is_named_business(w: WidgetInfo) -> bool:
    """是不是"用户亲手创建并赋了变量"的业务控件（报告默认只列这类）。"""
    return bool(w.var_name) and not w.var_name.startswith("_") and w.depth > 0


def _region_ancestor(widgets: list[WidgetInfo]) -> dict[int, int]:
    """给每个控件找出它所属的「顶级业务容器」的下标。

    做法：业务控件在 widgets 列表里是按树的先序排列的（walk 先父后子），
    上下文中维护一条"当前祖先链"栈即可：
      遇到业务控件 → 它自己成为一个新区域，并压栈；
      遇到非业务控件 → 不改变栈（它只是中间容器）；
      用 depth 回退栈到正确的父层。
    没有业务容器包裹的控件，归到 -1（"（窗口直属）"区域）。
    """
    owner: dict[int, int] = {}
    stack: list[tuple[int, int]] = []  # (depth, index_of_widget); index=-1 表示无业务祖先

    for w in widgets:
        idx = w.index
        # 回退：弹掉深度 >= 当前控件深度的祖先
        while stack and stack[-1][0] >= w.depth:
            stack.pop()

        if _is_named_business(w):
            # 该控件自己就是区域的起点；它归属于"上一个业务祖先"
            parent_owner = stack[-1][1] if stack else -1
            owner[idx] = parent_owner
            stack.append((w.depth, idx))
        else:
            parent_owner = stack[-1][1] if stack else -1
            owner[idx] = parent_owner

    return owner


def _group_by_region(widgets: list[WidgetInfo]) -> list[tuple[WidgetInfo | None, list[WidgetInfo]]]:
    """把控件按顶级业务容器分组。

    返回 [(区域容器 or None, [该区域的控件...])]，保持首次出现顺序。
    only-named 模式下，"区域容器"本身也会出现在它自己的区域表里（作为首行），
    这样读者能看到"卡片 → 里面有哪些控件"。
    """
    owner = _region_ancestor(widgets)
    by_index = {w.index: w for w in widgets}

    groups: dict[int, list[WidgetInfo]] = {}
    order: list[int] = []
    for w in widgets:
        key = owner.get(w.index, -1)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(w)

    result: list[tuple[WidgetInfo | None, list[WidgetInfo]]] = []
    for key in order:
        container = by_index.get(key) if key >= 0 else None
        result.append((container, groups[key]))
    return result


def _ascii_layout(audit: WindowAudit, named: list[WidgetInfo],
                  cols: int = _ASCII_DIAGRAM_COLS) -> list[str]:
    """按比例把窗口内主要控件画成 ASCII 方框示意图。

    画法（试错后的结论）：
      · 58 列的空间里，父子框必然重叠；硬叠会画出 "┌┌2──┐" 这类乱码
      · 因此**只画互不重叠的框**：先按"面积大 + 位置靠上/靠左"排序，
        逐个尝试放置，与已放置框相交就跳过
      · 大容器（如 body）会先占掉整个区域；它的子卡片就没地方了。
        所以排序时**小框优先**——这样看到的是"具体控件在哪"，而不是一堆大空框
        （大容器本身仍会出现在下方「控件分组」与「层级结构」里）

    每个方框左上角标序号，图例按序号列出控件名 / 类 / 尺寸 / 坐标 / 代码位置。
    """
    # 画布尺寸：窗口逻辑尺寸（优先用几何字符串，退而用各控件的最大边界）
    win_w = max((w.abs_root_x + w.width for w in audit.widgets), default=0)
    win_h = max((w.abs_root_y + w.height for w in audit.widgets), default=0)
    try:
        geo = audit.root_geometry  # 形如 "1600x971+120+120" 或 "1600x971"
        head = geo.split("+")[0].split("-")[0]
        gw, gh = head.lower().split("x")
        win_w = max(win_w, int(gw))
        win_h = max(win_h, int(gh))
    except Exception:
        pass
    if win_w <= 1 or win_h <= 1:
        return ["（窗口尺寸不可用，无法绘制布局示意图）"]

    # 只画放得下的控件；太小的一律跳过，否则 58 列里全是噪点
    boxes = [w for w in named if w.width >= 18 and w.height >= 8 and w.is_visible]
    if not boxes:
        return ["（没有可绘制位置的控件）"]

    scale_x = cols / float(win_w)
    # 字符宽高比约 1:2：纵向按 0.5 压缩，保证图形不被拉长
    scale_y = scale_x * 0.5
    inner_cols = cols
    inner_rows = max(8, min(28, int(round(win_h * scale_y))))

    # 算字符矩形，丢掉缩放到 0 面积的
    rects: list[tuple[WidgetInfo, int, int, int, int]] = []
    for w in boxes:
        c0 = int(round(w.abs_root_x * scale_x))
        r0 = int(round(w.abs_root_y * scale_y))
        c1 = int(round((w.abs_root_x + w.width) * scale_x)) - 1
        r1 = int(round((w.abs_root_y + w.height) * scale_y)) - 1
        c0 = max(0, min(c0, inner_cols - 1))
        r0 = max(0, min(r0, inner_rows - 1))
        # 先夹进画布，再保证最小尺寸；最后**必须再夹一次**，否则靠近右/下边缘的控件
        # 会被 "max(c0 + 2, ...)" 顶出画布，导致后面 canvas[r1][c] 越界
        # （ctk 1600x971 真实项目就踩到过这个 IndexError）。
        c1 = max(c0 + 2, min(c1, inner_cols - 1))
        r1 = max(r0 + 1, min(r1, inner_rows - 1))
        c1 = min(c1, inner_cols - 1)
        r1 = min(r1, inner_rows - 1)
        if c1 - c0 < 3 or r1 - r0 < 1:
            continue
        rects.append((w, c0, r0, c1, r1))

    if not rects:
        return ["（控件太小，按当前比例无法绘制）"]

    # 排序：先大容器（面积大）→ 再让具体控件也能进来；
    # 实测"大框先占满整行"会挤掉所有子控件，所以这里按「面积升序」：
    # 小控件先落位，卡片类容器覆盖大部分区域时仍能画出来，且不会互相打架。
    rects.sort(key=lambda t: ((t[3] - t[2]) * (t[4] - t[1]), t[2], t[1]))

    canvas = [[" "] * inner_cols for _ in range(inner_rows)]
    placed: list[tuple[WidgetInfo, int, int, int, int]] = []

    def overlaps(c0, r0, c1, r1) -> bool:
        for (_w, pc0, pr0, pc1, pr1) in placed:
            if not (c1 < pc0 or pc1 < c0 or r1 < pr0 or pr1 < r0):
                return True
        return False

    for (w, c0, r0, c1, r1) in rects:
        # 与已放置的框重叠就跳过（保持图形干净，宁缺毋滥）
        if overlaps(c0, r0, c1, r1):
            continue
        placed.append((w, c0, r0, c1, r1))
        if len(placed) >= 14:  # 太多反而看不懂
            break

    # 按"从上到下、从左到右"重新编号，读者视线顺序更自然
    placed.sort(key=lambda t: (t[2], t[1]))

    def put(r: int, c: int, ch: str) -> None:
        """防御性写入：越界直接忽略。

        报告是给人看的东西，绝不能因为某个控件的坐标算出画布外的格子就整份生成失败。
        （ctk 1600x971 上曾因 c1/r1 被 "max(c0+2,...)" 顶出画布而抛过 IndexError。）
        """
        if 0 <= r < inner_rows and 0 <= c < inner_cols:
            canvas[r][c] = ch

    for tag, (w, c0, r0, c1, r1) in enumerate(placed, start=1):
        for c in range(c0 + 1, c1):
            put(r0, c, "─")
            put(r1, c, "─")
        for r in range(r0 + 1, r1):
            put(r, c0, "│")
            put(r, c1, "│")
        put(r0, c0, "┌")
        put(r0, c1, "┐")
        put(r1, c0, "└")
        put(r1, c1, "┘")
        # 序号写在框内：单行框没空间就写在框外右侧
        label = str(tag)
        if r1 - r0 >= 2:
            for i, ch in enumerate(label):
                if c0 + 1 + i < c1:
                    put(r0 + 1, c0 + 1 + i, ch)
        else:
            for i, ch in enumerate(label):
                put(r0, c1 + 1 + i, ch)

    out: list[str] = []
    out.append(f"窗口 {win_w}×{win_h}　→　{inner_cols} 列 × {inner_rows} 行"
               f"（横向 1 列≈{win_w / max(1, inner_cols):.0f}px，"
               f"纵向 1 行≈{win_h / max(1, inner_rows):.0f}px）")
    out.append("")
    out.append("  ┌" + "─" * inner_cols + "┐")
    for row in canvas:
        out.append("  │" + "".join(row) + "│")
    out.append("  └" + "─" * inner_cols + "┘")
    out.append("")
    out.append("图例（图内序号 → 控件）：")
    for tag, (w, _c0, _r0, _c1, _r1) in enumerate(placed, start=1):
        name = w.var_name or w.tk_name
        code = f"　`{w.code_location}`" if w.code_location else ""
        out.append(f"  {tag:>2}. {name}　[{w.widget_class}] {w.width}×{w.height} "
                   f"@({w.abs_root_x}, {w.abs_root_y}){code}")
    skipped = len(rects) - len(placed)
    if skipped > 0:
        out.append(f"  （另有 {skipped} 个控件因空间重叠未绘制，见下方「控件分组」表）")
    return out


def to_markdown(audit: WindowAudit, only_named: bool = True) -> str:
    """生成便于贴给他人看的 Markdown 报告。

    only_named=True（默认）：只列出有 Python 变量名的控件，即"用户真正创建的业务控件"。
    循环批量创建、或用工厂函数创建且没赋变量的控件会被排除，但会在报告中说明数量。

    报告结构（从"给谁看"倒推）：
        1. 概览        —— 一屏内看完全局：尺寸、控件数、命名率、DPI、能否信任几何
        2. 空间布局图  —— ASCII 方框示意图，直接看出控件在窗口里的位置关系
        3. 按区域分组  —— 每张顶级卡片一张小表，而不是一张平铺大表
        4. 层级结构    —— 缩进树，看父子关系
    """
    named = [w for w in audit.widgets if _is_named_business(w)]
    unnamed = [w for w in audit.widgets if w not in named]

    # ---------- 统计 ----------
    total = audit.widget_count
    named_count = len(named)
    with_code = sum(1 for w in named if w.code_location)
    with_size = sum(1 for w in named if w.width > 1 and w.height > 1)
    visible = sum(1 for w in named if w.is_visible)
    name_rate = (named_count / total * 100.0) if total else 0.0
    code_rate = (with_code / named_count * 100.0) if named_count else 0.0

    # 几何是否可信：直接看 audit.warnings 里有没有"未完成布局"那条
    layout_trustworthy = not any("尚未完成布局" in w for w in audit.warnings)

    lines: list[str] = []
    lines.append("# Tkinter 控件标注报告")
    lines.append("")
    lines.append(f"> {audit.window_title or '（无标题窗口）'}　·　"
                 f"{audit.root_geometry or '?'}　·　采集于 {audit.captured_at}")
    lines.append("")

    # ---------- 1. 概览 ----------
    lines.append("## 概览")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 窗口标题 | {_md_cell(audit.window_title or '（无标题）', 40)} |")
    lines.append(f"| 窗口类 | `{audit.window_class}` |")
    lines.append(f"| 窗口尺寸 | {_md_cell(audit.root_geometry, 32)} |")
    lines.append(f"| 屏幕尺寸 | {audit.screen_width} × {audit.screen_height} |")
    lines.append(f"| DPI 缩放 | {audit.dpi_scale}×（坐标均为 Tk 逻辑像素） |")
    lines.append(f"| 控件总数 | {total} |")
    lines.append(f"| 业务控件（有变量名） | {named_count} |")
    lines.append(f"| **命名率** | **{name_rate:.1f}%**（{named_count}/{total}） |")
    lines.append(f"| 定位到代码行 | {with_code}/{named_count}"
                 f"（{code_rate:.0f}%） |")
    lines.append(f"| 有真实尺寸的控件 | {with_size}/{named_count} |")
    lines.append(f"| 可见控件 | {visible}/{named_count} |")
    lines.append(f"| 几何可信 | {'是 ✅' if layout_trustworthy else '否 ⚠️（窗口未完成布局，尺寸/坐标为 1×1，勿据此判断）'} |")
    lines.append("")

    if audit.warnings:
        lines.append("**采集警告**")
        lines.append("")
        for w in audit.warnings:
            lines.append(f"- {w}")
        lines.append("")

    if only_named and unnamed:
        lines.append(f"> 报告只列出有 Python 变量名的 {named_count} 个业务控件。"
                     f"另有 {len(unnamed)} 个控件没有独立变量名"
                     f"（循环批量创建或内置容器），未列入下方分组表。")
        lines.append("")

    items = named if only_named else audit.widgets

    # ---------- 2. 空间布局示意图 ----------
    lines.append("## 空间布局示意图")
    lines.append("")
    lines.append("```text")
    lines.extend(_ascii_layout(audit, [w for w in named if w.is_visible]))
    lines.append("```")
    lines.append("")

    # ---------- 3. 按区域分组 ----------
    lines.append("## 控件分组（按顶级业务容器）")
    lines.append("")
    groups = _group_by_region([w for w in audit.widgets if _is_named_business(w)]
                              if only_named else [w for w in audit.widgets if w.depth > 0])

    if not groups:
        lines.append("_没有可列出的控件。_")
        lines.append("")
    else:
        # 序号：组内顺序编号，跨组连续，方便与布局图对应不上时按名字查
        counter = 0
        for container, members in groups:
            if container is None:
                title = "窗口直属"
            else:
                cname = container.var_name or container.tk_name
                label = _md_cell(container.text, 20, empty="")
                suffix = f"　«{label}»" if label and label != "—" else ""
                title = f"`{cname}` [{container.widget_class}]{suffix}"
            lines.append(f"### {title}")
            lines.append("")
            if container is not None:
                lines.append(
                    f"容器：{container.width}×{container.height} @ "
                    f"({container.abs_root_x}, {container.abs_root_y})　·　"
                    f"代码 `{container.code_location or '—'}`　·　"
                    f"含 {len(members)} 个控件"
                )
                lines.append("")
            lines.append("| # | 控件 | 类 | 文本 | 大小 | 窗口内位置 | 代码位置 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for w in members:
                counter += 1
                name = _md_cell(w.var_name or w.path, _MD_NAME_MAX)
                cls = _md_cell(w.widget_class, _MD_CLASS_MAX)
                text = _md_cell(w.text, _MD_TEXT_MAX)
                size = f"{w.width}×{w.height}"
                pos = f"({w.abs_root_x}, {w.abs_root_y})"
                loc = _md_cell(w.code_location, _MD_CODE_MAX)
                # 容器自己在「父区域」表里时加粗，方便一眼找到区域标题对应哪一行
                is_container_row = w is not None and _is_named_business(w) and any(
                    w is c for c, _m in groups if c is not None)
                name_cell = f"**`{name}`**" if is_container_row else f"`{name}`"
                lines.append(f"| {counter} | {name_cell} | {cls} | {text} | "
                             f"{size} | {pos} | `{loc}` |")
            lines.append("")

        lines.append("> 「#」为组内连续编号；**加粗**的是下面还会再展开的容器"
                     "（它的标题对应下方某个小节）。")
        lines.append("")

    # ---------- 4. 层级结构 ----------
    lines.append("## 层级结构")
    lines.append("")
    lines.append("```text")
    shown = items
    # 按深度缩进；只显示业务控件时，用相对缩进避免出现过深的空档
    min_depth = min((w.depth for w in shown), default=0)
    for w in shown:
        indent = "  " * max(0, w.depth - min_depth)
        # 文本可能含换行，必须压成一行，否则会破坏代码块排版
        raw_label = _md_cell(w.text, 40, empty="")
        suffix = f"  «{raw_label}»" if raw_label and raw_label != "—" else ""
        name = w.var_name or w.tk_name
        lines.append(f"{indent}{name} [{w.widget_class}] {w.width}×{w.height}{suffix}")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"由 tk_inspector {__version__} 生成　·　"
                 "把这份文件连同 `代码位置` 列一起发给对方，即可按行号定位要改的控件。")

    return "\n".join(lines)


def save_reports(audit: WindowAudit, out_dir: str | Path | None = None) -> tuple[Path, Path]:
    out = Path(out_dir) if out_dir else Path.cwd() / "tk_reports"
    out.mkdir(parents=True, exist_ok=True)

    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in (audit.window_title or "window"))[:40]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = out / f"{safe_title}_{stamp}.json"
    md_path = out / f"{safe_title}_{stamp}.md"

    json_path.write_text(to_json(audit), encoding="utf-8")
    md_path.write_text(to_markdown(audit), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# 浮层排版/定位用的小工具（纯几何与字体度量，无副作用）
# ---------------------------------------------------------------------------


def _short(text: str, limit: int) -> str:
    """把单行文本截断到 limit 个字符，超出加省略号。"""
    flat = str(text).replace("\r", " ").replace("\n", " ").strip()
    return flat if len(flat) <= limit else flat[: max(0, limit - 1)] + "…"


def _text_w(canvas: tk.Canvas, text: str, font) -> int:
    """量一段文字在给定字体下的像素宽度。

    优先用 tkinter.font.Font 精确度量；失败时按"中文 2 格 / 西文 1 格"估算。
    """
    if not text:
        return 0
    try:
        if tkfont is not None:
            spec = font if isinstance(font, tuple) else (font, 9)
            f = tkfont.Font(root=canvas, font=spec)
            return int(f.measure(text))
    except Exception:
        pass
    ascii_count = sum(1 for ch in text if ord(ch) < 128)
    return int(ascii_count * 7 + (len(text) - ascii_count) * 12)


def _round_rect(width: int, height: int, radius: int) -> list[float]:
    """生成圆角矩形的顶点坐标（配合 Canvas.create_polygon(smooth=True) 使用）。"""
    r = max(1, min(radius, width // 2, height // 2))
    pts: list[float] = []
    # 四个角，每个角用两个控制点近似
    corners = (
        (r, height - r, 90.0),          # 左下
        (width - r, height - r, 0.0),   # 右下
        (width - r, r, -90.0),          # 右上
        (r, r, 180.0),                  # 左上
    )
    for (cx, cy, start) in corners:
        for step in (0, 1):
            angle = math.radians(start + 45.0)
            # 用角点 + 半径方向的两个采样点；smooth=True 会把它拟合成圆弧
            if step == 0:
                angle = math.radians(start)
            else:
                angle = math.radians(start + 90.0)
            pts.extend([cx + r * math.cos(angle), cy + r * math.sin(angle)])
    return pts


def _clamp_to_screen(x: int, y: int, w: int, h: int, sw: int, sh: int) -> tuple[int, int, bool]:
    """把矩形夹进屏幕内。返回 (x, y, 是否发生过夹取)。"""
    clamped = False
    if x + w > sw:
        x = sw - w
        clamped = True
    if x < 0:
        x = 0
        clamped = True
    if y + h > sh:
        y = sh - h
        clamped = True
    if y < 0:
        y = 0
        clamped = True
    return x, y, clamped


def _rects_intersect(ax: int, ay: int, aw: int, ah: int,
                     bx: int, by: int, bw: int, bh: int) -> bool:
    """两个矩形是否相交（边贴边不算相交）。"""
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def _intersect_area(ax: int, ay: int, aw: int, ah: int,
                    bx: int, by: int, bw: int, bh: int) -> int:
    """两个矩形相交部分的面积。"""
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return w * h if w > 0 and h > 0 else 0


# ---------------------------------------------------------------------------
# 鼠标穿透（click-through）
#
# 实测结论（Windows 11 / Tk 8.6，证据见 README「鼠标穿透实测」）：
#   · window.attributes("-alpha", 0.9)           → 只是视觉半透明，**照样拦截鼠标**
#   · window.attributes("-transparentcolor", c)  → 只有**颜色恰好等于 c** 的像素穿透；
#                                                  卡片有不透明底和文字，实心区域照样拦截
#   · WS_EX_TRANSPARENT 扩展样式                   → **整个窗口**不接收鼠标，真正穿透 ✅
#
# 所以浮层用「WS_EX_TRANSPARENT 为主 + -transparentcolor 只负责抠圆角」的组合：
#   -transparentcolor 让窗口四角视觉透明（画圆角卡片），但它不承担"不挡鼠标"的职责；
#   真正保证点得到目标控件的是 WS_EX_TRANSPARENT。
#
# 纯标准库实现：Windows 用 ctypes 调 user32；非 Windows 或调用失败时降级（见 _warn_once）。
# ---------------------------------------------------------------------------

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080

# 用于抠色的"非常规"背景色：正常界面几乎不可能出现，避免误抠掉卡片本身
_TRANSPARENT_KEY = "#FE00FE"

_click_through_warned = False


def _hwnd_of(window: tk.Misc) -> int:
    """取 Tk 顶层窗口的 Win32 HWND。

    Tk 的 winfo_id() 返回的是子窗口（TkChild），带 WS_EX_* 的是它的容器，
    所以要先 GetParent 一层。拿不到父窗口时退回自身。
    """
    handle = int(window.winfo_id())
    try:
        import ctypes

        wrapper = ctypes.windll.user32.GetParent(handle)
        return wrapper if wrapper else handle
    except Exception:
        return handle


def _set_click_through(window: tk.Misc) -> bool:
    """给窗口设置 WS_EX_LAYERED | WS_EX_TRANSPARENT，让鼠标事件穿透到下层。

    返回 True 表示设置成功。失败不抛异常——浮层是辅助显示，绝不能因此让主程序崩。
    withdraw/deiconify 与 geometry 移动都不会清除该扩展样式（已实测）。
    """
    global _click_through_warned
    try:
        if sys.platform != "win32":
            if not _click_through_warned:
                _click_through_warned = True
                print("[tk_inspector] 非 Windows 平台：浮层无法设置 WS_EX_TRANSPARENT，"
                      "鼠标穿透不可用（已改为不覆盖目标控件的定位策略来规避遮挡）。")
            return False

        import ctypes

        window.update_idletasks()
        hwnd = _hwnd_of(window)
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        # WS_EX_TOOLWINDOW：不占任务栏、不出现在 Alt+Tab
        new_style = style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new_style)
        return bool(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TRANSPARENT)
    except Exception:
        if not _click_through_warned:
            _click_through_warned = True
            print("[tk_inspector] 设置鼠标穿透失败，浮层可能拦截鼠标"
                  "（不影响标注与导出功能）。")
        return False


def _is_click_through(window: tk.Misc) -> bool:
    """自检：窗口当前是否真的带了 WS_EX_TRANSPARENT。供测试与排错使用。"""
    try:
        if sys.platform != "win32":
            return False
        import ctypes

        hwnd = _hwnd_of(window)
        return bool(ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
                    & _WS_EX_TRANSPARENT)
    except Exception:
        return False


# 浮层配色：深色卡片 + 冷色强调，标题/键/值三级层次
_C_BG = "#1B1B1F"          # 卡片底
_C_BORDER = "#3A3A42"      # 1px 描边
_C_TITLE = "#4CC2FF"       # 标题（变量名）强调色
_C_TITLE_DIM = "#8B8B96"   # 标题里的控件类等次要信息
_C_KEY = "#8B8B96"         # 键（"大小"）次要色
_C_VAL = "#E8E8ED"         # 值（正文）
_C_CODE = "#FFD166"        # 代码位置：暖色，一眼能抓到
_C_ACCENT = "#4CC2FF"      # 高亮框
_C_ACCENT_SOFT = "#4CC2FF"

_CARD_RADIUS = 8
_CARD_PAD = 9              # 卡片内边距
_CORNER_LEN = 14           # 高亮框四角标记长度
_CORNER_THICK = 2


def _font_candidates(*preferred: str) -> tuple[str, int] | tuple[str, int, str]:
    """挑一个系统里存在的等宽字体（用于对齐数字），失败则退回 Tk 默认等宽族名。"""
    try:
        available = set(tk.font.families())
    except Exception:
        available = set()
    for name in preferred:
        if name in available:
            return (name, 9)
    return ("TkFixedFont", 9)


class InspectorOverlay:
    """控件标注浮层：高亮框（角标）+ 信息卡片。

    三个独立窗口，都以「鼠标穿透」方式显示，绝不拦截对目标控件的点击：
        window  高亮框：只画 1px 边框 + 四角标记，中间完全透明
        panel   信息卡片：单行标题 + 2 行关键信息（简略）／全部字段（详细）

    定位策略见 _place_panel：永远不覆盖被检查控件本体。
    """

    def __init__(self, master: tk.Misc) -> None:
        self.master = master
        self._visible = False
        self.detail = False          # False=简略（默认），True=详细
        self._info: WidgetInfo | None = None
        self._widget: tk.Misc | None = None
        self._last_rect: tuple[int, int, int, int] | None = None
        self._font = _font_candidates("Cascadia Mono", "Consolas", "Courier New")

        # ---- 高亮框窗口 ----
        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=_TRANSPARENT_KEY)
        try:
            self.window.attributes("-transparentcolor", _TRANSPARENT_KEY)
        except Exception:
            pass

        self.canvas = tk.Canvas(self.window, highlightthickness=0, bd=0,
                                bg=_TRANSPARENT_KEY)
        self.canvas.pack(fill="both", expand=True)

        # ---- 信息卡片窗口 ----
        self.panel = tk.Toplevel(master)
        self.panel.withdraw()
        self.panel.overrideredirect(True)
        self.panel.attributes("-topmost", True)
        self.panel.configure(bg=_TRANSPARENT_KEY)
        try:
            self.panel.attributes("-transparentcolor", _TRANSPARENT_KEY)
        except Exception:
            pass

        self.card = tk.Canvas(self.panel, highlightthickness=0, bd=0,
                              bg=_TRANSPARENT_KEY, cursor="arrow")
        self.card.pack(fill="both", expand=True)

        self._click_through = False

    # -- 鼠标穿透 --

    def _ensure_click_through(self) -> None:
        """给两个窗口挂上 WS_EX_TRANSPARENT（幂等，每次 show 前确认一次）。"""
        ok = _set_click_through(self.window) and _set_click_through(self.panel)
        if ok:
            self._click_through = True

    # -- 显示 --

    def show(self, info: WidgetInfo | None, widget: tk.Misc | None) -> None:
        if info is None or widget is None or not info.is_visible:
            self.hide()
            return

        try:
            sx = widget.winfo_rootx()
            sy = widget.winfo_rooty()
            sw = max(1, widget.winfo_width())
            sh = max(1, widget.winfo_height())
        except Exception:
            self.hide()
            return

        self._info = info
        self._widget = widget
        self._last_rect = (sx, sy, sw, sh)

        self._draw_highlight(sx, sy, sw, sh)
        self._render_card(info)
        self._place_panel(sx, sy, sw, sh)

        # 先设穿透再显示：避免 deiconify 到设置完成之间有一帧会拦截鼠标。
        # 窗口隐藏时其容器 hwnd 同样是可设置的（已在 _probe_debug 中确认），
        # 所以这里不依赖"先显示"。
        self._ensure_click_through()

        self.window.deiconify()
        self.window.lift()
        self.panel.deiconify()
        self.panel.lift()
        # 再补一次：个别 Windows 版本在 deiconify 时会重建窗口属性
        self._ensure_click_through()
        self._visible = True

    def toggle_detail(self) -> bool:
        """在简略／详细之间切换（Tab 键）。返回切换后是否为详细模式。"""
        self.detail = not self.detail
        if self._info is not None and self._widget is not None and self._visible:
            self._render_card(self._info)
            if self._last_rect is not None:
                self._place_panel(*self._last_rect)
                self.panel.lift()
                self._ensure_click_through()
        return self.detail

    # -- 高亮框：细边框 + 四角标记（不填色，比半透明块更不遮挡） --

    def _draw_highlight(self, sx: int, sy: int, sw: int, sh: int) -> None:
        pad = 2
        w, h = sw + pad * 2, sh + pad * 2
        self.window.geometry(f"{w}x{h}+{sx - pad}+{sy - pad}")

        cv = self.canvas
        cv.delete("all")
        # 1px 细边框：贴着控件外沿
        cv.create_rectangle(pad, pad, pad + sw - 1, pad + sh - 1,
                            outline=_C_BORDER, width=1)
        # 四角标记：只画角，中间留空 → 视线不被框线干扰
        n = min(_CORNER_LEN, max(4, min(sw, sh) // 3))
        t = _CORNER_THICK
        corners = (
            # (x0, y0, x_dir, y_dir) —— 从角点向内画两条短粗线
            (pad, pad, 1, 1),
            (pad + sw - 1, pad, -1, 1),
            (pad, pad + sh - 1, 1, -1),
            (pad + sw - 1, pad + sh - 1, -1, -1),
        )
        for (cx, cy, dx, dy) in corners:
            cv.create_line(cx, cy, cx + dx * n, cy, fill=_C_ACCENT, width=t)
            cv.create_line(cx, cy, cx, cy + dy * n, fill=_C_ACCENT, width=t)

    # -- 信息卡片 --

    def _card_lines(self, info: WidgetInfo) -> list[tuple[str, str, str]]:
        """按「(角色, 键, 值)」组织卡片内容。角色决定配色。

        简略模式只给用户真正关心的三件事：控件名、大小、代码位置。
        """
        name = info.var_name or info.tk_name
        text = (info.text or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text) > 46:
            text = text[:46] + "…"

        lines: list[tuple[str, str, str]] = []
        if not self.detail:
            lines.append(("size", "大小", f"{info.width} × {info.height}  px"))
            lines.append(("code", "代码", info.code_location or "（未能定位）"))
            return lines

        # 详细模式：把 JSON 里的字段按可读性排一遍
        lines.append(("kv", "tk_name", info.tk_name))
        lines.append(("kv", "path", info.path))
        lines.append(("kv", "大小", f"{info.width} × {info.height} px"))
        lines.append(("kv", "父内", f"({info.x}, {info.y})"))
        lines.append(("kv", "窗口内", f"({info.abs_root_x}, {info.abs_root_y})"))
        lines.append(("kv", "屏幕", f"({info.screen_x}, {info.screen_y})"))
        if text:
            lines.append(("kv", "文本", text))
        if info.state:
            lines.append(("kv", "状态", info.state))
        lines.append(("kv", "可用", "是" if info.is_enabled else "否"))
        lines.append(("kv", "可见", "是" if info.is_visible else "否"))
        if info.font:
            lines.append(("kv", "字体", _short(info.font, 40)))
        if info.fg or info.bg:
            lines.append(("kv", "前景/背景", f"{info.fg or '—'} / {info.bg or '—'}"))
        lines.append(("code", "代码", info.code_location or "（未能定位）"))
        lines.append(("kv", _inspector_text("manager"), info.manager))
        lines.append(("kv", _inspector_text("layout"), _short(json.dumps(info.layout, ensure_ascii=False), 64)))
        for key in ("corner_radius", "border_width", "border_color", "hover_color"):
            if key in info.options:
                lines.append(("kv", key, _short(str(info.options[key]), 64)))
        return lines

    def _render_card(self, info: WidgetInfo) -> None:
        """把卡片内容画进 Canvas，并让窗口尺寸贴合内容（紧凑，无空白）。"""
        cv = self.card
        cv.delete("all")

        name = info.var_name or info.tk_name
        name_font = ("Microsoft YaHei UI", 10, "bold")
        cls_font = ("Microsoft YaHei UI", 9)
        key_font = ("Microsoft YaHei UI", 9)
        val_font = self._font

        # --- 先量尺寸 ---
        title_y = _CARD_PAD
        rows = self._card_lines(info)

        # 标题行：变量名（强调色）+ 控件类（次要色）
        display_class = info.python_class.rsplit(".", 1)[-1] or info.widget_class
        name_w = _text_w(cv, name, name_font)
        cls_w = _text_w(cv, f"  {display_class}", cls_font)
        title_w = name_w + cls_w

        row_h = 17
        value_w = 0
        key_w = 0
        for _role, key, val in rows:
            key_w = max(key_w, _text_w(cv, key, key_font))
            value_w = max(value_w, _text_w(cv, val, val_font))
        body_w = key_w + 10 + value_w

        hint = _inspector_text("hint")
        hint_w = _text_w(cv, hint, ("Microsoft YaHei UI", 8))

        content_w = max(title_w, body_w, hint_w)
        card_w = int(content_w) + _CARD_PAD * 2
        card_h = _CARD_PAD + 18 + 3 + len(rows) * row_h + 4 + 14 + _CARD_PAD

        self.panel.geometry(f"{card_w}x{card_h}")

        # --- 画圆角卡片底 + 1px 描边 ---
        cv.create_polygon(
            _round_rect(card_w - 2, card_h - 2, _CARD_RADIUS),
            fill=_C_BG, outline=_C_BORDER, width=1, smooth=True,
        )

        # --- 标题行 ---
        x = _CARD_PAD
        cv.create_text(x, title_y, anchor="nw", text=name, fill=_C_TITLE, font=name_font)
        cv.create_text(x + name_w, title_y + 1, anchor="nw", text=f"  {display_class}",
                       fill=_C_TITLE_DIM, font=cls_font)

        # 标题下细分隔线
        y = title_y + 18 + 1
        cv.create_line(_CARD_PAD, y, card_w - _CARD_PAD, y, fill="#2E2E36")

        # --- 键值行 ---
        y += 3
        for role, key, val in rows:
            val_color = _C_CODE if role == "code" else _C_VAL
            cv.create_text(_CARD_PAD, y, anchor="nw", text=key, fill=_C_KEY, font=key_font)
            cv.create_text(_CARD_PAD + key_w + 10, y, anchor="nw", text=val,
                           fill=val_color, font=val_font)
            y += row_h

        # --- 底部提示（很淡，不抢信息） ---
        cv.create_text(card_w - _CARD_PAD, card_h - _CARD_PAD,
                       anchor="se", text=hint, fill="#5E5E6A",
                       font=("Microsoft YaHei UI", 8))

        self._card_size = (card_w, card_h)

    def _place_panel(self, sx: int, sy: int, sw: int, sh: int) -> None:
        """智能定位：默认右下；越界或被遮挡就翻转，**永不覆盖目标控件本体**。

        候选顺序（按视觉习惯优先）：
            1. 右下  2. 右上  3. 左下  4. 左上
            5. 左侧外（纵向居中）  6. 右侧外  7. 目标上方  8. 目标下方
        每组都做屏幕夹取；夹取后若仍与目标矩形相交，换下一个候选。
        全部候选都会相交（控件几乎占满屏幕）时，退到"屏幕内不被遮挡的角落"，
        并把卡片设为半透明，保证至少看得到控件。
        """
        if not hasattr(self, "_card_size"):
            self._card_size = (240, 80)
        pw, ph = self._card_size
        gap = 8

        screen_w = int(_safe(self.master, lambda: self.master.winfo_screenwidth(), 0) or 0)
        screen_h = int(_safe(self.master, lambda: self.master.winfo_screenheight(), 0) or 0)
        if screen_w <= 1 or screen_h <= 1:
            screen_w, screen_h = 1920, 1080

        target = (sx, sy, sw, sh)

        candidates = [
            (sx + sw + gap, sy + sh + gap),            # 1 右下
            (sx + sw + gap, sy - ph - gap),            # 2 右上
            (sx - pw - gap, sy + sh + gap),            # 3 左下
            (sx - pw - gap, sy - ph - gap),            # 4 左上
            (sx - pw - gap, sy + (sh - ph) // 2),      # 5 左侧外
            (sx + sw + gap, sy + (sh - ph) // 2),      # 6 右侧外
            (sx + (sw - pw) // 2, sy - ph - gap),      # 7 上方
            (sx + (sw - pw) // 2, sy + sh + gap),      # 8 下方
        ]

        chosen: tuple[int, int] | None = None
        for (cx, cy) in candidates:
            px, py, clamped = _clamp_to_screen(cx, cy, pw, ph, screen_w, screen_h)
            if not _rects_intersect(px, py, pw, ph, *target):
                chosen = (px, py)
                break

        if chosen is None:
            # 控件几乎占满屏：挑与控件重叠面积最小的位置，并让卡片半透明
            best = None
            best_area = None
            for (cx, cy) in candidates:
                px, py, _ = _clamp_to_screen(cx, cy, pw, ph, screen_w, screen_h)
                area = _intersect_area(px, py, pw, ph, *target)
                if best_area is None or area < best_area:
                    best_area, best = area, (px, py)
            chosen = best or (gap, gap)
            self._set_card_alpha(0.92)
        else:
            self._set_card_alpha(1.0)

        self.panel.geometry(f"{pw}x{ph}+{chosen[0]}+{chosen[1]}")

    def _set_card_alpha(self, value: float) -> None:
        try:
            self.panel.attributes("-alpha", value)
        except Exception:
            pass

    def hide(self) -> None:
        self.window.withdraw()
        self.panel.withdraw()
        self._visible = False
        self._info = None
        self._widget = None

    def destroy(self) -> None:
        for win in (self.window, self.panel):
            try:
                win.destroy()
            except Exception:
                pass


class TkInspector:
    """把标注能力挂到指定窗口上。

    用法：
        root = tk.Tk()
        ...
        inspector = TkInspector(root)     # 或 tk_inspector.attach(root)
        root.mainloop()
    """

    def __init__(self, target: tk.Misc, *, hotkey: bool = True, out_dir: str | Path | None = None) -> None:
        self.target = target
        self.out_dir = out_dir
        self.active = False
        self.selected: WidgetInfo | None = None
        self.overlay = InspectorOverlay(target)
        self._last_widget: tk.Misc | None = None
        self._index: dict[str, tk.Misc] = {}
        self._event_tag = f"QuestInspector{id(self)}"
        self._global_bindings: list[tuple[str, str]] = []
        self._tag_bindings: list[tuple[str, str]] = []
        self._tagged: weakref.WeakSet[tk.Misc] = weakref.WeakSet()
        self._details = None
        self._details_text = None
        self._details_info = None

        self._bind_events()
        if hotkey:
            self._bind_hotkeys()

        print(f"[tk_inspector] 已挂载到 {target.winfo_class()}。"
              f"按 Ctrl+Alt+I 开关标注模式，Ctrl+Alt+E 导出。")

    # -- 事件 --

    def _bind_events(self) -> None:
        target = self.target
        for sequence, callback in (("<Control-Alt-KeyPress-i>", self._on_toggle),
                                   ("<Control-Alt-KeyPress-e>", self._on_export),
                                   ("<Escape>", self._on_escape), ("<Map>", self._on_map)):
            self._global_bindings.append((sequence, target.bind_all(sequence, callback, add="+")))
        # An early bindtag runs before Entry/Text class bindings can consume Tab.
        for sequence, callback in (("<KeyPress-Tab>", self._on_tab),
                                   ("<KeyPress-ISO_Left_Tab>", self._on_tab),
                                   ("<F2>", self._on_tab),
                                   ("<Control-Alt-KeyPress-d>", self._on_details),
                                   ("<Motion>", self._on_motion),
                                   ("<Button-1>", self._on_click), ("<Leave>", self._on_leave)):
            self._tag_bindings.append((sequence, target.bind_class(self._event_tag, sequence, callback)))
        self._bind_motion()

    def _on_map(self, event) -> None:
        if isinstance(event.widget, tk.Misc):
            self._tag_widget(event.widget)

    def _tag_widget(self, widget) -> None:
        if self._owned(widget):
            return
        tags = widget.bindtags()
        if self._event_tag not in tags:
            widget.bindtags((self._event_tag,) + tags)
            self._tagged.add(widget)

    def _owned(self, widget) -> bool:
        top = _safe(widget, widget.winfo_toplevel)
        return top in (self.overlay.window, self.overlay.panel, self._details)

    def _bind_hotkeys(self) -> None:
        # bind_all 只在本程序有焦点时生效；这里再挂一份到所有子控件
        pass

    def _on_toggle(self, _event=None) -> str:
        self.toggle()
        return "break"

    def _on_export(self, _event=None) -> str:
        self.export()
        return "break"

    def _on_tab(self, _event=None) -> str | None:
        """Tab：在浮层里切换「简略 / 详细」。

        只有标注模式开着、且浮层正在显示时才拦截事件；其余情况返回 None，
        让目标程序保持原生的 Tab 焦点行为（不打断别人的程序）。
        """
        if not self.active or not getattr(self.overlay, "_visible", False):
            return None
        detail = self.overlay.toggle_detail()
        print(f"[tk_inspector] 浮层模式：{'详细' if detail else '简略'}")
        return "break"

    def _on_escape(self, _event=None) -> str | None:
        if not self.active:
            return None
        self.selected = None
        self.overlay.hide()
        self._print_selected()
        return "break"

    # -- 核心 --

    def toggle(self) -> None:
        self.active = not self.active
        if self.active:
            self._rebuild_index()
            self._bind_motion()
            print("[tk_inspector] 标注模式：开")
        else:
            self.overlay.hide()
            print("[tk_inspector] 标注模式：关")

    def _rebuild_index(self) -> None:
        """重建「路径 → 控件」映射，并同时刷新一次快照。"""
        self._index.clear()
        self._audit = audit_window(self.target)
        self._index = {w.tk_name: w for w in self._audit.widgets}

    def _bind_motion(self) -> None:
        """Use a removable tag; never overwrite the application's own bindings."""
        for widget in self._iter_widgets(self.target):
            self._tag_widget(widget)

    def _iter_widgets(self, widget: tk.Misc):
        yield widget
        for child in _safe(widget, lambda: list(tk.Misc.winfo_children(widget)), []) or []:
            yield from self._iter_widgets(child)

    def _on_motion(self, event) -> None:
        if not self.active:
            return
        widget = event.widget
        if widget is self.target:
            # 已选中（固定显示）时，鼠标移到空白处也会消失；用户按 Esc 才取消固定
            if self.selected is None:
                self.overlay.hide()
            return
        # 已选中某控件（点击固定）时，悬停其他控件不改变显示，保持"固定"语义
        if self.selected is not None:
            return
        info = self._info_for(widget)
        if info is not None:
            self.overlay.show(info, self._last_widget or widget)

    def _on_leave(self, event) -> None:
        if not self.active:
            return
        # 鼠标离开到程序外时才隐藏（Leave 在子控件间切换会频繁触发）
        self.target.after(120, self._maybe_hide)

    def _maybe_hide(self) -> None:
        if not self.active or self.selected is not None:
            return
        try:
            px = self.target.winfo_pointerx()
            py = self.target.winfo_pointery()
            hovered = self.target.winfo_containing(px, py)
            if hovered is None:
                self.overlay.hide()
        except Exception:
            self.overlay.hide()

    def _on_click(self, event) -> str | None:
        if not self.active:
            return
        widget = event.widget
        if widget is self.target:
            return
        info = self._info_for(widget)
        if info is None:
            return
        self.selected = info
        self.overlay.show(info, self._last_widget or widget)
        self._print_selected()
        return "break"

    def _info_for(self, widget: tk.Misc) -> WidgetInfo | None:
        if not hasattr(self, "_audit"):
            return None
        while (isinstance(widget, (tk.Canvas, tk.Label, tk.Entry, tk.Text))
               and type(getattr(widget, "master", None)).__module__.startswith("customtkinter")):
            widget = widget.master
        self._last_widget = widget
        # 重新采集该控件自身（位置/大小可能已变）
        fresh = collect_widgets(widget, max_depth=0, include_ctk_internal=True)
        if fresh:
            info = fresh[0]
            info.tk_name = str(widget)
            top = widget.winfo_toplevel()
            info.abs_root_x = info.screen_x - top.winfo_rootx()
            info.abs_root_y = info.screen_y - top.winfo_rooty()
            return info
        return None

    def _on_details(self, _event=None) -> str | None:
        if not self.active:
            return None
        info = self.selected or self.overlay._info
        if info is None:
            return "break"
        self._details_info = info
        if self._details is None or not self._details.winfo_exists():
            self._details = tk.Toplevel(self.target)
            self._details.title(_inspector_text("title"))
            self._details.geometry("720x560")
            self._details.minsize(420, 300)
            self._details.protocol("WM_DELETE_WINDOW", self._details.withdraw)
            self._details.grid_columnconfigure(0, weight=1)
            self._details.grid_rowconfigure(1, weight=1)
            ttk.Button(self._details, text=_inspector_text("copy"), command=self._copy_details).grid(
                row=0, column=0, sticky="w", padx=10, pady=10)
            self._details_text = tk.Text(self._details, wrap="none", font=("Consolas", 11))
            self._details_text.grid(row=1, column=0, sticky="nsew")
            vertical = ttk.Scrollbar(self._details, command=self._details_text.yview)
            vertical.grid(row=1, column=1, sticky="ns")
            horizontal = ttk.Scrollbar(self._details, orient="horizontal", command=self._details_text.xview)
            horizontal.grid(row=2, column=0, sticky="ew")
            self._details_text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self._details_text.configure(state="normal")
        self._details_text.delete("1.0", "end")
        self._details_text.insert("1.0", json.dumps(asdict(info), ensure_ascii=False, indent=2))
        self._details_text.configure(state="disabled")
        self._details.deiconify()
        self._details.lift()
        self._details_text.focus_set()
        return "break"

    def _copy_details(self) -> None:
        if self._details_info is not None:
            self.target.clipboard_clear()
            self.target.clipboard_append(json.dumps(asdict(self._details_info), ensure_ascii=False, indent=2))

    def _print_selected(self) -> None:
        s = self.selected
        if s is None:
            print("[tk_inspector] 已取消选中")
            return
        print(
            f"[tk_inspector] 选中 {s.var_name or s.tk_name} ({s.widget_class}) "
            f"{s.width}×{s.height} @ ({s.abs_root_x}, {s.abs_root_y}) "
            f"代码={s.code_location or '?'}"
        )

    def export(self) -> None:
        try:
            # 导出前再扫一次栈，尽量多抓到变量名（例如控件在某个函数里临时创建后仍被引用时）
            snapshot_caller_vars()
            _var_cache.clear()
            audit = audit_window(self.target)
            json_path, md_path = save_reports(audit, self.out_dir)
            named = sum(1 for w in audit.widgets if w.var_name)
            located = sum(1 for w in audit.widgets if w.code_location)
            print(f"[tk_inspector] 已导出：\n  {json_path}\n  {md_path}")
            print(f"[tk_inspector] {audit.widget_count} 个控件，"
                  f"识别变量名 {named} 个，定位代码行 {located} 个")
        except Exception:
            traceback.print_exc()

    def detach(self) -> None:
        self.active = False
        for widget in self._tagged:
            if _safe(widget, widget.winfo_exists, False):
                widget.bindtags(tuple(tag for tag in widget.bindtags() if tag != self._event_tag))
        for sequence, funcid in self._tag_bindings:
            self.target._unbind(("bind", self._event_tag, sequence), funcid)
        for sequence, funcid in self._global_bindings:
            self.target._unbind(("bind", "all", sequence), funcid)
        if self._details is not None and self._details.winfo_exists():
            self._details.destroy()
        self.overlay.destroy()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def attach(target: tk.Misc, **kwargs) -> TkInspector:
    """在目标窗口上挂载标注器。返回实例（可调用 .export()）。

    重要：本函数会扫描调用栈登记控件变量名。请在**控件创建完成之后**、
    最好与创建代码在同一个函数里调用，这样变量名和代码行才定位得准。
    """
    snapshot_caller_vars()
    return TkInspector(target, **kwargs)


def _scan_process_windows() -> list[tuple[int, str]]:
    """占位：跨进程扫描需要 UIA，本工具专注同进程内标注。

    如果需要在不改代码的前提下扫描另一个 python 程序，
    请使用 WinUI Inspector（能拿到窗口级信息，但拿不到控件名）。
    """
    return []


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Tkinter 控件标注工具")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--out", help="报告输出目录（默认 ./tk_reports）")
    parser.add_argument("--demo", action="store_true", help="运行内置演示窗口并挂载标注器")
    args = parser.parse_args()

    if args.demo:
        return _run_demo(args.out)

    parser.print_help()
    print()
    print("本工具需要在被标注的程序内部运行。改一行代码即可：")
    print()
    print("    import tk_inspector")
    print("    tk_inspector.attach(root)")
    print()
    print("或用 --demo 看效果。")
    return 0


def _run_demo(out_dir: str | None) -> int:
    """演示窗口：覆盖常见控件类型，用于验证标注器本身。"""
    root = tk.Tk()
    root.title("tk_inspector 演示窗口")
    root.geometry("680x460+120+120")

    top = tk.Frame(root, bg="#f0f0f0", height=52)
    top.pack(fill="x")

    title = ttk.Label(top, text="控件标注演示", font=("Microsoft YaHei UI", 13))
    title.pack(side="left", padx=12, pady=10)

    refresh_btn = ttk.Button(top, text="刷新", width=9)
    refresh_btn.pack(side="right", padx=8, pady=10)

    export_btn = ttk.Button(top, text="导出", width=9)
    export_btn.pack(side="right", pady=10)

    body = ttk.Frame(root)
    body.pack(fill="both", expand=True, padx=12, pady=10)

    left = ttk.LabelFrame(body, text="参数设置")
    left.pack(side="left", fill="both", expand=True, padx=(0, 8))

    name_label = ttk.Label(left, text="名称")
    name_label.grid(row=0, column=0, sticky="w", padx=8, pady=8)

    name_entry = ttk.Entry(left, width=24)
    name_entry.grid(row=0, column=1, pady=8)

    count_label = ttk.Label(left, text="数量")
    count_label.grid(row=1, column=0, sticky="w", padx=8, pady=8)

    count_spin = ttk.Spinbox(left, from_=1, to=99, width=22)
    count_spin.grid(row=1, column=1, pady=8)

    enable_check = ttk.Checkbutton(left, text="启用高级选项")
    enable_check.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=8)

    mode_combo = ttk.Combobox(left, values=["快速", "标准", "高精度"], width=22)
    mode_combo.current(1)
    mode_combo.grid(row=3, column=1, pady=8)

    mode_label = ttk.Label(left, text="模式")
    mode_label.grid(row=3, column=0, sticky="w", padx=8, pady=8)

    right = ttk.LabelFrame(body, text="预览")
    right.pack(side="left", fill="both", expand=True)
    preview = tk.Text(right, height=12, width=30)
    preview.pack(fill="both", expand=True, padx=8, pady=8)

    bottom = tk.Frame(root)
    bottom.pack(fill="x", padx=12, pady=(0, 12))

    ok_btn = ttk.Button(bottom, text="确定", width=12)
    ok_btn.pack(side="right", padx=4)

    cancel_btn = ttk.Button(bottom, text="取消", width=12)
    cancel_btn.pack(side="right")

    inspector = attach(root, out_dir=out_dir)

    def _auto_export():
        inspector.export()
        print("[demo] 已自动导出一次，可在 tk_reports/ 查看")

    root.after(1200, _auto_export)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
