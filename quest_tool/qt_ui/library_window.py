"""Local library preview; device access and destructive operations remain unavailable."""
from pathlib import Path
import math

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer, QThread, QModelIndex
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QPushButton, QSizePolicy, QSplitter, QStackedWidget, QTableView,
    QVBoxLayout, QWidget,
)

from ..messages import LANGUAGE_NAMES, set_language, tr
from ..song_search import SearchSession
from ..theme import DESKTOP, SPACE, get_palette
from .download_import import LocalClient, LocalModel
from .download_window import DownloadWindow
from .theme import apply_theme
from .icons import line_icon
from .library_covers import LibraryGrid, LibraryRowDelegate, LibraryTable, LocalCoverPresenter
from .library_preferences import LibraryPreferences
from .library_detail import DetailPanel
from .library_preview import LibraryPreview
from .library_userdata import LibraryUserData
from .library_ratings import LibraryRatings, RATING_ROLE
from .library_actions import LibraryActions
from .device_browser import DeviceBrowser
from .library_transfer import LibraryTransfer
from .library_report import LibraryReport
from .library_empty import LibraryEmptyStates
from .library_help import LibraryHelp
from .device_protocol import optional_metadata
from .snapshot_notice import SnapshotNotice
from .quest_media import QuestMediaPresenter
from .library_wireless import LibraryWireless


class LibraryModel(LocalModel):
    """Checks belong to folder identities, independent of filtering and sorting."""
    def __init__(self, parent=None, origin='local'):
        super().__init__(parent)
        self.checked = set()
        self.origin = origin
        self.rating_values = {}
        self.ratings_ready = False

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def row_rating(self, row):
        item = self.rows[row]
        if (item.get('origin') if self.origin == 'missing' else self.origin) != 'local':return None
        return self.rating_values.get(item['folder'],0 if self.ratings_ready else None)

    def set_ratings(self, ready, values, folder=None):
        self.ratings_ready = ready;self.rating_values = values
        if not self.rows:return
        if folder is None:
            self.dataChanged.emit(self.index(0,0),self.index(len(self.rows)-1,3),[Qt.ItemDataRole.DisplayRole,RATING_ROLE])
        else:
            for row,item in enumerate(self.rows):
                if item['folder']==folder:self.dataChanged.emit(self.index(row,0),self.index(row,3),[Qt.ItemDataRole.DisplayRole,RATING_ROLE])

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsUserCheckable if index.column() == 0 else flags

    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        if orientation==Qt.Orientation.Horizontal and role==Qt.ItemDataRole.DisplayRole:
            if section==0:return tr('qt.library.song')
            if section==3:return tr('qt.ratings.heading')
        return super().headerData(section,orientation,role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        if role == RATING_ROLE:return self.row_rating(index.row())
        if role == Qt.ItemDataRole.DisplayRole and index.column()==3:
            rating = self.row_rating(index.row())
            return tr('search.unknown') if rating is None else f'{rating} / 5'
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return Qt.CheckState.Checked if self.rows[index.row()]["folder"] in self.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            return self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            row = self.rows[index.row()]
            if index.column()==3:
                value = self.row_rating(index.row())
                return row["folder"]+"\n"+tr('qt.ratings.heading')+": "+(tr('search.unknown') if value is None else f'{value} / 5')
            return row["folder"] + ("\n" + row["error"] if row["error"] else "")
        return super().data(index, role)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid() or index.column() != 0 or not 0 <= index.row() < len(self.rows):
            return False
        key = self.rows[index.row()]["folder"]
        if value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value):
            self.checked.add(key)
        else:
            self.checked.discard(key)
        self.dataChanged.emit(index, index, [role])
        return True

    def replace(self, rows):
        self.checked.intersection_update(row["folder"] for row in rows)
        super().replace(rows)


