"""On-demand, read-only operation help; no commands or external links execute here."""
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QFrame, QLabel,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..messages import tr
from ..theme import DESKTOP, SPACE
from .surfaces import action_icons


GROUPS = (
    ('qt.help.general',(
        ('Ctrl+O','qt.help.choose'),('F5','qt.help.refresh'),('Ctrl+F','qt.help.search'),
        ('Ctrl+D','qt.help.details'),('F1','qt.help.show'),
    )),
    ('qt.help.results',(
        ('Ctrl+A','qt.help.visible'),('Ctrl+Shift+A','qt.help.all'),('Ctrl+C','qt.help.copy'),
        ('Shift+F10 / Menu','qt.help.menu'),('Esc','qt.help.clear'),
    )),
)


def text_label(text,role=None):
    label = QLabel(text);label.setTextFormat(Qt.TextFormat.PlainText);label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    policy = QSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
    policy.setHeightForWidth(True);label.setSizePolicy(policy)
    if role:label.setProperty('role',role)
    return label


class HelpDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent)
        super().setWindowTitle(tr('qt.help.title'));super().setWindowModality(Qt.WindowModality.WindowModal)
        super().setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        available=parent.screen().availableGeometry()
        width=max(1,available.width()-SPACE.XXL*2);height=max(1,available.height()-SPACE.XXL*2)
        super().setMinimumSize(min(DESKTOP.LIBRARY_HELP_MIN_W,width),min(DESKTOP.LIBRARY_HELP_MIN_H,height))
        super().resize(min(DESKTOP.LIBRARY_HELP_W,width),min(DESKTOP.LIBRARY_HELP_H,height))
        super().setSizeGripEnabled(True)
        box=QVBoxLayout(self);box.setContentsMargins(SPACE.L,SPACE.L,SPACE.L,SPACE.L)
        self.heading=text_label(tr('qt.help.title'),'subtitle');box.addWidget(self.heading)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        content=QWidget();content.setObjectName('page');body=QVBoxLayout(content)
        body.setContentsMargins(0,0,SPACE.XS,0)
        self.intro=text_label(tr('qt.help.intro'),'muted');body.addWidget(self.intro)
        self.rows=[]
        for heading,items in GROUPS:
            card=QFrame();card.setObjectName('card');card_box=QVBoxLayout(card)
            card_box.addWidget(text_label(tr(heading),'subtitle'))
            form=QFormLayout();form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setHorizontalSpacing(SPACE.L);form.setVerticalSpacing(SPACE.M)
            for sequence,key in items:
                shortcut=QLabel(sequence);shortcut.setTextFormat(Qt.TextFormat.PlainText)
                shortcut.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                shortcut.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop)
                description=text_label(tr(key));description.setMinimumWidth(DESKTOP.LIBRARY_RATING_W)
                form.addRow(shortcut,description);self.rows.append((sequence,key,shortcut,description))
            card_box.addLayout(form);body.addWidget(card)
        self.notes=[]
        for key in ('qt.help.selection_note','qt.help.search_note','qt.help.snapshot_note'):
            note=text_label(tr(key),'muted');body.addWidget(note);self.notes.append(note)
        body.addStretch();self.scroll.setWidget(content);box.addWidget(self.scroll,1)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_button=self.buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setText(tr('qt.help.close'));self.close_button.setDefault(True)
        action_icons((self.close_button,'back'))
        self.buttons.rejected.connect(self.reject);box.addWidget(self.buttons)


class LibraryHelp(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.dialog=None;self.closed=False

    def show(self):
        if self.closed or self.window.closed:return
        if self.dialog is not None:
            self.dialog.raise_();self.dialog.activateWindow();return
        if QApplication.activeModalWidget() is not None:return
        dialog=HelpDialog(self.window);self.dialog=dialog
        dialog.finished.connect(lambda _result,active=dialog:self.finished(active))
        dialog.open()

    def finished(self,dialog):
        if self.dialog is dialog:self.dialog=None

    def retranslate(self):
        window=self.window
        for widget,key,sequence in ((window.browse,'qt.help.choose','Ctrl+O'),
                                    (window.reload,'qt.help.refresh','F5'),
                                    (window.search,'qt.help.search','Ctrl+F'),
                                    (window.help_button,'qt.help.show','F1')):
            widget.setToolTip(tr(key)+' ('+sequence+')')
        for widget,key in ((window.select_visible,'qt.help.select_button'),
                           (window.clear,'qt.help.clear_button'),
                           (window.missing_side,'qt.help.direction_tip'),
                           (window.view_mode,'qt.help.view_tip')):
            widget.setToolTip(tr(key))
        window.help_button.setAccessibleName(tr('qt.help.open'))

    def close(self):
        self.closed=True
        if self.dialog is not None:self.dialog.reject()
