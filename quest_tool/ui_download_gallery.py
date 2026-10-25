"""Virtual canvas for download results: one widget, bounded images, no resize rebuild."""
from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from PIL import ImageOps, ImageTk

from .messages import tr
from .theme import color, DOWNLOAD


class DownloadGallery(tk.Canvas):
    def __init__(self, master, pane):
        super().__init__(master, width=1, height=1, highlightthickness=0, borderwidth=0,
                         background=color("BG_APP"), yscrollincrement=24)
        self.pane = pane
        self._job = None
        self._photos = {}
        self._image_size = None
        self.hitboxes = {}
        self.paint_count = 0
        self.last_paint_ms = 0.0
        self._font = tkfont.Font(root=self, family=pane.font_family, size=10)
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<MouseWheel>", self._wheel)

    def redraw(self):
        if self._job is None and self.winfo_exists():
            self._job = self.after(16, self._paint)

    def scroll(self, *args):
        super().yview(*args)
        self.redraw()

    def _wheel(self, event):
        self.scroll("scroll", -int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
        return "break"

    def reset(self):
        self._photos.clear()
        super().yview_moveto(0)
        self.redraw()

    def _fit(self, text, width):
        text = str(text).replace("\n", " ")
        if self._font.measure(text) <= width:
            return text
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if self._font.measure(text[:mid] + "…") <= width:
                low = mid
            else:
                high = mid - 1
        return text[:low] + "…"

    def _image(self, item, x, y, width, height):
        source = self.pane._covers.get(item.get("coverURL"))
        if source is None:
            super().create_rectangle(x, y, x+width, y+height, fill=color("BG_INPUT"), outline="")
            return
        key = (item["id"], width, height)
        if key not in self._photos:
            self._photos[key] = ImageTk.PhotoImage(ImageOps.fit(source, (width, height)), master=self)
        super().create_image(x, y, image=self._photos[key], anchor="nw")

    def _control(self, item, action, x, y, size):
        self.hitboxes[(item["id"], action)] = (x, y, x+size, y+size)
        selected = item["id"] in self.pane.selected
        bg = color("ACCENT" if action == "check" and selected else "BG_CARD")
        ink = color("ACCENT_TEXT" if action == "check" and selected else "TEXT_PRIMARY")
        # Paint directly over the artwork: no opaque child canvas or white label.
        if action == "play":
            super().create_oval(x, y, x+size, y+size, fill=bg, outline=color("BORDER"))
            ink = color("TEXT_SECONDARY" if item.get("previewURL") else "TEXT_MUTED")
            super().create_polygon(x+size*.39, y+size*.28, x+size*.39, y+size*.72,
                                x+size*.72, y+size*.5, fill=ink, outline="")
        else:
            super().create_rectangle(x, y, x+size, y+size, fill=bg if selected else "",
                                    outline=color("ACCENT" if selected else "TEXT_SECONDARY"), width=max(2, size/16))
            if not selected:
                super().create_rectangle(x+2, y+2, x+size-2, y+size-2, fill="",
                                        outline=color("BG_CARD"), width=1)
            if selected:
                super().create_line(x+size*.3, y+size*.5, x+size*.45, y+size*.65,
                                 x+size*.72, y+size*.35, fill=ink, width=max(2, size/12))

    def _paint(self):
        import time
        started = time.perf_counter()
        self._job = None
        pane = self.pane
        scale = pane._get_widget_scaling()
        width, height = self.winfo_width(), self.winfo_height()
        gap = max(4, round(DOWNLOAD.GAP*scale))
        columns = pane.grid_columns if pane.view_mode == "grid" else 1
        cell = max(1, (width-gap*(columns+1))//columns)
        art = round(DOWNLOAD.ART_HEIGHT*scale)
        row_height = round((DOWNLOAD.CARD_HEIGHT if pane.view_mode == "grid" else DOWNLOAD.LIST_HEIGHT)*scale)
        size = round(DOWNLOAD.CONTROL*scale)
        image_size = (cell, art, scale, pane.view_mode)
        if image_size != self._image_size:
            self._photos.clear()
            self._image_size = image_size
        self._font.configure(size=-max(9, round(12*scale)))
        self.configure(bg=color("BG_APP"), scrollregion=(0, 0, width,
                       max(height, math.ceil(len(pane.rows)/columns)*(row_height+gap)+gap)))
        top = super().canvasy(0)
        self.delete("all")
        self.hitboxes.clear()
        pane.song_views.clear()
        pane._columns, pane._cell_width = columns, cell/scale
        for index, item in enumerate(pane.rows):
            x, y = gap+(index % columns)*(cell+gap), gap+(index//columns)*(row_height+gap)
            pane.song_views[item["id"]] = (x, y, x+cell, y+row_height)
            if y+row_height < top or y > top+height:
                continue
            edge = "ACCENT" if pane.current_item and pane.current_item["id"] == item["id"] else "BORDER"
            super().create_rectangle(x, y, x+cell, y+row_height, fill=color("BG_CARD"), outline=color(edge))
            if pane.view_mode == "grid":
                self._image(item, x+1, y+1, max(1, cell-2), art)
                cx = x+cell-gap-size
                self._control(item, "check", cx, y+art-gap-size*2-gap, size)
                self._control(item, "play", cx, y+art-gap-size, size)
                available = max(1, cell-gap*2)
                for offset, text, role, bold in ((18, item["title"], "TEXT_PRIMARY", True),
                        (40, item["artist"], "TEXT_SECONDARY", False),
                        (62, tr("layout.mapper", mapper=item["mapper"]), "TEXT_MUTED", False)):
                    super().create_text(x+gap, y+art+round(offset*scale), anchor="w",
                        text=self._fit(text, available), fill=color(role),
                        font=(pane.font_family, -round(12*scale), "bold" if bold else "normal"))
                rating = tr("search.unknown") if item["rating"] is None else f"{item['rating']*100:.1f}%"
                super().create_text(x+gap, y+row_height-gap*2, anchor="w", text=rating,
                                 font=(pane.font_family, -round(11*scale)), fill=color("SUCCESS_TEXT"))
                super().create_text(x+cell-gap, y+row_height-gap*2, anchor="e", text=pane._duration_text(item),
                                 font=(pane.font_family, -round(11*scale)), fill=color("TEXT_MUTED"))
            else:
                thumb = round(52*scale)
                self._image(item, x+gap, y+gap, thumb, thumb)
                tx = x+thumb+gap*2
                for offset, text, role in ((22, item["title"], "TEXT_PRIMARY"),
                                          (44, item["artist"]+" · "+item["mapper"], "TEXT_MUTED")):
                    super().create_text(tx, y+round(offset*scale), anchor="w",
                        text=self._fit(text, max(1, cell-thumb-size*2-gap*6)),
                        font=(pane.font_family, -round(12*scale)), fill=color(role))
                self._control(item, "check", x+cell-size*2-gap*2, y+(row_height-size)//2, size)
                self._control(item, "play", x+cell-size-gap, y+(row_height-size)//2, size)
        self.paint_count += 1
        self.last_paint_ms = (time.perf_counter()-started)*1000

    def _click(self, event):
        x, y = super().canvasx(event.x), super().canvasy(event.y)
        for (song_id, action), (left, top, right, bottom) in self.hitboxes.items():
            if left <= x <= right and top <= y <= bottom:
                item = next(row for row in self.pane.rows if row["id"] == song_id)
                if action == "check":
                    checked = self.pane.checks[song_id]
                    checked.set(not checked.get())
                    self.pane.toggle(item)
                    self.redraw()
                elif item.get("previewURL"):
                    self.pane.preview_song(item)
                return
        for song_id, (left, top, right, bottom) in self.pane.song_views.items():
            if left <= x <= right and top <= y <= bottom:
                self.pane.show_info(next(row for row in self.pane.rows if row["id"] == song_id))
                return

    def destroy(self):
        if self._job is not None:
            self.after_cancel(self._job)
        super().destroy()
