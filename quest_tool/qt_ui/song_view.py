"""Virtualized native view; cards are delegates, not nested widget trees."""
from functools import lru_cache

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from ..messages import tr
from ..theme import DOWNLOAD, RADIUS, SPACE, get_palette
from .icons import line_icon


@lru_cache(maxsize=64)
def sample_cover(key, mode):
    """Bounded cache of procedural fixture art; no files, network or image decode."""
    p = get_palette(mode)
    image = QPixmap(256, 256)
    image.fill(QColor(p.BG_INPUT))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(p.ACCENT), 10))
    seed = sum(map(ord, key)) % 40
    for offset in (0, 45, 90):
        painter.drawEllipse(30+offset//3, 25+seed+offset//4, 170-offset, 170-offset)
    painter.end()
    return image


class SongDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def controls(self, rect):
        size = DOWNLOAD.CONTROL
        right = rect.right()-SPACE.M-size+1
        bottom = rect.top()+(DOWNLOAD.ART_HEIGHT if self.view.covers else DOWNLOAD.CONTROL*2+SPACE.M*3)-SPACE.M
        play = QRect(right, bottom-size, size, size)
        check = play.translated(0, -size-SPACE.M)
        return check, play

    def paint(self, painter, option, index):
        song = index.data(Qt.ItemDataRole.UserRole)
        p = get_palette(self.view.mode)
        rect = option.rect.adjusted(SPACE.XS, SPACE.XS, -SPACE.XS, -SPACE.XS)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        current = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setBrush(QColor(p.BG_HOVER if hover and not current else p.BG_CARD))
        painter.setPen(QPen(QColor(p.ACCENT if current else p.BORDER), 2 if current else 1))
        painter.drawRoundedRect(rect, RADIUS.CONTROL, RADIUS.CONTROL)
        if self.view.covers:
            art = QRect(rect.left()+1, rect.top()+1, rect.width()-2, DOWNLOAD.ART_HEIGHT-SPACE.XS)
            text = QRect(rect.left()+SPACE.M, rect.top()+DOWNLOAD.ART_HEIGHT+SPACE.XS,
                         rect.width()-2*SPACE.M, 24)
        else:
            art = QRect(rect.left()+SPACE.XS, rect.top()+SPACE.XS, 56, 56)
            text = QRect(art.right()+SPACE.M, rect.top()+SPACE.XS,
                         max(30, rect.width()-art.width()-3*SPACE.M-2*DOWNLOAD.CONTROL-SPACE.M), 22)
        painter.save()
        art_clip = QPainterPath()
        art_clip.addRoundedRect(QRectF(art), RADIUS.CHIP, RADIUS.CHIP)
        painter.setClipPath(art_clip, Qt.ClipOperation.IntersectClip)
        if self.view.online:
            image = (self.view.cover_cache.image(song.cover_url, art.size(), self.view.devicePixelRatioF())
                     if self.view.cover_cache is not None else None)
            if image is not None:
                painter.drawPixmap(art, image)
            else:
                painter.fillRect(art, QColor(p.BG_INPUT))
                painter.setPen(QColor(p.TEXT_MUTED))
                painter.drawText(art.adjusted(4, 4, -DOWNLOAD.CONTROL-SPACE.L, -4),
                                 Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                 tr("qt.search.cover" if self.view.cover_cache is None else
                                    "qt.cover.loading" if song.cover_url in self.view.cover_pending else "media.no_cover"))
        else:
            painter.drawPixmap(art, sample_cover(song.key, self.view.mode))
        painter.restore()
        status = index.model().status_labels.get(song.key, '')
        if status and self.view.covers:
            painter.save()
            badge = QRect(art.left()+SPACE.S, art.top()+SPACE.S,
                          max(1, min(art.width()-SPACE.M*2, painter.fontMetrics().horizontalAdvance(status)+SPACE.L*2)),
                          painter.fontMetrics().height()+SPACE.S*2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(p.BG_CARD))
            painter.drawRoundedRect(badge, RADIUS.CHIP, RADIUS.CHIP)
            painter.setPen(QColor(p.TEXT_PRIMARY))
            painter.drawText(badge.adjusted(SPACE.S, 0, -SPACE.S, 0), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(status, Qt.TextElideMode.ElideRight, badge.width()-SPACE.S*2))
            painter.restore()
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(p.TEXT_PRIMARY))
        painter.drawText(text, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(song.title, Qt.TextElideMode.ElideRight, text.width()))
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(p.TEXT_SECONDARY))
        lines = (song.artist, tr("qt.songs.mapper", name=song.mapper)) if self.view.covers else (
                     f"{song.artist} · {song.duration} · {song.rating_text}"+(' · '+status if status else ''),)
        for line in lines:
            text.translate(0, 22)
            painter.drawText(text, Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, text.width()))
        if self.view.covers:
            text.translate(0,22)
            duration_width = painter.fontMetrics().horizontalAdvance(song.duration)+SPACE.M
            rating_rect = text.adjusted(0,0,-duration_width,0)
            painter.drawText(rating_rect,Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(
                song.rating_text,Qt.TextElideMode.ElideRight,rating_rect.width()))
            painter.drawText(text,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,song.duration)
        check, play = self.controls(rect)
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        check_background = QColor(p.BG_CARD)
        check_background.setAlpha(220)
        over_cover = self.view.covers
        painter.setBrush(QColor(p.ACCENT) if checked else check_background if over_cover else Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(p.ACCENT if checked else p.BORDER_STRONG), 2))
        painter.drawRoundedRect(check.adjusted(2, 2, -2, -2), RADIUS.CHIP, RADIUS.CHIP)
        if checked:
            painter.setPen(QPen(QColor(p.ACCENT_TEXT), 2))
            painter.drawLine(check.left()+8, check.center().y(), check.left()+14, check.bottom()-8)
            painter.drawLine(check.left()+14, check.bottom()-8, check.right()-7, check.top()+8)
        painter.setBrush(QColor(p.BG_CARD))
        painter.setPen(QPen(QColor(p.BORDER_STRONG), 1))
        painter.drawEllipse(play.adjusted(1, 1, -1, -1))
        active = self.view.preview_key == song.key
        line_icon("stop" if active else "play", p.ACCENT if active else p.TEXT_MUTED).paint(
            painter, play.adjusted(6, 6, -6, -6))
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(p.ACCENT), 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), RADIUS.CONTROL, RADIUS.CONTROL)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        toggle = event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            rect = option.rect.adjusted(SPACE.XS, SPACE.XS, -SPACE.XS, -SPACE.XS)
            if self.controls(rect)[1].contains(event.position().toPoint()) and self.view.preview_enabled:
                self.view.previewRequested.emit(index.data(Qt.ItemDataRole.UserRole))
                return True
            toggle = self.controls(rect)[0].contains(event.position().toPoint())
        if toggle:
            checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
            return model.setData(index, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked,
                                 Qt.ItemDataRole.CheckStateRole)
        return False

    def sizeHint(self, option, index):
        return self.view.cell_size()


