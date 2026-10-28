"""Confirmed removal: Windows recycle bin or a recoverable Quest quarantine."""
import hashlib
import json
from pathlib import Path
import shlex
import time
import uuid

from PySide6.QtCore import QFile, QObject, Qt, QTimer
from PySide6.QtWidgets import QMessageBox
from .surfaces import action_icons

from ..downloads import DownloadError, check_cancel
from ..messages import tr
from ..song_search import SearchSession
from .adb_assets import _binding
from .device_protocol import descriptor
from .operation_records import save_operation
from .transfer_queue import folder_name


def confirm_removal(parent, title, text, details=''):
    box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setButtonText(QMessageBox.StandardButton.Yes, tr('songs.delete.confirm'))
    box.setButtonText(QMessageBox.StandardButton.Cancel, tr('settings.cancel'))
    box.button(QMessageBox.StandardButton.Yes).setProperty('role','danger')
    action_icons((box.button(QMessageBox.StandardButton.Yes),'trash'),(box.button(QMessageBox.StandardButton.Cancel),'close'))
    box.setDefaultButton(QMessageBox.StandardButton.Yes)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    if details:box.setDetailedText(details)
    return box.exec() == QMessageBox.StandardButton.Yes


def local_stamp(root, name):
    root = Path(root)
    name = folder_name(name)
    if (not root.is_absolute() or root == Path(root.anchor) or str(root).startswith(('\\\\', '//'))
            or any(p.is_symlink() or p.is_junction() for p in (root, *root.parents))):
        raise DownloadError('delete_changed')
    path = root/name
    if path.is_symlink() or path.is_junction() or path.resolve(strict=True).parent != root.resolve() or not path.is_dir():
        raise DownloadError('delete_changed')
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_mtime_ns)


class SongDeleteClient:
    def __init__(self, sandbox, provider=None, trash=None):
        self.sandbox = Path(sandbox)
        self.provider = provider
        self.trash = trash or QFile.moveToTrash

    def quest_remove(self, request, item, cancel):
        provider = self.provider
        if provider is None:raise DownloadError('device_rejected')
        selected = descriptor(request['device'])
        serial, hardware = json.loads(selected['device_id'])
        deadline = time.monotonic()+60
        with provider._operation(cancel):
            bridge, root = _binding(provider, selected, serial, hardware, cancel, deadline)
            contents = list(provider._contents(bridge, root, [item['folder']], cancel, deadline))
            if (len(contents) != 1 or contents[0][0] != item['folder'] or not item.get('info_hash')
                    or hashlib.sha256(contents[0][1]).hexdigest() != item['info_hash']):
                raise DownloadError('delete_changed')
            name = folder_name(item['folder'])
            transaction = uuid.uuid4().hex
            stage = root+'/.quest-deleted-'+transaction
            source, target = root+'/'+name, stage+'/'+name
            qroot, qstage, qsource, qtarget = map(shlex.quote, (root, stage, source, target))
            help_text = provider._run(bridge, ['shell', 'mv --help'], cancel, deadline)
            if '-n' not in help_text or '-T' not in help_text:raise DownloadError('device_command')
            journal = self.sandbox/'deleted-songs'
            record = dict(device=selected, source=source, quarantine=target, state='intent')
            save_operation(journal,transaction,record)
            # No recursive delete. A fresh, private directory prevents overwrite.
            command = (f'[ ! -L {qroot} ] && [ "$(readlink -f {qroot})" = {qroot} ] && '
                       f'[ -d {qsource} ] && [ ! -L {qsource} ] && [ "$(readlink -f {qsource})" = {qsource} ] && '
                       f'mkdir {qstage} && mv -nT {qsource} {qtarget} && '
                       f'[ ! -e {qsource} ] && [ -d {qtarget} ] && printf QSC_REMOVED')
            reply = provider._run(bridge, ['shell', command], cancel, deadline)
            if reply != 'QSC_REMOVED':raise DownloadError('device_command')
            record['state'] = 'quarantined'
            save_operation(journal,transaction,record)
            _binding(provider, selected, serial, hardware, cancel, deadline)
            return target

    def search(self, request, _page, _order, cancel):
        origin = request['origin'];items = request['items']
        if origin not in ('local', 'quest') or not 0 < len(items) <= 100000:
            raise DownloadError('delete_changed')
        done, failed = [], []
        for item in items:
            check_cancel(cancel)
            try:
                folder_name(item['folder'])
                if origin == 'local':
                    if local_stamp(request['root'], item['folder']) != tuple(item['stamp']):
                        raise DownloadError('delete_changed')
                    result = self.trash(str(Path(request['root'])/item['folder']))
                    ok, location = result if isinstance(result, tuple) else (result, '')
                    if not ok:raise DownloadError('delete_failed')
                else:location = self.quest_remove(request, item, cancel)
                done.append(dict(folder=item['folder'], location=location))
            except Exception as exc:
                failed.append(dict(folder=item['folder'], error=getattr(exc, 'code', 'delete_failed')))
        return dict(origin=origin, done=done, failed=failed)


