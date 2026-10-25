"""单独验证 Ctrl+C 绑定是否真的生效（排除测试脚手架的干扰）。"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

from _paths import ROOT  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

import customtkinter as ctk  # noqa: E402

from quest_tool.ui_songlist import SongItem, SongListWidget  # noqa: E402

root = ctk.CTk()
root.geometry("420x520+40+40")
widget = SongListWidget(root, title="测试")
widget.pack(fill="both", expand=True)
widget.set_items([SongItem(folder_name=f"Song{i:04d}", title=f"曲目 {i}") for i in range(10)])
root.update()
time.sleep(0.3)
root.update()

widget.select("Song0003")
widget._tree.focus_force()
root.update()
time.sleep(0.2)
root.update()

print("绑定列表:", widget._tree.bind("<Control-c>"))
print("当前选中 :", widget.selected())
print("焦点控件 :", root.focus_get())

widget._tree.event_generate("<Control-c>")
root.update()
try:
    print("剪贴板   :", repr(root.clipboard_get()))
except tk.TclError as exc:
    print("剪贴板   : <读取失败>", exc)
print("状态栏   :", repr(widget._status.cget("text")))

# 对照：直接调用处理函数
widget._on_key_copy()
root.update()
print("直接调用 :", repr(root.clipboard_get()))

root.destroy()
