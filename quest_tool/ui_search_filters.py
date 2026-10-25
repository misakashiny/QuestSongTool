"""Website filter drafts, with opt-in form styles and responsive layout."""
from __future__ import annotations

import copy
import tkinter as tk
import customtkinter as ctk

from .downloads import DownloadError
from .messages import tr
from .search_catalog import TAGS, STYLE_TAGS, ENVIRONMENTS, NEW_ENVIRONMENTS
from .search_filters import FLAGS, RANGES, CHARACTERISTICS, filter_params
from .theme import T, SPACE, FONT, FORM, RADIUS
from .ui_form_controls import FormTabs, form_style, keyboard_focus, menu_keyboard


class SearchFilters(ctk.CTkToplevel):
    def __init__(self, master, values, on_apply):
        super().__init__(master)
        self.title(tr("filter.title"))
        self.transient(master.winfo_toplevel())
        scale = self._get_window_scaling()
        available_w = max(460, int(self.winfo_screenwidth() / scale) - 48)
        available_h = max(320, int(self.winfo_screenheight() / scale) - 64)
        self.geometry(f"{min(760, available_w)}x{min(680, available_h)}")
        self.minsize(min(700, available_w), min(560, available_h))
        self.configure(fg_color=T["BG_APP"])
        self.values = copy.deepcopy(values or {})
        self.on_apply = on_apply
        self.tri, self.env, self.modes, self.tags, self.bounds = {}, {}, {}, {}, {}
        self.panels, self.guides = {}, []
        self.choice_rows, self.check_groups, self.metric_labels = [], [], []
        self._layout_job = None
        self._layout_signature = None
        self._panel_breakpoints = {}
        self.layout_passes = 0
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tabs = FormTabs(
            self, fg_color=T["BG_CARD"], corner_radius=RADIUS.PANEL, command=self._schedule_layout,
            border_width=1, border_color=T["BORDER"], anchor="nw",
            segmented_button_fg_color=T["BG_APP"],
            segmented_button_selected_color=T["ACCENT_SOFT"],
            segmented_button_selected_hover_color=T["BG_SELECTED"],
            segmented_button_unselected_color=T["BG_APP"],
            segmented_button_unselected_hover_color=T["BG_HOVER"],
            text_color=T["TEXT_PRIMARY"])
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=SPACE.XL, pady=(SPACE.L, 0))
        for key in ("general", "tags", "environments", "metrics"):
            tab = self.tabs.add(tr("filter." + key))
            panel = ctk.CTkScrollableFrame(
                tab, fg_color=T["BG_CARD"], corner_radius=0,
                scrollbar_button_color=T["BORDER_STRONG"],
                scrollbar_button_hover_color=T["TEXT_SECONDARY"])
            panel.pack(fill="both", expand=True, padx=SPACE.M, pady=SPACE.M)
            panel.grid_columnconfigure(0, weight=1)
            self.panels[key] = panel
            label = ctk.CTkLabel(panel, text=tr("filter.guide." + key), height=0,
                                 font=(FONT.FAMILY, FONT.SMALL), wraplength=620, justify="left",
                                 anchor="w", text_color=T["TEXT_SECONDARY"])
            label.grid(row=0, column=0, sticky="ew", padx=SPACE.M, pady=(SPACE.S, SPACE.XL))
            self.guides.append(label)
        self.options = {tr("filter.any"): None, tr("filter.yes"): True, tr("filter.no"): False}
        general = self.panels["general"]
        self.ai = self._choice(general, 1, tr("filter.ai"), {tr("filter.all"): "all", tr("filter.human"): "human", tr("filter.ai_only"): "ai"}, self.values.get("automapper", "human"))
        self.leaderboard = self._choice(general, 2, tr("filter.leaderboard"), {tr("filter.board."+key): key for key in ("All", "Ranked", "BeatLeader", "ScoreSaber")}, self.values.get("leaderboard", "All"))
        for index, key in enumerate(FLAGS, 3):
            self.tri[key] = self._choice(general, index, tr("filter.explain." + key), self.options, self.values.get(key))
        group = self._check_group(general, len(FLAGS) + 3, tr("filter.modes"))
        for key in CHARACTERISTICS:
            self.modes[key] = self._check(group, tr("filter.mode." + key), key in self.values.get("characteristics", []))
        panel = self.panels["tags"]
        row = 1
        for group_name, keys in (("style", STYLE_TAGS), ("genre", tuple(key for key in TAGS if key not in STYLE_TAGS))):
            self._heading(panel, row, tr("filter." + group_name))
            row += 1
            for key in sorted(keys, key=lambda key: TAGS[key]):
                self.tags[key] = self._choice(panel, row, tr("filter.tag." + key), self.options, self.values.get("tags", {}).get(key))
                row += 1
        panel = self.panels["environments"]
        for row, (group_name, keys) in enumerate((("legacy", [key for key in ENVIRONMENTS if key not in NEW_ENVIRONMENTS]), ("new", NEW_ENVIRONMENTS)), 1):
            group = self._check_group(panel, row, tr("filter." + group_name))
            for key in keys:
                self.env[key] = self._check(group, tr("filter.env." + key), key in self.values.get("environments", []))
        panel = self.panels["metrics"]
        hint = ctk.CTkLabel(panel, text=tr("filter.bounds_hint"), height=0, wraplength=620,
                            font=(FONT.FAMILY, FONT.SMALL), justify="left", anchor="w", text_color=T["TEXT_SECONDARY"])
        hint.grid(row=1, column=0, sticky="ew", padx=SPACE.M, pady=(0, SPACE.L))
        self.guides.append(hint)
        self.metrics = ctk.CTkFrame(panel, fg_color="transparent", corner_radius=0)
        self.metrics.grid(row=2, column=0, sticky="ew", padx=SPACE.M)
        self.metrics.grid_columnconfigure(0, weight=2, minsize=160)
        self.metrics.grid_columnconfigure((1, 2), weight=1, uniform="bounds")
        for column, key in ((1, "min"), (2, "max")):
            ctk.CTkLabel(self.metrics, text=tr("filter." + key), font=(FONT.FAMILY, FONT.SMALL),
                         text_color=T["TEXT_SECONDARY"]).grid(row=0, column=column, sticky="w", padx=SPACE.S)
        for row, name in enumerate(RANGES, 1):
            label = ctk.CTkLabel(self.metrics, text=tr("filter.metric." + name), height=0,
                                 font=(FONT.FAMILY, FONT.BODY), justify="left", anchor="w", wraplength=210,
                                 text_color=T["TEXT_PRIMARY"])
            label.grid(row=row, column=0, sticky="ew", padx=(0, SPACE.M), pady=SPACE.S)
            self.metric_labels.append(label)
            for column, prefix in enumerate(("min", "max"), 1):
                key = prefix + name
                entry = ctk.CTkEntry(self.metrics, width=100, **form_style("entry"))
                entry.insert(0, str(self.values.get(key, "")))
                entry.grid(row=row, column=column, sticky="ew", padx=SPACE.S, pady=SPACE.S)
                keyboard_focus(entry, entry=True)
                self.bounds[key] = entry
        for row, key in enumerate(("from", "to"), len(RANGES) + 3):
            line = ctk.CTkFrame(panel, fg_color="transparent", corner_radius=0)
            line.grid(row=row, column=0, sticky="ew", padx=SPACE.M, pady=SPACE.S)
            line.grid_columnconfigure(0, weight=1)
            label = ctk.CTkLabel(line, text=tr("filter." + key), font=(FONT.FAMILY, FONT.BODY),
                                 anchor="w", text_color=T["TEXT_PRIMARY"])
            label.grid(row=0, column=0, sticky="w")
            entry = ctk.CTkEntry(line, placeholder_text="YYYY-MM-DD", width=FORM.MENU_W, **form_style("entry"))
            entry.insert(0, self.values.get(key, ""))
            entry.grid(row=0, column=1, sticky="ew", padx=(SPACE.L, 0))
            keyboard_focus(entry, entry=True)
            self.bounds[key] = entry
        self.error = ctk.CTkLabel(self, text="", height=0, wraplength=650,
                                  font=(FONT.FAMILY, FONT.SMALL), text_color=T["DANGER"], justify="left")
        self.error.grid(row=1, column=0, sticky="ew", padx=SPACE.XL, pady=SPACE.XS)
        self.footer = ctk.CTkFrame(self, fg_color=T["BG_CARD"], corner_radius=RADIUS.PANEL,
                                   border_width=1, border_color=T["BORDER"])
        self.footer.grid(row=2, column=0, sticky="ew", padx=SPACE.XL, pady=(0, SPACE.L))
        self.footer.grid_columnconfigure(1, weight=1)
        self.reset_button = self._button(tr("filter.reset"), self.reset, "secondary", 0, 112)
        self.cancel_button = self._button(tr("filter.cancel"), self.destroy, "secondary", 2, 100)
        self.apply_button = self._button(tr("filter.apply"), self.apply, "primary", 3, 136)
        # Style public CTk child widgets, without replacing Tabview internals.
        for child in tk.Misc.winfo_children(self.tabs):
            if isinstance(child, ctk.CTkSegmentedButton):
                child.configure(height=FORM.CONTROL_H, font=(FONT.FAMILY, FONT.BODY),
                                corner_radius=RADIUS.CONTROL)
                for button in tk.Misc.winfo_children(child):
                    if isinstance(button, ctk.CTkButton):
                        button.configure(border_width=FORM.FOCUS_WIDTH, border_color=T["BG_APP"])
                        keyboard_focus(button, activate=button.invoke)
        self.bind("<Configure>", self._schedule_layout, add="+")
        self._layout_job = self.after_idle(self._reflow)

    def _button(self, text, command, kind, column, width):
        button = ctk.CTkButton(self.footer, text=text, command=command, width=width, **form_style(kind))
        button.grid(row=0, column=column, padx=SPACE.M, pady=SPACE.M)
        keyboard_focus(button, activate=button.invoke)
        return button

    @staticmethod
    def _heading(parent, row, text):
        ctk.CTkLabel(parent, text=text, font=(FONT.FAMILY, FONT.BODY, "bold"), anchor="w",
                     text_color=T["TEXT_PRIMARY"]).grid(row=row, column=0, sticky="ew",
                                                        padx=SPACE.M, pady=(SPACE.XL, SPACE.S))

    def _choice(self, parent, row, label, choices, value):
        line = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        line.grid(row=row, column=0, sticky="ew", padx=SPACE.M, pady=SPACE.XS)
        line.grid_columnconfigure(0, weight=1)
        name = ctk.CTkLabel(line, text=label, height=0, wraplength=380, justify="left", anchor="w",
                            font=(FONT.FAMILY, FONT.BODY), text_color=T["TEXT_PRIMARY"])
        name.grid(row=0, column=0, sticky="ew", pady=SPACE.XS)
        shell = ctk.CTkFrame(line, fg_color=T["BG_INPUT"], border_color=T["BORDER_STRONG"],
                             border_width=FORM.FOCUS_WIDTH, corner_radius=RADIUS.CONTROL)
        shell.grid(row=0, column=1, sticky="e", padx=(SPACE.XL, 0))
        menu = ctk.CTkOptionMenu(shell, values=list(choices), width=FORM.MENU_W, **form_style("menu"))
        menu.pack(padx=FORM.FOCUS_WIDTH, pady=FORM.FOCUS_WIDTH)
        menu.set(next((name for name, item in choices.items() if item == value), next(iter(choices))))
        keyboard_focus(menu, activate=menu_keyboard(menu), ring=shell)
        self.choice_rows.append((line, name, shell))
        return menu, choices

    def _check_group(self, parent, row, title):
        group = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        group.grid(row=row, column=0, sticky="ew", padx=SPACE.M, pady=SPACE.S)
        self._heading(group, 0, title)
        body = ctk.CTkFrame(group, fg_color="transparent", corner_radius=0)
        body.grid(row=1, column=0, sticky="ew")
        group.grid_columnconfigure(0, weight=1)
        self.check_groups.append((body, []))
        return body

    def _check(self, parent, label, value):
        var = ctk.BooleanVar(master=self, value=value)
        cell = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        cell.grid_columnconfigure(1, weight=1)
        check = ctk.CTkCheckBox(cell, text="", width=FORM.CHECK_SIZE, variable=var, **form_style("check"))
        check.grid(row=0, column=0, sticky="n", padx=(0, SPACE.M), pady=SPACE.XS)
        name = ctk.CTkLabel(cell, text=label, height=0, wraplength=280, justify="left", anchor="w",
                            font=(FONT.FAMILY, FONT.BODY), text_color=T["TEXT_PRIMARY"])
        name.grid(row=0, column=1, sticky="ew", pady=SPACE.M)
        name.bind("<Button-1>", lambda _event: check.toggle(), add="+")
        keyboard_focus(check, activate=check.toggle)
        self.check_groups[-1][1].append((cell, name))
        return var

    def _schedule_layout(self, event=None):
        if event is not None and event.widget is not self:
            return
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
        # Newly mapped pages must wrap before their first paint. Only live
        # window resizing is debounced; drafts and widget identities persist.
        self._layout_job = (self.after_idle(self._reflow) if event is None
                            else self.after(FORM.REFLOW_MS, self._reflow))

    def _reflow(self):
        self._layout_job = None
        if not self.winfo_exists():
            return
        # Parent viewport width, never the children's requested content width.
        width = max(300, int(self.winfo_width() / self.tabs._get_widget_scaling()) - 96)
        selected = self.tabs.get()
        signature = (width // 12, selected)
        if signature == self._layout_signature:
            return
        self._layout_signature = signature
        self.layout_passes += 1
        active = next(panel for key, panel in self.panels.items() if tr("filter." + key) == selected)
        stacked = width < FORM.STACK_AT
        columns = 2 if width >= FORM.CHECK_COLUMNS_AT else 1
        previous = self._panel_breakpoints.get(selected)
        self._panel_breakpoints[selected] = (stacked, columns)
        for line, label, shell in self.choice_rows:
            if line.master is not active:
                continue
            label.configure(wraplength=max(160, width - (0 if stacked else FORM.MENU_W + SPACE.XXL)))
            if previous is None or previous[0] != stacked:
                shell.grid_configure(row=1 if stacked else 0, column=0 if stacked else 1,
                                     sticky="w" if stacked else "e", padx=0 if stacked else (SPACE.XL, 0))
        for body, cells in self.check_groups:
            if body.master.master is not active:
                continue
            changed = previous is None or previous[1] != columns
            if changed:
                body.grid_columnconfigure(0, weight=1, uniform="checks")
                body.grid_columnconfigure(1, weight=1 if columns == 2 else 0, uniform="checks")
            for index, (cell, label) in enumerate(cells):
                if changed:
                    cell.grid(row=index // columns, column=index % columns, sticky="ew",
                              padx=(0, SPACE.L), pady=SPACE.XXS)
                label.configure(wraplength=max(160, width // columns - 58))
        for label in self.guides:
            if label.master is active:
                label.configure(wraplength=width)
        if active is self.panels["metrics"]:
            for label in self.metric_labels:
                label.configure(wraplength=max(140, int(width * .37)))
        self.error.configure(wraplength=width)

    def destroy(self):
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        super().destroy()

    @staticmethod
    def _value(pair):
        menu, choices = pair
        return choices[menu.get()]

    def reset(self):
        for menu, choices in list(self.tri.values()) + list(self.tags.values()):
            menu.set(next(iter(choices)))
        self.ai[0].set(next(name for name, value in self.ai[1].items() if value == "all"))
        self.leaderboard[0].set(next(name for name, value in self.leaderboard[1].items() if value == "All"))
        for var in list(self.env.values()) + list(self.modes.values()):
            var.set(False)
        for entry in self.bounds.values():
            entry.delete(0, "end")
        self.error.configure(text="")

    def apply(self):
        values = {"automapper": self._value(self.ai), "leaderboard": self._value(self.leaderboard)}
        values.update({key: self._value(pair) for key, pair in self.tri.items() if self._value(pair) is not None})
        values["tags"] = {key: self._value(pair) for key, pair in self.tags.items() if self._value(pair) is not None}
        values["environments"] = [key for key, value in self.env.items() if value.get()]
        values["characteristics"] = [key for key, value in self.modes.items() if value.get()]
        values.update({key: value.get().strip() for key, value in self.bounds.items() if value.get().strip()})
        try:
            filter_params(values)
        except DownloadError:
            self.error.configure(text=tr("dl.error.filters"))
            return
        self.on_apply(values)
        self.destroy()
