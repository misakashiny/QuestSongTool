"""Focus-scoped library commands; mutations use a separate confirmed worker."""
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, QModelIndex, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMenu

from ..messages import tr


class LibraryActions(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.menu = None
        self.last_view = window.table
        self.clipboard_write = lambda text: QApplication.clipboard().setText(text)
        self.open_directory = lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.views = ((window.table, 'local'), (window.grid, 'local'),
                      (window.quest_table, 'quest'), (window.missing_table, 'missing'))
        for view, key in self.views:
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda point, v=view, k=key: self.show_menu(v, k, point))
        for sequence, callback in (('Ctrl+D', self.toggle_details),):
            shortcut = QShortcut(QKeySequence(sequence), window)
            shortcut.activated.connect(callback);window.shortcuts.append(shortcut)
        QApplication.instance().installEventFilter(self)

    def active(self):
        return not self.window.closed and QApplication.activeWindow() is self.window and QApplication.activeModalWidget() is None

    def focused_view(self):
        focus = QApplication.focusWidget()
        return next(((view, key) for view, key in self.views if focus is view or view.isAncestorOf(focus)), None)

    def eventFilter(self, watched, event):
        if event.type() not in (QEvent.Type.FocusIn, QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):return False
        if not self.active():return False
        if event.type() == QEvent.Type.FocusIn:
            pair = self.focused_view()
            if pair:self.last_view = pair[0]
        if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):return False
        pair = self.focused_view()
        modifiers = event.modifiers();key = event.key()
        command = None
        if pair:
            if (modifiers == Qt.KeyboardModifier.NoModifier and key == Qt.Key.Key_Menu) or (modifiers == Qt.KeyboardModifier.ShiftModifier and key == Qt.Key.Key_F10):
                command = 'menu'
            elif modifiers == Qt.KeyboardModifier.ControlModifier:
                command = {Qt.Key.Key_A:'visible', Qt.Key.Key_C:'copy'}.get(key)
            elif modifiers == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) and key == Qt.Key.Key_A:
                command = 'all'
            elif modifiers == Qt.KeyboardModifier.NoModifier and key == Qt.Key.Key_Escape:command = 'clear'
            elif modifiers == Qt.KeyboardModifier.NoModifier and key == Qt.Key.Key_Delete:command = 'delete'
        elif QApplication.focusWidget() is self.window.search and modifiers == Qt.KeyboardModifier.NoModifier and key == Qt.Key.Key_Escape:
            command = 'search'
        if command is None:return False
        event.accept()
        if event.type() == QEvent.Type.ShortcutOverride:return True
        if event.isAutoRepeat():return True
        if command == 'search':self.window.search.clear()
        elif command == 'menu':
            view, key = pair
            index = view.currentIndex()
            if index.isValid():
                view.scrollTo(index)
                self.show_menu(view,key,view.visualRect(index).center())
        elif command == 'copy':self.copy_selection(*pair)
        elif command == 'delete':self.window.deletion.remove(*pair)
        else:self.checks(pair[0], command)
        return True

    def ready(self, key):
        return self.window.local_ready if key == 'local' else self.window.quest_ready if key == 'quest' else self.window.local_ready and self.window.quest_ready

    def token(self, key, row):
        origin = row.get('origin') if key == 'missing' else key
        if not self.ready(key) or origin not in ('local','quest'):return None
        source = str(self.window.target) if origin == 'local' else self.window.quest_identity
        return (key, origin, source, row['folder'])

    def resolve(self, token):
        if token is None or self.window.closed:return None
        key, origin, source, folder = token
        proxy = {'local':self.window.proxy, 'quest':self.window.quest_proxy, 'missing':self.window.missing_proxy}[key]
        for index in range(proxy.rowCount()):
            row = proxy.index(index,0).data(Qt.ItemDataRole.UserRole)
            if row['folder'] == folder and self.token(key,row) == token:return row
        return None

    def local_path(self, token):
        if self.resolve(token) is None or token[1] != 'local':return None
        try:
            root = Path(token[2]);folder = token[3]
            if not root.is_absolute() or str(root).startswith(('\\\\','//')) or root == Path(root.anchor):return None
            if not folder or folder in ('.','..') or any(c in folder for c in '/\\:'):return None
            path = root/folder
            if path.is_symlink() or path.is_junction():return None
            resolved = path.resolve(strict=True)
            if not resolved.is_dir() or resolved == root or not resolved.is_relative_to(root):return None
            return resolved
        except (OSError, ValueError, RuntimeError):return None

    def copy_token(self, token, field):
        row = self.resolve(token)
        if row is not None:self.clipboard_write(row[field])

    def open_token(self, token):
        path = self.local_path(token)
        if path is not None and not self.open_directory(path):self.window.statusBar().showMessage(tr('qt.actions.open_failed'))

    def copy_selection(self, view, key):
        if not self.ready(key):return
        model = view.model().sourceModel()
        names = [row['folder'] for row in model.rows if row['folder'] in model.checked]
        if not names:
            names = [index.data(Qt.ItemDataRole.UserRole)['folder'] for index in view.selectionModel().selectedRows(0)]
        if names:self.clipboard_write('\n'.join(names))

    def checks(self, view, mode):
        key = next(key for item,key in self.views if item is view)
        if not self.ready(key):return
        proxy = view.model();model = proxy.sourceModel()
        if mode == 'clear':
            model.checked.clear();view.selectionModel().clear();view.setCurrentIndex(QModelIndex())
            if self.window.details.active_view == key:self.window.details.clear()
        else:
            for row in range(proxy.rowCount()):
                index = proxy.index(row,0)
                if mode == 'all' or view.visualRect(index).intersects(view.viewport().rect()):model.checked.add(index.data(Qt.ItemDataRole.UserRole)['folder'])
        if model.rows:model.dataChanged.emit(model.index(0,0),model.index(len(model.rows)-1,0),[Qt.ItemDataRole.CheckStateRole])
        self.window.render_status()

    def direction(self, key):
        if self.active() and self.window.local_ready and self.window.quest_ready:
            self.window.missing_side.setCurrentIndex(self.window.missing_side.findData(key))

    def toggle_details(self):
        if not self.active():return
        page = self.window.details.page
        if page.isVisible():
            self.detail_sizes = self.window.library_split.sizes()
            page.hide()
            view = self.last_view
            if not view.isVisible():view = self.window.grid if self.window.grid.isVisible() else self.window.table
            view.setFocus()
        else:
            page.show()
            if hasattr(self,'detail_sizes'):self.window.library_split.setSizes(self.detail_sizes)

    def show_menu(self, view, key, point):
        if not self.active():return
        index = view.indexAt(point)
        if not index.isValid():return
        view.setCurrentIndex(index);view.setFocus()
        self.window.details.select(key,index)
        token = self.token(key,index.data(Qt.ItemDataRole.UserRole))
        if token is None:return
        if self.menu:self.menu.close()
        menu = QMenu(view);menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.addAction(tr('songlist.ctx.copy_folder'),lambda:self.copy_token(token,'folder'))
        menu.addAction(tr('songlist.ctx.copy_title'),lambda:self.copy_token(token,'title'))
        action = menu.addAction(tr('songlist.ctx.open_explorer'),lambda:self.open_token(token))
        action.setEnabled(self.local_path(token) is not None)
        menu.addSeparator()
        action = menu.addAction(tr('songs.delete.action'),lambda:self.window.deletion.remove(view,key,token))
        action.setShortcut(QKeySequence('Del'))
        action.setEnabled(self.window.deletion.available() and (token[1] == 'local' or bool(
            self.window.device_browser.selected and self.window.deletion.session.client.provider is not None
            and self.window.device_browser.selected['identity'] == token[2] and self.resolve(token).get('info_hash'))))
        menu.addSeparator()
        for mode in ('visible','all','clear'):
            menu.addAction(tr('qt.actions.'+mode),lambda m=mode:self.menu_checks(token,view,m))
        menu.aboutToHide.connect(lambda:self.menu_hidden(menu))
        self.menu = menu;menu.popup(view.viewport().mapToGlobal(point))

    def menu_hidden(self, menu):
        if self.menu is menu:self.menu = None

    def menu_checks(self, token, view, mode):
        if self.resolve(token) is not None:self.checks(view,mode)

    def close(self):
        QApplication.instance().removeEventFilter(self)
        if self.menu:self.menu.close()
