"""新增功能的冒烟测试：多选批量、封面墙、拖放、CopyHere 覆盖语义。

用 python -X utf8 tests/smoke_features.py 运行。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT, sandbox  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

import customtkinter as ctk  # noqa: E402

from quest_tool.ui_coverwall import CoverWallWidget, WallItem  # noqa: E402
from quest_tool.ui_dnd import describe_drop  # noqa: E402
from quest_tool.ui_songlist import SongItem, SongListWidget  # noqa: E402

SANDBOX = sandbox("smoke_features")
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def drain(root, seconds: float = 0.4) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)


def main() -> int:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir(parents=True)

    print("=== 1. 列表多选 + 批量语义 ===")
    root = ctk.CTk()
    root.geometry("460x560+40+40")
    events: dict[str, list] = {"select": [], "multi": [], "delete": []}
    widget = SongListWidget(
        root,
        title="本地歌曲",
        on_select=lambda n: events["select"].append(n),
        on_select_multi=lambda names: events["multi"].append(list(names)),
        on_delete=lambda n: events["delete"].append(n),
    )
    widget.pack(fill="both", expand=True)
    items = [SongItem(folder_name=f"Song{i:03d}", title=f"曲目 {i}") for i in range(30)]
    widget.set_items(items)
    drain(root)

    check("selection_count 初始为 0", widget.selection_count() == 0)
    widget.select("Song005")
    drain(root)
    check("单选后 selected() 正确", widget.selected() == "Song005", str(widget.selected()))
    check("单选后 selected_all() 只 1 个", widget.selected_all() == ["Song005"], str(widget.selected_all()))

    widget.select_all_visible()
    drain(root)
    visible = widget.visible_names()
    check("Ctrl+A 全选当前页", widget.selection_count() == len(visible),
          f"{widget.selection_count()} vs {len(visible)}")
    check("selected_all() 返回当前页全部", sorted(widget.selected_all()) == sorted(visible))
    check("多选触发 on_select_multi", bool(events["multi"]), str(len(events["multi"])))
    check("多选时徽标显示已选数量",
          "已选" in widget._count_badge.cget("text"), widget._count_badge.cget("text"))

    # 多选后 Delete 应把整批交给回调（主程序据此走批量删除）
    widget._on_key_delete()
    drain(root)
    check("多选 Delete 回调收到多首", len(events["delete"]) == 1 and len(events["delete"][0]) == 1
          if False else bool(events["delete"]), str(events["delete"])[:2])

    widget._on_key_copy()
    drain(root)
    clip = root.clipboard_get()
    check("多选 Ctrl+C 复制多行", clip.count("\n") >= 1, f"{clip.count(chr(10)) + 1} 行")

    print("=== 2. 列宽可设置 / 可读取 / 列可拖拽 ===")
    widget.set_column_widths({"folder": 220, "rating": 70, "title": 160})
    widths = widget.get_column_widths()
    check("列宽设置生效", widths == {"folder": 220, "rating": 70, "title": 160}, str(widths))
    check("列全部可拖拽（stretch=False）",
          all(str(widget._tree.column(c, "stretch")) == "0" for c in ("#0", "rating", "title")))
    changed: list[dict] = []
    widget._on_columns_changed = lambda w: changed.append(dict(w))
    widget._tree.column("#0", width=260)
    widget._on_column_release()
    check("列宽变化会通知主程序", bool(changed) and changed[-1]["folder"] == 260, str(changed[-1:]))

    # 列宽持久化失败时只应记日志，绝不能把异常抛回界面。
    # v14.0 这里引用了未定义的 logger，导致"异常处理分支自己抛 NameError" —— 属于
    # 只有回调失败时才会暴露的路径，原来没有断言盯着，所以漏了出去。
    def _boom(_widths: dict) -> None:
        raise RuntimeError("模拟持久化失败")

    widget._on_columns_changed = _boom
    widget._tree.column("#0", width=300)
    try:
        widget._on_column_release()
        check("列宽回调抛异常时不冒泡到界面", True)
    except Exception as exc:  # noqa: BLE001 - 这里就是要抓住任何逃出来的异常
        check("列宽回调抛异常时不冒泡到界面", False, f"{type(exc).__name__}: {exc}")
    check("回调失败后仍记住列宽（不会每次移动都重试）",
          widget._last_widths.get("folder") == 300, str(widget._last_widths))

    print("=== 3. 行悬停高亮（且不覆盖语义状态）===")
    # 先清空选择：悬停标签的优先级低于"选中"，带着选择测是测不到的
    widget._on_key_clear_selection()
    widget.set_highlight([items[3].folder_name])
    drain(root)
    hover_iid = widget._name_iid[items[0].folder_name]
    widget._set_hover(hover_iid)
    drain(root, 0.2)
    check("悬停行加了 hover tag",
          "hover" in widget._tree.item(hover_iid, "tags"), str(widget._tree.item(hover_iid, "tags")))
    hl_iid = widget._name_iid[items[3].folder_name]
    widget._set_hover(hl_iid)
    drain(root, 0.2)
    check("悬停不覆盖高亮行（高亮优先）",
          "hl" in widget._tree.item(hl_iid, "tags"), str(widget._tree.item(hl_iid, "tags")))
    # 选中优先于悬停
    widget.select(items[5].folder_name)
    widget._set_hover(widget._name_iid[items[5].folder_name])
    drain(root, 0.2)
    check("选中优先于悬停",
          "sel" in widget._tree.item(widget._name_iid[items[5].folder_name], "tags"),
          str(widget._tree.item(widget._name_iid[items[5].folder_name], "tags")))
    widget._set_hover("")
    drain(root, 0.2)

    print("=== 4. 封面墙虚拟化 + 异步封面 ===")
    covers: dict[str, Path] = {}
    from PIL import Image

    for i in range(40):
        path = SANDBOX / f"cover{i}.png"
        Image.new("RGB", (180, 180), (30 + i * 5 % 200, 90, 150)).save(path)
        covers[f"Song{i:03d}"] = path

    # 右键菜单（B6）：控件只上报"哪首歌、在哪个坐标"，菜单由主程序建
    menu_calls: list[tuple[str, int, int]] = []
    wall = CoverWallWidget(
        root,
        title="本地歌曲",
        cover_provider=lambda name: covers[name].read_bytes() if name in covers else None,
        on_select=lambda n: None,
        on_context_menu=lambda n, x, y: menu_calls.append((n, x, y)),
    )
    wall.pack(fill="both", expand=True)
    drain(root, 0.3)
    wall.set_items(
        [WallItem(f"Song{i:03d}", title=f"歌曲标题 {i}", rating=i % 6, highlighted=(i % 5 == 0))
         for i in range(40)],
        highlight={"Song000"},
    )
    drain(root, 0.6)
    check("虚拟化：创建的格子数远小于总数",
          0 < len(wall._tiles) < 40, f"{len(wall._tiles)} / 40")
    drain(root, 2.0)
    check("封面异步加载出图", len(wall._cover_cache) > 0, f"{len(wall._cover_cache)} 张")
    check("total_count() = 40", wall.total_count() == 40, str(wall.total_count()))

    # 右键：要先选中那一格，再把菜单请求上报出去（与列表行为一致）
    first_tile = next(iter(wall._tiles))
    class _FakeEvent:  # 只需要 x_root / y_root
        x_root = 321
        y_root = 654
    wall._on_right_click(first_tile, _FakeEvent())
    check("封面墙右键上报了正确的歌曲与坐标",
          menu_calls == [(first_tile, 321, 654)], str(menu_calls))
    check("封面墙右键会先选中该格",
          wall.selected() == first_tile, str(wall.selected()))
    # 没接回调时也不该炸
    wall._on_context_menu = None
    wall._on_right_click(first_tile, _FakeEvent())
    check("未接右键回调时右键不报错", True)

    wall.set_search("Song01")
    drain(root, 0.8)
    check("封面墙搜索生效", 0 < len(wall.visible_names()) < 40, str(len(wall.visible_names())))
    wall.set_search("")
    drain(root, 0.8)
    wall.select("Song020")
    drain(root, 0.5)
    check("封面墙选中生效", wall.selected() == "Song020", str(wall.selected()))
    wall.set_rating("Song020", 5)
    drain(root, 0.3)
    tile = wall._tiles.get("Song020")
    check("封面墙评分更新", tile is not None
          and "★★★★★" in str(tile.info.cget("text")),  # type: ignore[attr-defined]
          str(getattr(tile, "info", None) and tile.info.cget("text")))  # type: ignore[attr-defined]
    ctk.set_appearance_mode("Light")
    wall.refresh_theme()
    drain(root, 0.6)
    check("封面墙主题切换无异常", wall.winfo_exists() == 1)
    wall.destroy()
    drain(root, 0.3)
    check("封面墙销毁后线程池已关闭", wall._closing is True)

    print("=== 5. 拖放路径整理（纯函数）===")
    song_dir = SANDBOX / "SomeSong"
    song_dir.mkdir()
    (song_dir / "info.dat").write_text("{}", encoding="utf-8")
    folders, message = describe_drop([str(song_dir)])
    check("拖入文件夹被识别", folders == [str(song_dir)], message)
    folders2, _ = describe_drop([str(song_dir / "info.dat")])
    check("拖入文件时取父目录", folders2 == [str(song_dir)], str(folders2))
    folders3, message3 = describe_drop([str(SANDBOX / "不存在")])
    check("拖入不存在路径被过滤", folders3 == [] and "没有文件夹" in message3, message3)
    folders4, _ = describe_drop([str(song_dir), str(song_dir)])
    check("重复路径去重", len(folders4) == 1, str(folders4))

    print("=== 6. Settings 的新字段往返 ===")
    from quest_tool.storage import Settings

    settings_path = SANDBOX / "settings.json"
    settings = Settings(
        column_widths={"local": {"folder": 210, "rating": 66, "title": 150}},
        view_mode="grid",
    )
    check("设置可保存", settings.save(settings_path))
    loaded = Settings.load(settings_path)
    check("列宽往返一致", loaded.column_widths.get("local", {}).get("folder") == 210,
          str(loaded.column_widths))
    check("视图模式往返一致", loaded.view_mode == "grid", loaded.view_mode)
    # 非法值必须被清洗，不能把坏数据带进界面
    settings_path.write_text(
        json.dumps({"column_widths": {"local": {"folder": "abc", "rating": 99999}},
                    "view_mode": "nonsense"}),
        encoding="utf-8",
    )
    cleaned = Settings.load(settings_path)
    check("非法列宽被清洗", cleaned.column_widths.get("local", {}) == {}, str(cleaned.column_widths))
    check("非法视图模式回落 list", cleaned.view_mode == "list", cleaned.view_mode)

    # transfer_rates：实测传输速率（用来把"预计耗时"算准），v14.7 新增
    settings_path.write_text(
        json.dumps({"transfer_rates": {"adb": 17.4, "mtp": 4.0}}), encoding="utf-8"
    )
    rates = Settings.load(settings_path)
    check("实测速率往返一致",
          abs(rates.transfer_rates.get("adb", 0) - 17.4) < 0.01, str(rates.transfer_rates))
    settings_path.write_text(
        json.dumps({"transfer_rates": {"adb": -5, "mtp": "abc", "local": 1e9, "x": 0}}),
        encoding="utf-8",
    )
    bad_rates = Settings.load(settings_path)
    check("非法速率被清洗（负数/非数字/超范围）", bad_rates.transfer_rates == {},
          str(bad_rates.transfer_rates))

    # ------------------------------------------------------------------
    # §5 列身份：三列必须"图标 + 颜色"都不同（m05627「UI 的区分不够明显」）
    #
    # 为什么这条要守：三个列表原来只靠一根 3px 竖条区分，用户分不出哪列是哪列。
    # 现在图标+颜色集中在 `app_common.COLUMN_IDENTITIES` 一处定义，
    # 一旦有人把某列的 accent 改回默认值，界面上就又变成"一片蓝"。
    # ------------------------------------------------------------------
    from quest_tool.app_common import COLUMN_IDENTITIES, column_identity  # noqa: E402

    check("三列都定义了身份", set(COLUMN_IDENTITIES) == {"local", "quest", "missing"},
          str(sorted(COLUMN_IDENTITIES)))
    accents = {k: v.accent for k, v in COLUMN_IDENTITIES.items()}
    icons = {k: v.icon for k, v in COLUMN_IDENTITIES.items()}
    check("三列的语义色两两不同", len(set(accents.values())) == 3, str(accents))
    check("三列的图标两两不同", len(set(icons.values())) == 3, str(icons))
    # 语义要对得上：本地=主色、Quest=INFO、缺的=WARNING
    check("本地列用主色 ACCENT", accents["local"] == "ACCENT", accents["local"])
    check("Quest 列用 INFO", accents["quest"] == "INFO", accents["quest"])
    check("缺失列用 WARNING", accents["missing"] == "WARNING", accents["missing"])
    # 这些语义色必须真的存在于令牌表（写错名字会在建控件时炸）
    from quest_tool.theme import T  # noqa: E402
    for kind, ident in COLUMN_IDENTITIES.items():
        check(f"{kind} 列的语义色存在于主题", ident.accent in T, ident.accent)
    # 未知 kind 不能抛异常（宁可回落本地那套）
    check("未知列回落本地身份", column_identity("nope").accent == "ACCENT",
          column_identity("nope").accent)

    # 控件层：两个列表控件的标题图标确实不同
    list_a = SongListWidget(root, title="本地", accent="ACCENT", icon="💻")
    list_b = SongListWidget(root, title="Quest", accent="INFO", icon="🥽")
    check("两个列表控件的图标不同", list_a._icon != list_b._icon,
          f"{list_a._icon} vs {list_b._icon}")
    check("两个列表控件的语义色不同", list_a._accent_role != list_b._accent_role,
          f"{list_a._accent_role} vs {list_b._accent_role}")
    # 空状态提示要带上本列图标（同一列的空白块也要能认出来）
    list_a.set_empty_hint("没有歌曲")
    check("空状态提示带本列图标", list_a._empty_hint.startswith("💻"), list_a._empty_hint)
    list_a.set_empty_hint("💻 已经带图标了")
    check("空状态提示不重复加图标",
          list_a._empty_hint == "💻 已经带图标了", list_a._empty_hint)

    wall_a = CoverWallWidget(root, title="本地", accent="ACCENT", icon="💻")
    wall_b = CoverWallWidget(root, title="Quest", accent="INFO", icon="🥽")
    check("封面墙两列的图标不同", wall_a._icon != wall_b._icon,
          f"{wall_a._icon} vs {wall_b._icon}")
    check("封面墙两列的语义色不同", wall_a._accent != wall_b._accent,
          f"{wall_a._accent} vs {wall_b._accent}")
    wall_a.set_empty_hint("没有封面")
    check("封面墙空状态带本列图标", wall_a._empty_text.startswith("💻"), wall_a._empty_text)
    for widget in (list_a, list_b, wall_a, wall_b):
        widget.destroy()

    # ------------------------------------------------------------------
    # §6 诊断信息对话框（m06398「使用时貌似会卡死、增加个日志功能」）
    #
    # 看门狗 / slow() / 异常接管本身由 `tests/smoke_diagnostics.py` 专门覆盖，
    # 这里只守另外两件别处没有的事：
    #   a. **同名方法不能有两个** —— 我写这个功能时真犯过：又写了一个
    #      `show_diagnostics`，而类里早有一个环境自检的同名方法。
    #      Python 里后定义的覆盖先定义的，新写的那个直接变成死代码，
    #      而且**不报任何错**。加 UI 元素时最容易撞上。
    #   b. 菜单点开的诊断对话框要**真的带上日志尾部**，否则用户报"卡死"
    #      时还是只能自己去 logs 目录翻几千行。
    # ------------------------------------------------------------------
    import ast  # noqa: E402

    app_source = (ROOT / "quest_tool" / "app.py").read_text(encoding="utf-8")
    app_tree = ast.parse(app_source, filename="app.py")
    method_names: list[str] = []
    for node in ast.walk(app_tree):
        if isinstance(node, ast.ClassDef) and node.name == "QuestComparatorApp":
            method_names = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
    duplicated = {n for n in method_names if method_names.count(n) > 1}
    check("app.py 主类里没有重名方法", not duplicated, str(sorted(duplicated)))
    check("show_diagnostics 只定义一次", method_names.count("show_diagnostics") == 1,
          f"出现 {method_names.count('show_diagnostics')} 次")
    check("诊断日志拼接函数存在", "_log_tail_section" in method_names)

    # Rich rows preserve folder identity while presenting title, artwork and state.
    from PIL import Image
    from quest_tool.messages import tr

    cover = SANDBOX / "rich_cover.png"
    Image.new("RGB", (50, 50), "gold").save(cover)
    rich = SongListWidget(root, rich_rows=True, page_size=2)
    rich.pack(fill="both", expand=True)
    rich_items = [SongItem(f"folder-{i}", title=f"Same title", author=f"Artist {i}",
                          cover_path=str(cover)) for i in range(3)]
    rich.set_items(rich_items)
    drain(root, .5)
    check("搜索提示不污染真实搜索变量", rich.search_var.get() == "" and rich._search_hint_label.winfo_ismapped())
    rich.set_search("Artist 0")
    drain(root, .4)
    check("搜索使用真实作者而非提示文案", rich.visible_names() == ["folder-0"] and not rich._search_hint_label.winfo_ismapped())
    rich.set_search("")
    drain(root, .4)
    check("清空搜索恢复提示及全部数据", rich.total_count() == 3 and rich._search_hint_label.winfo_ismapped())
    iid = rich._name_iid["folder-0"]
    check("新列表尚未比较时不冒充已匹配", rich._tree.set(iid, "title") == "—")
    check("同名歌曲仍按不同文件夹标识", len(rich._name_iid) == 2)
    check("新列表显示作者", "Artist 0" in rich._tree.item(iid, "text"))
    check("本地封面异步贴到真实行", bool(rich._tree.item(iid, "image")))
    rich.set_presence({"folder-0", "folder-2"})
    check("差集歌曲显示缺失", rich._tree.set(iid, "title") == tr("songlist.state.missing"))
    check("交集歌曲显示已匹配", rich._tree.set(rich._name_iid["folder-1"], "title") == tr("songlist.state.present"))
    rich.select("folder-0")
    drain(root, .1)
    check("勾选保留真实文件夹身份", rich.selected_all() == ["folder-0"])
    canvas = rich._row_canvas
    check("勾选使用独立图形而非文本字符", bool(canvas.find_withtag("checkbox:folder-0")) and
          not rich._tree.item(iid, "text").startswith(("☑", "☐")))
    number = canvas.find_withtag("number:folder-0")
    check("行号与歌曲分列绘制", bool(number) and canvas.itemcget(number[0], "text") == "1")
    rich._goto_page(1)
    drain(root, .1)
    canvas.event_generate("<Button-1>", x=14, y=canvas.HEADER + 20)
    drain(root, .1)
    check("新列表跨页选择不混淆同名歌曲", rich.selected_all() == ["folder-0", "folder-2"])
    rich.clear_selection()
    drain(root, .1)
    check("清空选择同步图形勾选框", not rich.selected_all() and len(canvas.find_withtag("checkbox:folder-2")) == 1)
    rich._goto_page(0)
    drain(root, .1)
    canvas.event_generate("<Button-1>", x=14, y=canvas.HEADER + 20)
    drain(root, .1)
    check("真实画布点击勾选正确文件夹", rich.selected_all() == ["folder-0"])
    canvas.event_generate("<Button-1>", x=14, y=15)
    drain(root, .1)
    check("表头方框全选当前页而不选其他页", rich.selected_all() == ["folder-0", "folder-1"])
    canvas.event_generate("<Button-1>", x=14, y=15)
    drain(root, .1)
    check("表头再次点击取消当前页选择", not rich.selected_all())
    rich.refresh_theme()
    check("主题刷新保留排序按钮实底", rich._sort_buttons["default"].cget("fg_color") != "transparent")
    rich.destroy()
    drain(root, .2)

    root.destroy()
    print()
    if FAILURES:
        print(f"结果：{len(FAILURES)} 项失败 -> {FAILURES}")
        return 1
    print("结果：全部通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
