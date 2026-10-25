"""对比 深色创建→切浅色 与 直接浅色创建 两种情况下控件的实际颜色。

如果两者不一致，说明"显式 configure 的颜色元组没有跟随主题切换"。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))


def build(sandbox: Path) -> Path:
    local = sandbox / "local"
    local.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        folder = local / f"Song {index}"
        folder.mkdir(exist_ok=True)
        (folder / "info.dat").write_text(
            json.dumps({"_songName": f"Song {index}", "_songAuthorName": "A", "_beatsPerMinute": 120,
                        "_difficultyBeatmapSets": [{"_difficultyBeatmaps": [{"_difficulty": "Expert"}]}]},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    return local


def probe(tag: str, initial_mode: str, switch_to: str | None, sandbox: Path) -> None:
    import customtkinter as ctk

    from quest_tool.app import QuestComparatorApp
    from quest_tool.storage import Settings

    ctk.set_appearance_mode(initial_mode)
    app = QuestComparatorApp(
        settings=Settings(local_path=str(build(sandbox)), device_poll_on_start=False,
                          appearance=initial_mode)
    )
    app.root.geometry("1620x1000+20+20")
    app.root.update()
    app.load_library("local")
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and app.list_local.total_count() < 3:
        app.root.update()
        time.sleep(0.05)

    if switch_to:
        app._appearance_var.set(switch_to)
        app._on_appearance_changed()
    for _ in range(8):
        app.root.update_idletasks()
        app.root.update()
        time.sleep(0.05)

    header = app.list_local._header
    badge = app.list_local._count_badge
    print(f"[{tag}] 外观={ctk.get_appearance_mode()}")
    print(f"    header text_color={header.cget('text_color')!r}")
    print(f"    badge  text_color={badge.cget('text_color')!r} fg_color={badge.cget('fg_color')!r}")
    print(f"    appbar device_badge text_color={app.device_badge.cget('text_color')!r}")
    if app.metric_labels:
        first = next(iter(app.metric_labels.values()))
        print(f"    metric value text_color={first.cget('text_color')!r} text={first.cget('text')!r}")
    app.on_close()


def main() -> int:
    sandbox = Path(__file__).resolve().parent / "_diag7"
    shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True)
    probe("A 深色创建→切浅色", "Dark", "Light", sandbox / "a")
    probe("B 直接浅色创建", "Light", None, sandbox / "b")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
