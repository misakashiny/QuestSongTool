"""Local user ratings: vector stars, shared model state and committed updates."""
from functools import lru_cache
import math

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..messages import tr
from ..theme import DOWNLOAD, SPACE, get_palette

RATING_ROLE = int(Qt.ItemDataRole.UserRole) + 1


@lru_cache(maxsize=8)
def star_polygon(size):
    center = size / 2
    return QPolygonF([QPointF(center+math.cos(-math.pi/2+i*math.pi/5)*center*(1 if i%2==0 else .45),
                             center+math.sin(-math.pi/2+i*math.pi/5)*center*(1 if i%2==0 else .45))
                      for i in range(10)])


def paint_rating(painter, rect, value, palette, ink=None):
    painter.save()
    painter.setClipRect(rect)
    color = ink or QColor(palette.TEXT_MUTED)
    if value is None:
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, tr('qt.ratings.unknown_mark'))
    else:
        size = min(DOWNLOAD.RATING_STAR_SIZE, max(1, (rect.width()-4*DOWNLOAD.RATING_STAR_GAP)//5))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(rect.left(), rect.center().y()-size/2)
        shape = star_polygon(size)
        for index in range(5):
            painter.setPen(QColor(palette.STAR) if index < value else color)
            painter.setBrush(QColor(palette.STAR) if index < value else Qt.BrushStyle.NoBrush)
            painter.drawPolygon(shape)
            painter.translate(size+DOWNLOAD.RATING_STAR_GAP, 0)
    painter.restore()


class RatingDelegate(QStyledItemDelegate):
    def __init__(self, window, view):
        super().__init__(view)
        self.window = window
        self.view = view

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        super().initStyleOption(opt, index)
        opt.text = ''
        self.view.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, self.view)
        ink = opt.palette.highlightedText().color() if opt.state & QStyle.StateFlag.State_Selected else None
        paint_rating(painter, opt.rect.adjusted(SPACE.XS,0,-SPACE.XS,0), index.data(RATING_ROLE),
                     get_palette(self.window.mode.currentData()), ink)


class LibraryRatings(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.root = None
        self.sequence = None
        self.ready = False
        self.pending = False
        self.failed = False
        self.values = {}
        window.userdata.ratingsResult.connect(self.received)
        window.userdata.ratingConfirmed.connect(self.confirmed)
        window.rating_retry.clicked.connect(self.load)
        for view in (window.table, window.quest_table, window.missing_table):
            view.setItemDelegateForColumn(3, RatingDelegate(window, view))

    def reset(self):
        self.root = None; self.sequence = None
        self.ready = False; self.pending = False; self.failed = False; self.values = {}
        self.publish()

    def load(self):
        if self.window.closed or not self.window.local_ready: return
        self.root = str(self.window.target)
        self.ready = False; self.pending = True; self.failed = False; self.values = {}
        self.sequence = self.window.userdata.request(('local', self.root, ''), 'ratings')
        self.publish()

    def received(self, result):
        if self.window.closed or result['sequence'] != self.sequence: return
        if tuple(result['key']) != ('local', self.root, ''): return
        if not self.window.local_ready or self.root != str(self.window.target): return
        if result.get('skipped'): return
        self.pending = False; self.failed = result['failed']; self.ready = not self.failed
        if self.ready: self.values = result['ratings']
        self.publish()

    def confirmed(self, key, rating):
        if self.window.closed or not self.window.local_ready: return
        if tuple(key[:2]) != ('local',str(self.window.target)) or self.root != str(self.window.target): return
        self.values[key[2]] = rating
        self.publish(key[2])

    def publish(self, folder=None):
        window = self.window
        for model in (window.model, window.quest_model, window.missing_model):
            model.set_ratings(self.ready, self.values, folder)
        if hasattr(window, 'grid'): window.grid.viewport().update()
        self.render()

    def render(self):
        state = 'loading' if self.pending else 'failed' if self.failed else 'ready' if self.ready else 'idle'
        self.window.rating_state.setText(tr('qt.ratings.'+state))
        self.window.rating_retry.setVisible(self.failed)
        self.window.rating_retry.setEnabled(not self.pending)
