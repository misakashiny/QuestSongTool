"""UI-thread coordinator. All device work is explicitly requested and queued."""
import json
import queue
import uuid

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QPushButton

from ..downloads import DownloadError
from ..messages import tr
from ..song_search import SearchSession
from .wireless_form import WirelessForm
from .wireless_validation import validated
from .wireless_recovery import WirelessRecovery
from .surfaces import action_icons


class LibraryWireless(QObject):
    def __init__(self, window, client=None, transfer_factory=None):
        super().__init__(window)
        self.window = window;self.transfer_factory = transfer_factory
        self.session = SearchSession(client) if client is not None else None
        self.dialog = None;self.closed = False;self.generation = None;self.busy = False;self.result = None
        self.operation = None
        self.button = QPushButton(window.device_browser.page);self.button.setObjectName('wirelessConnect')
        window.device_browser.page.layout().addWidget(self.button,0,4)
        self.button.setEnabled(self.session is not None);action_icons((self.button,'settings'))
        self.button.clicked.connect(self.show)
        self.timer = QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.poll)
        self.retranslate()
        # Creating the adapters performs no device I/O. Actions are visible from
        # startup; the existing transfer dialog still confirms each operation.
        if transfer_factory is not None and window.transfers.backend is None and window.transfers.push_backend is None:
            self.enable_transfers()
        self.recovery = WirelessRecovery(self,client)

    def available(self):
        transfers = self.window.transfers
        deletion = getattr(self.window,'deletion',None);playlists = getattr(self.window,'playlists',None)
        return not (self.closed or self.window.closed or transfers.dialog is not None or any(job.tasks.busy() for job in transfers.jobs)
                    or (deletion and (deletion.confirming or deletion.generation is not None))
                    or (playlists and playlists.device_generation is not None))

    def show(self):
        if self.dialog is not None:self.dialog.raise_();self.dialog.activateWindow();return
        if self.session is None:return
        if not self.available() or self.busy:
            self.button.setToolTip(tr('qt.wireless.wait'));return
        self.recovery.cancel_check()
        self.result = None
        dialog = WirelessForm(self.window);self.dialog = dialog
        dialog.requestReady.connect(self.start);dialog.cancelRequested.connect(self.cancel)
        dialog.contextChanged.connect(self.clear_result);dialog.finished.connect(self.finished)
        dialog.read_button.clicked.connect(self.read_connected)
        self.recovery.configure(dialog)
        self.window.device_browser.connection_open = True;self.window.device_browser.render()
        self.window.transfers.render();dialog.open()

    def start(self, request):
        if self.dialog is None or self.busy or not self.available():return
        try:validated(request)
        except DownloadError as exc:self.status(exc.code);return
        if request['mode'] not in ('scan','discover','diagnose'):self.result = None
        self.dialog.read_button.setEnabled(False)
        if request['mode'] not in ('scan','discover','diagnose'):
            browser=self.window.device_browser
            self.recovery.begin(request)
            browser.read_after_connect=False;browser.read_epoch+=1
            browser.invalidate(keep_snapshot=True);browser.state='cancelled';browser.render()
        self.operation = uuid.uuid4().hex
        self.busy = True;self.generation = self.session.request(dict(request,operation=self.operation),0,'')
        self.dialog.set_busy(True)
        self.status({'scan':'scanning','discover':'discovering','diagnose':'diagnosing'}.get(request['mode'],'working'))
        self.timer.start()

    def clear_result(self):
        previous = self.result
        self.result = None
        if self.dialog is not None:
            self.dialog.read_button.setEnabled(False);self.dialog.run_button.setEnabled(not self.busy)
        if previous is not None and not self.busy:self.status('ready')

    def status(self, key, **values):
        if self.dialog is not None:self.dialog.status.setText(tr('qt.wireless.state.'+key,**values))

    def cancel(self):
        if not self.busy:return
        self.session.cancel();self.generation = None;self.operation = None;self.status('cancelling')
        if self.recovery.state=='connecting':self.recovery.failed('cancelled')
        if self.dialog is not None:self.dialog.cancel_button.setEnabled(False)

    def poll(self):
        if self.closed or self.session is None:return
        events = getattr(self.session.client,'events',None)
        if events is not None:
            while True:
                try:event = events.get_nowait()
                except queue.Empty:break
                if self.operation and event['operation']==self.operation:self.status(event['state'])
        for message in self.session.tasks.drain():
            data = message.payload
            if not isinstance(data,dict) or self.generation is None or data.get('generation')!=self.generation:continue
            if message.kind not in ('search','search_error'):continue
            self.generation = None
            if message.kind=='search_error':
                code = data.get('code','failed')
                allowed = {'wireless_address','wireless_port','wireless_code','wireless_usb','wireless_pair','wireless_tcpip',
                            'wireless_connect','wireless_command','device_rejected','device_adb_path','device_timeout',
                            'device_unauthorized','device_offline'}
                self.status(code if code in allowed else 'failed')
                if self.recovery.state=='connecting':self.recovery.failed(code)
            else:
                result = data['result']
                if result['mode']=='scan' and self.dialog is not None:
                    self.dialog.devices.clear()
                    for device in result['devices']:self.dialog.devices.addItem(device['label'],device)
                    self.status('usb_ready' if self.dialog.devices.count() else 'no_usb')
                elif result['mode']=='discover' and self.dialog is not None:
                    self.dialog.populate(result['devices'])
                    self.status(result['discovery'] if not result['devices'] else 'discovered',count=len(result['devices']))
                elif result['mode']=='diagnose' and self.dialog is not None:
                    self.dialog.diagnostic.setText(tr('qt.wireless.diagnostic',version=result['version'],mdns=tr('qt.wireless.state.'+result['discovery'])))
                    self.dialog.diagnostic.show();self.status('diagnosed')
                elif result.get('paired') and not result.get('connected'):
                    if self.dialog is not None:self.dialog.mark_paired(result)
                    self.status('paired_wait')
                    reason = result.get('reason')
                    if reason in ('mdns_unavailable','port_ambiguous','wireless_connect','device_rejected','device_offline',
                                  'device_unauthorized','wireless_command','device_timeout','device_adb_path') and self.dialog is not None:
                        self.dialog.status.setText(self.dialog.status.text()+'\n'+tr('qt.wireless.state.'+reason))
                else:
                    self.result = result
                    self.recovery.accepted(result)
                    if self.dialog is not None:
                        host,port = result['endpoint'].rsplit(':',1)
                        self.dialog.fields['host'].setText(host);self.dialog.fields['port'].setText(port)
                    self.status('connected',address=result['endpoint'])
                    if self.dialog is not None:self.dialog.read_button.setEnabled(True)
        if self.generation is None and not self.session.tasks.busy():
            self.busy = False;self.timer.stop()
            if self.dialog is not None:
                if self.dialog.status.text()==tr('qt.wireless.state.cancelling'):self.status('cancelled')
                self.dialog.set_busy(False);self.dialog.read_button.setEnabled(self.result is not None)
                self.dialog.run_button.setEnabled(self.result is None)
            else:self.release()
            self.recovery.render()

    def finished(self, _result):
        self.dialog = None
        if self.busy:self.cancel()
        else:self.release()

    def release(self):
        browser = self.window.device_browser;browser.connection_open = False
        if self.closed:return
        if self.result is not None:
            browser.preferred_transport = json.dumps([self.result.get('serial',self.result['endpoint']),self.result['hardware']],separators=(',',':'))
            self.result = None
            # Preserve the verified connection independently of its song snapshot.
            if browser.selected and browser.selected['device_id']==browser.preferred_transport:
                browser.preferred_transport=None;browser.state='choose'
                if browser.read_after_connect:browser.read_after_connect=False;browser.read_selected()
                else:browser.render()
            else:browser.discover()
        else:browser.render()
        self.window.transfers.render()

    def read_connected(self):
        if self.busy or self.result is None or self.dialog is None:return
        self.window.device_browser.read_after_connect = True
        self.dialog.accept()

    def enable_transfers(self):
        if self.transfer_factory is None or self.busy or not self.available():return
        backend,push_backend = self.transfer_factory()
        self.window.transfers.enable_backends(backend,push_backend);self.status('transfers')

    def retranslate(self):
        self.button.setText(tr('qt.wireless.open'));self.button.setToolTip(tr('qt.wireless.intro'))
        if hasattr(self,'recovery'):self.recovery.render()

    def close(self):
        self.closed = True;self.timer.stop()
        self.recovery.close()
        if self.session:self.session.close()
        if self.dialog:self.dialog.reject()

    def wait_closed(self):
        self.recovery.wait_closed()
        if self.session:
            for thread in tuple(self.session.tasks._threads):thread.join()
