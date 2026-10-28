"""Read-only song details. Workers return plain metadata and normalized PNG bytes."""
import os
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFormLayout, QFrame, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..downloads import check_cancel
from ..fsapi import LocalFS, Location
from ..messages import tr
from ..song_search import SearchSession
from ..theme import DESKTOP, DOWNLOAD, SPACE
from ..workers import human_size
from .library_covers import local_cover
from .cover_browser import CoverCache
from .playlist_widgets import RoundedCover


class DetailCover(RoundedCover):
    """One normalized cover, decoded once; bounded DPR-aware scaled images."""
    def __init__(self):
        super().__init__()
        self.cache = CoverCache();self.data = None;self.ready = False
        self.placeholder = '';self.rendered_key = None
        super().setObjectName('libraryDetailCover')
        super().setAlignment(Qt.AlignmentFlag.AlignCenter)
        super().setTextFormat(Qt.TextFormat.PlainText)
        super().setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        super().setFixedHeight(DESKTOP.LIBRARY_DETAIL_ART)

    def set_image(self,data,placeholder):
        self.placeholder = placeholder
        if data != self.data:
            self.data = data;self.cache = CoverCache();self.rendered_key = None
            self.ready = bool(data) and self.cache.put('detail',data)
        self.refresh_image()

    def refresh_image(self):
        if not self.ready:
            super().setText(self.placeholder);return
        side = max(1,min(super().contentsRect().width(),DESKTOP.LIBRARY_DETAIL_ART))
        ratio = super().devicePixelRatioF();key = (side,ratio)
        if key == self.rendered_key:return
        image = self.cache.image('detail',QSize(side,side),ratio)
        if image is not None:
            super().setPixmap(image);self.rendered_key = key

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.refresh_image()

    def event(self,event):
        handled = super().event(event)
        if event.type()==QEvent.Type.DevicePixelRatioChange and hasattr(self,'cache'):
            self.rendered_key = None;self.refresh_image()
        return handled


