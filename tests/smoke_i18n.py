"""多语言检查（ROADMAP C5）。

守三件事：

1. **两张表的键必须完全一致**、值不能为空、占位符要对得上
   —— 缺哪边、写错占位符都当场红；
2. **切换语言真的会改到界面上**：建一次真实窗口，中英各截一次文案，
   比对"确实不一样"（只测 `tr()` 返回值等于没测 UI）；**并且英文界面里
   不能再残留中文 UI 文案**；
3. **所有界面模块都不许退回内联中文**：AST 扫 `quest_tool/*.py`，发现
   "绑定到控件的中文字面量"就报错。这样 i18n 不会写着写着又散了。

**覆盖范围**：v16.8 起 `quest_tool/` 下所有会显示文案的位置都走文案表
（app / settings / wizard / dialogs / songlist / coverwall / devicepicker /
wireless / 各 mixin）。剩下允许有中文的只有：注释、日志、异常消息、
以及**数据键**（如歌曲字段名）—— 那是数据不是界面。
"""
from __future__ import annotations

import ast
import re
import shutil
import sys
import time

from _paths import ROOT, sandbox  # noqa: E402

sys.path.insert(0, str(ROOT))

from quest_tool.messages import LANGUAGES, catalog, tr  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []

#: 允许出现中文的非界面位置：注释、日志、异常、数据键
_UI_KWARGS = frozenset({
    "text", "label", "values", "placeholder_text", "title", "message", "detail",
    "button_text", "cancel_text", "default_text",
})

#: 这些行允许出现中文（注释、日志、非 UI 用途）
_CHINESE = re.compile(r"[\u4e00-\u9fff]")


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  -> ' + detail) if detail else ''}")


