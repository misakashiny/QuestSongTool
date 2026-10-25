# -*- coding: utf-8 -*-
"""探针：工作线程里 self.after(0, cb) 能否唤醒正在 wait_window 的主线程？"""
from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import customtkinter as ctk

print("tcl threaded :", tk.Tcl().eval("set tcl_platform(threaded)"))
print("_tkinter has Tkapp_Call threading:", hasattr(tk, "_tkinter"))

root = ctk.CTk()
root.withdraw()
win = ctk.CTkToplevel(root)
win.geometry("300x150+100+100")
state = {"after_ok": None, "err": "", "queue": False}


def worker() -> None:
    time.sleep(0.2)
    try:
        win.after(0, lambda: state.__setitem__("queue", True))
        state["after_ok"] = True
    except Exception as exc:  # noqa: BLE001
        state["after_ok"] = False
        state["err"] = f"{type(exc).__name__}: {exc}"


threading.Thread(target=worker, daemon=True).start()
root.after(400, lambda: None)
t0 = time.perf_counter()
win.wait_visibility()
print(f"wait_visibility 用时 {(time.perf_counter() - t0) * 1000:.0f} ms")
t0 = time.perf_counter()
win.after(1500, win.destroy)
win.wait_window()
print(f"wait_window 结束，用时 {(time.perf_counter() - t0) * 1000:.0f} ms")
print("worker after() 成功 :", state["after_ok"], state["err"])
print("回调真的执行了吗  :", state["queue"])
root.destroy()
