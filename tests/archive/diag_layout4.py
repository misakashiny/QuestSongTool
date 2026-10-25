"""诊断 4：底部各行/按钮的实际几何，找出重叠与溢出的控件。"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))


def build_library(local: Path) -> None:
    names = ["1000 Light Years Away", "Beat It", "Believer", "Blinding Lights", "Dance Monkey",
             "Dynamite", "Escape", "Gangnam Style", "Havana", "Ievan Polkka"]
    for index, name in enumerate(names):
        folder = local / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "info.dat").write_text(
            json.dumps({"_songName": name, "_songAuthorName": "A", "_beatsPerMinute": 120 + index,
                        "_difficultyBeatmapSets": [{"_difficultyBeatmaps": [{"_difficulty": "Expert"}]}]},
                       ensure_ascii=False),
            encoding="utf-8",
        )


def rect(widget) -> str:
    try:
        return (f"x={widget.winfo_x()} y={widget.winfo_y()} "
                f"w={widget.winfo_width()} h={widget.winfo_height()} "
                f"reqw={widget.winfo_reqwidth()} reqh={widget.winfo_reqheight()}")
    except Exception as exc:
        return f"<{exc}>"


def main() -> int:
    sandbox = Path(__file__).resolve().parent / "_diag4"
    shutil.rmtree(sandbox, ignore_errors=True)
    local = sandbox / "local"
    local.mkdir(parents=True)
    build_library(local)

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
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and app.list_local.total_count() < 10:
        app.root.update()
        time.sleep(0.05)
    app.list_local.select("Blinding Lights")
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and app.detail_snapshot()["title"] == "—":
        app.root.update()
        time.sleep(0.05)
    for _ in range(10):
        app.root.update_idletasks()
        app.root.update()
        time.sleep(0.05)

    print("== 左卡片（列表区）==")
    left = app.left_card
    print(f"  左卡片: {rect(left)}")
    for child in left.winfo_children():
        print(f"    - {child.__class__.__name__}: {rect(child)}")

    print("== 列表控件内部（第一个列表）==")
    widget = app.list_local
    print(f"  SongListWidget: {rect(widget)}")
    for child in widget.winfo_children():
        mapped = child.winfo_ismapped()
        print(f"    - {child.__class__.__name__}: mapped={mapped} {rect(child)}")
        for grand in child.winfo_children():
            if grand.winfo_reqwidth() > 120 or grand.winfo_reqheight() > 20:
                print(f"        · {grand.__class__.__name__}: mapped={grand.winfo_ismapped()} {rect(grand)}")

    print("== 底部状态栏 ==")
    for child in app.root.winfo_children():
        if child.grid_info().get("row") == 2:
            print(f"  状态栏: {rect(child)}")
            for grand in child.winfo_children():
                print(f"    - {grand.__class__.__name__}: {rect(grand)}")

    print("== 操作按钮组 ==")
    bar = left.winfo_children()[-1] if left.winfo_children() else None
    if bar is not None:
        for group in bar.winfo_children():
            print(f"  组: {rect(group)}")
            for glass in group.winfo_children():
                print(f"    - {glass.__class__.__name__}: {rect(glass)}")

    print(f"== 根窗口: {rect(app.root)} 屏幕={app.root.winfo_screenwidth()}x{app.root.winfo_screenheight()}")
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
