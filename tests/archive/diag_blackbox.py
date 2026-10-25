"""在截图前用 winfo_containing() 反查"黑块"位置的控件，并打印其颜色配置。"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))


def main() -> int:
    sandbox = Path(__file__).resolve().parent / "_diag8"
    shutil.rmtree(sandbox, ignore_errors=True)
    local = sandbox / "local"
    local.mkdir(parents=True)
    for index in range(6):
        folder = local / f"Song {index}"
        folder.mkdir()
        (folder / "info.dat").write_text(
            json.dumps({"_songName": f"Song {index}", "_songAuthorName": "A", "_beatsPerMinute": 120,
                        "_difficultyBeatmapSets": [{"_difficultyBeatmaps": [{"_difficulty": "Expert"}]}]},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    import customtkinter as ctk

    from quest_tool.app import QuestComparatorApp
    from quest_tool.storage import Settings

    ctk.set_appearance_mode("Dark")
    app = QuestComparatorApp(
        settings=Settings(local_path=str(local), device_poll_on_start=False, appearance="Dark")
    )
    app.root.geometry("1620x1000+20+20")
    app.root.update()
    app.load_library("local")
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and app.list_local.total_count() < 6:
        app.root.update()
        time.sleep(0.05)

    # 模拟 screenshot.py：切成浅色
    ctk.set_appearance_mode("Light")
    for widget in (app.list_local, app.list_quest, app.list_missing):
        widget.refresh_theme()
    for _ in range(10):
        app.root.update_idletasks()
        app.root.update()
        time.sleep(0.05)

    root_x, root_y = app.root.winfo_rootx(), app.root.winfo_rooty()
    print("反查屏幕像素对应的控件（窗口坐标）：")
    for y in (132, 136, 140, 144, 202, 210, 278, 282):
        for x in (30, 45, 60, 300, 420, 830):
            widget = app.root.winfo_containing(root_x + x, root_y + y)
            if widget is None:
                continue
            name = widget.__class__.__name__
            detail = ""
            for attr in ("cget",):
                try:
                    fg = widget.cget("fg_color")
                    bg = ""
                    try:
                        bg = widget.cget("bg")
                    except Exception:
                        pass
                    detail = f"fg={fg!r} bg={bg!r}"
                    break
                except Exception:
                    pass
            text = ""
            try:
                text = str(widget.cget("text"))[:14]
            except Exception:
                pass
            print(f"  ({x},{y}) -> {name} {detail} text={text!r}")
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
