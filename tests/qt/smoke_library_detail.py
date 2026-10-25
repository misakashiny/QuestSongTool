"""M6B1 real selection, local metadata/media reads, origin and stale-detail guards."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import wave

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]


def child():
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.messages import tr
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.library_detail import DetailClient
    from quest_tool.song_search import SearchSession
    from quest_tool.workers import human_size

    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def report(kind,value,tb):errors.append(str(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    class Client(DetailClient):
        def __init__(self):
            self.calls=[];self.threads=[];self.hold=False;self.fail=False;self.entered=threading.Event()

        def search(self,request,page,order,cancel):
            self.calls.append(request);self.threads.append(threading.get_ident());self.entered.set()
            while self.hold:
                if cancel.wait(.01):raise RuntimeError('controlled cancellation')
            if self.fail:raise OSError('controlled permission failure')
            return super().search(request,page,order,cancel)

    def wait(predicate,seconds=10):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if predicate():return
        raise AssertionError('M6B1 timeout')
    def row(proxy,folder):
        return next(proxy.index(i,0) for i in range(proxy.rowCount()) if proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder']==folder)
    def click(table,index):
        table.scrollTo(index);app.processEvents();QTest.mouseClick(table.viewport(),Qt.MouseButton.LeftButton,pos=table.visualRect(index).center())

    with tempfile.TemporaryDirectory() as folder:
        base=Path(folder).resolve();library=base/'中文曲库';library.mkdir()
        for name,title in (('first','<b>Long song 中文</b> '*12),('second','Second song')):
            path=library/name;path.mkdir()
            (path/'Info.dat').write_text(json.dumps({'_version':'2.0.0','_songName':title,'_songSubName':'Subtitle',
                '_songAuthorName':'Artist','_beatsPerMinute':155,'_songFilename':'song.wav',
                '_difficultyBeatmapSets':[{'_beatmapCharacteristicName':'Standard','_difficultyBeatmaps':[
                    {'_difficulty':'Easy','_beatmapFilename':'Easy.dat'},{'_difficulty':'ExpertPlus','_beatmapFilename':'ExpertPlus.dat'}]}]}),encoding='utf-8')
            for diff in ('Easy','ExpertPlus'):(path/(diff+'.dat')).write_text('{}',encoding='utf-8')
            with wave.open(str(path/'song.wav'),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000);out.writeframes(b'\0\0'*16000)
            Image.new('RGB',(300,300),'red' if name=='first' else 'blue').save(path/'cover.png')
        bad=library/'broken';bad.mkdir();(bad/'Info.dat').write_text('{broken',encoding='utf-8');(bad/'cover.png').write_bytes(b'bad')
        before={str(p.relative_to(library)):hashlib.sha256(p.read_bytes()).hexdigest() for p in library.rglob('*') if p.is_file()}
        for language in ('zh','en'):
            client=Client();window=LibraryWindow(base/'sandbox',language,detail_session=SearchSession(client));window.show()
            detail=window.details
            try:
                assert detail.key is None and not client.calls
                window.set_target(str(library));wait(lambda:not window.scanning)
                click(window.table,row(window.proxy,'first'));wait(lambda:not detail.loading and detail.data is not None)
                assert detail.key==('local',str(library),'first') and not detail.failed
                assert detail.title.text().startswith('<b>Long song') and detail.title.textFormat()==Qt.TextFormat.PlainText
                assert detail.fields['duration'].text()=='0:02' and detail.fields['bpm'].text()=='155'
                assert 'Easy' in detail.fields['difficulties'].text() and 'ExpertPlus' in detail.fields['difficulties'].text()
                assert detail.fields['version'].text()=='2.0.0' and detail.subtitle.text()=='Subtitle'
                assert detail.fields['size'].text()==human_size(sum(p.stat().st_size for p in (library/'first').rglob('*') if p.is_file()))
                assert detail.cover.pixmap() is not None and not detail.cover.pixmap().isNull()
                click(window.table,row(window.proxy,'second'));wait(lambda:not detail.loading and detail.key[-1]=='second')
                assert detail.cover.pixmap().toImage().pixelColor(80,80).blue()>200
                # Block a current request, then inject current-generation wrong-key
                # and old-generation successes. Neither can replace the loading row.
                client.hold=True;client.entered.clear()
                click(window.table,row(window.proxy,'first'));wait(client.entered.is_set)
                generation=detail.generation;key=detail.key
                detail.session.tasks.emit('search',payload={'generation':generation,'result':{'key':('local',str(library),'wrong'),'data':{'title':'wrong'}}})
                detail.poll();assert detail.loading and detail.key==key and detail.title.text().startswith('<b>')
                click(window.table,row(window.proxy,'second'))
                detail.session.tasks.emit('search',payload={'generation':generation,'result':{'key':key,'data':{'title':'late'}}})
                detail.poll();assert detail.title.text()=='Second song'
                client.hold=False;wait(lambda:not detail.loading)
                # Error and retry use current identity; broken metadata remains partial.
                client.fail=True;click(window.table,row(window.proxy,'first'));wait(lambda:detail.failed)
                assert detail.retry.isVisible()
                client.fail=False;detail.retry.click();wait(lambda:not detail.loading and not detail.failed)
                click(window.table,row(window.proxy,'broken'));wait(lambda:not detail.loading)
                assert detail.data['error'] and detail.fields['bpm'].text()==tr('search.unknown')
                # Snapshot rows and Quest-origin missing rows never trigger local reads.
                snapshot=[dict(folder='remote-only',title='Quest snapshot song',author='Artist',error='',duration=125)]
                window.set_quest_snapshot(snapshot,'controlled fixture');count=len(client.calls)
                click(window.quest_table,row(window.quest_proxy,'remote-only'))
                assert detail.key[0]=='quest' and detail.fields['duration'].text()=='2:05' and len(client.calls)==count
                assert detail.fields['size'].text()==tr('search.unknown') and detail.fields['bpm'].text()==tr('search.unknown')
                window.missing_side.setCurrentIndex(1);click(window.missing_table,row(window.missing_proxy,'remote-only'))
                assert detail.key[0]=='quest' and len(client.calls)==count
                window.missing_side.setCurrentIndex(0);click(window.missing_table,row(window.missing_proxy,'first'))
                wait(lambda:not detail.loading);assert detail.key[0]=='local'
                # Re-clicking a row whose current index did not change must still
                # return from the Quest panel to local details.
                click(window.table,row(window.proxy,'first'));wait(lambda:not detail.loading)
                click(window.quest_table,row(window.quest_proxy,'remote-only'));assert detail.key[0]=='quest'
                click(window.table,row(window.proxy,'first'));wait(lambda:not detail.loading)
                assert detail.key[0]=='local' and detail.key[-1]=='first'
                window.view_mode.setCurrentIndex(window.view_mode.findData('covers'));app.processEvents()
                current=detail.key;window.mode.setCurrentIndex(1);window.language.setCurrentIndex(1-window.language.currentIndex());app.processEvents()
                assert detail.key==current
                window.mode.setCurrentIndex(0)
                # M6C2 intentionally restores the legacy show/hide semantics.
                window.activateWindow();window.grid.setFocus();app.processEvents()
                QTest.keyClick(window.grid,Qt.Key.Key_D,Qt.KeyboardModifier.ControlModifier)
                app.processEvents();assert not detail.page.isVisible() and window.grid.hasFocus() and detail.key==current
                QTest.keyClick(window.grid,Qt.Key.Key_D,Qt.KeyboardModifier.ControlModifier)
                app.processEvents();assert detail.page.isVisible() and detail.key==current
                for width,height in ((1000,640),(1400,850)):
                    available=window.screen().availableGeometry();window.resize(min(width,available.width()-32),min(height,available.height()-32));app.processEvents()
                    assert detail.page.width()>80 and detail.scroll.height()>100
                    assert detail.page.mapTo(window,detail.page.rect().bottomRight()).x()<window.width()
                    assert detail.title.hasHeightForWidth()
                    for name,caption in detail.captions.items():
                        assert caption.isVisible() and caption.width()>0 and caption.height()>=caption.fontMetrics().height(), (name,caption.size())
                        assert detail.fields[name].width()>20,(name,detail.fields[name].size())
                if os.environ.get('QUEST_QT_SHOTS'):
                    window.grid.setCurrentIndex(row(window.proxy,'second'));wait(lambda:not detail.loading and detail.key[-1]=='second')
                    output=Path(os.environ['QUEST_QT_SHOTS']);output.mkdir(parents=True,exist_ok=True)
                    assert window.grab().save(str(output/f'detail-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                # Refresh clears selection context immediately; old results stay ignored.
                generation=detail.generation;window.reload.click();assert detail.key is None
                detail.session.tasks.emit('search',payload={'generation':generation,'result':{'key':key,'data':{'title':'late'}}})
                detail.poll();assert detail.key is None
                wait(lambda:not window.scanning)
                client.hold=True;client.entered.clear()
                window.view_mode.setCurrentIndex(window.view_mode.findData('list'))
                click(window.table,row(window.proxy,'first'));wait(client.entered.is_set)
                window.close();window.wait_closed()
                assert detail.closed and not any(thread.is_alive() for thread in detail.session.tasks._threads)
                assert client.threads and all(thread!=threading.get_ident() for thread in client.threads)
                print('PASS',language,'details metadata/duration/cover/size/origin/snapshot/no-local-I/O/stale/retry/reclick/DPI/close')
            finally:client.hold=False;window.close();window.wait_closed()
        assert before=={str(p.relative_to(library)):hashlib.sha256(p.read_bytes()).hexdigest() for p in library.rglob('*') if p.is_file()}
        assert not errors,errors
        assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)
        assert not (base/'sandbox'/'songs_cache.db').exists()


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for factor in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=factor),capture_output=True,timeout=120)
            print(factor,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
