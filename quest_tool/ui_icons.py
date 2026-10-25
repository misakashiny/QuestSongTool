"""Small semantic line icons drawn without emoji font fallback."""
from __future__ import annotations

import tkinter as tk
import customtkinter as ctk

from .theme import T, color


class LineIcon(ctk.CTkFrame):
    def __init__(self, master, kind="local", role="ACCENT"):
        super().__init__(master, width=30, height=30, corner_radius=6, fg_color=T["BG_INPUT"])
        self.kind, self.role = kind, role
        self._icon_canvas = tk.Canvas(self, width=28, height=28, highlightthickness=0, borderwidth=0)
        self._icon_canvas.place(relx=.5, rely=.5, anchor="center")
        self._paint()

    def _paint(self):
        canvas = self._icon_canvas
        canvas.configure(bg=color("BG_INPUT"))
        canvas.delete("all")
        ink = color(self.role)
        if self.kind == "local":
            canvas.create_rectangle(4, 4, 24, 19, outline=ink, width=2)
            canvas.create_line(14, 20, 14, 24, fill=ink, width=2)
            canvas.create_line(8, 25, 20, 25, fill=ink, width=2)
        elif self.kind == "quest":
            canvas.create_rectangle(3, 8, 25, 20, outline=ink, width=2)
            canvas.create_oval(6, 11, 12, 16, outline=ink)
            canvas.create_oval(17, 11, 23, 16, outline=ink)
            canvas.create_line(12, 14, 17, 14, fill=ink)
        else:
            canvas.create_polygon(14, 3, 26, 25, 2, 25, outline=ink, fill="", width=2)
            canvas.create_line(14, 10, 14, 17, fill=ink, width=2)
            canvas.create_oval(13, 20, 15, 22, fill=ink, outline=ink)

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "_icon_canvas"):
            self._paint()


class _IconImage(ctk.CTkImage):
    def __init__(self, master, **kwargs):
        super().__init__(**kwargs)
        self._master = master
        self._photos = {}

    def create_scaled_photo_image(self, widget_scaling, appearance_mode):
        from PIL import ImageTk
        width, height = self.cget("size")
        size = (round(width * widget_scaling), round(height * widget_scaling))
        key = (size, appearance_mode)
        if key not in self._photos:
            source = self.cget("dark_image" if appearance_mode == "dark" else "light_image")
            self._photos[key] = ImageTk.PhotoImage(source.resize(size), master=self._master)
        return self._photos[key]


