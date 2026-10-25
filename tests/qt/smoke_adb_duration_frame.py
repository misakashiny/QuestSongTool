"""Strict single-request binary protocol and error boundaries; no external I/O."""
from pathlib import Path
import shlex
import sys
import threading
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from quest_tool.downloads import DownloadError
from quest_tool.qt_ui.adb_duration import _decode_parts, remote_duration
from quest_tool.song_duration import HEAD_LIMIT, TAIL_LIMIT


def main():
    marker=b'\x00QSD1:999999\n:QSD_END:\n\xff'
    head=(marker*(HEAD_LIMIT//len(marker)+1))[:HEAD_LIMIT]
    tail=(marker*(TAIL_LIMIT//len(marker)+1))[:TAIL_LIMIT]
    end=b'\n:QSD_END:\n'
    def frame(size=b'999999',first=head,last=tail):return b'QSD1:'+size+b'\n'+first+last+end
    for size in (b'196609',b'999999',b'999999999999999999'):
        assert _decode_parts(frame(size))==(head,tail)
    for size in (b'',b'0',b'196608',b'-999999',b'+999999',b'0999999',b'1e9',b' 999999',b'999999\r',b'9'*19):
        assert _decode_parts(frame(size)) is None,size
    for raw in (None,'',42,b'',frame()[:-1],frame()+b'x',frame(first=head[:-1]),frame(last=tail[:-1]),
                frame(first=head+b'x'),frame().replace(b'QSD1:',b'QSD2:',1),frame()+frame()):
        assert _decode_parts(raw) is None
    cancel=threading.Event();calls=[]
    root='/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels'
    def run(bridge,args,token,deadline,*,binary=False):
        assert bridge=='bridge' and token is cancel and deadline==123 and binary
        calls.append(args);return frame()
    def read(folder='song',name='song.egg',runner=run,selected_root=root):
        return remote_duration(runner,'bridge',selected_root,folder,{'_songFilename':name},cancel,123)
    with patch('quest_tool.qt_ui.adb_duration.duration_from_parts',return_value=123.5) as decode:
        assert read('中文 space\' $(unused);song','音频\' $(unused);.egg')==123.5
        decode.assert_called_once_with(head,tail)
    assert len(calls)==1 and calls[0][0]=='exec-out'
    command=calls[0][1]
    assert shlex.quote(root+'/中文 space\' $(unused);song/音频\' $(unused);.egg') in command
    assert command.count('readlink -f')==4 and command.count('stat -c %s')==2
    calls.clear()
    for bad in ('','..','.','a/b','a\\b','a\nb','a\x00b'):
        assert read(folder=bad)==0 and read(name=bad)==0
    assert read(selected_root='/outside')==0 and not calls
    def expect(call,code):
        try:call()
        except DownloadError as exc:assert exc.code==code;return
        raise AssertionError(code)
    cancel.set();expect(read,'cancelled');assert not calls;cancel.clear()
    def cancelled(*args,**kwargs):cancel.set();return frame()
    expect(lambda:read(runner=cancelled),'cancelled');cancel.clear()
    for code in ('device_command','device_protocol','device_timeout','device_disconnected','device_rejected','cancelled'):
        def failed(*args,**kwargs):raise DownloadError(code)
        if code in ('device_command','device_protocol'):assert read(runner=failed)==0
        else:expect(lambda:read(runner=failed),code)
    print('PASS fixed binary frames, boundary sizes, embedded markers, truncation, one request, quoting, path rejection and error/cancel propagation')


if __name__=='__main__':main()
