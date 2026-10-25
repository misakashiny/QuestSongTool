"""精确定位截图中的深色矩形，并与控件坐标对账。"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT, sandbox  # noqa: E402
sys.path.insert(0, str(ROOT))

import customtkinter as ctk  # noqa: E402

from quest_tool.app import QuestComparatorApp  # noqa: E402
from quest_tool.storage import Settings  # noqa: E402

OUT = sandbox("diag_boxes")


def find_dark_boxes(path: Path, region: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    """在给定区域内找出连通的深色矩形（粗粒度：按行/列扫描）。"""
    from PIL import Image

    image = Image.open(path).convert("RGB")
    x0, y0, x1, y1 = region
    boxes: list[tuple[int, int, int, int]] = []
    visited: set[tuple[int, int]] = set()

    def is_dark(x: int, y: int) -> bool:
        r, g, b = image.getpixel((x, y))
        return (r + g + b) / 3 < 60

    for y in range(y0, y1):
        for x in range(x0, x1):
            if (x, y) in visited or not is_dark(x, y):
                continue
            # 洪水填充（栈实现，避免递归）
            stack = [(x, y)]
            min_x = max_x = x
            min_y = max_y = y
            count = 0
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in visited or not (x0 <= cx < x1 and y0 <= cy < y1):
                    continue
                if not is_dark(cx, cy):
                    continue
                visited.add((cx, cy))
                count += 1
                min_x, max_x = min(min_x, cx), max(max_x, cx)
                min_y, max_y = min(min_y, cy), max(max_y, cy)
                stack.extend(((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)))
            if count > 120:  # 过滤细碎像素
                boxes.append((min_x, min_y, max_x, max_y))
    return boxes


def main() -> int:
    work = sandbox("diag_boxes_work")
    shutil.rmtree(sandbox, ignore_errors=True)
    local = work / "local"
    local.mkdir(parents=True)
    for index in range(20):
        folder = local / f"Song {index:02d}"
        folder.mkdir()
        (folder / "info.dat").write_text(
            json.dumps({"_songName": f"Song {index}", "_songAuthorName": "A", "_beatsPerMinute": 120,
                        "_difficultyBeatmapSets": [{"_difficultyBeatmaps": [{"_difficulty": "Expert"}]}]},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    ctk.set_appearance_mode("Dark")
    app = QuestComparatorApp(
        settings=Settings(local_path=str(local), device_poll_on_start=False, appearance="Dark")
    )
    app.root.geometry("1620x1000+20+20")
    app.root.update()
    app.load_library("local")
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and app.list_local.total_count() < 20:
        app.root.update()
        time.sleep(0.05)

    app._appearance_var.set("Light")
    app._on_appearance_changed()
    for _ in range(20):
        app.root.update()
        time.sleep(0.08)

    app.root.lift()
    app.root.attributes("-topmost", True)
    for _ in range(10):
        app.root.update()
        time.sleep(0.1)
    from PIL import ImageGrab

    x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
    path = OUT / "boxes_light.png"
    ImageGrab.grab(bbox=(x, y, x + app.root.winfo_width(), y + app.root.winfo_height())).save(path)
    app.root.attributes("-topmost", False)

    print("深色矩形（窗口坐标）:")
    for box in find_dark_boxes(path, (0, 120, 1160, 260)):
        print("   ", box)
        left, top, right, bottom = box
        widget = app.root.winfo_containing(x + (left + right) // 2, y + (top + bottom) // 2)
        if widget is not None:
            chain = []
            node = widget
            while node is not None and len(chain) < 6:
                chain.append(node.__class__.__name__)
                node = getattr(node, "master", None)
            text = ""
            try:
                text = str(widget.cget("text"))[:16]
            except Exception:
                pass
            print(f"        命中控件={widget.__class__.__name__} text={text!r} 祖先链={' < '.join(chain)}")
        else:
            print("        命中控件=<无>")
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
