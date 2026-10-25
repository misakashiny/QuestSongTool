"""首次使用向导（ROADMAP C4）。

新用户第一次打开程序时，最大的困惑不是"某个按钮在哪"，而是**该按什么顺序来**：
路径框、「📡 检测设备」、「🔍 找 Quest 缺的」都摆在那儿，但先点哪个没人说。

这个向导把三步摆明：

    ① 选本地歌曲文件夹  →  ② 连 Quest 并检测  →  ③ 开始对比

**刻意做得很轻**：三步都在一个窗口里，每步都能单独做，随时可以「稍后再说」；
而且只在第一次启动时自动弹一次（`Settings.wizard_done`），
之后可以从「帮助 → 首次使用向导」再打开。

它不自己干任何活：选目录/检测设备/开始对比都回调主程序 ——
那些动作涉及后台任务与状态机，主程序才知道该怎么做。
向导只负责"显示当前到哪一步了"（`refresh`）。
"""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk
from tkinter import messagebox

from .messages import tr
from .theme import FONT, RADIUS, SIZE, SPACE, T
from .ui_dialogs import BODY_FONT_SIZE, TITLE_FONT_SIZE, ModalDialog

#: 三步的说明文案（序号、标题、提示）
#: 三步的文案键（序号、标题键、提示键）
_STEPS: tuple[tuple[str, str, str], ...] = (
    ("1", "wizard.step1.title", "wizard.step1.hint"),
    ("2", "wizard.step2.title", "wizard.step2.hint"),
    ("3", "wizard.step3.title", "wizard.step3.hint"),
)


