"""System-owned rounded outer frames, with an opaque resize-only fallback."""
import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, QRectF
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QDialog, QMainWindow


class WindowAppearance(QObject):
    def __init__(self, app):
        super().__init__(app);self.app=app;self.mode='light';self.border='#d6e4fa';self.native=None
        if sys.platform=='win32':
            from ctypes import wintypes
            try:
                library=ctypes.WinDLL('dwmapi',use_last_error=True)
                function=library.DwmSetWindowAttribute
                function.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
                function.restype=ctypes.c_long;self.native=function
            except OSError:pass
        app.installEventFilter(self)

    def configure(self, mode, border):
        self.mode=mode;self.border=border
        for widget in self.app.topLevelWidgets():
            if isinstance(widget,(QMainWindow,QDialog)) and widget.isVisible():self.apply(widget)

    def apply(self, widget):
        native=False
        if self.native:
            corner=ctypes.c_int(2);hwnd=int(widget.winId())
            result=self.native(hwnd,33,ctypes.byref(corner),ctypes.sizeof(corner))
            widget.setProperty('windowCornerResult',result);native=result==0
            dark=ctypes.c_int(self.mode=='dark');self.native(hwnd,20,ctypes.byref(dark),ctypes.sizeof(dark))
            value=self.border.lstrip('#');color=ctypes.c_uint32(int(value[0:2],16)|(int(value[2:4],16)<<8)|(int(value[4:6],16)<<16))
            self.native(hwnd,34,ctypes.byref(color),ctypes.sizeof(color))
        widget.setProperty('frameRoundFallback',not native)
        if native:widget.clearMask()
        else:self.mask(widget)

    def mask(self, widget):
        if widget.isMaximized() or widget.isFullScreen():widget.clearMask();return
        path=QPainterPath();path.addRoundedRect(QRectF(widget.rect()),12,12)
        widget.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def eventFilter(self, widget, event):
        if event.type() not in (QEvent.Type.Show,QEvent.Type.Resize,QEvent.Type.WindowStateChange):return False
        if isinstance(widget,(QMainWindow,QDialog)) and widget.isWindow():
            if event.type()==QEvent.Type.Show:self.apply(widget)
            elif event.type() in (QEvent.Type.Resize,QEvent.Type.WindowStateChange) and widget.property('frameRoundFallback'):self.mask(widget)
        return False


def install_appearance(app, mode, border):
    if not hasattr(app,'quest_window_appearance'):app.quest_window_appearance=WindowAppearance(app)
    app.quest_window_appearance.configure(mode,border)