def pump(root, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def collect_widget_texts(widget, out: list[str]) -> None:
    """把一棵控件树里所有"文字类"属性收集起来（递归）。"""
    try:
        for option in ("text",):
            try:
                value = widget.cget(option)
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                out.append(value)
    except Exception:
        pass
    try:
        children = widget.winfo_children()
    except Exception:
        return
    for child in children:
        collect_widget_texts(child, out)


def check_catalogs() -> None:
    print("\n--- 文案表一致性 ---")
    zh, en = catalog("zh"), catalog("en")
    check("至少两套语言", len(LANGUAGES) >= 2, str(LANGUAGES))
    check("中文表非空", len(zh) > 0, f"{len(zh)} 条")
    check("英文表非空", len(en) > 0, f"{len(en)} 条")

    only_zh = sorted(set(zh) - set(en))
    only_en = sorted(set(en) - set(zh))
    check("两张表的键完全一致", not only_zh and not only_en,
          f"只在中文：{only_zh[:5]}；只在英文：{only_en[:5]}" if (only_zh or only_en) else
          f"{len(zh)} 条一一对应")

    empty = [k for k, v in list(zh.items()) + list(en.items()) if not str(v).strip()]
    check("没有空文案", not empty, str(empty[:6]))

    # 占位符必须两边一致，否则英文界面会 KeyError（或者显示成没替换的 {x}）
    mismatch = []
    for key in sorted(set(zh) & set(en)):
        placeholders = lambda s: set(re.findall(r"\{(\w+)\}", s))  # noqa: E731
        if placeholders(zh[key]) != placeholders(en[key]):
            mismatch.append(key)
    check("中英占位符一致", not mismatch, str(mismatch[:6]))

    # 每个 key 都能取到，且不会原样返回键
    keys = list(zh)
    bad = [k for k in keys if tr(k) == k and k not in zh.values()]
    check("所有键都能取到文案（不会原样返回键）", not bad, str(bad[:6]))

    # 未知键：原样返回、不抛异常
    check("未知键原样返回（界面不会空白）", tr("nope.not.exists") == "nope.not.exists",
          tr("nope.not.exists"))


def check_language_switches_ui() -> None:
    from quest_tool.app import QuestComparatorApp
    from quest_tool.messages import set_language
    from quest_tool.storage import Settings

    print("\n--- 切换语言真的改到界面上 ---")
    root_dir = sandbox("i18n")
    shutil.rmtree(root_dir, ignore_errors=True)
    songs = root_dir / "Songs"
    songs.mkdir(parents=True)

    snapshots: dict[str, list[str]] = {}
    for language in ("zh", "en"):
        set_language(language)
        settings = Settings(local_path=str(songs), device_poll_on_start=False,
                            appearance="Light", wizard_done=True, language=language)
        app = QuestComparatorApp(settings=settings)
        app.root.withdraw()
        pump(app.root, 0.6)
        texts: list[str] = []
        collect_widget_texts(app.root, texts)
        snapshots[language] = texts
        check(f"[{language}] 主界面取到了文案", len(texts) > 5, f"{len(texts)} 条")
        app.on_close()
        pump(app.root, 0.3)

    zh_texts, en_texts = set(snapshots["zh"]), set(snapshots["en"])
    changed = zh_texts - en_texts
    check("中英两套界面文案确实不同", len(changed) >= 8,
          f"中文独有 {len(changed)} 条，例如 "
          f"{sorted(t for t in changed if _CHINESE.search(t))[:3]}")

    # 「外壳已英文化」怎么断言才诚实：**只针对已转换的表面**。
    # 还没搬的是 ui_songlist / ui_coverwall / ui_dialogs / 各 mixin 的提示语
    # （见本文件顶部的覆盖面说明），所以不能笼统要求"整棵树里没有中文" ——
    # 那会把"已知未搬"当成失败，逼着人去放宽阈值。
    #
    # 注意两点：
    #   * 用**子串**匹配（标签带 emoji 前缀，如 "📁 Song folders"）；
    #   * 菜单栏**不在控件树里**（tk.Menu 没有子控件、label 也取不到），
    #     所以菜单文案改为直接问文案表，见下面。
    chrome_must_be_english = (
        "① Load", "② Compare", "③ Fill",                  # 三组标题
        "Local", "Quest",                            # 主按钮
        "Quest songs", "Local songs",                       # 双路径区
        "Duplicates", "Import playlist", "Export playlist", "Export report",  # 顶部工具入口
    )
    def has(fragment: str) -> bool:
        return any(fragment in t for t in en_texts)

    missing_en = [s for s in chrome_must_be_english if not has(s)]
    check("英文界面里外壳文案都是英文", not missing_en, f"缺：{missing_en}")

    # 菜单文案：tk.Menu 取不到文字，所以直接核对文案表
    menu_keys = ("menu.file", "menu.action", "menu.view", "menu.help",
                 "menu.file.export_csv", "menu.help.wizard", "menu.action.delete")
    menu_ok = all(not _CHINESE.search(tr(key, count=1)) for key in menu_keys)
    check("菜单文案在英文下也是英文", menu_ok,
          str([(k, tr(k)) for k in menu_keys if _CHINESE.search(tr(k))]))

    # v16.8：全量转换完成后，这条从"少于一半"收紧成"一条都不许剩"。
    # 之前宽松是因为 ui_dialogs / ui_songlist / ui_coverwall 与各 mixin
    # 本来就还没搬，把"已知未搬"当失败会逼着人去放宽阈值。
    zh_left = sorted(t for t in en_texts if _CHINESE.search(t))
    check("英文界面里没有残留的中文 UI 文案", not zh_left,
          "；".join(zh_left[:5]) if zh_left else f"中文界面 {len(zh_texts)} 条文案全部有英文版")

    # 语言设置能持久化，非法值回落
    from quest_tool.storage import Settings as S

    path = root_dir / "settings.json"
    s = S(language="en")
    s.save(path)
    check("语言能存进设置并读回来", S.load(path).language == "en", S.load(path).language)
    path.write_text('{"language": "klingon"}', encoding="utf-8")
    check("非法语言回落 zh", S.load(path).language == "zh", S.load(path).language)
    set_language("zh")


def _inline_chinese_ui_literals(path) -> list[str]:
    """找出一个 .py 里"会显示给用户"的中文字面量（AST 判定，比正则可靠）。

    判定：字符串出现在这些关键字实参里 —— `text=` / `label=` / `values=` /
    `placeholder_text=` / `message=` 等。这样注释、日志、异常消息、数据键
    （如歌曲字段名）都不会误报。
    """
    offenders: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - 静态检查第 1 项会先报
        return [f"{path.name}: 无法解析（{exc}）"]
    # User-supplied inspector has an independent CLI demo, never executed by
    # attach(). Keep scanning all attached/runtime code and every product UI.
    if path.name == "tk_inspector.py":
        tree.body = [node for node in tree.body
                     if not (isinstance(node, ast.FunctionDef) and node.name == "_run_demo")]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        hit = False
        for keyword in node.keywords:
            if keyword.arg not in _UI_KWARGS:
                continue
            value = keyword.value
            # 处理隐式字符串拼接（"a" "b" 在 AST 里会合成一个 Constant）
            if isinstance(value, ast.Constant) and isinstance(value.value, str) \
                    and _CHINESE.search(value.value):
                hit = True
        # add_command(label=...) 走的是 Menu，同样是界面文案
        if hit:
            offenders.append(f"{path.name}:{node.lineno}: {ast.unparse(node)[:72]}")
    return offenders


def check_no_regression() -> None:
    """所有界面模块里，不许再出现"绑定到控件的中文字面量"。"""
    print("\n--- 界面模块不许退回内联中文 ---")
    offenders: list[str] = []
    scanned = 0
    for path in sorted((ROOT / "quest_tool").glob("*.py")):
        if path.name in ("messages.py", "__init__.py"):
            continue  # messages.py 就是文案表本身；包初始化不显示文案
        scanned += 1
        offenders += _inline_chinese_ui_literals(path)
    check("界面模块没有内联中文文案", not offenders,
          "；".join(offenders[:5]) if offenders else f"{scanned} 个模块干净")

    # 自检：这段扫描器必须真的能抓到东西，否则"没报错"毫无意义
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        probe = tmp + "/probe.py"
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("import tkinter as tk\n"
                         "tk.Label(text='你好世界')\n"
                         "tk.Label(text='hello')\n")
        caught = _inline_chinese_ui_literals(type(path)(probe))
        demo_source = ("def _run_demo():\n    tk.Label(text='独立演示')\n"
                       "def attach():\n    tk.Label(text='接入运行')\n")
        demo = type(path)(tmp) / "tk_inspector.py"
        demo.write_text(demo_source, encoding="utf-8")
        inspector_hits = _inline_chinese_ui_literals(demo)
        type(path)(probe).write_text(demo_source, encoding="utf-8")
        product_hits = _inline_chinese_ui_literals(type(path)(probe))
    check("扫描器自检：能抓到内联中文界面文案", len(caught) == 1, str(caught))
    check("独立工具只排除CLI演示，接入运行代码仍被检查",
          len(inspector_hits) == 1 and "接入运行" in inspector_hits[0], str(inspector_hits))
    check("普通产品模块的同名演示函数仍被检查", len(product_hits) == 2, str(product_hits))


def main() -> int:
    print("=" * 78)
    print("多语言检查（文案表一致性 / 切换生效 / 不许退回内联中文）")
    print("=" * 78)
    check_catalogs()
    check_language_switches_ui()
    check_no_regression()

    print()
    print("=" * 78)
    failed = [r for r in RESULTS if not r[1]]
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    for label, _ok, detail in failed:
        print(f"  FAIL  {label}  {detail}")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
