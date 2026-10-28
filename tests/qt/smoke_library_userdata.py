"""M6B3 isolated legacy schema, ratings/comments, async order and preferences."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialogButtonBox
    from quest_tool.database import Database
    from quest_tool.fsapi import Location
    from quest_tool.qt_ui import library_userdata
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.library_preferences import LibraryPreferences
    app = QApplication([]); app.setStyle('Fusion'); errors = []
    def report(kind, value, tb):
        errors.append(repr(value)); sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    def wait(predicate, seconds=10):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            app.processEvents(); QTest.qWait(10)
            if predicate(): return
        raise AssertionError('M6B3 timeout')
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp); first = base/'first'; second = base/'second'
        for root in (first, second):
            song = root/'same'; song.mkdir(parents=True)
            (song/'Info.dat').write_text(json.dumps({'_version':'2.0.0','_songName':'Same song','_songDuration':90}), encoding='utf-8')
        originals = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in (first,second) for p in root.rglob('*') if p.is_file()}
        seed = base/'old-format-copy.db'
        identities = [Location('local',str(root.resolve())).identity for root in (first,second)]
        with Database(seed) as db:
            db.set_rating(identities[0], 'same', 3); db.set_comment(identities[0], 'same', 'seed comment')
            db.set_rating(identities[1], 'same', 5); db.set_comment(identities[1], 'same', 'other library')
        with closing(sqlite3.connect(seed)) as conn: schema = conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall()
        calls = []; main = threading.get_ident(); real_factory = library_userdata.Database
        def factory(path):
            calls.append(threading.get_ident()); return real_factory(path)
        library_userdata.Database = factory
        for language in ('zh','en'):
            sandbox = base/language; sandbox.mkdir(); dbpath = sandbox/'qt-library-userdata.sqlite3'; shutil.copy2(seed,dbpath)
            window = LibraryWindow(sandbox, language); window.show(); user = window.userdata
            release = threading.Event(); entered = threading.Event(); old_rating = Database.set_rating; old_comment = Database.set_comment
            def select(root):
                assert window.set_target(root); wait(lambda:window.local_ready)
                window.table.setCurrentIndex(window.proxy.index(0,0))
                window.details.select('local',window.proxy.index(0,0))
                wait(lambda:not user.pending and not user.active)
            def edit(text):
                window.details.scroll.ensureWidgetVisible(user.edit); app.processEvents()
                QTest.mouseClick(user.edit,Qt.MouseButton.LeftButton); wait(lambda:user.dialog is not None)
                user.dialog.editor.setPlainText(text); return user.dialog
            try:
                assert not user.rating.isEnabled() and not user.queue.started
                select(first); assert user.rating_value==3 and user.comment_value=='seed comment'
                user.rating.setCurrentIndex(5); wait(lambda:not user.pending)
                assert user.rating_value==5 and user.comment_value=='seed comment'
                user.rating.setCurrentIndex(0); wait(lambda:not user.pending)
                assert user.rating_value==0 and user.comment_value=='seed comment'
                dialog=edit('x'*501)
                assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
                QTest.keyClick(dialog.editor,Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier)
                assert user.dialog is dialog
                QTest.keyClick(dialog.editor,Qt.Key.Key_Escape); wait(lambda:user.dialog is None)
                assert user.comment_value=='seed comment'
                expected='多行\n<b>literal text</b>'
                dialog=edit('  '+expected+'  ')
                QTest.keyClick(dialog.editor,Qt.Key.Key_Return,Qt.KeyboardModifier.ControlModifier)
                wait(lambda:user.dialog is None and not user.pending)
                assert user.comment_value==expected and user.rating_value==0
                def fail(db, location, folder, comment): raise OSError('controlled write error')
                Database.set_comment=fail
                dialog=edit('retry draft'); dialog.save(); wait(lambda:user.failed)
                assert user.retry.isVisible() and user.failures[(user.key,'comment')]['value']=='retry draft'
                Database.set_comment=old_comment
                user.retry.click(); wait(lambda:not user.pending)
                assert not user.failed and user.comment_value=='retry draft' and not user.failures
                def slow(db, location, folder, rating):
                    entered.set(); assert release.wait(5); old_rating(db,location,folder,rating)
                Database.set_rating=slow
                user.rating.setCurrentIndex(4); wait(entered.is_set)
                window.set_target(second); release.set(); wait(lambda:window.local_ready)
                window.table.setCurrentIndex(window.proxy.index(0,0)); window.details.select('local',window.proxy.index(0,0))
                wait(lambda:not user.pending and not user.active)
                assert user.rating_value==5 and user.comment_value=='other library'
                Database.set_rating=old_rating
                user.queue.tasks.emit('userdata',payload=dict(sequence=user.sequence,key=('local',str(first),'same'),action='get',value=None,rating=1,comment='late'))
                user.poll(); assert user.rating_value==5 and user.comment_value=='other library'
                window.set_quest_snapshot([dict(folder='same',title='Quest',author='',duration=0,error='')],'fixture')
                window.details.select('quest',window.quest_proxy.index(0,0))
                assert user.key is None and not user.rating.isEnabled() and not user.edit.isEnabled()
                select(first); assert user.rating_value==4 and user.comment_value=='retry draft'
                for width,height in ((1000,640),(1400,850)):
                    available=window.screen().availableGeometry()
                    window.resize(min(width,available.width()-32),min(height,available.height()-32));app.processEvents()
                    window.details.scroll.ensureWidgetVisible(user.edit);app.processEvents()
                    if window.details.scroll.horizontalScrollBar().maximum():
                        print('WIDTHS',language,width,[(type(c).__name__,c.width(),c.minimumSizeHint().width(),c.minimumWidth()) for c in (window.details.scroll.viewport(),window.details.scroll.widget(),user.page,user.rating,user.edit,user.caption,user.state)],flush=True)
                    assert window.details.scroll.horizontalScrollBar().maximum()==0
                    for control in (user.caption,user.rating,user.comment,user.edit,user.state):
                        assert control.width()>20 and control.height()>0
                window.details.scroll.ensureWidgetVisible(user.edit); app.processEvents()
                shots=Path(os.environ['QUEST_QT_SHOTS']); shots.mkdir(parents=True,exist_ok=True)
                window.grab().save(str(shots/f'userdata-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                assert not hasattr(window.preview,'length');window.preview.volume.setValue(35)
                user.rating.setCurrentIndex(2)
                window.close(); window.wait_closed()
                assert not any(t.is_alive() for t in user.queue.tasks._threads)
                with Database(dbpath) as db:
                    assert db.get_rating(identities[0],'same')==(2,'retry draft')
                    assert db.get_rating(identities[1],'same')==(5,'other library')
                with closing(sqlite3.connect(dbpath)) as conn: assert schema==conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall()
                restored=LibraryWindow(sandbox,language)
                assert restored.preview.play_seconds==0 and restored.preview.volume.value()==35
                assert restored.target is None and not restored.userdata.queue.started
                restored.close(); restored.wait_closed()
                print('PASS',language,'legacy schema/location/rating clear/comment cancel-limit-shortcut/failure draft retry/order/stale/Quest/close/preferences')
            finally:
                release.set(); Database.set_rating=old_rating; Database.set_comment=old_comment
                window.close(); window.wait_closed()
        assert calls and all(t!=main for t in calls)
        assert LibraryPreferences.validate({'preview_seconds':True,'preview_volume':True})=={}
        assert LibraryPreferences.validate({'preview_seconds':99,'preview_volume':101})=={}
        assert LibraryPreferences.validate({'preview_seconds':0,'preview_volume':0})=={'preview_seconds':0,'preview_volume':0}
        assert originals=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in (first,second) for p in root.rglob('*') if p.is_file()}
        assert not errors,errors
        assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)


if __name__=='__main__':
    if '--child' in sys.argv: child()
    else:
        for scale in ('1','1.25','1.5'):
            p=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,p.stdout.decode('utf-8','replace'),p.stderr.decode('utf-8','replace'))
            assert p.returncode==0
