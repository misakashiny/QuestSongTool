"""Native task status badges and progress bars; no per-row child widgets."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionProgressBar

from ..theme import get_palette


class QueueDelegate(QStyledItemDelegate):
    def __init__(self, browser):
        super().__init__(browser.table);self.browser = browser

    def paint(self, painter, option, index):
        if index.column() not in (1,2):return super().paint(painter,option,index)
        row = self.browser.model.rows[index.row()]
        palette = get_palette(self.browser.window.mode)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save();painter.setClipRect(option.rect)
        if selected:painter.fillRect(option.rect,QColor(palette.BG_HOVER))
        area = option.rect.adjusted(8,12,-8,-12)
        if index.column() == 1:
            state = row['state']
            color = palette.DANGER if state in ('failed','interrupted') else palette.SUCCESS if state in ('done','exists') else palette.ACCENT
            fill = QColor(color);fill.setAlpha(24)
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(fill);painter.drawRoundedRect(area,6,6)
            painter.setPen(QColor(color));painter.drawText(area,Qt.AlignmentFlag.AlignCenter,index.data())
        else:
            bar = QStyleOptionProgressBar();bar.rect = area
            bar.state = option.state | QStyle.StateFlag.State_Horizontal;bar.palette = option.palette
            bar.palette.setColor(QPalette.ColorRole.Highlight,QColor(palette.ACCENT))
            bar.palette.setColor(QPalette.ColorRole.Base,QColor(palette.BG_INPUT))
            bar.minimum = 0;bar.maximum = 100;bar.progress = max(0,min(100,round(row['progress']*100)))
            bar.text = f'{bar.progress}%';bar.textVisible = True;bar.textAlignment = Qt.AlignmentFlag.AlignCenter
            QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar,bar,painter,self.browser.table)
        painter.restore()
