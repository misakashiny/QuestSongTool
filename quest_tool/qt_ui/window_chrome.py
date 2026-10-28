"""Integrated library window controls; system movement rather than repaint loops."""
import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractButton, QComboBox, QHBoxLayout, QLineEdit, QPushButton, QTabBar, QWidget

from ..messages import tr
from .surfaces import action_icons


def resize_hit(x, y, width, height, border=6):
    if not (0<=x<width and 0<=y<height):return 0
    left,right=x<border,x>=width-border;top,bottom=y<border,y>=height-border
    if top:return 13 if left else 14 if right else 12
    if bottom:return 16 if left else 17 if right else 15
    return 10 if left else 11 if right else 0


class WindowChrome(QObject):
    def __init__(self, window, header):
        super().__init__(window);self.window=window;self.header=header;self.native=None;self.closed=False
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint,True)
        self.controls=QWidget();self.controls.setObjectName('windowControls')
        row=QHBoxLayout(self.controls);row.setContentsMargins(0,0,0,0);row.setSpacing(2)
        self.minimize=QPushButton();self.maximize=QPushButton();self.close_button=QPushButton()
        for button in (self.minimize,self.maximize,self.close_button):
            button.setProperty('windowControl',True);button.setFixedSize(36,38);row.addWidget(button)
        self.close_button.setObjectName('windowClose')
        self.minimize.clicked.connect(window.showMinimized);self.maximize.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close);header.add_controls(self.controls)
        for widget in (window,header,header.identity):widget.installEventFilter(self)
        self.shortcut=QShortcut(QKeySequence('Alt+F4'),window);self.shortcut.activated.connect(window.close)
        self.retranslate()
        if sys.platform=='win32':
            from .window_chrome_win import NativeChrome
            self.native=NativeChrome(self)

    def retranslate(self):
        for button,key in ((self.minimize,'minimize'),(self.maximize,'restore' if self.window.isMaximized() else 'maximize'),(self.close_button,'close')):
            text=tr('ui.window.'+key);button.setAccessibleName(text);button.setToolTip(text)
        action_icons((self.minimize,'window_minimize'),(self.maximize,'window_restore' if self.window.isMaximized() else 'window_maximize'),(self.close_button,'close'))

    def toggle_maximized(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def draggable(self, point):
        if not self.header.rect().contains(self.header.mapFrom(self.window,point)):return False
        child=self.window.childAt(point)
        while child is not None and child is not self.header:
            if isinstance(child,(QAbstractButton,QComboBox,QLineEdit,QTabBar)):return False
            child=child.parentWidget()
        return child is self.header

    def hit_test(self, point):
        window=self.window
        if not window.isMaximized() and not window.isFullScreen():
            hit=resize_hit(point.x(),point.y(),window.width(),window.height())
            if hit:return hit
        return 2 if self.draggable(point) else 1

    def eventFilter(self, watched, event):
        kind=event.type()
        if kind==QEvent.Type.WindowStateChange:self.retranslate()
        if kind==QEvent.Type.MouseButtonDblClick and event.button()==Qt.MouseButton.LeftButton:
            point=watched.mapTo(self.window,event.position().toPoint())
            if self.draggable(point):self.toggle_maximized();return True
        if kind==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
            point=watched.mapTo(self.window,event.position().toPoint());handle=self.window.windowHandle()
            if self.draggable(point) and handle:return handle.startSystemMove()
        return False

    def close(self):
        if self.closed:return
        self.closed=True
        self.header.shutdown()
        if self.native:self.native.close()
        for widget in (self.window,self.header,self.header.identity):widget.removeEventFilter(self)

