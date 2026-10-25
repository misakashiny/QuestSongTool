"""UI 截图回归（ROADMAP C7）。

把界面在一个**固定尺寸 + 固定主题 + 固定歌曲集**下截下来，与基线图做像素比对 ——
目的是"谁不小心改了配色/间距，测试立刻红"，而不是精确到每个像素。

用法::

    python tests\\screenshot_regression.py                 # 与基线比对
    set QUEST_UPDATE_BASELINE=1 && python tests\\screenshot_regression.py   # 用当前界面更新基线

判定用两个指标（`_compare`）：

* **平均色差**（全图所有通道的平均绝对差）：抓"整体变色"（改错底色/文字色）；
* **显著差异像素占比**（单通道差 > 32 的像素比例）：抓"局部变了"（少了一个控件、
  错位、边框消失）。
两个都超阈值才算失败 —— 这样字体抗锯齿的细微差别不会误报。

**已知局限（很重要）**：基线图是**机器相关**的 —— 字体渲染、系统 DPI、主题都影响像素。
换机器或改了系统缩放后需要按上面的方式重新生成基线。所以它守的是
"**同一台机器上**的 UI 回归"，不是跨机器的绝对一致性。
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT, sandbox  # noqa: E402

sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  -> ' + detail) if detail else ''}")


# ---------------- 阈值 ----------------
#: 全图平均色差超过它就判失败（改错底色会远超这个数）
MAX_MEAN_DIFF = 6.0
#: 显著差异像素占比上限
MAX_CHANGED_RATIO = 0.02
#: 单通道差超过它算"显著"
SIGNIFICANT = 32

#: 窗口固定尺寸：跟内容强相关，改版式时基线要一起更新。
#: 取 1600 宽是为了**同时看到三列列表 + 详情栏**（窄于此会自动收起详情栏，
#: 那样基线只覆盖到部分界面，改坏详情栏就测不出来了）。
SHOT_SIZE = (1600, 860)
BASELINE_DIR = Path(__file__).resolve().parent / "baseline"


def make_song(root: Path, name: str, title: str, bpm: int) -> None:
    import json

    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "info.dat").write_text(
        json.dumps({"_version": "2.0.0", "_songName": title, "_songAuthorName": "作者",
                    "_beatsPerMinute": bpm, "_difficultyBeatmapSets": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def pump(root, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def _is_degenerate(path: Path) -> bool:
    """截图是不是"几乎单色"（窗口没置顶/被遮住/还没画出来）。

    屏幕截图有个绕不开的问题：抓到的是**屏幕上那一块**，不是"窗口自己"。
    窗口没在最前面时，抓到的可能是别的窗口。这里用"颜色种类太少"做判据 ——
    真界面必然有文字、边框、分栏，颜色种类远不止几种。
    """
    from PIL import Image

    with Image.open(path) as img:
        small = img.convert("RGB").resize((80, 44))
    return len(set(small.getdata())) < 8


def capture(root, path: Path) -> tuple[int, int]:
    """把窗口所在的屏幕区域截下来存成 PNG，返回 (宽, 高)。

    截图前反复"置顶 + 抢焦点"，并**校验抓到的画面不是几乎单色** ——
    否则会拿一张被遮挡的图去比对，报出一个毫无意义的巨大差异
    （开发时就遇到过：抓成 4 KB 的纯色图，平均色差 243）。
    """
    from PIL import ImageGrab

    last_size = (0, 0)
    for attempt in range(3):
        try:
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.focus_force()
        except Exception:  # pragma: no cover - 某些窗口管理器不支持
            pass
        pump(root, 0.5 if attempt == 0 else 0.8)
        root.update_idletasks()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        width, height = root.winfo_width(), root.winfo_height()
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            from window_capture import capture_client
            try:
                capture_client(root, path)
            except (OSError, RuntimeError):
                ImageGrab.grab(bbox=(x, y, x + width, y + height)).save(path)
        else:
            ImageGrab.grab(bbox=(x, y, x + width, y + height)).save(path)
        last_size = (width, height)
        if not _is_degenerate(path):
            return last_size
        print(f"  第 {attempt + 1} 次截图疑似被遮挡/未画出来，重试…")
    raise RuntimeError("连续 3 次截到的画面都几乎单色，窗口可能被其他窗口盖住了")


def compare(current: Path, baseline: Path) -> tuple[bool, str]:
    """返回 (是否通过, 说明)。两个指标都超阈值才算失败。"""
    from PIL import Image, ImageChops

    with Image.open(current) as cur_img, Image.open(baseline) as base_img:
        cur = cur_img.convert("RGB")
        base = base_img.convert("RGB")
    if cur.size != base.size:
        return False, f"尺寸不一致：{cur.size} vs {base.size}（改版式后要更新基线）"

    diff = ImageChops.difference(cur, base)
    pixels = list(diff.getdata())
    total = len(pixels) * 3
    mean_diff = sum(sum(p) for p in pixels) / max(1, total)
    changed = sum(1 for p in pixels if max(p) > SIGNIFICANT) / max(1, len(pixels))

    ok = mean_diff <= MAX_MEAN_DIFF and changed <= MAX_CHANGED_RATIO
    detail = (f"平均色差 {mean_diff:.2f}（上限 {MAX_MEAN_DIFF}）、"
              f"显著差异像素 {changed:.2%}（上限 {MAX_CHANGED_RATIO:.0%}）")
    return ok, detail


def main() -> int:
    import customtkinter as ctk

    print("=" * 78)
    print("UI 截图回归（固定尺寸 + 固定主题 + 固定歌曲集）")
    print("=" * 78)

    from quest_tool.app import QuestComparatorApp
    from quest_tool.storage import Settings

    root_dir = sandbox("shots")
    shutil.rmtree(root_dir, ignore_errors=True)
    local = root_dir / "LocalSongs"
    quest = root_dir / "QuestSongs"
    local.mkdir(parents=True)
    quest.mkdir(parents=True)
    for i, name in enumerate(["Alpha", "Bravo", "Charlie", "Delta"]):
        make_song(local, name, f"曲目 {i}", 120 + i * 10)
    for i, name in enumerate(["Alpha", "Charlie", "Echo"]):
        make_song(quest, name, f"设备 {i}", 100 + i * 10)

    ctk.set_appearance_mode("Light")  # 固定主题，否则基线随系统主题漂
    settings = Settings(local_path=str(local), quest_path=str(quest),
                        device_poll_on_start=False, appearance="Light", wizard_done=True)
    app = QuestComparatorApp(settings=settings)
    root = app.root
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    pump(root, 0.6)
    # 尺寸必须在**主程序初始化完成之后**再设：`_initial_geometry()` 在 __init__ 里
    # 已经按屏幕尺寸摆过一次窗口，先设会被它覆盖掉。
    root.geometry(f"{SHOT_SIZE[0]}x{SHOT_SIZE[1]}+0+0")
    pump(root, 0.6)

    app.load_library("local")
    pump(root, 2.5)
    app.load_library("quest")
    pump(root, 2.5)
    app.compare("quest")
    pump(root, 1.5)
    # 确保详情栏是展开的（窗口够宽时不会自动收起，这里再兜一次底）
    if not app._details_visible:
        app._show_details()
        app._details_visible = True
    pump(root, 0.8)   # 让异步封面/评分都落定

    current = BASELINE_DIR / "main_window.png"
    baseline = BASELINE_DIR / "main_window.baseline.png"
    try:
        size = capture(root, current)
        check(f"截图成功（{size[0]}x{size[1]}）", size == SHOT_SIZE, f"实际 {size}")
    except Exception as exc:  # 无图形环境/被其他窗口遮挡
        check("截图成功", False, f"{type(exc).__name__}: {exc}")
        app.on_close()
        return _summary()

    if os.environ.get("QUEST_UPDATE_BASELINE") == "1" or not baseline.exists():
        shutil.copyfile(current, baseline)
        reason = ("QUEST_UPDATE_BASELINE=1 指定更新" if os.environ.get("QUEST_UPDATE_BASELINE") == "1"
                  else "基线不存在，已生成")
        print(f"  基线：{baseline.relative_to(ROOT)}（{reason}）")
        check("基线已就绪", True, reason)
    else:
        ok, detail = compare(current, baseline)
        check("与基线一致（改错配色/改坏版式会失败）", ok, detail)
        print(f"  当前图：{current.relative_to(ROOT)}")

    # 自检：把"故意改错颜色"模拟出来，确认比对**真的**抓得住
    from PIL import Image

    tampered = BASELINE_DIR / "main_window.tampered.png"
    with Image.open(baseline) as base_img:
        spoiled = base_img.convert("RGB").copy()
    spoiled.paste((255, 0, 0), (0, 0, spoiled.width, spoiled.height // 3))
    spoiled.save(tampered)
    tamper_ok, tamper_detail = compare(tampered, baseline)
    check("自检：故意涂红 1/3 画面会被判为不一致", not tamper_ok, tamper_detail)
    tampered.unlink(missing_ok=True)

    app.on_close()
    pump(root, 0.5)
    return _summary()


def _summary() -> int:
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
