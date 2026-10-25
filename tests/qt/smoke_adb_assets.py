"""Pure fake-bridge draft checks; no subprocess, GUI, audio or network."""
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image
from quest_tool.adb import AdbDevice, AdbError
from quest_tool.downloads import DownloadError
from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
from quest_tool.qt_ui import adb_assets as assets
from quest_tool.song_duration import HEAD_LIMIT, TAIL_LIMIT

DEVICE_ROOT = '/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels'
INFO = dict(_songName='Song', _songSubName='Sub', _songAuthorName='Artist', _beatsPerMinute=120,
            _version='2.1.0', _coverImageFilename="cover ' 中文 $(unused);.png", _songFilename='song.egg')
RAW_INFO = json.dumps(INFO, ensure_ascii=False).encode('utf-8')
ROW = dict(folder="folder ' 中文 $(unused);", title='Song', author='Artist', duration=0, error='',
           bpm=120, subtitle='Sub', version='2.1.0', cover_file=INFO['_coverImageFilename'],
           info_hash=hashlib.sha256(RAW_INFO).hexdigest(), need_cover=True, need_duration=True)
buffer=io.BytesIO()
Image.new('RGBA', (32,16), (3,5,7,255)).save(buffer,format='PNG')
PNG=buffer.getvalue()
HEAD=bytearray(HEAD_LIMIT)
HEAD[:4]=b'OggS';HEAD[14:18]=b'ABCD';HEAD[28:35]=b'\x01vorbis';struct.pack_into('<I',HEAD,40,48000)
TAIL=bytearray(TAIL_LIMIT)
TAIL[:4]=b'OggS';TAIL[14:18]=b'ABCD';struct.pack_into('<Q',TAIL,6,48000*123+24000)


class Bridge:
    def __init__(self, state):self.state=state

    def run(self, args, timeout, binary=False):
        state=self.state
        assert 0<timeout<=10
        state['calls'].append(args)
        if args==['devices','-l']:
            return 'List of devices attached\n'+('A device\n' if state.get('connected',True) else '')
        command=args[1]
        if command.startswith('getprop '):
            props={'ro.product.model':'Quest_3','ro.product.device':'eureka','ro.product.name':'eureka',
                   'ro.product.brand':'oculus','ro.product.manufacturer':'oculus','ro.serialno':state.get('hardware','HW')}
            return '\n'.join(props.get(part.split(' ',1)[1],'') for part in command.split('; '))+'\n'
        if command.startswith('if [ -d '):return DEVICE_ROOT+'\n'
        if command.startswith('cd '):return (ROW['folder']+'/\n').encode('utf-8')
        assert binary and args[0]=='exec-out'
        if command.startswith('[ ! -L '):
            state['info_calls']+=1
            if state.get('info_error'):raise AdbError('permission denied')
            return state.get('info',RAW_INFO)
        if 'qsd_size=' in command:
            state['duration_calls']+=1
            return b'QSD1:999999\n'+bytes(HEAD)+bytes(TAIL)+b'\n:QSD_END:\n'
        assert 'qsa_size=' in command
        assert command.count('readlink -f')==3
        assert 'head -c 5242881' in command
        assert command.count('stat -c %s')==2
        state['cover_calls']+=1
        if state.get('cancel'):state['cancel'].set()
        if state.get('disconnect'):state['connected']=False
        if state.get('swap'):state['hardware']='OTHER'
        if state.get('cover_error'):
            raise AdbError(state['cover_error'])
        image=state.get('image',PNG)
        return state.get('frame', b'QSA1:'+str(len(image)).encode()+b'\n'+image+b'\n:QSA_END:\n')


class Provider(AdbReadOnlyProvider):
    def __init__(self,state):super().__init__();self.state=state
    def _bridge(self,serial=''):return Bridge(self.state)