def icon_image(kind, ink, size=18, master=None):
    """Antialiased assets; no system symbol font or emoji fallback."""
    from PIL import Image, ImageDraw
    def render(shade):
        im = Image.new("RGBA", (96, 96))
        d = ImageDraw.Draw(im)
        def line(points):
            d.line([(int(x * 4), int(y * 4)) for x, y in points], fill=shade, width=6)
        def box(bounds):
            d.rectangle(tuple(int(v * 4) for v in bounds), outline=shade, width=6)
        if kind == "search":
            d.ellipse((12, 8, 68, 64), outline=shade, width=6)
            line([(16, 15), (23, 22)])
        elif kind == "local":
            box((3, 3, 21, 16)); line([(12, 16), (12, 21)]); line([(7, 21), (17, 21)])
        elif kind == "quest":
            d.rounded_rectangle((8, 28, 88, 76), radius=12, outline=shade, width=6)
            for x in (6, 16):
                d.ellipse((x*4, 11*4, (x+3)*4, 14*4), fill=shade)
            line([(10, 13), (14, 13)])
        elif kind == "list":
            for y in (6, 12, 18):
                box((3, y-1, 5, y+1)); line([(8, y), (21, y)])
        elif kind == "grid":
            for x in (3, 14):
                for y in (3, 14): box((x, y, x+7, y+7))
        elif kind in ("import", "export", "arrow"):
            if kind == "arrow": line([(3, 12), (21, 12)]); line([(15, 6), (21, 12), (15, 18)])
            else:
                line([(4, 17), (4, 21), (20, 21), (20, 17)])
                line([(12, 3), (12, 16)])
                line([(7, 11), (12, 16), (17, 11)] if kind == "import" else [(7, 8), (12, 3), (17, 8)])
        elif kind in ("report", "duplicate"):
            if kind == "duplicate": box((3, 7, 16, 21)); box((8, 3, 21, 17))
            else:
                box((4, 3, 20, 21))
                for y in (8, 12, 16): line([(8, y), (16, y)])
        elif kind == "edit":
            line([(4, 17), (4, 21), (8, 20), (21, 7), (17, 3), (4, 17)])
            line([(14, 6), (18, 10)])
        elif kind == "folder":
            line([(3, 20), (3, 5), (10, 5), (12, 8), (21, 8), (21, 20), (3, 20)])
        elif kind == "delete":
            box((6, 7, 18, 21)); line([(3, 6), (21, 6)]); line([(9, 3), (15, 3)])
            line([(10, 10), (10, 18)]); line([(14, 10), (14, 18)])
        elif kind == "volume":
            line([(3, 9), (7, 9), (12, 4), (12, 20), (7, 15), (3, 15), (3, 9)])
            for b in ((9, 6, 19, 18), (8, 2, 23, 22)):
                d.arc(tuple(v*4 for v in b), -60, 60, fill=shade, width=5)
        elif kind == "warning":
            line([(12, 2), (23, 21), (1, 21), (12, 2)]); line([(12, 8), (12, 14)]); box((11.5, 17, 12.5, 18))
        elif kind == "settings":
            import math
            for i in range(8):
                a=i*math.pi/4
                line([(12+7*math.cos(a),12+7*math.sin(a)), (12+10*math.cos(a),12+10*math.sin(a))])
            d.ellipse((20,20,76,76), outline=shade, width=6); d.ellipse((38,38,58,58), outline=shade, width=5)
        elif kind in ("wireless", "detect"):
            d.ellipse((40,40,56,56), outline=shade, width=5)
            for b in ((5,5,19,19), (1,1,23,23)):
                d.arc(tuple(v*4 for v in b), -55,55,fill=shade,width=5)
                d.arc(tuple(v*4 for v in b), 125,235,fill=shade,width=5)
        elif kind == "history":
            d.arc((12,12,84,84), -120,210,fill=shade,width=6); line([(12,6),(12,12),(17,15)])
        else:
            d.arc((12,12,84,84), 25,320,fill=shade,width=6); line([(16,3),(21,5),(20,10)])
        return im.resize((size*2,size*2),Image.Resampling.LANCZOS)
    shades = ink if isinstance(ink, (tuple,list)) else (ink,ink)
    return _IconImage(master, light_image=render(shades[0]),dark_image=render(shades[1]),size=(size,size))


class IconButton(ctk.CTkButton):
    """Keep translated labels while replacing fragile font symbols with assets."""
    def __init__(self, master, icon=None, **kwargs):
        text = kwargs.get("text", "")
        mapping = {"✎":"edit", "✏":"edit", "⚡":"refresh", "🔍":"warning", "🔌":"detect", "📶":"wireless", "🔄":"refresh", "⚙":"settings",
                   "🕘":"history", "📁":"folder", "📂":"folder", "📥":"local", "📱":"quest",
                   "📤":"arrow", "🗑":"delete", "⚠":"warning", "☷":"list", "▦":"grid"}
        for glyph, kind in mapping.items():
            if text.startswith(glyph):
                icon = icon or kind
                text = text[len(glyph):].lstrip("️ ")
                break
        if icon:
            if icon == "arrow":
                text = text.lstrip("→ ")
            kwargs["text"] = text
            kwargs["image"] = icon_image(icon, kwargs.get("text_color", T["TEXT_PRIMARY"]), master=master)
        super().__init__(master, **kwargs)
