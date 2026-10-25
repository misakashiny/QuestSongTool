"""Search wire contract, stale requests, and real UI to existing safe download queue."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quest_tool.downloads import DownloadError, DownloadManager, TERMINAL, check_cancel
from quest_tool.song_search import SearchClient, SearchSession, search_map
from smoke_downloads import FakeClient, make_archive
from quest_tool.playlist import song_hash


def sample(map_id="a1", title="Search Test"):
    return {"id": map_id, "description": "Test description", "metadata": {"songName": title,
            "songAuthorName": "Artist", "levelAuthorName": "Mapper", "duration": 190, "bpm": 120},
            "stats": {"score": .97}, "versions": [{"state": "Published", "createdAt": "2026-10-17T00:00:00Z",
            "hash": "a" * 40, "downloadURL": "https://r2cdn.beatsaver.com/a.zip",
            "diffs": [{"difficulty": "ExpertPlus", "characteristic": "Standard", "nps": 5.2, "ne": True}]}]}


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out")


class SearchTests(unittest.TestCase):
    def test_url_encoding_and_page_contract(self):
        class Client(SearchClient):
            seen = ""
            def fetch(client, url, destination, cancel, progress, limit):
                client.seen = url
                assert limit == 4 * 1024 * 1024
                destination.write_text(json.dumps({"docs": [sample()]}), encoding="utf-8")
        client = Client()
        result = client.search("AC/DC & 音乐", 2, "Rating", threading.Event())
        parsed = urllib.parse.urlsplit(client.seen)
        assert parsed.path == "/search/text/2"
        assert urllib.parse.parse_qs(parsed.query) == {"q": ["AC/DC & 音乐"], "order": ["Rating"], "pageSize": ["20"]}
        assert len(result["rows"]) == 1 and not result["has_next"]

    def test_input_bounds_before_network(self):
        client = SearchClient()
        for query, page, order in (("", 0, "Relevance"), ("a" * 201, 0, "Latest"),
                                   ("abc\n", 0, "Latest"), ("a", -1, "Latest"),
                                   ("a", 1001, "Latest"), ("a", True, "Latest"), ("a", 0, "evil")):
            # Trailing whitespace is trimmed; interior controls remain invalid.
            if query == "abc\n":
                query = "ab\nc"
            with unittest.TestCase.assertRaises(self, DownloadError):
                client.search(query, page, order, threading.Event())

    def test_only_published_trusted_versions(self):
        good = sample()
        good["versions"].append(dict(good["versions"][0], state="Uploaded", hash="b" * 40, createdAt="2099"))
        assert search_map(good)["hash"] == "a" * 40
        for change in ({"state": "Uploaded"}, {"hash": "invalid"}, {"downloadURL": "https://evil.com/a.zip"}):
            bad = sample(); bad["versions"][0].update(change)
            assert search_map(bad) is None

    def test_partial_bad_metadata_and_numbers(self):
        value = sample()
        value["metadata"].update(bpm=float("nan"), duration=10 ** 400)
        value["stats"]["score"] = float("inf")
        result = search_map(value)
        assert result["bpm"] is None and result["duration"] is None and result["rating"] is None
        assert result["requirements"] == ["Noodle Extensions"]
        for invalid in (None, [], {}, {"id": "../bad"}):
            assert search_map(invalid) is None
        bad = sample(); bad["versions"][0]["diffs"] = 42
        assert search_map(bad) is None

    def test_duplicate_ids_and_next_page_from_raw_count(self):
        class Client(SearchClient):
            def fetch(client, url, destination, cancel, progress, limit):
                docs = [sample()] * 19 + [{"id": "deleted"}]
                destination.write_text(json.dumps({"docs": docs}), encoding="utf-8")
        result = Client().search("test", 0, "Relevance", threading.Event())
        assert result["has_next"] and len(result["rows"]) == 1

    def test_bad_response_rejected(self):
        class Client(SearchClient):
            content = ""
            def fetch(client, url, destination, cancel, progress, limit):
                destination.write_text(client.content, encoding="utf-8")
        client = Client()
        for content in ("not json", "{}", '{"docs": {}}', json.dumps({"docs": [sample()] * 21})):
            client.content = content
            with unittest.TestCase.assertRaises(self, DownloadError):
                client.search("test", 0, "Latest", threading.Event())

    def test_serialized_latest_request_and_close(self):
        entered = threading.Event()
        class Client:
            active = 0
            maximum = 0
            def search(client, query, page, order, cancel, **kwargs):
                client.active += 1
                client.maximum = max(client.maximum, client.active)
                try:
                    if query == "old":
                        entered.set()
                        cancel.wait(2)
                        # Simulate an uncooperative server returning old data after cancellation.
                    return {"rows": [], "query": query}
                finally:
                    client.active -= 1
        client = Client(); session = SearchSession(client)
        session.request("old", 0, "Latest")
        assert entered.wait(1)
        latest = session.request("new", 0, "Latest")
        wait_for(lambda: not session.tasks.busy())
        messages = session.tasks.drain()
        assert len(messages) == 1 and messages[0].payload["generation"] == latest
        assert messages[0].payload["result"]["query"] == "new" and client.maximum == 1
        session.close()
        with unittest.TestCase.assertRaises(self, DownloadError):
            session.request("closed", 0, "Latest")

    def test_failure_is_reported_and_retry_survives(self):
        class Client:
            def search(client, query, page, order, cancel, **kwargs):
                raise DownloadError("network")
        session = SearchSession(Client())
        for query in ("first", "retry"):
            generation = session.request(query, 0, "Latest")
            wait_for(lambda: not session.tasks.busy())
            messages = session.tasks.drain()
            assert messages[-1].kind == "search_error"
            assert messages[-1].payload == {"generation": generation, "code": "network"}
        session.close()

    def test_close_cancels_active_request(self):
        entered = threading.Event(); released = threading.Event()
        class Client:
            def search(client, query, page, order, cancel, **kwargs):
                entered.set()
                try:
                    cancel.wait(2)
                    check_cancel(cancel)
                finally:
                    released.set()
        session = SearchSession(Client()); session.request("test", 0, "Latest")
        assert entered.wait(1)
        session.close(); session.close()
        assert released.wait(1)
        session._reaper.join(2)
        assert not session.tasks.drain()


def gui_check():
    import customtkinter as ctk
    import zipfile
    from quest_tool.ui_downloads import DownloadWindow
    from quest_tool.messages import tr
    from window_capture import capture_client
    workspace = Path(__file__).resolve().parent / ".tmp/search_ui"
    workspace.mkdir(parents=True, exist_ok=True)
    root = ctk.CTk(); root.withdraw()
    with tempfile.TemporaryDirectory(dir=workspace) as folder:
        work = Path(folder); target = work / "songs"; target.mkdir()
        archive = make_archive(work)
        fixture = work / "fixture"; fixture.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(fixture)
        manager = DownloadManager(work / "downloads.json", FakeClient(archive, song_hash(fixture)))
        window = DownloadWindow(root, manager, str(target))
        pane = window.search_pane
        class Client:
            def search(client, query, page, order, cancel, **kwargs):
                check_cancel(cancel)
                if query == "fail": raise DownloadError("network")
                rows = [search_map(sample(hex(page * 20 + i + 1)[2:], f"Song {page * 20 + i + 1}")) for i in range(20 if page == 0 else 1)]
                return {"rows": rows, "page": page, "query": query, "order": order, "has_next": page == 0}
        pane.session.close(); pane.session = SearchSession(Client())
        def pump_until(predicate, seconds=8):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                root.update(); time.sleep(.01)
                if predicate(): return
            raise AssertionError("GUI timeout")
        try:
            pane.search_button.invoke()
            assert pane.status.cget("text") == tr("dl.error.query")
            pane.entry.insert(0, "test"); pane.search_button.invoke()
            pump_until(lambda: not pane.busy)
            assert len(pane.rows) == 20 and "Mapper" in pane.info.get("1.0", "end")
            assert "ExpertPlus" in pane.info.get("1.0", "end")
            first = pane.rows[0]
            pane.checks[first["id"]].set(True); pane.toggle(first)
            pane.next.invoke(); pump_until(lambda: not pane.busy)
            second = pane.rows[0]
            pane.checks[second["id"]].set(True); pane.toggle(second)
            pane.previous.invoke(); pump_until(lambda: not pane.busy)
            assert pane.checks[first["id"]].get() and len(pane.selected) == 2
            window.target.set(str(work / "missing")); pane.download_button.invoke()
            assert len(pane.selected) == 2 and not manager.snapshot(), "Invalid destination lost selection"
            assert pane.status.cget("text") == tr("dl.error.target"), "Search must show a persistent destination error"
            window.target.set(str(target)); pane.download_button.invoke()
            pump_until(lambda: len(manager.snapshot()) == 2 and all(j["state"] in TERMINAL for j in manager.snapshot()))
            assert {j["source"] for j in manager.snapshot()} == {first["id"], second["id"]}
            assert {j["state"] for j in manager.snapshot()} == {"done", "exists"}
            assert not pane.selected and window.tabs.get() == tr("search.tasks")
            assert all(j["target"] == str(target) for j in manager.snapshot())
            print("[PASS] search/details/cross-page checkboxes -> existing queue -> installed and duplicate skipped; invalid target preserves selection")
            window.tabs.set(tr("search.tab"))
            pane.entry.delete(0, "end"); pane.entry.insert(0, "fail"); pane.search_button.invoke()
            pump_until(lambda: not pane.busy)
            assert pane.status.cget("text") == tr("dl.error.network")
            pane.entry.delete(0, "end"); pane.entry.insert(0, "test"); pane.search_button.invoke()
            pump_until(lambda: not pane.busy)
            pane.select_page(); assert len(pane.selected) == 20
            pane.clear_selection(); assert not pane.selected
            # A changed keyword clears prior choices, while pagination preserves them.
            pane.select_page(); pane.entry.delete(0, "end"); pane.entry.insert(0, "another"); pane.search_button.invoke()
            pump_until(lambda: not pane.busy)
            assert not pane.selected
            for scale in (1.0, 1.25, 1.5):
                ctk.set_widget_scaling(scale)
                root.update()
                assert window.winfo_reqwidth() <= window.winfo_width() + 2
                assert pane.download_button.winfo_rooty() >= pane.next.winfo_rooty() + pane.next.winfo_height(), 'Shared bulk footer overlaps pagination'
                assert pane.download_button.winfo_rootx() + pane.download_button.winfo_width() <= window.winfo_rootx() + window.winfo_width(), 'Download button overflows window'
            ctk.set_widget_scaling(1.0); root.update()
            if os.environ.get("QUEST_CAPTURE_SEARCH") == "1":
                for mode in ("Light", "Dark"):
                    ctk.set_appearance_mode(mode); root.update()
                    time.sleep(.2); root.update()
                    capture_client(window, Path(__file__).resolve().parent.parent / f"docs/images/v19.1-search-{mode.lower()}.png")
            print("[PASS] failed search retry, select/clear page, query change clears selection, 100/125/150% layout")
        finally:
            window.close(); manager.shutdown()
            for thread in manager.tasks._threads: thread.join(3)
            root.destroy()


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SearchTests))
    if not result.wasSuccessful(): raise SystemExit(1)
    gui_check()
    print("[PASS] SEARCH REGRESSION")
