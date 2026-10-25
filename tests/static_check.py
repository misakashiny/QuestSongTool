"""静态检查：不运行程序也能抓到的两类错误。

为什么要有这个文件（v14.1 的血泪教训）：
v14.0 交付时 8 组测试全绿，却带着两个 **undefined name** 缺陷：
  * `device.py` 用了没导入的 `CUSTOM_LEVELS_RELATIVE`；
  * `ui_songlist.py` 的 except 分支用了没定义的 `logger`。
这两处都在"平时走不到"的分支上，所以动态测试照不到，但静态检查**一眼就能看出来**。
本机没装（也可能没网装）pyflakes/ruff，所以这里用标准库 `ast` 自己实现，零依赖。

检查两项：
  1. **未定义名**：模块里 Load 了、但整个模块都没定义/导入过的名字；
  2. **.ps1 必须是 UTF-8 带 BOM**：`shell_probe.ps1` / `shell_copy.ps1` 里有中文路径，
     Windows PowerShell 5.1 读"无 BOM 的 UTF-8"会按 GBK 解码 → 中文变乱码 → COM 调用失败。
     （HANDOVER 4.6 节自己写了"tests/ 里没有自动检查，建议加一条"，就是这一条。）

已知局限（如实说明，别指望它包治百病）：
  * 只看"整个模块里有没有这个名字"，**不判断使用顺序**，所以抓不到
    "先用后定义"或"只在某个分支里定义"；
  * 遇到 `from x import *` 会跳过该文件（无法静态分析）；
  * 不做类型检查、不查未使用的导入。

用法：`python tests/static_check.py`（退出码非 0 表示有问题）
"""
from __future__ import annotations

import ast
import builtins
import re
import sys
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）


def _make_stdout_forgiving() -> None:
    """让 print 在**任何**控制台代码页下都不会抛异常。

    为什么必须做这件事（v17.9 实测踩到）：
    这个脚本正常跑完会 `print("...全部通过 ✔")`，而 `✔`(U+2714) 不在 GBK 里。
    直接跑在中文 Windows 控制台（代码页 936）时，这一行会抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '\\u2714'`，
    于是 —— **7 项检查全部 PASS，退出码却是 1**。

    对 CI / 批处理 / 另一个 AI 来说，"退出码 1" 就是"检查没过"，
    比漏检更坏：它会让人去修一个根本不存在的问题，也会让人怀疑真出问题时的红灯。
    "静态检查必须可信" 是这一项的全部意义，所以宁可把不可编码的字符降级成 `?`，
    也不能让输出编码决定退出码。

    `errors="replace"` 只在**真正不可编码**时才生效，正常 UTF-8 控制台输出不变。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # 被重定向成非文本流时忽略：那不是致命问题


_make_stdout_forgiving()


#: 参与静态检查的源码。
#: **测试代码也要扫** —— 测试本身写坏了同样是问题，而且本项目就发生过
#: `static_check.py` 自己用了没导入的 `re` 却查不出来的事（因为它当时不在扫描范围里）。
TARGETS: tuple[Path, ...] = (ROOT / "main.py", ROOT / "main_qt.py", ROOT / "quest_tool", ROOT / "tests")
#: 排除：`tests/archive/` 是**明确归档、不参与日常使用**的一次性排查脚本
#: （见该目录 README），要求它们 lint 干净没有意义。
#:
#: `.tmp` 是 `tests/_paths.py` 里 `sandbox()` 定义的**临时沙箱目录**
#: （已被 `.gitignore` 忽略），里面放的是测试产物与一次性排查脚本。
#: v17.9 补进排除列表 —— 在此之前它会被一并扫描：只要有人往 `.tmp` 里
#: 放过一个带 BOM 的脚本，静态检查就会红，而那不是产品代码的问题。
EXCLUDED_PARTS: tuple[str, ...] = ("archive", "__pycache__", ".tmp")
#: 必须存成 UTF-8 BOM 的 PowerShell 脚本
BOM_REQUIRED: tuple[Path, ...] = (
    ROOT / "quest_tool" / "shell_probe.ps1",
    ROOT / "quest_tool" / "shell_copy.ps1",
)
UTF8_BOM = b"\xef\xbb\xbf"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


# --------------------------------------------------------------------------------------
# 1) 未定义名
# --------------------------------------------------------------------------------------
def _collect_defined(tree: ast.AST) -> set[str] | None:
    """收集模块里所有"算作已定义"的名字；返回 None 表示有 `import *`，放弃分析。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                return None
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.alias):  # 兜底：单独出现的 alias
            names.add((node.asname or node.name).split(".")[0])
        else:
            # match 语句的捕获名（Python 3.10+）
            for attr in ("name", "rest"):
                value = getattr(node, attr, None)
                if isinstance(value, str) and isinstance(
                    node, (ast.MatchAs, ast.MatchStar, ast.MatchMapping)
                ):
                    names.add(value)
    return names


