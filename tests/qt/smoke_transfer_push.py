"""Actual bridge/FS/push coordinator, sealed local transport; never real ADB."""
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
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.transfer_push import AdbPushBackend
    from quest_tool.qt_ui.transfer_pull import AdbPullBackend
    from quest_tool.qt_ui.transfer_queue import TransferPlan,TransferQueue
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui import bootstrap
    from quest_tool.constants import QUEST_CUSTOMLEVELS_CANDIDATES
    from quest_tool.workers import GuardError
    from quest_tool.downloads import DownloadError
    from quest_tool.fsapi import FSError
    app=QApplication([]);app.setStyle('Fusion');errors=[];external=[]
    def report(kind,value,tb):errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    def wait(predicate):
        end=time.monotonic()+12
        while time.monotonic()<end:
            app.processEvents();time.sleep(.005)
            if predicate():return
        raise AssertionError('push timeout')
    def rejects(call):
        try:call()
        except (GuardError,DownloadError,FSError,OSError):return
        raise AssertionError('unsafe push accepted')
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp).resolve();remote=base/'remote';remote.mkdir();local=base/'local';local.mkdir()
        for name in ('中文歌曲',"song's name",'existing','bad','race','replace','hardware','capability','ui'):
            folder=local/name;folder.mkdir();(folder/'Info.dat').write_text(json.dumps({'_songName':name,'_songDuration':90}),encoding='utf-8');(folder/'audio.ogg').write_bytes(b'new audio data')
        for name in ('existing','replace'):
            folder=remote/name;folder.mkdir();(folder/'old.txt').write_bytes(b'original must survive')
        source_hash={p.relative_to(local).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in local.rglob('*') if p.is_file()}
        root=QUEST_CUSTOMLEVELS_CANDIDATES[1];exe=base/'sealed-adb.exe';exe.write_bytes(b'never execute')
        state=dict(hardware='HW_A',capability=True,truncate=False,race=False,fail_publish=False,switch=False)
        calls=[]
        def mapped(path):
            assert path==root or path.startswith(root+'/'),path
            result=remote/path[len(root):].lstrip('/');assert result.resolve().is_relative_to(remote)
            return result
        def fake_run(args,**kwargs):
            assert args[0]==str(exe)
            serial=args[2] if len(args)>2 and args[1]=='-s' else ''
            cmd=args[3:] if serial else args[1:];calls.append((threading.get_ident(),tuple(cmd)))
            output=b'';code=0
            if cmd==['devices','-l']:output=b'List of devices attached\nA device\n'
            elif cmd[0]=='push':
                assert serial=='A' and Path(cmd[1]).name=='payload' and cmd[1].isascii()
                assert '/.quest-transfer-' in cmd[2] and mapped(cmd[2]).is_dir()
                target=mapped(cmd[2])/'payload';shutil.copytree(cmd[1],target)
                if state['truncate']:(target/'audio.ogg').write_bytes(b'bad')
                if state['switch']:state['hardware']='REPLACED'
            elif cmd[0]=='shell':
                text=cmd[1];tokens=shlex.split(text)
                assert not any(token in ('rm','push','-rf') for token in tokens),text
                if text.startswith('getprop '):output=('eureka\noculus\n\nQuest_3\neureka\n'+state['hardware']+'\n').encode()
                elif text.startswith('if [ -d '):output=(root+'\n').encode()
                elif text=='mv --help':output=b'-T no target directory; -n never overwrite' if state['capability'] else b'unsupported'
                elif text.startswith('if [ -e '):output=b'QSC_EXISTS' if os.path.lexists(mapped(tokens[3])) else b'QSC_EMPTY'
                elif text.startswith('[ -e '):output=b'YES' if mapped(tokens[2]).exists() else b'NO'
                elif text.startswith('[ -d '):output=(b'QSC_DIRECTORY' if 'QSC_DIRECTORY' in tokens else b'YES') if mapped(tokens[2]).is_dir() else b'NO'
                elif text.startswith('find '):output=''.join(str(p.stat().st_size)+'\n' for p in mapped(tokens[1]).rglob('*') if p.is_file()).encode()
                elif 'mkdir' in tokens:
                    target=tokens[tokens.index('mkdir')+1];assert '/.quest-transfer-' in target
                    mapped(target).mkdir();output=b'QSC_CREATED'
                elif 'mv' in tokens:
                    index=tokens.index('mv');assert tokens[index+1]=='-nT'
                    src,dst=tokens[index+2:index+4];publishing=src.endswith('/payload')
                    if publishing and state['race']:
                        mapped(dst).mkdir();(mapped(dst)/'winner.txt').write_bytes(b'concurrent winner')
                    if (publishing and state['fail_publish']) or mapped(dst).exists():code=1
                    else:os.rename(mapped(src),mapped(dst));output=b'QSC_MOVED'
                elif 'rmdir' in tokens:
                    target=tokens[tokens.index('rmdir')+1];assert '/.quest-transfer-' in target
                    mapped(target).rmdir();output=b'QSC_CLEAN'
                elif text.endswith('printf QSC_STAGE'):output=b'QSC_STAGE'
                else:raise AssertionError('unexpected shell: '+text)
            else:raise AssertionError('unexpected ADB argv: '+repr(cmd))
            return subprocess.CompletedProcess(args,code,stdout=output,stderr=b'controlled failure' if code else b'')
        def forbidden(*args,**kwargs):external.append(args);raise AssertionError('real process prohibited')
        with patch('subprocess.run',fake_run),patch('subprocess.Popen',forbidden):
            provider=AdbReadOnlyProvider(str(exe));backend=AdbPushBackend(provider,base/'journal')
            selected=provider._descriptor(type('Device',(),dict(serial='A',state='device',model='Quest_3',codename='eureka',brand='oculus'))(),'HW_A',root)
            def plan(name):return TransferPlan.create('to_quest',local,selected,[name])
            queue=TransferQueue(backend);queue.start(TransferPlan.create('to_quest',local,selected,['中文歌曲',"song's name",'existing']));queue.wait_closed()
            result=queue.tasks.drain()[-1].payload;assert result['ok']==2 and result['skipped']==1,result
            assert (remote/'existing'/'old.txt').read_bytes()==b'original must survive'
            assert (remote/'中文歌曲'/'Info.dat').read_bytes()==(local/'中文歌曲'/'Info.dat').read_bytes()
            collision=remote/('.quest-transfer-'+'0'*32);collision.mkdir();(collision/'foreign.txt').write_bytes(b'not ours')
            with patch('quest_tool.qt_ui.transfer_push.uuid.uuid4',return_value=SimpleNamespace(hex='0'*32)):
                rejects(lambda:backend.copy_verified(plan('bad'),'bad'))
            assert (collision/'foreign.txt').read_bytes()==b'not ours'
            for name,setting in (('bad',{'truncate':True}),('race',{'race':True}),('hardware',{'switch':True}),('capability',{'capability':False})):
                old=copy.deepcopy(state);state.update(setting);count=len(calls)
                rejects(lambda:backend.copy_verified(plan(name),name))
                if name=='race':assert (remote/name/'winner.txt').read_bytes()==b'concurrent winner'
                else:assert not (remote/name).exists()
                if name=='capability':assert not any(cmd[0]=='push' or 'mkdir' in cmd[-1] for _,cmd in calls[count:])
                state.clear();state.update(old)
            # Backup-capable primitive is direct API only; normal queue/UI never enables overwrite.
            replacing=AdbPushBackend(provider,base/'replace-journal',replace_existing=True)
            state['fail_publish']=True
            rejects(lambda:replacing.copy_verified(plan('replace'),'replace'))
            receipt=json.loads(next((base/'replace-journal').glob('*.json')).read_text(encoding='utf-8'))
            assert receipt['state']=='publish_intent' and receipt['recovery_required']
            assert (mapped(receipt['backup'])/'old.txt').read_bytes()==b'original must survive'
            assert not (remote/'replace').exists() and mapped(receipt['stage']+'/payload').is_dir()
            # Manual safe restore only in fixture, then a successful backup publication.
            os.rename(mapped(receipt['backup']),remote/'replace');state['fail_publish']=False
            assert replacing.copy_verified(plan('replace'),'replace') is True
            receipts=[json.loads(p.read_text(encoding='utf-8')) for p in (base/'replace-journal').glob('*.json')]
            committed=next(x for x in receipts if x['state']=='committed')
            assert (mapped(committed['backup'])/'old.txt').read_bytes()==b'original must survive'
            assert (remote/'replace'/'Info.dat').read_bytes()==(local/'replace'/'Info.dat').read_bytes()
            language=os.environ.get('TEST_LANG','zh');ui_start=len(calls)
            window=LibraryWindow(base/'preview',language,device_provider=provider,transfer_backend=AdbPullBackend(provider),push_backend=backend);window.show()
            try:
                assert window.set_target(str(local));wait(lambda:window.local_ready)
                window.device_browser.selected=selected;window.set_quest_snapshot([],selected['identity'])
                window.model.checked={'ui'};window.transfers.render();assert window.transfers.push.isEnabled()
                window.transfers.push.click();dialog=window.transfers.dialog
                assert dialog is not None and dialog.plan.direction=='to_quest' and len(calls)==ui_start
                dialog.start.click();wait(lambda:dialog.result is not None)
                assert dialog.result['ok']==1 and not window.quest_ready
                assert not window.transfers.push.isEnabled() and (remote/'ui'/'Info.dat').exists()
                dialog.close()
                # Both transfer directions can be enabled; missing origin cannot flip a push.
                window.set_quest_snapshot([],selected['identity']);window.missing_side.setCurrentIndex(window.missing_side.findData('quest'))
                window.missing_model.checked={'existing'};window.transfers.render();assert window.transfers.push_missing.isEnabled()
                window.transfers.push_missing.click();assert window.transfers.dialog.plan.folders==('existing',)
                window.transfers.dialog.close()
                window.missing_side.setCurrentIndex(window.missing_side.findData('local'));window.transfers.render();assert not window.transfers.push_missing.isEnabled()
                for width in (1000,1400):
                    window.resize(width,850);app.processEvents()
                    for button in (window.transfers.select,window.transfers.clear,window.transfers.pull,window.transfers.missing,window.transfers.push,window.transfers.push_missing):
                        assert button.width()>40 and window.transfers.page.rect().contains(button.geometry())
                shots=os.environ.get('QUEST_QT_SHOTS')
                if shots:
                    path=Path(shots);path.mkdir(parents=True,exist_ok=True);window.grab().save(str(path/f'push-{language}-{os.environ.get("QT_SCALE_FACTOR","1")}.png'))
            finally:window.close();window.wait_closed()
            assert all(thread!=threading.get_ident() for thread,cmd in calls[ui_start:] if cmd[0]=='push')
            for argv in (['--quest-push'],['--library','--quest-push']):
                try:bootstrap.run(argv)
                except SystemExit as exc:assert exc.code==2
                else:raise AssertionError('missing read provider accepted')
            count=len(calls)
            def entry_exec(_app):
                entry=next(w for w in app.topLevelWidgets() if isinstance(w,LibraryWindow) and not w.closed)
                assert isinstance(entry.transfers.push_backend,AdbPushBackend) and entry.transfers.backend is None
                assert not entry.transfers.push.isEnabled() and len(calls)==count
                entry.close();return 0
            with patch.object(bootstrap,'QApplication',lambda *_args:app),patch.object(QApplication,'exec',entry_exec):
                assert bootstrap.run(['--library','--quest-adb','--quest-push','--adb-path',str(exe),'--data-dir',str(base/'entry')])==0
            assert source_hash=={p.relative_to(local).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in local.rglob('*') if p.is_file()}
            assert not external and not errors,(external,errors)
            assert not any(n.startswith(('tkinter','customtkinter')) for n in sys.modules)
            print('PASS staged push: real bridge/FS, ASCII, skip, capability, verify, race, hardware, retained backup/recovery journal, Qt confirmation/CLI/missing/geometry')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for language in ('zh','en'):
            for scale in ('1','1.25','1.5'):
                result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,TEST_LANG=language,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
                print(language,scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True)
                assert result.returncode==0
