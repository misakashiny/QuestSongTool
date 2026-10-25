"""Real Qt controls and audio callback, controlled existing preview transport."""
from dataclasses import replace
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QAbstractSlider
    from quest_tool import audio
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import sample_songs
    from quest_tool.search_media import MediaClient, PreviewClient
    from quest_tool.song_search import SearchSession
    from quest_tool.theme import SPACE

    app = QApplication([])
    errors = []
    sys.excepthook = lambda kind, value, tb: errors.append(repr(value))
    main_thread = threading.get_ident()
    def wav(seconds):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as out:
            out.setnchannels(2);out.setsampwidth(2);out.setframerate(44100)
            out.writeframes(struct.pack('<hh', 2500, -2500)*int(seconds*44100))
        return buffer.getvalue()
    normal, long = wav(4), wav(31)
    class Media(MediaClient):
        def __init__(self):
            super().__init__()
            self.calls = []
            self.entered, self.release = threading.Event(), threading.Event()
        def fetch(self, url, destination, cancel, progress, **kwargs):
            assert kwargs['limit'] == 16*1024*1024
            assert url.startswith('https://cdn.beatsaver.com/')
            self.calls.append((url, threading.get_ident()))
            if url.endswith('/slow'):
                self.entered.set();assert self.release.wait(5)
            destination.write_bytes(b'bad audio' if url.endswith('/bad') else long if url.endswith('/long') else normal)
    media = Media()
    player = audio.PreviewPlayer()
    session = SearchSession(PreviewClient(media, player))
    songs = tuple(replace(s, preview_url='https://cdn.beatsaver.com/'+str(i)) for i, s in enumerate(sample_songs()))
    window = DownloadWindow(songs=songs)
    window.enable_preview(session, player)
    window.show()
    controller = window.preview_browser
    callbacks = []
    ma = audio._load()
    assert ma is not None, 'Actual audio component required'
    original = ma.PlaybackDevice._data_callback
    def capture(device, raw, output, input_data, frames):
        active = device.callback_generator is not None
        original(device, raw, output, input_data, frames)
        if active and output:
            values = struct.unpack('<'+'h'*(frames*2), bytes(ma.ffi.buffer(output, frames*4)))
            callbacks.append(max(map(abs, values), default=0))
    ma.PlaybackDevice._data_callback = capture
    def wait(predicate, seconds=8):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            app.processEvents();QTest.qWait(10);time.sleep(.002)
            if predicate():return
        raise AssertionError('Qt preview timeout: '+str((controller.pending, controller.error, player.is_playing())))
    def play(song):
        controller.play_song(song)
        wait(lambda: not controller.pending and player.is_playing())
    try:
        assert not player.is_playing() and player.volume() == 1
        # Click the actual delegate play control, keeping the checkbox untouched.
        index = window.song_model.index(0)
        rect = window.results.visualRect(index).adjusted(SPACE.XS,SPACE.XS,-SPACE.XS,-SPACE.XS)
        point = window.results.itemDelegate().controls(rect)[1].center()
        QTest.mouseClick(window.results.viewport(), Qt.MouseButton.LeftButton, pos=point)
        wait(lambda: player.is_playing() and not controller.pending and any(v > 100 for v in callbacks))
        assert not window.song_model.checked and controller.song.key == songs[0].key
        assert controller.total == 4 and window.seek.isEnabled() and player.volume() == .8
        assert window.preview_time.width() >= window.preview_time.fontMetrics().horizontalAdvance(window.preview_time.text())
        window.volume.setValue(0);assert player.volume() == 0
        window.volume.setValue(35);assert player.volume() == .35
        request = player.request_id
        window.show_song(songs[1]);window.tabs.setCurrentIndex(1);app.processEvents()
        assert player.request_id == request and window.player.isVisible()
        window.refresh_page(1);assert player.request_id == request
        # Drag commits only once on release, and replay reuses the byte cache.
        calls = len(media.calls)
        window.seek.setSliderDown(True);window.seek.setSliderPosition(500)
        window.seek.setSliderDown(False)
        wait(lambda: not controller.pending and player.is_playing() and player.start_offset == 2)
        assert len(media.calls) == calls
        window.seek.triggerAction(QAbstractSlider.SliderAction.SliderSingleStepAdd)
        wait(lambda: not controller.pending and player.start_offset > 2)
        window.play.click();assert not player.is_playing() and not controller.timer.isActive()
        window.play.click();wait(lambda: player.is_playing() and not controller.pending)
        play(songs[2]);assert controller.song.key == songs[2].key
        play(replace(songs[3], preview_url='https://cdn.beatsaver.com/long'))
        assert controller.total == 30
        for language in ('en','zh'):
            window.retranslate(language)
            for mode in ('dark','light'):
                window.change_theme(mode)
                assert player.is_playing() and controller.song.key == songs[3].key
                assert window.preview_time.width() >= window.preview_time.fontMetrics().horizontalAdvance(window.preview_time.text())
        shots = Path(os.environ.get('QUEST_QT_SHOTS', ROOT/'tests/.tmp/qt-preview'))
        shots.mkdir(parents=True, exist_ok=True)
        window.tabs.setCurrentIndex(0);app.processEvents()
        window.grab().save(str(shots/('m4b-playing-'+os.environ.get('QT_SCALE_FACTOR','1')+'.png')))
        controller.start(songs[0], 3.8)
        wait(lambda: controller.request_id is None)
        assert not player.is_playing() and not window.seek.isEnabled()
        controller.play_song(replace(songs[0], preview_url=''))
        assert controller.error == 'media.missing'
        controller.play_song(replace(songs[1], preview_url='http://127.0.0.1/private'))
        wait(lambda: bool(controller.error))
        assert not player.is_playing()
        controller.play_song(replace(songs[1], preview_url='https://cdn.beatsaver.com/bad'))
        wait(lambda: bool(controller.error))
        assert not player.is_playing()
        available = audio.available
        try:
            audio.available = lambda: False
            controller.play_song(songs[2])
            wait(lambda: bool(controller.error))
            assert controller.error == 'dl.error.preview_unavailable'
        finally:
            audio.available = available
        controller.play_song(songs[2]);wait(lambda: player.is_playing() and not controller.pending)
        # Late worker response after stop must not reopen a device.
        slow = replace(songs[4], preview_url='https://cdn.beatsaver.com/slow')
        controller.play_song(slow);wait(media.entered.is_set)
        controller.stop();media.release.set()
        wait(lambda: not session.tasks.busy())
        assert not player.is_playing()
        session.tasks.emit('search',payload={'generation': -1,'result':{'total':30,'request_id':-1}})
        controller.poll();assert controller.request_id is None
        media.previews.clear();media.entered.clear();media.release.clear()
        controller.play_song(slow);wait(media.entered.is_set)
        controller.play_song(songs[5]);media.release.set()
        wait(lambda: not controller.pending and player.is_playing())
        assert controller.song.key == songs[5].key
        # Closing an active request is prompt and permanently disables the pump.
        media.previews.clear();media.entered.clear();media.release.clear()
        controller.play_song(slow);wait(media.entered.is_set)
        start = time.monotonic();window.close();assert time.monotonic()-start < .3
        media.release.set();wait(lambda: not session.tasks.busy())
        assert not player.is_playing() and not controller.timer.isActive() and controller.closed
        assert all(t != main_thread for _,t in media.calls) and not errors, errors
        assert not any(n.startswith(('tkinter','customtkinter')) for n in sys.modules)
        print('PASS M4B real Qt/audio: callback samples, card/toolbar, volume, seek/cache, 30s cap, tabs/pages, natural end, errors, stale/stop/close and worker transport')
    finally:
        media.release.set();window.close();player.stop()
        ma.PlaybackDevice._data_callback = original


if __name__ == '__main__':
    if '--child' in sys.argv:
        child()
    else:
        for factor in ('1','1.25','1.5'):
            result = subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                                    env=dict(os.environ,QT_SCALE_FACTOR=factor),capture_output=True,timeout=90)
            print(factor, result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode == 0
