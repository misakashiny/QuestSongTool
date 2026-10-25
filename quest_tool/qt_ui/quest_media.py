"""Visible Quest artwork and duration enrichment, separate from the song list."""
from collections import OrderedDict
import math

from PySide6.QtCore import QEvent, QObject, Qt, QTimer

from ..downloads import DownloadError, check_cancel
from ..messages import tr
from ..song_search import SearchSession
from .cover_browser import CoverCache


class QuestMediaClient:
    def __init__(self, provider):self.provider = provider

    def search(self, request, _page, _order, cancel):
        check_cancel(cancel)
        result = self.provider.read_assets(request['device'],request['items'],cancel)
        check_cancel(cancel)
        if not isinstance(result,dict) or result.get('identity')!=request['device']['identity']:
            raise DownloadError('device_rejected')
        values = result.get('items');expected = {row['folder'] for row in request['items']}
        if not isinstance(values,list) or len(values)!=len(expected):raise DownloadError('device_protocol')
        seen = set()
        for row in values:
            if not isinstance(row,dict) or row.get('folder') not in expected or row['folder'] in seen:
                raise DownloadError('device_protocol')
            seen.add(row['folder'])
            if type(row.get('cover_done')) is not bool or type(row.get('duration_done')) is not bool:
                raise DownloadError('device_protocol')
            value = row.get('duration',0)
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:
                raise DownloadError('device_protocol')
            image = row.get('image')
            if image is not None and (not isinstance(image,bytes) or len(image)>1024*1024):
                raise DownloadError('device_protocol')
            if row.get('error','') not in ('','changed','cover','duration','metadata'):
                raise DownloadError('device_protocol')
        return dict(result,epoch=request['epoch'])


