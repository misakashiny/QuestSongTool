"""Opt-in form styling and keyboard focus, without global theme mutations."""
from __future__ import annotations

import tkinter as tk
import customtkinter as ctk

from .theme import T, FONT, FORM, FORM_COLORS, RADIUS


class FormTabs(ctk.CTkTabview):
    """Same CTk 5.2.2 stale-callback guard used by the existing download tabs."""

    _button_height = FORM.CONTROL_H

    def __init__(self, master, command=None, **kwargs):
        self._on_change = command
        super().__init__(master, command=command, **kwargs)

    def set(self, name):
        super().set(name)
        if self._on_change is not None:
            self._on_change()

    def _grid_forget_all_tabs(self, exclude_name=None):
        if exclude_name is not None and exclude_name != self.get():
            return
        super()._grid_forget_all_tabs(exclude_name)


def form_style(kind):
    """Return fresh kwargs so consumers cannot mutate shared style dictionaries."""
    common = dict(height=FORM.CONTROL_H, corner_radius=RADIUS.CONTROL,
                  font=(FONT.FAMILY, FONT.BODY), text_color=T["TEXT_PRIMARY"])
    if kind == "menu":
        return dict(common, fg_color=T["BG_INPUT"], button_color=T["BG_HOVER"],
                    button_hover_color=T["ACCENT_SOFT"], dropdown_fg_color=T["BG_ELEVATED"],
                    dropdown_hover_color=T["BG_SELECTED"], dropdown_text_color=T["TEXT_PRIMARY"],
                    text_color_disabled=T["TEXT_MUTED"], dynamic_resizing=False)
    if kind == "entry":
        return dict(common, fg_color=T["BG_INPUT"], border_color=T["BORDER_STRONG"],
                    border_width=1, placeholder_text_color=T["TEXT_MUTED"])
    if kind == "check":
        return dict(common, checkbox_width=FORM.CHECK_SIZE, checkbox_height=FORM.CHECK_SIZE,
                    corner_radius=RADIUS.CHIP, border_width=2, border_color=T["BORDER_STRONG"],
                    fg_color=T["ACCENT"], hover_color=T["ACCENT_HOVER"],
                    checkmark_color=T["ACCENT_TEXT"], text_color_disabled=T["TEXT_MUTED"])
    return dict(common, border_width=2, border_color=T["BORDER_STRONG"],
                fg_color=T["ACCENT"] if kind == "primary" else T["BG_CARD"],
                hover_color=FORM_COLORS["PRIMARY_HOVER"] if kind == "primary" else T["BG_HOVER"],
                text_color=T["ACCENT_TEXT"] if kind == "primary" else T["TEXT_PRIMARY"],
                text_color_disabled=FORM_COLORS["DISABLED_TEXT"])


def keyboard_focus(widget, *, activate=None, ring=None, entry=False):
    """Use public Tk focus APIs; leave CTk's mouse callbacks in place."""
    target = ring or widget
    original = target.cget("border_color")

    def focus_in(_event):
        if widget.cget("state") != "disabled":
            target.configure(border_color=T["TEXT_PRIMARY"] if widget.cget("fg_color") == T["ACCENT"] and ring is None else T["ACCENT"])

    def focus_out(_event):
        target.configure(border_color=original)

    widget.bind("<FocusIn>", focus_in, add="+")
    widget.bind("<FocusOut>", focus_out, add="+")
    if entry:
        return
    # CTk controls are Frames. Put the frame itself in the native Tab order,
    # instead of depending on CTk's private canvas/label attribute names.
    tk.Frame.configure(widget, takefocus=lambda _path=None: widget.cget("state") != "disabled")
    tk.Misc.bind(widget, "<FocusIn>", focus_in, add="+")
    tk.Misc.bind(widget, "<FocusOut>", focus_out, add="+")
    widget.bind("<Button-1>", lambda _event: tk.Misc.focus_set(widget), add="+")
    if activate:
        def invoke(_event):
            if widget.cget("state") != "disabled":
                activate()
            return "break"
        tk.Misc.bind(widget, "<space>", invoke, add="+")
        tk.Misc.bind(widget, "<Return>", invoke, add="+")


def menu_keyboard(menu):
    """Arrow keys change options; Space/Return opens the native CTk menu."""
    def move(event):
        if menu.cget("state") == "disabled":
            return "break"
        values = menu.cget("values")
        index = values.index(menu.get())
        menu.set(values[(index + (1 if event.keysym == "Down" else -1)) % len(values)])
        command = menu.cget("command")
        if command:
            command(menu.get())
        return "break"

    def open_menu():
        for child in menu.winfo_children():
            if isinstance(child, tk.Menu):
                # Post the existing native menu directly, without synthesizing
                # a second mouse click or depending on CTk private attributes.
                child.post(menu.winfo_rootx(), menu.winfo_rooty() + menu.winfo_height() + 2)
                break

    tk.Misc.bind(menu, "<Down>", move, add="+")
    tk.Misc.bind(menu, "<Up>", move, add="+")
    return open_menu