class FirstRunWizard(ModalDialog):
    """三步式首次使用向导。``show()`` 返回 True 表示用户点了「开始对比」。"""

    ICON = "🚀"
    # 默认值只是兜底；`__init__` 会按**当前语言**覆盖成实例属性 ——
    # 类属性在 import 期就固定了，跟着语言走会错。
    DEFAULT_TEXT = "开始对比"
    CANCEL_TEXT = "稍后再说"

    def __init__(
        self,
        master,
        *,
        local_path: str = "",
        quest_path: str = "",
        on_pick_local: Callable[[], None] | None = None,
        on_detect_quest: Callable[[], None] | None = None,
        on_start_compare: Callable[[], None] | None = None,
        size: tuple[int, int] = (660, 470),
    ) -> None:
        # 建体之前就得备好：_create_body() 会读它们
        self.DEFAULT_TEXT = tr("wizard.start")
        self.CANCEL_TEXT = tr("wizard.later")
        self._local_path = local_path
        self._quest_path = quest_path
        self._on_pick_local = on_pick_local
        self._on_detect_quest = on_detect_quest
        self._on_start_compare = on_start_compare
        self._status_labels: list[ctk.CTkLabel] = []
        self._step_buttons: list[ctk.CTkButton] = []

        super().__init__(master, tr("wizard.title"), size=size, resizable=(False, False))
        self.result = False

    # ------------------------------------------------------------------
    # 内容
    # ------------------------------------------------------------------
    def _create_body(self, parent: ctk.CTkFrame) -> None:
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", pady=(2, SPACE.S))
        ctk.CTkLabel(head, text=self.ICON, font=(self.font, 34)).pack(side="left", padx=(2, 12))
        ctk.CTkLabel(
            head, text=tr("wizard.heading"), font=(self.font, TITLE_FONT_SIZE, "bold"), anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            parent,
            text=tr("wizard.intro"),
            font=(self.font, BODY_FONT_SIZE), text_color=T["TEXT_SECONDARY"],
            justify="left", wraplength=560, anchor="w",
        ).pack(fill="x", pady=(0, SPACE.M))

        actions = (self._on_pick_local, self._on_detect_quest, None)
        labels = (tr("wizard.step1.button"), tr("wizard.step2.button"), tr("wizard.step3.button"))
        for index, (number, title, hint) in enumerate(_STEPS):
            card = ctk.CTkFrame(parent, corner_radius=RADIUS.CARD, fg_color=T["BG_CARD"],
                                border_width=1, border_color=T["BORDER"])
            card.pack(fill="x", pady=SPACE.XXS)
            card.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(card, text=number, width=26, font=(self.font, BODY_FONT_SIZE, "bold"),
                         text_color=T["ACCENT"]).grid(row=0, column=0, rowspan=2,
                                                      padx=(SPACE.M, SPACE.XS), pady=SPACE.S)
            ctk.CTkLabel(card, text=tr(title), font=(self.font, BODY_FONT_SIZE, "bold"),
                         text_color=T["TEXT_PRIMARY"], anchor="w").grid(
                row=0, column=1, sticky="w", pady=(SPACE.S, 0))
            status = ctk.CTkLabel(card, text=tr(hint), font=(self.font, FONT.TINY),
                                  text_color=T["TEXT_MUTED"], justify="left",
                                  wraplength=430, anchor="w")
            status.grid(row=1, column=1, sticky="w", pady=(0, SPACE.S))
            self._status_labels.append(status)

            button = ctk.CTkButton(
                card, text=labels[index], width=112, height=SIZE.INPUT_H,
                corner_radius=RADIUS.CONTROL, font=(self.font, FONT.SMALL),
                fg_color=T["ACCENT"] if index == 2 else T["NEUTRAL"],
                hover_color=T["ACCENT_HOVER"] if index == 2 else T["NEUTRAL_HOVER"],
                command=actions[index] if actions[index] is not None else self._on_default,
            )
            button.grid(row=0, column=2, rowspan=2, padx=(SPACE.XS, SPACE.M), pady=SPACE.S)
            self._step_buttons.append(button)

        self._refresh_status()

    def _create_buttons(self, parent: ctk.CTkFrame) -> None:
        ctk.CTkButton(
            parent, text=self.CANCEL_TEXT, width=120, height=SIZE.BUTTON_H,
            font=(self.font, BODY_FONT_SIZE), fg_color=T["NEUTRAL"],
            hover_color=T["NEUTRAL_HOVER"], command=self._on_cancel,
        ).pack(side="right", padx=(10, 0))
        self.default_button = ctk.CTkButton(
            parent, text=self.DEFAULT_TEXT, width=150, height=SIZE.BUTTON_H,
            font=(self.font, BODY_FONT_SIZE, "bold"), fg_color=T["ACCENT"],
            hover_color=T["ACCENT_HOVER"], command=self._on_default,
        )
        self.default_button.pack(side="right")
        self.confirm_button = self.default_button

    # ------------------------------------------------------------------
    # 状态刷新
    # ------------------------------------------------------------------
    def refresh(self, local_path: str = "", quest_path: str = "") -> None:
        """主程序在"选好目录 / 检测完设备"之后调用，把打勾状态更新出来。"""
        self._local_path = local_path or self._local_path
        self._quest_path = quest_path or self._quest_path
        self._refresh_status()

    def _refresh_status(self) -> None:
        paths = (self._local_path, self._quest_path, "")
        for index, label in enumerate(self._status_labels):
            done = bool(paths[index]) if index < 2 else (
                bool(self._local_path) and bool(self._quest_path)
            )
            if index < 2:
                hint = tr(_STEPS[index][2])
                if paths[index]:
                    shown = paths[index]
                    if len(shown) > 58:
                        shown = "…" + shown[-57:]
                    label.configure(text=tr("wizard.ready", path=shown), text_color=T["SUCCESS"])
                else:
                    label.configure(text=hint, text_color=T["TEXT_MUTED"])
            else:
                if done:
                    label.configure(text=tr("wizard.ready.all"), text_color=T["SUCCESS"])
                else:
                    label.configure(text=tr(_STEPS[index][2]), text_color=T["TEXT_MUTED"])
            if index == 2:
                self._step_buttons[index].configure(state="normal" if done else "disabled")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def _on_default(self) -> None:
        if not (self._local_path and self._quest_path):
            messagebox.showinfo(
                tr("wizard.not_ready.title"),
                tr("wizard.not_ready.body"),
                parent=self,
            )
            return
        if self._on_start_compare is not None:
            self._on_start_compare()
        self._finish(True)
