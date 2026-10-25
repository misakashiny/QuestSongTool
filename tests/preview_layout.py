"""Render layout screenshots with synthetic songs and isolated runtime data."""
from __future__ import annotations

import json
import sys
import time

from _paths import ROOT, sandbox

sys.path.insert(0, str(ROOT))


def pump(root, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)


def main():
    from PIL import Image, ImageDraw, ImageGrab
    from quest_tool import storage

    workspace = sandbox("layout_preview")
    storage.data_dir = lambda: workspace
    from quest_tool.app import QuestComparatorApp
    from quest_tool.storage import Settings
    import customtkinter as ctk

    examples = [
        ("Plain Jane", "A$AP Ferg", "#f4c524"),
        ("Overwhelmed", "Royal & the Serpent", "#255d8c"),
        ("Rightfully", "Mili", "#a94359"),
        ("Daddy", "PSY", "#d6b85a"),
        ("24K Magic", "Bruno Mars", "#962f4d"),
        ("Hit The Road Jack", "Ray Charles", "#34445a"),
        ("Time Left", "Underoath", "#576a80"),
        ("Rap God", "Eminem", "#5a4e6a"),
        ("Satisfaction", "Benny Benassi", "#8863a7"),
        ("Take On Me", "a-ha", "#467eac"),
        ("The Spectre", "Alan Walker", "#265d88"),
        ("Believer", "Imagine Dragons", "#cd6839"),
    ]
    local, quest = workspace / "LocalSongs", workspace / "QuestSongs"
    for index, (title, artist, shade) in enumerate(examples):
        for library in (local, quest):
            if library == quest and index in (0, 2, 4, 6):
                continue
            folder = library / f"{index:02d}_{title}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "info.dat").write_text(json.dumps({
                "_version": "2.0.0", "_songName": title,
                "_songAuthorName": artist, "_beatsPerMinute": 190,
                "_coverImageFilename": "cover.png",
                "_difficultyBeatmapSets": [{"_difficultyBeatmaps": [
                    {"_difficulty": "Expert"}, {"_difficulty": "ExpertPlus"}]}],
            }), encoding="utf-8")
            art = Image.new("RGB", (320, 320), shade)
            ImageDraw.Draw(art).text((24, 135), title.upper(),
                                    fill="black" if index == 0 else "white", font_size=28)
            art.save(folder / "cover.png")
    app = QuestComparatorApp(Settings(local_path=str(local), quest_path=str(quest),
                                     appearance="Light", wizard_done=True,
                                     device_poll_on_start=False, ui_scale="1.0"))
    root = app.root
    root.geometry("1240x780+0+0")
    pump(root, 1.5)
    assert root.winfo_reqwidth() <= root.winfo_width(), "Minimum window clips content"
    assert not app._details_visible, "Narrow window should collapse details"
    print("[PASS] minimum 1240x780 fits; details collapse")
    root.geometry("1600x980+0+0")
    pump(root, 2)
    assert app._details_visible, "Wide window should restore details"
    app.load_library("local")
    app.load_library("quest")
    pump(root, 3)
    app.compare("quest")
    pump(root, 1)
    app.list_local.select("00_Plain Jane")
    pump(root, 2)
    app.toast.clear()
    import ctypes
    user = ctypes.windll.user32
    hwnd = user.GetParent(root.winfo_id())
    assert user.GetWindowLongW(hwnd, -16) & 0x00C00000 != 0x00C00000, "Native caption still visible"
    assert user.GetMenu(hwnd) == 0, "Native menu bar still visible"
    app.window_chrome.maximize()
    pump(root, .4)
    assert root.state() == "zoomed", "Maximize failed"
    app.window_chrome.maximize()
    pump(root, .4)
    assert root.state() == "normal", "Restore failed"
    app.window_chrome.minimize()
    pump(root, .3)
    assert root.state() == "iconic", "Minimize failed"
    root.deiconify()
    root.geometry("1600x980+0+0")
    pump(root, .5)
    print("[PASS] native caption/menu absent; custom minimize/maximize/restore work")
    assert app.preview_button.winfo_width() == app.preview_button.winfo_height() == 32, "Preview button is not circular"
    assert app.preview_progress_row.winfo_ismapped(), "Idle preview controls are hidden"
    from quest_tool.audio import player
    app.preview_volume.cget("command")(.37)
    assert abs(player().volume() - .37) < .001, "Visible volume control is not connected to PCM gain"
    app.preview_volume.cget("command")(1.0)
    print("[PASS] preview is circular; idle controls visible; volume callback changes player gain")
    for mode in ("Light", "Dark"):
        ctk.set_appearance_mode(mode)
        app._refresh_theme()
        app._force_repaint()
        pump(root, 1)
        root.lift()
        root.attributes("-topmost", True)
        pump(root, .5)
        x, y = root.winfo_rootx(), root.winfo_rooty()
        path = ROOT / "docs" / "images" / f"v18-layout-{mode.lower()}.png"
        ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(path)
        print(path)
    app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
