"""Native Qt filter drafts; shared catalog and validator, no backend changes."""
from __future__ import annotations

import copy

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..downloads import DownloadError
from ..messages import tr
from ..search_catalog import ENVIRONMENTS, NEW_ENVIRONMENTS, STYLE_TAGS, TAGS
from ..search_filters import CHARACTERISTICS, FLAGS, RANGES, filter_params
from ..theme import FORM, SPACE


class CheckOption(QWidget):
    """Native checkbox with a wrapping, clickable plain-text label."""
    def __init__(self, text, checked):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, SPACE.XS, SPACE.S, SPACE.XS)
        row.setSpacing(SPACE.M)
        self.check = QCheckBox()
        self.check.setAccessibleName(text)
        self.check.setChecked(checked)
        self.label = QLabel(text)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setMinimumWidth(0)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.label.setBuddy(self.check)
        self.label.installEventFilter(self)
        row.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.label, 1)
        super().setFocusProxy(self.check)

    def eventFilter(self, watched, event):
        if watched is self.label and event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton and self.check.isEnabled():
                self.check.click()
                self.check.setFocus()
                return True
        return super().eventFilter(watched, event)


class FilterDialog(QDialog):
    applied = Signal(dict)

    def __init__(self, parent=None, values=None):
        super().__init__(parent)
        super().setObjectName("filterDialog")
        super().setWindowTitle(tr("filter.title"))
        self.values = copy.deepcopy(values or {})
        self.tri, self.tags, self.env, self.modes, self.bounds = {}, {}, {}, {}, {}
        self.panels, self.forms, self.groups = {}, [], []
        self.breakpoints = {}
        super().setWindowModality(Qt.WindowModality.WindowModal)
        available = self.screen().availableGeometry()
        super().setMinimumSize(min(460, available.width()), min(360, available.height()))
        super().resize(min(760, available.width()-SPACE.XL), min(680, available.height()-SPACE.XL))
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
        root.setSpacing(SPACE.M)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(False)
        root.addWidget(self.tabs, 1)
        for key in ("general", "tags", "environments", "metrics"):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            content = QWidget()
            content.setObjectName("filterContent")
            layout = QVBoxLayout(content)
            layout.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
            layout.setSpacing(SPACE.M)
            layout.addWidget(self.label(tr("filter.guide."+key), "muted"))
            scroll.setWidget(content)
            scroll.viewport().installEventFilter(self)
            self.panels[key] = scroll
            self.tabs.addTab(scroll, tr("filter."+key))
        self.options = ((tr("filter.any"), None), (tr("filter.yes"), True), (tr("filter.no"), False))
        general = self.form("general")
        self.ai = self.choice(general, tr("filter.ai"),
                              tuple((tr("filter."+name), value) for name, value in
                                    (("all", "all"), ("human", "human"), ("ai_only", "ai"))),
                              self.values.get("automapper", "human"))
        self.leaderboard = self.choice(general, tr("filter.leaderboard"),
                                      tuple((tr("filter.board."+key), key) for key in
                                            ("All", "Ranked", "BeatLeader", "ScoreSaber")),
                                      self.values.get("leaderboard", "All"))
        for key in FLAGS:
            self.tri[key] = self.choice(general, tr("filter.explain."+key), self.options, self.values.get(key))
        self.check_group("general", tr("filter.modes"), CHARACTERISTICS, "mode", self.modes,
                         self.values.get("characteristics", []))
        for name, keys in (("style", STYLE_TAGS), ("genre", tuple(key for key in TAGS if key not in STYLE_TAGS))):
            self.panels["tags"].widget().layout().addWidget(self.label(tr("filter."+name), "subtitle"))
            form = self.form("tags")
            for key in sorted(keys, key=lambda item: TAGS[item]):
                self.tags[key] = self.choice(form, tr("filter.tag."+key), self.options,
                                            self.values.get("tags", {}).get(key))
        for name, keys in (("legacy", tuple(key for key in ENVIRONMENTS if key not in NEW_ENVIRONMENTS)),
                           ("new", NEW_ENVIRONMENTS)):
            self.check_group("environments", tr("filter."+name), keys, "env", self.env,
                             self.values.get("environments", []))
        metrics = self.panels["metrics"].widget().layout()
        metrics.addWidget(self.label(tr("filter.bounds_hint"), "muted"))
        form = self.form("metrics")
        for name in RANGES:
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(SPACE.M)
            for prefix in ("min", "max"):
                key = prefix+name
                entry = QLineEdit(str(self.values.get(key, "")))
                entry.setMinimumWidth(0)
                entry.setPlaceholderText(tr("filter."+prefix))
                entry.setAccessibleName(tr("filter.metric."+name)+" · "+tr("filter."+prefix))
                entry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                row.addWidget(entry)
                self.bounds[key] = entry
            form.addRow(self.label(tr("filter.metric."+name)), line)
        for key in ("from", "to"):
            entry = QLineEdit(str(self.values.get(key, "")))
            entry.setPlaceholderText("YYYY-MM-DD")
            entry.setAccessibleName(tr("filter."+key))
            self.bounds[key] = entry
            form.addRow(self.label(tr("filter."+key)), entry)
        for panel in self.panels.values():
            panel.widget().layout().addStretch()
        self.error = self.label("", "error")
        self.error.hide()
        root.addWidget(self.error)
        self.footer = QFrame()
        self.footer.setObjectName("filterFooter")
        buttons = QHBoxLayout(self.footer)
        buttons.setContentsMargins(SPACE.M, SPACE.M, SPACE.M, SPACE.M)
        buttons.setSpacing(SPACE.M)
        self.reset_button = QPushButton(tr("filter.reset"))
        self.cancel_button = QPushButton(tr("filter.cancel"))
        self.apply_button = QPushButton(tr("filter.apply"))
        self.apply_button.setProperty("role", "primary")
        self.apply_button.setDefault(True)
        for button in (self.reset_button, self.cancel_button, self.apply_button):
            button.setAutoDefault(False)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.apply_button)
        root.addWidget(self.footer)
        self.reset_button.clicked.connect(self.reset)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.apply)
        self.tabs.currentChanged.connect(self.reflow)
        self.breakpoints.clear()
        self.reflow()

    @staticmethod
    def label(text, role=None):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if role:
            label.setProperty("role", role)
        return label

    def form(self, panel):
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(SPACE.XL)
        form.setVerticalSpacing(SPACE.M)
        self.panels[panel].widget().layout().addLayout(form)
        self.forms.append((panel, form))
        return form

    def choice(self, form, text, choices, value):
        menu = QComboBox()
        for label, data in choices:
            menu.addItem(label, data)
        menu.setCurrentIndex(max(0, next((i for i, (_label, data) in enumerate(choices) if data == value), 0)))
        # Native edit-field padding/arrow must not truncate long translated enums.
        text_width = max(menu.fontMetrics().horizontalAdvance(label) for label, _data in choices)
        menu.setFixedWidth(max(FORM.MENU_W, text_width+SPACE.L*2+FORM.CONTROL_H))
        menu.setAccessibleName(text)
        label = self.label(text)
        form.addRow(label, menu)
        return menu

    def check_group(self, panel, text, keys, prefix, target, selected):
        layout = self.panels[panel].widget().layout()
        layout.addWidget(self.label(text, "subtitle"))
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE.L)
        grid.setVerticalSpacing(0)
        cells = []
        for key in keys:
            cell = CheckOption(tr("filter."+prefix+"."+key), key in selected)
            target[key] = cell.check
            grid.addWidget(cell, len(cells), 0)
            cells.append(cell)
        layout.addWidget(body)
        self.groups.append((panel, grid, cells))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.reflow()
        return super().eventFilter(watched, event)

    def reflow(self, *_args):
        for key, panel in self.panels.items():
            width = panel.viewport().width()-SPACE.L*2
            breakpoint = (width < FORM.STACK_AT, 2 if width >= FORM.CHECK_COLUMNS_AT else 1)
            if self.breakpoints.get(key) == breakpoint:
                continue
            self.breakpoints[key] = breakpoint
            stacked, columns = breakpoint
            for name, form in self.forms:
                if name == key:
                    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows if stacked
                                          else QFormLayout.RowWrapPolicy.DontWrapRows)
            for name, grid, cells in self.groups:
                if name == key:
                    for cell in cells:
                        grid.removeWidget(cell)
                    grid.setColumnStretch(0, 1)
                    grid.setColumnStretch(1, 1 if columns == 2 else 0)
                    for index, cell in enumerate(cells):
                        grid.addWidget(cell, index//columns, index % columns)

    def reset(self):
        for menu in (*self.tri.values(), *self.tags.values()):
            menu.setCurrentIndex(0)
        self.ai.setCurrentIndex(self.ai.findData("all"))
        self.leaderboard.setCurrentIndex(self.leaderboard.findData("All"))
        for check in (*self.env.values(), *self.modes.values()):
            check.setChecked(False)
        for entry in self.bounds.values():
            entry.clear()
        self.error.hide()

    def draft(self):
        values = {"automapper": self.ai.currentData(), "leaderboard": self.leaderboard.currentData()}
        values.update({key: menu.currentData() for key, menu in self.tri.items() if menu.currentData() is not None})
        values["tags"] = {key: menu.currentData() for key, menu in self.tags.items() if menu.currentData() is not None}
        values["environments"] = [key for key, check in self.env.items() if check.isChecked()]
        values["characteristics"] = [key for key, check in self.modes.items() if check.isChecked()]
        values.update({key: entry.text().strip() for key, entry in self.bounds.items() if entry.text().strip()})
        return values

    def apply(self):
        values = self.draft()
        try:
            filter_params(values)
        except DownloadError:
            self.error.setText(tr("dl.error.filters"))
            self.error.show()
            return
        self.applied.emit(copy.deepcopy(values))
        super().accept()