class QuestMediaPresenter(QObject):
    """One worker, four songs per batch; caches are scoped to a single snapshot."""
    def __init__(self, window, provider):
        super().__init__(window)
        self.window = window
        self.session = SearchSession(QuestMediaClient(provider)) if callable(getattr(provider,'read_assets',None)) else None
        self.cache = CoverCache();self.encoded = OrderedDict();self.attempted = OrderedDict()
        self.encoded_bytes = 0;self.epoch = 0;self.generation = None;self.busy = False
        self.closed = False;self.notice = '';self.requested = []
        self.timer = QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.poll)
        self.debounce = QTimer(self);self.debounce.setSingleShot(True);self.debounce.setInterval(80)
        self.debounce.timeout.connect(self.request_visible)
        self.views = (window.quest_table,window.missing_table)
        for view in self.views:
            view.viewport().installEventFilter(self)
            view.verticalScrollBar().valueChanged.connect(self.schedule)
            view.model().layoutChanged.connect(self.schedule)
            view.model().modelReset.connect(self.schedule)
        window.details.selectionChanged.connect(self.schedule)

    def eventFilter(self, watched, event):
        if not self.closed and event.type() in (QEvent.Type.Resize,QEvent.Type.Show):self.schedule()
        return False

    def reset(self):
        if self.closed:return
        self.epoch += 1
        if self.session:self.session.cancel()
        self.timer.stop();self.debounce.stop();self.generation = None;self.busy = False
        self.cache = CoverCache();self.encoded.clear();self.attempted.clear();self.encoded_bytes = 0
        self.notice = '';self.requested = [];self.schedule()

    def schedule(self, *_args):
        if not self.closed and self.session:self.debounce.start()

    def status(self):
        return tr('qt.device.media.'+('loading' if self.busy else self.notice or 'lazy'))

    def image_bytes(self, folder):
        data = self.encoded.get(folder)
        if data is not None:self.encoded.move_to_end(folder)
        return data

    def candidates(self):
        keys = [];detail = self.window.details
        if detail.key and detail.key[:2]==('quest',self.window.quest_identity):keys.append(detail.key[2])
        for view in self.views:
            if not view.isVisible():continue
            start = max(0,view.rowAt(0))
            for index in range(start,min(view.model().rowCount(),start+64)):
                cell = view.model().index(index,0)
                if not view.visualRect(cell).intersects(view.viewport().rect()):continue
                row = cell.data(Qt.ItemDataRole.UserRole)
                if view is self.window.missing_table and row.get('origin')!='quest':continue
                keys.append(row['folder'])
        return list(dict.fromkeys(keys))

    def request_visible(self):
        window = self.window;browser = window.device_browser
        if self.closed or self.session is None or self.busy or not window.quest_ready or not browser.media_deferred:return
        if browser.selected is None or browser.selected['identity']!=window.quest_identity:return
        rows = {row['folder']:row for row in window.quest_model.rows};items = []
        for key in self.candidates():
            row = rows.get(key)
            if row is None or not row.get('info_hash') or row.get('error'):continue
            done_cover,done_duration = self.attempted.get(key,(False,False))
            need_cover = (key not in self.cache.sources or key not in self.encoded) and not done_cover
            need_duration = row['duration']<=0 and not done_duration
            if not need_cover and not need_duration:continue
            items.append(dict(row,need_cover=need_cover,need_duration=need_duration))
            if len(items)==4:break
        if not items:return
        self.requested = items;self.busy = True
        self.generation = self.session.request(dict(device=dict(browser.selected),items=items,epoch=self.epoch),0,'')
        self.timer.start();browser.render()

    def remember(self, folder, cover, duration):
        old_cover,old_duration = self.attempted.pop(folder,(False,False))
        self.attempted[folder] = (cover or old_cover,duration or old_duration)
        while len(self.attempted)>256:self.attempted.popitem(last=False)

    def store_image(self, folder, image):
        previous = set(self.cache.sources)
        if image is None or not self.cache.put(folder,image):return
        evicted = previous-set(self.cache.sources)
        self.encoded_bytes -= len(self.encoded.pop(folder,b''))
        self.encoded[folder] = image;self.encoded_bytes += len(image)
        while len(self.encoded)>128 or self.encoded_bytes>16*1024*1024:
            key,old = self.encoded.popitem(last=False);self.encoded_bytes -= len(old);evicted.add(key)
        # A pixmap evicted by the existing bounded cache can be loaded on revisit.
        for key in evicted:
            if key in self.attempted:
                cover,duration = self.attempted[key]
                if cover:self.attempted[key] = (False,duration)

    def apply(self, value):
        folder = value['folder']
        self.remember(folder,value['cover_done'],value['duration_done'])
        if value.get('error')=='changed':self.notice = 'changed';return
        if value.get('error') and self.notice!='changed':self.notice = 'failed'
        if value.get('image') is not None:self.store_image(folder,value['image'])
        for model in (self.window.quest_model,self.window.missing_model):
            for index,row in enumerate(model.rows):
                if row['folder']!=folder or (model is self.window.missing_model and row.get('origin')!='quest'):continue
                if value.get('duration',0)>0:row['duration'] = value['duration']
                model.dataChanged.emit(model.index(index,0),model.index(index,3),[Qt.ItemDataRole.DisplayRole,Qt.ItemDataRole.UserRole])
                if model is self.window.quest_model:self.window.details.update_remote(row,self.image_bytes(folder))
        for view in self.views:view.viewport().update()

    def poll(self):
        if self.closed or self.session is None:return
        for message in self.session.tasks.drain():
            data = message.payload
            if not isinstance(data,dict) or self.generation is None or data.get('generation')!=self.generation:continue
            if message.kind not in ('search','search_error'):continue
            self.busy = False;self.timer.stop();self.generation = None
            if message.kind=='search_error':
                code = data.get('code')
                if code in ('device_disconnected','device_rejected','device_unauthorized','device_offline','device_roots'):
                    browser = self.window.device_browser;browser.invalidate()
                    browser.state = {'device_disconnected':'disconnected','device_rejected':'rejected',
                        'device_unauthorized':'unauthorized','device_offline':'offline','device_roots':'roots'}[code]
                    browser.render();return
                for row in self.requested:self.remember(row['folder'],row['need_cover'],row['need_duration'])
                if self.notice!='changed':self.notice = 'failed'
            else:
                result = data['result']
                if result.get('epoch')!=self.epoch or result.get('identity')!=self.window.quest_identity:continue
                for value in result['items']:self.apply(value)
            self.requested = [];self.window.device_browser.render();self.schedule()

    def close(self):
        if self.closed:return
        self.reset();self.closed = True;self.timer.stop();self.debounce.stop()
        for view in self.views:view.viewport().removeEventFilter(self)
        if self.session:self.session.close()

    def wait_closed(self):
        if self.session:
            for thread in tuple(self.session.tasks._threads):thread.join()
