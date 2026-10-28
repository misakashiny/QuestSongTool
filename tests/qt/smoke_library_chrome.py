"""Integrated title controls, native hit tests and annotated library layout."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QWidget
    from quest_tool.messages import tr
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.window_chrome import resize_hit
    from quest_tool.qt_ui.window_chrome_win import MinMaxInfo

    app=QApplication([]);app.setStyle('Fusion');errors=[]
    sys.excepthook=lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    def wait(predicate):
        end=time.monotonic()+8
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError(('timeout',errors))
    def inside(widget,parent):
        point=widget.mapTo(parent,QPoint())
        assert point.x()>=0 and point.y()>=0,(widget.objectName(),point)
        assert point.x()+widget.width()<=parent.width()+1,(widget.objectName(),widget.geometry(),parent.size())
        assert point.y()+widget.height()<=parent.height()+1,(widget.objectName(),widget.geometry(),parent.size())
    points=((0,0,13),(199,0,14),(0,99,16),(199,99,17),(50,0,12),(50,99,15),(0,50,10),(199,50,11),(50,50,0),(-1,20,0))
    for x,y,expected in points:assert resize_hit(x,y,200,100)==expected
    device=dict(kind='adb',device_id='fixture',root='/sdcard/CustomLevels',label='Quest 3 · Layout fixture',
        state='device',model='Quest 3',codename='eureka',brand='Meta',identity_verified=True)
    class Provider:
        def __init__(self):self.calls=[]
        def list_devices(self,cancel):self.calls.append('list');return [dict(device)]
        def read_library(self,selected,cancel):
            self.calls.append('read')
            return dict(device=dict(device),connected=True,rows=[dict(folder='sample',title='星海回声 · Starlight',author='Studio Demo',duration=193,error='')])
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM];user.SendMessageW.restype=ctypes.c_ssize_t
    user.ClientToScreen.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)];user.ClientToScreen.restype=wintypes.BOOL
    with tempfile.TemporaryDirectory(prefix='library-chrome-') as temp,patch('socket.socket',side_effect=AssertionError('network forbidden')),patch('subprocess.Popen',side_effect=AssertionError('process forbidden')):
        base=Path(temp)
        for language in ('zh','en'):
            for theme in ('light','dark'):
                provider=Provider();window=LibraryWindow(base/(language+theme),language,theme,device_provider=provider,
                    push_backend=SimpleNamespace(journal_dir=base/'journals'))
                window.show();assert QTest.qWaitForWindowExposed(window,5000)
                window.setWindowTitle('Quest Layout Test '+language+' '+theme)
                print('STEP window '+language+' '+theme+' PID '+str(os.getpid()),flush=True)
                try:
                    chrome=window.chrome;hwnd=chrome.native.hwnd;browser=window.device_browser
                    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
                    assert not provider.calls
                    for width,height in ((1000,640),(1400,850),(1800,1000)):
                        area=app.primaryScreen().availableGeometry()
                        window.resize(min(width,area.width()),min(height,area.height()));QTest.qWait(100)
                        assert window.width()<=width and window.height()<=height,(language,theme,width,height,window.size(),window.minimumSize(),window.header.wrapped,window.header.minimumSizeHint())
                        for button in (chrome.minimize,chrome.maximize,chrome.close_button,window.transfers.push,window.transfers.push_missing,window.transfers.history):inside(button,window.header)
                        assert window.header.geometry().bottom()<window.connection_panel.geometry().top()
                        assert window.library_split.geometry().bottom()<window.preview.page.geometry().top()
                        tops=[table.mapTo(window,QPoint()).y() for table in (window.table,window.quest_table,window.missing_table)]
                        assert max(tops)-min(tops)<=1,tops
                        assert all(table.viewport().height()>=120 for table in (window.table,window.quest_table,window.missing_table))
                        for table in (window.table,window.quest_table,window.missing_table):
                            assert abs(table.columnViewportPosition(3)+table.columnWidth(3)-table.viewport().width())<=2
                        assert browser.status.parentWidget() is window.statusBar()
                        assert browser.page.layout().rowCount()==1
                        assert not window.transfers.push_hint.isVisible()
                        assert '\n' not in window.local_summary.text()
                        if width==1000:assert window.header.wrapped
                        if window.width()>=1700:assert not window.header.wrapped
                    # Interactive controls remain client hit areas, never start a drag.
                    print('STEP hit tests',flush=True)
                    def native_hit(point):
                        scale=window.devicePixelRatioF();physical=wintypes.POINT(round(point.x()*scale),round(point.y()*scale))
                        assert user.ClientToScreen(hwnd,ctypes.byref(physical));x,y=physical.x,physical.y
                        packed=(x & 0xffff)|((y & 0xffff)<<16)
                        return user.SendMessageW(hwnd,0x84,0,packed)
                    for point,expected in ((QPoint(2,2),13),(QPoint(window.width()-3,2),14),
                        (QPoint(2,window.height()-3),16),(QPoint(window.width()-3,window.height()-3),17)):
                        assert native_hit(point)==expected,(point,native_hit(point))
                    caption=window.brand_icon.mapTo(window,window.brand_icon.rect().center())
                    assert native_hit(caption)==2
                    assert native_hit(window.download.mapTo(window,window.download.rect().center()))==1
                    max_point=chrome.maximize.mapTo(window,chrome.maximize.rect().center())
                    assert native_hit(max_point)==1
                    # Real HWND bounds, with window buttons using ordinary Qt actions.
                    bounds=MinMaxInfo();user.SendMessageW(hwnd,0x24,0,ctypes.addressof(bounds))
                    assert bounds.max_size.x>0 and bounds.max_size.y>0
                    print('STEP maximize',flush=True)
                    chrome.maximize.click()
                    wait(window.isMaximized)
                    assert chrome.maximize.toolTip()==tr('ui.window.restore')
                    area=window.screen().availableGeometry()
                    # A maximized Windows sizing frame may extend outside the
                    # monitor. The visible client, including footer, must fit.
                    assert area.adjusted(-1,-1,1,1).contains(window.geometry()),(area,window.geometry(),window.frameGeometry())
                    for button in (chrome.close_button,chrome.minimize,chrome.maximize,window.download):
                        assert native_hit(button.mapTo(window,button.rect().topLeft()+QPoint(2,2)))==1
                    chrome.maximize.click();wait(lambda:not window.isMaximized())
                    print('STEP caption double click',flush=True)
                    QTest.mouseDClick(window.header.identity,Qt.MouseButton.LeftButton,pos=window.brand_icon.rect().center())
                    wait(window.isMaximized);chrome.toggle_maximized();wait(lambda:not window.isMaximized())
                    print('STEP minimize',flush=True)
                    chrome.minimize.click();wait(window.isMinimized);window.showNormal();wait(lambda:window.isVisible() and not window.isMinimized())
                    # Child windows retain normal title bars; close affects only this window.
                    dialog=QDialog(window);dialog.show();QTest.qWait(40)
                    assert not dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
                    dialog.close()
                    print('STEP device read',flush=True)
                    browser.discover();wait(lambda:not browser.busy);browser.choices.setCurrentIndex(1)
                    browser.read_selected();wait(lambda:window.quest_ready)
                    assert provider.calls==['list','read']
                    widgets=len(window.findChildren(QWidget))
                    for number in range(12):window.resize(1010+number*45,760);QTest.qWait(10)
                    assert len(window.findChildren(QWidget))<=widgets+2
                    if os.environ.get('QUEST_QT_SHOTS'):
                        window.resize(1800,1000);QTest.qWait(100)
                        window.grab().save(str(Path(os.environ['QUEST_QT_SHOTS'])/f'chrome-{language}-{theme}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                    chrome.close_button.click();wait(lambda:window.closed)
                    assert chrome.closed and chrome.native.closed and not (base/'journals').exists()
                    assert not errors,errors
                finally:window.close();window.wait_closed()
    print('PASS library chrome: reference layout, compact/wide, native edge/caption/max hit tests, work-area maximize, double-click, minimize/restore, child dialogs, close and bounded widgets')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True)
            assert result.returncode==0
