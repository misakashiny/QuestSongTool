"""Offline website browse/filter/media contracts and actual GUI/audio flow."""
from __future__ import annotations

import io
import math
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image
from quest_tool import audio
from quest_tool.downloads import BeatSaverClient, DownloadError, DownloadManager, TERMINAL
from quest_tool.search_catalog import TAGS, ENVIRONMENTS
from quest_tool.search_filters import browse_params, filter_params
from quest_tool.search_media import MediaClient, PreviewClient
from quest_tool.song_search import SearchClient, SearchSession, search_map
from quest_tool.playlist import song_hash
from smoke_search import sample
from smoke_downloads import make_archive, FakeClient


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(2); out.setsampwidth(2); out.setframerate(44100)
        out.writeframes(b"".join(struct.pack("<hh", int(9000 * math.sin(i * 440 * math.tau / 44100)),
                                              int(9000 * math.sin(i * 440 * math.tau / 44100))) for i in range(88200)))
    return buffer.getvalue()


class FakeMedia(MediaClient):
    def __init__(self):
        super().__init__(); self.calls = []
        output = io.BytesIO(); Image.new("RGB", (80, 80), (240, 150, 20)).save(output, format="PNG")
        self.image = output.getvalue(); self.sound = wav_bytes()

    def fetch(self, url, destination, cancel, progress, limit):
        self.calls.append((url, limit))
        destination.write_bytes(self.image if "cover" in url else self.sound)


class DiscoverTests(unittest.TestCase):
    def test_website_catalog(self):
        assert len(TAGS) == 45 and len(ENVIRONMENTS) == 47
        assert TAGS["hip-hop-rap"] == "Hip Hop & Rap"
        assert ENVIRONMENTS["ProdigyEnvironment"] == "Prodigy"

    def test_filter_exact_wire_values(self):
        values = {"automapper": "ai", "leaderboard": "BeatLeader", "chroma": False, "verified": True,
                  "tags": {"pop": True, "tech": False}, "environments": ["DefaultEnvironment"],
                  "characteristics": ["Standard"], "minNps": "2.5", "maxNps": "6", "minRating": "90",
                  "minDuration": "60", "to": "2026-10-17"}
        params = filter_params(values)
        assert params == {"automapper": "false", "leaderboard": "BeatLeader", "chroma": "false", "verified": "true",
                          "tags": "pop,!tech", "environments": "DefaultEnvironment", "characteristics": "Standard",
                          "minNps": "2.5", "maxNps": "6", "minRating": "0.9", "minDuration": "60", "to": "2026-10-18T00:00:00Z"}
        assert "automapper" not in filter_params({"automapper": "human"})
        assert filter_params({"automapper": "all"})["automapper"] == "true"

    def test_invalid_filters_rejected(self):
        for value in ({"minNps": "nan"}, {"minNps": "inf"}, {"maxBpm": -1}, {"minNps": 8, "maxNps": 2},
                      {"minRating": 101}, {"minDuration": 2.5}, {"from": "2026-02-30"}, {"from": "2026-10-18", "to": "2026-10-17"},
                      {"tags": {"evil": True}}, {"chroma": "true"}, {"environments": ["evil"]}, {"characteristics": ["evil"]}, {"random": 1}):
            with unittest.TestCase.assertRaises(self, DownloadError): filter_params(value)

    def test_browse_intervals_and_conflicts(self):
        now = datetime(2026, 10, 17, tzinfo=timezone.utc)
        params, order = browse_params("Week", {}, now)
        assert params["from"] == "2026-10-10T00:00:00Z" and order == "Rating"
        for mode in ("Curated", "Verified", "Ranked", "Latest", "Random"):
            params, order = browse_params(mode, {})
            assert order in ("Curated", "Latest", "Rating", "Random")
        with unittest.TestCase.assertRaises(self, DownloadError): browse_params("Curated", {"curated": False})
        with unittest.TestCase.assertRaises(self, DownloadError): browse_params("Week", {"to": "2025-01-01"}, now)

    def test_empty_keyword_browse_uses_filters(self):
        class Client(SearchClient):
            seen = ""
            def fetch(client, url, destination, cancel, progress, limit):
                client.seen = url; destination.write_text('{"docs": []}', encoding="utf-8")
        client = Client(); client.search("", 0, "Relevance", threading.Event(), filters={"minNps": "2"}, mode="Curated")
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(client.seen).query, keep_blank_values=True)
        assert params["q"] == [""] and params["curated"] == ["true"] and params["minNps"] == ["2"]

    def test_media_urls_validated_independently(self):
        data = sample(); data["versions"][0].update(coverURL="https://evil.com/a.jpg", previewURL="https://r2cdn.beatsaver.com/a.mp3")
        result = search_map(data)
        assert result["coverURL"] == "" and result["previewURL"].endswith(".mp3")

    def test_cover_decoded_resized_and_cached(self):
        client = FakeMedia(); cancel = threading.Event(); url = "https://r2cdn.beatsaver.com/cover.jpg"
        data = client.load(url, "cover", cancel)
        assert Image.open(io.BytesIO(data)).size == (256, 256)
        assert client.load(url, "cover", cancel) == data and len(client.calls) == 1
        assert client.calls[0][1] == 5 * 1024 * 1024

    def test_invalid_cover_not_cached(self):
        client = FakeMedia(); client.image = b"bad image"
        with unittest.TestCase.assertRaises(self, DownloadError): client.load("https://r2cdn.beatsaver.com/cover", "cover", threading.Event())
        assert not client.covers
        output = io.BytesIO(); Image.new("RGB", (4097, 1)).save(output, format="PNG")
        client.image = output.getvalue()
        with unittest.TestCase.assertRaises(self, DownloadError): client.load("https://r2cdn.beatsaver.com/wide-cover", "cover", threading.Event())
        assert not client.covers

    def test_audio_cache_bound_and_cancel(self):
        client = FakeMedia()
        for i in range(4): client.load(f"https://r2cdn.beatsaver.com/{i}.ogg", "preview", threading.Event())
        assert len(client.previews) == 3 and all(limit == 16 * 1024 * 1024 for _url, limit in client.calls)
        cancel = threading.Event(); cancel.set()
        with unittest.TestCase.assertRaises(self, DownloadError): client.load("https://r2cdn.beatsaver.com/3.ogg", "preview", cancel)

    def test_preview_decode_and_request_identity(self):
        if not audio.available():
            unittest.TestCase.skipTest(self, "Optional audio dependency unavailable")
        class Player:
            calls = []
            def play_file(player, path, seconds, start, request_id):
                assert path.exists(); player.calls.append((seconds, start, request_id)); return path
        client = PreviewClient(FakeMedia(), Player())
        result = client.search({"id": "a1", "previewURL": "https://r2cdn.beatsaver.com/test.ogg"}, .5, 7, threading.Event())
        assert result["total"] == 2 and result["request_id"] == 7 and client.player.calls == [(1.5, .5, 7)]

    def test_auth_access_and_throttle_failures(self):
        class Opener:
            code = 403
            def open(opener, request, timeout):
                raise urllib.error.HTTPError(request.full_url, opener.code, "test", {"Retry-After": "1"}, None)
        class Cancel:
            def is_set(cancel): return False
            def wait(cancel, seconds): return False
        client = BeatSaverClient(); client.opener = Opener()
        with tempfile.TemporaryDirectory() as folder:
            for code, error in ((401, "auth"), (403, "access"), (429, "rate_limit")):
                client.opener.code = code
                with unittest.TestCase.assertRaises(self, DownloadError) as caught:
                    client.fetch("https://api.beatsaver.com/test", Path(folder)/"data", Cancel(), lambda _a,_b: None)
                assert caught.exception.code == error


