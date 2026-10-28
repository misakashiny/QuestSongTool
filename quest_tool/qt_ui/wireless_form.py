"""Wireless setup form only. Emits requests; never runs device commands."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLineEdit,
    QPushButton, QScrollArea, QTabBar, QVBoxLayout, QWidget,
)

from ..downloads import DownloadError
from .library_header import LibraryHeader
from .window_chrome import WindowChrome
from .surfaces import surface, action_icons

from ..messages import tr
from ..theme import DESKTOP, SPACE
from .library_help import text_label
from .wireless_validation import validated


class WirelessForm(QDialog):
    requestReady = Signal(dict)
    cancelRequested = Signal()
    contextChanged = Signal()
    forgetRequested = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        super().setObjectName('wirelessManager')
        self.busy = False
        self.paired = None
        super().setWindowTitle(tr('qt.wireless.title'));super().setWindowModality(Qt.WindowModality.WindowModal)
        super().setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        available = parent.screen().availableGeometry()
        super().setMinimumSize(min(DESKTOP.LIBRARY_HELP_MIN_W,available.width()),min(DESKTOP.LIBRARY_HELP_MIN_H,available.height()))
        super().resize(min(DESKTOP.LIBRARY_HELP_W,available.width()),min(DESKTOP.LIBRARY_HELP_H,available.height()))
        super().setSizeGripEnabled(True)
        layout = QVBoxLayout(self);layout.setContentsMargins(SPACE.L,SPACE.L,SPACE.L,SPACE.L)
        title = text_label(tr('qt.wireless.title'),'subtitle');title.setProperty('uiRole','heading');title.setObjectName('deviceManagerTitle')
        self.header=LibraryHeader(self)
        from .icons import line_icon
        from ..theme import get_palette
        from PySide6.QtWidgets import QLabel
        mark=QLabel();mark.setObjectName('questBrandIcon');mark.setFixedSize(40,40);mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(line_icon('settings',get_palette(parent.mode.currentData()).ACCENT_TEXT).pixmap(26,26))
        self.header.identity_layout.addWidget(mark);self.header.identity_layout.addWidget(title)
        title_row=self.header.action_layout;layout.addWidget(self.header)
        self.chrome=WindowChrome(self,self.header)
        self.diagnose = QPushButton(tr('qt.wireless.diagnose'));title_row.addWidget(self.diagnose)
        scroll = QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = surface(QWidget());body = QVBoxLayout(content);body.setSpacing(SPACE.L)
        body.addWidget(text_label(tr('qt.wireless.intro'),'muted'))
        self.mode = QTabBar();self.mode.setExpanding(False);self.mode.setDrawBase(False)
        for mode in ('usb','connect','pair'):
            index = self.mode.addTab(tr('qt.wireless.tab.'+mode));self.mode.setTabData(index,mode)
        self.mode.setAccessibleName(tr('qt.wireless.method'));body.addWidget(self.mode)
        self.hint = text_label('');body.addWidget(self.hint)
        self.fields = {};self.form = QFormLayout()
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setSpacing(SPACE.M);body.addLayout(self.form)
        self.devices = QComboBox();self.scan = QPushButton(tr('qt.wireless.scan'))
        self.usb_row = QWidget();usb = QHBoxLayout(self.usb_row);usb.setContentsMargins(0,0,0,0)
        usb.addWidget(self.devices,1);usb.addWidget(self.scan)
        self.form.addRow(tr('qt.wireless.device'),self.usb_row)
        self.discovered = QComboBox();self.discovered.setMinimumWidth(0)
        self.discovered.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.discovered.setMinimumContentsLength(12);self.discovered.addItem(tr('qt.wireless.pick_discovered'),None)
        self.discover = QPushButton(tr('qt.wireless.discover'))
        self.discovery_row = QWidget();discovery = QHBoxLayout(self.discovery_row);discovery.setContentsMargins(0,0,0,0)
        discovery.addWidget(self.discovered,1);discovery.addWidget(self.discover)
        self.form.addRow(tr('qt.wireless.found'),self.discovery_row)
        self.recent = QComboBox();self.recent.setMinimumContentsLength(12)
        self.recent.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.forget = QPushButton(tr('qt.wireless.forget'))
        self.recent_row = QWidget();recent_layout = QHBoxLayout(self.recent_row);recent_layout.setContentsMargins(0,0,0,0)
        recent_layout.addWidget(self.recent,1);recent_layout.addWidget(self.forget)
        self.form.addRow(tr('qt.wireless.recent'),self.recent_row)
        self.history_note = text_label(tr('qt.wireless.recent_hint'),'muted');body.addWidget(self.history_note)
        for name in ('host','port','pair_port','code'):
            entry = QLineEdit();entry.setMaxLength(15 if name=='host' else 6 if name=='code' else 5)
            entry.setAccessibleName(tr('qt.wireless.'+name));self.fields[name] = entry
            self.form.addRow(tr('qt.wireless.'+name),entry)
        self.fields['code'].setEchoMode(QLineEdit.EchoMode.Password)
        self.fields['port'].setText('5555');self.fields['host'].setPlaceholderText('192.168.1.100')
        self.fields['port'].setPlaceholderText(tr('qt.wireless.auto_port'))
        self.diagnostic = text_label('','muted');body.addWidget(self.diagnostic);self.diagnostic.hide()
        self.status = text_label(tr('qt.wireless.state.ready'));self.status.setObjectName('wirelessStatus');self.status.setProperty('uiRole','note')
        self.read_button = QPushButton(tr('qt.wireless.read_short'));self.read_button.setEnabled(False)
        self.read_button.setProperty('role','primary')
        body.addStretch();scroll.setWidget(content);layout.addWidget(scroll,1)
        buttons = QHBoxLayout();self.run_button = QPushButton();self.run_button.setProperty('role','primary')
        self.cancel_button = QPushButton(tr('qt.wireless.stop_short'));self.close_button = QPushButton(tr('qt.wireless.back_short'))
        action_icons((self.scan,'refresh'),(self.read_button,'folder'),(self.discover,'search'),(self.diagnose,'settings'),
                     (self.run_button,'link'),(self.cancel_button,'stop'),(self.close_button,'back'))
        footer = surface(QFrame(),'footer');footer_layout = QVBoxLayout(footer)
        footer_layout.addWidget(self.status)
        footer_layout.addWidget(self.run_button)
        buttons.addWidget(self.read_button);buttons.addStretch();buttons.addWidget(self.cancel_button);buttons.addWidget(self.close_button)
        footer_layout.addLayout(buttons);layout.addWidget(footer)
        self.mode.currentChanged.connect(self.update_mode)
        self.scan.clicked.connect(lambda:self.requestReady.emit({'mode':'scan'}))
        self.discover.clicked.connect(lambda:self.requestReady.emit({'mode':'discover'}))
        self.diagnose.clicked.connect(lambda:self.requestReady.emit({'mode':'diagnose'}))
        self.discovered.activated.connect(self.choose_discovered)
        self.recent.activated.connect(self.choose_recent)
        self.forget.clicked.connect(self.forget_recent)
        for name,field in self.fields.items():field.textEdited.connect(lambda _text,key=name:self.edited(key))
        self.run_button.clicked.connect(self.submit);self.cancel_button.clicked.connect(self.cancelRequested)
        self.close_button.clicked.connect(self.reject);self.finished.connect(lambda:self.fields['code'].clear())
        self.devices.setAccessibleName(tr('qt.wireless.device'))
        self.devices.currentIndexChanged.connect(lambda _index:self.contextChanged.emit())
        self.update_mode();self.set_busy(False)

    def update_mode(self):
        mode = self.connection_mode();self.paired = None;self.fields['code'].clear()
        if mode=='pair' and self.fields['port'].text()=='5555':self.fields['port'].clear()
        elif mode!='pair' and not self.fields['port'].text():self.fields['port'].setText('5555')
        self.form.setRowVisible(self.usb_row,mode=='usb')
        self.form.setRowVisible(self.discovery_row,mode!='usb')
        self.form.setRowVisible(self.recent_row,mode=='connect')
        for key in ('pair_port','code'):self.form.setRowVisible(self.fields[key],mode=='pair')
        self.form.labelForField(self.fields['port']).setText(tr('qt.wireless.auto_port_label' if mode=='pair' else 'qt.wireless.port'))
        self.hint.setText(tr('qt.wireless.hint.'+mode))
        self.run_button.setText(tr('qt.wireless.reconnect' if self.recent_match() else 'qt.wireless.run.'+mode))
        self.fields['code'].setEnabled(not self.busy);self.contextChanged.emit()

    def connection_mode(self):
        return self.mode.tabData(self.mode.currentIndex())

    def edited(self, key):
        if key in ('host','port'):self.recent.setCurrentIndex(0);self.forget.setEnabled(False)
        if key in ('host','pair_port','code'):
            self.paired = None
            self.fields['code'].setEnabled(not self.busy)
            self.run_button.setText(tr('qt.wireless.run.'+self.connection_mode()))
        self.contextChanged.emit()
        if self.connection_mode()=='connect':self.run_button.setText(tr('qt.wireless.run.connect'))

    def populate_recent(self, rows, preferred=None):
        self.recent.clear();self.recent.addItem(tr('qt.wireless.manual'),None)
        for row in rows:
            self.recent.addItem(f"{row['model']} · {row['host']}:{row['port']}",dict(row))
            self.recent.setItemData(self.recent.count()-1,row['hardware'],Qt.ItemDataRole.ToolTipRole)
        index=next((i for i in range(1,self.recent.count()) if self.recent.itemData(i)['hardware']==preferred),1 if rows else 0)
        self.recent.setCurrentIndex(index)
        self.choose_recent(index)
        self.forget.setEnabled(bool(index) and not self.busy)

    def choose_recent(self, index):
        row=self.recent.itemData(index)
        if self.busy:return
        self.forget.setEnabled(bool(row))
        if row:
            self.mode.setCurrentIndex(1)
            self.fields['host'].setText(row['host']);self.fields['port'].setText(str(row['port']))
        self.run_button.setText(tr('qt.wireless.reconnect' if row else 'qt.wireless.run.'+self.connection_mode()))
        self.contextChanged.emit()

    def recent_match(self):
        row=self.recent.currentData()
        return row if row and self.connection_mode()=='connect' and self.fields['host'].text().strip()==row['host'] and self.fields['port'].text().strip()==str(row['port']) else None

    def forget_recent(self):
        row=self.recent.currentData()
        if row and not self.busy:self.forgetRequested.emit(row['hardware'])

    def mark_paired(self, result):
        self.paired = (result['host'],str(result['pair_port']))
        self.fields['code'].clear();self.fields['code'].setEnabled(False)
        self.run_button.setText(tr('qt.wireless.continue_connect'))

    def populate(self, devices):
        self.discovered.clear();self.discovered.addItem(tr('qt.wireless.pick_discovered'),None)
        for item in devices:
            label = f"{item['host']}:{item['port']} · "+tr('qt.wireless.kind.'+item['kind'])
            self.discovered.addItem(label,item)
            self.discovered.setItemData(self.discovered.count()-1,item['instance'],Qt.ItemDataRole.ToolTipRole)

    def choose_discovered(self, index):
        item = self.discovered.itemData(index)
        if not item or self.busy:return
        previous_host = self.fields['host'].text()
        if item['kind']=='pairing':self.mode.setCurrentIndex(2)
        self.fields['host'].setText(item['host'])
        self.fields['pair_port' if item['kind']=='pairing' else 'port'].setText(str(item['port']))
        self.edited('port' if previous_host==item['host'] and item['kind']!='pairing' else 'host')

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.mode,self.scan,self.devices,self.run_button,self.discover,self.discovered,self.diagnose,self.recent,*self.fields.values()):widget.setEnabled(not busy)
        self.forget.setEnabled(not busy and self.recent.currentData() is not None)
        self.fields['code'].setEnabled(not busy and self.paired is None)
        self.cancel_button.setEnabled(busy)

    def submit(self):
        request = {key:field.text().strip() for key,field in self.fields.items()}
        mode = self.connection_mode()
        if mode=='pair' and self.paired==(request['host'],request['pair_port']):mode='paired_connect'
        request.update(mode=mode,device=self.devices.currentData())
        recent=self.recent_match()
        if recent:request.update(mode='reconnect',hardware=recent['hardware'])
        try:validated(request)
        except DownloadError as exc:self.status.setText(tr('qt.wireless.state.'+exc.code));return
        self.requestReady.emit(request)
        self.fields['code'].clear()

    def done(self, result):
        self.chrome.close();super().done(result)

    def closeEvent(self, event):
        self.chrome.close();super().closeEvent(event)
