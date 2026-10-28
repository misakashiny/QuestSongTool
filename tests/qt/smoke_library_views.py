"""M6A2 real local covers, folder-based comparison and isolated preference recovery."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]


def child():
    from PIL import Image
    from PySide6.QtCore import QPoint,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui import library_covers
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.library_preferences import LibraryPreferences

    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def report(kind,value,tb):
        errors.append(str(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    reads=[];threads=[];real_cover=library_covers.local_cover
    def logged(path):
        reads.append(str(path));threads.append(threading.get_ident());return real_cover(path)
    library_covers.local_cover=logged
    def wait(predicate,seconds=10):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            if predicate():return
        raise AssertionError('M6A2 timeout')
    def names(model):return {row['folder'] for row in model.rows}
    def visible(proxy):return {proxy.index(row,0).data(Qt.ItemDataRole.UserRole)['folder'] for row in range(proxy.rowCount())}
    def settle(window):wait(lambda:not window.covers.pending and not window.covers.debounce.isActive())
    with tempfile.TemporaryDirectory() as folder:
        base=Path(folder).resolve();library=base/'中文歌曲';library.mkdir();sandbox=base/'sandbox'
        png=base/'cover.png';Image.new('RGB',(500,350),'red').save(png)
        other=base/'other';other.mkdir();(other/'row000').mkdir()
        (other/'row000'/'Info.dat').write_text(json.dumps({'_version':'2.0.0','_songName':'Blue library'}),encoding='utf-8')
        Image.new('RGB',(256,256),'blue').save(other/'row000'/'cover.png')
        old_red=real_cover(png)
        for index in range(160):
            song=library/f'row{index:03}';song.mkdir()
            (song/'Info.dat').write_text(json.dumps({'_version':'2.0.0','_songName':f'Song {index:03}',
                '_songAuthorName':'Artist','_beatsPerMinute':120,'_songDuration':120+index}),encoding='utf-8')
            if index==1:(song/'cover.png').write_bytes(b'broken')
            elif index==2:
                with (song/'cover.png').open('wb') as file:file.truncate(5*1024*1024+1)
            elif index!=3:shutil.copyfile(png,song/'cover.png')
        hashes={str(p.relative_to(library)):hashlib.sha256(p.read_bytes()).hexdigest() for p in library.rglob('*') if p.is_file()}
        for language in ('zh','en'):
            window=LibraryWindow(sandbox,language);window.show()
            try:
                assert not window.missing_model.rows and not window.missing_side.isEnabled()
                window.set_target(str(library));wait(lambda:not window.scanning)
                assert len(window.model.rows)==160 and not window.quest_ready
                snapshot=[dict(window.model.rows[0],title='Different title, same folder'),dict(window.model.rows[2]),
                          dict(window.model.rows[0],folder='quest-only',title='Only on Quest')]
                window.set_quest_snapshot(snapshot,'controlled read-only fixture')
                assert not window.missing_model.rows and not window.identity_checks.complete()
                # View fixtures declare known content; real map hashes and
                # cross-folder matches are verified in smoke_usability.
                window.identity_checks.values={side:{row['folder']:hashlib.sha1(row['folder'].encode()).hexdigest() for row in model.rows} for side,model in (('local',window.model),('quest',window.quest_model))}
                window.update_comparison()
                assert window.quest_ready and names(window.quest_model)=={'row000','row002','quest-only'}
                assert names(window.missing_model)==names(window.model)-names(window.quest_model)
                assert window.missing_origin=='local' and all(row['origin']=='local' for row in window.missing_model.rows)
                window.local_filter.setCurrentIndex(1);assert visible(window.proxy)=={'row000','row002'}
                window.local_filter.setCurrentIndex(2);assert window.proxy.rowCount()==158
                window.quest_filter.setCurrentIndex(2);assert visible(window.quest_proxy)=={'quest-only'}
                window.missing_model.checked.add('row001')
                window.missing_side.setCurrentIndex(1)
                assert names(window.missing_model)=={'quest-only'} and window.missing_origin=='quest' and not window.missing_model.checked
                assert window.missing_model.rows[0]['origin']=='quest'
                # An invalid incoming snapshot is transactional; old valid data remains.
                for bad in ([snapshot[0],snapshot[0]],[dict(snapshot[0],folder='../outside')],[dict(snapshot[0],duration=float('nan'))]):
                    try:window.set_quest_snapshot(bad,'invalid')
                    except ValueError:pass
                    else:raise AssertionError('Invalid snapshot accepted')
                assert window.quest_identity=='controlled read-only fixture'
                rejected=[]
                def wrong_thread():
                    try:window.set_quest_snapshot([], 'worker')
                    except RuntimeError:rejected.append(True)
                worker=threading.Thread(target=wrong_thread);worker.start();worker.join();assert rejected
                # Compare directly with the original ReportMixin.compare branch.
                tree=ast.parse((ROOT/'quest_tool/app_report.py').read_text(encoding='utf-8'))
                compare=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='compare')
                branch=next(n for n in compare.body if isinstance(n,ast.If) and isinstance(n.test,ast.Compare))
                for side in ('quest','local'):
                    scope={'local_names':names(window.model),'quest_names':names(window.quest_model),'missing_side':side,
                           'self':type('OriginalContext',(),{'list_local':None,'list_quest':None})()}
                    exec(compile(ast.Module(body=[branch],type_ignores=[]),'original-folder-difference','exec'),scope)
                    window.missing_side.setCurrentIndex(0 if side=='quest' else 1)
                    assert [row['folder'] for row in window.missing_model.rows]==scope['missing']
                window.local_filter.setCurrentIndex(0);window.quest_filter.setCurrentIndex(0)
                window.view_mode.setCurrentIndex(window.view_mode.findData('covers'))
                wait(lambda:bool(window.covers.cache.sources));settle(window)
                assert 'row000' in window.covers.cache.sources
                assert len(window.covers.cache.sources)<160,'Eager full-library cover reads'
                # Only visible covers are requested; the fixed audio bar reduces
                # viewport height, so visit each invalid fixture explicitly.
                for folder in ('row001','row002','row003'):
                    invalid = next(window.proxy.index(i,0) for i in range(window.proxy.rowCount())
                                   if window.proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder']==folder)
                    window.grid.scrollTo(invalid)
                    wait(lambda:folder in window.covers.failed)
                index=window.proxy.index(0,0);window.grid.scrollTo(index);app.processEvents()
                option=window.grid.visualRect(index).adjusted(4,4,-4,-4)
                delegate=window.grid.itemDelegate();point=delegate.check_rect(option).center()
                QTest.mouseClick(window.grid.viewport(),Qt.MouseButton.LeftButton,pos=point)
                assert 'row000' in window.model.checked
                window.view_mode.setCurrentIndex(window.view_mode.findData('list'))
                assert window.proxy.index(0,0).data(Qt.ItemDataRole.CheckStateRole)==Qt.CheckState.Checked
                window.table.setColumnWidth(0,333);window.table.setColumnWidth(1,177);window.table.setColumnWidth(2,91)
                window.view_mode.setCurrentIndex(window.view_mode.findData('covers'));app.processEvents();settle(window)
                count=len(reads);window.mode.setCurrentIndex(1);window.mode.setCurrentIndex(0);settle(window)
                assert len(reads)==count,'Theme changed local I/O'
                window.grid.scrollToBottom();wait(lambda:any('row159' in key for key in window.covers.cache.sources));settle(window)
                assert len(window.covers.cache.sources)<=128 and window.covers.cache.source_bytes<=32*1024*1024
                for width,height in ((1000,640),(1400,850)):
                    available=window.screen().availableGeometry();window.resize(min(width,available.width()-32),min(height,available.height()-32));app.processEvents()
                    assert window.grid.cell_size().width()>0
                    assert window.grid.mapTo(window,QPoint(0,0)).x()>=0
                # Empty-but-valid differs from not-yet-loaded; clearing cannot invent missing.
                window.set_quest_snapshot([], 'valid empty fixture');assert len(window.missing_model.rows)==0
                window.identity_checks.values={'quest':{},'local':{row['folder']:hashlib.sha1(row['folder'].encode()).hexdigest() for row in window.model.rows}}
                window.update_comparison()
                window.missing_side.setCurrentIndex(0);assert len(window.missing_model.rows)==160
                window.clear_quest_snapshot();assert not window.missing_model.rows and not window.missing_side.isEnabled()
                window.set_quest_snapshot(snapshot,'controlled read-only fixture');window.missing_side.setCurrentIndex(0)
                old_epoch=window.covers.epoch
                window.set_target(str(other))
                assert not window.missing_model.rows and not window.missing_side.isEnabled()
                wait(lambda:not window.scanning and 'row000' in window.covers.cache.sources);settle(window)
                assert window.covers.cache.sources['row000'].toImage().pixelColor(100,100).blue()>200
                window.covers.session.tasks.emit('local_cover',payload={'epoch':old_epoch,'folder':'row000','data':old_red})
                window.covers.poll()
                assert window.covers.cache.sources['row000'].toImage().pixelColor(100,100).blue()>200,'Stale cover overwrote new root'
                window.set_target(str(library));wait(lambda:not window.scanning);window.grid.scrollToTop();settle(window)
                if os.environ.get('QUEST_QT_SHOTS'):
                    output=Path(os.environ['QUEST_QT_SHOTS']);output.mkdir(exist_ok=True,parents=True)
                    assert window.grab().save(str(output/f'library-covers-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                window.close();window.wait_closed();assert not window.preference_failed
                restored=LibraryWindow(sandbox,language)
                try:
                    assert restored.view_mode.currentData()=='covers'
                    assert restored.table.saved_widths()==[333,177,91]
                    assert restored.table.isColumnHidden(1)
                    assert restored.target is None and not restored.quest_ready
                finally:restored.close();restored.wait_closed()
                print('PASS',language,'covers/literal identity/filter/old comparison parity/origin/snapshot validation/prefs/thread guard/close')
            finally:window.close();window.wait_closed()
        prefs=LibraryPreferences(sandbox)
        prefs.path.write_text('{broken',encoding='utf-8');assert prefs.load()=={}
        assert prefs.save({'view':'illegal','widths':[0,'bad',99999],'target':'external'}) and prefs.load()=={}
        prefs.path.write_bytes(b'x'*17000);assert prefs.load()=={}
        assert hashes=={str(p.relative_to(library)):hashlib.sha256(p.read_bytes()).hexdigest() for p in library.rglob('*') if p.is_file()}
        assert threads and all(t!=threading.get_ident() for t in threads)
        assert not errors,errors
        assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for factor in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=factor),capture_output=True,timeout=150)
            print(factor,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
