"""Metadata round trips, fallback and actual local POSIX shell framing; no ADB I/O."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtWidgets import QApplication
    from quest_tool.adb import AdbError
    from quest_tool.downloads import DownloadError
    from quest_tool.qt_ui.adb_metadata import batches,decode_batch,CAPABILITY,COMMAND_LIMIT,INFO_LIMIT
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.library_window import LibraryWindow
    root='/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels'
    cancel=threading.Event();calls=[];state=dict(capability=b'QSC_BATCH',count=1000,hardware='HW',connected=True,fault='',cancel=False)
    def payload(name):return json.dumps({'_songName':name,'_songAuthorName':'Artist','_songDuration':123}).encode()
    class Bridge:
        def __init__(self,*_args,serial='',**_kwargs):self.serial=serial
        def run(self,args,timeout,binary=False):
            assert 0<timeout<=10;calls.append((threading.get_ident(),list(args)))
            if args==['devices','-l']:return 'List of devices attached\n'+('A device\n' if state['connected'] else '')
            command=args[1]
            if command.startswith('getprop '):return 'eureka\noculus\n\nQuest_3\neureka\n'+state['hardware']+'\n'
            if command.startswith('if [ -d '):return root+'\n'
            if command.startswith('cd '):return ''.join(f'song{i:04d}/\n' for i in range(state['count']))
            assert args[0]=='exec-out' and binary
            if command==CAPABILITY:return state['capability']
            names=list(dict.fromkeys(re.findall(r'/song[0-9]{4}',command)));names=[n[1:] for n in names]
            assert names and 'head -c 1048577' in command and 'readlink -f' in command
            if command.startswith('[ ! -L '):return payload(names[0])
            assert command.startswith('set -o pipefail') and len(command.encode())<=COMMAND_LIMIT
            if state['fault']=='permission':raise AdbError('controlled read failure')
            raw=b''.join(f'QSC1:{i}\n'.encode()+base64.b64encode(payload(name))+b'\n:QSC_END:\n' for i,name in enumerate(names))
            if state['fault']=='corrupt':raw=raw[:-1]
            if state['fault']=='identity':state['hardware']='REPLACED'
            if state['fault']=='disconnect':state['connected']=False
            if state['cancel']:cancel.set()
            return raw
    provider=AdbReadOnlyProvider(bridge_factory=Bridge)
    def rejects(fn):
        try:fn()
        except (DownloadError,AdbError):return
        raise AssertionError('Malformed or stale batch accepted')
    def metadata_count(prefix):return sum(args[0]=='exec-out' and args[1].startswith(prefix) for _,args in calls)
    selected=provider.list_devices(cancel)[0];calls.clear()
    result=provider.read_library(selected,cancel);batch_calls=metadata_count('set -o pipefail')
    assert len(result['rows'])==1000 and batch_calls<=80 and metadata_count('[ ! -L ')==0
    calls.clear();state['capability']=b'QSC_SINGLE';fallback=provider.read_library(selected,cancel)
    assert fallback['rows']==result['rows'] and metadata_count('[ ! -L ')==1000
    state.update(capability=b'QSC_BATCH',count=20)
    for fault in ('corrupt','permission','identity','disconnect'):
        state.update(fault=fault,hardware='HW',connected=True);calls.clear()
        rejects(lambda:provider.read_library(selected,cancel));assert metadata_count('[ ! -L ')==0
    state.update(fault='',hardware='HW',connected=True,cancel=True);calls.clear()
    rejects(lambda:provider.read_library(selected,cancel));assert metadata_count('set -o pipefail')==1
    cancel.clear();state['cancel']=False
    state['capability']=b'unknown';rejects(lambda:provider.read_library(selected,cancel));state['capability']=b'QSC_BATCH'
    valid=b'QSC1:0\n'+base64.b64encode(b'hello\x00\n:QSC_END:\n')+b'\n:QSC_END:\n'
    assert decode_batch(valid,['one'])==[('one',b'hello\x00\n:QSC_END:\n')]
    for bad in (valid[:-1],valid+b'junk',valid.replace(b'QSC1:0',b'QSC1:1'),b'QSC1:0\n%%\n:QSC_END:\n',b'QSC1:0\n'+base64.b64encode(b'x'*(INFO_LIMIT+1))+b'\n:QSC_END:\n'):
        rejects(lambda value=bad:decode_batch(value,['one']))
    long_names=['中文歌曲'+str(i)+'名'*150 for i in range(50)]
    chunks=list(batches(root,long_names));assert sum(len(names) for names,_ in chunks)==50
    assert all(len(names)<=16 and len(command.encode())<=COMMAND_LIMIT for names,command in chunks)
    rejects(lambda:list(batches(root,['x'*20000])))
    # Execute the generated shell against disposable local files, not a device.
    bash=Path(r'C:\Program Files\Git\bin\bash.exe');assert bash.is_file(),'Local Git bash is required for shell contract validation'
    with tempfile.TemporaryDirectory() as tmp:
        directory=Path(tmp).resolve();names=["quote's name",'dollar $ name','missing']
        for name in names:(directory/name).mkdir()
        raw_data=b'raw\x00\n:QSC_END:\nbytes'
        for name in names[:2]:(directory/name/'Info.dat').write_bytes(raw_data)
        posix='/'+directory.drive[0].lower()+directory.as_posix()[2:]
        probe=subprocess.run([str(bash),'--noprofile','--norc','-c','PATH=/usr/bin:/bin:$PATH; '+CAPABILITY],capture_output=True,timeout=15)
        assert probe.returncode==0 and probe.stdout==b'QSC_BATCH'
        probe=subprocess.run([str(bash),'--noprofile','--norc','-c','PATH=/nonexistent; '+CAPABILITY],capture_output=True,timeout=15)
        assert probe.returncode==0 and probe.stdout==b'QSC_SINGLE'
        command='PATH=/usr/bin:/bin:$PATH; '+list(batches(posix,names))[0][1]
        shell=subprocess.run([str(bash),'--noprofile','--norc','-c',command],capture_output=True,timeout=15)
        assert shell.returncode==0,shell.stderr
        assert decode_batch(shell.stdout,names)==[(names[0],raw_data),(names[1],raw_data),(names[2],b'')]
        failure=subprocess.run([str(bash),'--noprofile','--norc','-c','head(){ return 7; }; '+command],capture_output=True,timeout=15)
        assert failure.returncode!=0,'head failure was swallowed by a successful base64 pipeline'
        # The actual provider still flows through the existing Qt main-thread pump.
        app=QApplication([]);app.setStyle('Fusion');main=threading.get_ident()
        window=LibraryWindow(directory/'preview',language=os.environ.get('TEST_LANG','zh'),device_provider=provider);window.show()
        def forbidden(*args,**kwargs):raise AssertionError('Real process execution is forbidden in Qt provider tests')
        def wait(test):
            end=time.monotonic()+10
            while time.monotonic()<end:
                app.processEvents();time.sleep(.01)
                if test():return
            raise AssertionError('Batch Qt timeout')
        try:
            with patch('subprocess.Popen',forbidden):
                browser=window.device_browser;browser.discover();wait(lambda:not browser.busy)
                browser.choices.setCurrentIndex(1);calls.clear();browser.read_selected();wait(lambda:not browser.busy)
                assert browser.state=='ready' and window.quest_ready and metadata_count('set -o pipefail')==2
                assert all(t!=main for t,_ in calls)
        finally:window.close();window.wait_closed()
    print(f'PASS 1000 songs: {batch_calls} batch payload calls versus 1000 fallback payload calls; identical rows; framing/limits/UTF8/pipefail/cancel/identity and actual local shell + Qt transport')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for language in ('zh','en'):
            for scale in ('1','1.25','1.5'):
                run=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale,TEST_LANG=language),capture_output=True,timeout=90)
                print(language,scale,run.stdout.decode('utf-8','replace'),run.stderr.decode('utf-8','replace'),flush=True)
                assert run.returncode==0
