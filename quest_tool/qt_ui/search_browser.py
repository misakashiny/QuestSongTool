"""UI-thread adapter for the existing message-only SearchSession."""
import copy
import json
import math

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QComboBox, QPushButton
from PySide6.QtGui import QKeySequence, QShortcut

from ..messages import catalog, tr
from ..song_search import ORDERS, SearchSession
from ..search_filters import browse_params, filter_params
from .filter_dialog import FilterDialog
from .filter_tools import FilterTools
from .song_model import Song
from .page_cache import PageCache
from .icons import line_icon
from ..theme import get_palette


class SearchBrowser(QObject):
    def __init__(self, window, session=None, prefetch_session=None):
        super().__init__(window)
        self.window = window
        self.session = session if session is not None else SearchSession()
        self.cache = PageCache()
        self.cache_hits = 0
        self.from_cache = False
        self.context = None
        self.prefetch_session = prefetch_session if prefetch_session is not None else (SearchSession() if session is None else None)
        self.prefetch_generation = None
        self.prefetch_key = None
        self.waiting_prefetch = False
        self.next_plan = None
        self.prefetch_delay = QTimer(window)
        self.prefetch_delay.setSingleShot(True)
        self.prefetch_delay.setInterval(300)
        self.prefetch_delay.timeout.connect(self.start_prefetch)
        self.status_expiry = QTimer(window)
        self.status_expiry.setSingleShot(True)
        self.status_expiry.timeout.connect(self.refresh_status)
        self.generation = None
        self.closed = False
        self.preferences = None
        self.state = "idle"
        self.error = ""
        self.limit = False
        self.params = ("", "Curated", "Relevance")
        self.signature = None
        self.filters = {}
        self.filter_window = None
        self.pending_page = 0
        self.pending_size = 48
        self.timer = QTimer(window)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.poll)
        controls = window.search_controls
        self.status = window.note
        self.sort = QComboBox()
        for order in ORDERS:
            self.sort.addItem("", order)
        controls.insertWidget(2, self.sort)
        self.cancel_button = QPushButton()
        self.retry_button = QPushButton()
        controls.insertWidget(3, self.cancel_button)
        controls.insertWidget(4, self.retry_button)
        self.refresh_button = QPushButton()
        self.refresh_button.setFixedWidth(42)
        self.refresh_button.setIcon(line_icon('refresh',get_palette(window.mode).TEXT_PRIMARY))
        window.semantic_icons.append((self.refresh_button,'refresh'))
        controls.insertWidget(5, self.refresh_button)
        self.refresh_button.clicked.connect(self.refresh_current)
        self.refresh_shortcut = QShortcut(QKeySequence('F5'), window)
        self.refresh_shortcut.setAutoRepeat(False)
        self.refresh_shortcut.activated.connect(self.refresh_current)
        window.tabs.currentChanged.connect(self.refresh_status)
        window.unavailable.remove(window.search_button)
        window.search_button.setEnabled(True)
        window.unavailable.remove(window.filter_button)
        window.filter_button.setEnabled(True)
        window.filter_button.setToolTip("")
        window.filter_button.clicked.connect(self.open_filters)
        window.search.returnPressed.connect(self.search)
        window.search_button.clicked.connect(self.search)
        window.categories.group.buttonClicked.connect(self.category_changed)
        self.sort.currentIndexChanged.connect(self.category_changed)
        self.cancel_button.clicked.connect(self.cancel)
        self.retry_button.clicked.connect(lambda: self.request(self.pending_page, force=True))
        window.song_model.limitReached.connect(self.show_limit)
        self.filter_tools = FilterTools(self)

    def category(self):
        return next(key for key, button in self.window.categories.buttons.items() if button.isChecked())

    def search(self):
        self.window.categories.buttons["Search"].setChecked(True)
        self.window.update_category()
        self.category_changed()

    def category_changed(self, *_args):
        if self.closed:
            return
        mode = self.category()
        _extra, preset_order = browse_params(mode, {})
        if preset_order:
            self.sort.blockSignals(True)
            self.sort.setCurrentIndex(self.sort.findData(preset_order))
            self.sort.blockSignals(False)
        params = (self.window.search.text().strip(), self.category(), self.sort.currentData())
        signature = (*params, repr(self.filters))
        if signature != self.signature:
            self.window.song_model.clear_checks()
        self.params, self.signature = params, signature
        query, mode, _order = params
        if mode == "Search" and (not query or len(query) > 200 or any(ord(c) < 32 for c in query)):
            self.stop_prefetch()
            self.cache.clear()
            self.session.cancel()
            self.generation = None
            self.timer.stop()
            self.state, self.error = "error", "query"
            self.replace_rows((), 0, False)
            self.refresh_status()
            return
        self.request(0, force=True)

    def stop_prefetch(self):
        self.prefetch_delay.stop()
        self.next_plan = None
        self.prefetch_generation = None
        self.prefetch_key = None
        self.waiting_prefetch = False
        if self.prefetch_session:
            self.prefetch_session.cancel()
        if self.window.cover_browser:
            self.window.cover_browser.cancel_warm()

    def refresh_current(self):
        if self.closed or self.state not in ('ready', 'empty') or self.window.tabs.currentIndex() != 0:
            return
        if self.window.isEnabled() and self.window.isActiveWindow():
            self.request(self.window.song_model.page, force=True)

    def request(self, page=0, force=False):
        if self.closed:
            return
        self.limit = False
        self.from_cache = False
        self.pending_page = page
        self.pending_size = int(self.window.columns.currentText())*int(self.window.rows.currentText())
        context = (*self.params, json.dumps(self.filters, sort_keys=True), self.pending_size)
        changed = context != self.context
        if changed or force:
            self.stop_prefetch()
            self.cache.clear()
        self.context = context
        self.state, self.error = "loading", ""
        if changed:
            self.replace_rows((), page, False)
        key = (context, page)
        cached = self.cache.get(key) if self.cacheable() and not force else None
        self.session.cancel()
        self.generation = None
        if cached is not None:
            self.cache_hits += 1
            self.from_cache = True
            self.stop_prefetch()
            self.deliver(cached, page, store=False)
        elif self.prefetch_generation is not None and self.prefetch_key == key:
            self.waiting_prefetch = True
        else:
            self.stop_prefetch()
            self.foreground()
        self.sync_timer()
        self.refresh_status()

    def cacheable(self):
        return self.params[1] != "Random" and self.params[2] != "Random"

    def foreground(self):
        query, mode, order = self.params
        self.generation = self.session.request(query, self.pending_page, order,
            filters=copy.deepcopy(self.filters), mode=mode, page_size=self.pending_size)

    def sync_timer(self):
        if self.generation is not None or self.prefetch_generation is not None:
            self.timer.start()
        else:
            self.timer.stop()

    @staticmethod
    def decode(result, page, size):
        if not isinstance(result, dict) or type(result.get("page")) is not int or result["page"] != page:
            raise ValueError("page")
        rows = result.get("rows")
        if not isinstance(rows, (list, tuple)) or len(rows) > size or type(result.get("has_next")) is not bool:
            raise ValueError("rows")
        songs = tuple(Song.from_result(item) for item in rows)
        if len({song.key for song in songs}) != len(songs):
            raise ValueError("duplicate")
        return songs, result["has_next"]

    def deliver(self, snapshot, page, store=True):
        songs, has_next = snapshot
        self.state, self.error = ("ready" if songs else "empty"), ""
        self.replace_rows(songs, page, has_next)
        if store and self.cacheable():
            self.cache.put((self.context, page), songs, has_next)
        if self.prefetch_session and self.cacheable() and has_next and page < 1000:
            self.next_plan = (self.context, page+1)
            self.prefetch_delay.start()
        self.refresh_status()

    def start_prefetch(self):
        plan, self.next_plan = self.next_plan, None
        if self.closed or plan is None or plan[0] != self.context or self.state != "ready":
            return
        cached = self.cache.get(plan)
        if cached is not None:
            if self.window.cover_browser:
                self.window.cover_browser.warm(cached[0])
            self.refresh_status()
            return
        query, mode, order = self.params
        self.prefetch_key = plan
        self.prefetch_generation = self.prefetch_session.request(query, plan[1], order,
            filters=copy.deepcopy(self.filters), mode=mode, page_size=self.pending_size)
        self.sync_timer()
        self.refresh_status()

    def replace_rows(self, songs, page, has_next):
        window = self.window
        window.song_model.replace_page(songs, page, self.pending_size, has_next)
        window.switch_view("covers" if window.views["covers"].isChecked() else "list")
        if songs:
            window.results.setCurrentIndex(window.song_model.index(0))
        else:
            window.show_song(None)
        window.results.scrollToTop()
        window.refresh_status()
        if window.cover_browser:
            window.cover_browser.page(songs)

    def poll(self):
        if self.closed:
            return
        for message in self.session.tasks.drain():
            data = message.payload
            if self.generation is None or not isinstance(data, dict) or data.get("generation") != self.generation:
                continue
            if message.kind not in ("search", "search_error"):
                continue
            self.generation = None
            if message.kind == "search":
                try:
                    snapshot = self.decode(data.get("result"), self.pending_page, self.pending_size)
                except (KeyError, TypeError, ValueError, AttributeError):
                    self.state, self.error = "error", "metadata"
                else:
                    self.deliver(snapshot, self.pending_page)
            else:
                self.state, self.error = "error", data.get("code", "unexpected")
            self.refresh_status()
        prefetch_updated = False
        if self.prefetch_session:
            for message in self.prefetch_session.tasks.drain():
                data = message.payload
                if self.prefetch_generation is None or not isinstance(data, dict) or data.get("generation") != self.prefetch_generation:
                    continue
                if message.kind not in ("search", "search_error"):
                    continue
                key, waiting = self.prefetch_key, self.waiting_prefetch
                prefetch_updated = True
                self.prefetch_generation = None
                self.prefetch_key = None
                self.waiting_prefetch = False
                if key is None or key[0] != self.context:
                    continue
                try:
                    if message.kind != "search":
                        raise ValueError("prefetch")
                    snapshot = self.decode(data.get("result"), key[1], self.pending_size)
                except (KeyError, TypeError, ValueError, AttributeError):
                    if waiting:
                        self.foreground()
                else:
                    self.cache.put(key, *snapshot)
                    if waiting:
                        self.deliver(snapshot, key[1])
                    elif self.window.cover_browser:
                        self.window.cover_browser.warm(snapshot[0])
        self.sync_timer()
        if prefetch_updated:
            self.refresh_status()

    def cancel(self):
        if self.closed:
            return
        self.generation = None
        self.session.cancel()
        self.timer.stop()
        self.stop_prefetch()
        self.state = "cancelled"
        self.refresh_status()

    def show_limit(self):
        self.limit = True
        self.refresh_status()

    def open_filters(self):
        if self.closed:
            return
        if self.filter_window is not None:
            self.filter_window.raise_()
            self.filter_window.activateWindow()
            return
        dialog = FilterDialog(self.window, self.filters)
        self.filter_window = dialog
        dialog.applied.connect(self.apply_filters)
        dialog.finished.connect(lambda _result: self.finish_filters(dialog))
        dialog.open()

    def finish_filters(self, dialog):
        if self.filter_window is dialog:
            self.filter_window = None
        dialog.deleteLater()

    def apply_filters(self, values):
        if self.closed:
            return
        filter_params(values)
        self.filters = copy.deepcopy(values)
        self.filter_tools.refresh()
        if not self.window.search.text().strip() and self.category() == "Search":
            self.window.categories.buttons["Latest"].setChecked(True)
            self.window.update_category()
        self.category_changed()

    def retranslate(self):
        if self.preferences is not None:
            self.preferences.refresh()
        self.filter_tools.refresh()
        for index in range(self.sort.count()):
            self.sort.setItemText(index, tr("search.order." + self.sort.itemData(index)))
        self.sort.setAccessibleName(tr("qt.search.sort"))
        self.cancel_button.setText(tr("qt.search.cancel"))
        self.retry_button.setText(tr("qt.search.retry"))
        self.refresh_button.setText('')
        self.refresh_button.setToolTip(tr('qt.search.refresh_hint'))
        self.refresh_button.setAccessibleName(tr('qt.search.refresh_page'))
        self.window.note.setText(tr("qt.search.note"))
        self.refresh_status()

    def refresh_status(self, *_args):
        if self.closed:
            return
        self.status_expiry.stop()
        window = self.window
        model = window.song_model
        busy = self.state == "loading"
        refreshable = self.state in ('ready', 'empty') and window.tabs.currentIndex() == 0
        self.refresh_button.setEnabled(refreshable)
        self.refresh_shortcut.setEnabled(refreshable)
        self.refresh_button.setVisible(self.state in ('ready', 'empty'))
        count = len(self.filter_tools.menu.actions())
        window.filter_button.setText(tr("filter.active", count=count)
                                     if count else tr("qt.dl.filter"))
        window.previous.setEnabled(not busy and model.page > 0)
        window.next.setEnabled(not busy and model.has_next and model.page < 1000)
        window.page_number.setText(tr("search.page", page=model.page+1))
        window.select_page.setEnabled(not busy and bool(model.rowCount()))
        self.cancel_button.setEnabled(busy)
        self.retry_button.setEnabled(not busy and self.state in ("error", "cancelled"))
        self.sort.setEnabled(self.category() == "Search")
        self.sort.setVisible(self.category() == "Search")
        self.cancel_button.setVisible(busy)
        self.retry_button.setVisible(self.state in ("error", "cancelled"))
        if len(model.checked) < 200:
            self.limit = False
        key = {"loading": "search.loading", "empty": "search.empty", "cancelled": "qt.search.cancelled"}.get(self.state)
        if self.state == "error":
            key = "dl.error." + self.error
            if key not in catalog("zh"):
                key = "dl.error.unexpected"
        elif self.limit and not busy:
            key = "search.limit"
        text = tr(key) if key else tr('qt.search.note')
        if not key and self.state == 'ready':
            next_key = (self.context, model.page+1)
            if self.cacheable() and self.cache.get(next_key, touch=False) is not None:
                text = tr('qt.search.next_ready')
                self.status_expiry.start(max(1, math.ceil(self.cache.remaining(next_key)*1000)))
            elif self.prefetch_generation is not None:
                text = tr('qt.search.next_loading')
            elif self.next_plan is not None:
                text = tr('qt.search.next_planned')
            if self.from_cache:
                text = tr('qt.search.cached')+' · '+text
        self.status.setText(text)
        self.status.setToolTip(text)
        window.results.empty_text = self.status.text() if not model.rowCount() else ""
        window.results.viewport().update()
        self.window.results.setAccessibleName(tr("qt.search.browser"))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.filter_tools.close()
        self.status_expiry.stop()
        self.refresh_shortcut.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.stop_prefetch()
        self.cache.clear()
        if self.prefetch_session:
            self.prefetch_session.close()
        self.generation = None
        self.timer.stop()
        if self.filter_window is not None:
            self.filter_window.reject()
        self.session.close()
