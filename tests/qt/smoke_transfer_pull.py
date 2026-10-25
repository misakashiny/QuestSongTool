"""Real AdbBridge + AdbFS + Qt import, with a sealed transport and local fixtures."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.transfer_pull import AdbPullBackend
    from quest_tool.qt_ui.transfer_queue import TransferPlan, TransferQueue
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui import bootstrap
    from quest_tool.constants import QUEST_CUSTOMLEVELS_CANDIDATES
    from quest_tool.workers import GuardError
    from quest_tool.downloads import DownloadError
    from quest_tool.fsapi import FSError
    app=QApplication([]);app.setStyle('Fusion');errors=[];external=[]
    def report(kind,value,tb):
        errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    def wait(predicate):
        end=time.monotonic()+12
        while time.monotonic()<end:
            app.processEvents();time.sleep(.005)
            if predicate():return
        raise AssertionError('pull timeout')
    def rejects(fn):
        try:fn()
        except (GuardError,DownloadError,FSError,OSError):return
        raise AssertionError('unsafe import accepted')
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp).resolve();remote=base/'remote';remote.mkdir()
        for name in ('中文歌曲',"song's name",'third'):
            song=remote/name;song.mkdir()
            (song/'Info.dat').write_text(json.dumps({'_songName':name,'_songDuration':90}),encoding='utf-8')
            (song/'song.ogg').write_bytes(b'fixture audio')
        before={p.relative_to(remote).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in remote.rglob('*') if p.is_file()}
        exe=base/'sealed-adb.exe';exe.write_bytes(b'not executable')
        root=QUEST_CUSTOMLEVELS_CANDIDATES[1]
        state=dict(hardware='HW_A',connected=True,root=True,links=False,truncate=False,race=False,switch_after_pull=False)
        calls=[]
        entered=threading.Event();release=threading.Event();hold=False
        def forbidden(*args,**kwargs):external.append(args);raise AssertionError('real process prohibited')
        def local_path(value):
            assert value.startswith(root+'/'),value
            return remote/value[len(root)+1:]
        def fake_run(args,**kwargs):
            assert args[0]==str(exe),args
            serial=args[2] if len(args)>2 and args[1]=='-s' else ''
            cmd=args[3:] if serial else args[1:]
            calls.append((threading.get_ident(),tuple(cmd)))
            assert cmd[0] in ('devices','shell','pull'),cmd
            output=b''
            if cmd==['devices','-l']:
                output=b'List of devices attached\n'+(b'A device\n' if state['connected'] else b'')
            elif cmd[0]=='pull':
                assert serial=='A'
                entered.set()
                if hold:assert release.wait(8)
                source=local_path(cmd[1]);target=Path(cmd[2])/source.name
                shutil.copytree(source,target)
                if state['truncate']:(target/'song.ogg').write_bytes(b'bad')
                if state['race']:
                    # copy_out receives <private>/.payload.partial; its parent is private.
                    destination=Path(cmd[2]).parent.parent/source.name
                    destination.mkdir();(destination/'keep.txt').write_bytes(b'concurrent winner')
                if state['switch_after_pull']:state['hardware']='REPLACED'
            elif cmd[1].startswith('getprop '):
                assert serial=='A'
                output=('eureka\noculus\n\nQuest_3\neureka\n'+state['hardware']+'\n').encode()
            elif cmd[1].startswith('if [ -d '):
                requested=shlex.split(cmd[1])[3]
                output=(root+'\n').encode() if requested==root and state['root'] else b''
            elif cmd[1].startswith('[ ! -L '):
                assert 'find ' in cmd[1] and '-type l -print' in cmd[1]
                output=b'controlled symlink\n' if state['links'] else b''
            elif cmd[1].startswith('[ -d '):
                output=b'YES\n' if local_path(shlex.split(cmd[1])[2]).is_dir() else b'NO\n'
            elif cmd[1].startswith('find '):
                path=local_path(shlex.split(cmd[1])[1])
                output=''.join(str(p.stat().st_size)+'\n' for p in path.rglob('*') if p.is_file()).encode()
            else:raise AssertionError('unapproved remote command: '+repr(cmd))
            return subprocess.CompletedProcess(args,0,stdout=output,stderr=b'')
        with patch('subprocess.run',fake_run),patch('subprocess.Popen',forbidden):
            provider=AdbReadOnlyProvider(str(exe));backend=AdbPullBackend(provider)
            target=base/'local';target.mkdir()
            assert not calls
            selected=provider._descriptor(type('Device',(),dict(serial='A',model='Quest_3',state='device',codename='eureka',brand='oculus'))(),'HW_A',root)
            plan=TransferPlan.create('to_local',target,selected,['中文歌曲',"song's name"])
            queue=TransferQueue(backend);queue.start(plan);queue.wait_closed()
            assert queue.tasks.drain()[-1].payload['ok']==2
            assert (target/'中文歌曲'/'Info.dat').read_bytes()==(remote/'中文歌曲'/'Info.dat').read_bytes()
            assert not list(target.glob('.quest-pull-*'))
            original=(target/'中文歌曲'/'Info.dat').read_bytes()
            queue.start(plan);queue.wait_closed();assert queue.tasks.drain()[-1].payload['skipped']==2
            assert (target/'中文歌曲'/'Info.dat').read_bytes()==original
            rejects(lambda:backend.revalidate(TransferPlan.create('to_quest',target,selected,['third'])))
            for key,value in (('links',True),('truncate',True),('switch_after_pull',True),('root',False),('connected',False),('race',True)):
                destination=base/('case-'+key);destination.mkdir()
                saved=copy.deepcopy(state);state[key]=value
                single=TransferPlan.create('to_local',destination,selected,['third'])
                rejects(lambda:backend.copy_verified(single,'third'))
                if key=='race':assert (destination/'third'/'keep.txt').read_bytes()==b'concurrent winner'
                else:assert not (destination/'third').exists(),key
                assert not list(destination.glob('.quest-pull-*')),key
                state.clear();state.update(saved)
            # Actual widgets and coordinator: no import before explicit confirmation.
            ui_start=len(calls)
            language=os.environ.get('TEST_LANG','zh')
            window=LibraryWindow(base/'preview',language,device_provider=provider,transfer_backend=backend)
            window.show()
            try:
                assert not window.transfers.pull.isEnabled()
                local=base/'ui-target';local.mkdir();assert window.set_target(str(local));wait(lambda:window.local_ready)
                browser=window.device_browser;browser.selected=selected
                rows=[dict(folder=name,title=name,author='Artist',duration=90,error='') for name in ('中文歌曲',"song's name",'third')]
                window.set_quest_snapshot(rows,selected['identity'])
                panel=window.transfers;panel.select.click();assert len(window.quest_model.checked)==3
                panel.pull.click();dialog=panel.dialog;assert dialog is not None
                assert not list(local.iterdir()) and dialog.generation is None
                dialog.start.click();wait(lambda:dialog.result is not None)
                assert dialog.result['ok']==3
                wait(lambda:window.local_ready and len(window.model.rows)==3)
                assert not any(thread==threading.get_ident() for thread,cmd in calls[ui_start:] if cmd[0]=='pull')
                dialog.close();wait(lambda:panel.dialog is None)
                # A prepared import cannot start after the UI changes its selected root.
                panel.pull.click();dialog=panel.dialog
                assert dialog is not None
                window.clear_quest_snapshot();dialog.start.click()
                assert dialog.generation is None
                dialog.close()
                # Missing view uses the real Quest origin, not the local side.
                window.set_quest_snapshot(rows,selected['identity'])
                window.missing_side.setCurrentIndex(window.missing_side.findData('quest'))
                window.missing_model.checked.add('third');panel.render();assert not panel.missing.isEnabled()
                other=base/'ui-empty';other.mkdir();window.set_target(str(other));wait(lambda:window.local_ready)
                window.missing_side.setCurrentIndex(window.missing_side.findData('local'))
                window.missing_model.checked.add('third');panel.render();assert panel.missing.isEnabled()
                panel.missing.click();assert panel.dialog.plan.folders==('third',)
                panel.dialog.close()
                # Closing during pull still refreshes the matching local view
                # after the current song completes, without updating a dead dialog.
                window.quest_model.checked={'third'};panel.render()
                hold=True;entered.clear();panel.pull.click();dialog=panel.dialog
                dialog.start.click();wait(entered.is_set);dialog.close();release.set()
                wait(lambda:window.local_ready and any(row['folder']=='third' for row in window.model.rows))
                hold=False
                for width in (1000,1400):
                    window.resize(width,850);app.processEvents()
                    for button in (panel.select,panel.clear,panel.pull,panel.missing):
                        assert button.width()>40 and button.height()>0
                        assert panel.page.rect().contains(button.geometry()),(button.text(),button.geometry(),panel.page.rect())
                shots=os.environ.get('QUEST_QT_SHOTS')
                if shots:
                    path=Path(shots);path.mkdir(parents=True,exist_ok=True)
                    window.grab().save(str(path/f'pull-{language}-{os.environ.get("QT_SCALE_FACTOR","1")}.png'))
            finally:window.close();window.wait_closed()
            # New opt-in still rejects inconsistent CLI flags before application creation.
            for argv in (['--quest-pull'],['--library','--quest-pull']):
                try:bootstrap.run(argv)
                except SystemExit as exc:assert exc.code==2
                else:raise AssertionError('pull opt-in accepted without read provider')
            before_entry=len(calls)
            def entry_exec(_app):
                entry=next(w for w in app.topLevelWidgets() if isinstance(w,LibraryWindow) and not w.closed)
                assert isinstance(entry.transfers.backend,AdbPullBackend)
                assert not entry.transfers.pull.isEnabled() and len(calls)==before_entry
                entry.close();return 0
            with patch.object(bootstrap,'QApplication',lambda *_args:app),patch.object(QApplication,'exec',entry_exec):
                assert bootstrap.run(['--library','--quest-adb','--quest-pull','--adb-path',str(exe),'--data-dir',str(base/'cli')])==0
            assert before=={p.relative_to(remote).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in remote.rglob('*') if p.is_file()}
            assert not external and not errors,(external,errors)
            assert not any(n.startswith(('tkinter','customtkinter')) for n in sys.modules)
            print('PASS pull: real bridge/FS, no remote writes, stage/verify/publish, race/identity/failure/skip, Qt confirm/missing/refresh/CLI/layout')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for language in ('zh','en'):
            for scale in ('1','1.25','1.5'):
                result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                    env=dict(os.environ,TEST_LANG=language,QT_SCALE_FACTOR=scale),capture_output=True,timeout=100)
                print(language,scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True)
                assert result.returncode==0
