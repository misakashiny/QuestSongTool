"""高 DPI / 界面缩放检查（ROADMAP C1）。

验收标准是"三种缩放下控件不重叠、字体不糊"。这里把它变成**可执行**的判据：

1. **内容装得下**：`winfo_reqwidth/reqheight <= winfo_width/height` ——
   装不下的直接表现就是控件被裁掉/挤到重叠；
2. **最小尺寸跟着缩放走**：150% 时最小窗口必须比 100% 时大，
   否则用户把窗口缩到最小就必然重叠；
3. **关键控件不重叠**：对一组"结构性控件"（路径框、各个按钮、三个列表容器、
   分割器的两个窗格）做两两矩形相交检测。
4. **关键控件都真的画出来了**：`winfo_ismapped()` 且尺寸为正。

为什么不检查**所有**兄弟控件的重叠：封面墙是**故意**和列表叠在同一个格子里的
（按视图模式切换显示，见 `_build_ui` 的注释），全量检查会把这种有意设计判成错。

"字体不糊"没法自动断言（截图回归那边也只能验像素一致），
所以这里只保证版式；字体随缩放由 customtkinter 统一处理。
"""
from __future__ import annotations

import shutil
import sys
import time

from _paths import ROOT, sandbox  # noqa: E402

sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []
SCALES = ("1.0", "1.25", "1.5")
#: 三个歌曲列表在基准窗口下至少要让用户看到这么多行。
#: 取 8 是留了余量：正常版式在 100% 缩放下是 20+ 行，被压扁时只剩 1 行左右，
#: 8 这个阈值两头都分得很开。
MIN_VISIBLE_ROWS = 8


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  -> ' + detail) if detail else ''}")


