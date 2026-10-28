"""Snapshot comparison export. Workers never access widgets or live models."""
from dataclasses import dataclass
import logging
import math
import os
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QLabel, QMenu, QPushButton

from ..messages import catalog, tr
from ..reporting import render_report
from ..workers import TaskRunner, Token

logger = logging.getLogger('quest_tool.qt_preview')


@dataclass(frozen=True)
class ReportSnapshot:
    local: tuple
    quest: tuple
    ratings: tuple
    labels: tuple
    local_root: str
    quest_root: str | None
    generated_at: str


def freeze_rows(rows):
    fields = ('folder','title','author','error','duration','bpm','difficulties','verified_digest')
    return tuple(tuple((key,tuple(row[key]) if key=='difficulties' else row[key])
        for key in fields if key in row) for row in rows)


def report_rows(snapshot):
    labels = dict(snapshot.labels)
    local = {dict(row)['folder']:dict(row) for row in snapshot.local}
    quest = {dict(row)['folder']:dict(row) for row in snapshot.quest}
    ratings = dict(snapshot.ratings)
    unknown = labels['search.unknown']
    rows = []
    if all(item.get('verified_digest') for item in (*local.values(),*quest.values())):
        return verified_report_rows(snapshot,local,quest)
    for name in sorted(local.keys() | quest.keys(),key=lambda value:(value.lower(),value)):
        origin = 'both' if name in local and name in quest else 'local' if name in local else 'quest'
        item = local[name] if name in local else quest[name]
        bpm = item.get('bpm');duration = item['duration'];diffs = item.get('difficulties',())
        bpm_text = (str(int(bpm)) if float(bpm).is_integer() else f'{bpm:g}') if type(bpm) in (int,float) and math.isfinite(bpm) and bpm>0 else unknown
        seconds = int(round(duration)) if math.isfinite(duration) and duration>0 else 0
        duration_text = f'{seconds//60}:{seconds%60:02d}' if math.isfinite(duration) and duration>0 else unknown
        rating = (str(ratings[name]) if ratings.get(name) else '') if name in local else unknown
        rows.append((labels['report.state.'+origin],name,item['title'] or name,
            item['author'] or unknown,bpm_text,duration_text,'/'.join(diffs) if diffs else unknown,rating))
    return tuple(rows)


def verified_report_rows(snapshot, local, quest):
    # Preserve every folder while comparing verified content, never directory names.
    from dataclasses import replace
    groups = {}
    for origin,items in (('local',local),('quest',quest)):
        for name,item in items.items():groups.setdefault(item['verified_digest'],{'local':[], 'quest':[]})[origin].append((name,item))
    result = []
    for digest,sides in sorted(groups.items()):
        names = ' / '.join(dict.fromkeys(name for origin in ('local','quest') for name,_item in sides[origin]))
        original = (sides['local'] or sides['quest'])[0][1]
        row = {key:value for key,value in original.items() if key!='verified_digest'};row['folder']=names
        rating = dict(snapshot.ratings).get(sides['local'][0][0],0) if sides['local'] else 0
        fragment = replace(snapshot,local=freeze_rows([row]) if sides['local'] else (),quest=freeze_rows([row]) if sides['quest'] else (),ratings=((names,rating),))
        result.extend(report_rows(fragment))
    return tuple(result)


def output_path(value, fmt, roots):
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != '.'+fmt or not path.parent.is_dir():
        raise ValueError('report_destination')
    if any(p.is_symlink() or p.is_junction() for p in (path,*path.parents)) or path.is_dir():
        raise ValueError('report_destination')
    resolved = path.resolve()
    if any(resolved.is_relative_to(Path(root).resolve()) for root in roots):
        raise ValueError('report_source_destination')
    return resolved


def write_report(snapshot, fmt, destination, protected, token):
    path = output_path(destination,fmt,protected)
    if not token.alive():raise InterruptedError('report_cancelled')
    rows = report_rows(snapshot)
    text = render_report(rows,fmt,labels=dict(snapshot.labels),generated_at=snapshot.generated_at,
                         local_root=snapshot.local_root,quest_root=snapshot.quest_root)
    if not token.alive():raise InterruptedError('report_cancelled')
    parent = path.parent.stat()
    fd,name = tempfile.mkstemp(prefix='.quest-report-',suffix='.tmp',dir=path.parent)
    temporary = Path(name);identity = os.fstat(fd)
    try:
        with os.fdopen(fd,'w',encoding='utf-8-sig' if fmt=='csv' else 'utf-8',newline='') as stream:
            stream.write(text);stream.flush();os.fsync(stream.fileno())
        if not token.alive():raise InterruptedError('report_cancelled')
        output_path(destination,fmt,protected)
        fresh = path.parent.stat()
        if (parent.st_dev,parent.st_ino)!=(fresh.st_dev,fresh.st_ino):raise ValueError('report_destination_changed')
        os.replace(temporary,path)
        return len(rows)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            fresh = temporary.stat();directory = temporary.parent.stat()
            if ((fresh.st_dev,fresh.st_ino)==(identity.st_dev,identity.st_ino)
                    and (directory.st_dev,directory.st_ino)==(parent.st_dev,parent.st_ino)):
                temporary.unlink()


