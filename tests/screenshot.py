"""启动真实窗口、灌入示例数据、截图保存为 PNG（用于 UI 走查）。

用法: python tests/screenshot.py [输出目录]
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT, SHOTS, sandbox  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

from quest_tool.storage import Settings  # noqa: E402

SANDBOX = sandbox("screenshot")
OUT = SHOTS
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT

SONGS_LOCAL = [
    ("1000 Light Years Away", "Aqua", 128),
    ("Beat It", "Michael Jackson", 139),
    ("Believer", "Imagine Dragons", 125),
    ("Blinding Lights", "The Weeknd", 171),
    ("Ça Plane Pour Moi", "Plastic Bertrand", 168),
    ("Dance Monkey", "Tones and I", 98),
    ("Dynamite", "BTS", 114),
    ("Escape", "Ken Ashcorp", 150),
    ("Gangnam Style", "PSY", 132),
    ("Havana", "Camila Cabello", 105),
    ("Ievan Polkka", "Hatsune Miku", 119),
    ("K/DA POP/STARS", "K/DA", 170),
    ("Levitating", "Dua Lipa", 103),
    ("Night of Nights", "BeatMARIO", 180),
    ("Overtime", "Cash Cash", 128),
    ("Rasputin", "Boney M.", 126),
    ("Sandstorm", "Darude", 136),
    ("Take On Me", "a-ha", 169),
    ("Uptown Funk", "Mark Ronson", 115),
    ("Virtual Insanity", "Jamiroquai", 105),
    ("We Will Rock You", "Queen", 81),
    ("X Gon' Give It To Ya", "DMX", 96),
    ("Yoake To Hotaru", "n-buna", 200),
    ("Zenzenzense", "RADWIMPS", 190),
    ("中文歌曲·夜航星", "群星", 128),
    ("中文歌曲·起风了", "买辣椒也用券", 130),
]


def make_song(root: Path, name: str, author: str, bpm: float, diffs: list[str]) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "info.dat").write_text(
        json.dumps(
            {
                "_version": "2.0.0",
                "_songName": name,
                "_songSubName": "",
                "_songAuthorName": author,
                "_levelAuthorName": "Mapper",
                "_beatsPerMinute": bpm,
                "_songTimeOffset": 0,
                "_previewStartTime": 12,
                "_previewDuration": 10,
                "_coverImageFilename": "cover.png",
                "_difficultyBeatmapSets": [
                    {
                        "_beatmapCharacteristicName": "Standard",
                        "_difficultyBeatmaps": [{"_difficulty": d} for d in diffs],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from PIL import Image, ImageDraw

    # 生成一张有辨识度的封面（渐变 + 文字），方便看排版效果
    img = Image.new("RGB", (512, 512))
    draw = ImageDraw.Draw(img)
    for y in range(512):
        ratio = y / 511
        draw.line(
            [(0, y), (512, y)],
            fill=(int(40 + 120 * ratio), int(60 + 40 * (1 - ratio)), int(150 - 60 * ratio)),
        )
    draw.ellipse((140, 140, 372, 372), outline=(255, 255, 255), width=6)
    img.save(folder / "cover.png")


def make_library(root: Path, songs: list[tuple[str, str, float]], start: int = 0) -> None:
    root.mkdir(parents=True, exist_ok=True)
    variants = [
        ["Easy", "Normal", "Hard", "Expert"],
        ["Normal", "Hard", "Expert", "ExpertPlus"],
        ["Hard", "Expert"],
        ["Expert", "ExpertPlus"],
    ]
    for index, (name, author, bpm) in enumerate(songs):
        make_song(root, name, author, bpm, variants[(index + start) % len(variants)])


def main() -> int:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    local = SANDBOX / "PC CustomLevels"
    quest = SANDBOX / "Quest CustomLevels"
    # Quest 少 6 首、多 2 首，制造"缺失"效果
    make_library(local, SONGS_LOCAL, start=0)
    make_library(quest, SONGS_LOCAL[6:], start=2)
    make_library(quest, [("Quest 独占歌曲 A", "Someone", 140), ("Quest 独占歌曲 B", "Someone", 150)], start=0)

    import customtkinter as ctk

    from quest_tool.app import QuestComparatorApp

    ctk.set_appearance_mode("Dark")
    settings = Settings(
        local_path=str(local),
        quest_path=str(quest),
        appearance="Dark",
        device_poll_on_start=False,
    )
    app = QuestComparatorApp(settings=settings)
    # 使用程序自己的默认尺寸（会按屏幕夹取并居中），这样截图反映真实初始外观
    app.root.update()

    def pump(seconds: float, until=None) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.root.update()
            if until is not None and until():
                return
            time.sleep(0.05)

    app.load_library("local")
    app.load_library("quest")
    pump(25, until=lambda: app.list_local.total_count() >= 20 and app.list_quest.total_count() >= 20)
    app.compare("quest")
    pump(2)
    # 选中一首歌，让右侧详情/封面有内容
    app.list_local.select("Blinding Lights")
    pump(6, until=lambda: app.detail_snapshot()["title"] != "—")
    app.set_rating(4)
    pump(1)

    shots: list[tuple[str, str]] = []

    def grab(tag: str) -> None:
        # 置顶并抢焦点，否则截图可能抓到压在下面的其它窗口（实测踩过）
        try:
            app.root.deiconify()
            app.root.lift()
            app.root.attributes("-topmost", True)
            app.root.focus_force()
        except Exception:
            pass
        for _ in range(12):
            app.root.update_idletasks()
            app.root.update()
            time.sleep(0.12)
        app.root.update_idletasks()
        app.root.update()
        from PIL import ImageGrab

        # 隐藏所有 Toplevel（对话框），避免它们压在主窗口上被一起截进去
        hidden: list[object] = []
        for child in app.root.winfo_children():
            try:
                if isinstance(child, __import__("customtkinter").CTkToplevel) and child.winfo_ismapped():
                    child.withdraw()
                    hidden.append(child)
            except Exception:
                continue  # 有些对话框可能在遍历过程中已被销毁
        for _ in range(4):
            app.root.update()
            time.sleep(0.05)

        x = app.root.winfo_rootx()
        y = app.root.winfo_rooty()
        w = app.root.winfo_width()
        h = app.root.winfo_height()
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        path = OUT / f"{tag}.png"
        image.save(path)
        for child in hidden:
            try:
                child.deiconify()
            except Exception:
                pass
        try:
            app.root.attributes("-topmost", False)
        except Exception:
            pass
        shots.append((tag, str(path)))
        print(f"已保存 {path}  ({image.size[0]}x{image.size[1]})")

    grab("01_main_dark")

    # 封面墙视图
    app.set_view_mode("grid")
    pump(6.0)
    grab("01b_coverwall_dark")
    app.set_view_mode("list")
    pump(2.0)

    ctk.set_appearance_mode("Light")
    for widget in (app.list_local, app.list_quest, app.list_missing):
        widget.refresh_theme()
    pump(2)
    grab("02_main_light")

    ctk.set_appearance_mode("Dark")
    for widget in (app.list_local, app.list_quest, app.list_missing):
        widget.refresh_theme()
    pump(2)

    # 对话框截图
    from quest_tool.ui_dialogs import CommentEditDialog, ConfirmDialog, ProgressDialog

    dialog = ConfirmDialog(
        app.root,
        title="确认复制",
        message="将复制 6 首歌曲到 Quest 歌曲。",
        detail=f"目标：{quest}\n目标中同名文件夹会被覆盖（原内容先删除）。",
    )
    dialog.update()
    pump(1)
    grab("03_dialog_confirm")
    dialog.destroy()

    editor = CommentEditDialog(app.root, initial_text="节奏很稳，Expert 难度手感不错，推荐练手。")
    editor.update()
    pump(1)
    grab("04_dialog_comment")
    editor.destroy()

    progress = ProgressDialog(app.root, title="正在复制", message="正在复制 6 首歌曲…")
    progress.show_nonblocking()
    progress.set_progress(45, "正在复制 3/6：Believer")
    progress.append_log("已复制 1000 Light Years Away")
    progress.append_log("已复制 Beat It")
    pump(1.2)
    grab("05_dialog_progress")
    progress.destroy()

    from quest_tool.ui_devicepicker import DeviceFolderPicker

    picker = DeviceFolderPicker(app.root, candidates=[])
    picker.update()
    pump(1.5)
    grab("06_device_picker")
    picker.destroy()

    app.on_close()
    print("截图完成：")
    for tag, path in shots:
        print(f"  {tag}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
