"""Bounded local log viewer. It never uploads, clears or executes log content."""
import os
from pathlib import Path
import re
import sys

from PySide6.QtCore import QEvent, QObject, QTimer, qVersion
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from ..messages import tr
from ..workers import TaskRunner
from .surfaces import action_icons, heading


def redact(text):
    home=str(Path.home())
    for value in (home,home.replace('\\','/')):text=text.replace(value,'%USERPROFILE%')
    text=re.sub(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b','[IP]',text)
    return re.sub(r'(?i)((?:password|token|api[_-]?key|pairing[_-]?code)\s*[:=]\s*)[^\s,;]+',r'\1[hidden]',text)


def read_logs(root):
    if root is None:return []
    root=Path(root).absolute()
    if any(path.is_symlink() or path.is_junction() for path in (root,*root.parents)):raise ValueError('linked log directory')
    rows=[]
    for name in ('preview.log.2','preview.log.1','preview.log'):
        path=root/name
        if not path.exists():continue
        if path.is_symlink() or path.is_junction() or not path.is_file():raise ValueError('linked log')
        before=path.stat()
        with path.open('rb') as stream:
            opened=os.fstat(stream.fileno())
            if (before.st_dev,before.st_ino)!=(opened.st_dev,opened.st_ino):raise ValueError('changed log')
            stream.seek(max(0,opened.st_size-65536));data=stream.read(65536)
        rows.append(dict(name=name,text=redact(data.decode('utf-8','replace')),limited=opened.st_size>65536))
    return rows


class DiagnosticsDialog(QDialog):
    def __init__(self, root, metadata, parent):
        super().__init__(parent);self.root=root;self.metadata=metadata;self.closed=False;self.tasks=TaskRunner(max_workers=1)
        super().setWindowTitle(tr('qt.logs.title'));super().resize(900,640);super().setMinimumSize(560,420)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,20,20,20)
        top=QHBoxLayout();top.addWidget(heading('qt.logs.title'),1)
        self.refresh=QPushButton(tr('qt.logs.refresh'));self.copy=QPushButton(tr('qt.logs.copy'))
        top.addWidget(self.refresh);top.addWidget(self.copy);layout.addLayout(top)
        hint=QLabel(tr('qt.logs.hint'));hint.setWordWrap(True);layout.addWidget(hint)
        self.text=QPlainTextEdit();self.text.setReadOnly(True);self.text.setMaximumBlockCount(2500)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap);layout.addWidget(self.text,1)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        action_icons((self.refresh,'refresh'),(self.copy,'copy'))
        self.refresh.clicked.connect(self.reload);self.copy.clicked.connect(self.copy_text)
        self.timer=QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.poll);self.reload()

    def reload(self):
        if self.closed or self.tasks.busy():return
        self.refresh.setEnabled(False);self.status.setText(tr('qt.logs.loading'));self.timer.start()
        root=self.root;tasks=self.tasks
        def worker(token):
            try:result=dict(rows=read_logs(root),failed=False)
            except (OSError,ValueError):result=dict(rows=[],failed=True)
            if token.alive():tasks.emit('logs',payload=result)
        self.tasks.submit(worker,name='diagnostic-logs')

    def poll(self):
        if self.closed:return
        for event in self.tasks.drain():
            if event.kind!='logs':continue
            self.timer.stop();self.refresh.setEnabled(True);data=event.payload
            parts=[redact(self.metadata)]
            for row in data['rows']:parts.extend(['\n--- '+row['name']+' ---\n',row['text']])
            self.text.setPlainText('\n'.join(parts))
            self.status.setText(tr('qt.logs.failed' if data['failed'] else 'qt.logs.limited' if any(row['limited'] for row in data['rows']) else 'qt.logs.ready' if data['rows'] else 'qt.logs.empty'))

    def copy_text(self):
        QApplication.clipboard().setText(self.text.toPlainText());self.status.setText(tr('qt.logs.copied'))

    def closeEvent(self, event):
        self.closed=True;self.tasks.cancel_all();self.timer.stop();super().closeEvent(event)

    def reject(self):self.close()

    def wait_closed(self):
        for thread in tuple(self.tasks._threads):thread.join()


class Diagnostics(QObject):
    def __init__(self, window):
        super().__init__(window);self.window=window;self.dialogs=[]
        self.button=QPushButton(tr('qt.logs.open'),window.header);self.button.setObjectName('diagnosticLogs');self.button.hide()
        action_icons((self.button,'list'));self.button.clicked.connect(self.show)
        window.header.installEventFilter(self);self.retranslate()

    def eventFilter(self, watched, event):
        if event.type()==QEvent.Type.Resize:self.retranslate()
        return False

    def show(self):
        for dialog in self.dialogs:
            if not dialog.closed:dialog.raise_();dialog.activateWindow();return
        root=getattr(self.window,'sandbox',None) or getattr(self.window,'browse_sandbox',None)
        metadata=f'Quest UI diagnostics\nPython: {sys.version.split()[0]}\nQt: {qVersion()}\nPlatform: {sys.platform}\nWindow: {type(self.window).__name__} {self.window.width()}x{self.window.height()}\nData: {root or "-"}'
        dialog=DiagnosticsDialog(root,metadata,self.window);self.dialogs.append(dialog)
        self.dialogs=[item for item in self.dialogs if item is dialog or item.tasks.busy()]
        dialog.show()

    def retranslate(self):
        text=tr('qt.logs.open');compact=self.window.header.width()<1100;self.button.setText('' if compact else text)
        if self.button.property('compactIcon')!=compact:
            self.button.setProperty('compactIcon',compact);self.button.style().unpolish(self.button);self.button.style().polish(self.button)
            self.button.updateGeometry()
        self.button.setToolTip(text);self.button.setAccessibleName(text)

    def close(self):
        for dialog in self.dialogs:dialog.close()

    def wait_closed(self):
        for dialog in self.dialogs:dialog.wait_closed()
