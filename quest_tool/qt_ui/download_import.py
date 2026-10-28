"""Qt import form and read-only local library refresh using the existing LocalFS."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton, QTableView, QVBoxLayout, QWidget

from ..downloads import check_cancel
from ..fsapi import LocalFS, Location
from .surfaces import surface, heading, style_table, action_strip, action_icons

from ..messages import tr
from ..song_search import SearchSession
from ..theme import SPACE
from .device_protocol import report_fields
from .playlist_download import PlaylistDownloadDialog


class LocalClient:
    def search(self, target, _page, _order, cancel):
        fs = LocalFS(Location("local", target))
        try:
            folders = fs.list_song_folders(lambda _text, _percent: check_cancel(cancel))
            check_cancel(cancel)
            metadata = fs.scan_metadata(folders, cancel=cancel.is_set)
            check_cancel(cancel)
            return {"target": target, "rows": [
                {"title": m.display_title, "author": m.author, "duration": m.duration,
                 "folder": m.folder_name, "error": m.parse_error, **report_fields(m)} for m in metadata.values()]}
        finally:
            fs.close()


class LocalModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.headers = None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(("dl.column.song", "qt.local.author", "qt.local.duration")[section])
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return row["folder"]
        if role == Qt.ItemDataRole.DisplayRole:
            seconds = int(row["duration"])
            duration = f"{seconds//60}:{seconds%60:02}" if seconds else tr("search.unknown")
            return (row["title"], row["author"] or tr("search.unknown"), duration)[index.column()]
        return None

    def replace(self, rows):
        super().beginResetModel()
        self.rows = rows
        super().endResetModel()

    def refresh_headers(self):
        headers = tuple(self.headerData(i, Qt.Orientation.Horizontal) for i in range(3))
        if headers == self.headers:
            return
        self.headers = headers
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 2)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows)-1, 2),
                                  [Qt.ItemDataRole.DisplayRole])


class ImportPanel(QObject):
    localRefreshed = Signal(str)

    def __init__(self, queue):
        super().__init__(queue)
        self.queue = queue
        self.closed = False
        self.playlist_dialog = None
        self.session = SearchSession(LocalClient())
        self.generation = None
        self.scanning = False
        self.scan_target = None
        self.refresh_pending = False
        self.automatic_refresh = QTimer(self)
        self.automatic_refresh.setSingleShot(True)
        self.automatic_refresh.setInterval(250)
        self.automatic_refresh.timeout.connect(self.run_automatic_refresh)
        self.error = False
        self.page = surface(QWidget())
        layout = QVBoxLayout(self.page)
        layout.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
        self.page.setObjectName('downloadImport')
        self.heading = heading('layout.import');layout.addWidget(self.heading)
        self.source_card = surface(QWidget(),'toolbar');source_layout = QVBoxLayout(self.source_card)
        layout.addWidget(self.source_card)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        source_layout.addWidget(self.hint)
        self.input = QPlainTextEdit()
        self.input.setMaximumHeight(110)
        source_layout.addWidget(self.input)
        self.links = QPushButton()
        self.zip = QPushButton()
        self.playlist = QPushButton()
        self.keep = QCheckBox()
        self.keep.setChecked(queue.manager.last_keep_zip)
        self.links.setProperty('role','primary')
        action_icons((self.links,'link'),(self.zip,'folder'),(self.playlist,'list'))
        source_layout.addWidget(action_strip(self.links,self.zip,self.playlist,self.keep))
        row = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.refresh_button = QPushButton()
        action_icons((self.refresh_button,'refresh'))
        row.addWidget(self.summary, 1);row.addWidget(self.refresh_button)
        layout.addLayout(row)
        self.model = LocalModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        style_table(self.table)
        layout.addWidget(self.table, 1)
        queue.window.replace_page("import_page",self.page)
        self.links.clicked.connect(self.import_links)
        self.zip.clicked.connect(self.import_zip)
        self.playlist.clicked.connect(self.open_playlist)
        self.refresh_button.clicked.connect(self.refresh_local)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.render()
        self.refresh_local()

    def import_links(self):
        if '/playlists/' in self.input.toPlainText():
            self.open_playlist()
            self.playlist_dialog.url.setText(self.input.toPlainText().strip())
            self.playlist_dialog.load('url', self.input.toPlainText())
            return
        self.queue.submit("links", {"text": self.input.toPlainText(), "target": str(self.queue.target),
                                    "keep_zip": self.keep.isChecked()})

    def open_playlist(self):
        if self.closed:
            return
        if self.playlist_dialog is None:
            self.playlist_dialog = PlaylistDownloadDialog(self)
        self.playlist_dialog.render()
        self.playlist_dialog.show();self.playlist_dialog.raise_();self.playlist_dialog.activateWindow()

    def import_zip(self):
        files, _selected = QFileDialog.getOpenFileNames(self.queue.window, tr("dl.import_zip"), "", "ZIP (*.zip)")
        if files:
            self.queue.enqueue(list(dict.fromkeys(files)), kind="zip")

    def request_refresh(self):
        """Merge automatic completions; never cancel an in-progress local scan."""
        if self.closed:
            return
        self.refresh_pending = True
        self.scanning = True
        self.error = False
        self.schedule_refresh()
        self.render()

    def schedule_refresh(self):
        if not self.closed and self.refresh_pending and self.generation is None and not self.automatic_refresh.isActive():
            self.automatic_refresh.start()

    def run_automatic_refresh(self):
        if not self.closed and self.refresh_pending and self.generation is None:
            self.refresh_local()

    def refresh_local(self):
        if self.closed:
            return
        if self.queue.verification:self.queue.verification.request(force=True)
        self.automatic_refresh.stop()
        self.refresh_pending = False
        self.scanning = True
        self.error = False
        self.scan_target = str(self.queue.target)
        self.generation = self.session.request(self.scan_target, 0, "")
        self.timer.start()
        self.render()

    def poll(self):
        if self.closed:
            return
        changed = False
        for message in self.session.tasks.drain():
            data = message.payload
            if self.generation is None or not isinstance(data, dict) or data.get("generation") != self.generation:
                continue
            if message.kind not in ("search", "search_error"):
                continue
            self.scanning = False
            self.generation = None
            changed = True
            self.error = message.kind == "search_error"
            if not self.error:
                result = data.get('result')
                self.error = (not isinstance(result, dict) or
                              result.get('target') != self.scan_target or
                              self.scan_target != str(self.queue.target) or
                              not isinstance(result.get('rows'), list))
                if not self.error:
                    self.model.replace(result['rows'])
                    self.localRefreshed.emit(result['target'])
            self.timer.stop()
        self.schedule_refresh()
        if self.refresh_pending:
            self.scanning = True
        if changed:
            self.render()

    def render(self):
        if self.closed:
            return
        self.heading.setText(tr("layout.import"))
        self.hint.setText(tr("qt.import.hint"))
        self.input.setPlaceholderText(tr("qt.import.placeholder"))
        self.input.setAccessibleName(tr("dl.links"))
        self.links.setText(tr("dl.start"));self.zip.setText(tr("dl.import_zip"));self.keep.setText(tr("dl.keep_zip"))
        self.playlist.setText(tr('qt.playlist_dl.title'))
        self.playlist.setEnabled(not self.queue.busy)
        if self.playlist_dialog is not None:
            self.playlist_dialog.render()
        self.links.setEnabled(not self.queue.busy);self.zip.setEnabled(not self.queue.busy)
        self.refresh_button.setText(tr("qt.local.refresh"))
        self.summary.setText(tr("qt.local.failed") if self.error else tr("qt.local.loading") if self.scanning else tr("qt.local.count", count=len(self.model.rows)))
        self.model.refresh_headers()

    def close(self):
        self.closed = True
        if self.playlist_dialog is not None:
            self.playlist_dialog.shutdown()
        self.automatic_refresh.stop()
        self.refresh_pending = False
        self.scanning = False
        self.generation = None
        self.timer.stop()
        self.session.close()

    def wait_closed(self):
        if self.playlist_dialog is not None:
            self.playlist_dialog.wait_closed()
        for thread in tuple(self.session.tasks._threads):
            thread.join()
