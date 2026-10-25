"""诊断 3：从根往下打印所有 reqwidth>380 的控件（带类名与层级）。"""
from __future__ import annotations

import json
import shutil
import sys
import time

from _paths import ROOT, sandbox  # noqa: E402
sys.path.insert(0, str(ROOT))


def main() -> int:
    work = sandbox("diag_layout3")
    shutil.rmtree(sandbox, ignore_errors=True)
    local = work / "local"
    local.mkdir(parents=True)
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
    app.list_local.select("Song 00")
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and app.detail_snapshot()["title"] == "—":
        app.root.update()
        time.sleep(0.05)
    app.root.update_idletasks()
    app.root.update()

    print(f"root: width={app.root.winfo_width()} reqwidth={app.root.winfo_reqwidth()}")

    hits: list[tuple[int, str, int, int, str]] = []

    def walk(widget, depth: int = 0, path: str = ".") -> None:
        for index, child in enumerate(widget.winfo_children()):
            req = child.winfo_reqwidth()
            name = child.__class__.__name__
            here = f"{path}/{name}[{index}]"
            if req > 380:
                hits.append((depth, name, req, child.winfo_width(), here))
            walk(child, depth + 1, here)

    walk(app.root)
    for depth, name, req, width, path in hits:
        print(f"{'  ' * depth}- {name}: reqw={req} w={width}  @ {path}")
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
