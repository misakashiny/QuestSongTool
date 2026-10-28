"""UI-thread consumer for the injected, read-only device provider."""
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QComboBox, QGridLayout, QLayout, QPushButton, QWidget

from ..messages import tr
from ..downloads import DownloadError
from ..song_search import SearchSession
from .device_protocol import DeviceClient, descriptor, snapshot_rows
from .surfaces import action_icons
from .library_header import InlineLabel


def directory_presentation(item):
    """Describe known ADB roots for display only; never change their identity."""
    root = item['root'].replace('/storage/emulated/0/', '/sdcard/', 1)
    prefix = '/sdcard/ModData/com.beatgames.beatsaber/Mods/'
    category = {prefix+'SongCore/CustomLevels':'songcore',
                prefix+'SongLoader/CustomLevels':'songloader',
                prefix+'SongCore/CustomWIPLevels':'wip'}.get(root) if item['kind']=='adb' else None
    if category:
        return (tr('qt.device.directory.'+category)+' · '+item['label'],
                tr('qt.device.directory.'+category+'_hint')+'\n'+item['label']+'\n'+item['root'])
    return item['label'], item['label']+'\n'+item['root']


class DeviceBrowser(QObject):
    def __init__(self, window, provider=None):
        super().__init__(window)
        self.window = window;self.closed = False
        self.session = SearchSession(DeviceClient(provider)) if provider is not None else None
        self.generation = None;self.action = None;self.selected = None;self.devices = []
        self.read_after_connect = False
        self.read_epoch = 0
        self.media_deferred = False
        self.connection_open = False;self.preferred_transport = None
        self.busy = False;self.state = 'idle' if self.session else 'disabled';self.rejected = 0;self.skipped_names = 0
        self.page = QWidget();layout = QGridLayout(self.page);layout.setContentsMargins(0,0,0,0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.choices = QComboBox();layout.addWidget(self.choices,0,0);layout.setColumnStretch(0,1)
        self.refresh = QPushButton();self.read = QPushButton();self.clear = QPushButton()
        action_icons((self.refresh,'refresh'),(self.read,'folder'),(self.clear,'close'))
        layout.addWidget(self.refresh,0,1);layout.addWidget(self.read,0,2);layout.addWidget(self.clear,0,3)
        self.status = InlineLabel();self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status.setMaximumWidth(max(160,window.width()//2));window.statusBar().addPermanentWidget(self.status,1)
        self.status.setObjectName('deviceState')
        self.timer = QTimer(self);self.timer.setInterval(75);self.timer.timeout.connect(self.poll)
        self.refresh.clicked.connect(self.discover);self.read.clicked.connect(self.read_selected)
        self.clear.clicked.connect(self.cancel);self.choices.currentIndexChanged.connect(self.choose)
        self.retranslate()

    def discover(self):
        if self.closed or self.session is None or self.connection_open:return
        self.read_epoch += 1
        self.devices = [];self.rejected = 0;self.populate()
        self.request({'action':'list'})

    def choose(self, _index):
        if self.closed:return
        index = self.choices.currentData()
        candidate = self.devices[index] if type(index) is int and 0<=index<len(self.devices) else None
        if candidate!=self.selected:
            self.read_epoch += 1
            self.invalidate();self.selected = candidate
            self.state = 'choose' if self.devices else 'idle' if self.session else 'disabled'
        self.render()

    def read_selected(self):
        if not self.closed and not self.connection_open and self.session and self.selected is not None:
            self.read_epoch += 1
            self.request({'action':'read','device':dict(self.selected),'metadata_only':True})

    def invalidate(self, keep_snapshot=False):
        if self.session:self.session.cancel()
        self.generation = None;self.action = None;self.busy = False;self.skipped_names = 0;self.timer.stop()
        self.media_deferred = False
        if keep_snapshot and self.window.quest_model.rows:
            self.window.quest_ready = False;self.window.quest_stale = True
            self.window.reports.changed()
            self.window.quest_media.reset();self.window.update_comparison()
        else:self.window.clear_quest_snapshot()

    def request(self, value):
        self.invalidate(keep_snapshot=True)
        self.action = value['action'];self.state = 'discovering' if self.action=='list' else 'loading';self.busy = True
        self.generation = self.session.request(value,0,'');self.timer.start();self.render()

    def cancel(self):
        self.read_after_connect = False
        self.read_epoch += 1
        if self.closed:return
        self.invalidate();self.state = 'cancelled' if self.session else 'disabled';self.render()

    def poll(self):
        if self.closed or self.session is None:return
        for message in self.session.tasks.drain():
            data = message.payload
            if not isinstance(data,dict) or self.generation is None or data.get('generation')!=self.generation:continue
            if message.kind not in ('search','search_error'):continue
            self.busy = False;self.timer.stop()
            if message.kind=='search_error':
                self.read_after_connect = False
                self.preferred_transport = None;self.read_epoch += 1
                self.window.quest_ready = False
                self.window.quest_stale = bool(self.window.quest_model.rows)
                self.window.update_comparison()
                self.state = {'device_disconnected':'disconnected','device_rejected':'rejected','device_protocol':'rejected',
                    'device_unauthorized':'unauthorized','device_offline':'offline','device_adb_path':'adb_path',
                    'device_roots':'roots','device_timeout':'timeout','device_command':'command'}.get(data.get('code'),'failed')
            else:
                try:
                    result = data['result']
                    if result['action']!=self.action:raise ValueError('device action')
                    if self.action=='list':
                        if not isinstance(result['devices'],list) or len(result['devices'])>1024:raise ValueError('device count')
                        choices = [descriptor(item) for item in result['devices']]
                        if len({item['identity'] for item in choices})!=len(choices):raise ValueError('device duplicate')
                        if type(result['rejected']) is not int or result['rejected']<0:raise ValueError('device count')
                        self.devices = choices;self.rejected = result['rejected'];self.populate()
                        self.state = 'choose' if self.devices else 'rejected' if self.rejected else 'none'
                        preferred_transport = self.preferred_transport or (self.selected['device_id'] if self.selected else None)
                        matches = [i for i,item in enumerate(choices) if item['device_id']==preferred_transport]
                        if matches:
                            preferred = next((i for i in matches if choices[i]['root'].endswith('/SongCore/CustomLevels')),matches[0])
                            preferred = next((i for i in matches if self.selected and choices[i]['identity']==self.selected['identity']),preferred)
                            self.choices.setCurrentIndex(preferred+1)
                        if not matches:self.selected = None
                        if matches and self.read_after_connect:
                            expected = self.selected['identity']
                            QTimer.singleShot(0, lambda identity=expected,epoch=self.read_epoch:self.read_selected() if epoch==self.read_epoch and not self.closed and self.selected is not None and self.selected['identity']==identity and not self.busy and not self.connection_open else None)
                        self.read_after_connect = False
                        self.preferred_transport = None
                    else:
                        if self.selected is None or result['identity']!=self.selected['identity']:raise ValueError('device identity')
                        rows = snapshot_rows(result['rows'])
                        self.media_deferred = result.get('media_deferred') is True
                        skipped = result.get('skipped_names',0)
                        if type(skipped) is not int or not 0<=skipped<=100000:raise ValueError('skipped names')
                        if skipped:self.window.set_quest_snapshot(rows,result['identity'],skipped=skipped)
                        else:self.window.set_quest_snapshot(rows,result['identity'])
                        self.skipped_names = skipped
                        self.window.quest_display = self.selected['label'];self.window.update_comparison()
                        self.state = 'partial' if skipped else 'ready' if rows else 'empty'
                except (KeyError,TypeError,ValueError,RuntimeError,DownloadError):
                    self.read_after_connect = False;self.preferred_transport = None;self.read_epoch += 1
                    self.window.clear_quest_snapshot();self.state = 'rejected'
            self.generation = None;self.action = None;self.render()

    def populate(self):
        self.choices.blockSignals(True);self.choices.clear();self.choices.addItem(tr('qt.device.pick'),None)
        for index,item in enumerate(self.devices):
            label,hint = directory_presentation(item)
            self.choices.addItem(label,index)
            self.choices.setItemData(index+1,hint,Qt.ItemDataRole.ToolTipRole)
            self.choices.setItemData(index+1,hint,Qt.ItemDataRole.AccessibleDescriptionRole)
        self.choices.blockSignals(False)

    def retranslate(self):
        self.refresh.setText(tr('qt.device.list'));self.read.setText(tr('qt.device.read'));self.clear.setText(tr('qt.device.clear_short'));self.clear.setToolTip(tr('qt.device.clear'))
        self.choices.setAccessibleName(tr('qt.device.pick'))
        self.choices.setPlaceholderText(tr('qt.device.pick'))
        if self.choices.count():self.choices.setItemText(0,tr('qt.device.pick'))
        for index,item in enumerate(self.devices):
            label,hint = directory_presentation(item)
            self.choices.setItemText(index+1,label)
            self.choices.setItemData(index+1,hint,Qt.ItemDataRole.ToolTipRole)
            self.choices.setItemData(index+1,hint,Qt.ItemDataRole.AccessibleDescriptionRole)
        self.render()

    def render(self):
        enabled = self.session is not None and not self.closed and not self.connection_open
        self.refresh.setEnabled(enabled and not self.busy)
        self.read.setEnabled(enabled and not self.busy and self.selected is not None)
        self.choices.setEnabled(enabled and bool(self.devices))
        hint = directory_presentation(self.selected)[1] if self.selected else tr('qt.device.pick')
        self.choices.setToolTip(hint);self.choices.setAccessibleDescription(hint)
        self.clear.setEnabled(enabled and (self.busy or self.window.quest_ready or self.state not in ('idle','cancelled')))
        self.clear.setText(tr('qt.device.cancel_short' if self.busy else 'qt.device.clear_short'))
        self.status.setText(tr('qt.device.state.'+self.state,count=self.skipped_names if self.state=='partial' else len(self.devices),songs=self.window.quest_model.rowCount()))
        if self.media_deferred and self.window.quest_ready and hasattr(self.window,'quest_media'):
            self.status.setText(self.status.text()+' · '+self.window.quest_media.status())
        if self.window.quest_stale:
            self.status.setText(self.status.text()+' · '+tr('ux.stale'))
        self.status.setToolTip(self.status.text()+'\n'+tr('qt.device.excluded',count=self.rejected))
        self.status.setMaximumWidth(max(160,self.window.width()//2))
        self.status.setVisible(self.session is not None)
        self.read.setToolTip(self.status.text())
        if self.status.property('status') != self.state:
            self.status.setProperty('status', self.state)
            style = self.status.style();style.unpolish(self.status);style.polish(self.status)
        wireless=getattr(self.window,'wireless',None)
        if wireless is not None and hasattr(wireless,'recovery'):wireless.recovery.browser_rendered()

    def close(self):
        if self.closed:return
        self.read_after_connect = False
        self.closed = True;self.timer.stop()
        if self.session:self.session.close()

    def wait_closed(self):
        if self.session:
            for thread in tuple(self.session.tasks._threads):thread.join()