class LibraryReport(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window;self.tasks = TaskRunner(max_workers=1)
        self.closed = False;self.busy = False;self.epoch = 0;self.sequence = 0
        self.token = None;self.job_binding = None;self.state = 'need';self.detail = ''
        self.button = QPushButton();self.menu = QMenu(self.button);self.button.setMenu(self.menu)
        self.actions = {}
        for fmt in ('csv','md'):
            action = QAction(self.menu);self.menu.addAction(action)
            action.triggered.connect(lambda _checked=False,fmt=fmt:self.open(fmt));self.actions[fmt] = action
        self.status = QLabel();self.status.setTextFormat(Qt.TextFormat.PlainText);self.status.setMaximumWidth(240)
        window.statusBar().addPermanentWidget(self.status)
        window.model.modelReset.connect(self.changed);window.quest_model.modelReset.connect(self.changed)
        self.timer = QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.poll);self.timer.start()
        self.retranslate()

    def binding(self):
        w = self.window
        if hasattr(w,'identity_checks') and not w.identity_checks.complete():return None
        if (self.closed or w.closed or not w.local_ready or not w.quest_ready or w.quest_incomplete or w.scanning
                or not w.ratings.ready or w.ratings.root!=str(w.target) or w.target is None):return None
        return (self.epoch,str(w.target),w.quest_identity,w.ratings.sequence)

    def changed(self):
        self.epoch += 1
        if self.token:self.token.cancel()
        if not self.busy:self.state = 'ready'
        self.detail = '';self.render()

    def snapshot(self):
        w = self.window
        selected = w.device_browser.selected
        root = selected['root'] if selected and selected['identity']==w.quest_identity else None
        labels = catalog(w.language.currentData())
        local = [dict(row,verified_digest=w.identity_checks.values['local'].get(row['folder'])) for row in w.model.rows]
        quest = [dict(row,verified_digest=w.identity_checks.values['quest'].get(row['folder'])) for row in w.quest_model.rows]
        return ReportSnapshot(freeze_rows(local),freeze_rows(quest),
            tuple(w.ratings.values.items()),tuple((key,value) for key,value in labels.items()
                if key.startswith('report.') or key=='search.unknown'),str(w.target),root,
            time.strftime('%Y-%m-%d %H:%M:%S'))

    def open(self, fmt):
        binding = self.binding()
        if binding is None or self.busy or self.tasks.busy() or fmt not in self.actions:return
        path,_filter = QFileDialog.getSaveFileName(self.window,tr('report.export_report_title'),
            'Quest-comparison.'+fmt,self.actions[fmt].text()+' (*.'+fmt+')')
        if not path:return
        if self.binding()!=binding:
            self.state = 'cancelled';self.render();return
        snapshot = self.snapshot()
        protected = (snapshot.local_root,str(self.window.sandbox))
        self.sequence += 1;sequence = self.sequence;self.job_binding = binding
        token = Token('qt-report-'+str(sequence));self.token = token
        tasks = self.tasks
        def worker(_token):
            try:
                count = write_report(snapshot,fmt,path,protected,token)
                payload = dict(sequence=sequence,state='saved',path=path,count=count)
            except InterruptedError:payload = dict(sequence=sequence,state='cancelled',path=path)
            except Exception as exc:
                logger.warning('Report export failed: %s',exc)
                payload = dict(sequence=sequence,state='failed',path=path,error=str(exc))
            tasks.emit('report',payload=payload)
        self.busy = True;self.state = 'writing';self.detail = path;self.render()
        self.tasks.submit(worker,name='qt-report')

    def poll(self):
        if self.closed:return
        if self.busy and self.binding()!=self.job_binding and self.token:self.token.cancel()
        for message in self.tasks.drain():
            data = message.payload
            if message.kind!='report' or data['sequence']!=self.sequence:continue
            self.busy = False
            same = self.binding()==self.job_binding
            self.state = data['state'] if same or data['state']!='saved' else 'snapshot_saved'
            error = data.get('error','')
            self.detail = (tr('qt.report.destination_error') if error.startswith('report_')
                else tr('qt.report.write_error')) if error else data['path']
        self.render()

    def render(self):
        if self.closed:return
        ready = self.binding() is not None
        self.button.setEnabled(ready and not self.busy)
        key = 'writing' if self.busy else 'need' if not ready else self.state if self.state in ('saved','failed','cancelled','snapshot_saved') else 'ready'
        text = tr('qt.report.'+key)
        if not ready and self.window.local_ready and self.window.quest_ready and not self.window.quest_incomplete and not self.window.identity_checks.complete():
            text = tr('ux.report_verify')
        self.status.setText(text);self.status.setToolTip(self.detail)
        self.button.setToolTip(tr('qt.report.hint')+'\n'+text+('\n'+self.detail if self.detail else ''))

    def retranslate(self):
        self.button.setText(tr('qt.report.export'))
        for fmt,action in self.actions.items():action.setText(tr('qt.report.'+fmt))
        self.render()

    def close(self):
        self.closed = True;self.timer.stop()
        if self.token:self.token.cancel()

    def wait_closed(self):
        for thread in tuple(self.tasks._threads):thread.join()
