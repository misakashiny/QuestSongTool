"""On-demand selected-song review; existing selection, media and queue services."""
from html import escape

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSize, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QGridLayout, QHeaderView, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QTableView, QVBoxLayout,
)

from ..messages import tr
from ..theme import DESKTOP, SPACE
from .song_model import Song
from .song_status import StatusIndex


class SelectedModel(QAbstractTableModel):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self.songs = ()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.songs)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(('qt.selection.cover', 'qt.selection.song', 'qt.selection.artist', 'qt.selection.status')[section])
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.songs):
            return None
        song = self.songs[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return song
        status = self.controller.index.label(song) or tr('qt.selection.unverified')
        if role == Qt.ItemDataRole.ToolTipRole:
            text = f'{song.title}\n{song.artist}\n{song.mapper}\n{song.key}\n{status}\n'+tr('qt.selection.history_hint')
            return '<qt>'+escape(text).replace('\n', '<br>')+'</qt>'
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            browser = self.controller.window.cover_browser
            if browser:
                return browser.cache.image(song.cover_url, QSize(DESKTOP.SELECTED_ART, DESKTOP.SELECTED_ART),
                                           self.controller.window.devicePixelRatioF())
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return ('', song.title, song.artist, status)[index.column()]
        return None

    def replace(self, songs):
        if songs == self.songs:
            return False
        super().beginResetModel(); self.songs = songs; super().endResetModel()
        return True


class SelectedDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        super().setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        area = controller.window.screen().availableGeometry()
        width, height = max(1, area.width()-SPACE.XXL*2), max(1, area.height()-SPACE.XXL*2)
        super().setMinimumSize(min(DESKTOP.SELECTED_MIN_W, width), min(DESKTOP.SELECTED_MIN_H, height))
        super().resize(min(DESKTOP.SELECTED_W, width), min(DESKTOP.SELECTED_H, height))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
        layout.setSpacing(SPACE.M)
        self.summary = QLabel(); self.summary.setWordWrap(True); self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.search = QLineEdit(); layout.addWidget(self.search)
        self.model = SelectedModel(controller, self)
        self.labels = ()
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table = QTableView(); self.table.setModel(self.proxy)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.setIconSize(QSize(DESKTOP.SELECTED_ART, DESKTOP.SELECTED_ART))
        self.table.verticalHeader().setDefaultSectionSize(DESKTOP.SELECTED_ART+SPACE.M)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, DESKTOP.SELECTED_ART+SPACE.L)
        layout.addWidget(self.table, 1)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(DESKTOP.SELECTED_DETAIL_H)
        layout.addWidget(self.detail)
        self.target = QLabel(); self.target.setWordWrap(True); self.target.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.target)
        self.hint = QLabel(); self.hint.setWordWrap(True); self.hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.hint)
        for label in (self.summary, self.target, self.hint):
            policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            policy.setHeightForWidth(True)
            label.setSizePolicy(policy)
        row = QGridLayout()
        row.setSpacing(SPACE.M)
        self.preview = QPushButton(); self.remove = QPushButton(); self.download = QPushButton(); self.close_button = QPushButton()
        self.download.setProperty('role', 'primary')
        for index, button in enumerate((self.preview, self.remove, self.download, self.close_button)):
            row.addWidget(button, index//2, index % 2)
        layout.addLayout(row)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        self.preview.clicked.connect(self.play)
        self.remove.clicked.connect(self.remove_selected)
        self.download.clicked.connect(self.enqueue)
        self.close_button.clicked.connect(self.close)
        self.retranslate()

    def selected(self):
        return [index.data(Qt.ItemDataRole.UserRole) for index in self.table.selectionModel().selectedRows()]

    def selection_changed(self, *_args):
        songs = self.selected()
        song = songs[0] if songs else None
        self.remove.setEnabled(bool(songs))
        self.preview.setEnabled(bool(song and song.preview_url and self.controller.window.preview_browser))
        detail = song.title+'\n'+tr('qt.search.details', artist=song.artist, mapper=song.mapper,
            duration=song.duration, bpm=song.bpm_text, rating=song.rating_text, map_id=song.key,
            published=song.published[:10] or tr('search.unknown'),
            requirements=', '.join(song.requirements) or tr('search.no_requirements'),
            diffs='\n'.join(song.diffs) or tr('search.unknown'), description=song.description) if song else ''
        if self.detail.toPlainText() != detail:
            self.detail.setPlainText(detail)

    def play(self):
        songs = self.selected()
        browser = self.controller.window.preview_browser
        if songs and browser and songs[0].preview_url:
            self.controller.window.show_song(songs[0])
            browser.play_song(songs[0])

    def remove_selected(self):
        model = self.controller.window.song_model
        for song in self.selected():
            model.checked.discard(song.key); model.selected_records.pop(song.key, None)
        model.notify_checks()

    def enqueue(self):
        queue = self.controller.window.download_browser
        if queue and not queue.closed and not queue.busy:
            queue.enqueue_checked()

    def refresh(self):
        chosen = {song.key for song in self.selected()}
        changed = self.model.replace(self.controller.records())
        if chosen and changed:
            from PySide6.QtCore import QItemSelectionModel
            for row in range(self.proxy.rowCount()):
                index = self.proxy.index(row, 0)
                if index.data(Qt.ItemDataRole.UserRole).key in chosen:
                    self.table.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        labels = tuple(self.controller.index.label(song) or tr('qt.selection.unverified') for song in self.model.songs)
        if self.model.rowCount() and labels != self.labels:
            self.model.dataChanged.emit(self.model.index(0, 3), self.model.index(self.model.rowCount()-1, 3))
        self.labels = labels
        count = len(self.controller.window.song_model.checked)
        self.summary.setText(tr('qt.selection.summary', count=count))
        self.download.setText(tr('qt.selection.download', count=count))
        queue = self.controller.window.download_browser
        self.download.setEnabled(bool(queue and not queue.closed and not queue.busy and count))
        self.target.setText(tr('qt.selection.target', path=str(queue.target)) if queue else tr('qt.selection.no_queue'))
        self.hint.setText((tr('dl.error.'+queue.error)+'\n' if queue and queue.error else '')+tr('qt.selection.hint'))
        self.selection_changed()

    def retranslate(self):
        super().setWindowTitle(tr('qt.selection.title'))
        self.search.setPlaceholderText(tr('qt.selection.search'))
        self.search.setAccessibleName(tr('qt.selection.search'))
        self.table.setAccessibleName(tr('qt.selection.title'))
        self.hint.setText(tr('qt.selection.hint'))
        self.preview.setText(tr('qt.selection.preview'))
        self.preview.setToolTip(tr('qt.selection.preview_hint'))
        self.remove.setText(tr('qt.selection.remove'))
        self.remove.setToolTip(tr('qt.selection.remove_hint')); self.close_button.setText(tr('qt.selection.close'))
        self.model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 3)
        self.refresh()


