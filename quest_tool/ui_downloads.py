"""Non-modal download center. All widgets and timers live on the Tk thread."""
from __future__ import annotations

import os
from pathlib import Path
from tkinter import filedialog, ttk

import customtkinter as ctk

from .constants import resolve_font
from .downloads import DownloadError, DownloadManager, parse_map_ids
from .messages import tr
from .theme import DOWNLOAD, FONT, SPACE, T, color
from .ui_icons import IconButton, icon_image
from .ui_search import SearchPane


class DownloadAction(IconButton):
    def __init__(self, master, primary=False, **kwargs):
        super().__init__(master, fg_color=T["ACCENT"] if primary else T["BG_CARD"],
                         text_color=T["ACCENT_TEXT"] if primary else T["TEXT_PRIMARY"],
                         hover_color=T["ACCENT_HOVER"] if primary else T["BG_HOVER"],
                         border_width=0 if primary else 1, border_color=T["BORDER"],
                         height=32, **kwargs)


class DownloadTabs(ctk.CTkTabview):
    _outer_spacing = DOWNLOAD.TAB_GAP
    _outer_button_overhang = 0
    _button_height = DOWNLOAD.CONTROL

    def _grid_forget_all_tabs(self, exclude_name=None):
        # CTk schedules this 100 ms after switching. A superseded callback
        # must not hide the tab that the user has since switched back to.
        if exclude_name is not None and exclude_name != self.get():
            return
        super()._grid_forget_all_tabs(exclude_name)


