"""Wireless setup form only. Emits requests; never runs device commands."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..downloads import DownloadError
from ..messages import tr
from ..theme import DESKTOP, SPACE
from .library_help import text_label
from .wireless_validation import validated


class WirelessForm(QDialog):
    requestReady = Signal(dict)
    cancelRequested = Signal()
    transfersRequested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.busy = False
        super().setWindowTitle(tr('qt.wireless.title'));super().setWindowModality(Qt.WindowModality.WindowModal)
        super().setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        available = parent.screen().availableGeometry()
        super().setMinimumSize(min(DESKTOP.LIBRARY_HELP_MIN_W,available.width()),min(DESKTOP.LIBRARY_HELP_MIN_H,available.height()))
        super().resize(min(DESKTOP.LIBRARY_HELP_W,available.width()),min(DESKTOP.LIBRARY_HELP_H,available.height()))
        super().setSizeGripEnabled(True)
        layout = QVBoxLayout(self);layout.setContentsMargins(SPACE.L,SPACE.L,SPACE.L,SPACE.L)
        layout.addWidget(text_label(tr('qt.wireless.title'),'subtitle'))
        scroll = QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget();body = QVBoxLayout(content);body.setSpacing(SPACE.M)
        body.addWidget(text_label(tr('qt.wireless.intro'),'muted'))
        self.mode = QComboBox()
        for mode in ('usb','connect','pair'):self.mode.addItem(tr('qt.wireless.mode.'+mode),mode)
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
        for name in ('host','port','pair_port','code'):
            entry = QLineEdit();entry.setMaxLength(15 if name=='host' else 6 if name=='code' else 5)
            entry.setAccessibleName(tr('qt.wireless.'+name));self.fields[name] = entry
            self.form.addRow(tr('qt.wireless.'+name),entry)
        self.fields['code'].setEchoMode(QLineEdit.EchoMode.Password)
        self.fields['port'].setText('5555');self.fields['host'].setPlaceholderText('192.168.1.100')
        self.status = text_label(tr('qt.wireless.intro'));self.status.setObjectName('wirelessStatus');body.addWidget(self.status)
        self.transfer = QPushButton(tr('qt.wireless.transfers'))
        self.transfer.clicked.connect(self.transfersRequested);body.addWidget(self.transfer)
        body.addWidget(text_label(tr('qt.wireless.transfer_hint'),'muted'))
        body.addStretch();scroll.setWidget(content);layout.addWidget(scroll,1)
        buttons = QHBoxLayout();self.run_button = QPushButton();self.run_button.setProperty('role','primary')
        self.cancel_button = QPushButton(tr('qt.wireless.cancel'));self.close_button = QPushButton(tr('qt.wireless.close'))
        layout.addWidget(self.run_button)
        buttons.addStretch();buttons.addWidget(self.cancel_button);buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.scan.clicked.connect(lambda:self.requestReady.emit({'mode':'scan'}))
        self.run_button.clicked.connect(self.submit);self.cancel_button.clicked.connect(self.cancelRequested)
        self.close_button.clicked.connect(self.reject);self.finished.connect(lambda:self.fields['code'].clear())
        self.devices.setAccessibleName(tr('qt.wireless.device'))
        self.update_mode();self.set_busy(False)

    def update_mode(self):
        mode = self.mode.currentData()
        self.form.setRowVisible(self.usb_row,mode=='usb')
        for key in ('pair_port','code'):self.form.setRowVisible(self.fields[key],mode=='pair')
        self.hint.setText(tr('qt.wireless.hint.'+mode))
        self.run_button.setText(tr('qt.wireless.run.'+mode))

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.mode,self.scan,self.devices,self.run_button,self.transfer,*self.fields.values()):widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def submit(self):
        request = {key:field.text().strip() for key,field in self.fields.items()}
        request.update(mode=self.mode.currentData(),device=self.devices.currentData())
        try:validated(request)
        except DownloadError as exc:self.status.setText(tr('qt.wireless.state.'+exc.code));return
        self.requestReady.emit(request)
        self.fields['code'].clear()
