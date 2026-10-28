"""On-demand local playlists, with separate worker sessions and shared playback."""
import base64
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QTabBar, QTableView, QVBoxLayout, QWidget,
)

from .surfaces import surface, heading, style_table, action_strip, action_icons

from ..messages import tr
from ..song_search import SearchSession
from ..theme import SPACE
from .playlist_match import PlaylistMatchClient
from .playlist_enrichment import PlaylistEnrichment
from .player_widgets import PlaylistPlayer
from .library_header import LibraryHeader
from .window_chrome import WindowChrome
from .playlist_store import PlaylistStore
from .playlist_files import PlaylistFileClient
from .quest_playlists import QuestPlaylistClient, PLAYLIST_ROOT


class PlaylistRows(QAbstractTableModel):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(('qt.playlists.song', 'qt.playlists.mapper', 'qt.playlists.state', 'qt.playlists.key')[section])
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return index.row()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            match=self.controller.enrichment.installed(row['hash']);meta=self.controller.enrichment.display(row)
            state=tr('playlists.status.'+match[0]) if match else tr('qt.playlists.unmatched')
            title=meta['title'] or tr('qt.playlists.unnamed')
            if meta.get('artist') or meta.get('author'):title+='\n'+(meta.get('artist') or meta.get('author'))
            if role==Qt.ItemDataRole.ToolTipRole:
                return '\n'.join(filter(None,(title,meta['mapper'],state,row['hash'],meta['id'])))
            return (title,meta['mapper'],state,meta['id'])[index.column()]
        if role==Qt.ItemDataRole.DecorationRole and index.column()==0:
            return self.controller.enrichment.icon(row)
        return None

    def replace(self, rows):
        super().beginResetModel();self.rows = rows;super().endResetModel()

    def changed(self):
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows)-1, 3))


class PlaylistDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        super().setObjectName('localPlaylists')
        self.controller = controller
        super().setModal(False)
        super().resize(1120, 760);super().setMinimumSize(700, 520)
        layout = QVBoxLayout(self);layout.setContentsMargins(12, 12, 12, 12);layout.setSpacing(8)
        self.heading = heading('qt.playlists.title');self.heading.setProperty('uiRole','heading');self.heading.setObjectName('playlistManagerTitle');self.close_button = QPushButton();self.close_button.hide()
        self.header=LibraryHeader(self)
        self.brand_icon=QLabel();self.brand_icon.setObjectName('questBrandIcon');self.brand_icon.setFixedSize(40,40);self.brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header.identity_layout.addWidget(self.brand_icon);self.header.identity_layout.addWidget(self.heading)
        title_row=self.header.action_layout;layout.addWidget(self.header)
        self.chrome=WindowChrome(self,self.header)
        self.add = QPushButton();self.remove = QPushButton();self.refresh = QPushButton();self.cancel = QPushButton()
        self.read_quest = QPushButton();self.send_quest = QPushButton();self.save_local = QPushButton();self.export = QPushButton()
        self.send_quest.setProperty('role','primary')
        self.add.setProperty('role','primary');self.remove.setProperty('role','danger')
        self.sources = QTabBar();self.sources.addTab('');self.sources.addTab('');self.sources.setExpanding(False)
        title_row.addWidget(self.sources)
        self.source_actions = action_strip(self.add,self.remove,self.read_quest,self.send_quest,self.save_local,self.export)
        self.source_actions.layout().setContentsMargins(0,0,0,0);layout.addWidget(self.source_actions)
        split = QSplitter();split.setHandleWidth(12);layout.addWidget(split, 1)
        self.lists = QListWidget();self.lists.setMinimumWidth(150)
        self.lists.setIconSize(QSize(64, 64));split.addWidget(self.lists)
        self.lists.setSpacing(SPACE.XS);self.lists.setWordWrap(True)
        self.lists.setProperty("uiTable",True)
        content = surface(QWidget());right = QVBoxLayout(content);right.setSpacing(SPACE.XS);split.addWidget(content);split.setSizes([260, 820])
        self.title = QLabel();self.title.setTextFormat(Qt.TextFormat.PlainText);self.title.setWordWrap(True);right.addWidget(self.title)
        self.location = QLabel();self.location.setWordWrap(True);self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setProperty('role','muted');self.location.setParent(content);self.location.hide()
        self.title.setProperty('uiRole','section')
        self.search = QLineEdit();search_row = QHBoxLayout()
        search_row.addWidget(self.search,1);search_row.addWidget(self.refresh);search_row.addWidget(self.cancel)
        right.addLayout(search_row)
        self.model = PlaylistRows(controller);self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model);self.proxy.setFilterKeyColumn(-1);self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.table = QTableView();self.table.setModel(self.proxy);self.table.setIconSize(QSize(44,44))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for column,width in ((1,130),(2,110),(3,86)):self.table.setColumnWidth(column,width)
        self.table.verticalHeader().setDefaultSectionSize(64);right.addWidget(self.table, 1)
        self.summary = QLabel();self.summary.setTextFormat(Qt.TextFormat.PlainText);self.summary.setWordWrap(True);right.addWidget(self.summary)
        style_table(self.table)
        self.detail = QPushButton();self.play = QPushButton();self.download = QPushButton()
        self.download.setProperty('role','primary')
        self.song_actions = action_strip(self.detail,self.play,self.download)
        self.song_actions.layout().setContentsMargins(0,0,0,0)
        layout.addWidget(self.song_actions)
        self.status = QLabel();self.status.setTextFormat(Qt.TextFormat.PlainText);self.status.setWordWrap(True)
        self.help_button = QPushButton();self.help_button.setCheckable(True)
        action_icons((self.add,'add'),(self.remove,'trash'),(self.read_quest,'refresh'),(self.send_quest,'transfer'),
                     (self.save_local,'save'),(self.export,'download'),(self.refresh,'refresh'),(self.cancel,'stop'),
                     (self.detail,'folder'),(self.play,'play'),(self.download,'download'),(self.close_button,'back'),(self.help_button,'help'))
        info_row = QHBoxLayout();info_row.addWidget(self.status,1);info_row.addWidget(self.help_button);layout.addLayout(info_row)
        self.hint = QLabel();self.hint.setTextFormat(Qt.TextFormat.PlainText);self.hint.setWordWrap(True);layout.addWidget(self.hint)
        self.player=PlaylistPlayer(controller.window.preview);layout.addWidget(self.player)
        self.hint.setProperty('role','muted');self.hint.hide()
        self.help_button.toggled.connect(self.hint.setVisible)
        self.add.clicked.connect(controller.choose_import);self.remove.clicked.connect(controller.remove)
        self.sources.currentChanged.connect(controller.source_changed)
        self.read_quest.clicked.connect(controller.read_quest)
        self.send_quest.clicked.connect(controller.send_quest)
        self.save_local.clicked.connect(controller.save_local)
        self.export.clicked.connect(controller.export)
        self.refresh.clicked.connect(lambda:(controller.enrichment.refresh(),controller.match(force=True)));self.cancel.clicked.connect(controller.cancel_match)
        self.close_button.clicked.connect(self.reject)
        self.lists.currentRowChanged.connect(controller.select_list)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.search.textChanged.connect(controller.render)
        self.table.verticalScrollBar().valueChanged.connect(controller.enrichment.schedule)
        self.search.textChanged.connect(controller.enrichment.schedule)
        self.table.selectionModel().selectionChanged.connect(controller.render)
        self.detail.clicked.connect(lambda:controller.open_local(False));self.play.clicked.connect(lambda:controller.open_local(True))
        self.table.doubleClicked.connect(lambda _index:controller.open_local(True))
        self.download.clicked.connect(controller.prepare_download)
        self.finished.connect(controller.dialog_closed)
        self.retranslate()

    def retranslate(self):
        super().setWindowTitle(tr('qt.playlists.title'));self.heading.setText(tr('qt.playlists.title'))
        from .icons import line_icon
        from ..theme import get_palette
        self.brand_icon.setPixmap(line_icon('music',get_palette(self.controller.window.mode.currentData()).ACCENT_TEXT).pixmap(26,26))
        self.chrome.retranslate()
        for button, key in ((self.add,'import'),(self.remove,'remove'),(self.refresh,'refresh'),(self.cancel,'cancel'),
                            (self.detail,'detail'),(self.play,'play'),(self.download,'download'),(self.close_button,'close')):
            button.setText(tr('qt.playlists.'+key))
        self.search.setPlaceholderText(tr('qt.playlists.search'));self.search.setAccessibleName(tr('qt.playlists.search'))
        self.lists.setAccessibleName(tr('qt.playlists.title'));self.table.setAccessibleName(tr('qt.playlists.songs'))
        self.hint.setText(tr('qt.playlists.hint'))
        self.help_button.setText(tr('ui.playlist_help'));self.help_button.setToolTip(self.hint.text())
        for i,key in enumerate(('local','quest')):self.sources.setTabText(i,tr('playlists.source.'+key))
        for button,key in ((self.read_quest,'read'),(self.send_quest,'send'),(self.save_local,'save'),(self.export,'export')):
            button.setText(tr('playlists.device.'+key))
        self.model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 3);self.model.changed()
        self.fit_actions()

    def fit_actions(self):
        compact = self.width() < 760
        pairs = ((self.add,'import', 'qt.playlists.import'),(self.remove,'remove','qt.playlists.remove'),
                 (self.send_quest,'send','playlists.device.send'),(self.save_local,'save','playlists.device.save'),
                 (self.read_quest,'read','playlists.device.read'),(self.export,'export','playlists.device.export'))
        for button, key, full in pairs:
            button.setText(tr('ui.playlist.short.'+key) if compact else tr(full));button.setToolTip(tr(full))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self,'source_actions'):self.fit_actions()


