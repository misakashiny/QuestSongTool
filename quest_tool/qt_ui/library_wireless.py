"""UI-thread coordinator. All device work is explicitly requested and queued."""
import json

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QPushButton

from ..downloads import DownloadError
from ..messages import tr
from ..song_search import SearchSession
from .wireless_form import WirelessForm
from .wireless_validation import validated


class LibraryWireless(QObject):
    def __init__(self, window, client=None, transfer_factory=None):
        super().__init__(window)
        self.window = window;self.transfer_factory = transfer_factory
        self.session = SearchSession(client) if client is not None else None
        self.dialog = None;self.closed = False;self.generation = None;self.busy = False;self.result = None
        self.button = QPushButton();self.button.setObjectName('wirelessConnect')
        self.button.setVisible(client is not None)
        window.device_browser.page.layout().addWidget(self.button,0,4)
        self.button.clicked.connect(self.show)
        self.timer = QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.poll)
        self.retranslate()

    def available(self):
        transfers = self.window.transfers
        return not (self.closed or self.window.closed or transfers.dialog is not None or any(job.tasks.busy() for job in transfers.jobs))

    def show(self):
        if self.dialog is not None:self.dialog.raise_();self.dialog.activateWindow();return
        if self.session is None:return
        if not self.available() or self.busy:
            self.button.setToolTip(tr('qt.wireless.wait'));return
        self.result = None
        dialog = WirelessForm(self.window);self.dialog = dialog
        dialog.transfer.setEnabled(self.transfer_factory is not None)
        dialog.requestReady.connect(self.start);dialog.cancelRequested.connect(self.cancel)
        dialog.transfersRequested.connect(self.enable_transfers);dialog.finished.connect(self.finished)
        self.window.device_browser.connection_open = True;self.window.device_browser.render()
        self.window.transfers.render();dialog.open()

    def start(self, request):
        if self.dialog is None or self.busy or not self.available():return
        try:validated(request)
        except DownloadError as exc:self.status(exc.code);return
        self.result = None
        if request['mode']!='scan':self.window.device_browser.cancel()
        self.busy = True;self.generation = self.session.request(request,0,'')
        self.dialog.set_busy(True);self.status('scanning' if request['mode']=='scan' else 'working')
        self.timer.start()

    def status(self, key, **values):
        if self.dialog is not None:self.dialog.status.setText(tr('qt.wireless.state.'+key,**values))

    def cancel(self):
        if not self.busy:return
        self.session.cancel();self.generation = None;self.status('cancelling')
        if self.dialog is not None:self.dialog.cancel_button.setEnabled(False)

    def poll(self):
        if self.closed or self.session is None:return
        for message in self.session.tasks.drain():
            data = message.payload
            if not isinstance(data,dict) or self.generation is None or data.get('generation')!=self.generation:continue
            if message.kind not in ('search','search_error'):continue
            self.generation = None
            if message.kind=='search_error':
                code = data.get('code','failed')
                allowed = {'wireless_address','wireless_port','wireless_code','wireless_usb','wireless_pair','wireless_tcpip',
                           'wireless_connect','wireless_command','device_rejected','device_adb_path','device_timeout'}
                self.status(code if code in allowed else 'failed')
            else:
                result = data['result']
                if result['mode']=='scan' and self.dialog is not None:
                    self.dialog.devices.clear()
                    for device in result['devices']:self.dialog.devices.addItem(device['label'],device)
                    self.status('usb_ready' if self.dialog.devices.count() else 'no_usb')
                else:
                    self.result = result
                    if self.dialog is not None:self.dialog.fields['host'].setText(result['endpoint'].rsplit(':',1)[0])
                    self.status('connected',address=result['endpoint'])
        if self.generation is None and not self.session.tasks.busy():
            self.busy = False;self.timer.stop()
            if self.dialog is not None:
                if self.dialog.status.text()==tr('qt.wireless.state.cancelling'):self.status('cancelled')
                self.dialog.set_busy(False);self.dialog.transfer.setEnabled(self.transfer_factory is not None)
            else:self.release()

    def finished(self, _result):
        self.dialog = None
        if self.busy:self.cancel()
        else:self.release()

    def release(self):
        browser = self.window.device_browser;browser.connection_open = False
        if self.closed:return
        if self.result is not None:
            browser.preferred_transport = json.dumps([self.result['endpoint'],self.result['hardware']],separators=(',',':'))
            self.result = None;browser.discover()
        else:browser.render()
        self.window.transfers.render()

    def enable_transfers(self):
        if self.transfer_factory is None or self.busy or not self.available():return
        backend,push_backend = self.transfer_factory()
        self.window.transfers.enable_backends(backend,push_backend);self.status('transfers')

    def retranslate(self):
        self.button.setText(tr('qt.wireless.open'));self.button.setToolTip(tr('qt.wireless.intro'))

    def close(self):
        self.closed = True;self.timer.stop()
        if self.session:self.session.close()
        if self.dialog:self.dialog.reject()

    def wait_closed(self):
        if self.session:
            for thread in tuple(self.session.tasks._threads):thread.join()