class LibraryProxy(QSortFilterProxyModel):
    allowed = None

    def restrict(self, names):
        self.allowed = names
        super().invalidateFilter()

    def lessThan(self, left, right):
        if left.column() == 3:
            model = super().sourceModel();a = model.row_rating(left.row());b = model.row_rating(right.row())
            descending = super().sortOrder()==Qt.SortOrder.DescendingOrder
            if (a is None)!=(b is None):return a is None if descending else b is None
            if a!=b:return a<b
            x = model.rows[left.row()]['folder'];y = model.rows[right.row()]['folder']
            return (x.casefold(),x)>(y.casefold(),y) if descending else (x.casefold(),x)<(y.casefold(),y)
        if left.column() == 2:
            return super().sourceModel().rows[left.row()]["duration"] < super().sourceModel().rows[right.row()]["duration"]
        return super().lessThan(left, right)

    def filterAcceptsRow(self, row, parent):
        item = super().sourceModel().rows[row]
        if self.allowed is not None and item['folder'] not in self.allowed:return False
        query = self.parent().search.text().casefold()
        return query in " ".join((item["title"], item["author"], item["folder"])).casefold()


class LibraryWindow(QMainWindow):
    def __init__(self, sandbox, language="zh", mode="light", session=None, detail_session=None, device_provider=None, transfer_backend=None, push_backend=None, wireless_client=None, transfer_factory=None):
        super().__init__()
        set_language(language)
        self.sandbox = Path(sandbox)
        self.session = session or SearchSession(LocalClient())
        self.target = None
        self.generation = None
        self.scanning = False
        self.failed = False
        self.local_ready = False
        self.quest_ready = False
        self.quest_incomplete = False
        self.quest_identity = ''
        self.quest_display = ''
        self.missing_origin = None
        self.preferences = LibraryPreferences(sandbox)
        self.preference_values = self.preferences.load()
        self.preference_failed = False
        self.closed = False
        self.download_window = None
        self.download_queues = []
        self.texts = []
        self.shortcuts = []
        available = self.screen().availableGeometry()
        self.setMinimumSize(min(1000, available.width()-SPACE.XXL*2), min(640, available.height()-SPACE.XXL*2))
        self.resize(min(1400, available.width()-SPACE.XXL*2), min(850, available.height()-SPACE.XXL*2))
        page = QWidget();page.setObjectName("page")
        layout = QVBoxLayout(page);layout.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
        layout.setSpacing(SPACE.S)
        header = QHBoxLayout()
        header.addWidget(self.label("qt.library.title", "title"), 1)
        self.language = QComboBox()
        for key, name in LANGUAGE_NAMES.items():self.language.addItem(name, key)
        self.language.setCurrentIndex(self.language.findData(language))
        self.mode = QComboBox()
        for key in ("light", "dark"):self.mode.addItem("", key)
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.download = self.button("qt.library.download")
        self.download.setProperty('role', 'primary')
        header.addWidget(self.language);header.addWidget(self.mode);header.addWidget(self.download)
        layout.addLayout(header)
        self.connection_panel = QFrame();self.connection_panel.setObjectName("card")
        connection_layout = QVBoxLayout(self.connection_panel)
        connection_layout.setContentsMargins(SPACE.L,SPACE.M,SPACE.L,SPACE.M)
        path_row = QHBoxLayout()
        path_row.addWidget(self.label("qt.library.local"))
        self.path = QLineEdit();self.path.setReadOnly(True)
        self.browse = self.button("dl.browse")
        self.reload = self.button("qt.local.refresh")
        path_row.addWidget(self.path, 1);path_row.addWidget(self.browse);path_row.addWidget(self.reload)
        connection_layout.addLayout(path_row)
        layout.addWidget(self.connection_panel)
        self.search = QLineEdit()
        view_row = QHBoxLayout()
        view_row.addWidget(self.search,1)
        self.view_mode = QComboBox()
        for key in ('list','covers'):self.view_mode.addItem('',key)
        self.view_mode.setCurrentIndex(self.view_mode.findData(self.preference_values.get('view','list')))
        view_row.addWidget(self.view_mode)
        self.rating_sort = QComboBox()
        for key in ('title','artist','duration','high','low'):self.rating_sort.addItem('',key)
        self.rating_state = QLabel();self.rating_state.setWordWrap(True)
        self.rating_retry = QPushButton()
        view_row.addWidget(self.rating_sort)
        layout.addLayout(view_row)
        rating_row = QHBoxLayout();rating_row.addWidget(self.rating_state,1);rating_row.addWidget(self.rating_retry)
        split = QSplitter(Qt.Orientation.Horizontal);self.library_split = split
        split.setChildrenCollapsible(False);split.setHandleWidth(SPACE.M)
        local = QFrame();local.setObjectName("card");local_layout = QVBoxLayout(local)
        self.local_card = local
        local_layout.addWidget(self.label("qt.library.local", "subtitle"))
        self.model = LibraryModel(self)
        self.proxy = LibraryProxy(self);self.proxy.setSourceModel(self.model)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.table = LibraryTable();self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True);self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setMinimumSectionSize(60);self.table.horizontalHeader().setMaximumSectionSize(1200)
        for column,width in enumerate(self.preference_values.get('widths',DESKTOP.LIBRARY_COLUMNS)):self.table.setColumnWidth(column,width)
        self.table.setColumnWidth(3,self.preference_values.get('rating_width',DESKTOP.LIBRARY_RATING_W))
        self.grid = LibraryGrid(self);self.grid.setModel(self.proxy)
        self.grid.setModelColumn(0);self.grid.setSelectionModel(self.table.selectionModel())
        self.local_stack = QStackedWidget();self.local_stack.addWidget(self.table);self.local_stack.addWidget(self.grid)
        self.local_filter = QComboBox()
        for key in ('all','matched','missing'):self.local_filter.addItem('',key)
        local_layout.addWidget(self.local_filter)
        self.local_summary = QLabel();self.local_summary.setWordWrap(True)
        local_layout.addWidget(self.local_summary)
        local_layout.addWidget(self.local_stack, 1)
        controls = QHBoxLayout()
        self.select_visible = self.button("qt.library.local_select")
        self.clear = self.button("qt.library.local_clear")
        controls.addWidget(self.select_visible);controls.addWidget(self.clear)
        split.addWidget(local)
        quest_card = QFrame();quest_card.setObjectName('card');quest_layout = QVBoxLayout(quest_card)
        self.quest_card = quest_card
        quest_layout.addWidget(self.label('qt.library.quest','subtitle'))
        self.quest_summary = QLabel();self.quest_summary.setWordWrap(True);self.quest_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.quest_filter = QComboBox()
        for key in ('all','matched','missing'):self.quest_filter.addItem('',key)
        quest_layout.addWidget(self.quest_filter)
        quest_layout.addWidget(self.quest_summary)
        self.quest_model = LibraryModel(self,origin='quest');self.quest_proxy = LibraryProxy(self);self.quest_proxy.setSourceModel(self.quest_model)
        # Keep enriched rows in place until an explicit sort or filter change.
        self.quest_proxy.setDynamicSortFilter(False)
        self.quest_table = LibraryTable();self.quest_table.setModel(self.quest_proxy);self.quest_table.verticalHeader().hide()
        self.quest_table.setSortingEnabled(True);self.quest_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        quest_layout.addWidget(self.quest_table,1);split.addWidget(quest_card)
        missing_card = QFrame();missing_card.setObjectName('card');missing_layout = QVBoxLayout(missing_card)
        self.missing_card = missing_card
        missing_layout.addWidget(self.label('qt.library.missing','subtitle'))
        self.missing_side = QComboBox()
        for key in ('quest','local'):self.missing_side.addItem('',key)
        missing_layout.addWidget(self.missing_side)
        self.missing_summary = QLabel();self.missing_summary.setWordWrap(True);self.missing_summary.setTextFormat(Qt.TextFormat.PlainText)
        missing_layout.addWidget(self.missing_summary)
        self.missing_model = LibraryModel(self,origin='missing');self.missing_proxy = LibraryProxy(self);self.missing_proxy.setSourceModel(self.missing_model)
        self.missing_proxy.setDynamicSortFilter(False)
        self.missing_table = LibraryTable();self.missing_table.setModel(self.missing_proxy);self.missing_table.verticalHeader().hide()
        self.missing_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        missing_layout.addWidget(self.missing_table,1);split.addWidget(missing_card)
        self.details = DetailPanel(self,detail_session);split.addWidget(self.details.page)
        for card_layout in (local_layout,quest_layout,missing_layout):
            card_layout.setContentsMargins(SPACE.M,SPACE.M,SPACE.M,SPACE.M)
            card_layout.setSpacing(SPACE.XS)
        for card in (local,quest_card,missing_card,self.details.page):
            card.setMinimumWidth(DESKTOP.LIBRARY_CARD_MIN)
            card.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        for summary in (self.local_summary,self.quest_summary,self.missing_summary):
            summary.setProperty('role','muted')
            summary.setMinimumHeight(super().fontMetrics().height()*2)
            summary.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Minimum)
        for table in (self.table,self.quest_table,self.missing_table):
            table.hideColumn(1)
            table.setWordWrap(False)
            table.setProperty('libraryTable',True);table.setShowGrid(False)
            table.setAlternatingRowColors(True)
            table.verticalHeader().setDefaultSectionSize(DESKTOP.LIBRARY_ROW_H)
            table.horizontalHeader().setMinimumHeight(DESKTOP.TAB_H)
        for table in (self.quest_table,self.missing_table):
            table_header = table.horizontalHeader()
            table_header.setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
            for column,width in ((2,DESKTOP.LIBRARY_COLUMNS[2]),(3,DESKTOP.LIBRARY_RATING_W)):
                table_header.setSectionResizeMode(column,QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(column,width)
        for key,table in (('local',self.table),('quest',self.quest_table),('missing',self.missing_table)):
            table.setItemDelegateForColumn(0, LibraryRowDelegate(self, table, key))
            table.selectionModel().currentRowChanged.connect(lambda index,_old,view=key:self.details.select(view,index))
            table.clicked.connect(lambda index,view=key:self.details.select(view,index))
            table.activated.connect(lambda index,view=key:self.details.select(view,index))
        self.grid.clicked.connect(lambda index:self.details.select('local',index))
        split.setSizes([350,350,350,280])
        for index in range(4):split.setStretchFactor(index,1 if index<3 else 0)
        layout.addWidget(split, 1)
        rating_row.insertLayout(0,controls)
        layout.addLayout(rating_row)
        self.preview = LibraryPreview(self);layout.addWidget(self.preview.page)
        self.userdata = LibraryUserData(self)
        self.ratings = LibraryRatings(self)
        self.device_browser = DeviceBrowser(self,device_provider)
        connection_layout.addWidget(self.device_browser.page)
        self.device_browser.page.setVisible(device_provider is not None)
        self.setCentralWidget(page)
        self.timer = QTimer(self);self.timer.setInterval(75);self.timer.timeout.connect(self.poll)
        self.covers = LocalCoverPresenter(self)
        self.quest_media = QuestMediaPresenter(self,device_provider)
        self.grid.verticalScrollBar().valueChanged.connect(self.covers.schedule)
        self.table.verticalScrollBar().valueChanged.connect(self.covers.schedule)
        self.proxy.modelReset.connect(self.covers.schedule)
        self.proxy.layoutChanged.connect(self.covers.schedule)
        self.view_mode.currentIndexChanged.connect(self.change_view)
        self.local_filter.currentIndexChanged.connect(self.update_comparison)
        self.quest_filter.currentIndexChanged.connect(self.update_comparison)
        self.missing_side.currentIndexChanged.connect(self.update_comparison)
        self.browse.clicked.connect(self.choose_folder);self.reload.clicked.connect(self.refresh)
        self.search.textChanged.connect(self.filter_rows)
        self.select_visible.clicked.connect(self.check_visible);self.clear.clicked.connect(self.clear_checks)
        self.model.dataChanged.connect(self.render_status)
        self.download.clicked.connect(self.open_downloads)
        self.language.currentIndexChanged.connect(self.retranslate)
        self.mode.currentIndexChanged.connect(self.change_theme)
        self.rating_sort.activated.connect(self.sort_ratings)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self.sort_indicator)
        for sequence, callback in (("Ctrl+O", self.choose_folder), ("F5", self.refresh),
                                   ("Ctrl+F", self.search.setFocus)):
            shortcut = QShortcut(QKeySequence(sequence), self);shortcut.activated.connect(callback);self.shortcuts.append(shortcut)
        self.actions = LibraryActions(self)
        self.transfers = LibraryTransfer(self,transfer_backend,push_backend)
        self.wireless = LibraryWireless(self,wireless_client,transfer_factory)
        layout.insertWidget(layout.count()-1,self.transfers.page)
        self.reports = LibraryReport(self)
        header.addWidget(self.reports.button)
        self.help = LibraryHelp(self)
        self.help_button = self.button('qt.help.open');self.help_button.clicked.connect(self.help.show)
        header.addWidget(self.help_button)
        shortcut = QShortcut(QKeySequence('F1'),self);shortcut.activated.connect(self.help.show);self.shortcuts.append(shortcut)
        self.empty_states = LibraryEmptyStates(self)
        self.snapshot_notice = SnapshotNotice(self)
        self.retranslate();self.change_theme();self.change_view()

    def label(self, key, role=None):
        label = QLabel();label.setWordWrap(True);label.setTextFormat(Qt.TextFormat.PlainText)
        if role:label.setProperty("role", role)
        self.texts.append((label, key));return label

    def button(self, key):
        button = QPushButton();self.texts.append((button, key));return button

    def choose_folder(self):
        value = QFileDialog.getExistingDirectory(self, tr("qt.library.local"), str(self.target or ""))
        if value:self.set_target(value)

    def set_target(self, value):
        path = Path(value)
        if not path.is_absolute() or str(value).startswith(("\\\\", "//")):
            return False
        path = path.resolve()
        if not path.is_dir() or path == Path(path.anchor):return False
        if path != self.target:
            self.model.checked.clear();self.model.replace([])
        self.target = path;self.path.setText(str(path));self.refresh();return True

    def refresh(self):
        if self.closed or self.target is None:return
        self.failed = False;self.scanning = True
        self.local_ready = False
        self.details.clear()
        self.ratings.reset()
        self.update_comparison();self.covers.reset()
        self.generation = self.session.request(str(self.target), 0, "")
        self.timer.start();self.render_status()

    def poll(self):
        if self.closed:return
        for message in self.session.tasks.drain():
            data = message.payload
            if not isinstance(data, dict) or data.get("generation") != self.generation:continue
            if message.kind not in ("search", "search_error"):continue
            self.scanning = False;self.failed = message.kind == "search_error"
            self.local_ready = False
            if not self.failed and data["result"]["target"] == str(self.target):
                self.model.replace(data["result"]["rows"])
                self.local_ready = True;self.covers.reset();self.ratings.load()
            self.update_comparison()
            self.timer.stop();self.render_status()

    def filter_rows(self, text):
        for proxy in (self.proxy,self.quest_proxy,self.missing_proxy):proxy.setFilterFixedString(text)
        self.covers.schedule();self.quest_media.schedule();self.render_status()

    def check_visible(self):
        self.model.checked.update(self.proxy.index(row,0).data(Qt.ItemDataRole.UserRole)['folder'] for row in range(self.proxy.rowCount()))
        if self.model.rows:self.model.dataChanged.emit(self.model.index(0,0),self.model.index(len(self.model.rows)-1,0),[Qt.ItemDataRole.CheckStateRole])
        self.render_status()

    def clear_checks(self):
        self.model.checked.clear()
        if self.model.rows:self.model.dataChanged.emit(self.model.index(0, 0), self.model.index(len(self.model.rows)-1, 0), [Qt.ItemDataRole.CheckStateRole])
        self.render_status()

    def render_status(self, *_args):
        key = "qt.local.failed" if self.failed else "qt.local.loading" if self.scanning else "qt.library.count"
        self.statusBar().showMessage(tr(key, count=len(self.model.rows), visible=self.proxy.rowCount(), selected=len(self.model.checked)))
        self.local_summary.setText(tr(key, count=len(self.model.rows), visible=self.proxy.rowCount(), selected=len(self.model.checked)))
        self.reload.setEnabled(self.target is not None)
        self.select_visible.setEnabled(bool(self.proxy.rowCount()))
        self.clear.setEnabled(bool(self.model.checked))
        if hasattr(self,'empty_states'):self.empty_states.refresh()

    def retranslate(self):
        set_language(self.language.currentData())
        self.setWindowTitle(tr("qt.library.title"))
        for widget, key in self.texts:widget.setText(tr(key))
        for i, key in enumerate(("qt.light", "qt.dark")):self.mode.setItemText(i, tr(key))
        self.path.setPlaceholderText(tr("qt.library.choose"));self.search.setPlaceholderText(tr("qt.library.search"))
        self.search.setAccessibleName(tr("qt.library.search"));self.table.setAccessibleName(tr("qt.library.local"))
        self.model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 3)
        for model in (self.quest_model,self.missing_model):model.headerDataChanged.emit(Qt.Orientation.Horizontal,0,3)
        for menu in (self.local_filter,self.quest_filter):
            for index,key in enumerate(('all','matched','missing')):menu.setItemText(index,tr('qt.library.filter.'+key))
        for index,key in enumerate(('quest','local')):
            self.missing_side.setItemText(index,tr('qt.library.direction.'+key))
            self.missing_side.setItemData(index,tr('qt.library.missing.'+key),Qt.ItemDataRole.ToolTipRole)
        for index,key in enumerate(('list','covers')):self.view_mode.setItemText(index,tr('layout.'+key))
        self.update_comparison();self.grid.viewport().update()
        self.details.retranslate()
        self.preview.retranslate()
        self.userdata.retranslate()
        self.device_browser.retranslate()
        self.transfers.retranslate()
        self.wireless.retranslate()
        self.reports.retranslate()
        for i,key in enumerate(('title','artist','duration','high','low')):self.rating_sort.setItemText(i,tr('qt.ratings.sort.'+key))
        self.rating_sort.setAccessibleName(tr('qt.ratings.sort'))
        self.rating_retry.setText(tr('qt.ratings.retry'));self.ratings.render()
        self.help.retranslate()
        self.render_status()

    def change_theme(self):
        apply_theme(QApplication.instance(), self.mode.currentData())
        palette = get_palette(self.mode.currentData())
        self.browse.setIcon(line_icon('folder',palette.TEXT_PRIMARY))
        self.download.setIcon(line_icon('download',palette.ACCENT_TEXT))
        self.grid.viewport().update()
        self.preview.render()
        self.userdata.fit_width()

    def sort_ratings(self, _index):
        key = self.rating_sort.currentData()
        self.table.sortByColumn({'title':0,'artist':1,'duration':2}.get(key,3),Qt.SortOrder.DescendingOrder if key=='high' else Qt.SortOrder.AscendingOrder)

    def sort_indicator(self, column, order):
        key = ('high' if order==Qt.SortOrder.DescendingOrder else 'low') if column==3 else {0:'title',1:'artist',2:'duration'}.get(column,'title')
        self.rating_sort.setCurrentIndex(self.rating_sort.findData(key))
        self.covers.schedule()

    def change_view(self):
        self.local_stack.setCurrentIndex(1 if self.view_mode.currentData()=='covers' else 0)
        self.grid.setGridSize(self.grid.cell_size());self.covers.schedule()

    def set_quest_snapshot(self, rows, identity, *, skipped=0):
        """UI-thread adapter for M7; this method performs no I/O/device access."""
        if self.closed:return
        if QThread.currentThread()!=super().thread():raise RuntimeError('snapshot requires UI thread')
        if not isinstance(identity,str) or not identity.strip() or not isinstance(rows,(list,tuple)):raise ValueError('snapshot identity')
        if type(skipped) is not int or not 0<=skipped<=100000:raise ValueError('skipped names')
        accepted = []
        for row in rows:
            if not isinstance(row,dict) or any(not isinstance(row.get(key),str) for key in ('folder','title','author','error')):raise ValueError('snapshot row')
            folder = row['folder'];duration = row.get('duration')
            if not folder or folder in ('.','..') or any(c in folder for c in '/\\'):raise ValueError('snapshot folder')
            if type(duration) not in (int,float) or not math.isfinite(duration) or duration<0:raise ValueError('snapshot duration')
            accepted.append({key:row[key] for key in ('folder','title','author','error','duration')} | optional_metadata(row))
        if len({row['folder'] for row in accepted})!=len(accepted):raise ValueError('snapshot duplicate')
        self.quest_media.reset()
        if self.details.key is not None and self.details.key[0]=='quest':self.details.clear()
        if identity!=self.quest_identity:self.quest_model.checked.clear()
        self.quest_identity = identity;self.quest_display = identity;self.quest_ready = True;self.quest_incomplete = bool(skipped);self.quest_model.replace(accepted);self.snapshot_notice.captured();self.update_comparison()

    def clear_quest_snapshot(self):
        if self.closed:return
        if QThread.currentThread()!=super().thread():raise RuntimeError('snapshot requires UI thread')
        if self.details.key is not None and self.details.key[0]=='quest':self.details.clear()
        self.quest_ready = False;self.quest_incomplete = False;self.quest_identity = '';self.quest_display = '';self.quest_model.replace([]);self.snapshot_notice.clear();self.update_comparison()
        self.quest_media.reset()

    def update_comparison(self, *_args):
        ready = self.local_ready and self.quest_ready and not self.quest_incomplete
        local = {row['folder'] for row in self.model.rows};quest = {row['folder'] for row in self.quest_model.rows}
        self.quest_summary.setText(tr('qt.library.snapshot',source=self.quest_display,count=len(quest)) if self.quest_ready else tr('qt.library.later_device'))
        if hasattr(self,'snapshot_notice'):self.snapshot_notice.render()
        for menu,proxy,mine,other in ((self.local_filter,self.proxy,local,quest),(self.quest_filter,self.quest_proxy,quest,local)):
            menu.setEnabled(ready)
            for index,mode in enumerate(('all','matched','missing')):
                count = len(mine) if mode=='all' else len(mine & other) if ready and mode=='matched' else len(mine-other) if ready else '—'
                menu.setItemText(index,f"{tr('qt.library.filter.'+mode)} ({count})")
            key = menu.currentData()
            proxy.restrict(None if key=='all' else mine & other if ready and key=='matched' else mine-other if ready else set())
        self.missing_side.setEnabled(ready)
        origin = ('local' if self.missing_side.currentData()=='quest' else 'quest') if ready else None
        if origin!=self.missing_origin:self.missing_model.checked.clear()
        self.missing_origin = origin
        rows = self.model.rows if origin=='local' else self.quest_model.rows
        names = (local-quest if origin=='local' else quest-local) if ready else set()
        self.missing_model.replace(sorted((dict(row,origin=origin) for row in rows if row['folder'] in names),key=lambda row:row['folder'].lower()))
        self.missing_summary.setText(tr('qt.library.difference',count=len(names),source=tr('qt.library.'+origin)) if ready else tr('qt.library.need_both'))
        if hasattr(self,'transfers'):self.transfers.render()
        if hasattr(self,'empty_states'):self.empty_states.refresh()

    def open_downloads(self):
        if self.closed:return
        # A destroyed window can still have cancellation/history writes in flight.
        # Never create a second manager for the same history until they finish.
        if self.download_window is None and any(
                thread.is_alive() for queue in self.download_queues
                for thread in (*queue.actions._threads, *queue.manager.tasks._threads)):
            self.download.setEnabled(False)
            QTimer.singleShot(75, self.open_downloads)
            return
        self.download.setEnabled(True)
        if self.download_window is None:
            window = DownloadWindow(self.language.currentData(), self.mode.currentData())
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            window.browse_sandbox = self.sandbox
            window.enable_search();window.enable_covers();window.enable_preview();window.enable_downloads(self.sandbox)
            self.download_queues.append(window.download_browser)
            window.download_browser.importer.localRefreshed.connect(self.download_refreshed)
            window.destroyed.connect(self.download_destroyed)
            self.download_window = window
        self.download_window.show();self.download_window.raise_();self.download_window.activateWindow()

    def download_destroyed(self):
        self.download_window = None

    def download_refreshed(self, target):
        if not self.closed and self.target is not None and str(self.target) == target:self.refresh()

    def closeEvent(self, event):
        if self.closed:
            super().closeEvent(event)
            return
        self.preference_failed = not self.preferences.save({'view':self.view_mode.currentData(),'widths':self.table.saved_widths(),'preview_seconds':self.preview.length.currentData(),'preview_volume':self.preview.volume.value(),'rating_width':self.table.columnWidth(3)})
        self.snapshot_notice.close()
        self.wireless.close()
        self.help.close();self.empty_states.close();self.reports.close();self.transfers.close();self.quest_media.close();self.device_browser.close();self.actions.close();self.closed = True;self.timer.stop();self.session.close();self.covers.close();self.details.close();self.preview.close();self.userdata.close()
        if self.download_window:self.download_window.close()
        super().closeEvent(event)

    def wait_closed(self):
        self.wireless.wait_closed()
        self.reports.wait_closed()
        self.transfers.wait_closed()
        self.device_browser.wait_closed()
        self.quest_media.wait_closed()
        self.userdata.wait_closed()
        self.preview.wait_closed()
        self.details.wait_closed()
        self.covers.wait_closed()
        for thread in tuple(self.session.tasks._threads):thread.join()
        for queue in self.download_queues:
            queue.wait_closed()
