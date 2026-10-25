"""M2B fixture browser. No network or business service instances."""
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QActionGroup, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QSplitter, QStackedWidget, QStyle, QTabBar, QToolButton,
    QVBoxLayout, QWidget,
)

from ..messages import LANGUAGE_NAMES, set_language, tr
from ..search_filters import MODES
from ..theme import DESKTOP, DOWNLOAD, SPACE, get_palette
from .icons import line_icon
from .theme import apply_theme
from .song_model import SongModel, sample_songs
from .song_view import SongView, sample_cover


class CategoryBar(QWidget):
    """Reflow existing buttons only when the available column count changes."""
    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(SPACE.XS)
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
        width = max(button.sizeHint().width() for button in self.buttons.values())
        gap = self.grid.horizontalSpacing()
        columns = max(1, min(len(MODES), (self.width()+gap)//(width+gap)))
        if columns == self.columns:
            return
        for button in self.buttons.values():
            self.grid.removeWidget(button)
        for index, button in enumerate(self.buttons.values()):
            self.grid.addWidget(button, index//columns, index % columns)
        self.columns = columns


class DownloadWindow(QMainWindow):
    def __init__(self, language="zh", mode="light", songs=None):
        super().__init__()
        set_language(language)
        self.setObjectName("downloadPreview")
        available = self.screen().availableGeometry()
        usable_width = max(1, available.width()-SPACE.XXL*2)
        usable_height = max(1, available.height()-SPACE.XXL*2)
        self.setMinimumSize(min(1000, usable_width), min(760, usable_height))
        self.resize(min(DOWNLOAD.WINDOW_W, usable_width), min(DOWNLOAD.WINDOW_H, usable_height))
        self.song_model = SongModel(sample_songs() if songs is None else songs, self)
        self.current_song = None
        self.browser = None
        self.browse_sandbox = None
        self.cover_browser = None
        self.preview_browser = None
        self.download_browser = None
        self.selection_browser = None
        self.favorites_browser = None
        self.texts = []
        self.mode = mode
        self.semantic_icons = []
        self.unavailable = []
        page = QWidget()
        page.setObjectName("page")
        self.root_layout = QVBoxLayout(page)
        self.root_layout.setContentsMargins(SPACE.M, SPACE.M, SPACE.M, SPACE.M)
        self.root_layout.setSpacing(SPACE.S)
        self.setCentralWidget(page)
        self.build_header()
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(False)
        for _ in range(3):
            self.tabs.addTab("")
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 0, 0, 0)
        tabs_row.setSpacing(SPACE.S)
        tabs_row.addWidget(self.tabs)
        self.scan_status = QLabel()
        self.scan_status.setProperty('role', 'muted')
        self.scan_status.setTextFormat(Qt.TextFormat.PlainText)
        self.scan_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.scan_status.hide()
        tabs_row.addWidget(self.scan_status, 1)
        self.root_layout.addLayout(tabs_row)
        self.pages = QStackedWidget()
        self.discovery = self.build_discovery()
        self.pages.addWidget(self.discovery)
        self.pages.addWidget(self.empty_page("qt.dl.tasks_empty", QStyle.StandardPixmap.SP_FileDialogListView))
        self.pages.addWidget(self.empty_page("qt.dl.import_empty", QStyle.StandardPixmap.SP_DialogOpenButton))
        self.tabs.currentChanged.connect(self.pages.setCurrentIndex)
        self.root_layout.addWidget(self.pages, 1)
        self.build_footer()
        self.build_player()
        self.note = self.label("qt.dl.note", "muted")
        self.root_layout.addWidget(self.note)
        self.retranslate(language)
        self.change_theme(mode)
        self.connect_browser()
        self.refresh_page()
        from .download_selection import SelectionBrowser
        self.selection_browser = SelectionBrowser(self)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.ensure_visible)

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
        header = self.panel()
        row = QHBoxLayout(header)
        row.setContentsMargins(SPACE.L, SPACE.S, SPACE.L, SPACE.S)
        row.setSpacing(SPACE.L)
        self.title_icon = QLabel()
        self.title_icon.hide()
        self.search = QLineEdit()
        self.search_icon = self.search.addAction(line_icon("search", get_palette(self.mode).TEXT_SECONDARY), QLineEdit.ActionPosition.LeadingPosition)
        self.semantic_icons.append((self.search_icon, "search"))
        self.search.setClearButtonEnabled(True)
        row.addWidget(self.search, 1)
        self.search_button = self.button("search.go", disabled=True, role="primary")
        row.addWidget(self.search_button)
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

    def build_discovery(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE.S)
        toolbar = QHBoxLayout()
        self.categories = CategoryBar()
        toolbar.addWidget(self.categories, 1)
        self.view_group = QButtonGroup(self)
        self.views = {}
        for key in ("covers", "list"):
            button = self.button("layout." + key, role="segment")
            button.setCheckable(True)
            button.setChecked(key == "covers")
            self.view_group.addButton(button)
            self.views[key] = button
            toolbar.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        self.filter_button = self.button("qt.dl.filter", QStyle.StandardPixmap.SP_FileDialogContentsView, True)
        self.semantic_icons.append((self.filter_button, "filter"))
        toolbar.addWidget(self.filter_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(toolbar)
        controls = QHBoxLayout()
        self.category_title = QLabel()
        self.category_title.setProperty("role", "subtitle")
        controls.addWidget(self.category_title)
        self.select_page = self.button("search.select_page")
        controls.addWidget(self.select_page)
        controls.addStretch()
        controls.addWidget(self.label("layout.columns", wrap=False))
        self.columns = QComboBox()
        self.columns.addItems([str(i) for i in range(1, 7)])
        self.columns.setCurrentText("3")
        controls.addWidget(self.columns)
        controls.addWidget(self.label("layout.rows", wrap=False))
        self.rows = QComboBox()
        self.rows.addItems([str(i) for i in range(1, 16)])
        self.rows.setCurrentText("7")
        controls.addWidget(self.rows)
        layout.addLayout(controls)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.results = SongView()
        self.results.setModel(self.song_model)
        self.results.setMinimumWidth(420)
        self.splitter.addWidget(self.results)
        self.details = self.build_details()
        self.details.setMinimumWidth(DOWNLOAD.DETAIL_W)
        self.detail_panel = self.panel()
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(SPACE.L, SPACE.L, SPACE.L, SPACE.L)
        detail_layout.setSpacing(SPACE.M)
        detail_layout.addWidget(self.label("detail.heading", "subtitle", False))
        detail_layout.addWidget(self.details, 1)
        detail_actions = QHBoxLayout()
        self.favorite = self.button('qt.ui.favorite', disabled=True, role='favorite')
        self.favorite.setCheckable(True)
        detail_actions.addWidget(self.favorite)
        detail_actions.addWidget(self.download_one, 1)
        detail_layout.addLayout(detail_actions)
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([900, DESKTOP.DOWNLOAD_DETAIL_W+2*SPACE.L])
        layout.addWidget(self.splitter, 1)
        pager_panel = self.panel()
        pager = QGridLayout(pager_panel)
        pager.setContentsMargins(SPACE.M, SPACE.XS, SPACE.M, SPACE.XS)
        self.count = QLabel()
        self.count.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        pager.addWidget(self.count, 0, 0)
        navigation = QHBoxLayout()
        self.previous = self.button("search.previous")
        self.next = self.button("search.next")
        navigation.addWidget(self.previous)
        self.page_number = self.label("qt.dl.page", wrap=False)
        navigation.addWidget(self.page_number)
        navigation.addWidget(self.next)
        pager.addLayout(navigation, 0, 1)
        pager.addWidget(QWidget(), 0, 2)
        pager.setColumnStretch(0, 1)
        pager.setColumnStretch(2, 1)
        layout.addWidget(pager_panel)
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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        card = QWidget()
        card.setObjectName("detailContent")
        content = QVBoxLayout(card)
        content.setContentsMargins(0, 0, SPACE.XS, 0)
        content.setSpacing(SPACE.L)
        self.cover = QLabel()
        self.cover.setObjectName("coverPlaceholder")
        self.cover.setFixedHeight(DESKTOP.DOWNLOAD_DETAIL_ART)
        self.cover.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(self.cover)
        self.song_title = self.label("qt.dl.choose", "subtitle")
        self.song_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content.addWidget(self.song_title)
        self.song_info = self.label("qt.dl.detail_hint", "muted")
        self.song_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.song_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        content.addWidget(self.song_info)
        content.addStretch()
        self.download_one = self.button("layout.download_one", QStyle.StandardPixmap.SP_ArrowDown, True, "primary")
        self.semantic_icons.append((self.download_one, "download"))
        scroll.setWidget(card)
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
        row.addWidget(self.folder_icon)
        self.destination = QLineEdit()
        self.destination.setReadOnly(True)
        row.addWidget(self.destination, 1)
        self.browse = self.button("qt.dl.browse", QStyle.StandardPixmap.SP_DialogOpenButton, True)
        self.semantic_icons.append((self.browse, "folder"))
        row.addWidget(self.browse)
        self.selection = self.button("qt.dl.selected")
        row.addWidget(self.selection)
        self.clear = self.button("search.clear")
        row.addWidget(self.clear)
        self.download = self.button("qt.dl.download", QStyle.StandardPixmap.SP_ArrowDown, True, "primary")
        self.semantic_icons.append((self.download, "download"))
        row.addWidget(self.download)
        self.root_layout.addWidget(self.footer)

    def build_player(self):
        self.player = self.panel()
        row = QHBoxLayout(self.player)
        row.setContentsMargins(SPACE.L, SPACE.S, SPACE.L, SPACE.S)
        row.setSpacing(SPACE.L)
        self.preview_cover = QLabel()
        self.preview_cover.setFixedSize(DESKTOP.PLAYER_ART, DESKTOP.PLAYER_ART)
        self.preview_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_cover.setObjectName('coverPlaceholder')
        self.preview_art_key = None
        row.addWidget(self.preview_cover)
        self.preview_name = QLabel()
        self.preview_name.setTextFormat(Qt.TextFormat.PlainText)
        self.preview_name.setFixedWidth(DESKTOP.PLAYER_NAME_W)
        row.addWidget(self.preview_name)
        self.play = self.button("", QStyle.StandardPixmap.SP_MediaPlay, True)
        self.play.setFixedSize(DESKTOP.PLAYER_ART, DESKTOP.PLAYER_ART)
        self.play.setProperty("role", "playerAction")
        self.semantic_icons.append((self.play, "play"))
        row.addWidget(self.play)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 100)
        self.seek.setEnabled(False)
        row.addWidget(self.seek, 1)
        self.preview_time = self.label("qt.dl.time", wrap=False)
        self.preview_time.setMaximumWidth(220)
        row.addWidget(self.preview_time)
        self.volume_icon = QLabel()
        row.addWidget(self.volume_icon)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setMaximumWidth(DESKTOP.PLAYER_VOLUME_W)
        row.addWidget(self.volume)
        self.root_layout.addWidget(self.player)

    def refresh_preview_identity(self):
        song = (self.preview_browser.song if self.preview_browser else None) or self.current_song
        name = song.title+' · '+song.artist if song else tr('qt.dl.preview')
        self.preview_name.setText(self.preview_name.fontMetrics().elidedText(
            name, Qt.TextElideMode.ElideRight, self.preview_name.width()))
        self.preview_name.setToolTip(name)
        self.preview_name.setAccessibleName(name)
        cache = self.results.cover_cache
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
        for widget, key in self.texts:
            widget.setText(tr(key) if key else "")
        self.search.setPlaceholderText(tr("search.placeholder"))
        self.search.setAccessibleName(tr("search.go"))
        self.destination.setPlaceholderText(tr("qt.dl.destination"))
        self.destination.setAccessibleName(tr("qt.dl.destination"))
        for index, key in enumerate(("search.tab", "search.tasks", "layout.import")):
            self.tabs.setTabText(index, tr(key))
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
        self.play.setAccessibleName(tr("layout.preview"))
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

    def change_theme(self, mode):
        self.mode = mode
        apply_theme(QApplication.instance(), mode)
        self.theme_actions[mode].setChecked(True)
        palette = get_palette(mode)
        for widget, kind in self.semantic_icons:
            icon = QIcon(line_icon(kind, palette.ACCENT_TEXT if widget.property("role") in ("primary", "playerAction") else palette.TEXT_PRIMARY))
            icon.addPixmap(line_icon(kind,palette.TEXT_MUTED).pixmap(24,24),QIcon.Mode.Disabled)
            widget.setIcon(icon)
        self.folder_icon.setPixmap(line_icon("folder", palette.TEXT_SECONDARY).pixmap(20, 20))
        self.title_icon.setPixmap(line_icon("download", palette.ACCENT).pixmap(24, 24))
        self.volume_icon.setPixmap(line_icon("volume", palette.TEXT_PRIMARY).pixmap(20, 20))
        self.results.refresh_art(mode)
        self.show_song(self.current_song)
        if self.preview_browser:
            self.preview_browser.render()

    def connect_browser(self):
        self.song_model.dataChanged.connect(self.refresh_status)
        self.song_model.selectionChanged.connect(self.refresh_status)
        self.results.selectionModel().currentChanged.connect(
            lambda index, _previous: self.show_song(index.data(Qt.ItemDataRole.UserRole)))
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
        if song is None:
            self.cover.clear()
            self.cover.setText(tr("qt.dl.cover"))
            self.song_title.setText(tr("qt.dl.choose"))
            self.song_info.setText(tr("qt.dl.detail_hint"))
            return
        if self.browser:
            self.cover.clear()
            self.cover.setText(tr("qt.search.cover"))
        else:
            self.cover.setPixmap(sample_cover(song.key, self.mode).scaled(
                170, 170, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.song_title.setText(song.title)
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

    def closeEvent(self, event):
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
