"""验证"黑块"是渲染残留还是真实控件：同一状态连拍两张对比。"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

import customtkinter as ctk  # noqa: E402

from quest_tool.app import QuestComparatorApp  # noqa: E402
from quest_tool.storage import Settings  # noqa: E402

OUT = Path(__file__).resolve().parent / "_shots_out"


def grab(app, tag: str, settle: float) -> Path:
    app.root.lift()
    app.root.attributes("-topmost", True)
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.08)
    from PIL import ImageGrab

    x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
    w, h = app.root.winfo_width(), app.root.winfo_height()
    path = OUT / f"{tag}.png"
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
    app.root.attributes("-topmost", False)
    return path


def dark_ratio(path: Path, box: tuple[int, int, int, int]) -> float:
    from PIL import Image

    image = Image.open(path).convert("RGB").crop(box)
    pixels = list(image.getdata())
    dark = sum(1 for r, g, b in pixels if (r + g + b) / 3 < 60)
    return dark / max(1, len(pixels))


def main() -> int:
    sandbox = Path(__file__).resolve().parent / "_diag10"
    shutil.rmtree(sandbox, ignore_errors=True)
    local = sandbox / "local"
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

    # 切到浅色
    app._appearance_var.set("Light")
    app._on_appearance_changed()

    first = grab(app, "cmp_light_first", 1.0)
    second = grab(app, "cmp_light_second", 1.5)
    third = grab(app, "cmp_light_third", 3.0)

    # 头部区域（三个列表标题栏）：x 24..1160, y 195..232
    box = (24, 195, 1160, 232)
    for path in (first, second, third):
        print(f"{path.name}: 头部区域暗像素占比 = {dark_ratio(path, box):.4f}")
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
