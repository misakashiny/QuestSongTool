"""Single application header with Windows resize/taskbar behavior preserved."""
from __future__ import annotations

import sys


class WindowChrome:
    def __init__(self, root):
        self.root = root
        self._native = sys.platform == "win32"
        self._restore_geometry = None
        self._move_job = None
        self._move_target = None
        self._paint_job = None
        root.bind("<Map>", self._mapped, add="+")
        root.bind("<Configure>", self._geometry_changed, add="+")
        root.after_idle(self.apply)

    def _mapped(self, event):
        if event.widget is self.root:
            self.root.after_idle(self.apply)

    def _handle(self):
        import ctypes
        return ctypes.windll.user32.GetParent(self.root.winfo_id())

    def apply(self):
        if not self._native or not self.root.winfo_exists():
            return
        import ctypes
        from ctypes import wintypes
        user = ctypes.windll.user32
        handle = self._handle()
        style = user.GetWindowLongW(handle, -16)
        if style & 0x00400000:
            before = wintypes.RECT()
            user.GetClientRect(handle, ctypes.byref(before))
            # Remove WS_DLGFRAME (caption) but retain WS_BORDER: Tk accounts for
            # that one-pixel border when applying subsequent geometry requests.
            # Keep resizing, system menu, min/max and the normal taskbar owner.
            user.SetWindowLongW(handle, -16, (style & ~0x00400000) | 0x02000000)
            user.SetWindowPos(handle, 0, 0, 0, 0, 0, 0x0027 | 0x0010)
            after, frame = wintypes.RECT(), wintypes.RECT()
            user.GetClientRect(handle, ctypes.byref(after))
            user.GetWindowRect(handle, ctypes.byref(frame))
            # Caption removal changes the non-client inset. Preserve the requested
            # client area instead of silently adding two pixels to every restore.
            if before.right > 0 and before.bottom > 0:
                user.SetWindowPos(handle, 0, 0, 0,
                                  frame.right - frame.left + before.right - after.right,
                                  frame.bottom - frame.top + before.bottom - after.bottom,
                                  0x0016)

    def attach_drag(self, widget):
        widget.bind("<Button-1>", self._drag, add="+")
        widget.bind("<B1-Motion>", self._move, add="+")
        widget.bind("<ButtonRelease-1>", self._release, add="+")
        widget.bind("<Double-1>", lambda _event: self.maximize(), add="+")

    def _drag(self, event):
        # Keep moving inside Tk's event loop. A synchronous SendMessageW starts
        # a nested native move loop through the ctypes drop callback; on Python
        # 3.13 this reproduced PyEval_RestoreThread and a fatal process exit.
        self._drag_origin = (event.x_root, event.y_root,
                             self.root.winfo_x(), self.root.winfo_y())

    def _move(self, event):
        origin = getattr(self, "_drag_origin", None)
        if origin is None or self.root.state() == "zoomed":
            return
        x, y, left, top = origin
        self._move_target = (left + event.x_root - x, top + event.y_root - y)
        if self._move_job is None:
            self._move_job = self.root.after(16, self._flush_move)

    def _flush_move(self):
        self._move_job = None
        if self._move_target is not None:
            x, y = self._move_target
            self._move_target = None
            self.root.geometry(f"{x:+d}{y:+d}")

    def _release(self, _event=None):
        if self._move_job is not None:
            self.root.after_cancel(self._move_job)
        self._flush_move()

    def _geometry_changed(self, event):
        if event.widget is not self.root:
            return
        if self._paint_job is not None:
            self.root.after_cancel(self._paint_job)
        self._paint_job = self.root.after(80, self.repaint)

    def repaint(self):
        self._paint_job = None
        if self._native and self.root.winfo_exists():
            import ctypes
            # Invalidate parent + child surfaces after geometry settles. Do not
            # withdraw/remap windows or enter a synchronous native move loop.
            ctypes.windll.user32.RedrawWindow(self._handle(), None, None, 0x0085)

    def minimize(self):
        self.root.iconify()

    def maximize(self):
        if self.root.state() == "zoomed":
            self.root.state("normal")
        else:
            self.root.state("zoomed")
        self.root.after_idle(self.apply)
