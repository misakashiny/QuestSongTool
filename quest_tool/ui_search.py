"""Search UI. All widget calls, selection state and polling stay on the Tk thread."""
from __future__ import annotations

import io
from PIL import Image, ImageOps
import customtkinter as ctk

from . import audio
from .messages import tr
from .song_search import SearchSession
from .search_filters import filter_params
from .search_media import MediaSession
from .theme import DOWNLOAD, SPACE, T
from .ui_icons import _IconImage
from .ui_download_layout import SearchLayout
from .ui_search_filters import SearchFilters


class SearchPane(SearchLayout, ctk.CTkFrame):
    def __init__(self, master, font_family, action_class, on_download, session=None,
                 header_host=None, footer_host=None, player_host=None, on_search=None, grid_columns=3, grid_rows=7, on_grid=None):
        super().__init__(master, fg_color=T["BG_CARD"])
        self.grid_columns, self.grid_rows = grid_columns, grid_rows
        self.on_grid = on_grid
        self._toolbar_columns = 0
        self.font_family = font_family
        self.on_download = on_download
        self.session = session or SearchSession()
        self.media = MediaSession()
        self.filters = None
        self.filter_window = None
        self.mode = "Search"
        self._signature = None
        self.current_item = None
        self._covers = {}
        self._images = {}
        self._thumb_labels = {}
        self._preview_id = None
        self._preview_generation = 0
        self._preview_pending = False
        self._preview_total = 0
        blank = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        self._placeholder = _IconImage(self, light_image=blank, dark_image=blank, size=(1, 1))
        self.rows = []
        self.selected = {}
        self.checks = {}
        self.page = 0
        self.query = ""
        self.order = "Relevance"
        self.has_next = False
        self.busy = False
        self.generation = 0
        self.view_mode = "grid"
        self._layout_job = None
        self._columns = 0
        self._cell_width = 0
        self.song_views = {}
        self._preview_item = None
        self._player_image = None
        self._player_source = None
        self.on_search = on_search
        self.build_layout(action_class, header_host, footer_host, player_host)
        self.bind("<Configure>", self._schedule_layout, add="+")
        self.after_idle(self._schedule_layout)
        self._buttons()

    def _info(self, text):
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", text)
        self.info.configure(state="disabled")

    def show_info(self, item):
        self.current_item = item
        self._set_cover(self.cover, item.get("coverURL", ""), 180)
        self.song_title.configure(text=item["title"])
        self.song_artist.configure(text=item["artist"])
        self.download_one.configure(state="normal")
        self.play_button.configure(state="normal" if (self._preview_item or item).get("previewURL") else "disabled")
        self._highlight_current()
        unknown = tr("search.unknown")
        duration = item["duration"]
        duration = f"{int(duration)//60}:{int(duration)%60:02}" if duration is not None else unknown
        rating = f"{item['rating'] * 100:.1f}%" if item["rating"] is not None else unknown
        diffs = "\n".join(f"{d['characteristic']} · {d['difficulty']}" +
                           (f" · {d['nps']:g} NPS" if d['nps'] is not None else "") for d in item["diffs"]) or unknown
        self._info(tr("search.details_compact", title=item["title"], artist=item["artist"] or unknown,
                      mapper=item["mapper"] or unknown, map_id=item["id"], bpm=item["bpm"] if item["bpm"] is not None else unknown,
                      duration=duration, rating=rating, published=item["published"][:10] or unknown,
                      requirements=", ".join(item["requirements"]) or tr("search.no_requirements"),
                      diffs=diffs, description=item["description"]))

    def search(self):
        if self.on_search:
            self.on_search()
        query = self.entry.get().strip()
        mode = self.mode_names[self.mode_value.get()]
        if (not query and mode == "Search") or len(query) > 200 or any(ord(ch) < 32 for ch in query):
            self.status.configure(text=tr("dl.error.query"))
            return
        if mode != "Search":
            preset = {"Curated": "Curated", "Latest": "Latest", "Verified": "Latest", "Random": "Random"}.get(mode, "Rating")
            self.sort.set(tr("search.order." + preset))
        order = self.order_names[self.sort.get()]
        signature = repr((query, order, mode, self.filters))
        if signature != self._signature:
            self.clear_selection()
        self._signature = signature
        self.query, self.order, self.mode = query, order, mode
        self._request(0)

    def _request(self, page):
        self.current_item = None
        self.cover.configure(image=self._placeholder, text=tr("media.no_cover"))
        self.song_title.configure(text=tr("search.loading"))
        self.song_artist.configure(text="")
        self.download_one.configure(state="disabled")
        self._covers.clear()
        self._images.clear()
        self.busy = True
        self.rows = []
        self._render()
        self._info(tr("search.choose"))
        self.status.configure(text=tr("search.loading"))
        self.generation = self.session.request(self.query, page, self.order, filters=self.filters, mode=self.mode, page_size=self.grid_columns*self.grid_rows)
        self.media.covers.request([], self.generation, "")
        self._buttons()

    def change_page(self, delta):
        if not self.busy and (self.query or self.mode != "Search") and ((delta < 0 and self.page > 0) or (delta > 0 and self.has_next)):
            self._request(self.page + delta)

    def poll(self):
        for message in self.session.tasks.drain():
            data = message.payload
            if not isinstance(data, dict) or data.get("generation") != self.generation:
                continue
            self.busy = False
            if message.kind == "search":
                result = data["result"]
                self.rows, self.page, self.has_next = result["rows"], result["page"], result["has_next"]
                self._render()
                self.status.configure(text=tr("search.results", count=len(self.rows), selected=len(self.selected))
                                      if self.rows else tr("search.empty"))
                if self.rows:
                    self.show_info(self.rows[0])
                    self.media.covers.request(self.rows, self.generation, "")
            elif message.kind == "search_error":
                self.has_next = False
                self.status.configure(text=tr("dl.error." + data["code"]))
            self._buttons()
        self._poll_media()

    def _render(self):
        self._draw_songs()

    def toggle(self, item):
        if self.checks[item["id"]].get():
            if len(self.selected) >= 200 and item["id"] not in self.selected:
                self.checks[item["id"]].set(False)
                self.status.configure(text=tr("search.limit"))
                return
            self.selected[item["id"]] = item
        else:
            self.selected.pop(item["id"], None)
        self.status.configure(text=tr("search.results", count=len(self.rows), selected=len(self.selected)))
        self._buttons()
        self.list.redraw()

    def select_page(self):
        for item in self.rows:
            if len(self.selected) < 200:
                self.selected[item["id"]] = item
            self.checks[item["id"]].set(item["id"] in self.selected)
        self.status.configure(text=tr("search.results", count=len(self.rows), selected=len(self.selected)))
        self._buttons()

    def clear_selection(self):
        self.selected.clear()
        for checked in self.checks.values():
            checked.set(False)
        self.status.configure(text=tr("search.results", count=len(self.rows), selected=0))
        self._buttons()

    def download(self):
        if self.selected and self.on_download(list(self.selected)):
            self.clear_selection()

    def _buttons(self):
        self.sort.configure(state="normal" if self.mode == "Search" else "disabled")
        if self.mode == "Search" and not self.sort.winfo_manager():
            self.sort.pack(side="right")
        elif self.mode != "Search" and self.sort.winfo_manager():
            self.sort.pack_forget()
        self.previous.configure(state="normal" if not self.busy and self.page > 0 else "disabled")
        self.next.configure(state="normal" if not self.busy and self.has_next else "disabled")
        self.page_label.configure(text=tr("search.page", page=self.page + 1))
        self.download_button.configure(text=tr("search.download", count=len(self.selected)),
                                       state="normal" if self.selected else "disabled")
        self.selection_summary.configure(text=tr("layout.selected", count=len(self.selected)))
        self.result_title.configure(text=tr("browse." + self.mode) if self.mode != "Search" else tr("search.tab"))
        self.list.redraw()
        for key, button in self.preset_buttons.items():
            button.configure(border_color=T["ACCENT"] if key == self.mode else T["BORDER"],
                              text_color=T["ACCENT"] if key == self.mode else T["TEXT_SECONDARY"])

    def close(self):
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        self.stop_preview()
        self.media.close()
        if self.filter_window is not None and self.filter_window.winfo_exists():
            self.filter_window.destroy()
        self.session.close()

    def _mode_changed(self, _label):
        self.entry.delete(0, "end")
        self.search()

    def open_filters(self):
        if self.filter_window is not None and self.filter_window.winfo_exists():
            self.filter_window.lift()
            return
        self.filter_window = SearchFilters(self, self.filters, self.apply_filters)

    def apply_filters(self, values):
        self.filters = values
        count = len(filter_params(values))
        self.filter_button.configure(text=tr("filter.active", count=count))
        if not self.entry.get().strip() and self.mode_value.get() == tr("browse.Search"):
            self.mode_value.set(tr("browse.Latest"))
        self.search()

    def _set_cover(self, label, url, size):
        if url not in self._covers:
            label.configure(image=self._placeholder, text=tr("media.no_cover"))
            return
        key = (url, size)
        if key not in self._images:
            image = self._covers[url]
            dimensions = size if isinstance(size, tuple) else (size, size)
            image = ImageOps.fit(image, dimensions)
            self._images[key] = _IconImage(self, light_image=image, dark_image=image, size=dimensions)
        label.configure(image=self._images[key], text="")

    def preview_song(self, item):
        same = self._preview_item is not None and self._preview_item["id"] == item["id"]
        self.show_info(item)
        if same and (self._preview_pending or (self._preview_id is not None and audio.player().request_id == self._preview_id and audio.player().is_playing())):
            self.stop_preview()
        else:
            self._start_preview(0, item)

    def toggle_preview(self):
        item = self._preview_item or self.current_item
        if item is not None:
            self.preview_song(item)

    def stop_preview(self):
        self.media.preview.cancel()
        player = audio.player()
        if self._preview_id is not None and player.request_id == self._preview_id:
            player.stop()
        self._preview_id = None
        self._preview_pending = False
        self._preview_total = 0
        if hasattr(self, "play_button"):
            self.play_button.configure(text="▶")
            self.preview_status.configure(text=tr("media.preview"))
            self.progress.configure(state="disabled")
            self.progress.set(0)

    def _start_preview(self, start, item=None):
        item = item or self._preview_item or self.current_item
        if not item or not item.get("previewURL"):
            self.preview_status.configure(text=tr("media.missing"))
            return
        player = audio.player()
        player.stop()
        self._preview_item = dict(item)
        self.player_name.configure(text=f"{item['title']}\n{item['artist']}")
        self._update_player_cover()
        self._preview_id = player.request_id
        self._preview_pending = True
        self.play_button.configure(text="■")
        self.preview_status.configure(text=tr("media.loading"))
        self._preview_generation = self.media.preview.request(item, start, self._preview_id)

    def seek_preview(self, value):
        if self._preview_total > 0 and not self._preview_pending:
            self._start_preview(float(value) * min(self._preview_total, 30))

    def _poll_media(self):
        for message in self.media.covers.tasks.drain():
            data = message.payload
            if message.kind != "cover" or data["generation"] != self.generation or not data["data"]:
                continue
            url = data["url"]
            self._covers[url] = Image.open(io.BytesIO(data["data"])).copy()
            self.list.redraw()
            if self.current_item and self.current_item.get("coverURL") == url:
                self._set_cover(self.cover, url, 180)
            if self._preview_item and self._preview_item.get("coverURL") == url:
                self._update_player_cover()
        player = audio.player()
        for message in self.media.preview.tasks.drain():
            data = message.payload
            if not isinstance(data, dict) or data.get("generation") != self._preview_generation or self._preview_id != player.request_id:
                continue
            self._preview_pending = False
            if message.kind == "search_error":
                self.stop_preview()
                self.preview_status.configure(text=tr("media.failed"))
                self.status.configure(text=tr("dl.error." + data["code"]))
            elif message.kind == "search":
                self._preview_total = data["result"]["total"]
                self.progress.configure(state="normal")
        if self._preview_id is not None and self._preview_id != player.request_id:
            self.stop_preview()
        if self._preview_id is not None and not self._preview_pending:
            if player.is_playing():
                span = min(30, self._preview_total) or 30
                position = min(span, player.position())
                self.progress.set(position / span)
                self.preview_status.configure(text=f"{int(position)//60}:{int(position)%60:02} / {int(span)//60}:{int(span)%60:02}")
            else:
                self.stop_preview()

    def _update_player_cover(self):
        url = (self._preview_item or {}).get("coverURL", "")
        if url in self._covers:
            self._player_source = self._covers[url]
            image = ImageOps.fit(self._player_source, (44, 44))
            self._player_image = _IconImage(self, light_image=image, dark_image=image, size=(44, 44))
            self.player_cover.configure(image=self._player_image)
        else:
            self.player_cover.configure(image=self._placeholder)
            self._player_image = None

    def _search_keyword(self):
        if self.entry.get().strip():
            self.mode_value.set(tr("browse.Search"))
        self.search()

    def choose_mode(self, mode):
        self.mode_value.set(tr("browse." + mode))
        self._mode_changed(None)

    def _sort_changed(self):
        if self.query:
            self.search()

    def download_current(self):
        if self.current_item:
            self.on_download([self.current_item["id"]])

    @staticmethod
    def _duration_text(item):
        value = item.get("duration")
        return f"{int(value)//60}:{int(value)%60:02}" if value is not None else tr("search.unknown")

    def _highlight_current(self):
        self.list.redraw()

    def _schedule_layout(self, _event=None):
        if not self.winfo_ismapped():
            return
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
        self._layout_job = self.after(80, self._reflow)

    def _reflow(self):
        self._layout_job = None
        self._adapt_toolbar()
        self.list.redraw()

    def _adapt_toolbar(self):
        if not self.winfo_ismapped():
            return
        scale = self._get_widget_scaling()
        # Configure/scaling callbacks can still report the pane's old width.
        # The enclosing tab and pane each have SPACE.L padding on both sides.
        width = min(self.winfo_width()/scale,
                    self.winfo_toplevel().winfo_width()/scale - SPACE.L*4)
        available = max(DOWNLOAD.CATEGORY_W, width-DOWNLOAD.TOOLBAR_W)
        button_width = max(DOWNLOAD.CATEGORY_W, max(
            button.winfo_reqwidth()/scale for button in self.preset_buttons.values()))
        columns = max(1, min(len(self.preset_buttons), int(available//(button_width+SPACE.XS))))
        if columns != self._toolbar_columns:
            self._toolbar_columns = columns
            for index, button in enumerate(self.preset_buttons.values()):
                button.grid(row=index//columns, column=index%columns, sticky="ew", padx=SPACE.XXS, pady=SPACE.XXS)
        compact = self.winfo_toplevel().winfo_height()/scale < 700
        if compact and self.cover.winfo_manager():
            self.cover.grid_remove()
        elif not compact and not self.cover.winfo_manager():
            self.cover.grid()

    def _grid_changed(self, _label=None):
        self.grid_columns, self.grid_rows = int(self.columns_menu.get()), int(self.rows_menu.get())
        if self.on_grid:
            self.on_grid(self.grid_columns, self.grid_rows)
        if self.query or self.mode != "Search":
            self._request(0)
        else:
            self._render()

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        if hasattr(self, "preset_buttons"):
            # DPI changes must not leave the previous, wider category grid in place.
            self._adapt_toolbar()

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "list"):
            self.list.redraw()

    def set_view(self, label):
        self.view_mode = "grid" if label == tr("layout.covers") else "list"
        self._render()
