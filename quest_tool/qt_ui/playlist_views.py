"""Virtualized playlist cards and song rows using the shared Qt theme/cache."""
from PySide6.QtCore import QAbstractListModel, QAbstractTableModel, QEvent, QModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from ..messages import tr
from ..theme import DESKTOP, DOWNLOAD_UI, PLAYLIST_UI, RADIUS, SPACE, get_palette
from .icons import line_icon
from .song_model import Song


def playlist_count(row, key='qt.catalog.count'):
    return tr('qt.catalog.count_pending') if row['total'] is None else tr(key, count=row['total'])


class PlaylistCards(QAbstractListModel):
    def __init__(self, parent):
        super().__init__(parent);self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return row['title']+'\n'+row['author']+'\n'+playlist_count(row)+('\n'+tr('qt.catalog.dynamic') if row.get('dynamic') else '')
        return None

    def replace(self, rows):
        super().beginResetModel();self.rows = rows;super().endResetModel()


class PlaylistCardDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view);self.view = view

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is None:
            return
        p = get_palette(self.view.controller.window.mode)
        rect = option.rect.adjusted(SPACE.XS, SPACE.XS, -SPACE.XS, -SPACE.XS)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save();painter.setClipRect(option.rect);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(p.ACCENT if selected else p.BORDER), 2 if selected else 1))
        painter.setBrush(QColor(p.BG_HOVER if hovered else p.BG_CARD));painter.drawRoundedRect(rect, RADIUS.CONTROL, RADIUS.CONTROL)
        art = QRect(rect.left()+SPACE.M, rect.top()+SPACE.M, rect.width()-SPACE.M*2, self.view.art_height)
        pixmap = self.view.controller.cache.image(row['coverURL'], art.size(), self.view.devicePixelRatioF())
        if pixmap is not None:
            painter.drawPixmap(art, pixmap)
        else:
            painter.fillRect(art, QColor(p.BG_INPUT));painter.setPen(QColor(p.TEXT_MUTED))
            painter.drawText(art, Qt.AlignmentFlag.AlignCenter, tr('qt.catalog.cover'))
        y = art.bottom()+SPACE.S
        rating = '—' if row['rating'] is None else f"{row['rating']*100:.0f}%"
        stats = tr('qt.catalog.card_stats_pending', rating=rating) if row['total'] is None else tr('qt.catalog.card_stats', count=row['total'], rating=rating)
        lines = (row['title'] or tr('qt.playlists.unnamed'), row['author'], stats)
        for i, text in enumerate(lines):
            font = painter.font();font.setBold(i == 0);painter.setFont(font)
            painter.setPen(QColor(p.TEXT_PRIMARY if i == 0 else p.TEXT_SECONDARY))
            box = QRect(rect.left()+SPACE.M, y+i*DESKTOP.LIBRARY_TEXT_H, rect.width()-SPACE.M*2, DESKTOP.LIBRARY_TEXT_H)
            painter.drawText(box, Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, box.width()))
        badge_text = tr('qt.catalog.dynamic') if row.get('dynamic') else playlist_count(row)
        badge_width = min(art.width()-SPACE.M, max(72, painter.fontMetrics().horizontalAdvance(badge_text)+SPACE.L*2))
        badge = QRect(art.right()-badge_width-SPACE.S,art.top()+SPACE.S,badge_width,26)
        painter.setPen(Qt.PenStyle.NoPen);badge_color = QColor(p.ACCENT);badge_color.setAlpha(225);painter.setBrush(badge_color);painter.drawRoundedRect(badge,8,8)
        painter.setPen(QColor(p.ACCENT_TEXT));font = painter.font();font.setBold(True);painter.setFont(font)
        painter.drawText(badge,Qt.AlignmentFlag.AlignCenter,badge_text)
        painter.restore()

    def sizeHint(self, option, index):
        return self.view.gridSize()


