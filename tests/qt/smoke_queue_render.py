"""Offline queue notification budgets and stable selected-job identities."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QTableView
    from quest_tool.messages import set_language, tr
    from quest_tool.qt_ui.download_browser import QueueModel
    from quest_tool.qt_ui.download_import import LocalModel
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.workers import TaskRunner

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    set_language('zh')
    def rows(count):
        return [dict(id=str(i), title='Song', source=str(i), state='downloading', progress=.1,
                     error='', installed='', target='isolated', kind='online') for i in range(count)]
    model = QueueModel()
    records = rows(90)
    model.replace(records); model.refresh_headers()
    table = QTableView(); table.setModel(model)
    table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    table.selectRow(7)
    notifications, headers, resets = [], [], []
    model.dataChanged.connect(lambda a, b, roles: notifications.append((a.row(), a.column(), b.row(), b.column(), roles)))
    model.headerDataChanged.connect(lambda *_args: headers.append(1))
    model.modelReset.connect(lambda: resets.append(1))
    for _ in range(500):
        assert model.replace(copy.deepcopy(records)) is False
        model.refresh_headers()
    assert not notifications and not headers
    records[7]['progress'] = .23
    assert model.replace(records)
    assert [entry[:4] for entry in notifications] == [(7, 2, 7, 2)]
    assert table.selectionModel().selectedRows()[0].data(Qt.ItemDataRole.UserRole) == '7'
    notifications.clear()
    records[7]['progress'] = .230001
    assert model.replace(records) and not notifications
    records[7]['detail'] = 'internal bookkeeping'
    assert model.replace(records) and not notifications and model.rows[7]['detail'] == 'internal bookkeeping'
    records[7]['title'] = 'Updated'
    model.replace(records)
    assert [entry[:4] for entry in notifications] == [(7, 0, 7, 0)]
    notifications.clear()
    records[7]['error'] = 'network'
    model.replace(records)
    assert len(notifications) == 2
    assert notifications[-1][:4] == (7, 0, 7, 3)
    assert notifications[-1][4] == [Qt.ItemDataRole.ToolTipRole]
    assert model.data(model.index(7, 3)) == tr('dl.error.network')
    # Appends and tail removals preserve selection; identity replacement clears it.
    records.extend(rows(92)[90:])
    model.replace(records)
    assert not resets and table.selectionModel().selectedRows()[0].data(Qt.ItemDataRole.UserRole) == '7'
    records = records[:88]
    model.replace(records)
    assert not resets and table.selectionModel().selectedRows()[0].data(Qt.ItemDataRole.UserRole) == '7'
    records[7], records[8] = records[8], records[7]
    model.replace(records)
    assert len(resets) == 1 and not table.selectionModel().selectedRows()
    notifications.clear(); headers.clear()
    set_language('en'); model.refresh_headers()
    assert len(headers) == len(notifications) == 1
    assert model.data(model.index(8, 3)) == tr('dl.error.network')
    model.refresh_headers()
    assert len(headers) == 1
    local = LocalModel()
    local.replace([dict(title='Local', author='', duration=0, folder='fixture')])
    local.refresh_headers()
    local_signals = []
    local.headerDataChanged.connect(lambda *_args: local_signals.append('header'))
    local.dataChanged.connect(lambda *_args: local_signals.append('data'))
    for _ in range(500): local.refresh_headers()
    assert not local_signals
    set_language('zh'); local.refresh_headers()
    assert local_signals == ['header', 'data'] and local.data(local.index(0, 2)) == tr('search.unknown')

    class Manager:
        def __init__(self, history):
            self.history = history
            self.tasks = TaskRunner()
            self.records = rows(2)
            self.stopped = False
            self.last_keep_zip = False
        def snapshot(self):
            return copy.deepcopy(self.records)
        def shutdown(self):
            self.stopped = True

    with tempfile.TemporaryDirectory(prefix='quest-queue-render-') as folder:
        manager = Manager(Path(folder)/'history.json')
        window = DownloadWindow()
        window.enable_downloads(folder, manager)
        queue = window.download_browser
        window.show()
        deadline = time.monotonic()+5
        while queue.importer.scanning and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.005)
        assert not queue.importer.scanning
        updates = []
        queue.model.dataChanged.connect(lambda *_args: updates.append('queue'))
        queue.model.headerDataChanged.connect(lambda *_args: updates.append('queue-header'))
        queue.importer.model.headerDataChanged.connect(lambda *_args: updates.append('local-header'))
        for _ in range(100): queue.poll()
        assert not updates
        # Action completion must still restore controls even when the rows do not change.
        queue.busy = True; queue.refresh()
        assert not window.browse.isEnabled()
        queue.actions.emit('queue_action', payload={'action': 'clear'})
        queue.poll()
        assert not queue.busy and window.browse.isEnabled()
        queue.table.selectRow(1)
        manager.records[1]['progress'] = .5
        queue.poll()
        assert updates == ['queue'] and queue.cancel.isEnabled()
        assert queue.table.selectionModel().selectedRows()[0].data(Qt.ItemDataRole.UserRole) == '1'
        # Completion handling stays live and happens once per completed job.
        refreshes = []
        original_refresh = queue.importer.refresh_local
        def refresh_local():
            refreshes.append(1)
            original_refresh()
        queue.importer.refresh_local = refresh_local
        manager.records[1].update(state='done', target=str(queue.target))
        queue.poll(); queue.poll()
        deadline = time.monotonic()+3
        while not refreshes and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.005)
        assert len(refreshes) == 1
        manager.records[0]['state'] = 'cancelled'
        queue.poll()
        assert not queue.timer.isActive()
        window.retranslate('en')
        assert queue.model.headerData(1, Qt.Orientation.Horizontal) == tr('dl.column.state')
        assert queue.importer.model.headerData(1, Qt.Orientation.Horizontal) == tr('qt.local.author')
        window.close(); app.processEvents(); queue.wait_closed()
        assert manager.stopped and queue.closed and not errors, errors
    table.close()
    assert 'miniaudio' not in sys.modules
    metrics = dict(repeated_identical=500, rows=90, data_changed=0, affected_cells=0,
                   identical_queue_polls=100, idle_notifications=0, changed_progress_cells=1)
    destination = os.environ.get('QUEST_QT_SHOTS')
    if destination:
        path = Path(destination); path.mkdir(parents=True, exist_ok=True)
        (path/'queue-render-metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    print('PASS identical snapshots/targeted progress/tooltips/stable IDs/translation/idle polls/action completion/one refresh/close')
    print(json.dumps(metrics))


def main():
    if '--child' in sys.argv:
        child()
        return
    for scale in ('1', '1.25', '1.5'):
        env = dict(os.environ, QT_SCALE_FACTOR=scale, PYTHONIOENCODING='utf-8')
        result = subprocess.run([sys.executable, '-B', __file__, '--child'], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        print(scale, result.stdout.decode('utf-8', errors='replace'), flush=True)
        assert result.returncode == 0


if __name__ == '__main__':
    main()