class SongDeletion(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        session = window.device_browser.session
        provider = session.client.provider if session else None
        self.session = SearchSession(SongDeleteClient(window.sandbox, provider))
        self.generation = None;self.closed = False;self.confirming = False;self.request = None
        self.timer = QTimer(self);self.timer.setInterval(75);self.timer.timeout.connect(self.poll)

    def available(self):
        w = self.window
        playlists = getattr(w,'playlists',None)
        return not (self.closed or self.confirming or self.generation is not None or w.closed or w.scanning
                    or w.device_browser.busy or w.device_browser.connection_open
                    or (playlists is not None and playlists.device_generation is not None)
                    or w.transfers.dialog is not None or any(job.tasks.busy() for job in w.transfers.jobs))

    def remove(self, view, key, context_token=None):
        if not self.available():return
        actions = self.window.actions;model = view.model().sourceModel()
        selected = [i.data(Qt.ItemDataRole.UserRole)
                    for i in view.selectionModel().selectedRows(0)]
        use_checks = bool(model.checked) and (context_token is None or context_token[3] in model.checked)
        rows = [row for row in model.rows if row['folder'] in model.checked] if use_checks else selected
        if context_token is not None and not use_checks:
            row = actions.resolve(context_token);rows = [row] if row else []
        tokens = [actions.token(key, row) for row in rows]
        if not tokens or any(token is None for token in tokens) or len({t[1] for t in tokens}) != 1:return
        origin = tokens[0][1]
        device = self.window.device_browser.selected
        if origin == 'quest' and (device is None or device['identity'] != tokens[0][2] or self.session.client.provider is None):return
        root = tokens[0][2] if origin == 'local' else device['root']
        items = []
        try:
            for token, row in zip(tokens, rows):
                item = dict(folder=folder_name(row['folder']), info_hash=row.get('info_hash', ''))
                if origin == 'local':item['stamp'] = local_stamp(root, row['folder'])
                elif not item['info_hash']:raise DownloadError('delete_changed')
                items.append(item)
        except (DownloadError, OSError, ValueError):
            QMessageBox.warning(self.window, tr('songs.delete.title'), tr('dl.error.delete_changed'));return
        request = dict(origin=origin, root=root, items=items)
        if origin == 'quest':request['device'] = dict(self.window.device_browser.selected)
        self.confirming = True
        try:
            accepted = confirm_removal(self.window, tr('songs.delete.title'),
                tr('songs.delete.'+origin, count=len(items), path=root), '\n'.join(row['folder'] for row in rows))
        finally:self.confirming = False
        if not accepted or not self.available() or any(actions.resolve(t) is None for t in tokens):return
        if origin == 'quest' and self.window.device_browser.selected != request['device']:return
        self.window.preview.stop()
        self.request = request;self.generation = self.session.request(request, 0, '')
        self.timer.start();self.window.statusBar().showMessage(tr('songs.delete.working'))

    def poll(self):
        for event in self.session.tasks.drain():
            data = event.payload
            if not isinstance(data, dict) or data.get('generation') != self.generation:continue
            if event.kind not in ('search', 'search_error'):continue
            self.generation = None;self.timer.stop()
            result = data.get('result', dict(done=[], failed=self.request['items']))
            w = self.window
            if self.request['origin'] == 'local' and str(w.target) == self.request['root']:
                w.refresh()
                for queue in w.download_queues:
                    if not queue.closed and str(queue.target) == self.request['root'] and queue.verification:
                        queue.verification.request(force=True)
            elif self.request['origin'] == 'quest':
                w.clear_quest_snapshot()
                if w.device_browser.selected == self.request.get('device'):w.device_browser.read_selected()
            text = tr('songs.delete.result', done=len(result['done']), failed=len(result['failed']))
            if result['failed']:QMessageBox.warning(w, tr('songs.delete.title'), text)
            else:w.statusBar().showMessage(text, 15000)

    def close(self):
        self.closed = True;self.timer.stop();self.session.close()

    def wait_closed(self):
        for thread in tuple(self.session.tasks._threads):thread.join()