def main():
    checks=[]
    def run(label, values=None, rows=None, expect=None):
        state=dict(calls=[],info_calls=0,cover_calls=0,duration_calls=0)
        state.update(values or {})
        cancel=threading.Event()
        if state.get('cancel'):state['cancel']=cancel
        provider=Provider(state)
        selected=provider._descriptor(AdbDevice('A','device','Quest_3','eureka','oculus'),'HW',DEVICE_ROOT)
        try:
            result=assets.read_assets(provider,selected,[dict(ROW)] if rows is None else rows,cancel)
        except DownloadError as exc:
            assert exc.code==expect,(label,exc.code,expect)
            result=None
        else:assert expect is None,(label,expect)
        checks.append(dict(case=label,passed=True,commands=len(state['calls']),info=state['info_calls'],
                           cover=state['cover_calls'],duration=state['duration_calls'],expected_error=expect))
        return result,state
    def forbidden(*args,**kwargs):raise AssertionError('No external process/network allowed')
    with patch('subprocess.Popen',forbidden),patch('socket.socket',forbidden):
        state=dict(calls=[],info_calls=0,cover_calls=0,duration_calls=0)
        provider=Provider(state);cancel=threading.Event()
        selected=provider._descriptor(AdbDevice('A','device','Quest_3','eureka','oculus'),'HW',DEVICE_ROOT)
        metadata=provider.read_metadata(selected,cancel)
        assert state['duration_calls']==state['cover_calls']==0
        assert metadata['rows'][0]['bpm']==120 and metadata['rows'][0]['subtitle']=='Sub'
        assert metadata['rows'][0]['info_hash']==ROW['info_hash'] and metadata['rows'][0]['version']=='2.1.0'
        assert metadata['rows'][0]['duration']==0
        complete=provider.read_library(selected,cancel)
        assert complete['rows'][0]['duration']==123.5 and state['duration_calls']==1
        lock=provider._io_lock;lock.acquire();cancel.set()
        try:
            try:provider.read_metadata(selected,cancel)
            except DownloadError as exc:assert exc.code=='cancelled'
            else:raise AssertionError('Waiting for I/O lock ignored cancellation')
        finally:lock.release();cancel.clear()
        result,state=run('normal')
        item=result['items'][0]
        assert item['duration']==123.5 and item['cover_done'] and item['duration_done']
        with Image.open(io.BytesIO(item['image'])) as picture:assert picture.size==(256,256) and picture.mode=='RGB'
        assert state['info_calls']==state['cover_calls']==state['duration_calls']==1
        assert len([call for call in state['calls'] if call==['devices','-l']])==2
        result,state=run('info_changed',{'info':RAW_INFO+b' '})
        assert result['items'][0]['error']=='changed' and state['cover_calls']==state['duration_calls']==0
        result,state=run('cover_only',rows=[dict(ROW,need_duration=False)])
        assert state['duration_calls']==0 and result['items'][0]['duration_done'] is False
        result,state=run('duration_only',rows=[dict(ROW,need_cover=False)])
        assert state['cover_calls']==0 and result['items'][0]['cover_done'] is False
        for name,value in [('bad_image',{'image':b'not an image'}),('bad_frame',{'frame':b'QSA1:2\nx\n:QSA_END:\n'}),
                           ('missing_cover',{'cover_error':'permission denied'})]:
            result,_=run(name,value)
            assert result['items'][0]['image'] is None and result['items'][0]['duration']==123.5
        result,state=run('info_permission',{'info_error':True})
        assert result['items'][0]['error']=='metadata' and state['cover_calls']==state['duration_calls']==0
        run('cancel_after_cover',{'cancel':True},expect='cancelled')
        run('disconnect_at_end',{'disconnect':True},expect='device_disconnected')
        run('hardware_swapped_at_end',{'swap':True},expect='device_rejected')
        run('transport_disconnect',{'cover_error':'device offline'},expect='device_disconnected')
        run('transport_timeout',{'cover_error':'超时'},expect='device_timeout')
        for label,row in [('legacy', {key:value for key,value in ROW.items() if key!='info_hash'}),
                          ('bad_folder', dict(ROW,folder='../bad')),
                          ('boolean_flags',dict(ROW,need_cover=1))]:
            _,state=run(label,rows=[row],expect='device_protocol')
            assert not state['calls']
        _,state=run('too_many',rows=[dict(ROW,folder=str(i)) for i in range(5)],expect='device_protocol')
        assert not state['calls']
        _,state=run('empty',rows=[])
        assert not state['calls']
        rows=[]
        for name in ('','../escape','/abs','a\\b','a\nb'):
            info=dict(INFO,_coverImageFilename=name)
            raw=json.dumps(info,ensure_ascii=False).encode('utf-8')
            # The production snapshot protocol drops unsafe cover names; the
            # raw Info hash still identifies the bytes we reread here.
            row=dict(ROW,info_hash=hashlib.sha256(raw).hexdigest(),cover_file='',need_duration=False)
            result,state=run('invalid_cover_'+str(len(rows)),{'info':raw},rows=[row])
            assert state['cover_calls']==0 and result['items'][0]['image'] is None
            rows.append(name)
        info=dict(INFO,_coverImageFilename='x'*2000)
        raw=json.dumps(info,ensure_ascii=False).encode('utf-8')
        row=dict(ROW,folder='x'*2000,info_hash=hashlib.sha256(raw).hexdigest(),cover_file='',need_duration=False)
        result,state=run('oversized_cover_command',{'info':raw},rows=[row])
        assert state['cover_calls']==0 and result['items'][0]['image'] is None
        info=dict(INFO,_songFilename='x'*3000)
        raw=json.dumps(info,ensure_ascii=False).encode('utf-8')
        row=dict(ROW,info_hash=hashlib.sha256(raw).hexdigest(),need_cover=False)
        result,state=run('oversized_audio_name',{'info':raw},rows=[row])
        assert state['duration_calls']==0 and result['items'][0]['duration']==0
        embedded=b'QSA1:99\n:QSA_END:\n'
        assert assets._decode_cover(b'QSA1:'+str(len(embedded)).encode()+b'\n'+embedded+b'\n:QSA_END:\n')==embedded
        for raw in (None,'',b'',b'QSA1:0\n\n:QSA_END:\n',b'QSA1:5242881\n',b'QSA1:01\nx\n:QSA_END:\n'):
            assert assets._decode_cover(raw) is None
        large=io.BytesIO();Image.new('RGB',(4097,1)).save(large,format='PNG')
        try:assets.normalize_cover(large.getvalue())
        except ValueError:pass
        else:raise AssertionError('Oversized image accepted')
        assert not any(key.startswith(('PySide6','tkinter','miniaudio')) for key in sys.modules)
    print('PASS metadata without audio/cover, compatible full read, shared-lock cancellation; asset cases',len(checks),'no GUI/device/network')


if __name__=='__main__':main()