def _module_baseline() -> set[str]:
    extra = {
        "__file__", "__name__", "__doc__", "__package__", "__spec__",
        "__loader__", "__builtins__", "__debug__", "__class__",
    }
    return set(dir(builtins)) | extra


def iter_source_files() -> list[Path]:
    files: list[Path] = []
    for target in TARGETS:
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(
                sorted(
                    p for p in target.rglob("*.py")
                    if not any(part in p.parts for part in EXCLUDED_PARTS)
                )
            )
    return files


def check_undefined_names() -> None:
    print("=== 1. 未定义名（undefined name）===")
    baseline = _module_baseline()
    files = iter_source_files()
    problems: list[str] = []
    skipped: list[str] = []

    for path in files:
        try:
            # `utf-8-sig`：文件若被编辑器加上 BOM，Python 本身能正常导入（PEP 263），
            # 但 `ast.parse` 会把那个 U+FEFF 当成非法字符报"语法错误"。
            # 用 utf-8-sig 读就不会误报；BOM 本身由第 2 项检查单独管。
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as exc:
            problems.append(f"{path.relative_to(ROOT)}:{exc.lineno}: 语法错误 {exc.msg}")
            continue
        defined = _collect_defined(tree)
        if defined is None:
            skipped.append(path.relative_to(ROOT).as_posix())
            continue
        known = defined | baseline
        seen: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in known and node.id not in seen:
                    seen[node.id] = node.lineno
        for name, line in sorted(seen.items(), key=lambda kv: kv[1]):
            problems.append(f"{path.relative_to(ROOT).as_posix()}:{line}: 未定义的名字 '{name}'")

    check(f"扫描 {len(files)} 个文件，未定义名为 0", not problems,
          f"发现 {len(problems)} 处" if problems else "")
    for problem in problems:
        print(f"        {problem}")
    if skipped:
        print(f"  [跳过] 含 `import *`、无法静态分析: {', '.join(skipped)}")


# --------------------------------------------------------------------------------------
# 2) 文件编码：.ps1 必须带 BOM，.py 必须不带
# --------------------------------------------------------------------------------------
def check_ps1_bom() -> None:
    """两个方向都要管。

    * `.ps1` **必须**带 UTF-8 BOM：PowerShell 5.1 读无 BOM 的 UTF-8 会按 GBK 解码，
      中文路径/提示全乱（v14.6 踩过，BOM 还被编辑器悄悄去掉过一次）。
    * `.py` **必须不带** BOM：Python 自己能导入（PEP 263），但 `ast.parse` 会报
      "invalid non-printable character U+FEFF"，其它工具也常被绊到。
      v15.1 就发生过一次 —— 用 PowerShell 的 `Set-Content -Encoding UTF8` 改版本号，
      它默认写 BOM，结果给 `constants.py` / `README.md` / `HANDOVER.md` 都加了 BOM。
    """
    print("=== 2. 文件编码：.ps1 带 BOM / .py 不带 ===")
    for path in BOM_REQUIRED:
        rel = path.relative_to(ROOT).as_posix()
        if not path.exists():
            check(f"{rel} 存在", False, "文件不存在")
            continue
        head = path.read_bytes()[:3]
        check(f"{rel} 带 UTF-8 BOM", head == UTF8_BOM,
              "" if head == UTF8_BOM else f"开头是 {head!r}，PowerShell 5.1 会按 GBK 解码中文")

    offenders = [
        p.relative_to(ROOT).as_posix()
        for p in iter_source_files()
        if p.read_bytes()[:3] == UTF8_BOM
    ]
    check("Python 源码都不带 BOM", not offenders,
          f"这些文件多了 BOM: {', '.join(offenders)}" if offenders else "")


