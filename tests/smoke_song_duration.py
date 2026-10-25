"""Duration extraction without playback; local scanner and stale-cache UI integration."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import wave
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quest_tool.song_duration import duration_from_parts, local_song_duration
from quest_tool.fsapi import LocalFS, Location
from quest_tool.song_search import search_map
from smoke_search import sample


def main():
    head = b'OggS'+bytes(10)+b'abcd'+bytes(9)+b'\x01vorbis'+bytes(5)+struct.pack('<I',48000)
    tail = b'OggS'+bytes(2)+struct.pack('<Q',48000*193)+b'abcd'+bytes(9)
    assert duration_from_parts(head,tail)==193
    assert duration_from_parts(head,tail.replace(b'abcd',b'zzzz'))==0
    assert duration_from_parts(b'',b'')==0
    opus = b'OggS'+bytes(10)+b'abcd'+bytes(9)+b'OpusHead'+bytes(2)+struct.pack('<H',312)
    assert abs(duration_from_parts(opus,tail)-(193-312/48000))<.0001
    packed=(44100<<44)|(44100*71)
    flac=b'fLaC'+bytes(14)+packed.to_bytes(8,'big')
    assert duration_from_parts(flac,b'')==71
    raw=sample();raw['metadata'].pop('duration');raw['versions'][0]['diffs'][0]['seconds']=203.5
    assert search_map(raw)['duration']==203.5
    print('[PASS] Ogg Vorbis/Opus, FLAC, foreign serial, malformed headers and online diff duration fallback')
    workspace=Path(__file__).resolve().parent/'.tmp/duration_ui';workspace.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workspace) as temp:
        root=Path(temp);folder=root/'歌曲';folder.mkdir()
        info={'_version':'2.0.0','_songName':'Duration fixture','_songFilename':'音频.wav'}
        (folder/'Info.dat').write_text(json.dumps(info),encoding='utf-8')
        with wave.open(str(folder/'音频.wav'),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000)
            stream.writeframes(bytes(8000*2*7))
        assert local_song_duration(folder,info)==7
        assert local_song_duration(folder,{'_songFilename':'../outside.wav'})==0
        fs=LocalFS(Location('local',str(root)));meta=fs.scan_metadata(['歌曲'])['歌曲']
        assert meta.duration==7 and meta.display_duration=='0:07'
        from quest_tool import storage
        storage.data_dir=lambda:workspace
        from quest_tool.app import QuestComparatorApp
        from quest_tool.app_common import meta_row
        from quest_tool.storage import Settings
        from quest_tool.database import Database
        meta.duration=0
        with Database(workspace/'songs_cache.db') as db:
            db.save_meta(meta_row(meta))
            db.set_rating('duration-test','歌曲',4)
            db.set_comment('duration-test','歌曲','Keep this comment')
            db._conn.execute('UPDATE schema_info SET version=1');db._conn.commit()
        app=QuestComparatorApp(Settings(wizard_done=True,device_poll_on_start=False,ui_scale='1.0'))
        try:
            assert app.db.get_rating('duration-test','歌曲')==(4,'Keep this comment')
            app.path_vars['local'].set(str(root));app.load_library('local',silent=True)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                app.root.update();time.sleep(.01)
                if app.libraries['local'].meta.get('歌曲') and app.libraries['local'].meta['歌曲'].duration==7:break
            assert app.libraries['local'].meta['歌曲'].duration==7, 'Old zero-duration cache was not refreshed'
            assert app.db.get_meta('歌曲',meta.mtime).duration==7
            app.show_song('local','歌曲')
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                app.root.update();time.sleep(.01)
                duration_label = app.metric_labels.get('时长')
                if duration_label is not None and duration_label.cget('text')=='0:07':break
            assert app.metric_labels['时长'].cget('text')=='0:07'
            chrome=app.window_chrome
            geometry_calls=[];original_geometry=app.root.geometry
            def measured_geometry(value=None):
                if value is not None:geometry_calls.append(value)
                return original_geometry(value)
            app.root.geometry=measured_geometry
            chrome._drag(SimpleNamespace(x_root=100,y_root=100))
            for step in range(100):chrome._move(SimpleNamespace(x_root=100+step//5,y_root=100+step//5))
            assert not geometry_calls, 'Drag updates were not coalesced'
            chrome._release();assert len(geometry_calls)==1
            app.root.geometry=original_geometry
            app.open_downloads();window=app._download_window
            deadline=time.monotonic()+.4
            while time.monotonic()<deadline:app.root.update();time.sleep(.01)
            assert str(window.transient())==str(app.root)
            assert window.winfo_ismapped()
            import ctypes
            from ctypes import wintypes
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.GetParent.argtypes=[wintypes.HWND];user.GetParent.restype=wintypes.HWND
            user.GetWindow.argtypes=[wintypes.HWND,wintypes.UINT];user.GetWindow.restype=wintypes.HWND
            assert user.GetWindow(user.GetParent(window.winfo_id()),4)==user.GetParent(app.root.winfo_id())
            maps=[];app.root.bind('<Map>',lambda event: maps.append(event.widget),add='+')
            app._force_repaint();app.root.update()
            assert not maps and window.winfo_ismapped(), 'Repaint remapped main window over downloads'
            print('[PASS] 100 drag events -> one geometry update; download owner assigned; repaint does not remap or hide its child')
            window.close()
            settings=Settings(download_columns=4,download_rows=6);settings.save(root/'settings.json')
            loaded=Settings.load(root/'settings.json');assert (loaded.download_columns,loaded.download_rows)==(4,6)
            print('[PASS] Real v2 WAV scanner, Unicode path, stale cache -> DB -> visible duration and grid preference persistence')
        finally:app.on_close()


if __name__=='__main__':main()