class DownloadWindow(ctk.CTkToplevel):
    def __init__(self, master, manager: DownloadManager, initial_path: str, preferences=None, on_grid=None):
        super().__init__(master)
        self.configure(fg_color=T["BG_APP"])
        self.manager = manager
        self.title(tr("dl.title"))
        if master.winfo_toplevel().state() != "withdrawn":
            self.transient(master.winfo_toplevel())
        self.geometry(f"{DOWNLOAD.WINDOW_W}x{DOWNLOAD.WINDOW_H}")
        self.minsize(1000, 760)
        self._last_detail = None
        self._job = None
        self._closed = False
        self._last_selection = None
        self.font_family = resolve_font(self)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.target = ctk.StringVar(master=self, value=manager.last_target or initial_path)
        self.keep_zip = ctk.BooleanVar(master=self, value=manager.last_keep_zip)
        header = ctk.CTkFrame(self, fg_color=T["BG_CARD"], corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_columnconfigure(1, weight=1)
        brand = ctk.CTkFrame(header, fg_color="transparent", corner_radius=0)
        brand.grid(row=0, column=0, padx=SPACE.XXL, pady=SPACE.S)
        ctk.CTkLabel(brand, text="", image=icon_image("import", T["ACCENT"], 26, master=brand)).pack(side="left", padx=(0, SPACE.M))
        ctk.CTkLabel(brand, text=tr("dl.title"), font=(self.font_family, FONT.HEADLINE, "bold")).pack(side="left")
        self.search_header = ctk.CTkFrame(header, fg_color="transparent", corner_radius=0)
        self.search_header.grid(row=0, column=1, sticky="ew", padx=(SPACE.L, SPACE.XL), pady=SPACE.S)
        self.tabs = DownloadTabs(self, fg_color=T["BG_CARD"], anchor="w", corner_radius=0)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=SPACE.L)
        search_tab = self.tabs.add(tr("search.tab"))
        table = self.tabs.add(tr("search.tasks"))
        import_tab = self.tabs.add(tr("layout.import"))
        self.import_tab = import_tab
        footer = ctk.CTkFrame(self, fg_color=T["BG_CARD"], corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew", pady=(SPACE.S, 0))
        footer.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(footer, text="", image=icon_image("folder", T["TEXT_MUTED"], master=footer)).grid(row=0, column=0, padx=SPACE.L)
        self.target_entry = ctk.CTkEntry(footer, width=90, textvariable=self.target, fg_color=T["BG_INPUT"], border_color=T["BORDER"], text_color=T["TEXT_PRIMARY"])
        self.target_entry.grid(row=0, column=1, sticky="ew", pady=SPACE.M)
        DownloadAction(footer, text=tr("layout.change_path"), icon="folder", width=90, command=self._browse).grid(row=0, column=2, padx=SPACE.S)
        self.bulk_host = ctk.CTkFrame(footer, fg_color="transparent")
        self.bulk_host.grid(row=0, column=3, padx=SPACE.L)
        player = ctk.CTkFrame(self, fg_color=T["BG_CARD"], corner_radius=0)
        player.grid(row=3, column=0, sticky="ew", pady=(SPACE.XXS, 0))
        self.search_pane = SearchPane(search_tab, self.font_family, DownloadAction, self._search_download,
                                      header_host=self.search_header, footer_host=self.bulk_host,
                                      player_host=player, on_search=self._show_discovery,
                                      grid_columns=preferences[0] if preferences else 3,
                                      grid_rows=preferences[1] if preferences else 7, on_grid=on_grid)
        self.search_pane.pack(fill="both", expand=True, padx=SPACE.L, pady=(SPACE.S, 0))
        self._build_import(import_tab)
        ctk.CTkLabel(table, text=tr("search.tasks"), font=(self.font_family, FONT.TITLE, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=SPACE.L, pady=SPACE.M)
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(1, weight=1)
        style = ttk.Style(self)
        style.configure("Downloads.Treeview", background=color("BG_CARD"), foreground=color("TEXT_PRIMARY"),
                        fieldbackground=color("BG_CARD"), rowheight=34, font=(self.font_family, FONT.SMALL))
        style.configure("Downloads.Treeview.Heading", font=(self.font_family, FONT.SMALL))
        self.tree = ttk.Treeview(table, columns=("song", "mapper", "state", "progress"),
                                 show="headings", selectmode="extended", style="Downloads.Treeview")
        for key, width in (("song", 360), ("mapper", 160), ("state", 130), ("progress", 90)):
            self.tree.heading(key, text=tr("dl.column." + key))
            self.tree.column(key, width=width, minwidth=60, stretch=key == "song")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(SPACE.L, 0))
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._selection())
        task_actions = ctk.CTkFrame(table, fg_color="transparent")
        task_actions.grid(row=2, column=0, columnspan=2, sticky="ew", padx=SPACE.L, pady=SPACE.S)
        DownloadAction(task_actions, text=tr("dl.cancel"), command=self.cancel_selected, width=110).pack(side="left")
        DownloadAction(task_actions, text=tr("dl.retry"), command=self.retry_selected, width=110).pack(side="left", padx=SPACE.S)
        DownloadAction(task_actions, text=tr("dl.open"), icon="folder", command=self.open_folder, width=110).pack(side="left")
        DownloadAction(task_actions, text=tr("dl.clear"), command=self._clear, width=110).pack(side="left", padx=SPACE.S)
        self.summary = ctk.CTkLabel(task_actions, text="", anchor="e")
        self.summary.pack(side="right")
        self.detail = ctk.CTkTextbox(table, height=80, fg_color=T["BG_CARD"], text_color=T["TEXT_PRIMARY"], font=(self.font_family, FONT.SMALL))
        self.detail.grid(row=3, column=0, columnspan=2, sticky="ew", padx=SPACE.L, pady=(0, SPACE.L))
        self.status_line = ctk.CTkLabel(self, text="", anchor="w", font=(self.font_family, FONT.SMALL), text_color=T["TEXT_MUTED"], wraplength=950)
        self.status_line.grid(row=4, column=0, sticky="ew", padx=SPACE.L, pady=SPACE.XS)
        self._apply_style()
        self._message(tr("dl.ready"))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._poll()
        self._show_job = self.after(80, self.present)

    def present(self):
        if self._show_job is not None:
            self.after_cancel(self._show_job)
        self._show_job = None
        if not self._closed:
            self.deiconify()
            self.lift()
            self.focus_set()

    def _show_discovery(self):
        self.tabs.set(tr("search.tab"))

    def _build_import(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        form = ctk.CTkFrame(parent, fg_color=T["BG_CARD"])
        form.grid(row=0, column=0, sticky="ew", padx=SPACE.XL, pady=SPACE.XL)
        form.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(form, text=tr("layout.import"), font=(self.font_family, FONT.TITLE, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=SPACE.L, pady=SPACE.L)
        ctk.CTkLabel(form, text=tr("dl.intro"), anchor="w", wraplength=560, justify="left", text_color=T["TEXT_SECONDARY"]).grid(row=1, column=0, sticky="ew", padx=SPACE.L)
        ctk.CTkLabel(form, text=tr("dl.links"), anchor="w").grid(row=2, column=0, sticky="ew", padx=SPACE.L, pady=(SPACE.L, SPACE.S))
        self.input = ctk.CTkTextbox(form, height=150, fg_color=T["BG_INPUT"], text_color=T["TEXT_PRIMARY"], font=(self.font_family, FONT.BODY))
        self.input.grid(row=3, column=0, sticky="ew", padx=SPACE.L)
        ctk.CTkCheckBox(form, text=tr("dl.keep_zip"), variable=self.keep_zip).grid(row=4, column=0, sticky="w", padx=SPACE.L, pady=SPACE.L)
        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=5, column=0, sticky="ew", padx=SPACE.L, pady=SPACE.L)
        self.start_button = DownloadAction(actions, text=tr("dl.start"), primary=True, icon="import", command=self.start)
        self.start_button.pack(side="left", padx=(0, SPACE.S))
        DownloadAction(actions, text=tr("dl.import_zip"), icon="folder", command=self.import_zip).pack(side="left")
        ctk.CTkLabel(form, text=tr("layout.shared_path"), anchor="w", wraplength=560, text_color=T["TEXT_MUTED"]).grid(row=6, column=0, sticky="ew", padx=SPACE.L, pady=(0, SPACE.L))

    def _apply_style(self):
        style = ttk.Style(self)
        style.configure("Downloads.Treeview", background=color("BG_CARD"), foreground=color("TEXT_PRIMARY"),
                        fieldbackground=color("BG_CARD"), borderwidth=0, relief="flat")
        style.map("Downloads.Treeview", background=[("selected", color("BG_SELECTED"))],
                  foreground=[("selected", color("TEXT_PRIMARY"))])
        style.configure("Downloads.Treeview.Heading", background=color("BG_INPUT"),
                        foreground=color("TEXT_SECONDARY"), relief="flat", borderwidth=0,
                        padding=(SPACE.S, SPACE.S), font=(self.font_family, FONT.SMALL, "bold"))
        style.map("Downloads.Treeview.Heading", background=[("active",color("BG_HOVER"))])

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "tree"):
            self._apply_style()

    def _message(self, text):
        if text == self._last_detail:
            return
        self._last_detail = text
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")
        if hasattr(self, "status_line"):
            self.status_line.configure(text=text.splitlines()[0] if text else "")

    def _browse(self):
        folder = filedialog.askdirectory(parent=self, mustexist=True, initialdir=self.target.get() or None)
        if folder:
            self.target.set(folder)

    def _add(self, sources, kind):
        try:
            ids = self.manager.enqueue(sources, self.target.get().strip(), kind=kind,
                                       keep_zip=self.keep_zip.get())
            self._message(tr("dl.added", count=len(ids)))
            self._render()
            if ids:
                self.tree.selection_set(ids[-1])
                self.tree.see(ids[-1])
                self.tabs.set(tr("search.tasks"))
            return ids
        except (DownloadError, OSError) as exc:
            code = exc.code if isinstance(exc, DownloadError) else "unexpected"
            self._message(tr("dl.error." + code) + "\n" + str(exc))
            return []

    def _search_download(self, sources):
        ids = self._add(sources, "online")
        if not ids:
            self.search_pane.status.configure(text=self._last_detail.splitlines()[0])
        return ids

    def start(self):
        try:
            sources = parse_map_ids(self.input.get("1.0", "end"))
        except DownloadError as exc:
            self._message(tr("dl.error." + exc.code) + "\n" + exc.detail)
            return
        self._add(sources, "online")

    def import_zip(self):
        paths = filedialog.askopenfilenames(parent=self, title=tr("dl.import_zip"),
                                            filetypes=[(tr("dl.zip_files"), "*.zip")])
        if paths:
            self._add(list(paths), "zip")

    def cancel_selected(self):
        for job_id in self.tree.selection():
            self.manager.cancel(job_id)

    def retry_selected(self):
        try:
            for job_id in self.tree.selection():
                self.manager.retry(job_id)
        except (DownloadError, OSError) as exc:
            self._message(str(exc))

    def open_folder(self):
        selected = self.tree.selection()
        if not selected:
            return
        data = next((j for j in self.manager.snapshot() if j["id"] == selected[0]), None)
        if data:
            folder = Path(data["installed"] or data["target"])
            if folder.is_dir():
                try:
                    os.startfile(str(folder))
                except OSError as exc:
                    self._message(str(exc))

    def _clear(self):
        try:
            self.manager.clear_finished()
            self._render()
            self._message(tr("dl.ready"))
        except OSError as exc:
            self._message(tr("dl.error.history") + "\n" + str(exc))

    def _selection(self):
        selected = self.tree.selection()
        if not selected:
            return
        data = next((j for j in self.manager.snapshot() if j["id"] == selected[0]), None)
        if data:
            error = tr("dl.error." + data["error"]) if data["error"] else ""
            text = f"{data['title']}\n{data['installed'] or data['target']}\n{error} {data['detail']}"
            if text != self._last_selection:
                self._last_selection = text
                self._message(text)

    def _render(self):
        rows = self.manager.snapshot()
        done = 0
        ids = {job["id"] for job in rows}
        for item in self.tree.get_children():
            if item not in ids:
                self.tree.delete(item)
        for job in rows:
            values = (job["title"], job["mapper"], tr("dl.state." + job["state"]),
                      f"{round(job['progress'] * 100)}%")
            if self.tree.exists(job["id"]):
                if self.tree.item(job["id"], "values") != tuple(str(v) for v in values):
                    self.tree.item(job["id"], values=values)
            else:
                self.tree.insert("", 0, iid=job["id"], values=values)
            done += job["state"] in {"done", "exists"}
        self.summary.configure(text=tr("dl.summary", done=done, total=len(rows)))
        self._selection()

    def _poll(self):
        if not self._closed:
            self.search_pane.poll()
            self._render()
            self._job = self.after(300, self._poll)

    def close(self):
        self._closed = True
        if self._show_job is not None:
            self.after_cancel(self._show_job)
        self.search_pane.close()
        if self._job:
            self.after_cancel(self._job)
        self.destroy()
