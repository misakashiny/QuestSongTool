"""M6C1 real shared rating state, ordering, commits, failures and root guards."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.database import Database
    from quest_tool.fsapi import Location
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.library_preferences import LibraryPreferences
    from quest_tool.qt_ui.download_import import LocalModel
    from quest_tool.qt_ui.library_ratings import RATING_ROLE
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def report(kind,value,tb):errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    def wait(test,seconds=10):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            app.processEvents();QTest.qWait(10)
            if test():return
        raise AssertionError('M6C1 timeout')
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp);first=base/'first';second=base/'second'
        for root,names in ((first,('a','b','c','n','z')),(second,('a',))):
            for i,name in enumerate(names):
                path=root/name;path.mkdir(parents=True)
                (path/'Info.dat').write_text(json.dumps({'_version':'2.0.0','_songName':'Song '+name,'_songDuration':100+i}),encoding='utf-8')
                Image.new('RGB',(64,64),'red' if name=='n' else 'blue').save(path/'cover.png')
        original={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in (first,second) for p in root.rglob('*') if p.is_file()}
        old_all=Database.all_ratings;old_set=Database.set_rating
        state={'fail':False,'hold':False};entered=threading.Event();release=threading.Event();calls=[]
        def all_ratings(db,location=None):
            calls.append((location,threading.get_ident()))
            assert location is not None
            if state['hold']:
                entered.set();assert release.wait(5)
            if state['fail']:raise OSError('controlled read failure')
            return old_all(db,location)
        Database.all_ratings=all_ratings
        for language in ('zh','en'):
            sandbox=base/language;sandbox.mkdir()
            identity=Location('local',str(first.resolve())).identity
            other=Location('local',str(second.resolve())).identity
            with Database(sandbox/'qt-library-userdata.sqlite3') as db:
                for name,value in (('a',3),('b',3),('c',0),('z',5)):db.set_rating(identity,name,value)
                db.set_rating(other,'a',1)
            prefs=LibraryPreferences(sandbox)
            assert prefs.save(dict(widths=[333,177,91],preview_seconds=60,preview_volume=35,view='list'))
            window=LibraryWindow(sandbox,language);window.show()
            def row(folder,proxy=None):
                proxy=proxy or window.proxy
                return next(proxy.index(i,0) for i in range(proxy.rowCount()) if proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder']==folder)
            def values():return {item['folder']:window.model.row_rating(i) for i,item in enumerate(window.model.rows)}
            def order():return [window.proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder'] for i in range(window.proxy.rowCount())]
            def sort(high):window.table.sortByColumn(3,Qt.SortOrder.DescendingOrder if high else Qt.SortOrder.AscendingOrder);app.processEvents()
            def choose(folder):
                index=row(folder);window.table.setCurrentIndex(index);window.details.select('local',index)
                wait(lambda:not window.userdata.pending)
            try:
                assert LocalModel().columnCount()==3 and window.model.columnCount()==4
                state['fail']=True
                assert window.set_target(first);wait(lambda:window.ratings.failed)
                assert all(v is None for v in values().values()) and window.rating_retry.isVisible()
                state['fail']=False;window.rating_retry.click();wait(lambda:window.ratings.ready)
                assert values()==dict(a=3,b=3,c=0,n=0,z=5)
                assert row('n').data(RATING_ROLE)==0 and row('n').siblingAtColumn(3).data()=='0 / 5'
                sort(False);assert order()==['c','n','a','b','z']
                sort(True);assert order()==['z','a','b','c','n']
                window.rating_sort.setCurrentIndex(window.rating_sort.findData('low'));window.rating_sort.activated.emit(window.rating_sort.currentIndex())
                assert order()==['c','n','a','b','z']
                for name in ('a','b'):window.proxy.setData(row(name),Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
                choose('b');sort(True)
                window.userdata.rating.setCurrentIndex(5);wait(lambda:not window.userdata.pending)
                assert values()['b']==5 and order()==['b','z','a','c','n']
                assert window.details.key[2]=='b' and window.userdata.key[2]=='b' and window.model.checked=={'a','b'}
                window.userdata.rating.setCurrentIndex(0);wait(lambda:not window.userdata.pending)
                assert values()['b']==0 and window.details.key[2]=='b' and window.model.checked=={'a','b'}
                def fail_set(db,location,folder,rating):raise OSError('controlled write failure')
                Database.set_rating=fail_set
                window.userdata.rating.setCurrentIndex(4);wait(lambda:window.userdata.failed)
                assert values()['b']==0
                Database.set_rating=old_set;window.userdata.retry.click();wait(lambda:not window.userdata.pending)
                assert values()['b']==4
                window.set_quest_snapshot([dict(folder='a',title='Quest a',author='',duration=0,error=''),dict(folder='quest-only',title='Quest',author='',duration=0,error='')],'fixture')
                assert all(window.quest_model.row_rating(i) is None for i in range(window.quest_model.rowCount()))
                assert {item['folder']:window.missing_model.row_rating(i) for i,item in enumerate(window.missing_model.rows)}==dict(b=4,c=0,n=0,z=5)
                window.missing_side.setCurrentIndex(1)
                assert all(window.missing_model.row_rating(i) is None for i in range(window.missing_model.rowCount()))
                window.missing_side.setCurrentIndex(0)
                window.view_mode.setCurrentIndex(window.view_mode.findData('covers'));app.processEvents()
                assert window.grid.model() is window.proxy and row('b').data(RATING_ROLE)==4
                window.resize(1000,640);app.processEvents();sort(True)
                window.table.selectionModel().clearCurrentIndex();window.grid.scrollToTop()
                window.covers.reset()
                wait(lambda:bool(window.covers.visible) and not window.covers.pending and not window.covers.debounce.isActive())
                assert 'n' not in window.covers.visible and 'n' not in window.covers.cache.sources
                window.userdata.request(('local',str(first.resolve()),'n'),'rating',5)
                wait(lambda:values()['n']==5)
                # No header change, scrolling, resizing or manual cover request.
                wait(lambda:'n' in window.covers.visible and 'n' in window.covers.cache.sources)
                assert window.covers.cache.sources['n'].toImage().pixelColor(10,10).red()>200
                window.userdata.request(('local',str(first.resolve()),'n'),'rating',0)
                wait(lambda:values()['n']==0 and not window.userdata.active)
                choose('b')
                for width,height in ((1000,640),(1400,850)):
                    available=window.screen().availableGeometry();window.resize(min(width,available.width()-32),min(height,available.height()-32));app.processEvents()
                    assert window.rating_sort.width()>20 and window.grid.cell_size().height()>0
                shots=Path(os.environ['QUEST_QT_SHOTS']);shots.mkdir(parents=True,exist_ok=True)
                window.grab().save(str(shots/f'ratings-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                state['fail']=True;window.ratings.load();wait(lambda:window.ratings.failed)
                window.details.select('local',row('b'),True);wait(lambda:not window.userdata.pending)
                assert values()['b']==4 and all(v is None for key,v in values().items() if key!='b')
                sort(False);assert order()==['b','a','c','n','z']
                sort(True);assert order()==['b','a','c','n','z']
                state['fail']=False;window.rating_retry.click();wait(lambda:window.ratings.ready)
                state['hold']=True;entered.clear();release.clear();window.ratings.load();wait(entered.is_set)
                old_sequence=window.ratings.sequence
                window.set_target(second);wait(lambda:window.local_ready)
                assert all(v is None for v in values().values())
                state['hold']=False;release.set();wait(lambda:window.ratings.ready)
                assert values()=={'a':1}
                window.ratings.received(dict(sequence=window.ratings.sequence,key=('local',str(first),''),ratings={'a':5},failed=False))
                window.ratings.received(dict(sequence=old_sequence,key=('local',str(second),''),ratings={'a':5},failed=False))
                assert values()=={'a':1}
                choose('a');window.table.setColumnWidth(3,111)
                window.userdata.rating.setCurrentIndex(2);window.close();window.wait_closed()
                with Database(sandbox/'qt-library-userdata.sqlite3') as db:assert db.get_rating(other,'a')[0]==2
                restored=LibraryWindow(sandbox,language)
                assert restored.table.saved_widths()==[333,177,91]
                assert restored.table.isColumnHidden(1)
                assert restored.table.columnWidth(3)==111 and restored.preview.length.currentData()==60 and restored.preview.volume.value()==35
                restored.close();restored.wait_closed()
                print('PASS',language,'rating roots/numeric stable sorting/unknown last/selection/checks/commit/failure/retry/Quest/missing/grid/DPI/prefs/close')
            finally:
                state.update(fail=False,hold=False);release.set();Database.set_rating=old_set;window.close();window.wait_closed()
        Database.all_ratings=old_all
        assert calls and all(thread!=threading.get_ident() and location.startswith('local:') for location,thread in calls)
        assert original=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for root in (first,second) for p in root.rglob('*') if p.is_file()}
        assert not errors,errors
        assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            p=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,p.stdout.decode('utf-8','replace'),p.stderr.decode('utf-8','replace'))
            assert p.returncode==0
