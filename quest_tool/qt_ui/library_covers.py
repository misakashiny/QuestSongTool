"""Local cover reads on a message-only worker; virtual Qt cards on the UI thread."""
from pathlib import Path
import io
import warnings

from PIL import Image, ImageOps

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPen
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from ..downloads import check_cancel
from ..fsapi import LocalFS, Location
from ..messages import tr
from .library_ratings import RATING_ROLE, paint_rating
from ..song_search import SearchSession
from ..theme import DESKTOP, DOWNLOAD, RADIUS, SPACE, get_palette
from .cover_browser import CoverCache
from .icons import line_icon


def local_cover(path):
    """Apply the online cover service's byte/dimension/PNG policy to local files."""
    with path.open('rb') as stream:data = stream.read(5*1024*1024+1)
    if len(data)>5*1024*1024:raise ValueError('cover bytes')
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            if source.width>4096 or source.height>4096:raise ValueError('cover dimensions')
            image = ImageOps.fit(source.convert('RGB'),(256,256))
    buffer = io.BytesIO();image.save(buffer,format='PNG');return buffer.getvalue()


class LocalCoverClient:
    def __init__(self, tasks):
        self.tasks = tasks

    def search(self, items, epoch, _order, cancel):
        for root, folder in items:
            check_cancel(cancel)
            data = None
            fs = LocalFS(Location('local', root))
            try:
                base = Path(root).resolve()
                directory = (base/folder).resolve()
                if not directory.is_relative_to(base):raise ValueError('cover folder')
                name = fs.cover_name(folder)
                if name:
                    path = (directory/name).resolve()
                    if not path.is_relative_to(directory):raise ValueError('cover path')
                    stat = path.stat()
                    if stat.st_size > 5*1024*1024:raise ValueError('cover bytes')
                    data = local_cover(path)
            except (OSError, ValueError, RuntimeError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                check_cancel(cancel)
            finally:
                fs.close()
            check_cancel(cancel)
            self.tasks.emit('local_cover', payload={'epoch':epoch, 'folder':folder, 'data':data})
        return {}


class LocalCoverPresenter(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.cache = CoverCache()
        self.session = SearchSession()
        self.session.client = LocalCoverClient(self.session.tasks)
        self.epoch = 0
        self.pending = set()
        self.failed = set()
        self.visible = set()
        self.closed = False
        self.generation = None
        self.timer = QTimer(self);self.timer.setInterval(40);self.timer.timeout.connect(self.poll)
        self.debounce = QTimer(self);self.debounce.setSingleShot(True);self.debounce.setInterval(60)
        self.debounce.timeout.connect(self.request_visible)
        self.list_viewport = window.table.viewport()
        self.list_viewport.installEventFilter(self)

    def eventFilter(self, watched, event):
        if self.closed:return False
        if watched is self.list_viewport and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):self.schedule()
        return False

    def reset(self):
        self.epoch += 1;self.session.cancel();self.pending.clear();self.failed.clear();self.visible.clear()
        self.cache = CoverCache();self.schedule()

    def schedule(self, *_args):
        if not self.closed:self.debounce.start()

    def request_visible(self):
        view = self.window.grid if self.window.grid.isVisible() else self.window.table
        if self.closed or not view.isVisible() or self.window.target is None:return
        keys = set()
        if view is self.window.grid:
            cell = view.cell_size()
            columns = max(1,view.viewport().width()//cell.width())
            start = max(0,(view.verticalScrollBar().value()//cell.height())*columns-columns)
        else:
            start = max(0,view.rowAt(0))
        for row in range(start,min(self.window.proxy.rowCount(),start+64)):
            index = self.window.proxy.index(row, 0)
            if view.visualRect(index).intersects(view.viewport().rect()):
                keys.add(index.data(Qt.ItemDataRole.UserRole)['folder'])
                if len(keys) >= 64:break
        if keys == self.visible:return
        self.visible = keys
        self.failed.intersection_update(keys)
        self.epoch += 1;self.session.cancel();self.pending.clear()
        items = [(str(self.window.target), key) for key in keys if key not in self.cache.sources and key not in self.failed]
        self.pending = {folder for _root, folder in items}
        if items:self.generation=self.session.request(items, self.epoch, '');self.timer.start()

    def poll(self):
        if self.closed:return
        for message in self.session.tasks.drain():
            data = message.payload
            if message.kind=='search_error' and data.get('generation')==self.generation:
                self.failed.update(self.pending);self.pending.clear()
                continue
            if message.kind != 'local_cover' or data.get('epoch') != self.epoch:continue
            key = data['folder'];self.pending.discard(key)
            if not data['data'] or not self.cache.put(key, data['data']):self.failed.add(key)
            self.window.grid.viewport().update()
            self.window.table.viewport().update()
            self.window.missing_table.viewport().update()
        if not self.pending:self.timer.stop()

    def close(self):
        self.list_viewport.removeEventFilter(self)
        self.closed = True;self.epoch += 1;self.timer.stop();self.debounce.stop();self.session.close()

    def wait_closed(self):
        for thread in tuple(self.session.tasks._threads):thread.join()


class LibraryTable(QTableView):
    """Artist is presented under the title; preserve its legacy saved width."""
    def __init__(self):
        super().__init__()
        self.artist_width = DESKTOP.LIBRARY_COLUMNS[1]

    def setColumnWidth(self, column, width):
        if column == 1:self.artist_width = width
        super().setColumnWidth(column, width)

    def saved_widths(self):
        return [super().columnWidth(0), self.artist_width, super().columnWidth(2)]


class LibraryRowDelegate(QStyledItemDelegate):
    """Use native item painting/hit targets, adding only cached decoration."""
    def __init__(self, window, view, origin):
        super().__init__(view)
        self.window, self.view, self.origin = window, view, origin

    def subtitle(self, item):
        artist = item.get('author') or tr('search.unknown')
        origin = item.get('origin') if self.origin=='missing' else self.origin
        return tr('qt.library.artist_bpm',artist=artist,bpm=f"{item['bpm']:g}") if origin=='quest' and item.get('bpm',0)>0 else artist

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        item = index.data(Qt.ItemDataRole.UserRole)
        if not item:return
        option.text = item['title']+'\n'+self.subtitle(item)
        origin = item.get('origin') if self.origin == 'missing' else self.origin
        size = QSize(DESKTOP.LIBRARY_ROW_ART, DESKTOP.LIBRARY_ROW_ART)
        image = (self.window.covers.cache.image(item['folder'], size, self.view.devicePixelRatioF())
                 if origin == 'local' and hasattr(self.window, 'covers') else None)
        if origin=='quest' and hasattr(self.window,'quest_media'):
            image = self.window.quest_media.cache.image(item['folder'],size,self.view.devicePixelRatioF())
        option.icon = QIcon(image) if image is not None else line_icon('folder', get_palette(self.window.mode.currentData()).TEXT_MUTED)
        option.decorationSize = size
        option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration

    def paint(self, painter, option, index):
        rendered = QStyleOptionViewItem(option)
        self.initStyleOption(rendered, index)
        item = index.data(Qt.ItemDataRole.UserRole)
        if not item:return
        style = self.view.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, rendered, self.view)
        rendered.text = ''
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, rendered, painter, self.view)
        painter.save();painter.setClipRect(text_rect)
        font = QFont(option.font);font.setBold(True);painter.setFont(font)
        height = painter.fontMetrics().height()
        title = QRect(text_rect.left()+SPACE.XS, text_rect.center().y()-height,
                      max(0,text_rect.width()-SPACE.M), height)
        palette = get_palette(self.window.mode.currentData())
        painter.setPen(QColor(palette.TEXT_PRIMARY))
        painter.drawText(title,Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(
            item['title'],Qt.TextElideMode.ElideRight,title.width()))
        font.setBold(False);painter.setFont(font);painter.setPen(QColor(palette.TEXT_SECONDARY))
        painter.drawText(title.translated(0,height),Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(
            self.subtitle(item),Qt.TextElideMode.ElideRight,title.width()))
        painter.restore()


class LibraryDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def check_rect(self, rect):
        return QRect(rect.right()-DOWNLOAD.CONTROL-SPACE.M, rect.top()+SPACE.M, DOWNLOAD.CONTROL, DOWNLOAD.CONTROL)

    def content_rects(self, rect):
        """One geometry calculation for drawing and narrow/large-font checks."""
        art = QRect(rect.left()+SPACE.S,rect.top()+SPACE.S,rect.width()-SPACE.S*2,DOWNLOAD.ART_HEIGHT)
        height = self.view.text_row_height()
        title = QRect(art.left(),art.bottom()+1+SPACE.S,art.width(),height)
        author = title.translated(0,height)
        rating = author.translated(0,height)
        duration = QRect(rating)
        if self.view.metadata_stacked(art.width()):
            duration.translate(0,height)
        else:
            width = self.view.duration_width()
            duration.setLeft(duration.right()-width+1)
            rating.setRight(duration.left()-SPACE.S-1)
        return art,title,author,rating,duration

    def paint(self, painter, option, index):
        item = index.data(Qt.ItemDataRole.UserRole)
        rect = option.rect.adjusted(SPACE.XS, SPACE.XS, -SPACE.XS, -SPACE.XS)
        p = get_palette(self.view.window.mode.currentData())
        painter.save();painter.setClipRect(option.rect)
        painter.setBrush(QColor(p.BG_SELECTED if option.state & QStyle.StateFlag.State_Selected else p.BG_CARD))
        painter.setPen(QPen(QColor(p.ACCENT if option.state & QStyle.StateFlag.State_MouseOver else p.BORDER)))
        painter.drawRoundedRect(rect, RADIUS.CONTROL, RADIUS.CONTROL)
        art,title,author,rating,duration = self.content_rects(rect)
        cache = self.view.window.covers.cache
        image = cache.image(item['folder'], art.size(), self.view.devicePixelRatioF())
        if image is not None:painter.drawPixmap(art,image)
        else:
            painter.fillRect(art,QColor(p.BG_INPUT));painter.setPen(QColor(p.TEXT_MUTED))
            painter.drawText(art,Qt.AlignmentFlag.AlignCenter,tr('media.no_cover'))
        font = QFont(painter.font());heading = QFont(font);heading.setBold(True)
        painter.setFont(heading)
        painter.setPen(QColor(p.TEXT_PRIMARY))
        painter.drawText(title,Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(item['title'],Qt.TextElideMode.ElideRight,title.width()))
        painter.setFont(font);painter.setPen(QColor(p.TEXT_SECONDARY))
        painter.drawText(author,Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(item['author'] or tr('search.unknown'),Qt.TextElideMode.ElideRight,author.width()))
        paint_rating(painter,rating,index.data(RATING_ROLE),p)
        seconds = item.get('duration',0)
        value = f'{int(seconds)//60}:{int(seconds)%60:02}' if seconds>0 else tr('search.unknown')
        painter.setPen(QColor(p.TEXT_MUTED))
        painter.drawText(duration,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(value,Qt.TextElideMode.ElideLeft,duration.width()))
        check = self.check_rect(rect).adjusted(2,2,-2,-2)
        checked = index.data(Qt.ItemDataRole.CheckStateRole)==Qt.CheckState.Checked
        painter.setBrush(QColor(p.ACCENT if checked else p.BG_CARD))
        painter.setPen(QPen(QColor(p.ACCENT if checked else p.BORDER_STRONG),2))
        painter.drawRoundedRect(check,RADIUS.CHIP,RADIUS.CHIP)
        if checked:
            painter.setPen(QPen(QColor(p.ACCENT_TEXT),2))
            painter.drawLine(check.left()+6,check.center().y(),check.left()+11,check.bottom()-6)
            painter.drawLine(check.left()+11,check.bottom()-6,check.right()-5,check.top()+6)
        painter.restore()

    def editorEvent(self,event,model,option,index):
        toggle = event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Space
        if event.type()==QEvent.Type.MouseButtonRelease and event.button()==Qt.MouseButton.LeftButton:
            toggle = self.check_rect(option.rect.adjusted(SPACE.XS,SPACE.XS,-SPACE.XS,-SPACE.XS)).contains(event.position().toPoint())
        if toggle:
            checked = index.data(Qt.ItemDataRole.CheckStateRole)==Qt.CheckState.Checked
            return model.setData(index,Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
        return False

    def sizeHint(self,option,index):return self.view.cell_size()


class LibraryGrid(QListView):
    def __init__(self,window):
        super().__init__()
        self.window = window
        super().setViewMode(QListView.ViewMode.IconMode);super().setResizeMode(QListView.ResizeMode.Adjust)
        super().setMovement(QListView.Movement.Static);super().setUniformItemSizes(True)
        super().setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        super().setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        super().setMouseTracking(True);super().setItemDelegate(LibraryDelegate(self))

    def cell_size(self):
        width = max(1,super().viewport().width())
        columns = max(1,width//DESKTOP.LIBRARY_COVER_MIN)
        cell_width = max(1,(width-1)//columns)
        art_width = cell_width-SPACE.XS*2-SPACE.S*2
        rows = 4 if self.metadata_stacked(art_width) else 3
        return QSize(cell_width,DOWNLOAD.ART_HEIGHT+SPACE.XS*2+SPACE.S*3+rows*self.text_row_height())

    def text_row_height(self):
        return max(DESKTOP.LIBRARY_TEXT_H,super().fontMetrics().height()+SPACE.XS)

    def duration_width(self):
        metrics = super().fontMetrics()
        return max(metrics.horizontalAdvance('999:59'),metrics.horizontalAdvance(tr('search.unknown')))+SPACE.S

    def metadata_stacked(self,width):
        rating_width = DOWNLOAD.RATING_STAR_SIZE*5+DOWNLOAD.RATING_STAR_GAP*4
        return width<rating_width+SPACE.S+self.duration_width()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange,QEvent.Type.LanguageChange):
            super().setGridSize(self.cell_size())

    def resizeEvent(self,event):
        super().resizeEvent(event);super().setGridSize(self.cell_size())
        if hasattr(self.window,'covers'):self.window.covers.schedule()
