"""Real F1/modal/close behavior; help cannot execute song or device operations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QDialog,QFileDialog
    from quest_tool.messages import tr
    from quest_tool.qt_ui.download_import import LocalClient
    from quest_tool.qt_ui.library_help import HelpDialog
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.song_search import SearchSession
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def report(kind,value,tb):
        errors.append(str(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    class Client(LocalClient):
        def __init__(self):self.calls=0
        def search(self,target,page,order,cancel):
            self.calls+=1;return super().search(target,page,order,cancel)
    def wait(predicate):
        end=time.monotonic()+10
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError('help timeout')
    def focus(window):
        window.raise_();window.activateWindow();wait(lambda:QApplication.activeWindow() is window)
        window.search.setFocus();wait(lambda:QApplication.focusWidget() is window.search)
    browses=[];original=QFileDialog.getExistingDirectory
    QFileDialog.getExistingDirectory=lambda *_args,**_kwargs:browses.append(True) or ''
    try:
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);songs=base/'songs';song=songs/'demo';song.mkdir(parents=True)
            (song/'Info.dat').write_text(json.dumps(dict(_version='2.0.0',_songName='Blue Horizon',_songDuration=193)),encoding='utf-8')
            for language in ('zh','en'):
                for mode in ('light','dark'):
                    client=Client();window=LibraryWindow(base/(language+mode),language,mode,session=SearchSession(client));window.show()
                    assert QTest.qWaitForWindowExposed(window,5000)
                    try:
                        window.set_target(str(songs));wait(lambda:window.local_ready)
                        window.model.checked.add('demo');window.search.setText('Blue')
                        count=client.calls;checks=set(window.model.checked);focus(window)
                        QTest.keyClick(window.search,Qt.Key.Key_F1)
                        wait(lambda:window.help.dialog is not None and QApplication.activeModalWidget() is window.help.dialog)
                        dialog=window.help.dialog;assert dialog.windowTitle()==tr('qt.help.title')
                        assert dialog.parentWidget() is window and dialog.windowModality()==Qt.WindowModality.WindowModal
                        assert len(dialog.rows)==12
                        for _sequence,key,shortcut,description in dialog.rows:
                            assert description.text()==tr(key) and description.textFormat()==Qt.TextFormat.PlainText
                            assert shortcut.textFormat()==Qt.TextFormat.PlainText
                        assert 'Ctrl+A' in dialog.notes[0].text()
                        for _ in range(3):window.help.show()
                        assert window.help.dialog is dialog and len(window.findChildren(HelpDialog))==1
                        # Underlying shortcuts must not escape the modal help.
                        for key,modifier in ((Qt.Key.Key_F5,Qt.KeyboardModifier.NoModifier),
                                             (Qt.Key.Key_O,Qt.KeyboardModifier.ControlModifier),
                                             (Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier),
                                             (Qt.Key.Key_D,Qt.KeyboardModifier.ControlModifier)):
                            QTest.keyClick(dialog,key,modifier)
                        QTest.qWait(40)
                        assert client.calls==count and not browses
                        assert window.model.checked==checks and window.search.text()=='Blue' and window.details.page.isVisible()
                        for width,height in ((360,320),(640,620)):
                            available=dialog.screen().availableGeometry()
                            dialog.resize(min(width,available.width()-48),min(height,available.height()-48));QTest.qWait(50)
                            assert dialog.scroll.horizontalScrollBar().maximum()==0
                            assert dialog.close_button.isVisible() and dialog.close_button.mapTo(dialog,dialog.close_button.rect().bottomRight()).y()<dialog.height()
                            for _sequence,_key,shortcut,description in dialog.rows:
                                assert description.width()>0 and description.height()>0
                            dialog.scroll.ensureWidgetVisible(dialog.notes[-1]);QTest.qWait(20)
                            assert dialog.notes[-1].visibleRegion().boundingRect().height()>0
                        dialog.scroll.verticalScrollBar().setValue(0);QTest.qWait(40)
                        if os.environ.get('QUEST_QT_SHOTS'):
                            output=Path(os.environ['QUEST_QT_SHOTS']);output.mkdir(parents=True,exist_ok=True)
                            assert dialog.grab().save(str(output/f'help-{language}-{mode}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                        QTest.keyClick(dialog,Qt.Key.Key_Escape);wait(lambda:window.help.dialog is None)
                        QTest.qWait(30);assert not window.findChildren(HelpDialog)
                        QTest.mouseClick(window.help_button,Qt.MouseButton.LeftButton)
                        wait(lambda:window.help.dialog is not None)
                        QTest.mouseClick(window.help.dialog.close_button,Qt.MouseButton.LeftButton);wait(lambda:window.help.dialog is None)
                        # Another modal owner is not replaced or nested by help.
                        other=QDialog(window);other.open();wait(lambda:QApplication.activeModalWidget() is other)
                        window.help.show();assert window.help.dialog is None
                        other.reject();QTest.qWait(30)
                        focus(window);QTest.keyClick(window.search,Qt.Key.Key_F1);wait(lambda:window.help.dialog is not None)
                        assert 'F1' in window.help_button.toolTip() and 'F5' in window.reload.toolTip()
                        assert tr('qt.help.select_button')==window.select_visible.toolTip()
                        window.close();window.wait_closed();assert window.help.closed and window.help.dialog is None
                        window.help.show();assert window.help.dialog is None
                    finally:window.close();window.wait_closed()
    finally:QFileDialog.getExistingDirectory=original
    assert not errors,errors
    print('PASS help: bilingual/themes/scales, real F1/button, singleton modal guards, scroll/fixed close, Esc/reopen/parent close, no state changes')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
