"""Offline provider only: identity, cancellation, snapshots and real Qt controls."""
import copy
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
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.device_protocol import descriptor, DeviceClient, snapshot_rows
    from quest_tool.downloads import DownloadError
    from quest_tool import device, adb
    app = QApplication([]);app.setStyle('Fusion');errors = [];external = []
    def report(kind,value,tb):errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook = report
    def forbidden(*args,**kwargs):external.append('external I/O');raise AssertionError('M7A1 must remain offline')
    old_scan,old_bridge,old_popen = device.scan_quest,adb.AdbBridge.__init__,subprocess.Popen
    device.scan_quest=forbidden;adb.AdbBridge.__init__=forbidden;subprocess.Popen=forbidden
    def wait(test,seconds=10):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if test():return
        raise AssertionError('M7A1 timeout')
    def candidate(serial='A',root='/sdcard/CustomLevels',label='Quest A'):
        return dict(kind='adb',device_id=serial,root=root,label=label,state='device',model='Quest 3',codename='eureka',brand='oculus',identity_verified=True)
    a=candidate();b=candidate('B',label='Quest B')
    assert descriptor(a)['identity']!=descriptor(b)['identity']
    assert descriptor(a)['identity']==descriptor(dict(a,label='Renamed'))['identity']
    assert descriptor(a)['identity']==descriptor(dict(a,root=a['root']+'/'))['identity']
    assert descriptor(a)['identity']!=descriptor(dict(a,root='/sdcard/Other'))['identity']
    assert descriptor(dict(a,kind='mtp'))['identity']!=descriptor(a)['identity']
    assert descriptor(dict(a,kind='drive',root='D:/Songs/'))['identity']==descriptor(dict(a,kind='drive',root='d:\\songs'))['identity']
    def rejects(call):
        try:call()
        except DownloadError:return
        raise AssertionError('protocol accepted invalid data')
    for bad in (dict(a,identity_verified=False),dict(a,state='offline'),dict(a,model='Phone',codename='elish',brand='Xiaomi'),dict(a,root='/sdcard/../Other')):
        rejects(lambda item=bad:descriptor(item))
    row=dict(folder='same',title='Device song',author='Artist',error='',duration=60)
    for rows in ([dict(row,duration=True)],[dict(row,duration=float('nan'))],[dict(row,folder='..')],[row,row]):
        rejects(lambda data=rows:snapshot_rows(data))
    class Provider:
        def __init__(self):
            self.calls=[];self.rows=[row];self.fail=False;self.connected=True;self.bad=False
            self.hold=False;self.started=threading.Event();self.release=threading.Event()
        def list_devices(self,cancel):
            self.calls.append(('list',threading.get_ident(),''))
            if self.fail:raise OSError('controlled list failure')
            return copy.deepcopy([b,a,dict(a,device_id='phone',model='Phone',codename='elish',brand='Xiaomi'),dict(a,device_id='off',state='offline')])
        def read_library(self,selected,cancel):
            self.calls.append(('read',threading.get_ident(),selected['device_id']));self.started.set()
            # Ignore cancellation intentionally; the session must reject the return.
            while self.hold and not self.release.wait(.01):pass
            if self.fail:raise OSError('controlled read failure')
            fresh=copy.deepcopy(selected)
            if self.bad:fresh['device_id']='wrong-device'
            return {'connected':self.connected,'device':fresh,'rows':copy.deepcopy(self.rows)}
    with tempfile.TemporaryDirectory() as folder:
        base=Path(folder).resolve();local=base/'local';local.mkdir();song=local/'same';song.mkdir()
        (song/'Info.dat').write_text(json.dumps({'_version':'2.0.0','_songName':'Local song'}),encoding='utf-8')
        original=hashlib.sha256((song/'Info.dat').read_bytes()).hexdigest()
        for language in ('zh','en'):
            normal=LibraryWindow(base/('normal-'+language),language)
            assert normal.device_browser.session is None and not normal.quest_ready
            assert normal.device_browser.page.isHidden()
            assert not normal.device_browser.refresh.isEnabled() and not normal.device_browser.read.isEnabled()
            normal.close();normal.wait_closed()
            provider=Provider();window=LibraryWindow(base/('sandbox-'+language),language,device_provider=provider)
            ui_threads=[];setter=window.set_quest_snapshot
            def apply(rows,identity):ui_threads.append(threading.get_ident());setter(rows,identity)
            window.set_quest_snapshot=apply
            window.show();window.activateWindow();panel=window.device_browser
            try:
                assert not provider.calls and panel.state=='idle'
                assert window.set_target(str(local));wait(lambda:window.local_ready)
                window.model.checked.add('same')
                panel.refresh.click();assert panel.state=='discovering' and not window.quest_ready
                wait(lambda:not panel.busy);assert panel.state=='choose' and panel.rejected==2 and len(panel.devices)==2
                assert panel.selected is None and not panel.read.isEnabled() and len(provider.calls)==1
                def select(serial):
                    index=next(i for i,item in enumerate(panel.devices) if item['device_id']==serial)
                    panel.choices.setCurrentIndex(index+1)
                select('A');assert len(provider.calls)==1 and not window.quest_ready
                panel.read.click();wait(lambda:not panel.busy)
                assert panel.state=='ready' and window.quest_ready and window.quest_identity==descriptor(a)['identity']
                assert window.quest_model.rows[0]['title']=='Device song' and window.model.checked=={'same'}
                assert window.quest_proxy.rowCount()==1 and window.missing_proxy.rowCount()==0
                window.details.select('quest',window.quest_proxy.index(0,0));assert window.details.key[0]=='quest'
                select('B');assert not window.quest_ready and window.details.key is None and not window.quest_model.rows
                provider.rows=[];panel.read.click();wait(lambda:not panel.busy)
                assert panel.state=='empty' and window.quest_ready and not window.quest_model.rows
                provider.connected=False;panel.read.click();wait(lambda:not panel.busy)
                assert panel.state=='disconnected' and not window.quest_ready and not window.quest_model.rows
                provider.connected=True;provider.bad=True;panel.read.click();wait(lambda:not panel.busy)
                assert panel.state=='rejected' and not window.quest_ready
                provider.bad=False;provider.fail=True;panel.read.click();wait(lambda:not panel.busy)
                assert panel.state=='failed' and not window.quest_ready
                provider.fail=False;provider.rows=[row];provider.started.clear();provider.hold=True;provider.release.clear()
                panel.read.click();wait(provider.started.is_set);old_generation=panel.generation
                panel.clear.click();assert panel.state=='cancelled' and not window.quest_ready
                select('A');panel.read.click();new_generation=panel.generation
                assert new_generation!=old_generation
                provider.hold=False;provider.release.set();wait(lambda:not panel.busy)
                assert window.quest_identity==descriptor(a)['identity']
                # Wrong generation and same generation/wrong identity cannot install a snapshot.
                panel.session.tasks.emit('search',payload={'generation':old_generation,'result':{'action':'read','identity':descriptor(b)['identity'],'rows':[]}})
                panel.poll();assert window.quest_identity==descriptor(a)['identity']
                provider.started.clear();provider.hold=True;provider.release.clear();panel.read.click();wait(provider.started.is_set)
                panel.session.tasks.emit('search',payload={'generation':panel.generation,'result':{'action':'read','identity':descriptor(b)['identity'],'rows':[row]}})
                panel.poll();assert panel.state=='rejected' and not window.quest_ready
                provider.hold=False;provider.release.set();panel.cancel()
                panel.refresh.click();wait(lambda:not panel.busy);select('A');panel.read.click();wait(lambda:not panel.busy)
                assert panel.state=='ready'
                window.resize(1000,640);app.processEvents()
                for control in (panel.choices,panel.refresh,panel.read,panel.clear,panel.status):
                    assert control.width()>0 and control.height()>0
                    assert control.mapTo(window,control.rect().bottomRight()).x()<=window.width()
                controls=(panel.choices,panel.refresh,panel.read,panel.clear,panel.status)
                for i,first in enumerate(controls):
                    first_rect=first.rect().translated(first.mapTo(panel.page,first.rect().topLeft()))
                    assert panel.page.rect().contains(first_rect),(first.text() if hasattr(first,'text') else 'choice',first_rect,panel.page.rect())
                    for second in controls[i+1:]:
                        second_rect=second.rect().translated(second.mapTo(panel.page,second.rect().topLeft()))
                        assert not first_rect.intersects(second_rect),(first_rect,second_rect)
                assert window.quest_display=='Quest A' and descriptor(a)['identity'] not in window.quest_summary.text()
                if os.environ.get('QUEST_QT_SHOTS'):
                    shots=Path(os.environ['QUEST_QT_SHOTS']);shots.mkdir(exist_ok=True)
                    window.grab().save(str(shots/f'device-{language}-{os.environ.get("QT_SCALE_FACTOR","1")}.png'))
                provider.started.clear();provider.hold=True;provider.release.clear();panel.read.click();wait(provider.started.is_set)
                window.close();assert panel.closed and not panel.timer.isActive()
                provider.hold=False;provider.release.set();window.wait_closed()
                assert provider.calls and all(thread!=threading.get_ident() for _,thread,_ in provider.calls)
                assert ui_threads and all(thread==threading.get_ident() for thread in ui_threads)
                print('PASS',language,'offline/default/multi/identity/Quest/empty/failure/disconnect/cancel/late/close/DPI')
            finally:provider.hold=False;provider.release.set();window.close();window.wait_closed()
        assert original==hashlib.sha256((song/'Info.dat').read_bytes()).hexdigest()
        # Malformed provider batches are rejected atomically, not accepted as empty.
        provider=Provider();provider.list_devices=lambda cancel: 'bad'
        rejects(lambda:DeviceClient(provider).search({'action':'list'},0,'',threading.Event()))
        assert not external and not errors,(external,errors)
        assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)
    device.scan_quest=old_scan;adb.AdbBridge.__init__=old_bridge;subprocess.Popen=old_popen


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
