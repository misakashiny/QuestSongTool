"""Small presentation widgets for the playlist reference; no service access."""
from PySide6.QtCore import QRect, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHeaderView, QLabel, QTableView

from ..theme import PLAYLIST_UI, get_palette


class RoundedCover(QLabel):
    """Clip real artwork, since a QLabel QSS radius does not clip its pixmap."""
    def paintEvent(self, event):
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        size = pixmap.deviceIndependentSize();size.scale(QSizeF(self.size()),Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF((self.width()-size.width())/2,(self.height()-size.height())/2,size.width(),size.height())
        clip = QPainterPath();clip.addRoundedRect(target, 9, 9)
        painter.setClipPath(clip)
        painter.drawPixmap(target.toRect(), pixmap)


class PlaylistInfoCard(QFrame):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'cover'):
            size = min(PLAYLIST_UI.COVER_MAX, max(180, self.width()-40), max(200, self.height()-456))
            if self.cover.width() != size:
                self.cover.setFixedSize(size, size)
                self.cover_changed()


class PlaylistHeader(QHeaderView):
    def __init__(self, table):
        super().__init__(Qt.Orientation.Horizontal, table)
        self.table = table
        super().setSectionsClickable(True)
        self.sectionClicked.connect(self.select_all)
        super().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        super().setFixedHeight(50)

    def select_all(self, column):
        if column != 0 or not self.table.isEnabled():
            return
        controller = self.table.model().controller
        keys = {song.key for song in controller.songs.rows if song}
        if keys and keys.issubset(controller.checked):
            controller.checked.difference_update(keys)
            controller.songs.changed();controller.render()
        else:
            controller.mark_page()

    def paintSection(self, painter, rect, index):
        super().paintSection(painter, rect, index)
        if index != 0 or not self.table.model():
            return
        controller = self.table.model().controller
        p = get_palette(controller.window.mode)
        keys = {song.key for song in controller.songs.rows if song}
        checked = bool(keys) and keys.issubset(controller.checked)
        partial = bool(keys.intersection(controller.checked)) and not checked
        box = QRect(rect.center().x()-10, rect.center().y()-10, 20, 20)
        painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(p.ACCENT if checked or partial else p.BG_CARD))
        painter.setPen(QPen(QColor(p.ACCENT if checked or partial else p.BORDER_STRONG), 1.2))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QPen(QColor(p.ACCENT_TEXT), 2))
        if checked:
            painter.drawLine(box.left()+4, box.center().y(), box.left()+8, box.bottom()-5)
            painter.drawLine(box.left()+8, box.bottom()-5, box.right()-3, box.top()+5)
        elif partial:
            painter.drawLine(box.left()+4, box.center().y(), box.right()-4, box.center().y())
        painter.restore()


class PlaylistTable(QTableView):
    """Resize native columns and repaint only the previous/current hover rows."""
    def __init__(self):
        super().__init__()
        self.hover_row = -1
        super().setMouseTracking(True)
        super().setHorizontalHeader(PlaylistHeader(self))
        super().setObjectName('playlistSongs')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.model():
            return
        width = max(900, self.viewport().width())
        # Keep metadata separate without squeezing the title to a few letters.
        widths = {0: 46, 1: 42, 3: max(95, int(width*.10)),
                  4: max(120, int(width*.16)), 6: 70, 7: 72,
                  8: 100, 9: 116}
        for column, size in widths.items():
            self.setColumnWidth(column, size)
        self.setColumnWidth(2, max(220, width-sum(widths.values())-4))

    def mouseMoveEvent(self, event):
        old = self.hover_row
        self.hover_row = super().indexAt(event.position().toPoint()).row()
        if old != self.hover_row:
            for row in (old, self.hover_row):
                if row >= 0:
                    rect = super().visualRect(self.model().index(row, 0))
                    self.viewport().update(QRect(0, rect.y(), self.viewport().width(), rect.height()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover_row = -1;self.viewport().update()
        super().leaveEvent(event)