# --------------------------------------------------------------------------------------
# 3) 未使用的 import
# --------------------------------------------------------------------------------------
def check_unused_imports() -> None:
    """查"导入了但没用到"的名字。

    为什么要查：本项目已经反复栽在"**写好了但没接线**"上（tar 分组、兜底 find、
    `%y`、增量扫描、`prune_meta`、CopyHere 的源、`_missing_side`）。
    未使用的 import 是同一类问题的轻量版 —— 它让人以为某个模块依赖了某个东西，
    实际并没有（例如 `app.py` 曾导入 `describe_path_kind` 却从没调用，
    真正在用的是 `ui_dialogs.py` 里的延迟导入）。

    豁免：`from __future__ import ...`、带 `noqa` 的行、`__all__` 里列出的名字
    （那是有意的再导出）。
    """
    print("=== 3. 未使用的 import ===")
    problems: list[str] = []
    files = iter_source_files()
    for path in files:
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError:
            continue  # 语法错误已由上一项报告

        imported: dict[str, int] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if (getattr(node, "module", "") or "") == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported[(alias.asname or alias.name).split(".")[0]] = node.lineno

        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                base: ast.expr = node
                while isinstance(base, ast.Attribute):
                    base = base.value
                if isinstance(base, ast.Name):
                    used.add(base.id)
        # `__all__ = [...]` 里列出的名字算"有意的再导出"
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ):
                for element in getattr(node.value, "elts", []):
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        used.add(element.value)

        for name, lineno in sorted(imported.items(), key=lambda kv: kv[1]):
            if name in used:
                continue
            line = lines[lineno - 1] if lineno - 1 < len(lines) else ""
            if "noqa" in line:
                continue
            problems.append(f"{path.relative_to(ROOT).as_posix()}:{lineno}: 未使用的 import '{name}'")

    check(f"扫描 {len(files)} 个文件，未使用的 import 为 0", not problems,
          f"发现 {len(problems)} 处" if problems else "")
    for problem in problems:
        print(f"        {problem}")


# --------------------------------------------------------------------------------------
# 4) 后台线程不能直接碰 UI
# --------------------------------------------------------------------------------------
#: 出现这些就说明后台线程直接操作了 Tk / 自己的控件。
#: 只列**确定属于 UI** 的写法，宁可漏报也不要误报（误报会让人把检查关掉）。
_UI_TOKENS: tuple[str, ...] = (
    "self.root.",
    "self.toast.",
    "self.list_local",
    "self.list_quest",
    "self.list_missing",
    "self.wall_",
    "self.cover_label",
    "self.detail_",
    "self._progress_dialog",
    "self.window.",
    "messagebox",
)


def check_worker_thread_safety() -> None:
    """后台任务函数里不许直接碰 UI。

    这是本项目**排第一**的规约：`ToastHost.show()` / 控件方法都会直接操作 Tk，
    而 Tk 不是线程安全的（原版有 11 处工作线程直接 `root.after(...)`，高负载下随机崩）。
    唯一允许的 UI 更新入口是 `TaskRunner.emit()` → 主线程的 `_pump_queue()`。

    v14.8 用这条查出 3 处：`import_songs` 与 `batch_delete` 的 worker 里
    直接调了 `self.toast.info/warning/success` —— 已改成发一条 `toast` 消息由主线程弹。

    局限：按名字匹配 `self.tasks.submit(<名字>)` 提交的函数，所以**绕过 submit 直接起
    `threading.Thread` 的代码查不到**（本项目目前没有这种写法）。
    """
    print("=== 4. 后台线程不能直接碰 UI ===")
    problems: list[str] = []
    scanned = 0
    for path in iter_source_files():
        src = path.read_text(encoding="utf-8")
        if "tasks.submit" not in src:
            continue
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError:
            continue
        lines = src.splitlines()
        submitted = set(re.findall(r"self\.tasks\.submit\(\s*(\w+)", src))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in submitted:
                continue
            scanned += 1
            body = lines[node.lineno - 1 : (node.end_lineno or node.lineno)]
            for offset, line in enumerate(body):
                text = line.strip()
                if text.startswith("#") or "self.tasks.emit" in text or text.startswith("def "):
                    continue
                if any(token in text for token in _UI_TOKENS):
                    problems.append(
                        f"{path.relative_to(ROOT).as_posix()}:{node.lineno + offset}: "
                        f"后台函数 {node.name}() 直接碰了 UI -> {text[:70]}"
                    )
    check(f"扫描 {scanned} 个后台任务函数，直接碰 UI 的为 0", not problems,
          f"发现 {len(problems)} 处" if problems else "")
    for problem in problems:
        print(f"        {problem}")