class SelectionBrowser(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.dialog = None
        self.closed = self.refreshing = False
        self.index = StatusIndex()
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText)
        box = window.song_info.parentWidget().layout()
        box.insertWidget(box.indexOf(window.song_info), self.status)
        window.selection.clicked.connect(self.open)
        self.retranslate()

    def records(self):
        model = self.window.song_model
        visible = {song.key: song for song in model.visible_songs()}
        keys = [key for key in model.selected_records if key in model.checked]
        keys += sorted(model.checked-set(model.selected_records))
        return tuple(model.selected_records.get(key) or visible.get(key) or
                     Song(key, key, '', '', None, None, None) for key in keys)

    def open(self):
        if self.closed or QApplication.activeModalWidget() is not None:
            return
        if self.dialog is None:
            self.dialog = SelectedDialog(self)
            self.dialog.finished.connect(self.finished)
        self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow()

    def finished(self, _result):
        self.dialog = None

    def refresh(self):
        if self.closed or self.refreshing:
            return
        self.refreshing = True
        try:
            window = self.window
            queue = window.download_browser
            verification = queue.verification if queue else None
            self.index = StatusIndex(queue.model.rows, queue.target, verification.records if verification else None,
                                     verification.inventory if verification else None) if queue else StatusIndex()
            songs = {song.key: song for song in (*window.song_model.visible_songs(), *self.records())}
            labels = {key: self.index.label(song) for key, song in songs.items()}
            old = window.song_model.status_labels
            window.song_model.status_labels = labels
            for row, song in enumerate(window.song_model.visible_songs()):
                if old.get(song.key, '') != labels.get(song.key, ''):
                    index = window.song_model.index(row)
                    window.song_model.dataChanged.emit(index, index, [Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole])
                    window.results.viewport().update(window.results.visualRect(index))
            text = self.index.label(window.current_song) if window.current_song else ''
            self.status.setText(text); self.status.setVisible(bool(text))
            self.status.setToolTip(verification.action.toolTip() if verification else tr('qt.selection.history_hint'))
            if self.dialog:
                self.dialog.refresh()
        finally:
            self.refreshing = False

    def refresh_art(self):
        if self.dialog and self.dialog.isVisible():
            self.dialog.table.viewport().update()

    def retranslate(self):
        self.window.selection.setToolTip(tr('qt.selection.open_hint'))
        self.refresh()
        if self.dialog:
            self.dialog.retranslate()

    def close(self):
        self.closed = True
        if self.dialog:
            self.dialog.close()
