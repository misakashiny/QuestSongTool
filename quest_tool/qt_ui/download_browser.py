"""Sandbox-only Qt queue adapter; existing download safety remains authoritative."""
from pathlib import Path
import threading

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableView, QVBoxLayout, QWidget

from ..downloads import DownloadError, DownloadManager, TERMINAL, parse_map_ids
from .surfaces import surface, heading, style_table, action_icons

from ..messages import catalog, tr
from ..theme import SPACE
from ..workers import TaskRunner
from .download_import import ImportPanel
from .queue_results import QueueResults
from .queue_delegate import QueueDelegate
from .workspace_directory import WorkspaceDirectory


def queue_action(tasks, manager, action, values):
    """Worker receives plain values and services, never a window or widget."""
    try:
        if action == "enqueue":
            manager.enqueue(values["sources"], values["target"], kind=values.get("kind", "online"), keep_zip=values.get("keep_zip", False))
        elif action == "links":
            values["sources"] = parse_map_ids(values["text"])
            manager.enqueue(values["sources"], values["target"], keep_zip=values["keep_zip"])
        elif action == "clear":
            manager.clear_finished()
        elif action == "cancel":
            for job_id in values["ids"]:
                manager.cancel(job_id)
        elif action == "retry":
            for job_id in values["ids"]:
                manager.retry(job_id)
        tasks.emit("queue_action", payload={"action": action, "sources": values.get("sources", []), "input_text": values.get("text")})
    except Exception as exc:
        tasks.emit("queue_action", payload={"error": exc.code if isinstance(exc, DownloadError) else "unexpected"})


class QueueModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.headers = None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(("dl.column.song", "dl.column.state", "dl.column.progress", "qt.queue.detail")[section])
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row["id"]
        detail = tr("dl.error."+row["error"]) if row["error"] else row["installed"] or row["target"]
        if role == Qt.ItemDataRole.ToolTipRole:
            return detail
        if role == Qt.ItemDataRole.DisplayRole:
            return (row["title"] or row["source"], tr("dl.state."+row["state"]),
                    f"{max(0, min(100, round(row['progress']*100)))}%", detail)[index.column()]
        return None

    def refresh_headers(self):
        headers = tuple(self.headerData(i, Qt.Orientation.Horizontal) for i in range(4))
        if headers == self.headers:
            return
        self.headers = headers
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 3)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows)-1, 3),
                                  [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole])

    @staticmethod
    def visible_values(row):
        return (row['title'] or row['source'], row['state'],
                max(0, min(100, round(row['progress']*100))),
                (row['error'], row['installed'] or row['target']))

    def replace(self, rows):
        if rows == self.rows:
            return False
        rows = [dict(row) for row in rows]
        old = self.rows
        common = min(len(rows), len(old))
        if any(rows[i]['id'] != old[i]['id'] for i in range(common)):
            # A row number must never silently acquire a different selected job.
            super().beginResetModel()
            self.rows = rows
            super().endResetModel()
            return True
        if len(rows) < len(old):
            super().beginRemoveRows(QModelIndex(), len(rows), len(old)-1)
            self.rows = rows
            super().endRemoveRows()
        elif len(rows) > len(old):
            super().beginInsertRows(QModelIndex(), len(old), len(rows)-1)
            self.rows = rows
            super().endInsertRows()
        else:
            self.rows = rows
        for index in range(common):
            previous, current = self.visible_values(old[index]), self.visible_values(rows[index])
            for column in range(4):
                if previous[column] != current[column]:
                    self.dataChanged.emit(self.index(index, column), self.index(index, column),
                                          [Qt.ItemDataRole.DisplayRole])
            if previous[3] != current[3]:
                self.dataChanged.emit(self.index(index, 0), self.index(index, 3),
                                      [Qt.ItemDataRole.ToolTipRole])
        return True


