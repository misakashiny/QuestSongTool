"""M1 component preview, intentionally independent of song workflows."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..messages import LANGUAGE_NAMES, set_language, tr
from ..theme import SPACE
from .theme import apply_theme


class MainWindow(QMainWindow):
    def __init__(self, language="zh", mode="light"):
        super().__init__()
        self.setObjectName("qtPreview")
        self.setMinimumSize(640, 480)
        self.resize(1000, 720)
        self.labels = []
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE.XXL, SPACE.XXL, SPACE.XXL, SPACE.XXL)
        layout.setSpacing(SPACE.XL)
        layout.addWidget(self.label("qt.heading", "title"))
        layout.addWidget(self.label("qt.scope", "muted"))
        card = QFrame()
        card.setObjectName("card")
        content = QVBoxLayout(card)
        content.setContentsMargins(SPACE.XXL, SPACE.XXL, SPACE.XXL, SPACE.XXL)
        content.setSpacing(SPACE.XL)
        content.addWidget(self.label("qt.appearance", "subtitle"))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setSpacing(SPACE.L)
        self.language = QComboBox()
        for key, name in LANGUAGE_NAMES.items():
            self.language.addItem(name, key)
        self.language.setCurrentIndex(self.language.findData(language))
        self.mode = QComboBox()
        self.mode.addItem("", "light")
        self.mode.addItem("", "dark")
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.input = QLineEdit()
        self.check = QCheckBox()
        self.check.setChecked(True)
        self.unavailable = QPushButton()
        self.unavailable.setEnabled(False)
        for key, widget in (("qt.language", self.language), ("qt.theme", self.mode),
                            ("qt.input", self.input), ("qt.option", self.check),
                            ("qt.disabled", self.unavailable)):
            label = self.label(key)
            label.setBuddy(widget)
            form.addRow(label, widget)
        content.addLayout(form)
        self.result = self.label("qt.ready", "muted")
        content.addWidget(self.result)
        buttons = QHBoxLayout()
        self.reset = QPushButton()
        self.reset.setProperty("role", "danger")
        self.apply = QPushButton()
        self.apply.setProperty("role", "primary")
        self.exit_button = QPushButton()
        for button in (self.reset, self.apply, self.exit_button):
            buttons.addWidget(button)
        content.addLayout(buttons)
        layout.addWidget(card)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        self.setCentralWidget(scroll)
        self.language.currentIndexChanged.connect(self.retranslate)
        self.mode.currentIndexChanged.connect(self.change_theme)
        self.apply.clicked.connect(self.apply_example)
        self.reset.clicked.connect(self.reset_example)
        self.exit_button.clicked.connect(self.close)
        self.retranslate()
        self.change_theme()
        self.input.setFocus(Qt.FocusReason.OtherFocusReason)

    def label(self, key, role=None):
        label = QLabel()
        label.setWordWrap(True)
        if role:
            label.setProperty("role", role)
        self.labels.append((label, key))
        return label

    def retranslate(self):
        set_language(self.language.currentData())
        self.setWindowTitle(tr("qt.title"))
        for label, key in self.labels:
            label.setText(tr(key))
        self.mode.setItemText(0, tr("qt.light"))
        self.mode.setItemText(1, tr("qt.dark"))
        self.input.setPlaceholderText(tr("qt.placeholder"))
        self.check.setText(tr("qt.check"))
        self.unavailable.setText(tr("qt.unavailable"))
        self.apply.setText(tr("qt.apply"))
        self.reset.setText(tr("qt.reset"))
        self.exit_button.setText(tr("qt.close"))
        self.statusBar().showMessage(tr("qt.status"))

    def change_theme(self):
        apply_theme(QApplication.instance(), self.mode.currentData())

    def apply_example(self):
        self.result.setText(tr("qt.applied"))

    def reset_example(self):
        self.input.clear()
        self.check.setChecked(True)
        self.result.setText(tr("qt.ready"))
