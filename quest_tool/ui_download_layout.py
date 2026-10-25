"""Download-center presentation only. All callbacks and images stay on Tk."""
from __future__ import annotations

import customtkinter as ctk

from . import audio
from .messages import tr
from .song_search import ORDERS
from .search_filters import MODES
from .ui_download_gallery import DownloadGallery
from .theme import DOWNLOAD, FONT, SPACE, T
from .ui_icons import icon_image
from .ui_preview import RoundPlayButton


class SearchLayout:
    def build_layout(self, action_class, header_host, footer_host, player_host):
        self.grid_columnconfigure(0, weight=1)
        offset = 0
        if header_host is None:
            header_host = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
            header_host.grid(row=0, column=0, sticky="ew")
            offset = 1
        bar = ctk.CTkFrame(header_host, fg_color=T["BG_INPUT"], border_width=1,
                            border_color=T["BORDER"])
        bar.pack(fill="x", expand=True)
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bar, text="", image=icon_image("search", T["TEXT_MUTED"], master=bar), width=28).grid(row=0, column=0, padx=SPACE.S)
        self.entry = ctk.CTkEntry(bar, placeholder_text=tr("search.placeholder"), border_width=0,
                                 fg_color=T["BG_INPUT"], text_color=T["TEXT_PRIMARY"])
        self.entry.grid(row=0, column=1, sticky="ew", pady=SPACE.XS)
        self.entry.bind("<Return>", lambda _event: self._search_keyword())
        self.search_button = action_class(bar, text=tr("search.go"), width=80, command=self._search_keyword)
        self.search_button.grid(row=0, column=2, padx=SPACE.S)
        browse = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.browse_bar = browse
        self.category_bar = ctk.CTkFrame(browse, fg_color="transparent", corner_radius=0)
        self.category_bar.grid(row=0, column=0, sticky="ew", padx=(0, SPACE.S))
        browse.grid_columnconfigure(0, weight=1)
        browse.grid(row=offset, column=0, sticky="ew", pady=(SPACE.S, SPACE.XS))
        self.preset_buttons = {}
        for key in MODES:
            button = action_class(self.category_bar, text=tr("browse." + key), width=DOWNLOAD.CATEGORY_W,
                                   command=lambda mode=key: self.choose_mode(mode))
            self.preset_buttons[key] = button
        self.mode_names = {tr("browse." + key): key for key in MODES}
        self.mode_value = ctk.StringVar(master=self, value=tr("browse.Search"))
        self.filter_button = action_class(browse, text=tr("filter.title"), icon="settings",
                                          width=85, command=self.open_filters)
        self.filter_button.grid(row=0, column=2, sticky="e")
        self.view_menu = ctk.CTkSegmentedButton(browse, values=[tr("layout.covers"), tr("layout.list")],
                                               command=self.set_view)
        self.view_menu.set(tr("layout.covers"))
        self.view_menu.grid(row=0, column=1, sticky="e", padx=SPACE.S)
        heading = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        heading.grid(row=offset+1, column=0, sticky="ew", pady=(SPACE.XS, SPACE.M))
        self.grid_controls = ctk.CTkFrame(heading, fg_color="transparent", corner_radius=0)
        self.grid_controls.pack(side="right", padx=SPACE.S)
        ctk.CTkLabel(self.grid_controls, text=tr("layout.columns")).pack(side="left")
        self.columns_menu = ctk.CTkOptionMenu(self.grid_controls, width=58, values=[str(n) for n in range(1,7)], command=self._grid_changed)
        self.columns_menu.set(str(self.grid_columns)); self.columns_menu.pack(side="left", padx=SPACE.XS)
        ctk.CTkLabel(self.grid_controls, text=tr("layout.rows")).pack(side="left")
        self.rows_menu = ctk.CTkOptionMenu(self.grid_controls, width=58, values=[str(n) for n in range(1,16)], command=self._grid_changed)
        self.rows_menu.set(str(self.grid_rows)); self.rows_menu.pack(side="left", padx=SPACE.XS)
        self.result_title = ctk.CTkLabel(heading, text=tr("search.tab"),
                                         font=(self.font_family, FONT.TITLE, "bold"))
        self.result_title.pack(side="left")
        self.page_select = action_class(heading, text=tr("search.select_page"), width=90,
                                        command=self.select_page)
        self.page_select.pack(side="left", padx=SPACE.L)
        self.order_names = {tr("search.order." + key): key for key in ORDERS}
        self.sort = ctk.CTkOptionMenu(heading, values=list(self.order_names), width=115,
                                     command=lambda _value: self._sort_changed())
        self.sort.pack(side="right")
        body = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.results_body = body
        body.grid(row=offset+2, column=0, sticky="nsew")
        self.grid_rowconfigure(offset+2, weight=1)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        gallery_host = ctk.CTkFrame(body, fg_color=T["BG_APP"])
        gallery_host.grid(row=0, column=0, sticky="nsew", padx=(0, SPACE.L))
        gallery_host.grid_columnconfigure(0, weight=1)
        gallery_host.grid_rowconfigure(0, weight=1)
        self.list = DownloadGallery(gallery_host, self)
        self.list.grid(row=0, column=0, sticky="nsew")
        scrollbar = ctk.CTkScrollbar(gallery_host, command=self.list.scroll)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.list.configure(yscrollcommand=scrollbar.set)
        detail = ctk.CTkFrame(body, width=DOWNLOAD.DETAIL_W, fg_color=T["BG_CARD"], border_width=1,
                              border_color=T["BORDER"])
        self.detail_panel = detail
        detail.grid(row=0, column=1, sticky="ns")
        detail.grid_columnconfigure(0, weight=1)
        detail.grid_rowconfigure(3, weight=1)
        self.cover = ctk.CTkLabel(detail, text=tr("media.no_cover"), image=self._placeholder,
                                  width=180, height=180)
        self.cover.grid(row=0, column=0, padx=SPACE.L, pady=SPACE.L)
        self.song_title = ctk.CTkLabel(detail, text=tr("search.choose"), width=220, wraplength=220,
                                       justify="left", anchor="w", font=(self.font_family, FONT.TITLE, "bold"))
        self.song_title.grid(row=1, column=0, sticky="ew", padx=SPACE.L)
        self.song_artist = ctk.CTkLabel(detail, text="", width=220, wraplength=220,
                                        anchor="w", text_color=T["TEXT_SECONDARY"])
        self.song_artist.grid(row=2, column=0, sticky="ew", padx=SPACE.L, pady=SPACE.XS)
        self.info = ctk.CTkTextbox(detail, width=DOWNLOAD.DETAIL_W-SPACE.XL, height=100, activate_scrollbars=False, fg_color=T["BG_CARD"],
                                   text_color=T["TEXT_PRIMARY"], wrap="word", font=(self.font_family, FONT.SMALL))
        self.info.grid(row=3, column=0, sticky="nsew", padx=SPACE.S)
        self._info(tr("search.choose"))
        self.download_one = action_class(detail, text=tr("layout.download_one"), primary=True,
                                          icon="import", command=self.download_current)
        self.download_one.grid(row=4, column=0, sticky="ew", padx=SPACE.L, pady=SPACE.L)
        self.download_one.configure(state="disabled")
        nav = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        nav.grid(row=offset+3, column=0, sticky="ew", pady=SPACE.S)
        self.pagination_bar = nav
        nav.grid_columnconfigure(0, weight=1, uniform="page_sides")
        nav.grid_columnconfigure(4, weight=1, uniform="page_sides")
        self.previous = action_class(nav, text=tr("search.previous"), width=74,
                                     command=lambda: self.change_page(-1))
        self.previous.grid(row=0, column=1)
        self.page_label = ctk.CTkLabel(nav, text=tr("search.page", page=1), width=80)
        self.page_label.grid(row=0, column=2)
        self.next = action_class(nav, text=tr("search.next"), width=74,
                                 command=lambda: self.change_page(1))
        self.next.grid(row=0, column=3)
        self.status = ctk.CTkLabel(nav, text=tr("search.ready"), anchor="w", wraplength=180,
                                   font=(self.font_family, FONT.SMALL), text_color=T["TEXT_SECONDARY"])
        self.status.grid(row=0, column=0, sticky="w", padx=SPACE.XS)
        if footer_host is None:
            footer_host = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
            footer_host.grid(row=offset+4, column=0, sticky="ew")
        self.selection_summary = ctk.CTkLabel(footer_host, text="", width=100, wraplength=100, font=(self.font_family, FONT.SMALL))
        self.selection_summary.pack(side="left", padx=SPACE.M)
        action_class(footer_host, text=tr("search.clear"), width=85, command=self.clear_selection).pack(side="left", padx=SPACE.S)
        self.download_button = action_class(footer_host, text=tr("search.download", count=0), primary=True,
                                            icon="import", width=155, command=self.download)
        self.download_button.pack(side="left")
        if player_host is None:
            player_host = ctk.CTkFrame(self, fg_color=T["BG_CARD"])
            player_host.grid(row=offset+5, column=0, sticky="ew")
        self.build_player(player_host)

    def build_player(self, host):
        host.grid_columnconfigure(2, weight=1)
        now = ctk.CTkFrame(host, fg_color="transparent", width=DOWNLOAD.PLAYER_NAME_W, corner_radius=0)
        now.grid(row=0, column=0, padx=SPACE.L, pady=SPACE.S)
        self.player_cover = ctk.CTkLabel(now, text="", image=self._placeholder, width=44, height=44)
        # Keep the image receiver for playback state without consuming toolbar space.
        self.player_name = ctk.CTkLabel(now, text=tr("media.preview"), width=DOWNLOAD.PLAYER_NAME_W,
                                        wraplength=DOWNLOAD.PLAYER_NAME_W, height=DOWNLOAD.CONTROL,
                                        anchor="center", justify="center", font=(self.font_family, FONT.SMALL))
        self.player_name.pack(side="left")
        self.play_button = RoundPlayButton(host, command=self.toggle_preview)
        self.play_button.grid(row=0, column=1, padx=SPACE.S)
        self.play_button.configure(state="disabled")
        self.progress = ctk.CTkSlider(host, from_=0, to=1, width=80, command=self.seek_preview, state="disabled")
        self.progress.grid(row=0, column=2, sticky="ew", padx=SPACE.M)
        self.progress.set(0)
        self.preview_status = ctk.CTkLabel(host, text=tr("media.preview"), width=90,
                                          font=(self.font_family, FONT.SMALL))
        self.preview_status.grid(row=0, column=3, padx=SPACE.S)
        ctk.CTkLabel(host, text="", image=icon_image("volume", T["TEXT_SECONDARY"], master=host)).grid(row=0, column=4, padx=SPACE.XS)
        self.volume = ctk.CTkSlider(host, from_=0, to=1, width=100,
                                   command=lambda value: audio.player().set_volume(value))
        self.volume.set(audio.player().volume())
        self.volume.grid(row=0, column=5, padx=SPACE.L)

    def _draw_songs(self):
        self.checks.clear()
        for item in self.rows:
            self.checks[item["id"]] = ctk.BooleanVar(master=self, value=item["id"] in self.selected)
        self.list.reset()
