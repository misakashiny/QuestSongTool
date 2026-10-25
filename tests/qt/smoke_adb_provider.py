"""Production AdbBridge argv against a sealed fake transport, never a real device."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.adb import AdbError
    from quest_tool.constants import BACKUP_SUFFIX, QUEST_CUSTOMLEVELS_CANDIDATES
    from quest_tool.downloads import DownloadError
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui import bootstrap
    app = QApplication([]);app.setStyle('Fusion');main = threading.get_ident()
    root = QUEST_CUSTOMLEVELS_CANDIDATES[1]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp);exe = base/'fake-adb.exe';exe.write_bytes(b'never execute')
        payload = base/'Info.dat';payload.write_text(json.dumps({'_songName':'真实解析标题','_songAuthorName':'Artist','_songDuration':123}),encoding='utf-8')
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        calls = [];external = [];state = dict(hardware='HW_A',connected=True,root=root,names=["song's name",'missing','broken','.hidden','old'+BACKUP_SUFFIX],switch=False,big=False,deny=False,cancel=None)
        def fake_run(args, **kwargs):
            assert args[0]==str(exe),args
            assert 0<kwargs['timeout']<=10 and kwargs['capture_output'] is True
            calls.append((threading.get_ident(),list(args),kwargs['timeout']))
            serial = args[2] if len(args)>2 and args[1]=='-s' else ''
            command = args[3:] if serial else args[1:]
            code = 0
            if command==['devices','-l']:
                text = 'List of devices attached\n'+('A device model:Quest_3\nB device model:Quest_3\nphone device model:Quest_3\noff offline\n' if state['connected'] else '')
                output = text.encode()
            elif command[0]=='shell' and command[1].startswith('getprop '):
                assert serial in ('A','B','phone')
                values = ['eureka','oculus','','Quest_3','eureka',state['hardware'] if serial=='A' else 'HW_B'] if serial!='phone' else ['elish','Xiaomi','','Pad','elish','PHONE']
                output = ('\n'.join(values)+'\n').encode()
            elif command[0]=='shell' and command[1].startswith('if [ -d '):
                assert serial in ('A','B')
                requested = shlex.split(command[1])[3]
                output = ((state['root']+'\n') if requested in QUEST_CUSTOMLEVELS_CANDIDATES[:2] and state['root'] else '').encode()
            elif command==['shell',f'cd {shlex.quote(root)} && ls -1p']:
                output = ''.join(name+'/\n' for name in state['names']).encode()
                if state['deny']:code=1
            elif command[0]=='exec-out':
                assert serial in ('A','B')
                text = command[1]
                assert text.startswith('[ ! -L ') and 'readlink -f' in text and 'head -c 1048577' in text
                if state['deny']:code=1
                output = b'x'*1048577 if state['big'] else b'bad json' if '/broken' in text else b'' if '/missing' in text else payload.read_bytes()
                if state['switch']:state['hardware']='REPLACED'
                if state['cancel'] is not None:state['cancel'].set()
            else:raise AssertionError('Forbidden ADB operation: '+repr(command))
            return subprocess.CompletedProcess(args,code,stdout=output,stderr=b'controlled failure' if code else b'')
        def forbidden(*args,**kwargs):external.append(args);raise AssertionError('Real process execution prohibited')
        def wait(test):
            end=time.monotonic()+10
            while time.monotonic()<end:
                app.processEvents();QTest.qWait(10)
                if test():return
            raise AssertionError('ADB provider UI timeout')
        def rejects(fn):
            try:fn()
            except (DownloadError,AdbError):return
            raise AssertionError('Unsafe snapshot accepted')
        cancel = threading.Event()
        with patch('subprocess.run',fake_run),patch('subprocess.Popen',forbidden):
            provider = AdbReadOnlyProvider(str(exe));assert not calls
            choices = provider.list_devices(cancel)
            assert len(choices)==2 and all(x['root']==root for x in choices)
            state['root']=QUEST_CUSTOMLEVELS_CANDIDATES[2].replace('/sdcard/','/storage/emulated/0/',1)
            assert all(x['root']==state['root'] for x in provider.list_devices(cancel))
            state['root']=root
            chosen = next(x for x in choices if json.loads(x['device_id'])[0]=='A')
            result = provider.read_library(chosen,cancel)
            rows={row['folder']:row for row in result['rows']}
            assert set(rows)=={"song's name",'missing','broken'}
            assert rows["song's name"]['title']=='真实解析标题' and rows["song's name"]['duration']==123
            assert rows['missing']['error'] and rows['broken']['error']
            assert not any(BACKUP_SUFFIX in args[-1] or '/.hidden' in args[-1] for _,args,_ in calls if 'exec-out' in args)
            before_calls=len(calls);cancel.set();rejects(lambda:provider.list_devices(cancel));assert len(calls)==before_calls;cancel.clear()
            for changes in ({'switch':True},{'root':'/outside'},{'connected':False},{'names':['..']},{'names':['same','same']},{'names':['bad\tname']},{'big':True},{'deny':True}):
                saved=copy.deepcopy(state);state.update(changes);rejects(lambda:provider.read_library(chosen,cancel));state.clear();state.update(saved)
            state['cancel']=cancel;rejects(lambda:provider.read_library(chosen,cancel));cancel.clear();state['cancel']=None
            for value in ('{}','"AB"','["-option","HW_A"]'):
                bad=dict(chosen,device_id=value);before_calls=len(calls);rejects(lambda:provider.read_library(bad,cancel));assert len(calls)==before_calls
            with patch('quest_tool.qt_ui.adb_provider.time.monotonic',side_effect=[0,121]):
                rejects(lambda:provider.list_devices(cancel))
            # Default constructor does not enable or execute any provider.
            window=LibraryWindow(base/'default');window.show();app.processEvents()
            assert window.device_browser.session is None;window.close();window.wait_closed()
            before_calls=len(calls)
            window=LibraryWindow(base/'preview',language=os.environ.get('TEST_LANG','zh'),device_provider=provider);window.show();app.processEvents()
            browser=window.device_browser
            try:
                assert len(calls)==before_calls and not browser.read.isEnabled()
                QTest.mouseClick(browser.refresh,Qt.MouseButton.LeftButton);wait(lambda:not browser.busy)
                assert browser.state=='choose' and len(browser.devices)==2 and browser.selected is None
                browser.choices.setCurrentIndex(1);assert browser.read.isEnabled()
                before_calls=len(calls);QTest.mouseClick(browser.read,Qt.MouseButton.LeftButton);wait(lambda:not browser.busy)
                assert browser.state=='ready' and window.quest_ready
                assert all(t!=main for t,_,_ in calls[before_calls:])
                state['hardware']='REPLACED';QTest.mouseClick(browser.read,Qt.MouseButton.LeftButton);wait(lambda:not browser.busy)
                assert browser.state=='rejected' and not window.quest_ready;state['hardware']='HW_A'
                browser.cancel();assert not window.quest_ready
                for control in (browser.choices,browser.refresh,browser.read,browser.clear):
                    assert control.width()>20 and control.height()>0
            finally:window.close();window.wait_closed()
            for args in (['--quest-adb'],['--adb-path',str(exe)],['--adb-path','']):
                try:bootstrap.run(args)
                except SystemExit as exc:assert exc.code==2
                else:raise AssertionError('Invalid opt-in accepted')
            # Exercise the actual valid CLI -> constructor -> provider wiring.
            def entry_exec(_app):
                windows=[w for w in app.topLevelWidgets() if isinstance(w,LibraryWindow) and not w.closed]
                assert len(windows)==1
                entry=windows[0];controller=entry.device_browser
                assert controller.session is not None and len(calls)==entry_calls
                QTest.mouseClick(controller.refresh,Qt.MouseButton.LeftButton);wait(lambda:not controller.busy)
                assert controller.state=='choose' and controller.selected is None
                controller.choices.setCurrentIndex(1)
                QTest.mouseClick(controller.read,Qt.MouseButton.LeftButton);wait(lambda:not controller.busy)
                assert controller.state=='ready' and entry.quest_ready
                entry.close();return 0
            entry_calls=len(calls)
            with patch.object(bootstrap,'QApplication',lambda *_args:app),patch.object(QApplication,'exec',entry_exec):
                assert bootstrap.run(['--library','--quest-adb','--adb-path',str(exe),'--data-dir',str(base/'entry'),'--lang',os.environ.get('TEST_LANG','zh')])==0
            assert not external and hashlib.sha256(payload.read_bytes()).hexdigest()==digest
            print('PASS real AdbBridge argv, fresh props/hardware, root aliases, metadata limits, malformed/missing info, cancel/deadline/disconnect/replacement, explicit Qt controls and default no I/O')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for language in ('zh','en'):
            for scale in ('1','1.25','1.5'):
                run=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale,TEST_LANG=language),capture_output=True,timeout=60)
                print(language,scale,run.stdout.decode('utf-8','replace'),run.stderr.decode('utf-8','replace'),flush=True)
                assert run.returncode==0
