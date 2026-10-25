"""Real scan/filter/snapshot transitions; read-only empty hints never own input."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import QRect,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QWidget
    from quest_tool.qt_ui.download_import import LocalClient
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.song_search import SearchSession
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def report(kind,value,tb):
        errors.append(str(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    class Client(LocalClient):
        def __init__(self):self.hold=False;self.fail=False;self.entered=threading.Event()
        def search(self,target,page,order,cancel):
            self.entered.set()
            while self.hold:
                if cancel.wait(.01):raise RuntimeError('controlled cancel')
            if self.fail:raise OSError('controlled scan failure')
            return super().search(target,page,order,cancel)
    def wait(predicate):
        end=time.monotonic()+10
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError('empty-state timeout')
    def state(window,view,key):
        label=window.empty_states.labels[view]
        wait(lambda:label.property('state')==key and not label.isHidden()
            and not window.empty_states.timer.isActive())
        assert label.textFormat()==Qt.TextFormat.PlainText
        assert label.focusPolicy()==Qt.FocusPolicy.NoFocus
        assert label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert view.viewport().rect().contains(label.geometry())
        height=label.fontMetrics().boundingRect(QRect(0,0,label.width(),10000),int(Qt.TextFlag.TextWordWrap),label.text()).height()
        assert height<=label.height(),(key,label.text(),label.size(),height)
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp);songs=base/'songs';song=songs/'first';song.mkdir(parents=True)
        (song/'Info.dat').write_text(json.dumps(dict(_version='2.0.0',_songName='Blue Horizon',_songAuthorName='Artist',_songDuration=193)),encoding='utf-8')
        empty=base/'empty';empty.mkdir()
        for language in ('zh','en'):
            for mode in ('light','dark'):
                client=Client();window=LibraryWindow(base/(language+mode),language,mode,session=SearchSession(client))
                # Global hit testing requires an unobscured test window. Qt can
                # report activeWindow while another process covers the point.
                window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,True);window.show()
                assert QTest.qWaitForWindowExposed(window,5000)
                try:
                    state(window,window.table,'qt.empty.local');state(window,window.quest_table,'qt.empty.quest')
                    state(window,window.missing_table,'qt.empty.both')
                    assert len(window.empty_states.labels)==4
                    window.raise_();window.activateWindow();QTest.qWait(30)
                    point=window.table.viewport().mapToGlobal(window.table.viewport().rect().center())
                    wait(lambda:QApplication.topLevelAt(point) is window)
                    assert QApplication.widgetAt(point) is window.table.viewport(),'Hint intercepted mouse hit testing'
                    children=len(window.findChildren(QWidget))
                    client.hold=True;window.set_target(str(songs));wait(client.entered.is_set)
                    state(window,window.table,'qt.local.loading')
                    client.fail=True;client.hold=False;wait(lambda:window.failed)
                    state(window,window.table,'qt.empty.failed')
                    client.fail=False;window.refresh();wait(lambda:window.local_ready)
                    assert window.proxy.rowCount()==1 and window.empty_states.labels[window.table].isHidden()
                    window.search.setText('no such song');state(window,window.table,'qt.empty.filtered')
                    window.search.clear();assert window.empty_states.labels[window.table].isHidden()
                    snapshot=[dict(row) for row in window.model.rows]
                    window.set_quest_snapshot(snapshot,'fixture');state(window,window.missing_table,'qt.empty.complete')
                    window.set_quest_snapshot(snapshot,'fixture',skipped=1);state(window,window.missing_table,'qt.empty.partial')
                    assert not window.missing_side.isEnabled() and window.missing_model.rowCount()==0
                    window.clear_quest_snapshot();state(window,window.quest_table,'qt.empty.quest')
                    state(window,window.missing_table,'qt.empty.both')
                    window.set_target(str(empty));wait(lambda:window.local_ready)
                    state(window,window.table,'qt.empty.none')
                    window.view_mode.setCurrentIndex(window.view_mode.findData('covers'));QTest.qWait(40)
                    state(window,window.grid,'qt.empty.none')
                    assert window.empty_states.labels[window.grid].isVisible() and not window.empty_states.labels[window.table].isVisible()
                    window.set_target(str(songs));wait(lambda:window.local_ready)
                    window.set_quest_snapshot([],'valid-empty');state(window,window.quest_table,'qt.empty.none')
                    assert window.missing_model.rowCount()==1 and window.empty_states.labels[window.missing_table].isHidden()
                    window.search.setText('no such song');state(window,window.grid,'qt.empty.filtered')
                    state(window,window.missing_table,'qt.empty.filtered')
                    for width,height in ((1000,640),(1400,850),(1100,700))*4:
                        available=window.screen().availableGeometry()
                        window.resize(min(width,available.width()-32),min(height,available.height()-32));QTest.qWait(30)
                        state(window,window.grid,'qt.empty.filtered')
                        state(window,window.missing_table,'qt.empty.filtered')
                    assert len(window.findChildren(QWidget))<=children+2
                    if os.environ.get('QUEST_QT_SHOTS'):
                        output=Path(os.environ['QUEST_QT_SHOTS']);output.mkdir(parents=True,exist_ok=True)
                        assert window.grab().save(str(output/f'empty-{language}-{mode}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                    window.close();window.wait_closed();assert not window.empty_states.timer.isActive()
                    QTest.qWait(30)
                finally:
                    client.hold=False;window.close();window.wait_closed()
    assert not errors,errors
    print('PASS empty hints: untranslated states/scan failure/filter/empty vs unread/incomplete gate, real mouse passthrough, stable widgets, resize, close')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