def check_settings_fields_used() -> None:
    """`Settings` 的每个字段都必须**有人读** —— 否则就是"存了不读"的半成品。

    v14.9 靠人工审计查出 5 个：`copy_overwrite` / `quest_source` 只有定义、没有任何实现
    （界面上也没开关），`scan_state` 被数据库缓存取代，`recent_local` / `recent_quest`
    更微妙 —— 应用一直在写（`remember()`），却从来没人读。

    这里只统计**读**（`ast.Load`），不统计写：只写不读正是要抓的那种。
    """
    print("=== 5. Settings 字段必须有人读 ===")
    storage = ROOT / "quest_tool" / "storage.py"
    try:
        tree = ast.parse(storage.read_text(encoding="utf-8"), filename=str(storage))
    except (OSError, SyntaxError) as exc:
        check("能解析 storage.py", False, str(exc))
        return
    fields: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.append(stmt.target.id)

    others = [p for p in iter_source_files() if p.name != "storage.py"]
    read: set[str] = set()
    for path in others:
        try:
            sub = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(sub):
            if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
                continue
            if node.attr not in fields:
                continue
            # 形如 `settings.x` / `self.settings.x` / `app.settings.x`
            # 要把整条属性链上的名字都收进来再判断 —— 只看最内层会看到 "self" 而漏判
            chain: list[str] = [node.attr]
            base: ast.expr = node.value
            while isinstance(base, ast.Attribute):
                chain.append(base.attr)
                base = base.value
            if isinstance(base, ast.Name):
                chain.append(base.id)
            if any("settings" in part.lower() for part in chain):
                read.add(node.attr)

    unread = [f for f in fields if f not in read]
    check(f"扫描 {len(fields)} 个 Settings 字段，从未被读取的为 0", not unread,
          f"发现 {len(unread)} 个" if unread else "")
    for name in unread:
        print(f"        quest_tool/storage.py: Settings.{name} 从未被读取"
              f"（要么补上实现，要么删掉它）")


