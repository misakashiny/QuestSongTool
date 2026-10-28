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

from .surfaces import action_icons

from ..messages import LANGUAGE_NAMES, set_language, tr
from ..song_search import SearchSession
from ..theme import APP_UI, DESKTOP, SPACE, get_palette
from .download_import import LocalClient, LocalModel
from .download_window import DownloadWindow
from .theme import apply_theme
from .icons import line_icon
from .library_covers import LibraryGrid, LibraryRowDelegate, LibraryTable, LocalCoverPresenter
from .library_preferences import LibraryPreferences
from .workspace_directory import WorkspaceDirectory
from .library_detail import DetailPanel
from .library_preview import LibraryPreview
from .library_userdata import LibraryUserData
from .library_ratings import LibraryRatings, RATING_ROLE
from .library_actions import LibraryActions
from .song_delete import SongDeletion
from .device_browser import DeviceBrowser
from .library_transfer import LibraryTransfer
from .library_report import LibraryReport
from .library_empty import LibraryEmptyStates
from .library_help import LibraryHelp
from .device_protocol import optional_metadata
from .snapshot_notice import SnapshotNotice
from .quest_media import QuestMediaPresenter
from .library_wireless import LibraryWireless
from .library_playlists import LibraryPlaylists
from .library_workflow import LibraryWorkflow
from .library_identity import LibraryIdentity
from .library_selection import LibrarySelection
from .library_drag import LibraryDrag, MIME
from .library_header import InlineLabel, LibraryHeader
from .window_chrome import WindowChrome
from .diagnostics import Diagnostics


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
        if hasattr(self,'drag_coordinator'):
            if index.isValid():flags |= Qt.ItemFlag.ItemIsDragEnabled
            if self.origin!='missing':flags |= Qt.ItemFlag.ItemIsDropEnabled
        return flags | Qt.ItemFlag.ItemIsUserCheckable if index.column() == 0 else flags

    def mimeTypes(self):return [MIME]
    def supportedDragActions(self):return Qt.DropAction.CopyAction
    def supportedDropActions(self):return Qt.DropAction.CopyAction
    def mimeData(self, indexes):return self.drag_coordinator.mime(self,indexes)
    def canDropMimeData(self, data, action, row, column, parent):
        return hasattr(self,'drag_coordinator') and self.drag_coordinator.accepts(self,data,action)
    def dropMimeData(self, data, action, row, column, parent):
        return hasattr(self,'drag_coordinator') and self.drag_coordinator.drop(self,data,action)

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
            tip = row["folder"] + ("\n" + row["error"] if row["error"] else "")
            checks = getattr(self.parent(), 'identity_checks', None)
            origin = row.get('origin') if self.origin=='missing' else self.origin
            if checks is not None and origin in ('local','quest'):
                own = checks.values[origin];other = checks.values['quest' if origin=='local' else 'local']
                digest = own.get(row['folder']);state = 'unknown'
                if digest and digest in other.values():state = 'same'
                elif digest and row['folder'] in other:state = 'different'
                tip += '\n'+tr('ux.identity_'+state)
            return tip
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
        self.setObjectName('libraryWindow')
        self.sandbox = Path(sandbox)
        self.session = session or SearchSession(LocalClient())
        self.workspace_directory = WorkspaceDirectory(sandbox)
        self.target = None
        self.generation = None
        self.scanning = False
        self.failed = False
        self.local_ready = False
        self.quest_ready = False
        self.quest_incomplete = False
        self.quest_stale = False
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
        # Keep controller contracts for existing helpers, without layout slots or
        # focusable entries for the controls removed from the main library.
        self.retired_controls = QWidget(self)
        self.retired_controls.hide()
        available = self.screen().availableGeometry()
        self.setMinimumSize(min(1000, available.width()-SPACE.XXL*2), min(640, available.height()-SPACE.XXL*2))
        self.resize(min(1400, available.width()-SPACE.XXL*2), min(850, available.height()-SPACE.XXL*2))
        page = QWidget();page.setObjectName("page")
        layout = QVBoxLayout(page);layout.setContentsMargins(APP_UI.GUTTER, 9, APP_UI.GUTTER, 12)
        layout.setSpacing(APP_UI.GAP)
        # Offset the brand row within its existing gap; preserve content bounds.
        top_section = QVBoxLayout();top_section.setContentsMargins(0,0,0,0)
        top_section.setSpacing(APP_UI.GAP-9);layout.addLayout(top_section)
        header_panel = LibraryHeader(self);self.header=header_panel
        header = header_panel.action_layout
        self.brand_icon = QLabel();self.brand_icon.setObjectName('questBrandIcon');self.brand_icon.setFixedSize(44,44);self.brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_panel.identity_layout.addWidget(self.brand_icon)
        brand = self.label('ui.library_heading','title');brand.setObjectName('questBrandTitle');brand.setWordWrap(False)
        header_panel.identity_layout.addWidget(brand)
        self.language = QComboBox()
        for key, name in LANGUAGE_NAMES.items():self.language.addItem(name, key)
        self.language.setCurrentIndex(self.language.findData(language))
        self.mode = QComboBox()
        for key in ("light", "dark"):self.mode.addItem("", key)
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.download = self.button("qt.library.download")
        self.download.setProperty('role', 'primary')
        header.addWidget(self.language);header.addWidget(self.mode);header.addWidget(self.download)
        top_section.addWidget(header_panel)
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
        top_section.addWidget(self.connection_panel)
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
        self.rating_state.setProperty('role','muted')
        self.rating_retry = QPushButton()
        view_row.addWidget(self.rating_sort)
        layout.addLayout(view_row)
        self.rating_state.setParent(self.retired_controls)
        self.rating_retry.setParent(self.retired_controls)
        split = QSplitter(Qt.Orientation.Horizontal);self.library_split = split
        split.setChildrenCollapsible(False);split.setHandleWidth(APP_UI.GAP)
        local = QFrame();local.setObjectName("card");local_layout = QVBoxLayout(local)
        self.local_card = local
        local_heading=QHBoxLayout();local_layout.addLayout(local_heading)
        local_heading.addWidget(self.inline_heading('qt.library.local'))
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
        self.local_filter.setParent(self.retired_controls)
        self.local_summary = InlineLabel()
        local_summary_row = local_heading
        local_summary_row.addWidget(self.local_summary,1)
        local_layout.addWidget(self.local_stack, 1)
        self.select_visible = self.button("qt.library.local_select")
        self.clear = self.button("qt.library.local_clear")
        self.select_visible.setParent(self.retired_controls);self.clear.setParent(self.retired_controls)
        split.addWidget(local)
        quest_card = QFrame();quest_card.setObjectName('card');quest_layout = QVBoxLayout(quest_card)
        self.quest_card = quest_card
        quest_heading=QHBoxLayout();quest_layout.addLayout(quest_heading)
        quest_heading.addWidget(self.inline_heading('qt.library.quest'))
        self.quest_summary = InlineLabel();self.quest_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.quest_filter = QComboBox()
        for key in ('all','matched','missing'):self.quest_filter.addItem('',key)
        self.quest_filter.setParent(self.retired_controls)
        quest_summary_row = quest_heading
        quest_summary_row.addWidget(self.quest_summary,1)
        self.quest_model = LibraryModel(self,origin='quest');self.quest_proxy = LibraryProxy(self);self.quest_proxy.setSourceModel(self.quest_model)
        # Keep enriched rows in place until an explicit sort or filter change.
        self.quest_proxy.setDynamicSortFilter(False)
        self.quest_table = LibraryTable();self.quest_table.setModel(self.quest_proxy);self.quest_table.verticalHeader().hide()
        self.quest_table.setSortingEnabled(True);self.quest_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        quest_layout.addWidget(self.quest_table,1);split.addWidget(quest_card)
        missing_card = QFrame();missing_card.setObjectName('card');missing_layout = QVBoxLayout(missing_card)
        self.missing_card = missing_card
        missing_heading=QHBoxLayout();missing_layout.addLayout(missing_heading)
        missing_heading.addWidget(self.inline_heading('ui.library.quest_missing'))
        self.missing_side = QComboBox()
        for key in ('quest','local'):self.missing_side.addItem('',key)
        self.missing_side.setParent(self.retired_controls)
        self.missing_summary = InlineLabel();self.missing_summary.setTextFormat(Qt.TextFormat.PlainText)
        missing_summary_row = missing_heading
        missing_summary_row.addWidget(self.missing_summary,1)
        self.missing_model = LibraryModel(self,origin='missing');self.missing_proxy = LibraryProxy(self);self.missing_proxy.setSourceModel(self.missing_model)
        self.missing_proxy.setDynamicSortFilter(False)
        self.missing_table = LibraryTable();self.missing_table.setModel(self.missing_proxy);self.missing_table.verticalHeader().hide()
        self.missing_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        missing_layout.addWidget(self.missing_table,1);split.addWidget(missing_card)
        self.details = DetailPanel(self,detail_session);split.addWidget(self.details.page)
        self.details.title.setObjectName('librarySongTitle')
        self.details.author.setProperty('role','muted')
        for card_layout in (local_layout,quest_layout,missing_layout):
            card_layout.setContentsMargins(12,8,12,8)
            card_layout.setSpacing(8)
        for card in (local,quest_card,missing_card,self.details.page):
            card.setMinimumWidth(DESKTOP.LIBRARY_CARD_MIN)
            card.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        for summary in (self.local_summary,self.quest_summary,self.missing_summary):
            summary.setProperty('role','muted')
            summary.setMinimumHeight(0)
            summary.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Minimum)
        for table in (self.table,self.quest_table,self.missing_table):
            table.hideColumn(1)
            table.setWordWrap(False)
            table.setProperty('libraryTable',True);table.setShowGrid(False)
            table.setAlternatingRowColors(True)
            table.verticalHeader().setDefaultSectionSize(DESKTOP.LIBRARY_ROW_H)
            table.horizontalHeader().setMinimumHeight(APP_UI.HEADER_H)
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
            table.doubleClicked.connect(lambda index,view=key:self.preview.play_index(view,index))
        self.grid.clicked.connect(lambda index:self.details.select('local',index))
        self.grid.doubleClicked.connect(lambda index:self.preview.play_index('local',index))
        split.setSizes([350,350,350,280])
        for index in range(4):split.setStretchFactor(index,1 if index<3 else 0)
        layout.addWidget(split, 1)
        self.selection = LibrarySelection(self, (local_summary_row,quest_summary_row,missing_summary_row))
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
        self.transfers.mount_header(self.header.action_layout)
        self.logs=Diagnostics(self);self.header.action_layout.insertWidget(1,self.logs.button);self.logs.button.show()
        self.wireless = LibraryWireless(self,wireless_client,transfer_factory)
        self.deletion = SongDeletion(self)
        self.reports = LibraryReport(self)
        self.reports.button.setParent(self.retired_controls)
        self.statusBar().removeWidget(self.reports.status)
        self.reports.status.setParent(self.retired_controls)
        self.help = LibraryHelp(self)
        self.help_button = self.button('qt.help.open');self.help_button.clicked.connect(self.help.show)
        self.help_button.setParent(self.retired_controls)
        self.playlists = LibraryPlaylists(self)
        self.playlists_button = self.button('qt.playlists.open')
        self.playlists_button.clicked.connect(self.playlists.show)
        path_row.addWidget(self.playlists_button)
        shortcut = QShortcut(QKeySequence('F1'),self);shortcut.activated.connect(self.help.show);self.shortcuts.append(shortcut)
        self.workflow = LibraryWorkflow(self)
        self.workflow.page.setParent(self.retired_controls)
        self.identity_checks = LibraryIdentity(self,device_provider)
        self.detect_missing=QPushButton();self.detect_missing.setProperty('role','inlineAction')
        missing_summary_row.insertWidget(missing_summary_row.indexOf(self.selection.items['missing'][2]),self.detect_missing)
        self.detect_missing.clicked.connect(lambda:self.identity_checks.cancel() if self.identity_checks.busy else self.identity_checks.start())
        self.identity_checks.render()
        self.drag=LibraryDrag(self)
        self.empty_states = LibraryEmptyStates(self)
        self.snapshot_notice = SnapshotNotice(self)
        self.chrome=WindowChrome(self,self.header)
        self.retranslate();self.change_theme();self.change_view()
        remembered = self.workspace_directory.load()
        if remembered is not None:
            QTimer.singleShot(0, lambda:self.set_target(str(remembered)) if not self.closed else None)

    def label(self, key, role=None):
        label = QLabel();label.setWordWrap(True);label.setTextFormat(Qt.TextFormat.PlainText)
        if role:label.setProperty("role", role)
        self.texts.append((label, key));return label

    def button(self, key):
        button = QPushButton();self.texts.append((button, key));return button

    def inline_heading(self, key):
        label=InlineLabel();label.setProperty('role','subtitle')
        label.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Fixed)
        self.texts.append((label,key));return label

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self,'library_split') and not getattr(self,'fit_pending',False):
            self.fit_pending=True;QTimer.singleShot(0,self.fit_panels)

    def fit_panels(self):
        self.fit_pending=False
        if self.closed:return
        split=self.library_split;usable=split.width()-3*split.handleWidth()
        detail=min(300,max(DESKTOP.LIBRARY_CARD_MIN,round(usable*.17)))
        size=max(DESKTOP.LIBRARY_CARD_MIN,(usable-detail)//3)
        split.setSizes([size,size,size,detail])
        self.device_browser.status.setMaximumWidth(max(160,self.width()//2))
        if hasattr(self,'detect_missing'):self.identity_checks.render()

    def choose_folder(self):
        value = QFileDialog.getExistingDirectory(self, tr("qt.library.local"), str(self.target or ""))
        if value:self.set_target(value)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0,self.ensure_visible)

    def ensure_visible(self):
        if self.closed or not QMainWindow.isVisible(self):return
        screens = QApplication.screens()
        if not screens or any(QMainWindow.frameGeometry(self).intersects(screen.availableGeometry()) for screen in screens):return
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.x()+SPACE.L,area.y()+SPACE.L)

    def set_target(self, value):
        path = Path(value)
        if not path.is_absolute() or str(value).startswith(("\\\\", "//")):
            return False
        path = path.resolve()
        if not path.is_dir() or path == Path(path.anchor):return False
        if path != self.target:
            self.model.checked.clear();self.model.replace([])
        self.target = path;self.path.setText(str(path));self.workspace_directory.save(path)
        if self.download_window is not None and self.download_window.download_browser.target!=path:
            self.download_window.download_browser.set_target(str(path))
        self.refresh();return True

    def refresh(self):
        if self.closed or self.target is None:return
        self.failed = False;self.scanning = True
        self.local_ready = False
        self.reports.changed()
        self.details.clear()
        self.playlists.context_changed()
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
            self.playlists.context_changed()

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
        self.local_summary.setText(tr('ui.library.inline_count' if key=='qt.library.count' else key,
            count=len(self.model.rows),visible=self.proxy.rowCount(),selected=len(self.model.checked)))
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
        self.playlists.retranslate()
        self.render_status()
        self.chrome.retranslate();self.header.schedule()
        self.logs.retranslate()

        if hasattr(self,'workflow'):self.workflow.retranslate()
        if hasattr(self,'identity_checks'):self.identity_checks.render()

    def change_theme(self):
        apply_theme(QApplication.instance(), self.mode.currentData())
        palette = get_palette(self.mode.currentData())
        self.brand_icon.setPixmap(line_icon('music',palette.ACCENT_TEXT).pixmap(28,28))
        self.reload.setIcon(line_icon('refresh',palette.TEXT_PRIMARY))
        self.playlists_button.setIcon(line_icon('list',palette.TEXT_PRIMARY))
        self.workflow.tasks.setIcon(line_icon('list',palette.TEXT_PRIMARY))
        self.browse.setIcon(line_icon('folder',palette.TEXT_PRIMARY))
        self.download.setIcon(line_icon('download',palette.ACCENT_TEXT))
        action_icons((self.reload,'refresh'),(self.playlists_button,'list'),(self.browse,'folder'),(self.download,'download'))
        self.transfers.apply_icons()
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
        self.quest_stale = False
        self.quest_identity = identity;self.quest_display = identity;self.quest_ready = True;self.quest_incomplete = bool(skipped);self.quest_model.replace(accepted);self.snapshot_notice.captured();self.update_comparison()

    def clear_quest_snapshot(self):
        if self.closed:return
        if QThread.currentThread()!=super().thread():raise RuntimeError('snapshot requires UI thread')
        if self.details.key is not None and self.details.key[0]=='quest':self.details.clear()
        self.quest_stale = False
        self.quest_ready = False;self.quest_incomplete = False;self.quest_identity = '';self.quest_display = '';self.quest_model.replace([]);self.snapshot_notice.clear();self.update_comparison()
        self.quest_media.reset()

    def update_comparison(self, *_args):
        ready = self.local_ready and self.quest_ready and not self.quest_incomplete
        local = {row['folder'] for row in self.model.rows};quest = {row['folder'] for row in self.quest_model.rows}
        checks = getattr(self,'identity_checks',None)
        lm,qm,ld,qd = checks.sets() if checks is not None else (set(),set(),set(),set())
        verified = checks is not None and checks.complete()
        self.quest_summary.setText(tr('ui.library.snapshot_count',count=len(quest)) if self.quest_ready else tr('ux.stale') if self.quest_stale else '')
        if hasattr(self,'snapshot_notice'):self.snapshot_notice.render()
        for menu,proxy,mine,matched,missing in ((self.local_filter,self.proxy,local,lm,ld),(self.quest_filter,self.quest_proxy,quest,qm,qd)):
            menu.setEnabled(ready)
            for index,mode in enumerate(('all','matched','missing')):
                count = len(mine) if mode=='all' else len(matched) if ready and mode=='matched' else len(missing) if ready and verified else '—'
                menu.setItemText(index,f"{tr('qt.library.filter.'+mode)} ({count})")
            key = menu.currentData()
            proxy.restrict(None if key=='all' else matched if ready and key=='matched' else missing if ready and verified else set())
        self.missing_side.setEnabled(ready)
        origin = ('local' if self.missing_side.currentData()=='quest' else 'quest') if ready else None
        if origin!=self.missing_origin:self.missing_model.checked.clear()
        self.missing_origin = origin
        rows = self.model.rows if origin=='local' else self.quest_model.rows
        names = (ld if origin=='local' else qd) if ready and verified else set()
        self.missing_model.replace(sorted((dict(row,origin=origin) for row in rows if row['folder'] in names),key=lambda row:row['folder'].lower()))
        self.missing_summary.setText(tr('ui.library.missing_count',count=len(names)) if ready and verified else '')
        self.selection.render()
        if hasattr(self,'transfers'):self.transfers.render()
        if hasattr(self,'empty_states'):self.empty_states.refresh()
        if checks is not None:checks.render()

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
            if self.target is not None:
                window.download_browser.set_target(str(self.target))
            self.attach_downloads(window)
        if getattr(self,'download_requested_tab',None) is not None:
            self.download_window.show_tasks();self.download_requested_tab = None
        self.download_window.show();self.download_window.raise_();self.download_window.activateWindow()

    def attach_downloads(self, window):
        """Adopt one existing download window/queue without creating a second manager."""
        self.download_queues.append(window.download_browser)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.download_browser.targetChanged.connect(lambda target:self.set_target(target) if self.target!=Path(target) else None)
        window.download_browser.importer.localRefreshed.connect(self.download_refreshed)
        window.viewLocalRequested.connect(self.workflow.show_local)
        window.libraryRequested.connect(self.show_from_downloads)
        window.has_library_owner = True
        window.destroyed.connect(self.download_destroyed)
        self.download_window = window

    def show_from_downloads(self):
        if self.closed:return
        QMainWindow.showNormal(self) if QMainWindow.isMinimized(self) else self.show()
        QMainWindow.raise_(self);QMainWindow.activateWindow(self)

    def download_destroyed(self):
        self.download_window = None

    def download_refreshed(self, target):
        if not self.closed and self.target is not None and str(self.target) == target:self.refresh()

    def closeEvent(self, event):
        if self.closed:
            super().closeEvent(event)
            return
        self.preference_failed = not self.preferences.save({'view':self.view_mode.currentData(),'widths':self.table.saved_widths(),'preview_seconds':0,'preview_volume':self.preview.volume.value(),'rating_width':self.table.saved_rating_width()})
        self.chrome.close()
        self.logs.close()
        self.identity_checks.close()
        self.workflow.close()
        self.snapshot_notice.close()
        self.wireless.close()
        self.deletion.close()
        self.playlists.close();self.help.close();self.empty_states.close();self.reports.close();self.transfers.close();self.quest_media.close();self.device_browser.close();self.actions.close();self.closed = True;self.timer.stop();self.session.close();self.covers.close();self.details.close();self.preview.close();self.userdata.close()
        if self.download_window:self.download_window.close()
        super().closeEvent(event)

    def wait_closed(self):
        self.logs.wait_closed()
        self.deletion.wait_closed()
        self.identity_checks.wait_closed()
        self.playlists.wait_closed()
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
