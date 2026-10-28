"""Small cached Qt line icons, using the existing semantic icon vocabulary."""
from functools import lru_cache
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF


@lru_cache(maxsize=256)
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
        elif kind in ("back", "chevron_left"):
            painter.drawPolyline(QPolygonF([QPointF(14, 4), QPointF(6, 12), QPointF(14, 20)]))
            painter.drawLine(QPointF(7, 12), QPointF(21, 12))
        elif kind == "chevron_right":
            painter.drawPolyline(QPolygonF([QPointF(10, 4), QPointF(18, 12), QPointF(10, 20)]))
            painter.drawLine(QPointF(3, 12), QPointF(17, 12))
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
        elif kind in ('previous', 'next'):
            painter.setBrush(QColor(color))
            if kind == 'previous':
                painter.drawPolygon(QPolygonF([QPointF(18,4),QPointF(7,12),QPointF(18,20)]))
                painter.drawLine(QPointF(5,4),QPointF(5,20))
            else:
                painter.drawPolygon(QPolygonF([QPointF(6,4),QPointF(17,12),QPointF(6,20)]))
                painter.drawLine(QPointF(19,4),QPointF(19,20))
        elif kind == 'music':
            painter.drawPolyline(QPolygonF([QPointF(8,17),QPointF(8,5),QPointF(20,3),QPointF(20,15)]))
            painter.drawEllipse(QPointF(5,18),3,2.5);painter.drawEllipse(QPointF(17,16),3,2.5)
        elif kind in ('star', 'star_filled'):
            if kind == 'star_filled':painter.setBrush(QColor(color))
            points = []
            for i in range(10):
                angle = -math.pi/2+i*math.pi/5;radius = 10 if i % 2 == 0 else 4.5
                points.append(QPointF(12+radius*math.cos(angle),12+radius*math.sin(angle)))
            painter.drawPolygon(QPolygonF(points))
        elif kind == 'add':
            painter.drawRoundedRect(QRectF(4,3,16,18),2,2)
            painter.drawLine(QPointF(8,12),QPointF(16,12));painter.drawLine(QPointF(12,8),QPointF(12,16))
        elif kind == 'pause':
            painter.setBrush(QColor(color));painter.drawRoundedRect(QRectF(6,4,4,16),1,1);painter.drawRoundedRect(QRectF(14,4,4,16),1,1)
        elif kind == 'copy':
            painter.drawRoundedRect(QRectF(8,8,13,13),2,2)
            painter.drawPolyline(QPolygonF([QPointF(16,5),QPointF(16,3),QPointF(3,3),QPointF(3,16),QPointF(5,16)]))
        elif kind == 'window_minimize':
            painter.drawLine(QPointF(5,16),QPointF(19,16))
        elif kind == 'window_maximize':
            painter.drawRect(QRectF(5,5,14,14))
        elif kind == 'window_restore':
            painter.drawRect(QRectF(5,8,11,11))
            painter.drawPolyline(QPolygonF([QPointF(8,5),QPointF(19,5),QPointF(19,16)]))
        elif kind == 'close':
            painter.drawLine(QPointF(6,6),QPointF(18,18));painter.drawLine(QPointF(6,18),QPointF(18,6))
        elif kind == 'help':
            painter.drawEllipse(QRectF(2,2,20,20));painter.drawArc(QRectF(8,5,8,8),-90*16,270*16)
            painter.drawLine(QPointF(12,12),QPointF(12,14));painter.drawPoint(QPointF(12,18))
        elif kind == 'edit':
            painter.drawPolygon(QPolygonF([QPointF(4,16),QPointF(16,4),QPointF(20,8),QPointF(8,20),QPointF(3,21)]))
            painter.drawLine(QPointF(13,7),QPointF(17,11))
        elif kind == 'transfer':
            painter.drawPolyline(QPolygonF([QPointF(3,7),QPointF(21,7),QPointF(16,2)]))
            painter.drawPolyline(QPolygonF([QPointF(21,17),QPointF(3,17),QPointF(8,22)]))
        elif kind == 'refresh':
            painter.drawArc(QRectF(4,4,16,16),45*16,290*16)
            painter.drawPolyline(QPolygonF([QPointF(21,4),QPointF(21,10),QPointF(15,10)]))
        elif kind == 'list':
            for y in (6,12,18):
                painter.drawPoint(QPointF(3,y));painter.drawLine(QPointF(8,y),QPointF(21,y))
        elif kind == 'playlist':
            painter.drawRect(QRectF(3, 4, 13, 16))
            painter.drawLine(QPointF(7, 8), QPointF(13, 8))
            painter.drawLine(QPointF(7, 12), QPointF(13, 12))
            painter.drawLine(QPointF(7, 16), QPointF(11, 16))
            painter.drawEllipse(QPointF(18, 17), 3, 3)
            painter.drawLine(QPointF(21, 17), QPointF(21, 9))
        elif kind == 'link':
            painter.drawArc(QRectF(2, 8, 10, 8), 45*16, 270*16)
            painter.drawArc(QRectF(12, 8, 10, 8), 225*16, 270*16)
            painter.drawLine(QPointF(8, 12), QPointF(16, 12))
        elif kind == 'calendar':
            painter.drawRoundedRect(QRectF(4, 5, 16, 16), 2, 2)
            painter.drawLine(QPointF(4, 9), QPointF(20, 9))
            painter.drawLine(QPointF(8, 3), QPointF(8, 7));painter.drawLine(QPointF(16, 3), QPointF(16, 7))
            painter.drawPoint(QPointF(9, 14));painter.drawPoint(QPointF(14, 14))
        elif kind == 'save':
            painter.drawRoundedRect(QRectF(4, 3, 16, 18), 2, 2)
            painter.drawRect(QRectF(8, 4, 8, 5));painter.drawRect(QRectF(8, 14, 8, 7))
        elif kind == 'check_box':
            painter.drawRoundedRect(QRectF(3, 3, 18, 18), 3, 3)
            painter.drawPolyline(QPolygonF([QPointF(7, 12), QPointF(11, 16), QPointF(18, 8)]))
        elif kind == 'shuffle':
            painter.drawLine(QPointF(4, 7), QPointF(8, 7));painter.drawLine(QPointF(8, 7), QPointF(16, 17));painter.drawLine(QPointF(16, 17), QPointF(20, 17))
            painter.drawPolyline(QPolygonF([QPointF(16, 14), QPointF(20, 17), QPointF(16, 20)]))
            painter.drawLine(QPointF(4, 17), QPointF(8, 17));painter.drawLine(QPointF(8, 17), QPointF(16, 7));painter.drawLine(QPointF(16, 7), QPointF(20, 7))
            painter.drawPolyline(QPolygonF([QPointF(17, 4), QPointF(20, 7), QPointF(17, 10)]))
        elif kind == 'repeat':
            painter.drawPolyline(QPolygonF([QPointF(7, 7), QPointF(18, 7), QPointF(18, 17), QPointF(7, 17)]))
            painter.drawPolyline(QPolygonF([QPointF(7, 4), QPointF(4, 7), QPointF(7, 10)]))
            painter.drawPolyline(QPolygonF([QPointF(17, 14), QPointF(20, 17), QPointF(17, 20)]))
        elif kind == 'grid':
            for x in (3,14):
                for y in (3,14):painter.drawRect(QRectF(x,y,7,7))
        elif kind == 'trash':
            painter.drawPolyline(QPolygonF([QPointF(6,7),QPointF(7,21),QPointF(17,21),QPointF(18,7)]))
            painter.drawLine(QPointF(3,6),QPointF(21,6));painter.drawLine(QPointF(9,3),QPointF(15,3))
            for x in (10,14):painter.drawLine(QPointF(x,10),QPointF(x,17))
        elif kind == 'heart':
            from PySide6.QtGui import QPainterPath
            path=QPainterPath();path.moveTo(12,21)
            path.cubicTo(1,13,1,4,7,4);path.cubicTo(10,4,11,5,12,7)
            path.cubicTo(13,5,14,4,17,4);path.cubicTo(23,4,23,13,12,21)
            painter.drawPath(path)
        elif kind == "volume":
            painter.drawPolygon(QPolygonF([QPointF(3, 9), QPointF(7, 9), QPointF(12, 5),
                                          QPointF(12, 19), QPointF(7, 15), QPointF(3, 15)]))
            painter.drawArc(QRectF(9, 6, 10, 12), -60*16, 120*16)
            painter.drawArc(QRectF(9, 3, 14, 18), -60*16, 120*16)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