class LibraryPlaylists(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.source = 'local';self.local_records = [];self.quest_records = [];self.quest_context = None
        self.dialog = None;self.matches = {};self.current_id = None
        self.store = SearchSession(PlaylistStore(window.sandbox));self.matcher = SearchSession(PlaylistMatchClient())
        self.store_generation = None;self.match_generation = None;self.context = None;self.action = None
        self.message = '';self.closed = False
        provider = window.device_browser.session.client.provider if window.device_browser.session else None
        self.enrichment=PlaylistEnrichment(self,provider)
        self.device = SearchSession(QuestPlaylistClient(provider,window.sandbox))
        self.files = SearchSession(PlaylistFileClient(window.sandbox))
        self.device_generation = None;self.file_generation = None;self.device_request = None
        window.device_browser.choices.currentIndexChanged.connect(self.device_changed)
        self.timer = QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.poll)

    @property
    def records(self):
        return self.local_records if self.source == 'local' else [row['record'] for row in self.quest_records]

    def source_changed(self, index):
        self.source = 'local' if index == 0 else 'quest';self.current_id = None
        self.enrichment.schedule()
        self.matcher.cancel();self.match_generation = None;self.matches = {}
        self.refresh_lists();self.match()
        if self.source == 'quest' and not self.quest_records:self.message = 'device_hint';self.render()

    def device_changed(self, *_args):
        selected = self.window.device_browser.selected
        context = selected['device_id'] if selected else None
        if context != self.quest_context:
            self.device.cancel();self.device_generation = None;self.quest_records = [];self.quest_context = None
            if self.dialog is not None and self.source == 'quest':self.refresh_lists()
        self.enrichment.snapshot_changed();self.render()

    def device_available(self):
        w = self.window;selected = w.device_browser.selected
        return bool(selected and selected['kind'] == 'adb' and not w.device_browser.busy and not w.device_browser.connection_open
                    and w.transfers.dialog is None and not any(job.tasks.busy() for job in w.transfers.jobs)
                    and w.deletion.available())

    def device_action(self, request):
        if self.closed or not self.device_available() or self.device_generation is not None:return
        request = dict(request,device=dict(self.window.device_browser.selected))
        self.device_request = request;self.device_generation = self.device.request(request,0,'')
        self.message = 'device_working';self.timer.start();self.render()

    def read_quest(self):
        self.device_action(dict(action='list'))

    def send_quest(self):
        record = self.current()
        if self.source != 'local' or not record or not self.device_available():return
        selected = dict(self.window.device_browser.selected)
        if QMessageBox.question(self.dialog,tr('playlists.device.send'),tr('playlists.device.confirm',title=record['title'],path=PLAYLIST_ROOT)) != QMessageBox.StandardButton.Yes:return
        if self.current() != record or self.window.device_browser.selected != selected:return
        self.device_action(dict(action='push',record=record))

    def save_local(self):
        index = self.dialog.lists.currentRow()
        entry = self.quest_records[index] if 0 <= index < len(self.quest_records) else None
        if self.source == 'quest' and entry:
            self.device_action(dict(action='pull',name=entry['name'],sha256=entry['sha256']))

    def export(self):
        record = self.current()
        if not record or self.file_generation is not None:return
        path,_filter = QFileDialog.getSaveFileName(self.dialog,tr('playlists.device.export'),'playlist.bplist','Beat Saber (*.bplist)')
        if not path:return
        if not path.lower().endswith('.bplist'):
            path += '.bplist'
            if Path(path).exists() and QMessageBox.question(self.dialog,tr('playlists.device.export'),tr('playlists.device.overwrite',path=path)) != QMessageBox.StandardButton.Yes:return
        self.file_generation = self.files.request(dict(action='export',record=record,path=path),0,'')
        self.message = 'device_working';self.timer.start();self.render()

    def show(self):
        if self.closed:
            return
        if self.dialog is None:
            self.dialog = PlaylistDialog(self)
        self.dialog.show();self.dialog.raise_();self.dialog.activateWindow()
        self.load({'action':'load'})

    def load(self, request):
        self.matcher.cancel();self.match_generation = None;self.action = None;self.matches = {}
        if self.dialog is not None:
            self.dialog.model.changed()
        self.store_generation = self.store.request(request, 0, '')
        self.message = 'loading';self.timer.start();self.render()

    def choose_import(self):
        if self.source != 'local':return
        paths, _filter = QFileDialog.getOpenFileNames(self.dialog, tr('qt.playlists.import'), '', tr('qt.playlists.files'))
        if paths:
            self.load({'action':'import','paths':paths})

    def remove(self):
        if self.source == 'local' and self.current_id and QMessageBox.question(self.dialog, tr('qt.playlists.remove'), tr('qt.playlists.remove_confirm')) == QMessageBox.StandardButton.Yes:
            self.load({'action':'remove','id':self.current_id})

    def current(self):
        return next((v for v in self.records if v['id'] == self.current_id), None)

    def select_list(self, index):
        self.current_id = self.records[index]['id'] if 0 <= index < len(self.records) else None
        record = self.current();self.dialog.model.replace(record['songs'] if record else [])
        self.enrichment.schedule();self.render()

    def refresh_lists(self):
        dialog = self.dialog;old = self.current_id
        dialog.lists.blockSignals(True);dialog.lists.clear()
        for value in self.records:
            item = QListWidgetItem((value['title'] or tr('qt.playlists.unnamed'))+'\n'+value['author']+'\n'+tr('qt.catalog.count',count=len(value['songs'])))
            item.setSizeHint(QSize(180, 88))
            item.setToolTip(value['title']+'\n'+value['author'])
            if value['cover']:
                pixmap = QPixmap();pixmap.loadFromData(base64.b64decode(value['cover']));item.setIcon(QIcon(pixmap))
            else:
                from .icons import line_icon
                from ..theme import get_palette
                cached=self.enrichment.icon(value['songs'][0]) if value['songs'] else None
                item.setIcon(cached or line_icon('music',get_palette(self.window.mode.currentData()).ACCENT))
            dialog.lists.addItem(item)
        selected = next((i for i,v in enumerate(self.records) if v['id'] == old), 0 if self.records else -1)
        dialog.lists.setCurrentRow(selected);dialog.lists.blockSignals(False);self.select_list(selected)

    def local_context(self):
        return (str(self.window.target), self.window.generation) if self.window.local_ready else None

    def context_changed(self):
        self.matcher.cancel();self.match_generation = None;self.action = None;self.matches = {};self.context = None
        if self.dialog is not None:
            self.dialog.model.changed()
            if self.dialog.isVisible() and self.store_generation is None:
                self.match()
            self.render()

    def match(self, force=False):
        self.matcher.cancel();self.matches = {};self.action = None;self.match_generation = None
        self.context = self.local_context()
        if self.context and self.records:
            folders = [v['folder'] for v in self.window.model.rows]
            self.match_generation = self.matcher.request(dict(root=self.context[0], folders=folders, force=force), 0, '')
            self.message = 'matching';self.timer.start()
        else:
            self.message = 'choose_root' if self.records else 'empty'
        if self.dialog is not None:
            self.dialog.model.changed();self.render()

    def cancel_match(self):
        self.matcher.cancel();self.match_generation = None;self.action = None;self.matches = {}
        self.message = 'cancelled';self.dialog.model.changed();self.render()

    def selected(self):
        if self.dialog is None:
            return []
        return sorted(index.data(Qt.ItemDataRole.UserRole) for index in self.dialog.table.selectionModel().selectedRows())

    def open_local(self, play):
        indices = self.selected();record = self.current()
        if len(indices) != 1 or record is None:return
        index = indices[0];song = record['songs'][index]
        installed=self.enrichment.installed(song['hash'])
        if installed and installed[0]=='quest':
            for row,value in enumerate(self.window.quest_model.rows):
                if value['folder']==installed[1]:
                    cell=self.window.quest_model.index(row,0)
                    self.window.details.select('quest',cell,force=True)
                    if play:self.window.preview.play_index('quest',cell);self.play_queue('quest')
                    else:self.dialog.hide();self.window.raise_();self.window.activateWindow()
                    return
        if self.context!=self.local_context():return
        folder = self.matches.get(song['hash'])
        if not folder:
            return
        self.action = (record['id'], index, song['hash'], folder, play)
        self.match_generation = self.matcher.request(dict(root=self.context[0], folders=[folder], force=True), 0, '')
        self.message = 'matching';self.timer.start();self.render()

    def finish_open(self, result, action):
        identity, index, digest, folder, play = action
        if self.current_id != identity or self.selected() != [index] or result['matches'].get(digest) != folder:
            self.matches.pop(digest, None);self.message = 'unmatched';return
        for row, value in enumerate(self.window.model.rows):
            if value['folder'] == folder:
                self.window.details.select('local', self.window.model.index(row, 0), force=True)
                if play:
                    self.window.preview.play_index('local',self.window.model.index(row,0))
                    self.play_queue('local')
                else:self.dialog.hide();self.window.raise_();self.window.activateWindow()
                return

    def play_queue(self, origin):
        """Previous/next follows this playlist's installed songs, in file order."""
        record=self.current()
        if not record:return
        model=self.window.model if origin=='local' else self.window.quest_model
        rows={v['folder']:v for v in model.rows};queue=[];seen=set()
        for song in record['songs']:
            match=self.enrichment.installed(song['hash'])
            if match and match[0]==origin and match[1] in rows and match[1] not in seen:
                queue.append(dict(rows[match[1]]));seen.add(match[1])
        if queue:self.window.preview.queue=queue;self.window.preview.render()

    def download_text(self):
        record = self.current()
        if record is None:
            return ''
        keys=list(dict.fromkeys(self.enrichment.display(record['songs'][i])['id'] for i in self.selected()
                                 if not self.enrichment.installed(record['songs'][i]['hash']) and self.enrichment.display(record['songs'][i])['id']))
        return '\n'.join('https://beatsaver.com/maps/'+key for key in keys) if len(keys) <= 200 else ''

    def prepare_download(self):
        text = self.download_text()
        if not text:
            return
        self.window.open_downloads()
        window = self.window.download_window
        if window is None:
            self.message = 'download_busy';self.render();return
        queue = window.download_browser
        context = self.local_context();identity = self.current_id;target = str(queue.target)
        keys = [line.rsplit('/',1)[-1] for line in text.splitlines()]
        if QMessageBox.question(self.dialog, tr('qt.playlists.download'), tr('qt.playlist_dl.confirm',count=len(keys),path=target)) != QMessageBox.StandardButton.Yes:return
        if (self.current_id!=identity or self.local_context()!=context or self.download_text()!=text
                or str(queue.target)!=target or queue.busy or queue.closed):return
        queue.enqueue(keys);window.show_tasks()
        self.dialog.hide();window.raise_();window.activateWindow()

    def poll(self):
        if self.closed:
            return
        for event in self.store.tasks.drain():
            value = event.payload
            if not isinstance(value, dict) or self.store_generation is None or value.get('generation') != self.store_generation:
                continue
            if event.kind not in ('search','search_error'):
                continue
            self.store_generation = None
            if event.kind == 'search':
                self.local_records = value['result'];self.refresh_lists();self.match()
            else:
                self.message = 'limit' if value.get('code') == 'playlist_limit' else 'store_error'
        for event in self.matcher.tasks.drain():
            value = event.payload
            if not isinstance(value, dict) or self.match_generation is None or value.get('generation') != self.match_generation:
                continue
            if event.kind not in ('search','search_error'):
                continue
            self.match_generation = None;action = self.action;self.action = None
            if self.context != self.local_context():
                continue
            if event.kind == 'search_error':
                self.matches = {};self.message = 'match_error'
            elif action:
                self.finish_open(value['result'], action)
            else:
                result = value['result'];self.matches = result['matches']
                self.message = 'partial' if result['unknown'] or result['limited'] else 'ready'
            self.dialog.model.changed()
        for session,attribute in ((self.device,'device_generation'),(self.files,'file_generation')):
            for event in session.tasks.drain():
                value = event.payload
                if (not isinstance(value,dict) or getattr(self,attribute) is None
                        or value.get('generation') != getattr(self,attribute) or event.kind not in ('search','search_error')):continue
                setattr(self,attribute,None)
                if event.kind == 'search_error':
                    self.message = 'device_error'
                    if self.dialog:QMessageBox.warning(self.dialog,tr('qt.playlists.title'),tr('dl.error.'+value.get('code','unexpected')))
                    continue
                result = value['result']
                if session is self.device:
                    if self.device_request['device'] != self.window.device_browser.selected:continue
                    if result['action'] == 'list':
                        self.quest_records = result['rows'];self.quest_context = self.device_request['device']['device_id']
                        self.refresh_lists();self.match();self.message = 'device_partial' if result['failed'] else 'device_ready'
                        if not self.window.quest_ready:self.window.device_browser.read_selected()
                        self.enrichment.schedule()
                    elif result['action'] == 'pull':
                        self.message = 'device_saved';self.load({'action':'load'})
                    else:self.message = 'device_sent'
                else:self.message = 'device_exported'
        self.render()
        if (self.store_generation is None and self.match_generation is None and self.device_generation is None and self.file_generation is None
                and not any(s.tasks.busy() for s in (self.store,self.matcher,self.device,self.files))):
            self.timer.stop()

    def render(self, *_args):
        d = self.dialog
        if d is None:
            return
        busy = any(g is not None for g in (self.store_generation,self.match_generation,self.device_generation,self.file_generation))
        record = self.current();indices = self.selected()
        d.add.setVisible(self.source == 'local');d.remove.setVisible(self.source == 'local')
        d.send_quest.setVisible(self.source == 'local');d.save_local.setVisible(self.source == 'quest')
        d.read_quest.setVisible(self.source == 'quest')
        d.sources.setEnabled(not busy)
        d.read_quest.setEnabled(not busy and self.device_available())
        d.send_quest.setEnabled(not busy and bool(record) and self.device_available())
        d.save_local.setEnabled(not busy and bool(record) and self.device_available())
        d.export.setEnabled(not busy and bool(record))
        d.location.setText(tr('playlists.source.'+self.source))
        d.location.setToolTip(PLAYLIST_ROOT if self.source == 'quest' else str(self.window.sandbox/'playlists'))
        d.title.setToolTip(d.location.toolTip())
        d.add.setEnabled(not busy);d.remove.setEnabled(bool(record) and not busy and self.source == 'local')
        d.refresh.setEnabled(bool(record) and not busy);d.cancel.setEnabled(self.match_generation is not None)
        valid = bool(record and len(indices) == 1 and self.enrichment.installed(record['songs'][indices[0]]['hash']))
        d.detail.setEnabled(valid and not busy);d.play.setEnabled(valid and not busy)
        d.download.setEnabled(bool(self.download_text()) and not busy)
        count = len(record['songs']) if record else 0
        matched = sum(bool(self.enrichment.installed(s['hash'])) for s in record['songs']) if record else 0
        d.title.setText((record['title']+'\n'+record['author']) if record else tr('qt.playlists.empty'))
        d.title.setVisible(bool(record))
        d.summary.setText(tr('qt.playlists.summary', count=count, visible=d.proxy.rowCount(), matched=matched))
        d.status.setText(tr('playlists.resolving_quest') if self.enrichment.index_generation is not None else tr('qt.playlists.'+self.message) if self.message else '')
        if self.enrichment.lookup_failed:d.status.setText(tr('playlists.metadata_unavailable'))
        elif self.enrichment.index_failed:d.status.setText(tr('playlists.quest_match_unavailable'))
        if record and len({record['songs'][i]['key'] for i in indices
                           if record['songs'][i]['key'] and record['songs'][i]['hash'] not in self.matches}) > 200:
            d.status.setText(tr('qt.playlists.selection_limit'))

    def retranslate(self):
        if self.dialog is not None:
            self.dialog.retranslate();self.refresh_lists();self.render()

    def dialog_closed(self, _result):
        self.enrichment.cancel();self.store.cancel();self.matcher.cancel();self.store_generation = None;self.match_generation = None;self.action = None
        self.device.cancel();self.files.cancel();self.device_generation = None;self.file_generation = None

    def close(self):
        self.closed = True;self.timer.stop();self.enrichment.close()
        for session in (self.store,self.matcher,self.device,self.files):session.close()
        if self.dialog is not None:
            self.dialog.chrome.close();self.dialog.reject()

    def wait_closed(self):
        self.enrichment.wait_closed()
        for session in (self.store, self.matcher, self.device, self.files):
            for thread in tuple(session.tasks._threads):
                thread.join()
