"""Explicit, cancellable content identity checks, separate from fast listing."""
from pathlib import Path
import re
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton

from ..downloads import DownloadError, check_cancel
from ..messages import tr
from ..song_search import SearchSession
from .local_verification import inspect_install, ReadBudgetExceeded


class IdentityClient:
    def __init__(self, provider):self.provider = provider

    def search(self, request, _page, _order, cancel):
        if request['kind']=='quest':
            if self.provider is None or not hasattr(self.provider,'read_identities'):
                return {'items':[]}
            return self.provider.read_identities(request['device'],request['items'],cancel)
        root = Path(request['root']);budget = [256*1024*1024];items = []
        for name in request['folders'][:2000]:
            check_cancel(cancel)
            try:
                value = inspect_install(root/name,root,cancel,budget)
                if value.get('state')=='valid':items.append(dict(folder=name,state='verified',digest=value['digest']))
            except ReadBudgetExceeded:break
            except (DownloadError,OSError,ValueError,TypeError):check_cancel(cancel)
        return {'items':items}


class LibraryIdentity:
    def __init__(self, window, provider):
        self.window = window;self.closed = False;self.busy = False
        self.values = {'local':{},'quest':{}};self.epoch = 0;self.generation = None
        self.session = SearchSession(IdentityClient(provider));self.pending = [];self.kind = 'local'
        self.button = QPushButton();self.cancel_button = QPushButton();self.status = QLabel();self.status.setWordWrap(True)
        for widget in (self.button,self.cancel_button,self.status):widget.setParent(window.retired_controls)
        self.button.clicked.connect(self.start);self.cancel_button.clicked.connect(self.cancel)
        self.timer = QTimer(window);self.timer.setInterval(100);self.timer.timeout.connect(self.poll)
        window.model.modelReset.connect(self.reset);window.quest_model.modelReset.connect(self.reset)
        self.render()

    def reset(self):
        self.cancel();self.values = {'local':{},'quest':{}};self.epoch += 1;self.changed()

    def cancel(self):
        self.session.cancel();self.generation = None;self.busy = False;self.timer.stop();self.changed()

    def changed(self):
        self.render()
        if not self.closed:
            self.window.update_comparison();self.window.reports.changed()

    def context(self):return (str(self.window.target),self.window.quest_identity,self.epoch)

    def start(self):
        w = self.window
        if self.busy or not w.local_ready or not w.quest_ready or w.device_browser.busy or w.device_browser.connection_open:return
        resume = any(self.values.values()) and not self.complete()
        self.epoch += 1
        self.bound = self.context();self.device = dict(w.device_browser.selected) if w.device_browser.selected else None
        if not resume:self.values = {'local':{},'quest':{}}
        self.pending = [dict(row) for row in w.quest_model.rows if row['folder'] not in self.values['quest'] and re.fullmatch('[0-9a-f]{64}',row.get('info_hash','')) and not row['error']]
        self.remote_bytes = 0
        self.requested = {row['folder'] for row in w.model.rows if not row['error'] and row['folder'] not in self.values['local']}
        self.started = time.monotonic();self.busy = True;self.kind = 'local'
        self.generation = self.session.request({'kind':'local','root':str(w.target),'folders':sorted(self.requested)},0,'')
        self.timer.start();self.changed()

    def poll(self):
        if self.closed:return
        if self.busy and (self.context()!=self.bound or not self.window.quest_ready or not self.window.local_ready):self.cancel();return
        for message in self.session.tasks.drain():
            data = message.payload
            if self.generation is None or not isinstance(data,dict) or data.get('generation')!=self.generation:continue
            if message.kind not in ('search','search_error'):continue
            if message.kind=='search':
                result = data.get('result')
                if (not isinstance(result,dict) or not isinstance(result.get('items'),list)
                        or any(not isinstance(item,dict) or not isinstance(item.get('folder'),str) for item in result['items'])):
                    self.cancel();return
                if self.kind=='quest' and result.get('identity')!=self.window.quest_identity:
                    self.cancel();return
                count = result.get('bytes',0) if self.kind=='quest' else 0
                if type(count) is not int or not 0<=count<=64*1024*1024:self.cancel();return
                self.remote_bytes += count
                entries = result.get('items',[])
                names = [item.get('folder') for item in entries]
                if len(set(names))!=len(names) or not set(names).issubset(self.requested):self.cancel();return
                allowed = self.requested
                for item in result.get('items',[]):
                    if item.get('folder') in allowed and item.get('state')=='verified' and isinstance(item.get('digest'),str) and re.fullmatch('[0-9a-f]{40}',item['digest']):
                        self.values[self.kind][item['folder']] = item['digest']
            elif self.kind=='quest':self.pending.clear()
            if self.pending and self.device is not None and time.monotonic()-self.started<180 and self.remote_bytes<=192*1024*1024:
                items,self.pending = self.pending[:4],self.pending[4:];self.kind = 'quest'
                self.requested = {item['folder'] for item in items}
                self.generation = self.session.request({'kind':'quest','device':self.device,'items':items},0,'')
            else:self.generation = None;self.busy = False;self.timer.stop()
            self.window.update_comparison();self.window.reports.changed();self.render()

    def complete(self):
        w = self.window
        return (not self.busy and w.local_ready and w.quest_ready and not w.quest_incomplete
                and set(self.values['local'])=={row['folder'] for row in w.model.rows} and set(self.values['quest'])=={row['folder'] for row in w.quest_model.rows})

    def sets(self):
        local,quest = self.values['local'],self.values['quest']
        ld,qd = set(local.values()),set(quest.values())
        lm = {name for name,digest in local.items() if digest in qd}
        qm = {name for name,digest in quest.items() if digest in ld}
        return lm,qm,(set(local)-lm if self.complete() else set()),(set(quest)-qm if self.complete() else set())

    def render(self):
        self.button.setText(tr('ui.library.check_continue' if not self.busy and any(self.values.values()) and not self.complete() else 'ui.library.check_missing'))
        w = self.window
        available = not self.closed and not self.busy and w.local_ready and w.quest_ready and not w.device_browser.busy and not w.device_browser.connection_open
        self.button.setEnabled(available);self.button.setToolTip('' if available else tr('ux.read_first'))
        self.cancel_button.setText(tr('ux.cancel_verify'));self.cancel_button.setEnabled(self.busy)
        self.button.setVisible(not self.busy);self.cancel_button.setVisible(self.busy)
        self.status.setText(tr('ux.verify_progress' if self.busy else 'ux.verify_status',count=sum(map(len,self.values.values()))))
        if self.busy:self.button.setToolTip(self.status.text())
        self.button.setAccessibleDescription(self.status.text())
        if hasattr(w,'detect_missing'):
            caption=tr('ux.cancel_verify' if self.busy else 'ui.library.detect_missing')
            compact=w.missing_card.width()<350
            from .icons import line_icon
            from ..theme import get_palette
            w.detect_missing.setIcon(line_icon('stop' if self.busy else 'search',get_palette(w.mode.currentData()).TEXT_PRIMARY))
            w.detect_missing.setText('' if compact else caption);w.detect_missing.setAccessibleName(caption)
            w.detect_missing.setFixedWidth(36 if compact else w.detect_missing.sizeHint().width())
            w.detect_missing.setEnabled(self.busy or available);w.detect_missing.setToolTip(caption+' · '+(self.status.text() if self.busy or available else tr('ux.read_first')))

    def close(self):
        self.closed = True;self.cancel();self.session.close()

    def wait_closed(self):
        for thread in tuple(self.session.tasks._threads):thread.join()