class SongView(QListView):
    previewRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        super().setObjectName("songResults")
        self.covers = True
        self.columns = 3
        self.mode = "light"
        self.online = False
        self.preview_enabled = False
        self.preview_key = None
        self.cover_cache = None
        self.cover_pending = set()
        self.empty_text = ""
        super().setMouseTracking(True)
        super().setUniformItemSizes(True)
        super().setWrapping(True)
        super().setResizeMode(QListView.ResizeMode.Adjust)
        super().setMovement(QListView.Movement.Static)
        super().setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        super().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        super().setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        super().setItemDelegate(SongDelegate(self))
        self.configure_view(True, 3)

    def cell_size(self):
        width = max(1, super().viewport().width())
        # IconMode wraps the last cell when its right edge equals the viewport.
        return QSize(max(1, (width-1)//self.columns) if self.covers else width,
                     DOWNLOAD.CARD_HEIGHT+SPACE.M if self.covers else DOWNLOAD.CONTROL*2+SPACE.M*4)

    def configure_view(self, covers, columns):
        self.covers, self.columns = covers, columns
        super().setViewMode(QListView.ViewMode.IconMode if covers else QListView.ViewMode.ListMode)
        super().setWrapping(covers)
        super().setFlow(QListView.Flow.LeftToRight if covers else QListView.Flow.TopToBottom)
        super().setGridSize(self.cell_size())
        super().doItemsLayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        super().setGridSize(self.cell_size())

    def refresh_art(self, mode):
        self.mode = mode
        super().viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.empty_text and super().model() is not None and not super().model().rowCount():
            painter = QPainter(super().viewport())
            painter.setPen(QColor(get_palette(self.mode).TEXT_SECONDARY))
            painter.drawText(super().viewport().rect().adjusted(SPACE.XXL, SPACE.XXL, -SPACE.XXL, -SPACE.XXL),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)
            painter.end()
