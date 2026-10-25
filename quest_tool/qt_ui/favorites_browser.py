"""On-demand bookmarks, reusing the selected-song review and shared player."""
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from ..downloads import DownloadError
from ..messages import tr
from .download_selection import SelectedDialog
from .favorites_store import FavoritesStore


class FavoritesDialog(SelectedDialog):
    def selection_changed(self, *_args):
        super().selection_changed()
        count = len(self.selected())
        self.download.setText(tr('qt.favorites.select', count=count))
        self.download.setEnabled(bool(count) and not self.controller.closed)
        self.remove.setEnabled(bool(count) and not self.controller.error)

    def remove_selected(self):
        self.controller.remove([song.key for song in self.selected()])

    def enqueue(self):
        songs = self.selected()
        model = self.controller.window.song_model
        if len(model.checked | {song.key for song in songs}) > 200:
            self.controller.message = tr('qt.favorites.selection_limit')
        else:
            visible = {song.key: song for song in model.visible_songs()}
            for song in songs:
                if song.key not in model.checked:
                    model.selected_records[song.key] = visible.get(song.key, song)
                model.checked.add(song.key)
            model.notify_checks()
            self.controller.message = tr('qt.favorites.selected', count=len(songs))
        self.refresh()

    def refresh(self):
        super().refresh()
        self.summary.setText(tr('qt.favorites.summary', count=len(self.model.songs)))
        message = tr('qt.favorites.error') if self.controller.error or self.controller.write_error else self.controller.message
        self.hint.setText((message+'\n' if message else '')+tr('qt.favorites.hint'))
        self.selection_changed()

    def retranslate(self):
        super().retranslate()
        super().setWindowTitle(tr('qt.favorites.title'))
        self.table.setAccessibleName(tr('qt.favorites.title'))
        self.search.setPlaceholderText(tr('qt.favorites.search'))
        self.search.setAccessibleName(tr('qt.favorites.search'))
        self.remove.setText(tr('qt.favorites.remove'))
        self.remove.setToolTip(tr('qt.favorites.remove_hint'))
        self.download.setToolTip(tr('qt.favorites.select_hint'))


class FavoritesBrowser(QObject):
    def __init__(self, window, sandbox):
        super().__init__(window)
        self.window = window
        self.store = FavoritesStore(sandbox)
        self.values = {}
        self.error = self.closed = False
        self.write_error = False
        self.message = ''
        self.dialog = None
        menu = window.options.menu()
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        self.toggle_action = menu.addAction('')
        self.toggle_action.setCheckable(True)
        self.list_action = menu.addAction('')
        self.toggle_action.triggered.connect(self.toggle)
        window.favorite.clicked.connect(self.toggle)
        self.list_action.triggered.connect(self.open)
        menu.aboutToShow.connect(self.reload)
        if window.cover_browser:
            window.cover_browser.imagesChanged.connect(self.refresh_art)
        self.reload()

    @property
    def index(self):
        return self.window.selection_browser.index

    def records(self):
        return tuple(self.values.values())

    def reload(self):
        if self.closed:
            return
        try:
            self.values = self.store.load()
            self.error = False
        except (OSError, ValueError, TypeError, DownloadError):
            self.values = {}
            self.error = True
        self.refresh()

    def refresh(self):
        song = self.window.current_song
        self.toggle_action.setText(tr('qt.favorites.toggle'))
        self.toggle_action.setChecked(bool(song and song.key in self.values))
        valid = bool(song and song.key and len(song.key) <= 12 and all(c in '0123456789abcdef' for c in song.key)
                     and len(song.digest) == 40 and all(c in '0123456789abcdef' for c in song.digest))
        self.toggle_action.setEnabled(valid and not self.closed and not self.error)
        self.toggle_action.setToolTip(tr('qt.favorites.hint'))
        self.window.favorite.setText(tr('qt.ui.favorited' if self.toggle_action.isChecked() else 'qt.ui.favorite'))
        self.window.favorite.setChecked(self.toggle_action.isChecked())
        self.window.favorite.setEnabled(self.toggle_action.isEnabled())
        self.window.favorite.setToolTip(tr('qt.favorites.error') if self.error or self.write_error else tr('qt.favorites.hint'))
        self.list_action.setText(tr('qt.favorites.error') if self.error or self.write_error else tr('qt.favorites.open', count=len(self.values)))
        self.list_action.setEnabled(not self.closed)
        if self.dialog:
            self.dialog.refresh()

    def toggle(self, _checked=False):
        song = self.window.current_song
        if self.closed or song is None:
            return
        try:
            self.values = self.store.update(remove=(song.key,)) if song.key in self.values else self.store.update(song=song)
            self.error = self.write_error = False
            self.message = ''
        except (OSError, ValueError, TypeError, DownloadError):
            self.write_error = True
        self.refresh()

    def remove(self, keys):
        if self.closed:
            return
        try:
            self.values = self.store.update(remove=keys)
            self.error = self.write_error = False
            self.message = ''
        except (OSError, ValueError, TypeError, DownloadError):
            self.write_error = True
        self.refresh()

    def open(self):
        if self.closed or QApplication.activeModalWidget() is not None:
            return
        self.reload()
        if self.dialog is None:
            self.dialog = FavoritesDialog(self)
            self.dialog.finished.connect(self.finished)
        self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow()

    def finished(self, _result):
        self.dialog = None

    def refresh_art(self):
        if self.dialog and self.dialog.isVisible():
            self.dialog.table.viewport().update()

    def retranslate(self):
        self.message = ''
        self.refresh()
        if self.dialog:
            self.dialog.retranslate()

    def close(self):
        self.closed = True
        if self.dialog:
            self.dialog.close()
