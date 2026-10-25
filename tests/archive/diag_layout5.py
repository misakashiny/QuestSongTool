"""诊断 5：打印 SongListWidget 的 grid 行分配，找出重叠原因。"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))


def main() -> int:
    sandbox = Path(__file__).resolve().parent / "_diag5"
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
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and app.list_local.total_count() < 6:
        app.root.update()
        time.sleep(0.05)
    for _ in range(12):
        app.root.update_idletasks()
        app.root.update()
        time.sleep(0.05)

    widget = app.list_local
    print(f"SongListWidget: h={widget.winfo_height()} reqh={widget.winfo_reqheight()}")
    print("行分配（grid_rowconfigure / bbox）:")
    for row in range(6):
        try:
            bbox = widget.grid_bbox(0, row)
        except Exception as exc:
            bbox = f"<{exc}>"
        conf = {k: v for k, v in widget.grid_rowconfigure(row).items() if k in ("weight", "minsize")}
        print(f"  row {row}: bbox={bbox} {conf}")
    print("各子控件实际位置:")
    for child in widget.winfo_children():
        info = child.grid_info()
        print(f"  {child.__class__.__name__}: row={info.get('row')} y={child.winfo_y()} "
              f"h={child.winfo_height()} reqh={child.winfo_reqheight()} sticky={info.get('sticky')}")
    print(f"root: h={app.root.winfo_height()} reqh={app.root.winfo_reqheight()}")
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
