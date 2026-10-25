"""Real preview button, callback PCM, rapid seek, and native-header crash regression."""
from __future__ import annotations

import array
import json
import math
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    from quest_tool import storage
    work = Path(__file__).resolve().parent / '.tmp' / 'preview_ui'
    work.mkdir(parents=True, exist_ok=True)
    storage.data_dir = lambda: work
    from quest_tool.app import QuestComparatorApp
    from quest_tool.storage import Settings
    from quest_tool import audio
    ma = audio._load()
    assert ma is not None, 'Real audio dependency required for this regression'
    folder = work / 'songs' / 'preview'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'info.dat').write_text(json.dumps({'_songName':'Preview', '_songAuthorName':'Test'}), encoding='utf-8')
    samples = array.array('h', (int(5000 * math.sin(i * 2 * math.pi * 440 / 44100)) for i in range(44100 * 5)))
    with wave.open(str(folder / 'song.wav'), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(44100)
        stream.writeframes(samples.tobytes())
    observed = []
    original = ma.PlaybackDevice._data_callback
    def capture(device, native, output, input_data, frames):
        active = device.callback_generator is not None
        original(device, native, output, input_data, frames)
        if active and output:
            pcm = bytes(ma.ffi.buffer(output, frames * device.sample_width * device.nchannels))
            observed.append(any(pcm))
    ma.PlaybackDevice._data_callback = capture
    app = QuestComparatorApp(Settings(local_path=str(folder.parent), wizard_done=True,
                                     device_poll_on_start=False, preview_seconds=0, ui_scale='1.0'))
    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.root.update(); time.sleep(.01)
    def wait(predicate):
        end = time.monotonic() + 6
        while time.monotonic() < end:
            pump(.05)
            if predicate(): return
        raise AssertionError('Timed out waiting for preview state')
    try:
        pump(.5)
        from quest_tool.ui_icons import IconButton
        def buttons(widget):
            found = [widget] if isinstance(widget, IconButton) else []
            for child in widget.winfo_children(): found.extend(buttons(child))
            return found
        assets = [button for button in buttons(app.root) if button.cget('image') is not None]
        assert len(assets) >= 20, f'Missing drawn icons: {len(assets)}'
        assert all(not any(glyph in button.cget('text') for glyph in ('🔌','📶','🔄','📥','📱','🗑','▱','☷','▦')) for button in assets)
        print('[PASS] at least 20 controls use image assets with no fallback glyph labels')
        app.load_library('local')
        wait(lambda: bool(app.libraries['local'].folders))
        app.list_local.select('preview'); pump(.3)
        app.preview_button._icon_canvas.event_generate('<Button-1>', x=16, y=16)
        wait(lambda: audio.player().is_playing())
        pump(.4)
        assert audio.player().limit_seconds() == 0, 'Whole-track setting lost'
        assert any(observed), 'Device received no nonzero PCM'
        print('[PASS] actual canvas click starts whole-track playback; device receives nonzero PCM')
        audio.player().set_volume(0); pump(.3); observed.clear(); pump(.25)
        assert observed and not any(observed), 'Mute did not reach device output'
        audio.player().set_volume(.5); observed.clear(); pump(.3)
        assert any(observed), 'Unmute did not restore PCM'
        print('[PASS] actual output callback reflects mute and volume restoration')
        for start in (.1,.2,.3,.4,.5): app._start_preview(folder, start=start)
        wait(lambda: audio.player().is_playing() and audio.player().start_offset == .5)
        pump(.4)
        assert audio.player().start_offset == .5, 'Stale seek replaced latest request'
        print('[PASS] five overlapping requests settle on the latest seek')
        app.preview_button._icon_canvas.event_generate('<Button-1>', x=16, y=16)
        pump(.2); assert not audio.player().is_playing()
        app._start_preview(folder); app.toggle_preview(); pump(.7)
        assert not audio.player().is_playing(), 'Stop while loading was ignored'
        print('[PASS] stop works during playback and while loading')
        if app._drop_target:
            proc = app._drop_target._old_proc
            assert app._drop_target.install()
            assert app._drop_target._old_proc == proc
        for index in range(20):
            app.window_chrome._drag(SimpleNamespace(x_root=100,y_root=100))
            app.window_chrome._move(SimpleNamespace(x_root=101,y_root=101))
            pump(.01)
        print('[PASS] repeated header dragging with installed drop hook; repeated hook install is safe')
    finally:
        app.on_close()
        ma.PlaybackDevice._data_callback = original
    print('PREVIEW UI: 6/6 passed')


if __name__ == '__main__':
    main()
