"""Coalesced, cancellable local-history verification; UI consumes plain results."""
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, Qt, QTimer

from ..messages import tr
from ..song_search import SearchSession
from .local_inventory import InventoryClient
from .song_status import path_key


class VerificationBrowser(QObject):
    def __init__(self, queue, session=None):
        super().__init__(queue)
        self.queue = queue
        self.session = session if session is not None else SearchSession(InventoryClient())
        self.closed = self.busy = False
        self.signature = None
        self.records = {}
        self.checked_at = ''
        self.generation = None
        self.rows = []
        self.inventory = {}
        self.force_next = False
        self.cancelled = False
        self.failed = False
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.poll)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)
        self.debounce.timeout.connect(self.start)
        menu = queue.window.options.menu()
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        self.action = menu.addAction('')
        self.action.triggered.connect(lambda _checked=False:self.request(force=True))
        self.cancel_action = menu.addAction('')
        self.cancel_action.triggered.connect(self.cancel)
        self.summary = menu.addAction('')
        self.summary.setEnabled(False)
        queue.window.scan_status.installEventFilter(self)
        self.retranslate()

    def eventFilter(self, watched, event):
        if not self.closed and watched is self.queue.window.scan_status and event.type() == QEvent.Type.Resize:
            self.render_notice()
        return False

    def render_notice(self):
        if self.closed:return
        key = ('qt.inventory.badge_busy' if self.busy else 'qt.inventory.badge_cancelled' if self.cancelled else
               'qt.inventory.badge_failed' if self.failed else 'qt.inventory.badge_partial' if self.inventory and not self.inventory.get('complete') else
               'qt.inventory.badge_done' if self.inventory else 'qt.inventory.badge_unknown')
        text = tr(key, count=self.inventory.get('valid', 0), time=self.checked_at[11:16] or '—')
        label = self.queue.window.scan_status
        visible = label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(0,label.contentsRect().width()))
        if label.text() != visible:label.setText(visible)
        label.setToolTip(self.action.toolTip())
        label.setAccessibleName(text+'\n'+self.action.toolTip())
        label.show()

    def observe(self):
        if self.closed:return
        target = str(self.queue.target)
        rows = [{key: row.get(key, '') for key in ('id', 'installed', 'digest', 'source', 'kind')}
                for row in self.queue.model.rows if row.get('state') in ('done', 'exists')
                and path_key(row.get('target', '')) == path_key(target)]
        signature = (target, tuple(tuple(sorted(row.items())) for row in rows))
        if signature != self.signature:
            self.signature, self.rows = signature, rows
            self.request()
        self.retranslate()

    def request(self, force=False):
        if self.closed:return
        self.session.cancel()
        self.generation = None
        self.records = {}
        self.inventory = {}
        self.cancelled = False
        self.failed = False
        self.force_next = self.force_next or force
        self.checked_at = ''
        self.busy = True
        self.timer.stop()
        self.debounce.start()
        self.retranslate()
        self.queue.window.selection_browser.refresh()

    def start(self):
        if self.closed:return
        self.generation = self.session.request({'target': str(self.queue.target), 'rows': self.rows, 'force': self.force_next}, 0, '')
        self.force_next = False
        self.timer.start()

    def poll(self):
        if self.closed:return
        for message in self.session.tasks.drain():
            data = message.payload
            if not isinstance(data, dict) or data.get('generation') != self.generation or self.generation is None:continue
            if message.kind not in ('search', 'search_error'):continue
            result = data.get('result', {})
            self.records = (result.get('records', {}) if message.kind == 'search'
                            and result.get('target') == str(self.queue.target) else
                            {row['id']: {'state': 'unknown', 'reason': 'read'} for row in self.rows})
            self.checked_at = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')
            self.inventory = result.get('inventory', {}) if message.kind == 'search' and result.get('target') == str(self.queue.target) else {}
            self.failed = message.kind == 'search_error' or result.get('target') != str(self.queue.target) or bool(self.inventory.get('error'))
            self.busy = False
            self.generation = None
            self.timer.stop()
            self.retranslate()
            self.queue.window.selection_browser.refresh()

    def retranslate(self):
        self.action.setText(tr('qt.verify.busy' if self.busy else 'qt.verify.action'))
        self.action.setEnabled(not self.closed and not self.busy)
        self.action.setToolTip(tr('qt.verify.hint', time=self.checked_at or tr('search.unknown')))
        self.cancel_action.setText(tr('qt.inventory.cancel'))
        self.cancel_action.setEnabled(not self.closed and self.busy)
        key = ('qt.inventory.loading' if self.busy else 'qt.inventory.cancelled' if self.cancelled else 'qt.inventory.failed' if self.failed else
               'qt.inventory.summary' if self.inventory else 'qt.inventory.unknown')
        self.summary.setText(tr(key, checked=self.inventory.get('checked',0), valid=self.inventory.get('valid',0),
                                state=tr('qt.inventory.complete' if self.inventory.get('complete') else 'qt.inventory.partial')))
        hint = self.action.toolTip()+'\n'+self.summary.text()+'\n'+tr('qt.inventory.scope',path=str(self.queue.target))
        hint += '\n'+tr('qt.inventory.continuation')
        if self.inventory.get('passes', 1) > 1:
            detail = tr('qt.inventory.passes', count=self.inventory['passes'], maximum=self.inventory['max_passes'])
            self.summary.setText(self.summary.text()+' · '+detail)
            hint += '\n'+detail
        self.action.setToolTip(hint)
        self.summary.setToolTip(hint)
        self.render_notice()

    def cancel(self):
        if self.closed or not self.busy:return
        self.session.cancel()
        self.debounce.stop();self.timer.stop()
        self.generation = None
        self.records = {};self.inventory = {};self.checked_at = ''
        self.busy = False;self.cancelled = True
        self.failed = False
        self.retranslate()
        self.queue.window.selection_browser.refresh()

    def close(self):
        if self.closed:return
        self.closed = True
        self.debounce.stop();self.timer.stop()
        self.action.setEnabled(False)
        self.cancel_action.setEnabled(False)
        self.session.close()

    def wait_closed(self):
        for thread in tuple(self.session.tasks._threads):thread.join()
