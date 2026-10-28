"""Windows non-client hit tests and work-area bounds for one owned Qt window."""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication


class MonitorInfo(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('monitor',wintypes.RECT),('work',wintypes.RECT),('flags',wintypes.DWORD)]


class MinMaxInfo(ctypes.Structure):
    _fields_=[('reserved',wintypes.POINT),('max_size',wintypes.POINT),('max_position',wintypes.POINT),
              ('min_track',wintypes.POINT),('max_track',wintypes.POINT)]


class NativeChrome(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__();self.owner=owner;self.closed=False
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
        self.user.GetWindowRect.restype=wintypes.BOOL
        self.user.ScreenToClient.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)];self.user.ScreenToClient.restype=wintypes.BOOL
        self.user.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD];self.user.MonitorFromWindow.restype=wintypes.HMONITOR
        self.user.GetMonitorInfoW.argtypes=[wintypes.HMONITOR,ctypes.POINTER(MonitorInfo)];self.user.GetMonitorInfoW.restype=wintypes.BOOL
        self.user.IsZoomed.argtypes=[wintypes.HWND];self.user.IsZoomed.restype=wintypes.BOOL
        self.get_style=self.user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else self.user.GetWindowLongW
        self.set_style=self.user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else self.user.SetWindowLongW
        self.get_style.argtypes=[wintypes.HWND,ctypes.c_int];self.get_style.restype=ctypes.c_ssize_t
        self.set_style.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t];self.set_style.restype=ctypes.c_ssize_t
        self.user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
        self.user.SetWindowPos.restype=wintypes.BOOL
        self.hwnd=int(owner.window.winId())
        QApplication.instance().installNativeEventFilter(self)
        # Keep native sizing/system-menu capabilities; WM_NCCALCSIZE removes
        # their visible title strip. No translucent backing or shadow effect.
        self.set_style(self.hwnd,-16,self.get_style(self.hwnd,-16)|0x00CF0000)
        self.user.SetWindowPos(self.hwnd,None,0,0,0,0,0x0027)

    def nativeEventFilter(self, event_type, message):
        if self.closed or bytes(event_type) not in (b'windows_generic_MSG',b'windows_dispatcher_MSG'):return False,0
        msg=wintypes.MSG.from_address(int(message))
        if msg.hWnd!=self.hwnd:return False,0
        if msg.message==0x0083:  # WM_NCCALCSIZE: clip the maximized client to the work area.
            if msg.lParam and self.user.IsZoomed(self.hwnd):
                monitor=self.user.MonitorFromWindow(self.hwnd,2);info=MonitorInfo();info.size=ctypes.sizeof(info)
                if monitor and self.user.GetMonitorInfoW(monitor,ctypes.byref(info)):
                    rect=wintypes.RECT.from_address(msg.lParam)
                    rect.left=max(rect.left,info.work.left);rect.top=max(rect.top,info.work.top)
                    rect.right=min(rect.right,info.work.right);rect.bottom=min(rect.bottom,info.work.bottom)
            return True,0
        if msg.message==0x0084:  # WM_NCHITTEST uses signed physical screen coordinates.
            x=ctypes.c_short(msg.lParam & 0xffff).value;y=ctypes.c_short((msg.lParam>>16)&0xffff).value
            point=wintypes.POINT(x,y)
            if not self.user.ScreenToClient(self.hwnd,ctypes.byref(point)):return False,0
            scale=self.owner.window.devicePixelRatioF()
            from PySide6.QtCore import QPoint
            return True,self.owner.hit_test(QPoint(round(point.x/scale),round(point.y/scale)))
        if msg.message==0x0024 and msg.lParam:  # WM_GETMINMAXINFO
            monitor=self.user.MonitorFromWindow(self.hwnd,2);info=MonitorInfo();info.size=ctypes.sizeof(info)
            if monitor and self.user.GetMonitorInfoW(monitor,ctypes.byref(info)):
                limits=MinMaxInfo.from_address(msg.lParam);work=info.work;full=info.monitor
                limits.max_position.x=work.left-full.left;limits.max_position.y=work.top-full.top
                limits.max_size.x=work.right-work.left;limits.max_size.y=work.bottom-work.top
                scale=self.owner.window.devicePixelRatioF();minimum=self.owner.window.minimumSize()
                limits.min_track.x=round(minimum.width()*scale);limits.min_track.y=round(minimum.height()*scale)
                return True,0
        return False,0

    def close(self):
        if self.closed:return
        self.closed=True;QApplication.instance().removeNativeEventFilter(self)