class DownloadBrowser(QObject):
    targetChanged = Signal(str)
    def __init__(self, window, sandbox, manager=None):
        super().__init__(window)
        self.window = window
        self.sandbox = Path(sandbox).resolve()
        self.workspace_directory = WorkspaceDirectory(self.sandbox)
        self.target = self.workspace_directory.load() or self.sandbox/"songs"
        self.target.mkdir(parents=True, exist_ok=True)
        if self.target.resolve() != self.target or self.target.is_symlink() or self.target.is_junction():
            raise ValueError(tr("qt.data_conflict"))
        self.confirmed_target = self.target.resolve()
        self.manager = manager if manager is not None else DownloadManager(self.sandbox/"qt-downloads.json")
        if not Path(self.manager.history).resolve().is_relative_to(self.sandbox):
            raise ValueError(tr("qt.data_conflict"))
        self.actions = TaskRunner()
        self.closed = False
        self.busy = False
        self.reaper = None
        self.error = ""
        self.importer = None
        self.cover_loader = None
        self.verification = None
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.page = surface(QWidget());self.page.setObjectName('downloadTasks')
        layout = QVBoxLayout(self.page)
        layout.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
        title_row = QHBoxLayout();self.heading = heading('qt.queue.title');title_row.addWidget(self.heading,1);layout.addLayout(title_row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.view_local = QPushButton(tr('ux.view_local'))
        self.view_local.clicked.connect(window.view_local_result);title_row.addWidget(self.view_local)
        self.model = QueueModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2,150)
        self.table.horizontalHeader().setMinimumSectionSize(85)
        self.table.verticalHeader().hide()
        self.table.selectionModel().selectionChanged.connect(self.refresh)
        style_table(self.table)
        self.table.verticalHeader().setDefaultSectionSize(56)
        self.table.setItemDelegate(QueueDelegate(self))
        layout.addWidget(self.table, 1)
        self.results = QueueResults(self)
        layout.insertWidget(2, self.results)
        row = QHBoxLayout()
        self.cancel = QPushButton()
        self.retry = QPushButton()
        self.clear_history = QPushButton()
        self.retry.setProperty("role","primary");self.clear_history.setProperty("role","danger")
        action_icons((self.view_local,'folder'),(self.cancel,'stop'),(self.retry,'refresh'),(self.clear_history,'trash'))
        row.addWidget(self.clear_history);row.addStretch(1);row.addWidget(self.cancel);row.addWidget(self.retry)
        layout.addLayout(row)
        window.replace_page("tasks_page",self.page)
        self.cancel.clicked.connect(lambda: self.act("cancel"))
        self.retry.clicked.connect(lambda: self.act("retry"))
        self.clear_history.clicked.connect(lambda: self.submit("clear", {}))
        window.browse.clicked.connect(self.browse)
        window.download.clicked.connect(self.enqueue_checked)
        window.download_one.clicked.connect(self.enqueue_current)
        window.destination.setText(str(self.target))
        self.model.replace(self.manager.snapshot())
        self.seen_finished = {r["id"] for r in self.model.rows if r["state"] in TERMINAL}
        self.importer = ImportPanel(self)
        from .verification_browser import VerificationBrowser
        self.verification = VerificationBrowser(self)
        self.refresh()

    def refresh(self, *_args):
        if self.closed:
            return
        self.heading.setText(tr('qt.queue.title'))
        self.view_local.setText(tr('ux.view_local'))
        self.model.refresh_headers()
        active=sum(row['state'] not in TERMINAL for row in self.model.rows)
        self.window.task_badge.setText(str(active));self.window.task_badge.setVisible(active>0)
        self.results.refresh()
        self.cancel.setText(tr("dl.cancel"));self.retry.setText(tr("dl.retry"))
        self.clear_history.setText(tr("dl.clear"))
        self.clear_history.setEnabled(not self.busy and any(r["state"] in TERMINAL for r in self.model.rows))
        self.window.browse.setEnabled(not self.busy)
        ids = {i.data(Qt.ItemDataRole.UserRole) for i in self.table.selectionModel().selectedRows()}
        selected = [r for r in self.model.rows if r["id"] in ids]
        self.cancel.setEnabled(not self.busy and any(r["state"] not in TERMINAL for r in selected))
        self.retry.setEnabled(not self.busy and any(r["state"] in {"failed", "cancelled", "interrupted"} for r in selected))
        self.window.download.setEnabled(not self.busy and bool(self.window.song_model.checked))
        self.window.download_one.setEnabled(not self.busy and self.window.current_song is not None)
        self.status.setText(tr("dl.error."+self.error) if self.error else tr("qt.queue.sandbox", path=str(self.target)))
        self.table.setAccessibleName(tr("qt.queue.title"))
        if self.importer:
            self.importer.render()
        if self.verification:
            self.verification.observe()
        if self.window.selection_browser:
            self.window.selection_browser.refresh()

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self.window, tr("dl.destination"), str(self.target))
        if folder:
            self.set_target(folder)

    def set_target(self, value):
        path = Path(value)
        if not path.is_absolute() or str(value).startswith(("\\\\", "//")):
            self.error = "target";self.refresh();return False
        path = path.resolve()
        if not path.is_dir() or path == Path(path.anchor) or str(path).startswith(("\\\\", "//")):
            self.error = "target";self.refresh();return False
        self.target = self.confirmed_target = path
        self.workspace_directory.save(path)
        self.window.destination.setText(str(path))
        self.error = ""
        self.importer.model.replace([])
        self.importer.refresh_local()
        self.refresh()
        self.targetChanged.emit(str(path))
        return True

    def submit(self, action, values):
        if self.closed or self.busy:
            return
        if action in ("enqueue", "links") and (self.target.resolve() != self.confirmed_target or
                                               self.target.is_symlink() or self.target.is_junction() or not self.target.is_dir()):
            self.error = "target";self.refresh();return
        self.busy = True
        self.error = ""
        tasks, manager = self.actions, self.manager
        self.actions.submit(lambda _token: queue_action(tasks, manager, action, values), name="qt-queue-action")
        self.timer.start()
        self.refresh()

    def enqueue_current(self):
        song = self.window.current_song
        if song is not None:
            self.enqueue([song.key])

    def enqueue_checked(self):
        self.enqueue(sorted(self.window.song_model.checked))

    def enqueue(self, sources, kind="online"):
        if self.target.resolve() != self.confirmed_target or self.target.is_symlink():
            self.error = "target"
            self.refresh()
            return
        self.submit("enqueue", {"sources": list(sources), "target": str(self.target), "kind": kind,
                                "keep_zip": self.importer.keep.isChecked()})

    def act(self, action):
        ids = [i.data(Qt.ItemDataRole.UserRole) for i in self.table.selectionModel().selectedRows()]
        if action == "retry":
            jobs = {r["id"]: r for r in self.model.rows}
            if any(jobs[key]["kind"] not in {"online", "zip"} or Path(jobs[key]["target"]).resolve() != self.confirmed_target
                   for key in ids):
                self.error = "target"
                self.refresh()
                return
        self.submit(action, {"ids": ids})

    def poll(self):
        if self.closed:
            return
        action_changed = False
        for message in self.actions.drain():
            if message.kind != "queue_action":
                continue
            action_changed = True
            self.busy = False
            data = message.payload
            self.error = data.get("error", "")
            if self.error and "dl.error."+self.error not in catalog("zh"):
                self.error = "unexpected"
            if not self.error and data["action"] in ("enqueue", "links"):
                if data.get("input_text") is not None and self.importer.input.toPlainText() == data["input_text"]:
                    self.importer.input.clear()
                model = self.window.song_model
                for key in data["sources"]:
                    model.checked.discard(key);model.selected_records.pop(key, None)
                model.notify_checks()
                self.window.show_tasks()
        self.manager.tasks.drain()
        rows_changed = self.model.replace(self.manager.snapshot())
        present = {r["id"] for r in self.model.rows}
        self.seen_finished.intersection_update(present)
        refresh_local = False
        for job in self.model.rows:
            if job["id"] not in self.seen_finished and job["state"] in TERMINAL:
                self.seen_finished.add(job["id"])
                if job["state"] in {"done", "exists"} and Path(job["target"]).resolve() == self.confirmed_target:
                    refresh_local = True
        if refresh_local:
            self.importer.request_refresh()
        if action_changed or rows_changed:
            self.refresh()
        if not self.busy and not self.actions.busy() and all(r["state"] in TERMINAL for r in self.model.rows):
            self.timer.stop()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.manager.shutdown()
        self.importer.close()
        if self.verification:self.verification.close()
        threads = tuple(self.actions._threads)
        if threads:
            self.reaper = threading.Thread(target=lambda: [t.join() for t in threads], name="qt-queue-shutdown", daemon=False)
            self.reaper.start()

    def wait_closed(self):
        """After app.exec returns, retain the runtime lock until all writers exit."""
        if self.reaper is not None:
            self.reaper.join()
        for thread in tuple(self.manager.tasks._threads):
            thread.join()
        self.importer.wait_closed()
        if self.window.playlist_browser:self.window.playlist_browser.wait_closed()
        if self.cover_loader:self.cover_loader.wait_closed()
        if self.verification:self.verification.wait_closed()