def check_getattr_on_slots() -> None:
    """检查 `getattr(x, "name", None)` 里那个 `name` 是不是**永远取不到**。

    为什么加这一条（v17.0 的真缺陷）：`ui_coverwall.py` 的 `set_rating()` 里写着

        label = getattr(tile, "rating_label", None)

    但 `_Tile` 的文字控件叫 `info`，而且 `_Tile` 用了 `__slots__`（没有
    `rating_label`）—— 所以这一行**永远拿到 None**：评分改了封面墙上毫无反应，
    而且因为给了默认值 `None`，**连异常都不报**。这类"静默失效"靠读代码很难
    发现，靠动态测试也要正好点到那个功能才行。

    判据（宁可漏报，不误报）：
      * 文件里有 `class X` 且 `X.__slots__` 是**字面量元组/列表**；
      * 有 `getattr(obj, "name", <默认值>)` 形式调用；
      * `name` 不在该类的 `__slots__` 里，也不在**文件里任何** `__slots__` 里，
        也不是任何类的普通方法/属性赋值名；
      * `name` 不是**所有对象都有的通用属性**（`__name__` / `__doc__` /
        `__module__` …）—— 那些写在函数、类、模块上永远存在，
        对它们告警纯属误报（`workers.py:149` 的 `getattr(func, "__name__", ...)`
        就是这么一处）。

    只要该名字在文件里"任何可能的地方"出现过，就放过 —— 因为无法静态确定
    `obj` 到底是哪个类。
    """
    print("=== 6. getattr(x, \"名字\", 默认值) 不能永远取不到 ===")
    offenders: list[str] = []
    scanned = 0

    #: 所有对象都可能有的通用属性 —— 对它们告警一定是误报
    universal = {
        "__name__", "__doc__", "__module__", "__dict__", "__class__", "__qualname__",
        "__init__", "__repr__", "__str__", "__eq__", "__hash__", "__iter__",
        "__len__", "__getitem__", "__setitem__", "__enter__", "__exit__",
    }

    for path in iter_source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        scanned += 1

        # 文件里所有"合法属性名"的集合：__slots__ + 方法名 + 赋值出的属性
        known: set[str] = set()
        slots_seen = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                        known.add(stmt.name)
                    # 类级赋值（含 __slots__）
                    targets: list[ast.expr] = []
                    if isinstance(stmt, ast.Assign):
                        targets = list(stmt.targets)
                    elif isinstance(stmt, ast.AnnAssign):
                        targets = [stmt.target]
                    for target in targets:
                        if not isinstance(target, ast.Name):
                            continue
                        known.add(target.id)
                        if target.id != "__slots__":
                            continue
                        value = stmt.value
                        for element in getattr(value, "elts", []) or []:
                            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                                known.add(element.value)
                                slots_seen = True
            # self.x = ... 形式的属性
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                known.add(node.attr)

        if not slots_seen:
            continue  # 没有字面量 __slots__ 的类，无从判断，跳过整个文件

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "getattr"):
                continue
            if len(node.args) != 3:
                continue
            name_arg = node.args[1]
            if not (isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)):
                continue
            # 只看 `getattr(名字, "x", d)`：当第一个参数本身是属性链时
            # （`getattr(args.thread, "name", "?")`），那个对象来自外部库，
            # 本文件的属性表判断不了它 —— 一律放过，否则纯属误报。
            # 真缺陷 `getattr(tile, "rating_label", None)` 的 `tile` 是
            # 局部变量（ast.Name），照抓不误。
            if isinstance(node.args[0], ast.Attribute):
                continue
            if name_arg.value in known or name_arg.value in universal:
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{name_arg.lineno} "
                             f"getattr(…, {name_arg.value!r}, …)")
        # 本文件的属性名收集完毕

    check(f"扫描 {scanned} 个文件，永远取不到的 getattr 为 0", not offenders,
          f"发现 {len(offenders)} 处" if offenders else "")
    for item in offenders:
        print(f"        {item} —— 这个名字在该文件里从未定义过，getattr 必然返回默认值")


QT_WINDOW_METHODS = frozenset({
    "setObjectName", "setMinimumSize", "resize", "setCentralWidget",
    "setWindowTitle", "statusBar",
})


def qt_inherited_calls(tree: ast.Module) -> set[int]:
    """Allow only verified QMainWindow APIs inside a directly imported Qt subclass."""
    imports = {alias.asname or alias.name for node in tree.body
               if isinstance(node, ast.ImportFrom) and node.module == "PySide6.QtWidgets"
               for alias in node.names if alias.name == "QMainWindow"}
    return {id(call) for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
            and any(isinstance(base, ast.Name) and base.id in imports for base in cls.bases)
            for call in ast.walk(cls) if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute) and call.func.attr in QT_WINDOW_METHODS}


