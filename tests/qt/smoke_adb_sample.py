"""Bounded acceptance CLI with fake transport; never executes an ADB process."""
import base64
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import threading
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main():
    from quest_tool.adb import AdbError
    from quest_tool.downloads import DownloadError
    from quest_tool.messages import set_language
    from quest_tool.qt_ui.adb_sample import main as cli, sample, SampleProvider
    from quest_tool.qt_ui.adb_metadata import CAPABILITY
    root='/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels'
    state=dict(count=1000,capability=b'QSC_BATCH',hardware='HW',connected=True,fault='',meta=0)
    calls=[];seen=[];active_cancel=threading.Event()
    def forbidden(*args,**kwargs):raise AssertionError('No real process allowed')
    class Bridge:
        def run(self,args,timeout,binary=False):
            assert 0<timeout<=10;calls.append(args)
            if args==['devices','-l']:return 'List of devices attached\n'+('A device\n' if state['connected'] else '')
            command=args[1]
            if command.startswith('getprop '):return 'eureka\noculus\n\nQuest_3\neureka\n'+state['hardware']+'\n'
            if command.startswith('if [ -d '):return root+'\n'
            if command.startswith('cd '):return ''.join(f'song{i:04d}/\n' for i in reversed(range(state['count'])))
            assert args[0]=='exec-out' and binary
            if command==CAPABILITY:return state['capability']
            names=list(dict.fromkeys(re.findall(r'song[0-9]{4}',command)));assert names
            state['meta']+=1;seen.extend(names)
            if state['fault']=='identity':state['hardware']='CHANGED'
            if state['fault']=='disconnect':state['connected']=False
            if state['fault']=='cancel':active_cancel.set()
            if state['fault']=='command':raise AdbError('permission denied')
            def payload(name):
                title='changed' if state['fault']=='mismatch' and command.startswith('[ ! -L ') else name
                return json.dumps({'_songName':title,'_songAuthorName':'Artist','_songDuration':120}).encode()
            if command.startswith('[ ! -L '):return payload(names[0])
            raw=b''.join(f'QSC1:{i}\n'.encode()+base64.b64encode(payload(name))+b'\n:QSC_END:\n' for i,name in enumerate(names))
            return raw[:-1] if state['fault']=='frame' else raw
    class Provider(SampleProvider):
        def _bridge(self,serial=''):return Bridge()
    def reset(**values):
        state.update(count=1000,capability=b'QSC_BATCH',hardware='HW',connected=True,fault='',meta=0)
        state.update(values);calls.clear();seen.clear();active_cancel.clear()
    def expect(call,code):
        try:call()
        except DownloadError as exc:assert exc.code==code,(exc.code,code);return
        raise AssertionError('Expected '+code)
    with patch('subprocess.Popen',forbidden):
        for language in ('zh','en'):
            set_language(language)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                assert cli(['--serial','A','--root',root],provider_factory=forbidden)==0
            assert json.loads(out.getvalue())['executed'] is False and not calls
        for limit in (0,17,True):expect(lambda:sample('A',root,limit,provider_factory=forbidden),'sample_arguments')
        expect(lambda:sample('A','/sdcard/../other',8,provider_factory=forbidden),'sample_arguments')
        for count,limit in ((1000,1),(1000,7),(1000,8),(1000,16),(3,8)):
            reset(count=count);result=sample('A',root,limit,cancel=active_cancel,provider_factory=Provider)
            expected=min(count,limit);assert result['sampled']==expected and result['available_folders']==count
            assert result['equal'] and not result['complete_library'] and result['scope']=='sample_only'
            assert set(seen)=={f'song{i:04d}' for i in range(expected)}
            assert result['batch_verified']==(expected>=8)
            assert len(result['rows'])==len(result['metadata_sha256'])==expected
        reset(capability=b'QSC_SINGLE');result=sample('A',root,provider_factory=Provider)
        assert result['automatic_mode']=='fallback' and not result['batch_verified'] and len(seen)==16
        for fault,code in (('identity','device_rejected'),('disconnect','device_disconnected'),('cancel','cancelled'),
                ('command','device_command'),('frame','device_protocol'),('mismatch','sample_mismatch')):
            reset(fault=fault);expect(lambda:sample('A',root,cancel=active_cancel,provider_factory=Provider),code)
        reset(capability=b'unknown');expect(lambda:sample('A',root,provider_factory=Provider),'device_protocol')
        reset(count=0);expect(lambda:sample('A',root,provider_factory=Provider),'sample_empty')
        reset();active_cancel.set();expect(lambda:sample('A',root,cancel=active_cancel,provider_factory=forbidden),'cancelled')
    assert not any(name.startswith(('PySide6','tkinter','customtkinter')) for name in sys.modules)
    print('PASS sample dry-run/no process, 1..16 bounds, 1000 folders limited metadata, batch/fallback/single, identity/cancel/failure/mismatch')
    return 0


if __name__=='__main__':raise SystemExit(main())