def gui_check():
    import customtkinter as ctk
    from quest_tool.ui_downloads import DownloadWindow
    from quest_tool.messages import tr, set_language
    from window_capture import capture_client
    ma = audio._load()
    if ma is None:
        print('[SKIP] actual audio GUI: optional miniaudio dependency unavailable')
        return
    peaks = []
    original = ma.PlaybackDevice._data_callback
    def capture(device, raw_device, output, input_data, framecount):
        active = device.callback_generator is not None
        original(device, raw_device, output, input_data, framecount)
        if not active or not output:
            return
        data = bytes(ma.ffi.buffer(output, framecount * 4))
        values = struct.unpack('<' + 'h' * (len(data)//2), data)
        peaks.append(max(abs(value) for value in values) if values else 0)
    ma.PlaybackDevice._data_callback = capture
    root = ctk.CTk(); root.withdraw()
    workspace = Path(__file__).resolve().parent / '.tmp/discover_ui'; workspace.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workspace) as folder:
        work = Path(folder); target = work/'songs';target.mkdir()
        archive = make_archive(work); fixture = work/'fixture';fixture.mkdir()
        with zipfile.ZipFile(archive) as zipped: zipped.extractall(fixture)
        manager = DownloadManager(work/'history.json', FakeClient(archive, song_hash(fixture)))
        window = DownloadWindow(root, manager, str(target)); pane = window.search_pane
        class Client:
            seen = []
            def search(client, query, page, order, cancel, **kwargs):
                client.seen.append((query, page, order, kwargs))
                rows=[]
                for i in range(3):
                    data=sample(hex(i+1)[2:], 'Discover '+str(i+1));data['versions'][0].update(coverURL=f'https://r2cdn.beatsaver.com/cover{i}.png',previewURL=f'https://r2cdn.beatsaver.com/audio{i}.ogg'); rows.append(search_map(data))
                return {'rows':rows,'page':page,'has_next':False,'query':query,'order':order}
        client=Client();pane.session.close();pane.session=SearchSession(client)
        pane.media.covers.client.media=FakeMedia();pane.media.preview.client.media=FakeMedia()
        def pump_until(predicate, seconds=6):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                root.update();time.sleep(.01)
                if predicate():return
            raise AssertionError('GUI timed out')
        try:
            pane.mode_value.set(tr('browse.Curated'));pane._mode_changed(None)
            pump_until(lambda:not pane.busy and len(pane._covers)==3)
            assert client.seen[-1][0]=='' and client.seen[-1][3]['mode']=='Curated'
            assert pane.cover.cget('image') is not None
            assert pane.list._photos, "Visible canvas covers were not loaded"
            pane.filter_button.invoke();root.update();dialog=pane.filter_window
            dialog.bounds['minNps'].insert(0,'8');dialog.bounds['maxNps'].insert(0,'2');dialog.apply();root.update()
            assert dialog.winfo_exists() and dialog.error.cget('text')==tr('dl.error.filters')
            dialog.bounds['minNps'].delete(0,'end');dialog.bounds['minNps'].insert(0,'1')
            dialog.tags['pop'][0].set(tr('filter.yes'));dialog.apply()
            pump_until(lambda:not pane.busy and len(pane._covers)==3)
            assert client.seen[-1][3]['filters']['tags']['pop'] is True
            pane.play_button.invoke();pump_until(lambda:bool(peaks) and max(peaks)>100 and not pane._preview_pending)
            assert audio.player().is_playing() and pane._preview_total==2
            pane.seek_preview(.25);pump_until(lambda:audio.player().start_offset==.5 and not pane._preview_pending)
            playing_id = pane._preview_item['id']; request_id = audio.player().request_id
            pane.show_info(pane.rows[1])
            window.tabs.set(tr('layout.import')); root.update()
            assert pane.play_button.winfo_ismapped() and pane._preview_item['id']==playing_id
            assert audio.player().request_id==request_id, 'Changing detail/tab cancelled the shared preview'
            window.tabs.set(tr('search.tab')); root.update()
            assert pane.current_item['id']!=playing_id and pane._preview_item['id']==playing_id
            pane.play_button.invoke();pump_until(lambda:not audio.player().is_playing())
            pane.play_button.invoke();pane.stop_preview();pump_until(lambda:not pane.media.preview.tasks.busy())
            assert not audio.player().is_playing(), 'Stopped pending request started playing'
            item=pane.rows[0];pane.checks[item['id']].set(True);pane.toggle(item);pane.download_button.invoke()
            pump_until(lambda:bool(manager.snapshot()) and manager.snapshot()[0]['state'] in TERMINAL)
            assert manager.snapshot()[0]['state']=='done'
            window.tabs.set(tr('search.tab'))
            # Flush CTk's delayed tab callbacks: rapid queue -> search switching
            # used to leave the selected tab blank after 100 ms.
            deadline=time.monotonic()+.3
            while time.monotonic()<deadline:
                root.update();time.sleep(.01)
            assert pane.winfo_ismapped(), 'Selected search tab disappeared after delayed callback'
            for scaling in (1.,1.25,1.5):
                ctk.set_widget_scaling(scaling);root.update()
                assert window.winfo_reqwidth()<=window.winfo_width()+2
            ctk.set_widget_scaling(1.);root.update()
            if os.environ.get('QUEST_CAPTURE_DISCOVER')=='1':
                for mode in ('Light','Dark'):
                    ctk.set_appearance_mode(mode);root.update();time.sleep(.1);root.update()
                    capture_client(window,Path(__file__).resolve().parent.parent/f'docs/images/v19.2-discover-{mode.lower()}.png')
            print('[PASS] actual browse/filter dialog/cover thumbnails/detail cover/audio callback nonzero/seek/stop/pending cancellation/download queue/scaling')
            window.close()
            set_language('en');window=DownloadWindow(root,manager,str(target));root.update()
            window.search_pane.open_filters();root.update();window.search_pane.filter_window.reset();window.search_pane.filter_window.destroy()
            assert window.search_pane.mode_value.get()=='Search songs'
            print('[PASS] English browse and complete filter dialog constructed')
        finally:
            window.close();manager.shutdown()
            for thread in manager.tasks._threads:thread.join(3)
            root.destroy();audio.stop_all();ma.PlaybackDevice._data_callback=original


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DiscoverTests))
    if not result.wasSuccessful():raise SystemExit(1)
    gui_check();print('[PASS] DISCOVER REGRESSION')
