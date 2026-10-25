"""Canvas presentation for song rows; the existing list model owns all identities."""
from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont

from .messages import tr
from .theme import color


class SongRows(tk.Canvas):
    """Draw only visible rows, with independent checkboxes and numeric indices."""

    HEADER = 34

    def __init__(self, master, owner):
        super().__init__(master, highlightthickness=0, borderwidth=0, height=1, width=1)
        self.owner = owner
        self._job = None
        self._anchor = None
        self._font = tkfont.Font(root=self, family=owner._font_family, size=10)
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<Double-1>", self._activate)
        self.bind("<Button-3>", self._context)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda _e: self.scroll("scroll", -1, "units"))
        self.bind("<Button-5>", lambda _e: self.scroll("scroll", 1, "units"))
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: owner._set_hover(""))

    def redraw(self):
        if self._job is None and self.winfo_exists():
            self._job = self.after_idle(self._draw)

    def scroll(self, *args):
        self.owner._tree.yview(*args)
        self.redraw()

    def _wheel(self, event):
        self.scroll("scroll", -int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
        return "break"

    def _first(self):
        items = self.owner._page_items()
        return min(max(0, len(items) - 1), round(self.owner._tree.yview()[0] * len(items)))

    def row_at(self, y):
        if y < self.HEADER:
            return None
        items = self.owner._page_items()
        index = self._first() + int((y - self.HEADER) / self.owner._row_height())
        return items[index] if 0 <= index < len(items) else None

    def _click(self, event):
        owner = self.owner
        owner._tree.focus_set()
        items = owner._page_items()
        if event.y < self.HEADER:
            if event.x < 32:
                if items and all(item.key in owner._selected for item in items):
                    owner._tree.selection_remove(*owner._tree.selection())
                    owner._selected.difference_update(item.key for item in items)
                    owner._on_tree_select()
                else:
                    owner.select_all_visible()
            else:
                owner._on_sort_clicked("rating" if event.x > self.winfo_width() - 132 else "name")
            self.redraw()
            return "break"
        item = self.row_at(event.y)
        if item is None:
            return "break"
        iid = owner._name_iid[item.key]
        toggle = event.x < 32 or event.state & 0x4
        if event.state & 0x1 and self._anchor in owner._name_iid:
            keys = [row.key for row in items]
            start, end = sorted((keys.index(self._anchor), keys.index(item.key)))
            owner._tree.selection_add([owner._name_iid[key] for key in keys[start:end + 1]])
        elif toggle:
            if iid in owner._tree.selection():
                owner._tree.selection_remove(iid)
            else:
                owner._tree.selection_add(iid)
            self._anchor = item.key
        else:
            owner._selected.clear()
            owner._tree.selection_set(iid)
            self._anchor = item.key
        owner._tree.focus(iid)
        owner._on_tree_select()
        self.redraw()
        return "break"

    def _activate(self, event):
        if event.x < 32:
            return self._click(event)
        item = self.row_at(event.y)
        if item and self.owner._on_activate:
            self.owner._on_activate(item.key)
        return "break"

    def _context(self, event):
        item = self.row_at(event.y)
        if item is None:
            return "break"
        if item.key not in self.owner._selected:
            self.owner.select(item.key)
        menu = self.owner._build_menu(item.key)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _motion(self, event):
        item = self.row_at(event.y)
        self.owner._set_hover(self.owner._name_iid.get(item.key, "") if item else "")

    def _box(self, x, y, selected, tag):
        fill = color("ACCENT") if selected else color("BG_CARD")
        edge = color("ACCENT") if selected else color("BORDER_STRONG")
        super().create_polygon(x + 2, y, x + 14, y, x + 16, y, x + 16, y + 2,
                            x + 16, y + 14, x + 16, y + 16, x + 14, y + 16,
                            x + 2, y + 16, x, y + 16, x, y + 14, x, y + 2, x, y,
                            smooth=True, splinesteps=10, fill=fill, outline=edge, tags=(tag,))
        if selected:
            super().create_line(x + 3, y + 8, x + 7, y + 12, x + 13, y + 4,
                             fill=color("ACCENT_TEXT"), width=2, capstyle="round", tags=(tag,))

    def _fit(self, text, width):
        if self._font.measure(text) <= width:
            return text
        while text and self._font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…" if text else ""

    def _draw(self):
        self._job = None
        if not self.winfo_exists():
            return
        owner = self.owner
        self.configure(bg=color("BG_CARD"))
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        rating_x, state_x = width - 125, width - 47
        super().create_rectangle(0, 0, width, self.HEADER, fill=color("BG_INPUT"), outline="")
        items = owner._page_items()
        self._box(6, 9, bool(items) and all(item.key in owner._selected for item in items), "select-page")
        for x, label, anchor in ((43, "#", "center"), (66, tr("songlist.col.song"), "w"),
                                 (rating_x + 20, tr("songlist.col.rating"), "center"),
                                 (state_x, tr("layout.size" if owner._result_column else "songlist.col.status"), "center")):
            super().create_text(x, 17, text=label, anchor=anchor,
                             font=(owner._font_family, 9, "bold"), fill=color("TEXT_SECONDARY"))
        first = self._first()
        count = math.ceil(max(0, height - self.HEADER) / owner._row_height())
        for offset, item in enumerate(items[first:first + count]):
            y = self.HEADER + offset * owner._row_height()
            selected = item.key in owner._selected
            iid = owner._name_iid.get(item.key)
            bg = color("ROW_SELECTED") if selected else color("BG_HOVER") if iid == owner._hover_iid else color("BG_CARD")
            super().create_rectangle(0, y, width, y + owner._row_height(), fill=bg, outline="")
            super().create_line(0, y + owner._row_height(), width, y + owner._row_height(), fill=color("ROW_DIVIDER"))
            self._box(6, y + 18, selected, "checkbox:" + item.key)
            super().create_text(43, y + 27, text=str(owner._page * owner._page_size + first + offset + 1),
                             font=(owner._font_family, 8), fill=color("TEXT_MUTED"), tags=("number:" + item.key,))
            image = owner._thumb_cache.get(item.cover_path)
            if image:
                super().create_image(62, y + 8, image=image, anchor="nw")
            else:
                super().create_rectangle(62, y + 8, 98, y + 44, fill=color("BG_INPUT"), outline="")
                super().create_text(80, y + 26, text="♪", fill=color("TEXT_MUTED"))
            title_width = max(12, rating_x - 110)
            super().create_text(106, y + 18, text=self._fit(owner._title_of(item), title_width),
                             anchor="w", font=(owner._font_family, 10, "bold"), fill=color("TEXT_PRIMARY"))
            super().create_text(106, y + 37, text=self._fit(item.author, title_width),
                             anchor="w", font=(owner._font_family, 9), fill=color("TEXT_MUTED"))
            rating = owner._rating_of(item.key)
            for star in range(5):
                super().create_text(rating_x + star * 11, y + 27, text="★" if star < rating else "☆",
                                 font=("Segoe UI Symbol", 11), fill=color("STAR") if star < rating else color("TEXT_MUTED"))
            state = owner._presence_text(item.key)
            if owner._result_column:
                size = owner._row_sizes.get(item.key)
                label = "—" if size is None else f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.1f} KB"
                super().create_text(state_x, y + 27, text=label, font=(owner._font_family, 8), fill=color("TEXT_MUTED"))
            elif owner._presence_missing is not None:
                missing = item.key in owner._presence_missing
                super().create_rectangle(state_x - 24, y + 18, state_x + 24, y + 36,
                                      fill=color("WARNING_SOFT" if missing else "SUCCESS_SOFT"), outline="")
                super().create_text(state_x, y + 27, text=state, font=(owner._font_family, 8),
                                 fill=color("WARNING_TEXT" if missing else "SUCCESS_TEXT"))

    def destroy(self):
        if self._job is not None:
            self.after_cancel(self._job)
        super().destroy()