class PlaylistCardView(QListView):
    def __init__(self, controller):
        super().__init__();self.controller = controller
        super().setViewMode(QListView.ViewMode.IconMode);super().setResizeMode(QListView.ResizeMode.Adjust)
        super().setMovement(QListView.Movement.Static);super().setUniformItemSizes(True);super().setWordWrap(True)
        super().setMouseTracking(True);super().setItemDelegate(PlaylistCardDelegate(self));super().setSpacing(0)
        self.reflow()

    def reflow(self):
        width = max(1, self.viewport().width()-SPACE.S)
        columns = max(1, min(5, width//230))
        cell = max(150,width//columns)
        self.art_height = min(196,cell-SPACE.M*2-SPACE.XS*2)
        super().setGridSize(QSize(cell,self.art_height+DESKTOP.LIBRARY_TEXT_H*3+SPACE.XXL+SPACE.S))

    def resizeEvent(self, event):
        super().resizeEvent(event);self.reflow()
        if hasattr(self.controller, 'art_timer'):
            self.controller.schedule_covers()


class PlaylistSongDelegate(QStyledItemDelegate):
    """Paint compact song rows to match the playlist detail concept.

    The model keeps selection and download semantics while exposing the richer
    reference columns in the detail view so the row remains information-dense.
    Rich cells are rendered here instead of creating widgets per
    song, which keeps scrolling and cover updates bounded.
    """
    def __init__(self, table):
        super().__init__(table);self.table = table;self.controller = table.model().controller

    def _song(self, index):
        value = index.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, Song) else None

    def _palette(self):
        mode = self.controller.window.mode.currentData() if hasattr(self.controller.window.mode,'currentData') else self.controller.window.mode
        return get_palette(mode)

    @staticmethod
    def _text(painter, rect, text, color, *, bold=False, size=None, align=Qt.AlignmentFlag.AlignVCenter):
        painter.save()
        font = painter.font();font.setBold(bold)
        if size is not None:font.setPixelSize(size)
        painter.setFont(font);painter.setPen(QColor(color))
        painter.drawText(rect, align, painter.fontMetrics().elidedText(str(text), Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()

    @staticmethod
    def play_rect(rect):
        return QRect(rect.left()+8, rect.center().y()-18, 36, 36)

    def paint(self, painter, option, index):
        song = self._song(index)
        if song is None:
            return super().paint(painter, option, index)
        p = self._palette();rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = self.table.hover_row == index.row()
        painter.save();painter.setFont(option.font);painter.setClipRect(rect);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = p.ROW_SELECTED if song.key in self.controller.checked or selected else p.BG_HOVER if hovered else p.BG_CARD
        painter.fillRect(rect, QColor(background))
        painter.setPen(QPen(QColor(p.ROW_DIVIDER), 1));painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        col = index.column();pad = SPACE.M
        if col == 0:
            size = 20;box = QRect(rect.center().x()-size//2,rect.center().y()-size//2,size,size)
            painter.setBrush(QColor(p.ACCENT if song.key in self.controller.checked else p.BG_CARD))
            painter.setPen(QPen(QColor(p.ACCENT if song.key in self.controller.checked else p.BORDER_STRONG), 1.4));painter.drawRoundedRect(box,4,4)
            if song.key in self.controller.checked:
                painter.setPen(QPen(QColor(p.ACCENT_TEXT), 2));painter.drawLine(box.left()+4,box.center().y(),box.center().x()-1,box.bottom()-4);painter.drawLine(box.center().x()-1,box.bottom()-4,box.right()-3,box.top()+4)
        elif col == 1:
            self._text(painter, rect, str(index.row() + 1), p.TEXT_SECONDARY, align=Qt.AlignmentFlag.AlignCenter)
        elif col == 2:
            art_size = min(PLAYLIST_UI.ROW_ART, rect.height()-16);art = QRect(rect.left()+pad,rect.center().y()-art_size//2,art_size,art_size)
            image = self.controller.cache.image(song.cover_url, QSize(art_size,art_size), self.table.devicePixelRatioF())
            if image is not None:
                painter.save();clip = QPainterPath();clip.addRoundedRect(QRectF(art), 5, 5);painter.setClipPath(clip);painter.drawPixmap(art,image);painter.restore()
            else:
                painter.fillRect(art,QColor(p.BG_INPUT));self._text(painter,art,'♪',p.TEXT_MUTED,align=Qt.AlignmentFlag.AlignCenter)
            title = QRect(art.right()+12,rect.top(),max(0,rect.right()-art.right()-20),rect.height())
            self._text(painter,title,song.title,p.TEXT_PRIMARY)
        elif col == 3:
            self._text(painter,rect.adjusted(8,0,-8,0),song.artist,p.TEXT_PRIMARY)
        elif col == 4:
            badge = QRect(rect.left()+8,rect.center().y()-15,min(rect.width()-16,painter.fontMetrics().horizontalAdvance(song.mapper)+24),30)
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(DOWNLOAD_UI.EXPERT_PLUS_BG if p.name == 'light' else p.ACCENT_SOFT));painter.drawRoundedRect(badge,6,6)
            self._text(painter,badge.adjusted(10,0,-10,0),song.mapper,DOWNLOAD_UI.EXPERT_PLUS_TEXT if p.name == 'light' else p.ACCENT,align=Qt.AlignmentFlag.AlignVCenter)
        elif col == 5:
            parts = song.diffs[0].split('·') if song.diffs else ()
            difficulty = parts[1].strip() if len(parts) > 1 else '—'
            badge = QRect(rect.left()+pad,rect.center().y()-12,min(rect.width()-pad*2, max(58,painter.fontMetrics().horizontalAdvance(difficulty)+SPACE.L*2)),24)
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(DOWNLOAD_UI.EXPERT_PLUS_BG if 'Plus' in difficulty else DOWNLOAD_UI.EXPERT_BG));painter.drawRoundedRect(badge,8,8)
            self._text(painter,badge,difficulty,DOWNLOAD_UI.EXPERT_PLUS_TEXT if 'Plus' in difficulty else DOWNLOAD_UI.EXPERT_TEXT,bold=True,align=Qt.AlignmentFlag.AlignCenter)
        elif col == 6:
            self._text(painter,rect,song.bpm_text,p.TEXT_PRIMARY,align=Qt.AlignmentFlag.AlignCenter)
        elif col == 7:
            self._text(painter,rect,song.duration,p.TEXT_PRIMARY,align=Qt.AlignmentFlag.AlignCenter)
        elif col == 8:
            star = QRect(rect.left()+8,rect.center().y()-9,18,18)
            line_icon('star_filled',p.STAR).paint(painter,star)
            self._text(painter,rect.adjusted(32,0,-4,0),song.rating_text,p.TEXT_PRIMARY)
        elif col == 9:
            play = self.play_rect(rect);enabled = bool(song.preview_url and self.controller.window.preview_browser)
            preview = self.controller.window.preview_browser
            active = bool(preview and getattr(preview,'request_id',None) is not None and preview.song and preview.song.key == song.key)
            total = getattr(preview,'total',0) if active else 0
            duration = f'0:{int(total):02}' if total else '≤30s' if song.preview_url else '—'
            self._text(painter,QRect(play.right()+10,rect.top(),rect.right()-play.right()-14,rect.height()),duration,p.TEXT_SECONDARY)
            painter.setBrush(QColor(p.ACCENT_SOFT if active else p.BG_CARD));painter.setPen(QPen(QColor(p.ACCENT if active else p.BORDER_STRONG),1));painter.drawEllipse(play)
            line_icon('stop' if active else 'play',p.ACCENT if active else p.TEXT_PRIMARY if enabled else p.TEXT_MUTED).paint(painter,play.adjusted(9,9,-9,-9))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        song = self._song(index)
        if song is None:return False
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            if index.column() == 0:
                current = model.data(index,Qt.ItemDataRole.CheckStateRole)
                value = Qt.CheckState.Unchecked if current == Qt.CheckState.Checked else Qt.CheckState.Checked
                return bool(model.setData(index,value,Qt.ItemDataRole.CheckStateRole))
            if index.column() == 9 and self.play_rect(option.rect).contains(point):
                self.table.setCurrentIndex(index)
                self.controller.play_selected()
                return True
        return super().editorEvent(event,model,option,index)


class PlaylistSongs(QAbstractTableModel):
    def __init__(self, controller):
        super().__init__(controller);self.controller = controller;self.rows = [];self.headers = None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 10

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ('', '#', tr('qt.playlists.song'), tr('qt.local.author'), tr('qt.playlists.mapper'), tr('qt.playlists.difficulty'), 'BPM', tr('qt.local.duration'), tr('qt.catalog.rating'), tr('qt.dl.preview'))[section]
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0 and self.rows[index.row()] is not None:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        song = self.rows[index.row()]
        if song is None:
            return tr('qt.catalog.unavailable') if role == Qt.ItemDataRole.DisplayRole and index.column() == 2 else None
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return Qt.CheckState.Checked if song.key in self.controller.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            return song
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 2:
            image = self.controller.cache.image(song.cover_url, QSize(DESKTOP.PLAYER_ART, DESKTOP.PLAYER_ART), 1)
            return QIcon(image) if image is not None else None
        if role == Qt.ItemDataRole.ToolTipRole:
            return song.title+'\n'+song.artist+'\n'+song.key+'\n'+' / '.join(song.diffs)
        if role == Qt.ItemDataRole.DisplayRole:
            return ('', str(index.row()+1), song.title, song.artist, song.mapper, song.diffs[0] if song.diffs else '—', song.bpm_text, song.duration, song.rating_text, '0:30')[index.column()]
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid() or index.column() != 0:
            return False
        song = self.rows[index.row()]
        if song is None:
            return False
        if value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value:
            if len(self.controller.checked) >= 200 and song.key not in self.controller.checked:
                return False
            self.controller.checked.add(song.key)
        else:
            self.controller.checked.discard(song.key)
        self.changed();self.controller.render();return True

    def replace(self, rows):
        super().beginResetModel();self.rows = [Song.from_result(row) if row else None for row in rows];super().endResetModel()

    def changed(self):
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows)-1, 9))
