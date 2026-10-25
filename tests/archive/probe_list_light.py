"""单独把 SongListWidget 放在浅色窗口里截图，确认标题栏是否真的渲染异常。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

import customtkinter as ctk  # noqa: E402

from quest_tool.ui_songlist import SongItem, SongListWidget  # noqa: E402

OUT = Path(__file__).resolve().parent / "_shots_out"


def shoot(mode: str, tag: str) -> None:
    ctk.set_appearance_mode(mode)
    root = ctk.CTk()
    root.title(f"light-probe-{tag}")
    root.geometry("460x520+60+60")
    root.configure(fg_color=("#f4f6f9", "#16181d"))
    widget = SongListWidget(root, title="本地歌曲")
    widget.pack(fill="both", expand=True, padx=8, pady=8)
    widget.set_items(
        [SongItem(folder_name=f"Song {i:02d}", title=f"歌曲 {i}") for i in range(8)],
        rating_map={"Song 00": 4},
        highlight={"Song 02"},
    )
    for _ in range(12):
        root.update_idletasks()
        root.update()
        time.sleep(0.1)

    root.lift()
    root.attributes("-topmost", True)
    for _ in range(8):
        root.update()
        time.sleep(0.1)

    from PIL import ImageGrab

    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    path = OUT / f"probe_list_{tag}.png"
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
    print(f"已保存 {path}")
    root.attributes("-topmost", False)
    root.destroy()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    shoot("Light", "light")
    shoot("Dark", "dark")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
