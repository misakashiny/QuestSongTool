"""On-demand playlist discovery page and native detail navigation."""
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTabBar, QVBoxLayout, QWidget

from .surfaces import style_table, action_strip
from .icons import line_icon

from ..messages import catalog, tr
from ..song_search import SearchSession
from ..theme import PLAYLIST_UI, SPACE, get_palette
from .cover_browser import CoverCache
from .cover_session import CoverSession
from .playlist_catalog import ORDERS, PlaylistCatalogClient
from .playlist_views import PlaylistCards, PlaylistCardView, PlaylistSongDelegate, PlaylistSongs, playlist_count
from .playlist_files import PlaylistFileClient
from .playlist_widgets import PlaylistInfoCard, PlaylistTable, RoundedCover


class PlaylistBrowser(QObject):
    def __init__(self, window, client=None, covers=None):
        super().__init__(window)
        self.window = window;self.queue = window.download_browser
        self.session = SearchSession(client or PlaylistCatalogClient(self.queue.sandbox))
        self.file_session = SearchSession(PlaylistFileClient(self.queue.sandbox));self.file_generation = None
        self.covers = covers or CoverSession();self.cache = CoverCache()
        if hasattr(self.covers, 'configure_disk'):self.covers.configure_disk(self.queue.sandbox)
        self.generation = None;self.epoch = 0;self.closed = False
        self.list_result = None;self.detail_result = None;self.pending = None
        self.list_query = '';self.list_order = 'Curated';self.scroll = 0
        self.saved_selections = {}
        self.checked = set();self.failed_covers = set();self.state = 'hint';self.error = ''
        self.page = QWidget();self.page.setObjectName('playlistPage')
        self.banner = QLabel();self.banner.setTextFormat(Qt.TextFormat.PlainText)
        self.banner.hide()
        layout = QVBoxLayout(self.page);layout.setContentsMargins(0, 0, 0, 0);layout.setSpacing(0)
        self.browse_bar = QWidget();browse_bar_layout = QVBoxLayout(self.browse_bar);browse_bar_layout.setContentsMargins(0, 0, 0, SPACE.L)
        bar = QHBoxLayout();self.back = QPushButton();self.heading = QLabel();self.import_button = QPushButton();self.stop = QPushButton()
        bar.addWidget(self.heading, 1);bar.addWidget(self.import_button);bar.addWidget(self.stop);browse_bar_layout.addLayout(bar)
        layout.addWidget(self.browse_bar)
        self.heading.setProperty('uiRole','heading')
        self.stack = QStackedWidget();layout.addWidget(self.stack, 1)
        browse = QWidget();body = QVBoxLayout(browse);body.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout();self.query = QLineEdit();self.query.setMaxLength(200);self.order = QComboBox();self.search = QPushButton()
        self.search.setProperty('role','primary')
        for order in ORDERS:self.order.addItem('', order)
        self.order.hide()
        bar.addWidget(self.query, 1);bar.addWidget(self.search);body.addLayout(bar)
        self.categories = QTabBar();self.categories.setExpanding(False)
        for _order in ORDERS:self.categories.addTab('')
        self.categories.currentChanged.connect(self.order.setCurrentIndex)
        self.order.currentIndexChanged.connect(lambda _index:self.search_new())
        body.addWidget(self.categories)
        self.cards = PlaylistCardView(self);self.card_model = PlaylistCards(self);self.cards.setModel(self.card_model);body.addWidget(self.cards, 1)
        self.stack.addWidget(browse)
        detail = QWidget();detail_layout = QVBoxLayout(detail);detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_split = QSplitter(Qt.Orientation.Horizontal);self.detail_split.setObjectName('playlistSplit');self.detail_split.setChildrenCollapsible(False)
        detail_left = PlaylistInfoCard();detail_left.setObjectName('playlistInfo');detail_left.setMinimumWidth(PLAYLIST_UI.INFO_MIN_W)
        left_layout = QVBoxLayout(detail_left);left_layout.setContentsMargins(18, 18, 18, 14);left_layout.setSpacing(14)
        info_scroll = QScrollArea();info_scroll.setWidgetResizable(True);info_scroll.setFrameShape(QFrame.Shape.NoFrame)
        info_scroll.setObjectName('playlistInfoScroll');info_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        info_content = QWidget();info_content.setObjectName('playlistInfoContent')
        info_layout = QVBoxLayout(info_content);info_layout.setContentsMargins(0, 0, 0, 0);info_layout.setSpacing(14)
        self.art = RoundedCover();self.art.setObjectName('playlistDetailCover');self.art.setFixedSize(300, 300)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter);info_layout.addWidget(self.art, 0, Qt.AlignmentFlag.AlignHCenter)
        detail_left.cover = self.art;detail_left.cover_changed = self.schedule_covers
        self.badge = QLabel(self.art);self.badge.setObjectName('playlistBadge');self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setGeometry(14, 14, 106, 30);self.badge.raise_()
        self.title = QLabel();self.title.setWordWrap(True);self.title.setTextFormat(Qt.TextFormat.PlainText);self.title.setProperty('role','title')
        self.title.setObjectName('playlistTitle');self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        info_layout.addWidget(self.title)
        author_row = QWidget();author_layout = QHBoxLayout(author_row);author_layout.setContentsMargins(0, 0, 0, 0);author_layout.setSpacing(SPACE.M)
        self.author_avatar = QLabel();self.author_avatar.setObjectName('playlistAuthorAvatar');self.author_avatar.setFixedSize(44, 44);self.author_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter);author_layout.addWidget(self.author_avatar)
        author_text = QVBoxLayout();author_text.setContentsMargins(0, 0, 0, 0);author_text.setSpacing(0)
        self.author = QLabel();self.author.setProperty('role','subtitle');self.author.setTextFormat(Qt.TextFormat.PlainText)
        self.author_handle = QLabel();self.author_handle.setProperty('role','muted');self.author_handle.setTextFormat(Qt.TextFormat.PlainText)
        self.author.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        author_text.addWidget(self.author);author_text.addWidget(self.author_handle);author_layout.addLayout(author_text, 1);info_layout.addWidget(author_row)
        self.description = QPlainTextEdit();self.description.setReadOnly(True);self.description.setFrameShape(QFrame.Shape.NoFrame)
        self.description.setObjectName('playlistDescription');self.description.setFixedHeight(100)
        info_layout.addWidget(self.description)
        self.meta = QWidget();self.meta.setObjectName('playlistMeta');meta_layout = QGridLayout(self.meta)
        meta_layout.setContentsMargins(0, 0, 0, 0);meta_layout.setHorizontalSpacing(12);meta_layout.setVerticalSpacing(12)
        self.meta_icons = [];self.meta_labels = [];self.meta_values = []
        for row_index in range(3):
            glyph = QLabel();glyph.setFixedSize(22, 22);caption = QLabel();caption.setProperty('role','muted');value = QLabel()
            value.setTextFormat(Qt.TextFormat.PlainText);value.setProperty('role','statValue')
            self.meta_icons.append(glyph);self.meta_labels.append(caption);self.meta_values.append(value)
            meta_layout.addWidget(glyph, row_index, 0);meta_layout.addWidget(caption, row_index, 1);meta_layout.addWidget(value, row_index, 2)
        meta_layout.setColumnStretch(2, 1);info_layout.addWidget(self.meta);info_layout.addStretch(1)
        info_scroll.setWidget(info_content);left_layout.addWidget(info_scroll, 1)
        self.download_all = QPushButton();self.download_all.setProperty('role', 'primary');left_layout.addWidget(self.download_all)
        self.right_panel = QFrame();self.right_panel.setObjectName('playlistDetail');right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(14, 14, 14, 14);right_layout.setSpacing(16)
        self.check_page = QPushButton();self.clear = QPushButton();self.preview = QPushButton();self.download_selected = QPushButton();self.save_button = QPushButton()
        self.clear.setProperty('role','clearSelection')
        self.download_selected.setProperty('role','primary')
        self.download_all_top = QPushButton();self.download_all_top.setProperty('role','primary')
        self.detail_actions = action_strip(self.back,self.check_page,self.clear,self.preview,self.save_button,self.download_selected,self.download_all_top)
        self.detail_actions.setObjectName('playlistDetailActions')
        self.detail_actions.layout().setContentsMargins(0, 0, 0, 0);self.detail_actions.layout().setSpacing(10)
        right_layout.addWidget(self.detail_actions)
        self.table = PlaylistTable();self.songs = PlaylistSongs(self);self.table.setModel(self.songs)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.setColumnHidden(5, True)  # All difficulty data remains available in the row tooltip.
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        style_table(self.table)
        self.table.horizontalHeader().setFixedHeight(50)
        self.table.verticalHeader().setDefaultSectionSize(PLAYLIST_UI.ROW_H)
        self.table.setItemDelegate(PlaylistSongDelegate(self.table))
        right_layout.addWidget(self.table, 1)
        self.detail_hint = QLabel();self.detail_hint.setWordWrap(True);self.detail_hint.setProperty('role','muted');self.detail_hint.hide()
        self.detail_split.addWidget(detail_left);self.detail_split.addWidget(self.right_panel);self.detail_split.setStretchFactor(0, 0);self.detail_split.setStretchFactor(1, 1)
        self.detail_split.setHandleWidth(PLAYLIST_UI.PANEL_GAP);self.detail_split.setSizes([PLAYLIST_UI.INFO_W, 1100])
        detail_layout.addWidget(self.detail_split, 1)
        self.stack.addWidget(detail)
        self.pager = QWidget();self.pager.setObjectName('playlistPager');bar = QHBoxLayout(self.pager);bar.setContentsMargins(0, 4, 0, 2);bar.setSpacing(10)
        self.previous = QPushButton();self.page_label = QLabel();self.page_label.setObjectName('playlistPageNumber');self.next = QPushButton();self.refresh = QPushButton()
        self.status = QLabel();self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);self.status.setProperty('role','muted')
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bar.addWidget(self.status, 1);bar.addWidget(self.previous);bar.addWidget(self.page_label);bar.addWidget(self.next);bar.addWidget(self.refresh)
        body.addWidget(self.pager)
        self.index = window.replace_page('catalog_page',self.page)
        self.timer = QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.poll)
        self.art_timer = QTimer(self);self.art_timer.setSingleShot(True);self.art_timer.setInterval(75);self.art_timer.timeout.connect(self.request_covers)
        self.search.clicked.connect(self.search_new);self.query.returnPressed.connect(self.search_new);self.order.activated.connect(self.search_new)
        self.cards.clicked.connect(self.open_detail);self.cards.activated.connect(self.open_detail)
        self.back.clicked.connect(self.back_to_list);self.stop.clicked.connect(self.cancel)
        self.previous.clicked.connect(lambda:self.turn(-1));self.next.clicked.connect(lambda:self.turn(1));self.refresh.clicked.connect(self.reload)
        self.import_button.clicked.connect(self.queue.importer.open_playlist)
        self.check_page.clicked.connect(self.mark_page);self.clear.clicked.connect(self.clear_checks)
        self.save_button.clicked.connect(self.save_playlist)
        self.preview.clicked.connect(self.play_selected);self.download_selected.clicked.connect(lambda:self.enqueue(False));self.download_all.clicked.connect(lambda:self.enqueue(True));self.download_all_top.clicked.connect(lambda:self.enqueue(True))
        self.table.selectionModel().selectionChanged.connect(self.render)
        self.table.doubleClicked.connect(lambda _index:self.play_selected(restart=True))
        for view in (self.cards, self.table):view.verticalScrollBar().valueChanged.connect(self.schedule_covers)
        window.tabs.currentChanged.connect(self.activated)
        self.page.installEventFilter(self)
        self.render()

    def eventFilter(self, watched, event):
        if watched is self.page and event.type() == QEvent.Type.Resize:
            width = event.size().width()
            sidebar = min(PLAYLIST_UI.INFO_W, max(PLAYLIST_UI.INFO_MIN_W, round(width*.247)))
            self.detail_split.setSizes([sidebar, max(1, width-sidebar-PLAYLIST_UI.PANEL_GAP)])
        return super().eventFilter(watched, event)

    def activated(self, index):
        if self.closed:
            return
        self.window.search.setVisible(index != self.index);self.window.search_button.setVisible(index != self.index)
        self.window.search_host.setVisible(index != self.index)
        for button in (self.window.selection, self.window.clear, self.window.download):
            button.setVisible(index != self.index)
        if hasattr(self.window, 'footer'):
            self.window.footer.setVisible(index != self.index)
        self.banner.hide()
        self.window.scan_status.setVisible(index != self.index and bool(self.window.scan_status.text()))
        self.window.source_label.setVisible(index != self.index)
        self.window.adapt_chrome()
        if index == self.index:
            if self.list_result is None and self.generation is None and self.detail_result is None:
                self.search_new()
            else:
                self.schedule_covers()
        else:
            self.cancel()

    def request(self, request, page, order=''):
        if self.closed:
            return
        self.covers.cancel();self.art_timer.stop();self.epoch += 1
        self.pending = (request, page, order);self.error = '';self.state = 'loading'
        self.generation = self.session.request(request, page, order);self.timer.start();self.render()

    def save_selection(self):
        if self.detail_result:
            key = self.detail_result['playlist']['id']
            self.saved_selections[key] = (set(self.checked), self.detail_result['page'], self.table.verticalScrollBar().value())
            if len(self.saved_selections)>20:self.saved_selections.pop(next(iter(self.saved_selections)))

    def search_new(self, *_args):
        self.save_selection()
        self.stack.setCurrentIndex(0);self.checked.clear();self.detail_result = None
        self.request(dict(kind='list', query=self.query.text().strip()), 0, self.order.currentData())

    def open_detail(self, index):
        if self.generation is not None or not index.isValid() or self.stack.currentIndex() != 0:
            return
        row = index.data(Qt.ItemDataRole.UserRole)
        if not row:
            return
        self.save_selection()
        checked, page, self.restore_scroll = self.saved_selections.get(row['id'], (set(),0,0))
        self.scroll = self.cards.verticalScrollBar().value();self.checked = set(checked);self.detail_result = None
        self.songs.replace([]);self.art.clear();self.stack.setCurrentIndex(1)
        self.request(dict(kind='detail', id=row['id']), page)

    def back_to_list(self):
        self.save_selection()
        self.cancel();self.stack.setCurrentIndex(0);self.detail_result = None;self.checked.clear()
        self.cards.verticalScrollBar().setValue(self.scroll);self.state = 'ready';self.render();self.schedule_covers()

    def turn(self, delta):
        value = self.list_result if self.stack.currentIndex() == 0 else self.detail_result
        if self.generation is not None or not value or (delta > 0 and not value['has_next']) or value['page']+delta < 0:
            return
        if self.stack.currentIndex() == 0:
            self.request(dict(kind='list', query=self.list_query), value['page']+delta, self.list_order)
        else:
            self.request(dict(kind='detail', id=value['playlist']['id']), value['page']+delta)

    def reload(self):
        self.failed_covers.clear()
        if self.stack.currentIndex() == 0:
            page = self.list_result['page'] if self.list_result else 0
            self.request(dict(kind='list', query=self.list_query), page, self.list_order)
        elif self.pending:
            request, page, order = self.pending
            self.request(request, page, order)

    def cancel(self):
        self.session.cancel();self.generation = None;self.covers.cancel();self.epoch += 1;self.art_timer.stop()
        self.state = 'cancelled';self.timer.stop();self.render()

    def poll(self):
        if self.closed:
            return
        changed = False
        for event in self.session.tasks.drain():
            data = event.payload
            if not isinstance(data, dict) or self.generation is None or data.get('generation') != self.generation:
                continue
            if event.kind not in ('search', 'search_error'):
                continue
            changed = True
            self.generation = None
            if event.kind == 'search_error':
                self.state = 'failed';self.error = data.get('code', '')
            else:
                value = data['result'];self.state = 'ready'
                if value['kind'] == 'list':
                    self.list_query = self.pending[0]['query'];self.list_order = self.pending[2]
                    self.list_result = value;self.card_model.replace(value['rows']);self.cards.scrollToTop()
                else:
                    self.detail_result = value;self.songs.replace(value['rows']);self.table.verticalScrollBar().setValue(getattr(self,'restore_scroll',0));self.restore_scroll = 0
                    if value.get('complete'):
                        self.checked.intersection_update(song.key for song in self.songs.rows if song)
                self.schedule_covers()
        self.covers.pump()
        for event in self.covers.tasks.drain():
            data = event.payload
            if event.kind == 'cover' and data.get('generation') == self.epoch:
                changed = True
                if data.get('data') and self.cache.put(data['url'], data['data']):
                    self.cards.viewport().update();self.songs.changed()
                else:
                    self.failed_covers.add(data['url'])
            elif event.kind == 'search_error' and data.get('generation') == self.covers.generation:
                changed = True;self.error = data.get('code','cover')
                self.failed_covers.update(row['coverURL'] for row in self.card_model.rows)
                if self.detail_result:self.failed_covers.add(self.detail_result['playlist']['coverURL'])
                self.failed_covers.update(song.cover_url for song in self.songs.rows if song)
        for event in self.file_session.tasks.drain():
            data = event.payload
            if not isinstance(data,dict) or self.file_generation is None or data.get('generation') != self.file_generation:continue
            if event.kind not in ('search','search_error'):continue
            self.file_generation = None;changed = True
            if event.kind == 'search':
                self.state = 'saved';self.error = '';self.save_button.setToolTip(data['result']['path'])
            else:self.state = 'failed';self.error = data.get('code','unexpected')
        if changed:
            self.render()
            if self.covers.completed:self.schedule_covers()
        if self.generation is None and self.file_generation is None and self.covers.completed and not self.art_timer.isActive():
            self.timer.stop()

    def schedule_covers(self, *_args):
        if hasattr(self, 'art_timer') and not self.closed and self.window.tabs.currentIndex() == self.index and self.generation is None:
            self.art_timer.start()

    def request_covers(self):
        if self.closed or self.window.tabs.currentIndex() != self.index:
            return
        urls = []
        if self.stack.currentIndex() == 0:
            for i, row in enumerate(self.card_model.rows):
                if self.cards.visualRect(self.card_model.index(i)).intersects(self.cards.viewport().rect()):
                    urls.append(row['coverURL'])
        elif self.detail_result:
            urls.append(self.detail_result['playlist']['coverURL'])
            for i, song in enumerate(self.songs.rows):
                if song and self.table.visualRect(self.songs.index(i, 2)).intersects(self.table.viewport().rect()):
                    urls.append(song.cover_url)
        urls = [u for u in dict.fromkeys(urls) if u and u not in self.cache.sources and u not in self.failed_covers][:24]
        if hasattr(self.covers,'prioritize'):self.covers.prioritize(urls)
        if urls and self.covers.completed:
            self.epoch += 1;self.covers.request([{'coverURL':u} for u in urls], self.epoch, '');self.timer.start()
        self.render()

    def save_playlist(self):
        if self.closed or not self.detail_result or self.generation is not None or self.file_generation is not None:return
        identity = self.detail_result['playlist']['id']
        self.file_generation = self.file_session.request(dict(action='online',id=identity),0,'')
        self.state = 'saving';self.timer.start();self.render()

    def clear_checks(self):
        self.checked.clear();self.songs.changed();self.render()

    def mark_page(self):
        keys = {song.key for song in self.songs.rows if song}
        if len(keys | self.checked) > 200:
            self.state = 'limit'
        else:
            self.checked |= keys
        self.songs.changed();self.render()

    def current_song(self):
        index = self.table.currentIndex()
        return self.songs.rows[index.row()] if index.isValid() and index.row() < len(self.songs.rows) else None

    def play_selected(self, restart=False):
        song = self.current_song()
        if song and self.window.preview_browser and self.generation is None:
            if restart:self.window.preview_browser.play_song(song,restart=True)
            else:self.window.preview_browser.play_song(song)

    def all_keys(self):
        if not self.detail_result or not self.detail_result['complete']:
            return []
        keys = list(dict.fromkeys(song.key for song in self.songs.rows if song))
        return keys if 0 < len(keys) <= 200 else []

    def enqueue(self, entire):
        if self.closed or self.queue.busy or self.generation is not None:
            return
        keys = self.all_keys() if entire else sorted(self.checked)
        if not keys or len(keys) > 200:
            return
        target = str(self.queue.target);identity = self.detail_result['playlist']['id'] if self.detail_result else None
        if QMessageBox.question(self.window, tr('qt.playlist_dl.title'), tr('qt.playlist_dl.confirm', count=len(keys), path=target)) != QMessageBox.StandardButton.Yes:
            return
        if (self.closed or self.queue.closed or self.queue.busy or str(self.queue.target) != target
                or self.generation is not None or not self.detail_result or self.detail_result['playlist']['id'] != identity
                or keys != (self.all_keys() if entire else sorted(self.checked))):
            return
        self.queue.enqueue(keys)
        if entire:self.save_playlist()
        if self.queue.busy:self.window.show_tasks()

    def render(self, *_args):
        self.window.refresh_preview_navigation()
        self.window.tabs.setTabText(self.index, tr('qt.catalog.title'))
        self.banner.setText(tr('qt.catalog.banner'))
        self.heading.setText(tr('qt.catalog.detail_title' if self.stack.currentIndex() == 1 else 'qt.catalog.title'));detail = self.stack.currentIndex() == 1
        self.browse_bar.setVisible(not detail)
        target_layout = self.right_panel.layout() if detail else self.stack.widget(0).layout()
        if self.pager.parentWidget() is not target_layout.parentWidget():
            target_layout.addWidget(self.pager)
        palette = get_palette(self.window.mode.currentData() if hasattr(self.window.mode, 'currentData') else self.window.mode)
        def icon(button, kind):
            foreground = palette.ACCENT_TEXT if button.property('role') == 'primary' else palette.DANGER if button.property('role') == 'danger' else palette.TEXT_PRIMARY
            glyph = QIcon(line_icon(kind, foreground));glyph.addPixmap(line_icon(kind, palette.TEXT_MUTED).pixmap(24,24), QIcon.Mode.Disabled)
            button.setIcon(glyph);button.setIconSize(QSize(20, 20))
        icon(self.back, 'back');icon(self.import_button, 'link');icon(self.stop, 'stop');icon(self.search, 'search')
        icon(self.previous, 'chevron_left');icon(self.next, 'chevron_right');icon(self.refresh, 'refresh')
        icon(self.check_page, 'check_box');icon(self.clear, 'trash');icon(self.preview, 'play')
        icon(self.save_button, 'add');icon(self.download_selected, 'download');icon(self.download_all, 'download');icon(self.download_all_top, 'download')
        self.save_button.setText(tr('qt.catalog.save'));self.save_button.setEnabled(bool(self.detail_result) and self.generation is None and self.file_generation is None)
        for button, key in ((self.back,'back'),(self.import_button,'import'),(self.stop,'stop'),(self.search,'search'),(self.previous,'previous'),(self.next,'next'),(self.refresh,'refresh'),(self.check_page,'check_page'),(self.clear,'clear'),(self.preview,'preview'),(self.download_all,'all')):
            button.setText(tr('qt.catalog.'+key))
        self.download_selected.setText(tr('qt.catalog.selected', count=len(self.checked)))
        self.back.setText(tr('qt.catalog.back_short'));self.check_page.setText(tr('qt.catalog.check_short'))
        self.clear.setText(tr('qt.catalog.clear_short'));self.preview.setText(tr('qt.catalog.preview_short'))
        self.download_all_top.setText(self.download_all.text())
        self.query.setPlaceholderText(tr('qt.catalog.query'));self.query.setAccessibleName(tr('qt.catalog.query'))
        for i, order in enumerate(ORDERS):
            self.order.setItemText(i, tr('qt.catalog.order.'+order));self.categories.setTabText(i,tr('qt.catalog.order.'+order))
        self.cards.setAccessibleName(tr('qt.catalog.title'));self.table.setAccessibleName(tr('qt.playlists.songs'))
        busy = self.generation is not None
        self.back.setVisible(detail);self.stop.setEnabled(busy or not self.covers.completed)
        self.search.setEnabled(not busy);self.order.setEnabled(not busy);self.refresh.setEnabled(not busy)
        self.categories.setEnabled(not busy)
        self.cards.setEnabled(not busy);self.table.setEnabled(not busy)
        value = self.detail_result if detail else self.list_result
        self.previous.setEnabled(not busy and bool(value) and value['page'] > 0)
        self.next.setEnabled(not busy and bool(value) and value['has_next'])
        self.page_label.setText(str(value['page']+1 if value else 1))
        self.page_label.setToolTip(tr('qt.catalog.page', page=value['page']+1 if value else 1))
        self.download_all.setEnabled(not busy and not self.queue.busy and bool(self.all_keys()))
        self.download_all_top.setEnabled(self.download_all.isEnabled())
        self.download_selected.setEnabled(not busy and not self.queue.busy and bool(self.checked))
        self.check_page.setEnabled(not busy and bool(self.songs.rows));self.clear.setEnabled(bool(self.checked))
        song = self.current_song();self.preview.setEnabled(not busy and bool(song and song.preview_url and self.window.preview_browser))
        dynamic = bool(self.detail_result and self.detail_result['playlist'].get('dynamic'))
        self.detail_hint.setText(tr('qt.catalog.detail_hint')+('\n'+tr('qt.catalog.dynamic_hint') if dynamic else ''))
        self.download_all.setToolTip(self.detail_hint.text());self.download_all_top.setToolTip(self.detail_hint.text())
        self.badge.setText(tr('qt.catalog.dynamic' if dynamic else 'qt.catalog.featured'))
        self.badge.setFixedWidth(min(self.art.width()-24, self.badge.fontMetrics().horizontalAdvance(self.badge.text())+28))
        self.author_avatar.setPixmap(line_icon('playlist', palette.ACCENT).pixmap(22, 22))
        if self.detail_result:
            row = self.detail_result['playlist'];self.title.setText(row['title'])
            author = row['author'] or tr('qt.catalog.unknown_author')
            rating = '—' if row['rating'] is None else f"{row['rating']*100:.1f}%"
            self.author.setText(author);self.author.setToolTip(author);self.author_handle.setText('BeatSaver')
            for label, text in zip(self.meta_values, (playlist_count(row,'qt.catalog.songs_count'), rating, row['updated'][:10] or '—')):
                label.setText(text)
            if self.description.toPlainText() != row['description']:self.description.setPlainText(row['description'])
            art = self.cache.image(row['coverURL'], self.art.size(), self.art.devicePixelRatioF())
            if art is not None:self.art.setPixmap(art)
        else:
            self.title.setText(tr('qt.catalog.loading') if detail else '');self.author.clear();self.author_handle.clear();self.description.clear()
            for label in self.meta_values:label.clear()
        for label, key in zip(self.meta_labels, ('songs_label','rating_label','updated_label')):label.setText(tr('qt.catalog.'+key))
        for label, kind in zip(self.meta_icons, ('music','star','calendar')):label.setPixmap(line_icon(kind, palette.TEXT_PRIMARY).pixmap(22, 22))
        self.badge.setVisible(detail and bool(self.detail_result and (dynamic or self.detail_result['playlist'].get('curated'))))
        message = tr('qt.catalog.'+self.state)
        if self.state == 'ready' and value:
            if detail and value['rows']:
                message = tr('qt.catalog.page_selection', count=len(self.checked), shown=len(value['rows']))+' · '+playlist_count(value['playlist'],'qt.catalog.total_count')
            else:message = tr('qt.catalog.results', count=len(value['rows'])) if value['rows'] else tr('qt.catalog.empty')
            if value.get('unavailable'):
                message += ' · ' + tr('qt.catalog.unavailable_count', count=value['unavailable'])
        if self.error and 'dl.error.'+self.error in catalog('zh'):message += ' '+tr('dl.error.'+self.error)
        self.status.setText(message)
        self.table.horizontalHeader().viewport().update()
        headers = tuple(self.songs.headerData(i, Qt.Orientation.Horizontal) for i in range(10))
        if self.songs.headers != headers:
            self.songs.headers = headers;self.songs.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 9)
        self.cards.viewport().update()

    def close(self):
        self.closed = True;self.cancel();self.session.close();self.covers.close();self.file_session.close()

    def wait_closed(self):
        for thread in tuple(self.session.tasks._threads):thread.join()
        for thread in tuple(self.file_session.tasks._threads):thread.join()
        self.covers.wait_closed()
