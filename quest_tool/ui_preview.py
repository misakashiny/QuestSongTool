"""Fixed circular preview control, independent of font glyph metrics."""
from __future__ import annotations

import tkinter as tk
import customtkinter as ctk

from .theme import T, color


class RoundPlayButton(ctk.CTkFrame):
    def __init__(self, master, command):
        super().__init__(master, width=32, height=32, fg_color=T["BG_CARD"], corner_radius=0)
        super().pack_propagate(False)
        self._command = command
        self._symbol = "▶"
        self._state = "normal"
        self._icon_canvas = tk.Canvas(self, width=32, height=32, highlightthickness=0, borderwidth=0)
        self._icon_canvas.pack(fill="both", expand=True)
        self._icon_canvas.bind("<Button-1>", lambda _event: self.invoke())
        self._icon_canvas.bind("<Enter>", lambda _event: self._paint(True))
        self._icon_canvas.bind("<Leave>", lambda _event: self._paint())
        self._paint()

    def _paint(self, hover=False):
        canvas = self._icon_canvas
        canvas.configure(bg=color("BG_CARD"))
        canvas.delete("all")
        canvas.create_oval(1, 1, 30, 30, outline=color("BORDER"),
                           fill=color("BG_HOVER" if hover else "BG_CARD"))
        ink = color("TEXT_MUTED" if self._state == "disabled" else "TEXT_SECONDARY")
        if self._symbol == "■":
            canvas.create_rectangle(11, 11, 21, 21, outline="", fill=ink)
        else:
            canvas.create_polygon(12, 9, 12, 23, 23, 16, outline="", fill=ink)

    def configure(self, **kwargs):
        if "text" in kwargs:
            self._symbol = kwargs.pop("text")
        if "state" in kwargs:
            self._state = kwargs.pop("state")
        super().configure(**kwargs)
        if hasattr(self, "_icon_canvas"):
            self._paint()

    def cget(self, key):
        if key == "text":
            return self._symbol
        if key == "state":
            return self._state
        return super().cget(key)

    def invoke(self):
        if self._state != "disabled":
            self._command()

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "_icon_canvas"):
            self._paint()