class DetailMetadata(QFrame):
    """Wrap entire field rows when actual translated labels no longer fit."""
    def update_wrap(self):
        form = super().layout()
        if not isinstance(form,QFormLayout) or not form.rowCount():return
        labels = [];fields = []
        for row in range(form.rowCount()):
            label = form.itemAt(row,QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(row,QFormLayout.ItemRole.FieldRole)
            if label and label.widget():labels.append(label.widget().sizeHint().width())
            if field and field.widget():fields.append(field.widget().minimumWidth())
        margins = form.contentsMargins()
        required = max(labels,default=0)+max(fields,default=0)+form.horizontalSpacing()+margins.left()+margins.right()
        policy = QFormLayout.RowWrapPolicy.WrapAllRows if super().contentsRect().width()<required else QFormLayout.RowWrapPolicy.WrapLongRows
        if form.rowWrapPolicy()!=policy:form.setRowWrapPolicy(policy)

    def resizeEvent(self,event):
        super().resizeEvent(event);self.update_wrap()

    def event(self,event):
        handled = super().event(event)
        if event.type() in (QEvent.Type.LayoutRequest,QEvent.Type.FontChange,QEvent.Type.StyleChange):self.update_wrap()
        return handled


def folder_size(directory, cancel):
    errors = []
    total = 0
    for current, dirs, files in os.walk(directory, onerror=errors.append, followlinks=False):
        check_cancel(cancel)
        dirs[:] = [name for name in dirs if not (Path(current)/name).is_symlink() and not (Path(current)/name).is_junction()]
        for name in files:
            check_cancel(cancel)
            path = Path(current)/name
            try:
                if path.is_symlink():
                    errors.append(name)
                    continue
                total += path.stat().st_size
            except OSError as exc:
                errors.append(exc)
    return None if errors else total


class DetailClient:
    def search(self, request, _page, _order, cancel):
        root = Path(request['root']).resolve()
        folder = request['folder']
        if not folder or folder in ('.','..') or any(char in folder for char in '/\\'):raise ValueError('detail folder')
        directory = (root/folder).resolve()
        if directory == root or not directory.is_relative_to(root) or not directory.is_dir():raise ValueError('detail location')
        check_cancel(cancel)
        fs = LocalFS(Location('local',str(root)))
        try:
            meta = fs.scan_metadata([folder],cancel=cancel.is_set)[folder]
            check_cancel(cancel)
            image = None
            if meta.cover_file:
                path = (directory/meta.cover_file).resolve()
                try:
                    if path.is_relative_to(directory):image = local_cover(path)
                except (OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):
                    pass
            check_cancel(cancel)
            size = folder_size(directory,cancel)
            check_cancel(cancel)
            return {'key':request['key'], 'data':{
                'folder':folder,'title':meta.display_title,'subtitle':meta.subtitle,
                'author':meta.author,'bpm':meta.bpm,'duration':meta.duration,
                'version':meta.version,'difficulties':meta.difficulties,
                'size':size,'image':image,'error':meta.parse_error}}
        finally:
            fs.close()


class DetailPanel(QObject):
    selectionChanged = Signal()
    def __init__(self, window, session=None):
        super().__init__(window)
        self.window = window
        self.session = session or SearchSession(DetailClient())
        self.closed = False
        self.key = None
        self.active_view = None
        self.generation = None
        self.data = None
        self.loading = False
        self.failed = False
        self.current = None
        self.page = QFrame();self.page.setObjectName('card')
        layout = QVBoxLayout(self.page)
        self.heading = QLabel();self.heading.setProperty('role','subtitle');layout.addWidget(self.heading)
        self.scroll = QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setObjectName('detailScroll');self.scroll.viewport().setObjectName('detailViewport')
        self.scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        content = QWidget();content.setObjectName('detailContent');box = QVBoxLayout(content)
        box.setContentsMargins(SPACE.XS,SPACE.XS,SPACE.XS,SPACE.XS)
        box.setSpacing(SPACE.M)
        self.cover = DetailCover()
        box.addWidget(self.cover)
        self.title = self.label();self.title.setProperty('role','subtitle');box.addWidget(self.title)
        self.subtitle = self.label();box.addWidget(self.subtitle)
        self.author = self.label();box.addWidget(self.author)
        self.metadata = DetailMetadata();self.metadata.setObjectName('libraryDetailMetadata')
        form = QFormLayout(self.metadata);form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setContentsMargins(SPACE.M,SPACE.M,SPACE.M,SPACE.M)
        form.setHorizontalSpacing(SPACE.L);form.setVerticalSpacing(SPACE.S)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.fields = {};self.captions = {}
        for name in ('source','folder','bpm','duration','version','size','difficulties'):
            caption = self.label();value = self.label()
            caption.setProperty('role','muted')
            caption.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Preferred)
            field_policy = QSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
            field_policy.setHeightForWidth(True);value.setSizePolicy(field_policy)
            value.setMinimumWidth(DOWNLOAD.CONTROL*2)
            self.fields[name] = value;self.captions[name] = caption;form.addRow(caption,value)
        box.addWidget(self.metadata)
        self.state = self.label();self.state.setObjectName('libraryDetailState')
        self.state.setProperty('role','muted');box.addWidget(self.state)
        self.note = self.label();self.note.setProperty('role','muted');box.addWidget(self.note)
        self.extras = QVBoxLayout();box.addLayout(self.extras)
        box.addStretch();self.scroll.setWidget(content);layout.addWidget(self.scroll,1)
        self.retry = QPushButton();self.retry.clicked.connect(self.retry_current);layout.addWidget(self.retry)
        self.timer = QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.poll)
        self.retranslate()

    @staticmethod
    def label():
        label = QLabel();label.setWordWrap(True);label.setTextFormat(Qt.TextFormat.PlainText)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True);label.setSizePolicy(policy)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def select(self, view, index, force=False):
        if self.closed:return
        if not index.isValid():
            if view == self.active_view:self.clear()
            return
        row = index.data(Qt.ItemDataRole.UserRole)
        origin = row.get('origin') if view == 'missing' else view
        if origin not in ('local','quest'):self.clear();return
        source = str(self.window.target) if origin == 'local' else self.window.quest_identity
        if (origin == 'local' and not self.window.local_ready) or (origin == 'quest' and not self.window.quest_ready):self.clear();return
        key = (origin,source,row['folder'])
        self.active_view = view
        self.current = (view,dict(row))
        if key == self.key and not force:return
        self.session.cancel();self.timer.stop();self.generation = None
        self.key = key
        self.loading = origin == 'local';self.failed = False
        self.data = dict(subtitle='',bpm=0,version='',difficulties=(),size=None,image=None) | row
        if origin=='quest' and hasattr(self.window,'quest_media'):
            self.data['image'] = self.window.quest_media.image_bytes(row['folder'])
        if origin == 'local':
            self.generation = self.session.request({'key':key,'root':source,'folder':row['folder']},0,'')
            self.timer.start()
        self.selectionChanged.emit()
        self.render()

    def update_remote(self, row, image):
        if self.key is None or self.key!=('quest',self.window.quest_identity,row['folder']):return
        self.data.update(row);self.data['image'] = image
        self.render()

    def retry_current(self):
        if self.current is None:return
        view,row = self.current
        # Re-find the row; a removed or filtered entry cannot revive old details.
        proxy = {'local':self.window.proxy,'quest':self.window.quest_proxy,'missing':self.window.missing_proxy}[view]
        for index in range(proxy.rowCount()):
            candidate = proxy.index(index,0)
            if candidate.data(Qt.ItemDataRole.UserRole)['folder']==row['folder']:
                self.select(view,candidate,True);return
        self.clear()

    def poll(self):
        if self.closed:return
        for message in self.session.tasks.drain():
            payload = message.payload
            if not isinstance(payload,dict) or payload.get('generation') != self.generation:continue
            if message.kind not in ('search','search_error'):continue
            if self.key is None:continue
            source = str(self.window.target) if self.key[0]=='local' else self.window.quest_identity
            if source != self.key[1]:self.clear();return
            if message.kind=='search' and tuple(payload['result']['key'])!=self.key:continue
            self.loading = False;self.failed = message.kind=='search_error'
            if not self.failed and tuple(payload['result']['key']) == self.key:self.data = payload['result']['data']
            self.timer.stop();self.render()

    def clear(self):
        if self.closed:return
        self.session.cancel();self.timer.stop();self.key=None;self.generation=None
        self.active_view=None;self.current=None;self.data=None;self.loading=False;self.failed=False;self.selectionChanged.emit();self.render()

    def render(self):
        row = self.data or {}
        unknown = tr('search.unknown')
        self.title.setText(row.get('title') or tr('qt.detail.choose'))
        self.subtitle.setText(row.get('subtitle',''));self.author.setText(row.get('author',''))
        self.subtitle.setVisible(bool(row.get('subtitle')));self.author.setVisible(bool(row.get('author')))
        seconds = row.get('duration',0);bpm = row.get('bpm',0)
        values = {'source':tr('qt.library.'+self.key[0]) if self.key else unknown,
                  'folder':row.get('folder') or unknown,
                  'bpm':f'{bpm:g}' if bpm else unknown,
                  'duration':f'{int(seconds)//60}:{int(seconds)%60:02}' if seconds>0 else unknown,
                  'version':row.get('version') or unknown,
                  'size':human_size(row['size']) if row.get('size') is not None else unknown,
                  'difficulties':', '.join(row.get('difficulties',())) or unknown}
        for name,label in self.fields.items():label.setText(values[name])
        self.cover.set_image(row.get('image'),tr('media.no_cover'))
        if hasattr(self.window, 'preview'):self.window.preview.render()
        state = 'qt.detail.failed' if self.failed else 'qt.detail.loading' if self.loading else 'qt.detail.incomplete' if row.get('error') else 'qt.detail.snapshot' if self.key and self.key[0]=='quest' else 'qt.detail.ready' if self.key else 'qt.detail.choose'
        self.state.setText(tr(state))
        status = 'error' if self.failed else 'warning' if row.get('error') else 'normal'
        if self.state.property('status') != status:
            self.state.setProperty('status',status)
            style = self.state.style();style.unpolish(self.state);style.polish(self.state)
        self.retry.setVisible(self.failed)

    def retranslate(self):
        self.heading.setText(tr('qt.library.details'));self.retry.setText(tr('qt.detail.retry'))
        for name,label in self.captions.items():label.setText(tr('qt.detail.'+name))
        self.note.setText(tr('qt.detail.next'));self.render()

    def close(self):
        self.closed = True;self.timer.stop();self.session.close()

    def wait_closed(self):
        for thread in tuple(self.session.tasks._threads):thread.join()
