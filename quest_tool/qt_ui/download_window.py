"""M2B fixture browser. No network or business service instances."""
import random
from PySide6.QtCore import QEvent, Signal, QUrl, QSize, Qt, QTimer
from PySide6.QtGui import QDesktopServices, QActionGroup, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QStyle, QTabBar, QToolButton,
    QVBoxLayout, QWidget,
)

from ..messages import LANGUAGE_NAMES, set_language, tr
from ..search_filters import MODES
from ..theme import DOWNLOAD, DOWNLOAD_UI, PLAYLIST_UI, SPACE, get_palette
from .icons import line_icon
from .theme import apply_theme
from .song_model import SongModel, sample_songs
from .song_view import SongView, sample_cover
from .download_widgets import DifficultyTags, ElidedLabel, QuantityBox
from .library_header import LibraryHeader
from .window_chrome import WindowChrome
from .playlist_widgets import RoundedCover
from .diagnostics import Diagnostics


class CategoryBar(QWidget):
    """Reflow existing buttons only when the available column count changes."""
    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(SPACE.M)
        self.group = QButtonGroup(self)
        self.buttons = {}
        self.columns = 0
        super().setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        for key in MODES:
            button = QPushButton()
            button.setProperty("role", "category")
            button.setCheckable(True)
            self.group.addButton(button)
            self.buttons[key] = button
        self.buttons["Curated"].setChecked(True)
        self.retranslate()

    def retranslate(self):
        for key, button in self.buttons.items():
            button.setText(tr("browse." + key))
        self.columns = 0
        self.reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        visible = [button for key,button in self.buttons.items() if key!="Search"]
        self.buttons["Search"].hide()
        width = max(DOWNLOAD_UI.CATEGORY_W,max(button.sizeHint().width() for button in visible))
        gap = self.grid.horizontalSpacing()
        columns = max(1, min(len(visible), (self.width()+gap)//(width+gap)))
        if columns == self.columns:
            return
        for button in self.buttons.values():
            self.grid.removeWidget(button)
        for index, button in enumerate(visible):
            self.grid.addWidget(button, index//columns, index % columns)
        for column in range(len(MODES)+1):self.grid.setColumnStretch(column,0)
        self.grid.setColumnStretch(columns,1)
        for button in visible:button.setFixedWidth(width)
        self.columns = columns


class DownloadWindow(QMainWindow):
    viewLocalRequested = Signal(str)
    libraryRequested = Signal()
    def __init__(self, language="zh", mode="light", songs=None):
        super().__init__()
        set_language(language)
        self.setObjectName("downloadPreview")
        available = self.screen().availableGeometry()
        usable_width = max(1, available.width()-SPACE.XXL*2)
        usable_height = max(1, available.height()-SPACE.XXL*2)
        self.setMinimumSize(min(1000, usable_width), min(760, usable_height))
        self.resize(min(DOWNLOAD_UI.WINDOW_W, usable_width), min(DOWNLOAD_UI.WINDOW_H, usable_height))
        self.song_model = SongModel(sample_songs() if songs is None else songs, self)
        self.current_song = None
        self.browser = None
        self.browse_sandbox = None
        self.cover_browser = None
        self.preview_browser = None
        self.download_browser = None
        self.selection_browser = None
        self.favorites_browser = None
        self.playlist_browser = None
        self.texts = []
        self.mode = mode
        self.semantic_icons = []
        self.unavailable = []
        page = QWidget()
        page.setObjectName("page")
        self.root_layout = QVBoxLayout(page)
        self.root_layout.setContentsMargins(24, 0, 24, 16)
        self.root_layout.setSpacing(18)
        self.setCentralWidget(page)
        self.build_header()
        self.tabs = QTabBar()
        self.tabs.setObjectName('downloadNavigation');self.tabs.setIconSize(QSize(22,22))
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(False);self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        for _ in range(4):
            self.tabs.addTab("")
        self.task_badge=QLabel();self.task_badge.setObjectName('downloadTaskBadge')
        self.tabs.setTabButton(2,QTabBar.ButtonPosition.RightSide,self.task_badge);self.task_badge.hide()
        self.header_content.insertWidget(0, self.tabs, 1)
        self.scan_status = QLabel()
        self.scan_status.setProperty('role', 'muted')
        self.scan_status.setTextFormat(Qt.TextFormat.PlainText)
        self.scan_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.scan_status.hide()
        self.source_label = self.label("design.source", "muted", False)
        self.search_content.addWidget(self.scan_status);self.search_content.addWidget(self.source_label)
        self.pages = QStackedWidget()
        self.discovery = self.build_discovery()
        self.pages.addWidget(self.discovery)
        self.catalog_page = self.empty_page("qt.catalog.title", QStyle.StandardPixmap.SP_FileDialogListView)
        self.tasks_page = self.empty_page("qt.dl.tasks_empty", QStyle.StandardPixmap.SP_FileDialogListView)
        self.import_page = self.empty_page("qt.dl.import_empty", QStyle.StandardPixmap.SP_DialogOpenButton)
        for child in (self.catalog_page, self.tasks_page, self.import_page):self.pages.addWidget(child)
        self.tabs.currentChanged.connect(self.pages.setCurrentIndex)
        self.tabs.currentChanged.connect(lambda _index:self.refresh_preview_navigation())
        self.tabs.currentChanged.connect(lambda _index:self.set_tab_icons())
        self.root_layout.addWidget(self.pages, 1)
        self.build_footer()
        self.build_player()
        self.chrome=WindowChrome(self,self.header)
        self.logs=Diagnostics(self)
        self.logs_action=self.options.menu().addAction('')
        self.logs_action.triggered.connect(self.logs.show)
        self.pages.currentChanged.connect(self.adapt_chrome)
        self.adapt_chrome()

        self.retranslate(language)
        self.change_theme(mode)
        self.connect_browser()
        self.refresh_page()
        from .download_selection import SelectionBrowser
        self.selection_browser = SelectionBrowser(self)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.ensure_visible)

    def adapt_chrome(self, *_args):
        """Only the playlist pages use the generous reference spacing."""
        page = self.pages.currentWidget()
        catalog = page is self.catalog_page
        discovery = page is self.discovery
        self.search_host.setVisible(discovery)
        self.footer.setVisible(discovery or page is self.import_page)
        for button in (self.selection,self.clear,self.download):button.setVisible(discovery)
        gutter = PLAYLIST_UI.GUTTER if catalog else 20
        self.root_layout.setContentsMargins(gutter, 0, gutter, 16 if catalog else 8)
        self.root_layout.setSpacing(18 if catalog else 10)

    def ensure_visible(self):
        # Native initial placement may land in a gap between scaled monitors.
        # Leave every visible user-selected position intact; only rescue a
        # frame completely outside all working areas, on the GUI thread.
        if not QMainWindow.isVisible(self):return
        screens = QApplication.screens()
        if not screens or any(QMainWindow.frameGeometry(self).intersects(screen.availableGeometry()) for screen in screens):return
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.x()+SPACE.L, area.y()+SPACE.L)

    def label(self, key, role=None, wrap=True):
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(wrap)
        if role:
            label.setProperty("role", role)
        self.texts.append((label, key))
        return label

    def button(self, key, icon=None, disabled=False, role=None):
        button = QPushButton()
        if icon is not None:
            button.setIcon(QApplication.style().standardIcon(icon))
        if role:
            button.setProperty("role", role)
        self.texts.append((button, key))
        if disabled:
            button.setEnabled(False)
            self.unavailable.append(button)
        return button

    @staticmethod
    def panel():
        frame = QFrame()
        frame.setObjectName("downloadPanel")
        return frame

    def build_header(self):
        header = LibraryHeader(self);header.prefer_action_width=True;self.header=header
        row = header.action_layout
        header.setObjectName("downloadHeader")
        self.header_content = row
        self.title_icon = QLabel()
        self.title_icon.setObjectName('questBrandIcon');self.title_icon.setFixedSize(44,44);self.title_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.identity_layout.addWidget(self.title_icon)
        self.header_title = self.label("qt.app.brand", "title", False);self.header_title.setObjectName('questBrandTitle')
        header.identity_layout.addWidget(self.header_title)
        self.search_host = QWidget();self.search_content = QHBoxLayout(self.search_host)
        self.search_content.setContentsMargins(0,0,0,0);self.search_content.setSpacing(10)
        self.search = QLineEdit()
        self.search_icon = self.search.addAction(line_icon("search", get_palette(self.mode).TEXT_SECONDARY), QLineEdit.ActionPosition.LeadingPosition)
        self.semantic_icons.append((self.search_icon, "search"))
        self.search.setClearButtonEnabled(True)
        self.search_content.addWidget(self.search, 1)
        self.search_button = self.button("search.go", disabled=True, role="primary")
        self.search_button.setFixedWidth(DOWNLOAD_UI.SEARCH_W)
        self.search_content.addWidget(self.search_button)
        self.library_button = self.button("design.library")
        self.semantic_icons.append((self.library_button, "transfer"))
        self.library_button.clicked.connect(self.libraryRequested)
        row.addWidget(self.library_button)
        self.options = QToolButton()
        self.semantic_icons.append((self.options, "settings"))
        self.options.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.options.setFixedSize(QSize(36, 36))
        menu = QMenu(self.options)
        self.theme_group = QActionGroup(self)
        self.theme_actions = {}
        for key in ("light", "dark"):
            action = menu.addAction("")
            action.setCheckable(True)
            action.setChecked(key == self.mode)
            self.theme_group.addAction(action)
            action.triggered.connect(lambda _checked, value=key: self.change_theme(value))
            self.theme_actions[key] = action
        menu.addSeparator()
        self.language_group = QActionGroup(self)
        for key, name in LANGUAGE_NAMES.items():
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setData(key)
            self.language_group.addAction(action)
            action.triggered.connect(lambda _checked, value=key: self.retranslate(value))
        self.options.setMenu(menu)
        row.addWidget(self.options)
        self.root_layout.addWidget(header)
        self.root_layout.addWidget(self.search_host)

    def build_discovery(self):
        page = QWidget()
        layout = QVBoxLayout(page);layout.setContentsMargins(0,0,0,0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = self.panel();left.setObjectName('downloadBrowse')
        self.browse_panel=left;left.installEventFilter(self);self.compact_controls=None
        self.browse_layout = QVBoxLayout(left)
        self.browse_layout.setContentsMargins(SPACE.XL,SPACE.L,SPACE.S,0)
        self.browse_layout.setSpacing(SPACE.M)
        self.categories = CategoryBar()
        self.browse_layout.addWidget(self.categories)
        self.browse_layout.addSpacing(DOWNLOAD_UI.CATEGORY_AFTER)
        self.control_bar=QWidget();self.control_bar.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        controls = QHBoxLayout(self.control_bar);controls.setContentsMargins(0,0,0,0);controls.setSpacing(SPACE.S)
        self.browse_controls = controls
        self.category_title = QLabel();self.category_title.setProperty('role','subtitle')
        controls.addWidget(self.category_title)
        self.select_page = self.button('search.select_page')
        self.semantic_icons.append((self.select_page,'check'))
        controls.addWidget(self.select_page)
        self.filter_host=QWidget();self.filter_host.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        self.filter_container = QHBoxLayout(self.filter_host);self.filter_container.setContentsMargins(0,0,0,0)
        controls.addWidget(self.filter_host,1)
        self.view_controls=QWidget();view_row=QHBoxLayout(self.view_controls);view_row.setContentsMargins(0,0,0,0);view_row.setSpacing(SPACE.S)
        self.view_group = QButtonGroup(self);self.views = {}
        for key in ('covers','list'):
            button = self.button('layout.'+key,role='segment');button.setCheckable(True)
            button.setChecked(key=='covers');self.view_group.addButton(button);self.views[key]=button
            self.semantic_icons.append((button,'grid' if key=='covers' else 'list'))
            view_row.addWidget(button)
        self.filter_button = self.button('qt.dl.filter',disabled=True)
        self.semantic_icons.append((self.filter_button,'filter'));view_row.addWidget(self.filter_button)
        self.columns = QuantityBox("design.columns");self.columns.addItems([str(i) for i in range(1,7)]);self.columns.setCurrentText('6')
        self.rows = QuantityBox("design.rows");self.rows.addItems([str(i) for i in range(1,16)]);self.rows.setCurrentText('8')
        for control in (self.columns,self.rows):control.view().installEventFilter(self)
        view_row.addWidget(self.columns);view_row.addWidget(self.rows)
        controls.addWidget(self.view_controls)
        self.browse_layout.addWidget(self.control_bar)
        self.action_host=QWidget();self.action_host.setSizePolicy(QSizePolicy.Policy.Maximum,QSizePolicy.Policy.Preferred)
        self.search_controls = QHBoxLayout(self.action_host);self.search_controls.setContentsMargins(0,0,0,0);self.search_controls.setSpacing(SPACE.S)
        controls.insertWidget(2,self.action_host)
        self.browse_layout.addSpacing(SPACE.M)
        self.results = SongView();self.results.setModel(self.song_model);self.results.setMinimumWidth(420)
        self.browse_layout.addWidget(self.results,1)
        pager_panel = QWidget();pager_panel.setObjectName('downloadPager')
        pager = QHBoxLayout(pager_panel);pager.setContentsMargins(0,SPACE.XS,0,SPACE.XS)
        self.count = QLabel();self.count.setProperty('role','muted')
        self.count.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        pager.addWidget(self.count,1)
        self.previous = self.button('search.previous');self.next = self.button('search.next')
        self.page_number = self.label('qt.dl.page',wrap=False);self.page_number.setObjectName('currentDownloadPage')
        pager.addWidget(self.previous);pager.addWidget(self.page_number);pager.addWidget(self.next)
        self.note = ElidedLabel();self.note.setTextFormat(Qt.TextFormat.PlainText);self.note.setProperty('role','muted');self.texts.append((self.note,'qt.dl.note'))
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        pager.addWidget(self.note,1)
        self.browse_layout.addWidget(pager_panel)
        self.splitter.addWidget(left)
        self.details = self.build_details();self.details.setMinimumWidth(DOWNLOAD.DETAIL_W)
        self.detail_panel = self.panel();self.detail_panel.setObjectName('downloadDetail')
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(SPACE.XL,SPACE.L,SPACE.XL,SPACE.L);detail_layout.setSpacing(SPACE.L)
        detail_layout.addWidget(self.details,1)
        detail_actions = QHBoxLayout()
        self.favorite = self.button('qt.ui.favorite',disabled=True,role='favorite');self.favorite.setCheckable(True)
        self.semantic_icons.append((self.favorite,'heart'))
        detail_actions.addWidget(self.favorite);detail_actions.addWidget(self.download_one,1)
        detail_layout.addLayout(detail_actions)
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0,1);self.splitter.setStretchFactor(1,0)
        self.splitter.setSizes([1100,DOWNLOAD_UI.DETAIL_W])
        layout.addWidget(self.splitter)
        self.categories.group.buttonClicked.connect(self.update_category)
        return page

    def empty_page(self, key, icon):
        panel = self.panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(SPACE.XL, SPACE.XL, SPACE.XL, SPACE.XL)
        layout.addStretch()
        art = QLabel()
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setPixmap(QApplication.style().standardIcon(icon).pixmap(48, 48))
        layout.addWidget(art)
        text = self.label(key, "muted")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text)
        layout.addStretch()
        return panel

    def build_details(self):
        scroll = QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        card = QWidget();card.setObjectName('detailContent')
        content = QVBoxLayout(card);content.setContentsMargins(0,0,SPACE.XS,0);content.setSpacing(SPACE.M)
        self.cover = RoundedCover();self.cover.setObjectName('coverPlaceholder')
        self.cover.setFixedHeight(DOWNLOAD_UI.DETAIL_ART)
        self.cover.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(self.cover)
        self.song_title = self.label('qt.dl.choose','title');self.song_title.setObjectName('downloadSongTitle')
        self.song_title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);content.addWidget(self.song_title)
        self.song_artist = QLabel();self.song_artist.setTextFormat(Qt.TextFormat.PlainText)
        self.song_artist.setWordWrap(True);self.song_artist.setProperty('role','muted');content.addWidget(self.song_artist)
        self.detail_status = QLabel();self.detail_status.setProperty('role','status');self.detail_status.setWordWrap(True)
        content.addWidget(self.detail_status,0,Qt.AlignmentFlag.AlignLeft)
        self.meta_panel = QFrame();self.meta_panel.setObjectName('downloadMetadata')
        grid = QGridLayout(self.meta_panel);grid.setContentsMargins(0,SPACE.L,0,SPACE.L);grid.setVerticalSpacing(SPACE.M)
        self.detail_values = {}
        for i,key in enumerate(('mapper','duration','bpm','rating','id','published')):
            label = self.label('design.meta.'+key,'muted',False);value=QLabel()
            value.setTextFormat(Qt.TextFormat.PlainText);value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
            grid.addWidget(label,i,0);grid.addWidget(value,i,1);self.detail_values[key]=value
        grid.setColumnStretch(1,1);content.addWidget(self.meta_panel)
        self.difficulty_labels = DifficultyTags();content.addWidget(self.difficulty_labels)
        self.song_info = self.label('qt.dl.detail_hint','muted')
        self.song_info.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.song_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse|Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.more_details = self.button('design.more');self.more_details.setCheckable(True)
        self.more_details.toggled.connect(self.song_info.setVisible);self.song_info.hide()
        content.addWidget(self.more_details,0,Qt.AlignmentFlag.AlignLeft);content.addWidget(self.song_info);content.addStretch()
        self.download_one = self.button('layout.download_one',disabled=True,role='primary')
        self.semantic_icons.append((self.download_one,'download'));scroll.setWidget(card)
        return scroll

    def discard_page(self, page):
        """Unregister placeholder widgets before Qt deferred deletion."""
        self.texts = [(widget, key) for widget, key in self.texts if not page.isAncestorOf(widget)]
        self.unavailable = [widget for widget in self.unavailable if not page.isAncestorOf(widget)]
        self.semantic_icons = [(widget, kind) for widget, kind in self.semantic_icons
                               if not isinstance(widget, QWidget) or not page.isAncestorOf(widget)]
        self.pages.removeWidget(page)
        page.deleteLater()

    def build_footer(self):
        self.footer = self.panel()
        row = QHBoxLayout(self.footer)
        row.setContentsMargins(SPACE.L, SPACE.S, SPACE.L, SPACE.S)
        row.setSpacing(SPACE.M)
        self.folder_icon = QLabel()
        self.folder_icon.hide()
        row.addWidget(self.label("design.destination","muted",False))
        self.destination = QLineEdit()
        self.destination.setReadOnly(True)
        row.addWidget(self.destination, 1)
        self.browse = self.button("qt.dl.browse", QStyle.StandardPixmap.SP_DialogOpenButton, True)
        self.semantic_icons.append((self.browse, "folder"))
        row.addWidget(self.browse)
        self.selection = self.button("qt.dl.selected")
        self.semantic_icons.append((self.selection,"list"))
        row.addWidget(self.selection)
        self.clear = self.button("search.clear",role="danger")
        self.semantic_icons.append((self.clear,"trash"))
        row.addWidget(self.clear)
        self.download = self.button("qt.dl.download", QStyle.StandardPixmap.SP_ArrowDown, True, "primary")
        self.semantic_icons.append((self.download, "download"))
        row.addWidget(self.download)
        self.footer.setObjectName("downloadFooter")
        self.root_layout.addWidget(self.footer)

    def build_player(self):
        from .player_widgets import PlayerBar
        self.player=PlayerBar();self.player.setObjectName('downloadPlayer')
        self.preview_cover=self.player.cover;self.preview_art_key=None
        self.preview_name=self.player.title;self.preview_artist=self.player.artist
        self.preview_time=self.player.elapsed;self.preview_total=self.player.total;self.seek=self.player.seek
        self.shuffle=self.player.shuffle;self.repeat=self.player.repeat;self.play=self.player.play
        self.preview_previous=self.player.previous;self.preview_next=self.player.next
        self.unavailable.append(self.play)
        self.volume_icon=self.player.volume_icon;self.volume=self.player.volume;self.volume_value=self.player.volume_value
        self.shuffle.toggled.connect(lambda _checked:(self.refresh_player_mode_icons(),self.refresh_preview_navigation()))
        self.repeat.toggled.connect(lambda _checked:self.refresh_player_mode_icons())
        for button,delta in ((self.preview_previous,-1),(self.preview_next,1)):
            button.clicked.connect(lambda _checked=False,delta=delta:self.preview_adjacent(delta))
        self.semantic_icons.extend(((self.play,'play'),(self.preview_previous,'previous'),(self.preview_next,'next')))
        self.preview_length=self.label('design.preview_length','muted',False);self.preview_length.setParent(self.player);self.preview_length.hide()
        self.player.resized.connect(self.refresh_preview_identity)
        self.root_layout.addWidget(self.player)


    def refresh_preview_identity(self):
        song = (self.preview_browser.song if self.preview_browser else None) or self.current_song
        name = song.title if song else tr('qt.dl.preview')
        self.preview_name.setText(self.preview_name.fontMetrics().elidedText(
            name, Qt.TextElideMode.ElideRight, self.preview_name.width()))
        self.preview_artist.setText(self.preview_artist.fontMetrics().elidedText(
            song.artist if song else '', Qt.TextElideMode.ElideRight, self.preview_name.width()))
        self.preview_name.setToolTip(name+(' · '+song.artist if song else ''))
        self.preview_artist.setToolTip(song.artist if song else '')
        self.preview_name.setAccessibleName(name)
        cache = self.results.cover_cache
        if song and self.playlist_browser and song.cover_url in self.playlist_browser.cache.sources:
            cache = self.playlist_browser.cache
        image = cache.image(song.cover_url, self.preview_cover.size(), super().devicePixelRatioF()) if song and cache else None
        key = image.cacheKey() if image is not None else None
        if key != self.preview_art_key:
            self.preview_art_key = key
            self.preview_cover.clear()
            if image is not None:self.preview_cover.setPixmap(image)

    def update_category(self):
        for key, button in self.categories.buttons.items():
            if button.isChecked():
                self.category_title.setText(tr("browse." + key))
                return

    def retranslate(self, language):
        set_language(language)
        self.setWindowTitle(tr("qt.dl.title"))
        if hasattr(self,'logs_action'):self.logs_action.setText(tr('qt.logs.open'))
        if hasattr(self,'chrome'):self.chrome.retranslate();self.header.schedule()
        for widget, key in self.texts:
            widget.setText(tr(key) if key else "")
        self.search.setPlaceholderText(tr("search.placeholder"))
        self.search.setAccessibleName(tr("search.go"))
        self.destination.setPlaceholderText(tr("qt.dl.destination"))
        self.destination.setAccessibleName(tr("qt.dl.destination"))
        for index, key in enumerate(("search.tab", "qt.catalog.title", "search.tasks", "layout.import")):
            self.tabs.setTabText(index, tr(key))
        self.set_tab_icons()
        self.cover.setText(tr("qt.dl.cover"))
        self.options.setToolTip(tr("qt.appearance"))
        for key, action in self.theme_actions.items():
            action.setText(tr("qt." + key))
        for action in self.language_group.actions():
            action.setChecked(action.data() == language)
        self.categories.retranslate()
        self.update_category()
        self.refresh_status()
        self.show_song(self.current_song)
        self.results.setAccessibleName(tr("qt.songs.browser"))
        self.results.refresh_art(self.mode)
        for button in self.unavailable:
            button.setToolTip(tr("qt.dl.unavailable"))
        self.columns.setAccessibleName(tr("layout.columns"))
        self.rows.setAccessibleName(tr("layout.rows"))
        self.seek.setAccessibleName(tr("qt.dl.position"))
        self.volume.setAccessibleName(tr("layout.volume"))
        self.shuffle.setToolTip(tr('qt.player.shuffle'));self.shuffle.setAccessibleName(tr('qt.player.shuffle'))
        self.repeat.setToolTip(tr('qt.player.repeat'));self.repeat.setAccessibleName(tr('qt.player.repeat'))
        self.play.setAccessibleName(tr("layout.preview"))
        self.preview_previous.setAccessibleName(tr("design.previous_song"))
        self.preview_next.setAccessibleName(tr("design.next_song"))
        for control,key in ((self.columns,"layout.columns"),(self.rows,"layout.rows")):
            control.setToolTip(tr(key))
        if self.browser:
            self.browser.retranslate()
        if self.preview_browser:
            self.preview_browser.render()
        if self.download_browser:
            self.download_browser.refresh()

        if self.selection_browser:
            self.selection_browser.retranslate()
        if self.cover_browser:
            self.cover_browser.retranslate()
        if self.favorites_browser:
            self.favorites_browser.retranslate()
        if self.playlist_browser:
            self.playlist_browser.render()

    def change_theme(self, mode):
        self.mode = mode
        apply_theme(QApplication.instance(), mode)
        self.theme_actions[mode].setChecked(True)
        palette = get_palette(mode)
        self.set_tab_icons(palette)
        for widget, kind in self.semantic_icons:
            icon = QIcon(line_icon(kind, palette.ACCENT_TEXT if widget.property("role") in ("primary", "playerAction") else palette.TEXT_PRIMARY))
            icon.addPixmap(line_icon(kind,palette.TEXT_MUTED).pixmap(24,24),QIcon.Mode.Disabled)
            widget.setIcon(icon)
        self.refresh_player_mode_icons(palette)
        self.folder_icon.setPixmap(line_icon("folder", palette.TEXT_SECONDARY).pixmap(20, 20))
        self.title_icon.setPixmap(line_icon("music", palette.ACCENT_TEXT).pixmap(28, 28))
        self.volume_icon.setPixmap(line_icon("volume", palette.TEXT_PRIMARY).pixmap(20, 20))
        self.results.refresh_art(mode)
        self.show_song(self.current_song)
        if self.preview_browser:
            self.preview_browser.render()
        if self.playlist_browser:
            self.playlist_browser.render()

    def set_tab_icons(self, palette=None):
        """Keep the top navigation visually consistent with the reference layout."""
        palette = palette or get_palette(self.mode)
        kinds = ('search', 'list', 'check_box', 'link')
        for index, kind in enumerate(kinds):
            if index >= self.tabs.count():
                break
            color = palette.ACCENT if index == self.tabs.currentIndex() else palette.TEXT_PRIMARY
            self.tabs.setTabIcon(index, line_icon(kind, color))

    def refresh_player_mode_icons(self, palette=None):
        palette = palette or get_palette(self.mode)
        self.shuffle.setIcon(line_icon('shuffle', palette.ACCENT if self.shuffle.isChecked() else palette.TEXT_PRIMARY))
        self.repeat.setIcon(line_icon('repeat', palette.ACCENT if self.repeat.isChecked() else palette.TEXT_PRIMARY))

    def connect_browser(self):
        self.song_model.dataChanged.connect(self.refresh_status)
        self.song_model.selectionChanged.connect(self.refresh_status)
        self.results.selectionModel().currentChanged.connect(
            lambda index, _previous: self.show_song(index.data(Qt.ItemDataRole.UserRole)))
        self.results.doubleClicked.connect(lambda index:self.preview_browser.play_song(index.data(Qt.ItemDataRole.UserRole),restart=True) if self.preview_browser else None)
        self.select_page.clicked.connect(self.song_model.select_page)
        self.clear.clicked.connect(self.song_model.clear_checks)
        self.previous.clicked.connect(lambda: self.refresh_page(self.song_model.page-1))
        self.next.clicked.connect(lambda: self.refresh_page(self.song_model.page+1))
        self.columns.currentTextChanged.connect(lambda: self.refresh_page())
        self.rows.currentTextChanged.connect(lambda: self.refresh_page())
        for key, button in self.views.items():
            button.toggled.connect(lambda checked, view=key: self.switch_view(view) if checked else None)

    def switch_view(self, view):
        self.results.configure_view(view == "covers", int(self.columns.currentText()))

    def refresh_page(self, page=None):
        if self.browser:
            self.browser.request(0 if page is None else page)
            return
        key = self.current_song.key if self.current_song else None
        model = self.song_model
        model.paginate(model.page if page is None else page,
                       int(self.columns.currentText())*int(self.rows.currentText()))
        self.switch_view("covers" if self.views["covers"].isChecked() else "list")
        records = model.visible_songs()
        row = next((i for i, song in enumerate(records) if song.key == key), 0)
        if records:
            self.results.setCurrentIndex(model.index(row))
        else:
            self.show_song(None)
        self.results.scrollToTop()
        self.refresh_status()

    def refresh_status(self, *_args):
        model = self.song_model
        selected = len(model.checked)
        self.count.setText(tr("search.results", count=model.rowCount(), selected=selected))
        self.selection.setText(tr("qt.selection.open_count", count=selected))
        self.download.setText(tr("search.download", count=selected))
        self.page_number.setText(tr("qt.songs.page", page=model.page+1, total=model.page_count))
        self.previous.setEnabled(model.page > 0)
        self.next.setEnabled(model.page+1 < model.page_count)
        self.select_page.setEnabled(bool(model.rowCount()))
        self.clear.setEnabled(bool(selected))
        if self.browser:
            self.browser.refresh_status()
        if self.download_browser:
            self.download_browser.refresh()
        if self.selection_browser:
            self.selection_browser.refresh()

    def show_song(self, song):
        self.current_song = song
        self.refresh_preview_identity()
        if self.favorites_browser:
            self.favorites_browser.refresh()
        if self.selection_browser:
            self.selection_browser.refresh()
        if self.download_browser:
            self.download_browser.refresh()
        if self.preview_browser:
            self.preview_browser.render()
        self.render_detail_fields(song)
        if song is None:
            self.cover.clear()
            self.cover.setText(tr("qt.dl.cover"))
            self.song_info.hide()
            self.song_title.setText(tr("qt.dl.choose"))
            self.song_info.setText(tr("qt.dl.detail_hint"))
            return
        if self.browser:
            self.cover.clear()
            self.cover.setText(tr("qt.search.cover"))
        else:
            self.cover.setPixmap(sample_cover(song.key, self.mode).scaled(
                DOWNLOAD_UI.DETAIL_ART, DOWNLOAD_UI.DETAIL_ART, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.song_title.setText(song.title)
        self.song_info.setVisible(self.more_details.isChecked())
        key = "qt.search.details" if self.browser else "qt.songs.details"
        self.song_info.setText(tr(key, artist=song.artist, mapper=song.mapper,
                                  duration=song.duration, bpm=song.bpm_text, rating=song.rating_text,
                                  map_id=song.key, published=song.published[:10] or tr("search.unknown"),
                                  requirements=", ".join(song.requirements) or tr("search.no_requirements"),
                                  diffs="\n".join(song.diffs) or tr("search.unknown"), description=song.description))
        if self.cover_browser:
            self.cover_browser.show_detail(song)

    def enable_downloads(self, sandbox, manager=None):
        from .download_browser import DownloadBrowser

        if self.download_browser is None:
            self.unavailable.remove(self.download)
            self.unavailable.remove(self.download_one)
            self.unavailable.remove(self.browse)
            self.download_browser = DownloadBrowser(self, sandbox, manager)
            from .favorites_browser import FavoritesBrowser
            self.favorites_browser = FavoritesBrowser(self, sandbox)
            from .playlist_browser import PlaylistBrowser
            self.playlist_browser = PlaylistBrowser(self)
            if self.browser is not None:
                self.browser.filter_tools.configure(sandbox)
            self.configure_cover_cache()

    def configure_cover_cache(self):
        if self.cover_browser is not None and self.download_browser is not None:
            self.download_browser.cover_loader = self.cover_browser
            self.cover_browser.configure_disk(self.download_browser.sandbox)

    def enable_preview(self, session=None, player=None):
        from .preview_browser import PreviewBrowser

        if self.preview_browser is None:
            self.unavailable.remove(self.play)
            self.seek.setRange(0, 1000)
            self.results.preview_enabled = True
            self.preview_browser = PreviewBrowser(self, session, player)

    def enable_covers(self, session=None):
        from .cover_browser import CoverBrowser

        if self.cover_browser is None:
            self.cover_browser = CoverBrowser(self, session)
            self.cover_browser.imagesChanged.connect(self.selection_browser.refresh_art)
            self.cover_browser.imagesChanged.connect(self.refresh_preview_identity)
            if self.favorites_browser:
                self.cover_browser.imagesChanged.connect(self.favorites_browser.refresh_art)
            self.configure_cover_cache()
            self.cover_browser.page(self.song_model.visible_songs())

    def enable_search(self, session=None, prefetch_session=None, sandbox=None):
        from .search_browser import SearchBrowser

        if self.browser is not None:
            return
        self.browser = SearchBrowser(self, session, prefetch_session)
        sandbox = self.browse_sandbox if sandbox is None else sandbox
        if sandbox is not None:
            from .browse_preferences import BrowsePreferences
            self.browser.preferences = BrowsePreferences(self.browser, sandbox)
        if self.download_browser is not None:
            self.browser.filter_tools.configure(self.download_browser.sandbox)
        self.results.online = True
        self.browser.retranslate()
        self.browser.category_changed()

    def view_local_result(self):
        if self.download_browser is None:return
        target = str(self.download_browser.target)
        if getattr(self, 'has_library_owner', False):self.viewLocalRequested.emit(target)
        else:QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def closeEvent(self, event):
        self.chrome.close()
        self.logs.close()
        if self.playlist_browser:
            self.playlist_browser.close()
        if self.favorites_browser:
            self.favorites_browser.close()
        if self.selection_browser:
            self.selection_browser.close()
        if self.download_browser:
            self.download_browser.close()
        if self.preview_browser:
            self.preview_browser.close()
        if self.cover_browser:
            self.cover_browser.close()
        if self.browser:
            self.browser.close()
        super().closeEvent(event)

    def replace_page(self, name, page):
        old = getattr(self,name);index = self.pages.indexOf(old)
        self.discard_page(old);self.pages.insertWidget(index,page);setattr(self,name,page)
        self.pages.setCurrentIndex(self.tabs.currentIndex())
        return index

    def show_tasks(self):
        self.tabs.setCurrentIndex(self.pages.indexOf(self.tasks_page))

    def render_detail_fields(self, song):
        self.song_artist.setText(song.artist if song else '')
        self.meta_panel.setVisible(song is not None);self.more_details.setVisible(song is not None)
        self.difficulty_labels.setVisible(song is not None)
        self.detail_status.setText(self.song_model.status_labels.get(song.key,'') if song else '')
        self.detail_status.setVisible(bool(self.detail_status.text()))
        values = dict(mapper=song.mapper,duration=song.duration,bpm=song.bpm_text,rating=song.rating_text,id=song.key,published=song.published[:10] or tr('search.unknown')) if song else {}
        for key,label in self.detail_values.items():label.setText(values.get(key,''))
        difficulties = []
        for diff in song.diffs if song else ():
            value = diff.split(' · ')[1] if ' · ' in diff else diff
            if value not in difficulties:difficulties.append(value)
        self.difficulty_labels.render(difficulties,self.mode)
        self.refresh_preview_navigation()

    def preview_records(self):
        catalog = self.playlist_browser
        if catalog and self.pages.currentWidget() is catalog.page and catalog.detail_result:
            return [song for song in catalog.songs.rows if song is not None]
        return list(self.song_model.visible_songs())

    def refresh_preview_navigation(self):
        if not hasattr(self,'preview_previous'):return
        song = (self.preview_browser.song if self.preview_browser else None) or self.current_song
        records = self.preview_records();index = next((i for i,value in enumerate(records) if song and value.key==song.key),-1)
        for button,delta in ((self.preview_previous,-1),(self.preview_next,1)):
            choices=([value for value in records if value.preview_url and (not song or value.key!=song.key)] if self.shuffle.isChecked()
                     else [records[i] for i in range(index+delta,len(records) if delta>0 else -1,delta) if records[i].preview_url] if index>=0 else [])
            button.setEnabled(self.preview_browser is not None and bool(choices))

    def preview_adjacent(self, delta):
        if self.preview_browser is None:return
        records=self.preview_records();song=self.preview_browser.song or self.current_song
        playable = [value for value in records if value.preview_url]
        if self.shuffle.isChecked() and playable:
            choices = [value for value in playable if not song or value.key != song.key]
            if choices:
                self.preview_browser.play_song(random.choice(choices))
            return
        index=next((i for i,value in enumerate(records) if song and value.key==song.key),-1)
        if index>=0:
            target=next((records[i] for i in range(index+delta,len(records) if delta>0 else -1,delta) if records[i].preview_url),None)
            if target:self.preview_browser.play_song(target)

    def eventFilter(self, watched, event):
        if watched is getattr(self,'browse_panel',None) and event.type()==QEvent.Type.Resize:
            QTimer.singleShot(0,self.adapt_controls)
        elif event.type()==QEvent.Type.Hide and hasattr(self,'rows') and watched in (self.columns.view(),self.rows.view()):
            QTimer.singleShot(0,self.adapt_controls)
        return super().eventFilter(watched,event)

    def adapt_controls(self):
        if not hasattr(self,'rows'):return
        # A pending resize/search result must not reparent and dismiss an open popup.
        if self.columns.view().isVisible() or self.rows.view().isVisible():return
        compact=self.browse_panel.width()<900
        if compact==self.compact_controls:return
        self.compact_controls=compact
        if compact:
            self.browse_controls.removeWidget(self.view_controls)
            self.browse_layout.insertWidget(self.browse_layout.indexOf(self.control_bar)+1,self.view_controls,0,Qt.AlignmentFlag.AlignRight)
        else:
            self.browse_layout.removeWidget(self.view_controls)
            self.browse_controls.addWidget(self.view_controls)
        # Reparenting a visible QWidget hides it; restore it after either move.
        self.view_controls.show()
