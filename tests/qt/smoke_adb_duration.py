"""Bounded audio duration, provider/CLI/Qt wiring; no real device or audio playback."""
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtWidgets import QApplication
    from quest_tool.adb import AdbError
    from quest_tool.downloads import DownloadError
    from quest_tool.song_duration import HEAD_LIMIT,TAIL_LIMIT
    from quest_tool.qt_ui.adb_duration import remote_duration
    from quest_tool.qt_ui.adb_sample import SampleProvider,sample
    from quest_tool.qt_ui.library_window import LibraryWindow
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    sys.excepthook=lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    root='/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels'
    head=bytearray(HEAD_LIMIT);head[:4]=b'OggS';head[14:18]=b'ABCD';head[28:35]=b'\x01vorbis';struct.pack_into('<I',head,40,48000)
    tail=bytearray(TAIL_LIMIT);tail[:4]=b'OggS';tail[14:18]=b'ABCD';struct.pack_into('<Q',tail,6,48000*123+24000)
    head=bytes(head);tail=bytes(tail);state={};calls=[];cancel=threading.Event()
    def reset(**values):
        state.clear();state.update(audio='music.egg',duration=0,fault='',hardware='HW',connected=True)
        state.update(values);calls.clear();cancel.clear()
    class Bridge:
        def run(self,args,timeout,binary=False):
            assert 0<timeout<=10;calls.append(args)
            if args==['devices','-l']:return 'List of devices attached\n'+('A device\n' if state['connected'] else '')
            cmd=args[1]
            if cmd.startswith('getprop '):return 'eureka\noculus\n\nQuest_3\neureka\n'+state['hardware']+'\n'
            if cmd.startswith('if [ -d '):return root+'\n'
            if cmd.startswith('cd '):return 'song/\n'
            if cmd.startswith('[ ! -L '):
                return json.dumps({'_songName':'Song','_songFilename':state['audio'],'_songDuration':state['duration']}).encode()
            assert cmd.startswith('[ "$(readlink -f ') and '[ ! -L ' in cmd and '[ -f ' in cmd
            if state['fault']=='cancel':cancel.set()
            if state['fault']=='permission':raise AdbError('permission denied')
            if state['fault']=='timeout':
                error=AdbError('bounded');error.__cause__=subprocess.TimeoutExpired('fake',10);raise error
            assert args[0]=='exec-out' and binary
            assert 'stat -c %s' in cmd and f'head -c {HEAD_LIMIT}' in cmd and f'tail -c {TAIL_LIMIT}' in cmd
            if state['fault'] in ('missing','tiny'):return b''
            if state['fault']=='identity':state['hardware']='OTHER'
            if state['fault']=='disconnect':state['connected']=False
            frame=b'QSD1:999999\n'+(head[:-1] if state['fault']=='short' else head)+tail+b'\n:QSD_END:\n'
            return frame+b'x' if state['fault']=='oversize' else frame
    class Provider(SampleProvider):
        def _bridge(self,serial=''):return Bridge()
    def selected():
        p=Provider();return p,p.list_devices(cancel)[0]
    def read():
        p,item=selected();return p.read_library(item,cancel)['rows'][0]['duration']
    def expect(call,code):
        try:call()
        except DownloadError as exc:assert exc.code==code,(exc.code,code);return
        raise AssertionError('Expected '+code)
    def forbidden(*args,**kwargs):raise AssertionError('No real processes/devices allowed')
    with patch('subprocess.Popen',forbidden):
        reset();assert read()==123.5
        audio_calls=[args for args in calls if 'head -c 65536' in args[-1] or 'tail -c 131072' in args[-1]]
        assert len(audio_calls)==1
        reset(duration=77);assert read()==77 and not any('stat -c' in args[-1] for args in calls)
        for fault in ('missing','tiny','permission','short','oversize'):
            reset(fault=fault);assert read()==0,fault
            assert len([args for args in calls if 'qsd_size=' in args[-1]])==1
        for bad in ('../outside.egg','/outside.egg','x\\y.egg','x\ny.egg',''):
            reset(audio=bad);assert read()==0 and not any('stat -c' in args[-1] for args in calls)
        for fault,code in (('cancel','cancelled'),('timeout','device_timeout'),('identity','device_rejected'),('disconnect','device_disconnected')):
            reset(fault=fault);expect(read,code)
        reset();p=Provider()
        assert remote_duration(p._run,Bridge(),'/outside','song',{'_songFilename':'music.egg'},cancel,time.monotonic()+10)==0 and not calls
        reset();result=sample('A',root,probe_audio=True,provider_factory=Provider)
        assert result['rows'][0]['duration']==123.5 and result['duration_sources']==['audio_header'] and result['audio_duration_requested']
        reset();result=sample('A',root,provider_factory=Provider)
        assert result['rows'][0]['duration']==0 and result['duration_sources']==['unknown'] and not any('stat -c' in args[-1] for args in calls)
        def wait(test):
            end=time.monotonic()+8
            while time.monotonic()<end:
                app.processEvents();time.sleep(.01)
                if test():return
            raise AssertionError('Qt duration timeout')
        with tempfile.TemporaryDirectory() as folder:
            for language in ('zh','en'):
                reset();window=LibraryWindow(Path(folder)/language,language,device_provider=Provider());window.show();panel=window.device_browser
                try:
                    panel.refresh.click();wait(lambda:not panel.busy);panel.choices.setCurrentIndex(1)
                    panel.read.click();wait(lambda:not panel.busy)
                    assert panel.media_deferred
                    wait(lambda:window.quest_model.rows[0]['duration']==123.5)
                    assert panel.state=='ready' and window.quest_model.rows[0]['duration']==123.5
                finally:window.close();window.wait_closed()
    assert not errors,errors
    print('PASS bounded duration, no full tiny files, missing/permission/truncation, path guard, cancel/timeout/identity, CLI opt-in and real Qt rows')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            p=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
            print(scale,p.stdout.decode('utf-8','replace'),p.stderr.decode('utf-8','replace'));assert p.returncode==0
