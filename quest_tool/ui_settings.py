"""设置窗口。

v15.2 从 `app.py` 里搬出来的 —— 那里一度有 2765 行。
它只依赖 `Settings` 与几个常量，跟主控制器没有耦合。
"""
from __future__ import annotations

import os
from typing import Callable

import customtkinter as ctk
from tkinter import messagebox

from .audio import PREVIEW_LENGTH_CHOICES, format_preview_length
from .constants import APP_VERSION, resolve_font
from .messages import LANGUAGES, tr
from .storage import Settings, database_path, settings_path
from .theme import FONT, RADIUS, SIZE, SPACE, T, UI_SCALE_CHOICES




# ======================================================================================
# 设置对话框
# ======================================================================================
class SettingsDialog:
    """设置窗口：adb 路径、主题、删除确认、缩放、语言等。"""

    def __init__(self, parent: ctk.CTk, settings: Settings, on_saved: Callable[[], None] | None = None) -> None:
        self.parent = parent
        self.settings = settings
        self.on_saved = on_saved
        self.font = resolve_font(parent)
        self.window = ctk.CTkToplevel(parent)
        self.window.title(tr("settings.title"))
        self.window.geometry("620x580")
        self.window.transient(parent)
        self.window.configure(fg_color=T["BG_APP"])
        self._build()
        self.window.after(60, self._finish_open)
        self.window.bind("<Escape>", lambda _e: self.window.destroy())
        self.window.bind("<Return>", lambda _e: self._save())

    def _finish_open(self) -> None:
        try:
            self.window.wait_visibility()
            self.window.grab_set()
            self.window.focus_force()
        except Exception:
            pass

    def _build(self) -> None:
        card = ctk.CTkFrame(self.window, corner_radius=RADIUS.CARD, fg_color=T["BG_CARD"],
                            border_width=1, border_color=T["BORDER"])
        card.pack(fill="both", expand=True, padx=SPACE.XL, pady=SPACE.XL)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text=tr("settings.heading"), font=(self.font, FONT.HEADLINE, "bold"),
                     text_color=T["TEXT_PRIMARY"]).grid(row=0, column=0, columnspan=2, sticky="w",
                                                        padx=SPACE.XL, pady=(SPACE.XL, SPACE.M))

        def label(row: int, text: str) -> None:
            ctk.CTkLabel(card, text=text, font=(self.font, FONT.BODY),
                         text_color=T["TEXT_SECONDARY"]).grid(
                row=row, column=0, sticky="w", padx=(SPACE.XL, SPACE.M), pady=SPACE.S
            )

        label(1, tr("settings.adb"))
        self.adb_var = ctk.StringVar(value=self.settings.adb_path)
        ctk.CTkEntry(card, textvariable=self.adb_var, height=SIZE.INPUT_H,
                     corner_radius=RADIUS.CONTROL, font=(self.font, FONT.SMALL),
                     fg_color=T["BG_INPUT"], border_color=T["BORDER_STRONG"],
                     placeholder_text=tr("settings.adb.placeholder")).grid(
            row=1, column=1, sticky="ew", padx=(0, SPACE.XL), pady=SPACE.S
        )

        label(2, tr("settings.appearance"))
        self.appearance_var = ctk.StringVar(value=self.settings.appearance)
        ctk.CTkOptionMenu(
            card, values=["System", "Light", "Dark"], variable=self.appearance_var,
            height=SIZE.INPUT_H, corner_radius=RADIUS.CONTROL, font=(self.font, FONT.SMALL),
            fg_color=T["ACCENT"], button_color=T["ACCENT_HOVER"],
            command=lambda _v: self._preview_theme(),
        ).grid(row=2, column=1, sticky="w", padx=(0, SPACE.XL), pady=SPACE.S)

        self.verify_var = ctk.BooleanVar(value=self.settings.verify_before_delete)
        ctk.CTkCheckBox(
            card, text=tr("settings.verify"), variable=self.verify_var,
            font=(self.font, FONT.BODY), fg_color=T["ACCENT"], hover_color=T["ACCENT_HOVER"],
            text_color=T["TEXT_PRIMARY"],
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=SPACE.XL, pady=SPACE.XS)

        self.poll_var = ctk.BooleanVar(value=self.settings.device_poll_on_start)
        ctk.CTkCheckBox(
            card, text=tr("settings.poll"), variable=self.poll_var,
            font=(self.font, FONT.BODY), fg_color=T["ACCENT"], hover_color=T["ACCENT_HOVER"],
            text_color=T["TEXT_PRIMARY"],
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=SPACE.XL, pady=SPACE.XS)

        self.overwrite_var = ctk.BooleanVar(value=self.settings.copy_overwrite)
        ctk.CTkCheckBox(
            card, text=tr("settings.overwrite"),
            variable=self.overwrite_var,
            font=(self.font, FONT.BODY), fg_color=T["ACCENT"], hover_color=T["ACCENT_HOVER"],
            text_color=T["TEXT_PRIMARY"],
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=SPACE.XL, pady=SPACE.XS)

        label(6, tr("settings.source"))
        self.source_var = ctk.StringVar(value=self.settings.quest_source)
        ctk.CTkOptionMenu(
            card, values=["auto", "adb", "drive", "mtp"], variable=self.source_var,
            height=SIZE.INPUT_H, corner_radius=RADIUS.CONTROL, font=(self.font, FONT.SMALL),
            fg_color=T["ACCENT"], button_color=T["ACCENT_HOVER"],
        ).grid(row=6, column=1, sticky="w", padx=(0, SPACE.XL), pady=SPACE.S)
        ctk.CTkLabel(
            card,
            text=tr("settings.source.hint"),
            font=(self.font, FONT.TINY), text_color=T["TEXT_MUTED"], justify="left",
            wraplength=520,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=SPACE.XL)

        label(8, tr("settings.scale"))
        self.scale_var = ctk.StringVar(value=self.settings.ui_scale)
        ctk.CTkOptionMenu(
            card, values=list(UI_SCALE_CHOICES), variable=self.scale_var,
            height=SIZE.INPUT_H, corner_radius=RADIUS.CONTROL, font=(self.font, FONT.SMALL),
            fg_color=T["ACCENT"], button_color=T["ACCENT_HOVER"],
        ).grid(row=8, column=1, sticky="w", padx=(0, SPACE.XL), pady=SPACE.S)
        ctk.CTkLabel(
            card, text=tr("settings.scale.hint"),
            font=(self.font, FONT.TINY), text_color=T["TEXT_MUTED"], justify="left",
            wraplength=520,
        ).grid(row=9, column=0, columnspan=2, sticky="w", padx=SPACE.XL)

        label(10, tr("settings.language"))
        self.language_var = ctk.StringVar(value=self.settings.language)
        ctk.CTkOptionMenu(
            card, values=list(LANGUAGES), variable=self.language_var,
            height=SIZE.INPUT_H, corner_radius=RADIUS.CONTROL, font=(self.font, FONT.SMALL),
            fg_color=T["ACCENT"], button_color=T["ACCENT_HOVER"],
        ).grid(row=10, column=1, sticky="w", padx=(0, SPACE.XL), pady=SPACE.S)
        ctk.CTkLabel(
            card, text=tr("settings.language.hint"),
            font=(self.font, FONT.TINY), text_color=T["TEXT_MUTED"], justify="left",
            wraplength=520,
        ).grid(row=11, column=0, columnspan=2, sticky="w", padx=SPACE.XL)

        # 试听时长：值与显示文案分开 —— 界面上是「30 秒 / 整曲」，
        # 存进 Settings 的必须是档位整数（见 audio.PREVIEW_LENGTH_CHOICES）。
        label(12, tr("settings.preview"))
        self.preview_labels = [format_preview_length(s) for s in PREVIEW_LENGTH_CHOICES]
        self.preview_var = ctk.StringVar(
            value=format_preview_length(self.settings.preview_seconds)
        )
        ctk.CTkOptionMenu(
            card, values=list(self.preview_labels), variable=self.preview_var,
            height=SIZE.INPUT_H, corner_radius=RADIUS.CONTROL, font=(self.font, FONT.SMALL),
            fg_color=T["ACCENT"], button_color=T["ACCENT_HOVER"],
        ).grid(row=12, column=1, sticky="w", padx=(0, SPACE.XL), pady=SPACE.S)
        ctk.CTkLabel(
            card, text=tr("settings.preview.hint"),
            font=(self.font, FONT.TINY), text_color=T["TEXT_MUTED"], justify="left",
            wraplength=520,
        ).grid(row=13, column=0, columnspan=2, sticky="w", padx=SPACE.XL)

        info = tr("settings.info", settings=settings_path(),
                  database=database_path(), version=APP_VERSION)
        ctk.CTkLabel(card, text=info, justify="left", font=(self.font, FONT.TINY),
                     text_color=T["TEXT_MUTED"]).grid(
            row=14, column=0, columnspan=2, sticky="w", padx=SPACE.XL, pady=(SPACE.L, SPACE.S)
        )

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=15, column=0, columnspan=2, sticky="e", padx=SPACE.XL,
                     pady=(SPACE.S, SPACE.XL))
        for text, role, command in (
            (tr("settings.open_logs"), "NEUTRAL", self._open_logs),
            (tr("settings.cancel"), "NEUTRAL", self.window.destroy),
            (tr("settings.save"), "SUCCESS", self._save),
        ):
            ctk.CTkButton(buttons, text=text, width=110, height=SIZE.BUTTON_H,
                          corner_radius=RADIUS.CONTROL, font=(self.font, FONT.BODY),
                          fg_color=T[role], hover_color=T.get(f"{role}_HOVER", T["BG_HOVER"]),
                          command=command).pack(side="left", padx=SPACE.XS)

    def _preview_theme(self) -> None:
        ctk.set_appearance_mode(self.appearance_var.get())

    def _open_logs(self) -> None:
        from .storage import logs_dir

        try:
            os.startfile(str(logs_dir()))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("错误", f"无法打开日志目录: {exc}")

    def _save(self) -> None:
        self.settings.adb_path = self.adb_var.get().strip()
        self.settings.appearance = self.appearance_var.get()
        self.settings.verify_before_delete = bool(self.verify_var.get())
        self.settings.device_poll_on_start = bool(self.poll_var.get())
        self.settings.copy_overwrite = bool(self.overwrite_var.get())
        self.settings.quest_source = self.source_var.get()
        self.settings.ui_scale = self.scale_var.get()
        self.settings.language = self.language_var.get()
        # 界面上是「30 秒 / 整曲」这类文案，存回档位整数。
        picked = self.preview_var.get()
        self.settings.preview_seconds = next(
            (s for s in PREVIEW_LENGTH_CHOICES if format_preview_length(s) == picked),
            30,
        )
        self.settings.save()
        if self.on_saved:
            self.on_saved()
        self.window.destroy()

    def show(self) -> None:
        self.window.wait_window()
