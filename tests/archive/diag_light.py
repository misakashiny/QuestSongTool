"""定位浅色模式截图里那些"黑底"控件：打印它们的坐标、颜色与文字。"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))


def main() -> int:
    sandbox = Path(__file__).resolve().parent / "_diag6"
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

    app = QuestComparatorApp(
        settings=Settings(local_path=str(local), device_poll_on_start=False, appearance="Light")
    )
    app.root.geometry("1620x1000+20+20")
    app.root.update()
    app.load_library("local")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and app.list_local.total_count() < 6:
        app.root.update()
        time.sleep(0.05)
    for _ in range(10):
        app.root.update_idletasks()
        app.root.update()
        time.sleep(0.05)

    print(f"外观 = {ctk.get_appearance_mode()}")

    def report(label: str, widget, parent_prefix: str = "") -> None:
        try:
            x = widget.winfo_rootx() - app.root.winfo_rootx()
            y = widget.winfo_rooty() - app.root.winfo_rooty()
            w, h = widget.winfo_width(), widget.winfo_height()
            text = ""
            for attr in ("cget",):
                try:
                    text = str(getattr(widget, attr)("text"))[:20]
                    break
                except Exception:
                    pass
            print(f"  {label}: xy=({x},{y}) {w}x{h} text={text!r} {parent_prefix}")
        except Exception as exc:
            print(f"  {label}: <{exc}>")

    for name, widget in (("本地", app.list_local), ("Quest", app.list_quest), ("缺失", app.list_missing)):
        report(f"列表[{name}].header", widget._header)
        report(f"列表[{name}].badge", widget._count_badge)

    print("路径卡片标题：")
    for child in app.left_card.winfo_children()[0].winfo_children():
        report(f"  {child.__class__.__name__}", child)

    print("歌单工具条文字颜色令牌 T['TEXT_PRIMARY'] =", __import__('quest_tool.theme', fromlist=['T']).T["TEXT_PRIMARY"])
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