def pump(root, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def rect(widget) -> tuple[int, int, int, int]:
    return (widget.winfo_x(), widget.winfo_y(), widget.winfo_width(), widget.winfo_height())


def overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """两个矩形是否**实质性**相交（允许 1px 的取整误差）。"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    slack = 1
    return not (
        ax + aw - slack <= bx or bx + bw - slack <= ax
        or ay + ah - slack <= by or by + bh - slack <= ay
    )


def check_scale(app, scale: str) -> None:
    from quest_tool.app import MIN_SIZE, QuestComparatorApp
    from quest_tool.storage import Settings
    from quest_tool.theme import apply_ui_scale

    print(f"\n--- 缩放 {scale} ---")
    root_dir = sandbox(f"scaling_{scale.replace('.', '_')}")
    shutil.rmtree(root_dir, ignore_errors=True)
    songs = root_dir / "Songs"
    songs.mkdir(parents=True)

    settings = Settings(local_path=str(songs), device_poll_on_start=False,
                        appearance="Light", wizard_done=True, ui_scale=scale)
    app = QuestComparatorApp(settings=settings)
    root = app.root
    root.deiconify()
    factor = float(scale)
    # 给足空间：按缩放把窗口等比放大，模拟"用户在高 DPI 下正常使用"。
    # ⚠️ 基准宽度必须**明显大于** `AUTO_COLLAPSE_WIDTH + 60`（=1620）——
    # 否则窗口一开就被自动收起详情栏，而且宽度不到 1620 时**不会自动弹回来**，
    # 于是"详情栏没画出来"这个假失败就出现了（第一版基准写了 1600，正好踩中）。
    root.geometry(f"{int(1700 * factor)}x{int(950 * factor)}+0+0")
    pump(root, 1.2)

    check(f"[{scale}] 实际生效的缩放倍率正确",
          abs(app.ui_scale - factor) < 0.01, f"{app.ui_scale} vs {factor}")

    # 2) 最小尺寸必须跟着缩放走
    expected_min = (int(MIN_SIZE[0] * factor), int(MIN_SIZE[1] * factor))
    check(f"[{scale}] 最小窗口尺寸随缩放放大",
          app._scaled_min_size() == expected_min,
          f"{app._scaled_min_size()} vs {expected_min}")

    # 1) 内容装得下（装不下就会裁切/重叠）
    pump(root, 0.6)
    req_w, req_h = root.winfo_reqwidth(), root.winfo_reqheight()
    real_w, real_h = root.winfo_width(), root.winfo_height()
    check(f"[{scale}] 内容装得下（不被裁切）",
          req_w <= real_w and req_h <= real_h,
          f"需要 {req_w}x{req_h}，实际 {real_w}x{real_h}")

    # 4) 关键控件都画出来了
    key_widgets = {
        "本地路径框": app.path_entries["local"],
        "Quest 路径框": app.path_entries["quest"],
        "本地列表": app.list_local,
        "Quest 列表": app.list_quest,
        "缺失列表": app.list_missing,
        "详情栏": app.right_card,
    }
    missing = [name for name, w in key_widgets.items()
               if not w.winfo_ismapped() or w.winfo_width() <= 1 or w.winfo_height() <= 1]
    check(f"[{scale}] 关键控件都画出来了", not missing,
          f"没画出来的：{missing}" if missing else f"{len(key_widgets)} 个")

    # 3) 关键控件两两不重叠（同一个父容器里才比；跨容器的坐标不可比）
    pairs: list[tuple[str, str]] = []
    names = list(key_widgets)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            wa, wb = key_widgets[first], key_widgets[second]
            if wa.master is not wb.master:
                continue
            if overlaps(rect(wa), rect(wb)):
                pairs.append(f"{first} × {second}")
    check(f"[{scale}] 关键控件互不重叠", not pairs, "；".join(pairs) if pairs else "无重叠")

    # 5) 列表必须真的装得下若干行歌（v17.4 新增）
    #
    # **为什么单独加这一条**：v17.4 把操作按钮改成四个分组卡片后，分组标题胶囊
    # 漏写了 height，`pack_propagate(False)` 下 CTkFrame 用默认高度 200 ——
    # 四个分组各 212px 共 432px，把三个歌曲列表从 552px 挤到 **212px**
    # （表格区只剩 28px，一行歌都显示不出来）。
    #
    # 上面四条**全都通过了**：内容"装得下"（请求高度确实没超）、控件也没重叠
    # （列表只是变矮，没有压到别人身上）。"被挤扁"和"正常"在请求高度上
    # 看不出差别 —— 因为 `ttk.Treeview` 被刻意设成 `height=1`（见
    # `ui_songlist.py` 的注释），它的请求高度恒为一行。
    #
    # 所以判据要直接量"表格实际能显示几行"，把"够不够用"变成数字。
    row_h = max(1, app.list_local._row_height())
    table_h = app.list_local._tree.winfo_height()
    visible_rows = (table_h - 4) / row_h  # 减去表头与边框
    check(f"[{scale}] 列表装得下足够多的行（>= {MIN_VISIBLE_ROWS} 行）",
          visible_rows >= MIN_VISIBLE_ROWS,
          f"表格高 {table_h}px / 行高 {row_h}px = 约 {visible_rows:.1f} 行")

    app.on_close()
    pump(root, 0.4)


def main() -> int:
    print("=" * 78)
    print(f"高 DPI / 界面缩放检查（{', '.join(SCALES)}）")
    print("=" * 78)

    for scale in SCALES:
        check_scale(None, scale)

    # 自动档：拿不到 DPI 时必须老老实实回落到 1.0，而不是抛异常
    from quest_tool.theme import apply_ui_scale, detect_ui_scale

    check("detect_ui_scale 没有 root 时回落 1.0", detect_ui_scale(None) == 1.0,
          str(detect_ui_scale(None)))
    check('apply_ui_scale("auto") 不抛异常', apply_ui_scale("auto", None) > 0)
    check("非法档位回落（不抛异常）", apply_ui_scale("nonsense", None) == 1.0,
          str(apply_ui_scale("nonsense", None)))
    # 极端值要被夹住，不能把界面放大到失控
    check("过大的倍率会被夹到上限", apply_ui_scale("99", None) <= 3.0,
          str(apply_ui_scale("99", None)))

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
