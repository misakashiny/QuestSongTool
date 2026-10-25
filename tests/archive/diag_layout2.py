"""诊断 2：找出各个"宽度需求大户"。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))


def main() -> int:
    sandbox = Path(__file__).resolve().parent / "_diag2"
    shutil.rmtree(sandbox, ignore_errors=True)
    local = sandbox / "local"
    local.mkdir(parents=True)
    import json

    for i in range(5):
        folder = local / f"Song {i:02d}"
        folder.mkdir()
        (folder / "info.dat").write_text(
            json.dumps({"_songName": f"Song {i}", "_songAuthorName": "A", "_beatsPerMinute": 120,
                        "_difficultyBeatmapSets": [{"_difficultyBeatmaps": [{"_difficulty": "Expert"}]}]},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    import customtkinter as ctk
    from tkinter import ttk

    from quest_tool.app import QuestComparatorApp
    from quest_tool.storage import Settings

    ctk.set_appearance_mode("Dark")
    app = QuestComparatorApp(
        settings=Settings(local_path=str(local), device_poll_on_start=False, appearance="Dark")
    )
    app.root.geometry("1500x950+30+30")
    app.root.update()
    app.load_library("local")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and app.list_local.total_count() < 5:
        app.root.update()
        time.sleep(0.05)
    app.root.update_idletasks()
    app.root.update()

    print("=== 三个列表控件的宽度需求 ===")
    for name, widget in (("local", app.list_local), ("quest", app.list_quest), ("missing", app.list_missing)):
        print(f"  {name}: reqw={widget.winfo_reqwidth()} w={widget.winfo_width()}")
        for child in widget.winfo_children():
            print(f"     - {child.__class__.__name__}: reqw={child.winfo_reqwidth()}")
            if isinstance(child, ttk.Treeview):
                total = sum(child.column(col, "width") for col in child["columns"]) + 260
                print(f"       列宽合计(含#0)={total} 列={[(c, child.column(c,'width')) for c in child['columns']]}")

    print("=== 右侧详情面板 ===")
    right = app.root.grid_slaves(row=0, column=1)[0]
    print(f"  右列 reqw={right.winfo_reqwidth()}")
    for child in right.winfo_children():
        print(f"     - {child.__class__.__name__}: reqw={child.winfo_reqwidth()} w={child.winfo_width()}")
        for grand in child.winfo_children():
            print(f"        - {grand.__class__.__name__}: reqw={grand.winfo_reqwidth()} w={grand.winfo_width()}")
            for gg in grand.winfo_children():
                req = gg.winfo_reqwidth()
                if req > 200:
                    print(f"           - {gg.__class__.__name__}: reqw={req} w={gg.winfo_width()}")

    print("=== 路径区 ===")
    left = app.root.grid_slaves(row=0, column=0)[0]
    print(f"  左列 reqw={left.winfo_reqwidth()}")
    for child in left.winfo_children():
        print(f"     - {child.__class__.__name__}: reqw={child.winfo_reqwidth()} w={child.winfo_width()}")

    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
