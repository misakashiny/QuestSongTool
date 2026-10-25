"""Download safety, persistence, queue, and real-window installation regression."""
from __future__ import annotations

import json
import io
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quest_tool import downloads as dl
from quest_tool.playlist import song_hash


def make_archive(directory, name="song.zip", prefix="", title="Download Test", audio=b"RIFF audio"):
    info = {"_version": "2.0.0", "_songName": title, "_songAuthorName": "Test Artist",
            "_levelAuthorName": "Test Mapper", "_beatsPerMinute": 120,
            "_songFilename": "song.egg", "_difficultyBeatmapSets": [{"_difficultyBeatmaps": [
                {"_difficulty": "Expert", "_beatmapFilename": "Expert.dat"}]}]}
    path = directory / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
        for file, data in (("Info.dat", json.dumps(info).encode()), ("Expert.dat", b'{"_notes":[]}'),
                           ("song.egg", audio)):
            out.writestr(prefix + file, data)
    return path


class FakeClient:
    def __init__(self, archive, digest):
        self.archive, self.digest = archive, digest
        self.fetched = 0
        self.gate = None
        self.entered = threading.Event()

    def resolve(self, map_id, scratch, cancel):
        self.entered.set()
        while self.gate is not None and not self.gate.is_set():
            dl.check_cancel(cancel)
            time.sleep(.01)
        return {"map_id": map_id, "hash": self.digest, "url": "https://r2cdn.beatsaver.com/test.zip",
                "title": "Download Test", "mapper": "Test Mapper"}

    def fetch(self, url, destination, cancel, progress):
        self.fetched += 1
        dl.check_cancel(cancel)
        destination.write_bytes(self.archive.read_bytes())
        progress(destination.stat().st_size, destination.stat().st_size)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "songs"
        self.target.mkdir()
        self.archive = make_archive(self.root)
        sample = self.root / "sample"
        sample.mkdir()
        with zipfile.ZipFile(self.archive) as z:
            z.extractall(sample)
        self.digest = song_hash(sample)
        self.client = FakeClient(self.archive, self.digest)
        self.manager = dl.DownloadManager(self.root / "downloads.json", self.client)

    def tearDown(self):
        self.manager.shutdown()
        for thread in self.manager.tasks._threads:
            thread.join(3)
        self.temp.cleanup()

    def wait(self, ids):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = {row["id"]: row for row in self.manager.snapshot()}
            if all(rows[i]["state"] in dl.TERMINAL for i in ids):
                return [rows[i] for i in ids]
            time.sleep(.02)
        unittest.TestCase.fail(self, "Queue did not finish")

    def test_links_and_deduplication(self):
        unittest.TestCase.assertEqual(self, dl.parse_map_ids("14752\nhttps://beatsaver.com/maps/14752\nABCD"), ["14752", "abcd"])
        for value in ("", "https://evil.com/maps/14752", "https://beatsaver.com.evil/maps/14752",
                      "https://beatsaver.com/", "../14752", "http://beatsaver.com/maps/14752"):
            with unittest.TestCase.subTest(self, value=value), unittest.TestCase.assertRaises(self, dl.DownloadError):
                dl.parse_map_ids(value)

    def test_url_and_redirect_policy(self):
        unittest.TestCase.assertEqual(self, dl.safe_web_url("https://r2cdn.beatsaver.com/test.zip"), "https://r2cdn.beatsaver.com/test.zip")
        for value in ("http://r2cdn.beatsaver.com/a", "https://evil.com/a", "https://beatsaver.com:444/a",
                      "file:///tmp/a", "https://user@beatsaver.com/a"):
            with unittest.TestCase.subTest(self, value=value), unittest.TestCase.assertRaises(self, dl.DownloadError):
                dl.safe_web_url(value)
        with unittest.TestCase.assertRaises(self, dl.DownloadError):
            dl.SafeRedirect().redirect_request(None, None, 302, "", {}, "https://evil.com/a")

    def test_online_install_and_skip(self):
        first = self.wait(self.manager.enqueue(["14752"], str(self.target)))[0]
        unittest.TestCase.assertEqual(self, first["state"], "done", first)
        installed = Path(first["installed"])
        unittest.TestCase.assertTrue(self, (installed / "Info.dat").is_file())
        second = self.wait(self.manager.enqueue(["14752"], str(self.target)))[0]
        unittest.TestCase.assertEqual(self, second["state"], "exists")
        unittest.TestCase.assertEqual(self, second["installed"], str(installed))
        unittest.TestCase.assertEqual(self, self.client.fetched, 1)
        unittest.TestCase.assertEqual(self, len([p for p in self.target.iterdir() if not p.name.startswith('.')]), 1)
        # Terminal snapshots are published inside TemporaryDirectory's context.
        # Queue completion follows __exit__, so await cleanup before inspecting it.
        queue = self.manager._queue
        with queue.all_tasks_done:
            settled = queue.all_tasks_done.wait_for(lambda: queue.unfinished_tasks == 0, timeout=10)
        unittest.TestCase.assertTrue(self, settled, "Download worker did not finish cleanup")
        unittest.TestCase.assertFalse(self, list(self.target.glob('.quest-download-*')))

    def test_wrapped_zip_and_retention(self):
        wrapped = make_archive(self.root, "wrapped.zip", prefix="map/")
        row = self.wait(self.manager.enqueue([str(wrapped)], str(self.target), kind="zip", keep_zip=True))[0]
        unittest.TestCase.assertEqual(self, row["state"], "done", row)
        unittest.TestCase.assertTrue(self, (Path(row["installed"]) / "Info.dat").is_file())
        unittest.TestCase.assertEqual(self, len(list((self.target / '.quest-download-archives').glob('*.zip'))), 1)
        unittest.TestCase.assertTrue(self, wrapped.is_file(), 'ZIP import must preserve the user source')

    def test_zip_identity_includes_audio(self):
        one = self.wait(self.manager.enqueue([str(self.archive)], str(self.target), kind="zip"))[0]
        two = self.wait(self.manager.enqueue([str(self.archive)], str(self.target), kind="zip"))[0]
        unittest.TestCase.assertEqual(self, two["state"], "exists")
        other = make_archive(self.root, "different.zip", audio=b"different audio")
        three = self.wait(self.manager.enqueue([str(other)], str(self.target), kind="zip"))[0]
        unittest.TestCase.assertEqual(self, three["state"], "done", three)
        unittest.TestCase.assertNotEqual(self, one["installed"], three["installed"])

    def test_cancel_resolving_and_retry(self):
        self.client.gate = threading.Event()
        ids = self.manager.enqueue(["14752"], str(self.target))
        unittest.TestCase.assertTrue(self, self.client.entered.wait(2))
        self.manager.cancel(ids[0])
        row = self.wait(ids)[0]
        unittest.TestCase.assertEqual(self, row["state"], "cancelled", row)
        unittest.TestCase.assertFalse(self, list(self.target.iterdir()))
        self.client.gate.set()
        self.manager.retry(ids[0])
        newer = [j["id"] for j in self.manager.snapshot() if j["id"] != ids[0]]
        unittest.TestCase.assertEqual(self, self.wait(newer)[0]["state"], "done")

    def test_pending_sources_are_merged(self):
        self.client.gate = threading.Event()
        one = self.manager.enqueue(["14752"], str(self.target))
        two = self.manager.enqueue(["14752"], str(self.target))
        unittest.TestCase.assertEqual(self, one, two)
        self.client.gate.set()
        unittest.TestCase.assertEqual(self, self.wait(one)[0]["state"], "done")

    def test_parallel_same_version_installs_once(self):
        ids = self.manager.enqueue(["14752", "14753"], str(self.target))
        rows = self.wait(ids)
        unittest.TestCase.assertEqual(self, sorted(r["state"] for r in rows), ["done", "exists"])

    def test_hash_mismatch_never_installs(self):
        self.client.digest = "0" * 40
        row = self.wait(self.manager.enqueue(["14752"], str(self.target)))[0]
        unittest.TestCase.assertEqual(self, (row["state"], row["error"]), ("failed", "hash"))
        unittest.TestCase.assertFalse(self, list(self.target.iterdir()))

    def test_recovery_does_not_restart(self):
        self.client.gate = threading.Event()
        ids = self.manager.enqueue(["14752"], str(self.target))
        self.client.entered.wait(2)
        restored = dl.DownloadManager(self.manager.history, self.client)
        unittest.TestCase.assertEqual(self, restored.snapshot()[0]["state"], "interrupted")
        unittest.TestCase.assertEqual(self, restored.snapshot()[0]["source"], "14752")
        unittest.TestCase.assertFalse(self, restored.tasks._threads)
        self.manager.cancel(ids[0])
        self.wait(ids)

    def test_bad_zip_and_bad_references(self):
        self.archive.write_bytes(b"not a zip")
        row = self.wait(self.manager.enqueue([str(self.archive)], str(self.target), kind="zip"))[0]
        unittest.TestCase.assertEqual(self, row["error"], "zip")
        unittest.TestCase.assertFalse(self, list(self.target.iterdir()))
        self.archive = make_archive(self.root)
        with zipfile.ZipFile(self.archive, "a") as z:
            z.writestr("other/Info.dat", b"{}")
        row = self.wait(self.manager.enqueue([str(self.archive)], str(self.target), kind="zip"))[0]
        unittest.TestCase.assertEqual(self, row["error"], "structure")

    def test_traversal_reserved_names_and_symlinks(self):
        for name in ("../escape.txt", "/absolute.txt", "C:/escape.txt", "CON.txt", "a/NUL", "a/../x"):
            archive = self.root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(name, b"unsafe")
            with unittest.TestCase.subTest(self, name=name):
                row = self.wait(self.manager.enqueue([str(archive)], str(self.target), kind="zip"))[0]
                unittest.TestCase.assertEqual(self, row["error"], "unsafe", row)
                unittest.TestCase.assertFalse(self, list(self.target.iterdir()))
        entry = zipfile.ZipInfo("link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr(entry, "../escape")
        row = self.wait(self.manager.enqueue([str(self.archive)], str(self.target), kind="zip"))[0]
        unittest.TestCase.assertEqual(self, row["error"], "unsafe")

    def test_case_collisions_and_limits(self):
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr("Song.dat", b"a")
            z.writestr("song.dat", b"b")
        row = self.wait(self.manager.enqueue([str(self.archive)], str(self.target), kind="zip"))[0]
        unittest.TestCase.assertEqual(self, row["error"], "unsafe")
        with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("bomb", b"x" * (1024 * 1024))
        old = dl.MAX_EXPANDED
        try:
            dl.MAX_EXPANDED = 100
            row = self.wait(self.manager.enqueue([str(self.archive)], str(self.target), kind="zip"))[0]
            unittest.TestCase.assertEqual(self, row["error"], "limit")
        finally:
            dl.MAX_EXPANDED = old

    def test_no_overwrite_and_completed_history_clear(self):
        first = self.wait(self.manager.enqueue(["14752"], str(self.target)))[0]
        saved = Path(first["installed"]) / "Expert.dat"
        before = saved.read_bytes()
        self.manager.clear_finished()
        unittest.TestCase.assertFalse(self, self.manager.snapshot())
        unittest.TestCase.assertEqual(self, saved.read_bytes(), before)
        restored = dl.DownloadManager(self.manager.history)
        unittest.TestCase.assertEqual(self, Path(restored.last_target).resolve(), self.target.resolve())

    def test_history_write_failure_does_not_start_download(self):
        def unavailable():
            raise OSError('read only')
        self.manager._save = unavailable
        with unittest.TestCase.assertRaises(self, dl.DownloadError) as caught:
            self.manager.enqueue(['14752'], str(self.target))
        unittest.TestCase.assertEqual(self, caught.exception.code, 'history')
        unittest.TestCase.assertFalse(self, self.manager.snapshot())
        unittest.TestCase.assertFalse(self, self.manager.tasks._threads)

    def test_shutdown_waits_for_partial_file_cleanup(self):
        entered = threading.Event()
        def partial_download(url, destination, cancel, progress):
            with destination.open('wb') as stream:
                stream.write(b'partial package')
                entered.set()
                while not cancel.wait(.01):
                    pass
                dl.check_cancel(cancel)
        self.client.fetch = partial_download
        ids = self.manager.enqueue(['14752'], str(self.target))
        unittest.TestCase.assertTrue(self, entered.wait(3))
        self.manager.shutdown()
        self.manager._reaper.join(5)
        unittest.TestCase.assertFalse(self, self.manager._reaper.is_alive())
        unittest.TestCase.assertEqual(self, self.wait(ids)[0]['state'], 'cancelled')
        unittest.TestCase.assertFalse(self, list(self.target.iterdir()))

    def test_cancel_waiting_then_clear_does_not_break_consumers(self):
        self.client.gate = threading.Event()
        ids = self.manager.enqueue(['14752','14753','14754'],str(self.target))
        self.client.entered.wait(2)
        self.manager.cancel(ids[-1])
        rows = {r['id']:r for r in self.manager.snapshot()}
        unittest.TestCase.assertEqual(self, rows[ids[-1]]['state'], 'cancelled')
        self.manager.clear_finished()
        self.client.gate.set()
        self.wait(ids[:2])
        newer = self.manager.enqueue(['14755'],str(self.target))
        unittest.TestCase.assertEqual(self, self.wait(newer)[0]['state'], 'exists')

    def test_missing_or_escaping_references_never_install(self):
        for filename in ('missing.dat','../outside.dat'):
            with zipfile.ZipFile(self.archive,'w') as z:
                z.writestr('Info.dat',json.dumps({'_songName':'Bad','_songFilename':'song.egg',
                    '_difficultyBeatmapSets':[{'_difficultyBeatmaps':[{'_beatmapFilename':filename}]}]}))
                z.writestr('song.egg',b'RIFF test')
            row=self.wait(self.manager.enqueue([str(self.archive)],str(self.target),kind='zip'))[0]
            unittest.TestCase.assertEqual(self, row['state'],'failed')
            unittest.TestCase.assertFalse(self, list(self.target.iterdir()))

    def test_invalid_beatmap_json_rejected(self):
        with zipfile.ZipFile(self.archive,'w') as z:
            z.writestr('Info.dat',json.dumps({'_songFilename':'song.egg',
                '_difficultyBeatmapSets':[{'_difficultyBeatmaps':[{'_beatmapFilename':'Expert.dat'}]}]}))
            z.writestr('Expert.dat',b'not JSON')
            z.writestr('song.egg',b'audio')
        row=self.wait(self.manager.enqueue([str(self.archive)],str(self.target),kind='zip'))[0]
        unittest.TestCase.assertEqual(self,row['error'],'structure')

    def test_network_limits_retry_and_partial_download(self):
        class Response(io.BytesIO):
            def __init__(response,data,total=None):
                super().__init__(data)
                response.headers={'Content-Length':str(len(data) if total is None else total)}
            def geturl(response):
                return 'https://r2cdn.beatsaver.com/test.zip'
        class Opener:
            def __init__(opener,responses):
                opener.responses=list(responses)
            def open(opener,request,timeout):
                value=opener.responses.pop(0)
                if isinstance(value,Exception): raise value
                return value
        class Cancel:
            waits=[]
            def is_set(cancel): return False
            def wait(cancel,seconds):
                cancel.waits.append(seconds)
                return False
        client=dl.BeatSaverClient()
        url='https://r2cdn.beatsaver.com/test.zip'
        output=self.root/'download.zip'
        cancel=Cancel()
        client.opener=Opener([urllib.error.HTTPError(url,429,'rate limited',{'Retry-After':'3'},None),Response(b'abc')])
        client.fetch(url,output,cancel,lambda _a,_b:None)
        unittest.TestCase.assertEqual(self,output.read_bytes(),b'abc')
        unittest.TestCase.assertEqual(self,cancel.waits,[3])
        client.opener=Opener([Response(b'abc',total=10)])
        with unittest.TestCase.assertRaises(self,dl.DownloadError) as caught:
            client.fetch(url,output,cancel,lambda _a,_b:None)
        unittest.TestCase.assertEqual(self,caught.exception.code,'incomplete')
        client.opener=Opener([Response(b'abc',total=100)])
        with unittest.TestCase.assertRaises(self,dl.DownloadError) as caught:
            client.fetch(url,output,cancel,lambda _a,_b:None,limit=10)
        unittest.TestCase.assertEqual(self,caught.exception.code,'limit')


def gui_check():
    from quest_tool import storage
    workspace = Path(__file__).resolve().parent / '.tmp' / 'download_ui'
    workspace.mkdir(parents=True, exist_ok=True)
    storage.data_dir = lambda: workspace
    from quest_tool.app import QuestComparatorApp
    from quest_tool.storage import Settings
    from quest_tool.messages import tr
    from window_capture import capture_client
    import customtkinter as ctk
    with tempfile.TemporaryDirectory(dir=workspace) as tmp:
        work = Path(tmp)
        target = work / 'songs'
        target.mkdir()
        archive = make_archive(work)
        sample = work / 'fixture'
        sample.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(sample)
        client = FakeClient(archive, song_hash(sample))
        manager = dl.DownloadManager(work / 'downloads.json', client)
        app = QuestComparatorApp(Settings(local_path=str(target), wizard_done=True,
                                         device_poll_on_start=False, ui_scale='1.0'))
        app._download_manager = manager
        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.root.update()
                time.sleep(.01)
        try:
            pump(.5)
            def find_entry(widget):
                for child in widget.winfo_children():
                    if isinstance(child, ctk.CTkButton) and child.cget('text') == tr('dl.title'):
                        return child
                    found = find_entry(child)
                    if found is not None:
                        return found
                return None
            entry = find_entry(app.root)
            assert entry is not None, 'Main toolbar has no download entry'
            entry.invoke()
            window = app._download_window
            window.geometry('1000x680+100+80')
            window.input.insert('1.0', 'https://evil.com/maps/1')
            window.start_button.invoke()
            assert not manager.snapshot(), 'Invalid input must not create a task'
            assert tr('dl.error.input') in window.detail.get('1.0','end')
            window.input.delete('1.0','end')
            window.input.insert('1.0','14752')
            window.start_button.invoke()
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                pump(.1)
                if app.libraries['local'].folders:
                    break
            assert manager.snapshot()[0]['state'] == 'done', manager.snapshot()
            assert app.libraries['local'].folders, 'Download did not refresh the real local list'
            assert app.current_song is not None, 'New download was not selected'
            assert window.tree.get_children(), 'History not rendered'
            assert not list(target.glob('.quest-download-*')), 'Staging leaked'
            assert client.fetched == 1, 'Valid download button did not call the transfer client'
            window._add([str(archive)], 'zip')
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                pump(.1)
                rows = manager.snapshot()
                if len(rows) == 2 and rows[-1]['state'] in dl.TERMINAL:
                    break
            assert manager.snapshot()[-1]['state'] == 'exists', manager.snapshot()
            print('[PASS] actual toolbar and download buttons: invalid input blocked, online queue installed, ZIP duplicate skipped, main list refreshed and selected')
            for scaling in (1.0, 1.25, 1.5):
                ctk.set_widget_scaling(scaling)
                pump(.15)
                assert window.winfo_reqwidth() <= window.winfo_width() + 2, 'Dialog overflows at scaling'
            ctk.set_widget_scaling(1.0)
            pump(.2)
            if os.environ.get('QUEST_CAPTURE_DOWNLOADS') == '1':
                window.input.delete('1.0','end')
                window.input.insert('1.0','14752\nhttps://beatsaver.com/maps/551c1')
                for mode in ('Light', 'Dark'):
                    ctk.set_appearance_mode(mode)
                    pump(.3)
                    window.lift()
                    window.attributes('-topmost', True)
                    pump(.2)
                    output=Path(__file__).resolve().parent.parent/'docs/images'/f'v19-download-center-{mode.lower()}.png'
                    capture_client(window,output)
            window.close()
            app.open_downloads()
            assert app._download_window.tree.get_children(), 'Closing/reopening lost history'
            print('[PASS] dialog scaling and close/reopen retain history')
        finally:
            app.on_close()
            for thread in manager.tasks._threads:
                thread.join(3)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DownloadTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    gui_check()
    print('[PASS] DOWNLOAD REGRESSION: backend and real GUI passed')
