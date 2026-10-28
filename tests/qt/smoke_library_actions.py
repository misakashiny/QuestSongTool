"""M6C2 actual key/focus/menu routing with intercepted external side effects."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout, QWidget
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.messages import tr
    app = QApplication([]);app.setStyle('Fusion');errors = []
    def report(kind,value,tb):errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook = report
    def wait(test,seconds=10):
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if test():return
        raise AssertionError('M6C2 timeout')
    def focus(widget):
        # Key routing intentionally requires a real active window. A visible
        # widget alone is insufficient after resize, dialogs or native commands.
        owner=QWidget.window(widget);owner.raise_();owner.activateWindow()
        wait(lambda:QApplication.activeWindow() is owner)
        widget.setFocus();wait(lambda:QApplication.focusWidget() is widget)
    def key(widget,code,modifiers=Qt.KeyboardModifier.NoModifier):
        focus(widget);QTest.keyClick(widget,code,modifiers);app.processEvents()
    def menu(window,view,row=0):
        # A synthetic context-menu event does not activate its native window.
        # Establish the same precondition as an actual right click, retaining
        # the product's active-window and modal guards.
        focus(view)
        assert window.actions.active()
        view.scrollTo(view.model().index(row,0));app.processEvents()
        point = view.visualRect(view.model().index(row,0)).center()
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse,point,view.viewport().mapToGlobal(point))
        QApplication.sendEvent(view.viewport(),event)
        wait(lambda:window.actions.menu is not None)
        return window.actions.menu
    ctrl = Qt.KeyboardModifier.ControlModifier
    both = ctrl | Qt.KeyboardModifier.ShiftModifier
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder).resolve();first = base/'中文曲库';second = base/'other'
        for root,count in ((first,55),(second,1)):
            root.mkdir()
            for i in range(count):
                p = root/f's{i:02}';p.mkdir()
                (p/'Info.dat').write_text(json.dumps({'_version':'2.0.0','_songName':f'Title {i:02}',
                    '_songAuthorName':'Artist','_beatsPerMinute':120,'_songDuration':60}),encoding='utf-8')
        before = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in (first,second) for p in root.rglob('*') if p.is_file()}
        for language in ('zh','en'):
            w = LibraryWindow(base/('sandbox-'+language),language);copied = [];opened = []
            w.actions.clipboard_write = copied.append
            w.actions.open_directory = lambda path: opened.append(path) or True
            w.show();w.raise_();w.activateWindow();wait(lambda:QApplication.activeWindow() is w)
            try:
                assert w.set_target(str(first));wait(lambda:w.local_ready)
                w.resize(1100,700);app.processEvents()
                assert not copied and not opened
                key(w.table,Qt.Key.Key_A,ctrl)
                visible = {w.proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder'] for i in range(w.proxy.rowCount())
                    if w.table.visualRect(w.proxy.index(i,0)).intersects(w.table.viewport().rect())}
                assert w.model.checked==visible and 0<len(visible)<55
                key(w.table,Qt.Key.Key_C,ctrl);assert set(copied[-1].splitlines())==visible
                key(w.table,Qt.Key.Key_Escape);assert not w.model.checked
                w.search.setText('Title 0');key(w.table,Qt.Key.Key_A,both)
                assert w.model.checked=={f's{i:02}' for i in range(10)}
                w.search.setText('Title 1');focus(w.search)
                QTest.keyClick(w.search,Qt.Key.Key_A,ctrl)
                assert w.search.selectedText()=='Title 1' and len(w.model.checked)==10
                key(w.search,Qt.Key.Key_Escape);assert not w.search.text() and len(w.model.checked)==10
                # Dialog/editor focus must not trigger list checks, direction or detail commands.
                dialog = QDialog(w);dialog.setModal(True);box = QVBoxLayout(dialog);edit = QLineEdit('draft');box.addWidget(edit)
                dialog.show();wait(lambda:QApplication.activeModalWidget() is dialog)
                key(edit,Qt.Key.Key_A,ctrl);assert edit.selectedText()=='draft' and len(w.model.checked)==10
                QTest.keyClick(edit,Qt.Key.Key_D,ctrl);app.processEvents();assert w.details.page.isVisible()
                QTest.keyClick(edit,Qt.Key.Key_Escape);wait(lambda:not dialog.isVisible())
                w.activateWindow();wait(lambda:QApplication.activeWindow() is w)
                key(w.table,Qt.Key.Key_Escape);assert not w.model.checked
                key(w.table,Qt.Key.Key_D,ctrl);assert not w.details.page.isVisible()
                key(w.table,Qt.Key.Key_D,ctrl);assert w.details.page.isVisible()
                # Opening the menu has no side effects; deletion is an explicit action.
                old = (len(copied),len(opened));m = menu(w,w.table)
                assert (len(copied),len(opened))==old and len(m.actions())==9
                assert m.actions()[4].text()==tr('songs.delete.action')
                m.actions()[1].trigger();assert copied[-1]=='Title 00'
                m.actions()[2].trigger();assert opened[-1]==first/'s00'
                m.close();app.processEvents()
                key(w.table,Qt.Key.Key_F10,Qt.KeyboardModifier.ShiftModifier)
                wait(lambda:w.actions.menu is not None)
                w.actions.menu.close();app.processEvents()
                # Row highlight alone is not a business check; copy fallback still works.
                assert not w.model.checked
                key(w.table,Qt.Key.Key_C,ctrl);assert copied[-1]=='s00'
                w.set_quest_snapshot([{'folder':'s00','title':'Remote','author':'Q','error':'','duration':60},
                    {'folder':'remote','title':'Remote only','author':'Q','error':'','duration':60}], 'controlled-Quest')
                w.identity_checks.values={side:{row['folder']:row['folder'] for row in model.rows} for side,model in (('local',w.model),('quest',w.quest_model))}
                w.update_comparison()
                # Removed direction shortcuts must not silently flip the fixed Quest-missing list.
                key(w.table,Qt.Key.Key_2,ctrl);assert w.missing_origin=='local'
                m=menu(w,w.quest_table);assert not m.actions()[2].isEnabled();m.close();app.processEvents()
                key(w.quest_table,Qt.Key.Key_A,both);assert w.quest_model.checked=={'s00','remote'} and not w.model.checked
                key(w.quest_table,Qt.Key.Key_Escape);assert not w.quest_model.checked
                key(w.table,Qt.Key.Key_1,ctrl);assert w.missing_origin=='local'
                m=menu(w,w.missing_table);assert m.actions()[2].isEnabled();m.close();app.processEvents()
                # Reject traversal, junction/symlink escape, absent files and stale identities.
                token=w.actions.token('local',w.model.rows[0]);assert w.actions.local_path(token)==first/'s00'
                for folder_name in ('..','../other','C:bad','absent'):
                    assert w.actions.local_path((*token[:3],folder_name)) is None
                stale=m=menu(w,w.table);old=(len(copied),len(opened))
                w.set_target(str(second));wait(lambda:w.local_ready)
                stale.actions()[0].trigger();stale.actions()[2].trigger()
                next(a for a in stale.actions() if a.text()==tr('qt.actions.all')).trigger()
                assert (len(copied),len(opened))==old and not w.model.checked
                stale.close();app.processEvents()
                w.set_target(str(first));wait(lambda:w.local_ready)
                # Existing stale menu revalidates the directory at trigger time.
                m=menu(w,w.table);old=len(opened);missing=first/'s00';moved=base/'held-song'
                missing.rename(moved)
                try:m.actions()[2].trigger();assert len(opened)==old
                finally:moved.rename(missing)
                m.close();app.processEvents()
                # Force an actually present malicious row to exercise path validation.
                original=list(w.model.rows)
                for name in ('..','../other','C:bad'):
                    w.model.replace([dict(original[0],folder=name)])
                    token=w.actions.token('local',w.model.rows[0]);assert w.actions.resolve(token) is not None
                    assert w.actions.local_path(token) is None
                w.model.replace(original)
                # Real NTFS junction: opening must reject even when the name is present in the model.
                link=first/'link';command=['cmd','/c','mklink','/J',str(link),str(second)]
                result=subprocess.run(command,capture_output=True)
                assert result.returncode==0,result.stderr
                try:
                    w.refresh();wait(lambda:w.local_ready)
                    row=next(row for row in w.model.rows if row['folder']=='link')
                    assert w.actions.local_path(w.actions.token('local',row)) is None
                finally:link.rmdir()
                w.refresh();wait(lambda:w.local_ready)
                w.view_mode.setCurrentIndex(1);app.processEvents()
                key(w.grid,Qt.Key.Key_A,ctrl);assert 0<len(w.model.checked)<55
                key(w.grid,Qt.Key.Key_Escape);assert not w.model.checked
                # Unbound Delete does not remove files or queue a destructive operation.
                key(w.grid,Qt.Key.Key_Delete);assert (first/'s00'/'Info.dat').is_file()
                if os.environ.get('QUEST_QT_SHOTS'):
                    shots=Path(os.environ['QUEST_QT_SHOTS']);shots.mkdir(exist_ok=True)
                    w.grab().save(str(shots/f'actions-{language}-{os.environ.get("QT_SCALE_FACTOR","1")}.png'))
                w.close();w.wait_closed();assert not w.actions.active()
                print('PASS',language,'keys/focus/modal/checks/copy/menu/directions/details/stale/root/junction/grid/close')
            finally:w.close();w.wait_closed()
        assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in (first,second) for p in root.rglob('*') if p.is_file()}
        assert not errors,errors
        assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
