"""Small native display widgets for the approved download layout."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QStyle, QStyleOptionComboBox, QStylePainter, QWidget

from ..messages import tr
from ..theme import DOWNLOAD_UI, SPACE, get_palette


class ElidedLabel(QLabel):
    def __init__(self):
        super().__init__();self.full_text=''

    def setText(self, text):
        self.full_text=text;super().setToolTip(text)
        super().setText(super().fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,self.width()))

    def text(self):
        return self.full_text

    def resizeEvent(self, event):
        super().resizeEvent(event);self.setText(self.full_text)


class QuantityBox(QComboBox):
    def __init__(self, key):
        super().__init__();self.unit_key = key
        super().setMinimumWidth(72);super().setMaximumWidth(108)

    def sizeHint(self):
        return QSize(94,super().sizeHint().height())

    def paintEvent(self, event):
        option=QStyleOptionComboBox();self.initStyleOption(option)
        option.currentText=tr(self.unit_key,count=self.currentText())
        painter=QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox,option)
        group=QPalette.ColorGroup.Active if super().isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(self.palette().color(group,QPalette.ColorRole.Text))
        painter.drawText(self.rect().adjusted(10,0,-30,0),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,option.currentText)


class DifficultyTags(QWidget):
    def __init__(self):
        super().__init__();self.values=None;self.mode=None
        self.grid=QGridLayout(self);self.grid.setContentsMargins(0,0,0,0);self.grid.setSpacing(SPACE.S)

    def render(self, values, mode):
        if self.values==tuple(values) and self.mode==mode:return
        self.values=tuple(values);self.mode=mode
        while self.grid.count():self.grid.takeAt(0).widget().deleteLater()
        p=get_palette(mode)
        for i,value in enumerate(values):
            label=QLabel(value);label.setTextFormat(Qt.TextFormat.PlainText)
            label.setProperty('role','difficulty');label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bg,fg=(DOWNLOAD_UI.EXPERT_PLUS_BG,DOWNLOAD_UI.EXPERT_PLUS_TEXT) if 'Plus' in value or '+' in value else (DOWNLOAD_UI.EXPERT_BG,DOWNLOAD_UI.EXPERT_TEXT)
            if mode=='dark':bg,fg=p.ACCENT_SOFT,p.TEXT_PRIMARY
            label.setStyleSheet(f'color:{fg};background:{bg};padding:6px;border-radius:5px;')
            self.grid.addWidget(label,i//3,i%3,Qt.AlignmentFlag.AlignLeft)
