"""Recent addresses and passive health checks; reconnection requires a click."""
import json
import re

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QPushButton

from ..downloads import DownloadError
from ..messages import tr
from ..song_search import SearchSession
from .surfaces import action_icons
from .wireless_history import WirelessHistory, recent_record


class WirelessRecovery(QObject):
    def __init__(self, owner, client):
        super().__init__(owner)
        self.owner=owner;self.window=owner.window
        self.store=WirelessHistory(self.window.sandbox);self.history_error=False;self.rows=[]
        self.active=None;self.state='';self.generation=None;self.context=None;self.closed=False
        self.session=SearchSession(client) if client is not None else None
        self.timer=QTimer(self);self.timer.setInterval(5000);self.timer.timeout.connect(self.check)
        self.poll_timer=QTimer(self);self.poll_timer.setInterval(75);self.poll_timer.timeout.connect(self.poll)
        self.button=QPushButton(self.window.device_browser.page);self.button.setObjectName('wirelessReconnect')
        action_icons((self.button,'refresh'));self.button.clicked.connect(self.reconnect)
        layout=self.window.device_browser.page.layout()
        layout.addWidget(self.button,0,5)
        self.button.hide();self.reload()

    def reload(self):
        try:self.rows=self.store.load();self.history_error=False
        except (OSError,ValueError,TypeError,DownloadError):self.rows=[];self.history_error=True

    def configure(self, form):
        form.populate_recent(self.rows,self.active['hardware'] if self.active else None)
        form.forgetRequested.connect(self.forget)
        self.history_status(form)

    def history_status(self, form):
        form.history_note.setText(tr('qt.wireless.history_error' if self.history_error else 'qt.wireless.recent_hint'))

    def forget(self, hardware):
        try:self.store.forget(hardware)
        except (OSError,ValueError,TypeError,DownloadError):self.history_error=True
        else:self.reload()
        if self.owner.dialog:
            self.owner.dialog.populate_recent(self.rows);self.history_status(self.owner.dialog)

    def cancel_check(self):
        if self.session:self.session.cancel()
        self.generation=None;self.context=None;self.poll_timer.stop()

    def begin(self, request):
        self.cancel_check();self.timer.stop()
        if request['mode']=='reconnect':
            self.active=dict(recent_record(dict(request,model='Quest')),serial=f"{request['host']}:{request['port']}")
        else:self.active=None
        self.state='connecting'
        self.render()

    def accepted(self, result):
        host,number=result['endpoint'].rsplit(':',1)
        model=result.get('model','Quest')
        model=''.join(c for c in model if ord(c)>=32)[:80].strip() if isinstance(model,str) else ''
        row=recent_record(dict(result,host=host,port=number,model=model or 'Quest'))
        self.active=dict(row,serial=result.get('serial',result['endpoint']));self.state='online'
        try:self.store.remember(row)
        except (OSError,ValueError,TypeError,DownloadError):self.history_error=True
        else:self.reload()
        if self.owner.dialog:self.history_status(self.owner.dialog)
        self.timer.start();self.render()

    def failed(self, code):
        self.state={'device_unauthorized':'unauthorized','device_rejected':'rejected','device_offline':'offline'}.get(code,'disconnected')
        self.timer.stop();self.render()

    def binding(self):
        selected=self.window.device_browser.selected
        if not selected or selected['kind']!='adb':return None
        try:
            serial,hardware=json.loads(selected['device_id'])
            if not isinstance(serial,str) or not isinstance(hardware,str):return None
            return serial,hardware
        except (ValueError,TypeError):return None

    def browser_rendered(self):
        if self.closed:return
        browser=self.window.device_browser;binding=self.binding()
        if self.active and browser.preferred_transport==json.dumps([self.active['serial'],self.active['hardware']],separators=(',',':')):
            # Discovery still exposes the previous USB selection until its
            # result arrives; that temporary mismatch is not a disconnection.
            self.render();return
        if not browser.busy and not browser.connection_open and self.state!='connecting' and (browser.state=='cancelled' or (not binding and browser.state in ('choose','idle','none','disabled'))):
            self.cancel_check();self.timer.stop();self.active=None;self.state=''
        if binding and self.active and binding!=(self.active['serial'],self.active['hardware']):
            self.cancel_check();self.timer.stop();self.active=None;self.state=''
        if binding and not self.active and self.window.quest_ready and browser.state in ('ready','partial','empty'):
            match=re.fullmatch(r'([0-9.]+):([0-9]+)',binding[0])
            if match and self.session:
                try:row=recent_record(dict(host=match[1],port=match[2],hardware=binding[1],model=browser.selected['model'] or 'Quest'))
                except (ValueError,DownloadError):pass
                else:self.active=dict(row,serial=binding[0]);self.state='online';self.timer.start()
        if self.active and not self.owner.busy and not browser.busy:
            if browser.state in ('offline','disconnected','unauthorized','rejected'):
                self.state=browser.state;self.cancel_check();self.timer.stop()
            elif self.window.quest_ready and self.window.quest_identity==(browser.selected or {}).get('identity'):
                self.state='online';self.timer.start()
        self.render()

    def can_check(self):
        browser=self.window.device_browser
        return (self.session is not None and self.active is not None and self.state=='online' and self.generation is None
                and not self.closed and self.owner.available() and not self.owner.busy and self.owner.dialog is None
                and not browser.connection_open and not browser.busy and not self.window.quest_media.busy
                and not (browser.session and browser.session.tasks.busy())
                and not (self.window.quest_media.session and self.window.quest_media.session.tasks.busy())
                and self.binding()==(self.active['serial'],self.active['hardware'])
                and self.window.isVisible() and not self.window.isMinimized())

    def check(self):
        if not self.can_check() or self.session.tasks.busy():return
        self.context=(self.active['serial'],self.active['hardware'],self.window.device_browser.read_epoch)
        self.generation=self.session.request(dict(self.active,mode='check',port=str(self.active['port'])),0,'')
        self.poll_timer.start()

    def poll(self):
        if self.closed or self.session is None:return
        for message in self.session.tasks.drain():
            data=message.payload
            if not isinstance(data,dict) or self.generation is None or data.get('generation')!=self.generation:continue
            if message.kind not in ('search','search_error'):continue
            context=self.context;self.generation=None;self.context=None;self.poll_timer.stop()
            browser=self.window.device_browser
            if not self.active or context!=(self.active['serial'],self.active['hardware'],browser.read_epoch) or self.binding()!=context[:2]:continue
            # A transfer or another user action may have started while checking.
            if not self.owner.available() or self.owner.dialog or browser.busy:continue
            state=data['result']['state'] if message.kind=='search' else {'device_rejected':'rejected','device_unauthorized':'unauthorized','device_offline':'offline'}.get(data.get('code'),'disconnected')
            if state=='online':continue
            self.state=state;self.timer.stop()
            browser.read_epoch+=1;browser.read_after_connect=False
            browser.invalidate(keep_snapshot=True);browser.state=state;browser.render()

    def render(self):
        failed=self.active is not None and self.state in ('offline','disconnected','unauthorized','rejected')
        self.button.setText(tr('qt.wireless.reconnect'));self.button.setToolTip(tr('qt.wireless.recent_hint'))
        self.button.setVisible(failed);self.button.setEnabled(failed and self.owner.available() and not self.owner.busy)
        if failed:
            status=self.window.device_browser.status
            status.setText(tr('qt.wireless.recovery.'+self.state)+' · '+tr('qt.wireless.reload_hint'));status.show()
        elif self.active and self.state=='online' and self.binding()==(self.active['serial'],self.active['hardware']) and not self.window.quest_ready and not self.window.device_browser.busy:
            self.window.device_browser.status.setText(tr('qt.wireless.connected_read'))

    def reconnect(self):
        if not self.active or self.closed or not self.owner.available() or self.owner.busy:return
        row=dict(self.active)
        self.owner.show()
        if self.owner.dialog is None:return
        self.owner.dialog.populate_recent([row]+[item for item in self.rows if item['hardware']!=row['hardware']],row['hardware'])
        self.owner.dialog.submit()

    def close(self):
        self.closed=True;self.timer.stop();self.cancel_check()
        if self.session:self.session.close()

    def wait_closed(self):
        if self.session:
            for thread in tuple(self.session.tasks._threads):thread.join()
