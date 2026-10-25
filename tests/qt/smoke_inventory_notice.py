"""Visible scan status and tooltip remain independent of search feedback."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests/qt')]


def child():
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QWidget
    from quest_tool.messages import tr
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.workers import TaskRunner
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def report(kind,value,tb):
        errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    def wait(predicate):
        deadline=time.monotonic()+5
        while not predicate() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.003)
        assert predicate() and not errors,errors
    class Manager:
        def __init__(self,root):
            self.history=root/'history.json';self.tasks=TaskRunner();self.last_keep_zip=False
        def snapshot(self):return []
        def shutdown(self):pass
    class Session:
        def __init__(self):self.tasks=TaskRunner();self.generation=0
        def cancel(self):pass
        def close(self):pass
        def request(self,*_args):self.generation+=1;return self.generation
    with tempfile.TemporaryDirectory(prefix='quest-inventory-notice-') as directory:
        root=Path(directory).resolve()
        window=DownloadWindow(songs=[])
        assert window.scan_status.isHidden()
        window.enable_downloads(root,Manager(root));window.show()
        assert QTest.qWaitForWindowExposed(window,5000)
        # The unchanged baseline also receives a native first-resize correction
        # at 125%. Finish that startup geometry exchange before measuring the
        # status row; measured widths below must still meet the original bound.
        window.resize(1200,800);QTest.qWait(30)
        window.resize(1300,800);QTest.qWait(30)
        controller=window.download_browser.verification
        controller.cancel();controller.session.close();controller.wait_closed()
        controller.session=Session()
        def begin():
            controller.request();wait(lambda:controller.generation is not None)
        def finish(inventory=None,kind='search',target=None):
            result={'target':str(window.download_browser.target) if target is None else target,'records':{}}
            if inventory is not None:result['inventory']=inventory
            controller.session.tasks.emit(kind,payload={'generation':controller.generation,'result':result})
            controller.poll()
        window.note.setText('SEARCH FEEDBACK SENTINEL')
        begin();assert tr('qt.inventory.badge_busy') in window.scan_status.accessibleName()
        finish(dict(valid=3,checked=4,complete=True,digests=[]))
        assert not controller.failed and '3' in window.scan_status.accessibleName()
        assert controller.checked_at[:4].isdigit() and len(controller.checked_at)>20
        assert controller.checked_at in window.scan_status.toolTip()
        assert str(window.download_browser.target) in window.scan_status.toolTip()
        begin();finish(dict(valid=0,checked=0,complete=True,digests=[]))
        assert '0' in window.scan_status.accessibleName() and not controller.failed
        begin();finish(dict(valid=2,checked=3,complete=False,digests=[]))
        assert tr('qt.inventory.badge_partial',count=2) in window.scan_status.accessibleName()
        begin();finish(dict(valid=2,checked=3,complete=False,digests=[],passes=3,max_passes=3,budget_limited=True))
        assert tr('qt.inventory.passes',count=3,maximum=3) in controller.summary.text()
        assert tr('qt.inventory.continuation') in window.scan_status.toolTip()
        assert tr('qt.inventory.passes',count=3,maximum=3) in window.scan_status.accessibleName()
        begin();finish(dict(valid=0,checked=0,complete=False,error=True,digests=[]))
        assert controller.failed and tr('qt.inventory.badge_failed') in window.scan_status.accessibleName()
        begin();finish(kind='search_error')
        assert controller.failed and tr('qt.inventory.failed') in controller.summary.text()
        begin();controller.cancel()
        assert not controller.failed and tr('qt.inventory.badge_cancelled') in window.scan_status.accessibleName()
        begin();finish(dict(valid=2,checked=3,complete=False,digests=[]))
        assert not controller.failed and not controller.cancelled
        assert window.note.text()=='SEARCH FEEDBACK SENTINEL'
        count=len(window.findChildren(QWidget))
        for language in ('zh','en'):
            window.retranslate(language)
            for theme in ('light','dark'):
                window.change_theme(theme)
                for width in (1000,1500):
                    window.resize(width,800);QTest.qWait(25)
                    label=window.scan_status;tabs=window.tabs
                    assert label.isVisible() and window.width()<=width,(language,theme,width,window.width(),window.minimumSizeHint().width(),label.isVisible())
                    assert label.geometry().left()>=tabs.geometry().right()
                    assert label.geometry().right()<window.centralWidget().width()
                    assert abs(label.mapTo(window,label.rect().center()).y()-tabs.mapTo(window,tabs.rect().center()).y())<=2
                    assert window.pages.mapTo(window,QPoint()).y()>label.mapTo(window,label.rect().bottomLeft()).y()
                    assert label.fontMetrics().horizontalAdvance(label.text())<=label.contentsRect().width()+1
                    assert tr('qt.inventory.badge_partial',count=2) in label.accessibleName()
                    assert len(window.findChildren(QWidget))<=count+2
        shots=os.environ.get('QUEST_QT_SHOTS')
        if shots:
            path=Path(shots);path.mkdir(parents=True,exist_ok=True)
            assert window.grab().save(str(path/('inventory-notice-'+os.environ.get('QT_SCALE_FACTOR','1')+'.png')))
        window.close();window.download_browser.wait_closed();app.processEvents()
        assert controller.closed and not errors and 'miniaudio' not in sys.modules,errors
    print('PASS visible busy/cancel/done/empty/partial/failure, full timestamp/path, search-message isolation, bilingual/themes/widths/row geometry/elision, close')


def main():
    if '--child' in sys.argv:child();return
    for scale in ('1','1.25','1.5'):
        result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
            env=dict(os.environ,QT_SCALE_FACTOR=scale,PYTHONIOENCODING='utf-8'),capture_output=True,timeout=50)
        print(scale,result.stdout.decode('utf-8',errors='replace'),result.stderr.decode('utf-8',errors='replace'),flush=True)
        assert result.returncode==0


if __name__=='__main__':main()
