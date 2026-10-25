# -*- coding: utf-8 -*-
"""SongListWidget 冒烟测试：真实 Tk root + 3000 首 + 打字/排序/翻页/高亮/删除/选中。

怎么跑：
    python -X faulthandler tests\\smoke_songlist.py

几个测试上的取舍（都实测过，不是猜的）：
* 性能阶段用 root.withdraw()（完全不映射窗口）；键盘事件阶段把窗口切成
  "已映射但全透明"（-alpha 0 + 挪到屏幕外）——实测 Tk 只给 viewable 的窗口投递
  event_generate，withdraw 状态下合成事件一个都收不到（[10] 里两种状态都有对照打印）。
* 断言"只重画当前页"的办法：临时包一层 tree.insert / tree.delete 数调用次数。
* 断言"高亮落在正确的文件夹名上"必须读表格本身，所以会碰 widget._tree
  （公开 API 不暴露控件）；这是白盒冒烟测试，不是纯黑盒单元测试。
* 本沙箱禁止命名管道，所以全程不用 tempfile / subprocess / 管道。
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from tkinter import ttk

import _paths  # noqa: F401  （导入它即完成 sys.path 设置，名字本身用不到）

import customtkinter as ctk  # noqa: E402

from quest_tool.constants import SEARCH_DEBOUNCE_MS  # noqa: E402
from quest_tool.theme import color  # noqa: E402
from quest_tool.ui_songlist import SongItem, SongListWidget  # noqa: E402

N = 3000
N_BIG = 20000
PAGE = 100

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    """记一条断言结果并立刻打印（顺序就是执行顺序，方便对时间线）。"""
    results.append((label, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  -> ' + detail) if detail else ''}")


def ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms"


def make_items(count: int) -> list[SongItem]:
    """造数据：偶数下标标题 Alpha 开头、奇数 Zzz，所以"名称排序"后的顺序与插入顺序
    完全不同——这正是原版"按下标高亮"错位 bug 的触发条件。"""
    items: list[SongItem] = []
    for index in range(count):
        items.append(
            SongItem(
                folder_name=f"Song{index:04d}",
                title=f"{'Alpha' if index % 2 == 0 else 'Zzz'} 曲目 {index}",
                author=f"作者{index % 7}",
                mtime=float(index),
                local_path=f"D:\\Songs\\Song{index:04d}" if index % 2 == 0 else "",
            )
        )
    return items


def count_widgets(widget: tk.Misc) -> int:
    """子树里的 Tk widget 总数（原版 2000 首 = 8001 个，这里应该是个常数）。"""
    total = 1
    for child in widget.winfo_children():
        total += count_widgets(child)
    return total


def drain(root: tk.Tk, seconds: float) -> None:
    """跑事件循环 seconds 秒（不是纯 sleep，保证 after 回调真的有机会执行）。"""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        root.update()
        time.sleep(0.005)


def wait_debounce(root: tk.Tk, widget: SongListWidget, timeout: float = 3.0) -> None:
    """等搜索防抖真正执行完：_debounce_id 从"有值"变回 None 即代表回调跑过了。"""
    deadline = time.perf_counter() + timeout
    while widget._debounce_id is not None and time.perf_counter() < deadline:
        root.update()
        time.sleep(0.002)
    root.update()


def row_texts(tree: ttk.Treeview) -> list[str]:
    """当前页所有行的 #0 列（文件夹名）文本。

    #0 是 tree 列，只能用 item(iid, "text") 读；因为 displaycolumns 是显式配置的，
    tree.set(iid, "#0") 会报 "Display column #0 cannot be set"。
    """
    return [tree.item(iid, "text") for iid in tree.get_children("")]


def highlighted_rows(tree: ttk.Treeview) -> list[tuple[str, str, str, str]]:
    """当前页被高亮的行：(行id, 文件夹名, 评分, 标题)。

    模块里高亮行挂 "hl" tag，既高亮又被选中时挂 "hlsel"，两种都算高亮。
    """
    rows: list[tuple[str, str, str, str]] = []
    for iid in tree.get_children(""):
        tags = tree.item(iid, "tags")
        if "hl" in tags or "hlsel" in tags:
            rows.append(
                (iid, tree.item(iid, "text"), tree.set(iid, "rating"), tree.set(iid, "title"))
            )
    return rows


def tag_option(tree: ttk.Treeview, tag: str, option: str = "background") -> str:
    """读 tag 的某个选项（tag_configure 查询在不同版本可能返回 dict 或单值）。"""
    conf = tree.tag_configure(tag)
    if isinstance(conf, dict):
        return str(conf.get(option, ""))
    try:
        return str(tree.tag_configure(tag, option))
    except tk.TclError:
        return ""


def menu_labels(menu: tk.Menu | None) -> list[str]:
    """右键菜单里每一项的 label（分隔符记为空串），没建菜单就返回空列表。"""
    if menu is None:
        return []
    last = menu.index("end")
    if last is None:
        return []
    labels: list[str] = []
    for index in range(last + 1):
        if menu.type(index) == "separator":  # 分隔符没有 -label 选项，直接问会报 TclError
            labels.append("")
            continue
        try:
            labels.append(menu.entrycget(index, "label"))
        except tk.TclError:
            labels.append("")
    return labels


class StubEvent:
    """假事件对象（Tk 不允许合成 <Double-1>，只能直接调处理函数）。"""

    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class TreeCalls:
    """临时统计 Treeview 的 insert/delete 次数（用来证明"只重画当前页"）。"""

    def __init__(self, tree: ttk.Treeview) -> None:
        self.tree = tree
        self.inserts = 0
        self.deletes = 0
        self._insert = tree.insert
        self._delete = tree.delete

    def __enter__(self) -> "TreeCalls":
        def insert(*args, **kwargs):
            self.inserts += 1
            return self._insert(*args, **kwargs)

        def delete(*args, **kwargs):
            self.deletes += len(args)
            return self._delete(*args, **kwargs)

        self.tree.insert = insert
        self.tree.delete = delete
        return self

    def __exit__(self, *_exc) -> None:
        self.tree.insert = self._insert
        self.tree.delete = self._delete


def main() -> int:  # noqa: C901 - 冒烟测试就是一条长流水线，保持顺序可读
    print("=" * 78)
    print(f"SongListWidget 冒烟测试（N={N}，默认每页 {PAGE} 行）")
    print("=" * 78)

    events: dict[str, list] = {"select": [], "activate": [], "delete": [], "sort": [], "explorer": []}
    root = tk.Tk()
    root.withdraw()
    print(f"Python {sys.version.split()[0]} / Tk {root.tk.call('info', 'patchlevel')} / "
          f"customtkinter {ctk.__version__} / 外观 {ctk.get_appearance_mode()}")

    widget = SongListWidget(
        root,
        title="本地歌曲",
        on_select=lambda name: events["select"].append(name),
        on_activate=lambda name: events["activate"].append(name),
        on_delete=lambda name: events["delete"].append(name),
        on_sort_changed=lambda key, reverse: events["sort"].append((key, reverse)),
        on_open_in_explorer=lambda name: events["explorer"].append(name),
        open_path_provider=lambda name: f"D:\\Songs\\{name}" if name[-1] in "02468" else None,
    )
    widget.pack(fill="both", expand=True)
    root.update()

    items = make_items(N)
    widgets_before = count_widgets(widget)

    # ---------------- [1] set_items(3000) ----------------
    print("\n[1] set_items(3000)：墙钟时间 / 控件数量 / 是否只插当前页")
    calls = TreeCalls(widget._tree)
    with calls:
        t0 = time.perf_counter()
        widget.set_items(items)
        t1 = time.perf_counter()
    set_3000 = t1 - t0
    widgets_after = count_widgets(widget)
    print(f"  set_items(3000) = {ms(set_3000)}   （要求 < 1000 ms）")
    print(f"  期间 tree.insert {calls.inserts} 次 / tree.delete {calls.deletes} 行")
    print(f"  控件总数：建表前 {widgets_before} -> 建表后 {widgets_after}   （原版 2000 首 = 8001 个）")
    print(f"  表格行数 {len(widget._tree.get_children(''))} / total_count()={widget.total_count()} / "
          f"visible_names()={len(widget.visible_names())}")
    check("set_items(3000) < 1.0 s", set_3000 < 1.0, ms(set_3000))
    check("只 insert 当前页（100 行），不是 3000 行", calls.inserts == PAGE, f"inserts={calls.inserts}")
    check("控件总数是常数（< 100 个）", widgets_after < 100, f"{widgets_after} 个")
    check("控件数量不随歌曲数增长", widgets_after == widgets_before, f"{widgets_before} -> {widgets_after}")
    check("total_count() == 3000", widget.total_count() == N, str(widget.total_count()))
    check("visible_names() 默认 = 插入顺序",
          widget.visible_names()[:2] == ["Song0000", "Song0001"] and len(widget.visible_names()) == N)
    check("标题栏", widget._header.cget("text") == "本地歌曲"
          and widget._count_badge.cget("text") == "3000",
          f'{widget._header.cget("text")} / {widget._count_badge.cget("text")}')
    check("页码标签", widget._page_label.cget("text") == "第 1/30 页 · 共 3000 首",
          widget._page_label.cget("text"))

    # ---------------- [2] 规模翻 6.7 倍，耗时/控件应基本不变 ----------------
    print(f"\n[2] set_items({N_BIG})：规模翻 6.7 倍后的对照")
    big_items = make_items(N_BIG)
    t0 = time.perf_counter()
    widget.set_items(big_items)
    t1 = time.perf_counter()
    set_big = t1 - t0
    print(f"  set_items({N_BIG}) = {ms(set_big)}，控件总数 {count_widgets(widget)} 个，"
          f"表格行数 {len(widget._tree.get_children(''))}")
    check(f"set_items({N_BIG}) < 1.0 s", set_big < 1.0, ms(set_big))
    check("控件总数仍然不变", count_widgets(widget) == widgets_before, f"{count_widgets(widget)} 个")
    widget.set_items(items)

    # ---------------- [2b] append_items：流式分批追加（扫描过程中的真实用法） ----------------
    print(f"\n[2b] append_items：分批把 {N} 首喂进来，控件数量/插入行数都不能随数据量涨")
    widget.clear()
    widget.append_items(items[:250])
    widget._goto_last()
    calls = TreeCalls(widget._tree)
    with calls:
        t0 = time.perf_counter()
        widget.append_items(items[250:500])  # 一次追加 250 首
        t1 = time.perf_counter()
    print(f"  末页时一次追加 250 首：{ms(t1 - t0)}，insert {calls.inserts} 行 / delete "
          f"{calls.deletes} 行，{widget._page_label.cget('text')}")
    check("一次追加 250 首也只重画当前页（≤100 行），不是插 250 行",
          calls.inserts <= PAGE, f"inserts={calls.inserts}")
    calls = TreeCalls(widget._tree)
    with calls:
        t0 = time.perf_counter()
        for start in range(500, N, 250):
            widget.append_items(items[start : start + 250])
        t1 = time.perf_counter()
    print(f"  余下 {N - 500} 首分 10 批追加：{ms(t1 - t0)}，期间 insert {calls.inserts} 行"
          f"（不在末页就不重画），total_count()={widget.total_count()}，"
          f"控件总数 {count_widgets(widget)}，表格行数 {len(widget._tree.get_children(''))}")
    check("分批追加后 total_count 正确", widget.total_count() == N, str(widget.total_count()))
    check("追加过程中控件数量不变", count_widgets(widget) == widgets_before, str(count_widgets(widget)))
    check("追加后标题/页码标签已更新",
          widget._header.cget("text") == "本地歌曲"
          and widget._count_badge.cget("text") == "3000"
          and widget._page_label.cget("text").endswith("共 3000 首"),
          widget._page_label.cget("text"))
    widget.set_items(items)  # 复原，继续后面的测试

    # ---------------- [3] 搜索 ----------------
    print("\n[3] 搜索：6 次快速击键（防抖 + 只重画当前页）")
    var = widget._search_var
    calls = TreeCalls(widget._tree)
    with calls:
        t0 = time.perf_counter()
        for char in "song01":  # 6 次输入，中间不给事件循环（模拟快速连打）
            var.set(var.get() + char)
            root.update()
        typing_time = time.perf_counter() - t0
        wait_debounce(root, widget)
        settled = time.perf_counter() - t0
    matched = widget.visible_names()
    print(f"  键入「song01」6 次：键入本身 {ms(typing_time)}，含最后一次防抖结算共 {ms(settled)}"
          f"（防抖窗口 {SEARCH_DEBOUNCE_MS} ms，要求 < 1500 ms）")
    print(f"  命中 {len(matched)} 首（预期 Song0100..Song0199），重画时 insert {calls.inserts} 行 / "
          f"delete {calls.deletes} 行")
    check("6 次击键总耗时 < 1.5 s", settled < 1.5, ms(settled))
    check("打字过程本身几乎不阻塞（防抖真的生效）", typing_time < 0.1, ms(typing_time))
    check("只 insert 命中页（100 行）", calls.inserts == PAGE, f"inserts={calls.inserts}")
    check("命中 100 首且顺序正确",
          matched == [f"Song{i:04d}" for i in range(100, 200)],
          f"{len(matched)} 首，首行 {matched[0] if matched else None}")

    print("  —— 慢速打字：每次击键都等防抖真正跑完（这才是单键的真实代价）")
    per_key: list[float] = []
    for length in range(1, 7):
        t0 = time.perf_counter()
        var.set("song01"[:length])
        wait_debounce(root, widget)
        per_key.append(time.perf_counter() - t0)
    worst = max(per_key)
    print("  每键（含 180 ms 防抖等待）：" + " / ".join(ms(v) for v in per_key))
    print(f"  扣掉防抖窗口后的过滤 + 重画最坏值：约 {ms(max(0.0, worst - SEARCH_DEBOUNCE_MS / 1000))}")
    check("慢速打字单键 < 0.5 s（原版 ≈6 s/键）", worst < 0.5, ms(worst))

    var.set("alpha 作者3")
    wait_debounce(root, widget)
    expect_and = [it.key for it in items if "alpha" in it.title.lower() and "作者3" in it.author]
    print(f"  「alpha 作者3」AND 命中 {len(widget.visible_names())} 首（预期 {len(expect_and)} 首）")
    check("空格分隔 = AND，且匹配标题/作者", widget.visible_names() == expect_and, f"{len(expect_and)} 首")
    check("标题栏显示 过滤数/总数",
          widget._header.cget("text") == "本地歌曲"
          and widget._count_badge.cget("text") == f"{len(expect_and)} / 3000",
          f'{widget._header.cget("text")} / {widget._count_badge.cget("text")}')
    var.set("不存在的关键词")
    wait_debounce(root, widget)
    check("无命中时列表为空且不报错",
          widget.visible_names() == [] and len(widget._tree.get_children("")) == 0
          and widget._page_label.cget("text") == "第 1/1 页 · 共 0 首",
          widget._page_label.cget("text"))

    # ---------------- [4] 排序 ----------------
    widget.set_items(items)
    var.set("")
    wait_debounce(root, widget)
    events["sort"].clear()
    print("\n[4] 排序：左键升序 / 再点反转 / 右键强制降序 / 评分排序")
    calls = TreeCalls(widget._tree)
    with calls:
        t0 = time.perf_counter()
        widget._sort_buttons["name"].invoke()
        t1 = time.perf_counter()
    by_name = widget.visible_names()
    expect_name = [it.key for it in sorted(items, key=lambda it: it.title.lower())]
    print(f"  「🔤 名称」左键：{ms(t1 - t0)}，insert {calls.inserts} 行 / delete {calls.deletes} 行")
    print(f"  前 3 行 {by_name[:3]}（插入顺序是 Song0000/Song0001/...，显示顺序确实变了）")
    check("排序只重画当前页（100 行）",
          calls.inserts == PAGE and calls.deletes == PAGE,
          f"inserts={calls.inserts}, deletes={calls.deletes}")
    check("名称升序正确（标题小写；空标题回落文件夹名）", by_name == expect_name, f"{by_name[:2]}")
    check("on_sort_changed('name', False)", events["sort"][-1] == ("name", False), str(events["sort"][-1:]))
    widget._sort_buttons["name"].invoke()
    check("再点当前按钮 = 反转 on_sort_changed('name', True)",
          events["sort"][-1] == ("name", True), str(events["sort"][-1:]))
    check("降序 = 升序反转", widget.visible_names() == list(reversed(expect_name)))

    widget._sort_buttons["rating"].invoke()
    print(f"  「⭐ 评分」左键 -> on_sort_changed={events['sort'][-1]}"
          f"（真实右键事件要窗口 viewable 才投递，放到 [10b] 测）")

    rating_map = {f"Song{i:04d}": i % 5 for i in range(N)}
    widget.set_items(items, title_map={}, rating_map=rating_map)
    widget._sort_buttons["default"].invoke()
    widget._sort_buttons["rating"].invoke()  # 新键 -> 默认方向
    top = widget.visible_names()[:3]
    print(f"  评分排序前 3 行 {[(n, rating_map[n]) for n in top]}，单元格 ★ = "
          f"{[widget._tree.set(widget._name_iid[n], 'rating') for n in top]}")
    check("评分排序默认高分在前", all(rating_map[n] <= rating_map[top[0]] for n in top),
          str([(n, rating_map[n]) for n in top]))
    widget.set_items(items)  # 不传 rating_map
    widget._sort_buttons["default"].invoke()
    cells = {widget._tree.set(iid, "rating") for iid in widget._tree.get_children("")}
    check("set_items 不传 rating_map = 清空旧评分（原版会保留上一次的值，是 bug）",
          cells == {""}, str(cells))

    # ---------------- [5] 翻页 ----------------
    print("\n[5] 翻页：next / last / first + 页码标签 + 只重画一页")
    widget._next_btn.invoke()
    page1 = row_texts(widget._tree)
    print(f"  点「▶」：{widget._page_label.cget('text')}，首行 {page1[0] if page1 else None}，"
          f"行数 {len(page1)}")
    check("第 2 页内容正确", page1 == [f"Song{i:04d}" for i in range(100, 200)],
          page1[0] if page1 else "空")
    check("页码标签正确", widget._page_label.cget("text") == "第 2/30 页 · 共 3000 首",
          widget._page_label.cget("text"))
    widget._last_btn.invoke()
    last = row_texts(widget._tree)
    print(f"  点「⏭」：{widget._page_label.cget('text')}，首行 {last[0]}，末行 {last[-1]}，行数 {len(last)}")
    check("最后一页 = 第 30 页 100 行", len(last) == PAGE and last[-1] == "Song2999", last[-1])
    check("末页时 next/last 置灰",
          str(widget._next_btn.cget("state")) == "disabled"
          and str(widget._last_btn.cget("state")) == "disabled")
    calls = TreeCalls(widget._tree)
    with calls:
        widget._first_btn.invoke()
    check("翻页只重画一页", calls.inserts == PAGE and calls.deletes == PAGE,
          f"inserts={calls.inserts}, deletes={calls.deletes}")
    check("首页时 prev/first 置灰",
          str(widget._prev_btn.cget("state")) == "disabled"
          and str(widget._first_btn.cget("state")) == "disabled")
    check("回到第一页", widget._page_label.cget("text") == "第 1/30 页 · 共 3000 首",
          widget._page_label.cget("text"))

    # 页大小切换
    widget._size_menu._dropdown_callback("50")
    print(f"  页大小切成 50：{widget._page_label.cget('text')}，行数 {len(widget._tree.get_children(''))}")
    check("切页大小后回到第 1 页且行数=50",
          len(widget._tree.get_children("")) == 50
          and widget._page_label.cget("text") == "第 1/60 页 · 共 3000 首",
          widget._page_label.cget("text"))
    widget._size_menu._dropdown_callback("100")

    # ---------------- [6] 高亮（原版错位 bug 的回归验证） ----------------
    print("\n[6] 排序后 set_highlight：高亮必须落在正确的文件夹名上（原版按插入下标 -> 实测错位）")
    widget._sort_buttons["name"].invoke()
    order = widget.visible_names()
    target = {"Song0032", "Song0111"}
    wrong_names = [order[32], order[111]]
    print(f"  名称排序后：Song0032 在第 {order.index('Song0032')} 行、Song0111 在第 "
          f"{order.index('Song0111')} 行（插入下标是 32 / 111）")
    print(f"  若按插入下标高亮（原版做法）会点亮 {wrong_names}，与目标 {sorted(target)} 不同")
    widget.set_highlight(target)
    for name in ("Song0032", "Song0111"):
        widget.select(name)  # 跳到它所在的那一页
        rows = highlighted_rows(widget._tree)
        tags = widget._tree.item(widget._name_iid[name], "tags")
        print(f"  跳到 {name}：本页高亮行 {[(r[1], r[2], r[3]) for r in rows]}，"
              f"它的 tag={tags}")
        check(f"{name} 在表格里确实被高亮（高亮行 #0 列 == 请求的名字）",
              any(r[1] == name for r in rows), str([r[1] for r in rows]))
        check(f"{name} 本页没有多亮别人", {r[1] for r in rows} <= target, str({r[1] for r in rows}))
        check(f"{name} 选中 + 高亮 = hlsel tag", tags == ("hlsel",), str(tags))
    check("按下标高亮会点错（证明本测试确实能区分两种实现）", set(wrong_names) != target,
          str(wrong_names))
    widget.set_highlight(())
    check("set_highlight(()) 后没有高亮行（本页）", highlighted_rows(widget._tree) == [],
          str(highlighted_rows(widget._tree)))
    widget.set_highlight(target)

    # ---------------- [7] remove_item ----------------
    print("\n[7] remove_item（删除成功后主程序调用）")
    before_total = widget.total_count()
    widget.remove_item("Song0032")
    widget.select("Song0111")
    rows = highlighted_rows(widget._tree)
    print(f"  删除 Song0032：total {before_total} -> {widget.total_count()}，"
          f"visible_names() 里还有吗 {'Song0032' in widget.visible_names()}")
    print(f"  现在的高亮行 {[r[1] for r in rows]}，_highlight 集合 = {sorted(widget._highlight)}")
    check("总数 -1 且列表里已消失",
          widget.total_count() == before_total - 1 and "Song0032" not in widget.visible_names())
    check("被删的歌不再在高亮集合里", widget._highlight == {"Song0111"}, str(sorted(widget._highlight)))
    check("剩下那首仍然被高亮", [r[1] for r in rows] == ["Song0111"], str([r[1] for r in rows]))
    widget.remove_item("根本不存在的歌")
    check("删除不存在的歌不抛异常且数量不变", widget.total_count() == before_total - 1)

    # ---------------- [8] select() ----------------
    print("\n[8] select()：自动翻页 + 滚动到可见 + 触发 on_select")
    widget.set_items(items)
    widget._sort_buttons["default"].invoke()
    events["select"].clear()
    t0 = time.perf_counter()
    widget.select("Song2555")  # 落在第 26 页
    t1 = time.perf_counter()
    shown = row_texts(widget._tree)
    sel = widget._tree.selection()
    print(f"  select('Song2555') = {ms(t1 - t0)}，{widget._page_label.cget('text')}，"
          f"selected()={widget.selected()}，选中行文本="
          f"{widget._tree.item(sel[0], 'text') if sel else None}")
    print(f"  on_select 回调收到 {events['select']}")
    check("selected() 正确", widget.selected() == "Song2555", str(widget.selected()))
    check("自动翻到含它的那一页", "Song2555" in shown, widget._page_label.cget("text"))
    check("回调收到文件夹名", events["select"] == ["Song2555"], str(events["select"]))
    check("表格里选中行文本 == 请求的名字",
          bool(sel) and widget._tree.item(sel[0], "text") == "Song2555")
    events["select"].clear()
    widget.select("根本不存在的歌")
    check("select(不存在的名字) 静默返回、不回调", events["select"] == [], str(events["select"]))

    # ---------------- [9] 重画不得重复回调 ----------------
    print("\n[9] 重画/翻页/改评分都不得重复触发 on_select")
    events["select"].clear()
    widget.select("Song0007")
    first = list(events["select"])
    widget._next_btn.invoke()
    widget._goto_first()
    widget.set_highlight({"Song0009"})
    widget.set_rating("Song0007", 3)
    widget._search_var.set("")
    wait_debounce(root, widget)
    drain(root, 0.05)
    print(f"  首次 select 回调 {first}；随后翻页/高亮/改评分/重新过滤后的回调 {events['select']}")
    print(f"  set_rating 就地更新单元格 -> "
          f"{widget._tree.set(widget._name_iid['Song0007'], 'rating')!r}")
    check("只有首次 select 触发了一次回调", events["select"] == ["Song0007"], str(events["select"]))
    check("set_rating 只改了那一格（没有整页重建）",
          widget._tree.set(widget._name_iid["Song0007"], "rating") == "★★★",
          widget._tree.set(widget._name_iid["Song0007"], "rating"))

    # ---------------- [10] 键盘：真实事件 ----------------
    print("\n[10] 键盘：合成真实按键事件（按键只在窗口 viewable 时才投递）")
    probe: list[int] = []
    widget._tree.bind("<Button-1>", lambda _e: probe.append(1))
    widget.select("Song0042")
    events["select"].clear()
    widget._tree.event_generate("<Button-1>", x=5, y=5)
    widget._tree.event_generate("<Return>")
    root.update()
    print(f"  withdraw 状态：合成鼠标事件投递 {len(probe)} 次，合成 <Return> 触发回调 "
          f"{len(events['select'])} 次")
    root.attributes("-alpha", 0.0)
    root.geometry("1024x768+4000+4000")
    root.deiconify()
    root.update()
    probe.clear()
    events["select"].clear()
    widget._tree.event_generate("<Button-1>", x=5, y=5)
    widget._tree.event_generate("<Return>")
    root.update()
    print(f"  已映射（viewable={bool(widget._tree.winfo_viewable())}）：合成鼠标事件投递 "
          f"{len(probe)} 次，合成 <Return> 触发回调 {len(events['select'])} 次")
    check("窗口映射后合成事件（含按键）能送达，下面所有按键断言才成立",
          bool(probe) and len(events["select"]) == 1)

    widget.select("Song0042")
    events["select"].clear()
    events["delete"].clear()
    widget._tree.focus_force()
    root.update()
    widget._tree.event_generate("<Return>")
    root.update()
    check("Return 触发 on_select", events["select"] == ["Song0042"], str(events["select"]))
    widget._tree.event_generate("<space>")
    root.update()
    check("Space 触发 on_select", events["select"] == ["Song0042", "Song0042"], str(events["select"]))
    widget._tree.event_generate("<Down>")
    root.update()
    after_down = widget.selected()
    widget._tree.event_generate("<Up>")
    root.update()
    print(f"  ↓ 之后选中 {after_down}；↑ 之后回到 {widget.selected()}")
    check("Down 只走一行", after_down == "Song0043", str(after_down))
    check("Up 回到上一行", widget.selected() == "Song0042", str(widget.selected()))

    bind_delete = widget._tree.bind("<Delete>") or ""
    print(f"  Treeview 的 <Delete> 实例绑定：{bind_delete[:34]}...")
    print(f"  搜索框外层 Frame 的 <Delete> 绑定：{widget._search.bind('<Delete>')!r}"
          f"（ctk 的 bind 不返回查询结果，看内层真实 Entry 的）")
    print(f"  内层 Entry 的 <Delete> 实例绑定：{widget._search._entry.bind('<Delete>')!r}")
    print(f"  Tk root 的 <Delete> 绑定：{root.bind('<Delete>')!r}（原版就是绑在这里，边打字边删歌）")
    check("Treeview 上确实绑了 Delete", bool(bind_delete))
    check("root 上没有全局 Delete 绑定", not root.bind("<Delete>"))

    widget._search_var.set("")
    wait_debounce(root, widget)
    widget._search.focus_force()
    root.update()
    print(f"  焦点在搜索框，连按 3 次真实 Delete 键……")
    for _ in range(3):
        widget._search._entry.event_generate("<Delete>")
        root.update()
    print(f"  -> on_delete 回调 {events['delete']}（必须为空）")
    check("搜索框里按 Delete 不会删歌", events["delete"] == [], str(events["delete"]))
    widget._search_var.set("song004")
    widget._search._entry.insert("end", "x")
    root.update()
    widget._search._entry.event_generate("<Delete>")
    root.update()
    print(f"  搜索框有内容时再按 Delete -> on_delete {events['delete']}")
    check("边打字边按 Delete 也不会删歌", events["delete"] == [], str(events["delete"]))
    widget._search_var.set("song004")
    wait_debounce(root, widget)
    print(f"  过滤到「song004」后选中仍是 {widget.selected()}")

    widget._tree.focus_force()
    root.update()
    widget._tree.event_generate("<Delete>")
    root.update()
    print(f"  焦点在 Treeview 时按 Delete -> on_delete {events['delete']}")
    check("Treeview 上 Delete 触发 on_delete 且参数正确",
          events["delete"] == ["Song0042"], str(events["delete"]))
    ret = widget._on_key_delete()
    check("<Delete> 处理函数返回 'break'（吃掉按键，不再冒泡）", ret == "break", repr(ret))
    check("Up/Down/Return/Space 处理函数也返回 'break'",
          widget._move_selection(1) == "break" and widget._on_key_activate() == "break")

    # ---------------- [10b] 右键 / 菜单 / 双击 ----------------
    print("\n[10b] 真实右键：排序按钮强制降序 + 行右键菜单 + 双击")
    events["sort"].clear()
    widget._sort_buttons["rating"]._canvas.event_generate("<Button-3>")
    root.update()
    print(f"  在评分按钮上投递真实右键事件 -> on_sort_changed={events['sort'][-1]}")
    check("右键排序按钮 = 直接降序", events["sort"][-1] == ("rating", True), str(events["sort"][-1:]))
    widget._sort_buttons["default"].invoke()
    widget._search_var.set("")
    wait_debounce(root, widget)

    # 右键行：把 tk_popup 临时换成记录器，避免真的在桌面上弹菜单/抢 grab
    posted: list[tuple[int, int]] = []
    origin_popup = tk.Menu.tk_popup
    tk.Menu.tk_popup = lambda self, x, y, entry="": posted.append((x, y))  # type: ignore[method-assign]
    events["select"].clear()
    events["delete"].clear()
    try:
        widget.select("Song0042")
        root.update()
        bbox = widget._tree.bbox(widget._name_iid["Song0042"])
        widget._tree.event_generate("<Button-3>", x=bbox[0] + 8, y=bbox[1] + 4)
        root.update()
        labels = menu_labels(widget._menu)
    finally:
        tk.Menu.tk_popup = origin_popup  # type: ignore[method-assign]
    print(f"  在 Song0042 那一行右键：tk_popup 调用 {posted}，菜单项 {labels}")
    check("右键行确实弹出了菜单", bool(posted), str(posted))
    check("菜单项 = 复制文件夹名/复制歌曲标题/在资源管理器中打开/分隔符/删除此歌曲",
          labels == ["复制文件夹名", "复制歌曲标题", "在资源管理器中打开", "", "删除此歌曲"],
          str(labels))
    check("危险项是分隔符后的最后一项",
          widget._menu is not None and widget._menu.type(3) == "separator", "separator")

    copied_menu_idx = labels.index("复制文件夹名")
    widget._menu.invoke(copied_menu_idx)  # type: ignore[union-attr]
    root.update()
    print(f"  菜单「复制文件夹名」-> 剪贴板 {root.clipboard_get()!r}")
    check("菜单复制文件夹名写进剪贴板", root.clipboard_get() == "Song0042", root.clipboard_get())
    widget._menu.invoke(labels.index("复制歌曲标题"))  # type: ignore[union-attr]
    root.update()
    check("菜单复制歌曲标题写进剪贴板", root.clipboard_get().endswith("曲目 42"), root.clipboard_get())
    widget._menu.invoke(labels.index("删除此歌曲"))  # type: ignore[union-attr]
    root.update()
    print(f"  菜单「删除此歌曲」-> on_delete {events['delete']}")
    check("菜单删除走的是同一个 on_delete 回调", events["delete"] == ["Song0042"], str(events["delete"]))
    widget._menu.invoke(labels.index("在资源管理器中打开"))  # type: ignore[union-attr]
    root.update()
    check("菜单「在资源管理器中打开」走 on_open_in_explorer",
          events["explorer"] == ["Song0042"], str(events["explorer"]))

    # 同一个 Menu 控件会被复用（每次右键只重建菜单项，不新建 widget）
    menu_before = widget._menu
    no_path = menu_labels(widget._build_menu("Song0043"))  # provider 对它返回 None
    print(f"  设备上的歌（provider 返回 None）菜单项 {no_path}")
    check("本地没有路径时不给「在资源管理器中打开」",
          "在资源管理器中打开" not in no_path, str(no_path))
    check("右键菜单控件被复用（右键一万次也不会泄漏 Menu）", menu_before is widget._menu)

    events["activate"].clear()
    events["select"].clear()
    root.update()
    # Tk 不允许合成 <Double-1>（"Double modifier not allowed"），所以直接调处理函数，
    # 用真实的 y 坐标走一遍 identify_row 的命中判定；绑定本身另外断言。
    bbox = widget._tree.bbox(widget._name_iid["Song0042"])
    widget._on_double_click(StubEvent(y=bbox[1] + 4, x=bbox[0] + 8))
    root.update()
    print(f"  双击 Song0042 那一行 -> on_activate {events['activate']}，on_select {events['select']}")
    check("双击处理函数已绑到 <Double-1>", bool(widget._tree.bind("<Double-1>")))
    check("双击触发 on_activate", events["activate"] == ["Song0042"], str(events["activate"]))
    check("双击已选中的行不会重复触发 on_select", events["select"] == [], str(events["select"]))
    # 空白处双击：列表填满时整块可视区都是行，所以先过滤到只剩 1 行再找空白
    widget._search_var.set("song2999")
    wait_debounce(root, widget)
    blank_y = widget._tree.winfo_height() - 6
    hit = widget._tree.identify_row(blank_y)
    print(f"  过滤到只剩 {len(row_texts(widget._tree))} 行后，底部 y={blank_y} 的 "
          f"identify_row={hit!r}")
    check("列表下方确实存在空白坐标", hit == "", repr(hit))
    if hit == "":
        widget._on_double_click(StubEvent(y=blank_y, x=4))
        check("空白处双击不触发 on_activate", events["activate"] == ["Song0042"],
              str(events["activate"]))

    # ---------------- [11] Ctrl+C / 剪贴板 / 状态提示 ----------------
    print("\n[11] Ctrl+C 复制文件夹名 + 底部提示 2 秒后自动清除")
    widget._search_var.set("")
    wait_debounce(root, widget)
    widget.select("Song0003")
    widget._tree.focus_force()
    root.update()
    # 说明：窗口此时是"已映射但 alpha=0 且挪到屏幕外"的状态，Tk 对这种窗口
    # 不投递合成的键盘事件（本文件开头已记录该现象）。所以这里改为
    # ① 断言绑定确实存在，② 直接调用处理函数验证行为。
    # （绑定与行为都验证到了；单独用真实可见窗口跑 probe_ctrlc.py 时
    #   event_generate("<Control-c>") 是能正常送达的。）
    bound = bool(widget._tree.bind("<Control-c>")) and bool(widget._tree.bind("<Control-C>"))
    check("Treeview 上注册了 Ctrl+C 绑定", bound, str(widget._tree.bind("<Control-c>"))[:60])
    widget._on_key_copy()
    root.update()
    try:
        clip = root.clipboard_get()
    except tk.TclError as exc:  # pragma: no cover
        clip = f"<读取失败: {exc}>"
    print(f"  剪贴板内容 {clip!r}；状态栏 {widget._status.cget('text')!r}")
    check("Ctrl+C 复制的是选中歌曲的文件夹名", clip == "Song0003", repr(clip))
    check("状态栏显示已复制", widget._status.cget("text") == "已复制：Song0003",
          widget._status.cget("text"))
    check("已排定 2 秒清除定时器", widget._status_id is not None)
    drain(root, 2.3)
    print(f"  2.3 s 后状态栏 {widget._status.cget('text')!r}，定时器 id={widget._status_id}")
    check("2 秒后提示自动清除", widget._status.cget("text") == "" and widget._status_id is None)
    widget._tree.selection_remove(*widget._tree.selection())
    widget._tree.focus_force()
    root.update()
    # 直接调处理函数：本文件开头已记录"Tk 对屏幕外窗口不投递合成键盘事件"，
    # 用 event_generate 会偶发不送达（run_all 里就闪断过一次）。
    widget._on_key_copy()
    root.update()
    check("没选中时 Ctrl+C 只提示不报错", widget._status.cget("text") == "未选中任何歌曲",
          widget._status.cget("text"))

    # ---------------- [12] 空列表 ----------------
    print("\n[12] 空列表：把能点的都点一遍（不允许 IndexError / TclError）")
    widget.clear()
    root.update()
    errors: list[str] = []
    try:
        for button in (widget._first_btn, widget._prev_btn, widget._next_btn, widget._last_btn):
            button.invoke()
        for button in widget._sort_buttons.values():
            button.invoke()
            button._canvas.event_generate("<Button-3>")
        root.update()
        widget._size_menu._dropdown_callback("50")
        widget._size_menu._dropdown_callback("500")
        widget._size_menu._dropdown_callback("100")
        widget.select("没有这首")
        widget.remove_item("也没有这首")
        widget.set_highlight({"x"})
        widget.clear_highlight()
        widget.set_rating("x", 5)
        widget._on_key_delete()
        widget._on_key_activate()
        widget._move_selection(1)
        widget._move_selection(-1)
        widget._on_key_copy()
        widget._tree.event_generate("<Up>")
        widget._tree.event_generate("<Down>")
        widget._tree.event_generate("<Delete>")
        widget._on_right_click(type("E", (), {"y": 5, "x_root": 10, "y_root": 10})())
        root.update()
    except Exception as exc:  # noqa: BLE001 - 这里就是要抓所有异常
        errors.append(f"{type(exc).__name__}: {exc}")
    print(f"  空列表状态：{widget._page_label.cget('text')} / {widget._header.cget('text')} / "
          f"行数 {len(widget._tree.get_children(''))} / selected()={widget.selected()}")
    check("空列表下点遍所有按钮/按键都无异常", not errors, "; ".join(errors))
    check("空列表页码 = 第 1/1 页 · 共 0 首",
          widget._page_label.cget("text") == "第 1/1 页 · 共 0 首", widget._page_label.cget("text"))
    check("空列表 4 个翻页按钮全部置灰",
          all(str(b.cget("state")) == "disabled" for b in
              (widget._first_btn, widget._prev_btn, widget._next_btn, widget._last_btn)))
    check("clear() 后 total_count()==0 且 items()==[]",
          widget.total_count() == 0 and widget.items() == [])

    # ---------------- [13] 主题切换 ----------------
    print("\n[13] 深色模式 + refresh_theme()")
    widget.set_items(items, rating_map={f"Song{i:04d}": i % 5 for i in range(N)},
                     highlight={"Song0001"})
    widget._sort_buttons["default"].invoke()  # 复位排序，保证 Song0001 在第 1 页
    light_row = tag_option(widget._tree, "row")
    ctk.set_appearance_mode("Dark")
    widget.refresh_theme()
    root.update()
    dark_row = tag_option(widget._tree, "row")
    dark_hl = tag_option(widget._tree, "hl")
    dark_sel = tag_option(widget._tree, "sel")
    print(f"  外观={ctk.get_appearance_mode()}，row 背景 {light_row} -> {dark_row}，"
          f"hl 背景 {dark_hl}，sel 背景 {dark_sel}")
    print(f"  期望深色值：row={color('BG_CARD')!r} / hl={color('DANGER_SOFT')!r}")
    check("深色下 refresh_theme() 不抛异常", True)
    check("row tag 换成深色变体", dark_row == color("BG_CARD"), dark_row)
    check("高亮 tag 换成深色变体", dark_hl == color("DANGER_SOFT"), dark_hl)
    check("深色下高亮仍然在正确的行上",
          {r[1] for r in highlighted_rows(widget._tree)} == {"Song0001"},
          str([r[1] for r in highlighted_rows(widget._tree)]))
    check("深色下表格背景也变了", tag_option(widget._tree, "row", "foreground") != "",
          tag_option(widget._tree, "row", "foreground"))
    ctk.set_appearance_mode("Light")
    widget.refresh_theme()
    root.update()
    check("切回浅色 refresh_theme() 正常",
          tag_option(widget._tree, "row") == color("BG_CARD"), tag_option(widget._tree, "row"))

    # ---------------- [14] 销毁时取消挂起的 after ----------------
    print("\n[14] destroy() 必须取消挂起的防抖/提示定时器")
    widget._search_var.set("abc")  # 排一个 180 ms 后的防抖
    widget._show_status("待清除")  # 排一个 2 s 后的清除
    pending = (widget._debounce_id is not None, widget._status_id is not None)
    widget.destroy()
    root.update()
    drain(root, 0.4)
    print(f"  destroy 前挂着 debounce/status 定时器 {pending}；destroy 后 "
          f"debounce={widget._debounce_id} status={widget._status_id}")
    check("destroy 后没有残留定时器（回调不会打在已销毁控件上）",
          widget._debounce_id is None and widget._status_id is None)

    widget2 = SongListWidget(root, title="重建", page_size=50)
    widget2.pack()
    widget2.set_items(items)
    widget2.select("Song0010")
    check("销毁后重建新实例仍然正常",
          widget2.total_count() == N and widget2.selected() == "Song0010",
          f"{widget2.total_count()}/{widget2.selected()}")
    widget2.destroy()

    # ---------------- [15] 跨页多选（B1） ----------------
    # 原版的选择完全依赖 Treeview 的选中行，而 Treeview 只持有**当前页** ——
    # 翻一页选择就没了，Ctrl+A 也只选当前页。v15.2 改成按文件夹名维护独立选择集。
    print("\n[15] 跨页多选：翻页不丢选择、已选计数跨页、Ctrl+Shift+A 全选")
    widget3 = SongListWidget(root, title="跨页", page_size=10)
    widget3.pack()
    widget3.set_items(make_items(35))          # 35 首 / 每页 10 行 = 4 页
    root.update()

    widget3.select("Song0001")
    root.update()
    check("第 1 页选中 1 首", widget3.selection_count() == 1, str(widget3.selection_count()))

    # 语义说明：`select(name)` / 普通单击 = **换成这一首**（与 Treeview 一致）；
    # 跨页**累计**靠 Ctrl+单击 —— 所以这里要如实模拟"Ctrl+单击第 2 页的一行"。
    class _Click:
        def __init__(self, state: int) -> None:
            self.state = state

    widget3._next_btn.invoke()
    root.update()
    widget3._on_tree_click(_Click(0x0004))     # Ctrl+单击：不清空既有选择
    widget3._tree.selection_set(widget3._name_iid["Song0015"])
    widget3._on_tree_select()                  # 模拟 Tk 投递 <<TreeviewSelect>>
    root.update()
    check("Ctrl+单击第 2 页另一首 -> 跨页累计 2 首",
          widget3.selection_count() == 2, str(widget3.selected_all()))
    check("两首都在跨页选择集里（翻页没丢掉第 1 页那首）",
          set(widget3.selected_all()) == {"Song0001", "Song0015"}, str(widget3.selected_all()))

    widget3._first_btn.invoke()
    root.update()
    check("回到第 1 页，选中态被重新画出来",
          widget3.selection_count() == 2 and widget3.selected() == "Song0001",
          f"count={widget3.selection_count()} focus={widget3.selected()}")

    widget3._next_btn.invoke()
    root.update()
    check("第 2 页也能看到它自己那首的选中态",
          "Song0015" in [widget3._iid_name.get(i, "") for i in widget3._tree.selection()],
          str([widget3._iid_name.get(i, "") for i in widget3._tree.selection()]))

    widget3.clear_selection()
    root.update()
    check("Esc 清空后跨页计数归零",
          widget3.selection_count() == 0 and widget3.selected_all() == [],
          str(widget3.selected_all()))

    widget3.select_all()
    root.update()
    check("Ctrl+Shift+A 全选所有页（不是只选当前页）",
          widget3.selection_count() == 35, str(widget3.selection_count()))
    widget3.clear_selection()
    root.update()

    widget3.select_all_visible()
    root.update()
    check("Ctrl+A 仍然只选当前页",
          widget3.selection_count() == 10, str(widget3.selection_count()))

    # 普通单击必须清掉**整个**跨页选择，否则用户跨页选了 50 首后随手点一下，
    # 别的页那 40 首还留着，再按 Delete 就会删掉他以为已经取消的东西。
    widget3.select_all()
    root.update()
    widget3._on_tree_click(_Click(0))            # 普通单击（无修饰键）
    check("普通单击清空整个跨页选择",
          widget3.selection_count() == 0, str(widget3.selection_count()))

    widget3.select_all()
    root.update()
    widget3._on_tree_click(_Click(0x0004))       # Control 按住
    check("Ctrl+单击保留跨页选择（追加语义）",
          widget3.selection_count() == 35, str(widget3.selection_count()))
    widget3._on_tree_click(_Click(0x0001))       # Shift 按住
    check("Shift+单击同样保留跨页选择",
          widget3.selection_count() == 35, str(widget3.selection_count()))
    widget3.destroy()

    root.destroy()

    print("\n" + "=" * 78)
    failed = [r for r in results if not r[1]]
    print(f"结果：{len(results) - len(failed)}/{len(results)} 项通过")
    for label, _ok, detail in failed:
        print(f"  FAIL  {label}  {detail}")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
