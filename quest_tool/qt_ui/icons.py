"""Small cached Qt line icons, using the existing semantic icon vocabulary."""
from functools import lru_cache
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF


@lru_cache(maxsize=32)
def line_icon(kind, color):
    icon = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(24*scale, 24*scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if kind == "download":
            painter.drawLine(QPointF(12, 3), QPointF(12, 15))
            painter.drawPolyline(QPolygonF([QPointF(7, 10), QPointF(12, 15), QPointF(17, 10)]))
            painter.drawPolyline(QPolygonF([QPointF(4, 16), QPointF(4, 21), QPointF(20, 21), QPointF(20, 16)]))
        elif kind == "check":
            painter.drawPolyline(QPolygonF([QPointF(5,12),QPointF(10,17),QPointF(20,7)]))
        elif kind == "chevron":
            painter.drawPolyline(QPolygonF([QPointF(6, 9), QPointF(12, 15), QPointF(18, 9)]))
        elif kind == "search":
            painter.drawEllipse(QPointF(10, 10), 6, 6)
            painter.drawLine(QPointF(14.5, 14.5), QPointF(21, 21))
        elif kind == "folder":
            painter.drawPolyline(QPolygonF([QPointF(3, 7), QPointF(3, 4), QPointF(10, 4),
                QPointF(13, 7), QPointF(21, 7), QPointF(21, 20), QPointF(3, 20), QPointF(3, 7), QPointF(21, 7)]))
        elif kind == "filter":
            painter.drawPolygon(QPolygonF([QPointF(3, 4), QPointF(21, 4), QPointF(14, 12),
                                          QPointF(14, 19), QPointF(10, 21), QPointF(10, 12)]))
        elif kind == "settings":
            painter.drawEllipse(QPointF(12, 12), 7, 7)
            painter.drawEllipse(QPointF(12, 12), 2.5, 2.5)
            for index in range(8):
                angle = index * math.pi / 4
                painter.drawLine(QPointF(12+7*math.cos(angle), 12+7*math.sin(angle)),
                                 QPointF(12+10*math.cos(angle), 12+10*math.sin(angle)))
        elif kind == "play":
            painter.setBrush(QColor(color))
            painter.drawPolygon(QPolygonF([QPointF(7, 4), QPointF(20, 12), QPointF(7, 20)]))
        elif kind == "stop":
            painter.setBrush(QColor(color))
            painter.drawRect(QRectF(6, 6, 12, 12))
        elif kind == "volume":
            painter.drawPolygon(QPolygonF([QPointF(3, 9), QPointF(7, 9), QPointF(12, 5),
                                          QPointF(12, 19), QPointF(7, 15), QPointF(3, 15)]))
            painter.drawArc(QRectF(9, 6, 10, 12), -60*16, 120*16)
            painter.drawArc(QRectF(9, 3, 14, 18), -60*16, 120*16)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
