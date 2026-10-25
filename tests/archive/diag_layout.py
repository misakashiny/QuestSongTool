"""诊断：主窗口各区域的实际占用尺寸，找出把窗口撑爆的控件。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

SONGS = [(f"Song {i:02d}", "Author", 120 + i) for i in range(30)]


def make(root: Path) -> None:
    import json

    for name, author, bpm in SONGS:
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "info.dat").write_text(
            json.dumps({"_version": "2.0.0", "_songName": name, "_songAuthorName": author,
                        "_beatsPerMinute": bpm, "_coverImageFilename": "cover.png",
                        "_difficultyBeatmapSets": [{"_difficultyBeatmaps": [{"_difficulty": "Expert"}]}]},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    from PIL import Image

    Image.new("RGB", (256, 256), (90, 60, 160)).save(root / SONGS[0][0] / "cover.png")


def main() -> int:
    import shutil

    sandbox = Path(__file__).resolve().parent / "_diag"
    shutil.rmtree(sandbox, ignore_errors=True)
    local = sandbox / "local"
    local.mkdir(parents=True)
    make(local)

    import customtkinter as ctk

    from quest_tool.app import QuestComparatorApp
    from quest_tool.storage import Settings

    ctk.set_appearance_mode("Dark")
    app = QuestComparatorApp(
        settings=Settings(local_path=str(local), device_poll_on_start=False, appearance="Dark")
    )
    app.root.geometry("1500x950+30+30")
    app.root.update()
    app.load_library("local")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and app.list_local.total_count() < 30:
        app.root.update()
        time.sleep(0.05)
    app.list_local.select("Song 00")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and app.info_labels["title"].cget("text") == "—":
        app.root.update()
        time.sleep(0.05)
    app.root.update_idletasks()
    app.root.update()

    print(f"root geometry={app.root.geometry()}  reqwidth={app.root.winfo_reqwidth()} "
          f"width={app.root.winfo_width()}")
    left = app.root.grid_slaves(row=0, column=0)[0]
    right = app.root.grid_slaves(row=0, column=1)[0]
    for label, widget in (("左列", left), ("右列", right)):
        print(f"  {label}: x={widget.winfo_x()} w={widget.winfo_width()} "
              f"reqw={widget.winfo_reqwidth()} grid={widget.grid_info()}")

    def walk(widget, depth: int = 0, limit: int = 3) -> None:
        if depth > limit:
            return
        for child in widget.winfo_children():
            req = child.winfo_reqwidth()
            if req > 300:
                print("  " * depth + f"- {child.__class__.__name__} reqw={req} w={child.winfo_width()} "
                      f"x={child.winfo_x()} grid={child.grid_info() if child.winfo_manager() == 'grid' else child.winfo_manager()}")
            walk(child, depth + 1, limit)

    print("右侧列内 reqwidth>300 的子控件：")
    walk(right)

    screen_w = app.root.winfo_screenwidth()
    print(f"屏幕宽度={screen_w}  窗口宽={app.root.winfo_width()}  右列右边界="
          f"{right.winfo_x() + right.winfo_width()}")
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
