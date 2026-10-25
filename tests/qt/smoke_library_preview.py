"""M6B2 actual audio callbacks, seek, ownership, DPI and shutdown."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool import audio
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import sample_songs
    from quest_tool.search_media import PreviewClient, MediaClient
    from quest_tool.song_search import SearchSession
    from quest_tool.qt_ui.library_preview import LocalPreviewClient
    from dataclasses import replace
    app = QApplication([]); app.setStyle('Fusion'); errors = []
    def report(kind, value, tb):
        errors.append(repr(value)); sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    main = threading.get_ident(); threads = []; callbacks = []
    hold = threading.Event(); entered = threading.Event()
    original_search = LocalPreviewClient.search
    def search(client, *args):
        threads.append(threading.get_ident())
        if hold.is_set():
            entered.set()
            while hold.is_set():
                if args[3].wait(.01): return {}
        return original_search(client, *args)
    LocalPreviewClient.search = search
    ma = audio._load(); assert ma is not None
    original_callback = ma.PlaybackDevice._data_callback
    def capture(device, raw, output, input_data, frames):
        active = device.callback_generator is not None
        original_callback(device, raw, output, input_data, frames)
        if active and output:
            callbacks.append(max(map(abs, struct.unpack('<'+'h'*(frames*2), bytes(ma.ffi.buffer(output, frames*4)))), default=0))
    ma.PlaybackDevice._data_callback = capture
    def wait(test, seconds=10):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            app.processEvents(); QTest.qWait(10)
            if test(): return
        raise AssertionError('local preview timeout')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); library = root/'library'; library.mkdir()
        for folder in ('first', 'second', 'missing'):
            song = library/folder; song.mkdir()
            (song/'Info.dat').write_text(json.dumps({'_version':'2.0.0','_songName':folder,'_songFilename':'song.wav','_beatsPerMinute':120}), encoding='utf-8')
            if folder != 'missing':
                with wave.open(str(song/'song.wav'), 'wb') as out:
                    out.setnchannels(2); out.setsampwidth(2); out.setframerate(44100)
                    out.writeframes(struct.pack('<hh', 2500, -2500)*44100*90)
        before = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in library.rglob('*') if p.is_file()}
        class Media(MediaClient):
            def fetch(self, url, destination, cancel, progress, **kwargs):
                destination.write_bytes((library/'first'/'song.wav').read_bytes())
        for language in ('zh','en'):
            window = LibraryWindow(root/('sandbox-'+language), language=language); window.show()
            online = None
            try:
                assert window.preview.player is None and window.preview.session is None
                assert not window.preview.play.isEnabled()
                assert window.set_target(library); wait(lambda:window.local_ready)
                def select(folder):
                    index = next(window.proxy.index(i,0) for i in range(window.proxy.rowCount()) if window.proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder']==folder)
                    window.table.setCurrentIndex(index); window.details.select('local',index)
                select('first'); controller = window.preview
                wait(lambda:not window.details.loading)
                for size in ((1000,640),(1400,850)):
                    window.resize(*size); app.processEvents()
                    for control in (controller.play, controller.title, controller.length, controller.volume, controller.seek, controller.time):
                        assert control.isVisible() and control.width()>20 and control.height()>0
                    assert controller.time.width() >= controller.time.fontMetrics().horizontalAdvance(controller.time.text())
                def playing(): return not controller.pending and controller.player is not None and controller.player.is_playing()
                QTest.mouseClick(controller.play, Qt.MouseButton.LeftButton)
                wait(lambda:playing() and any(value>100 for value in callbacks))
                assert controller.total==90 and controller.player.limit_seconds()==30
                controller.volume.setValue(35); assert controller.player.volume()==.35
                for start in (15,30,45):
                    controller.seek.setSliderDown(True); request=controller.request_id
                    controller.seek.setSliderPosition(500); app.processEvents()
                    assert controller.request_id==request
                    controller.seek.setSliderDown(False)
                    wait(lambda:playing() and controller.player.start_offset==start)
                for seconds in (60,120,0):
                    controller.length.setCurrentIndex(controller.length.findData(seconds)); assert controller.request_id is None
                    controller.toggle(); wait(playing); assert controller.player.limit_seconds()==seconds
                select('second'); assert controller.request_id is None
                controller.toggle(); controller.stop(); app.processEvents()
                time.sleep(.15); app.processEvents(); assert not controller.player.is_playing()
                select('missing'); controller.toggle(); wait(lambda:controller.error)
                assert controller.request_id is None
                select('first'); hold.set(); entered.clear(); controller.toggle()
                wait(entered.is_set); controller.stop(); hold.clear()
                wait(lambda:not controller.session.tasks.busy())
                assert not controller.player.is_playing()
                controller.poll(); assert controller.request_id is None
                controller.toggle(); wait(playing)
                window.set_quest_snapshot([dict(folder='first',title='Quest',author='',duration=90,error='')], 'test')
                window.details.select('quest',window.quest_proxy.index(0,0))
                assert not controller.play.isEnabled() and controller.request_id is None
                select('first'); controller.toggle(); wait(playing)
                player=controller.player; online=DownloadWindow(language=language,songs=sample_songs())
                online.enable_preview(SearchSession(PreviewClient(Media(),player)),player)
                song=replace(sample_songs()[0],preview_url='https://cdn.beatsaver.com/test')
                online.preview_browser.play_song(song)
                wait(lambda:not online.preview_browser.pending and player.is_playing())
                network_request=player.request_id; controller.stop()
                assert player.request_id==network_request and player.is_playing()
                controller.volume.setValue(10); assert player.volume()==.8
                controller.toggle(); wait(playing); local_request=player.request_id
                online.close(); assert player.request_id==local_request and player.is_playing()
                window.view_mode.setCurrentIndex(window.view_mode.findData('covers')); app.processEvents()
                assert player.request_id==local_request and controller.page.isVisible()
                shots=Path(os.environ['QUEST_QT_SHOTS']); shots.mkdir(parents=True,exist_ok=True)
                window.grab().save(str(shots/f'local-preview-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                controller.stop()
                probe = audio.probe_duration
                try:
                    audio.probe_duration = lambda _path: None
                    controller.toggle(); wait(playing)
                    assert controller.total == 0 and not controller.seek.isEnabled()
                finally:
                    audio.probe_duration = probe
                if language == 'en':
                    controller.stop(); hold.set(); entered.clear(); controller.toggle(); wait(entered.is_set)
                window.close(); window.wait_closed(); assert not player.is_playing()
                assert not any(t.is_alive() for t in controller.session.tasks._threads)
            finally:
                hold.clear()
                if online: online.close()
                window.close(); window.wait_closed()
        assert all(t!=main for t in threads) and threads
        assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in library.rglob('*') if p.is_file()}
        assert not errors, errors
        assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)
        print('PASS local audio/callbacks/seek/lengths/volume/ownership/missing/DPI/close/source hashes')
    ma.PlaybackDevice._data_callback=original_callback


if __name__=='__main__':
    if '--child' in sys.argv: child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