def check_missing_methods() -> None:
    """检查"调用了自己类里根本不存在的方法"。

    为什么加这一条（v17.2 的两个真缺陷）：**两次都是同一类错误，而且都表现为
    "点了没反应"** ——

      * `app_detail.py` 的 `toggle_preview()` 里写着 `self.tasks.token()`，
        而 `TaskRunner` 只有 `new_token()`；
      * 同一个函数里写着 `if task_token.cancelled:`，
        而 `Token` 只有 `cancel()` / `alive()`。

    两次都抛 `AttributeError`，但**按钮回调里的异常被 Tk 的命令分发吞掉**：
    界面不报错、不弹窗、什么都不做。用户看到的就是"试听功能没用"。

    判据（宁可漏报，不误报）：
      * 先把**全仓库所有文件**里出现过的属性名/函数名收集成一张总表
        —— 5 个 mixin（`app_copy.py` 等）和主类分处不同文件，
        只在本文件里找必然全是误报（第一版就是这么错的，138 处误报）；
      * 再找 `self.<名字>(...)` 的**调用**；
      * `<名字>` 不在总表里、也不是通用方法名，才报。
    """
    print("=== 7. 不能调用自己类里不存在的方法 ===")
    offenders: list[str] = []
    scanned = 0
    parsed: list[tuple[Path, ast.Module]] = []

    #: `object` / tk / 各种常见容器自带的方法；`self.x()` 对它们永远合法
    universal = {
        "__init__", "__str__", "__repr__", "__eq__", "__hash__", "__len__",
        "__iter__", "__next__", "__enter__", "__exit__", "__call__",
        "__getitem__", "__setitem__", "__delitem__", "__contains__",
        "__bool__", "__format__", "__getattr__", "__setattr__", "__delattr__",
        "close", "send", "throw", "get", "keys", "values", "items", "copy",
        "update", "append", "extend", "pop", "add", "discard", "union",
        "join", "split", "strip", "format", "startswith", "endswith",
        "replace", "lower", "upper", "encode", "decode", "read", "write",
        "seek", "flush", "wait", "notify", "notify_all", "release", "acquire",
        "set", "submit", "result", "exception", "cancel", "invoke", "pack",
        "grid", "grid_remove", "grid_forget", "pack_forget", "bind", "winfo",
        "cget", "configure", "destroy", "after", "after_cancel", "focus_set",
        "update_idletasks", "update", "mainloop", "clipboard_clear",
        "clipboard_append", "title", "geometry", "protocol", "iconbitmap",
        # tk / customtkinter 基类方法（自绘对话框大量继承使用）
        "lift", "grab_set", "grab_release", "wait_window", "focus_get",
        "winfo_exists", "winfo_toplevel", "winfo_width", "winfo_height",
        "winfo_children", "winfo_reqwidth", "winfo_reqheight", "winfo_rootx",
        "winfo_rooty", "winfo_x", "winfo_y", "winfo_ismapped", "winfo_screenwidth",
        "winfo_screenheight", "grid_columnconfigure", "grid_rowconfigure",
        "columnconfigure", "rowconfigure", "bell", "deiconify", "withdraw",
        "iconify", "wait_visibility", "focus_force", "clipboard_get",
        "transient", "resizable", "attributes", "state", "minsize", "maxsize",
        "pack", "place", "place_forget", "unbind", "bind_all", "bind_class",
        "event_generate", "after_idle", "tk", "nametowidget", "register",
        "quit", "destroy", "setvar", "getvar", "report_callback_exception",
        # customtkinter 自己的基类方法
        "_get_window_scaling", "_get_widget_scaling", "_apply_widget_scaling",
    }

    for path in iter_source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        scanned += 1
        parsed.append((path, tree))

    # ---- 第一遍：全仓库的名字总表 ----
    known: set[str] = set()
    for _path, tree in parsed:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                known.add(node.name)
                for base in node.bases:            # 基类名也算"可能提供方法"
                    if isinstance(base, ast.Name):
                        known.add(base.id)
                    elif isinstance(base, ast.Attribute):
                        known.add(base.attr)
                for stmt in node.body:
                    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                        known.add(stmt.name)
                    elif isinstance(stmt, ast.Assign):
                        for target in stmt.targets:
                            if isinstance(target, ast.Name):
                                known.add(target.id)
                            elif isinstance(target, ast.Attribute):
                                known.add(target.attr)
                    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        known.add(stmt.target.id)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                known.add(node.name)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                known.add(node.attr)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        known.add(target.id)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                known.add(node.id)

    # ---- 第二遍：找 self.<名字>() 而全仓库没见过这个"名字"的 ----
    for path, tree in parsed:
        qt_calls = qt_inherited_calls(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id == "self"):
                continue
            name = f.attr
            if name in known or name in universal or id(node) in qt_calls:
                continue
            offenders.append(
                f"{path.relative_to(ROOT)}:{node.lineno} "
                f"self.{name}() —— 全仓库没有这个方法"
            )

    check(f"扫描 {scanned} 个文件，调用不存在的方法为 0", not offenders,
          f"发现 {len(offenders)} 处" if offenders else "")
    for item in offenders:
        print(f"        {item}")


def main() -> int:
    check_undefined_names()
    check_ps1_bom()
    check_unused_imports()
    check_worker_thread_safety()
    check_settings_fields_used()
    check_getattr_on_slots()
    check_missing_methods()
    print()
    if FAILURES:
        print(f"结果：{len(FAILURES)} 项失败 -> {FAILURES}")
        return 1
    print("结果：静态检查全部通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
