"""精确复现 screenshot.py 的路径：Dark 创建 → 切 Light → refresh_theme，打印 bg 值。"""
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


def main() -> int:
    sandbox = Path(__file__).resolve().parent / "_diag9"
    shutil.rmtree(sandbox, ignore_errors=True)
    local = sandbox / "local"
    local.mkdir(parents=True)
    for index in range(5):
        folder = local / f"Song {index}"
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
    while time.monotonic() < deadline and app.list_local.total_count() < 5:
        app.root.update()
        time.sleep(0.05)
    for _ in range(10):
        app.root.update_idletasks()
        app.root.update()
        time.sleep(0.05)

    widget = app.list_local
    print("=== 切换前（Dark）===")
    print(f"  外观={ctk.get_appearance_mode()}  list.bg={widget.cget('bg')!r}  panel_bg={widget._panel_bg()!r}")

    # 完全照 screenshot.py 的做法
    ctk.set_appearance_mode("Light")
    for target in (app.list_local, app.list_quest, app.list_missing):
        target.refresh_theme()
    for _ in range(10):
        app.root.update_idletasks()
        app.root.update()
        time.sleep(0.05)

    print("=== 切换后（Light）===")
    print(f"  外观={ctk.get_appearance_mode()}  list.bg={widget.cget('bg')!r}  panel_bg={widget._panel_bg()!r}")
    print(f"  header text_color={widget._header.cget('text_color')!r} fg={widget._header.cget('fg_color')!r}")
    print(f"  ttk row tag option={widget._tree.tag_configure('row')}")

    # 再走一遍应用自己的主题切换入口（会额外刷新根窗口底色）
    app._appearance_var.set("Light")
    app._on_appearance_changed()
    for _ in range(10):
        app.root.update_idletasks()
        app.root.update()
        time.sleep(0.05)
    print("=== 走 _on_appearance_changed 之后 ===")
    print(f"  list.bg={widget.cget('bg')!r}  panel_bg={widget._panel_bg()!r}")
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
