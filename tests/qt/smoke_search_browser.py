"""Qt UI -> real SearchSession/SearchClient -> controlled HTTP transport."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quest_tool.downloads import DownloadError
from quest_tool.song_search import SearchClient, SearchSession


class ScriptedClient(SearchClient):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def fetch(self, url, destination, cancel, progress, **_kwargs):
        parsed = urllib.parse.urlparse(url)
        assert parsed.hostname == "api.beatsaver.com" and "/search/text/" in parsed.path
        params = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
        page = int(parsed.path.rsplit("/", 1)[1])
        self.calls.append((params, page, threading.get_ident()))
        query = params["q"]
        if query == "slow":
            self.entered.set()
            assert self.release.wait(5), "Release the controlled transport"
        if query in ("rate_limit", "auth", "access", "network"):
            raise DownloadError(query)
        docs = []
        size = int(params["pageSize"])
        count = 0 if query == "empty" else min(size, max(0, 205-page*size))
        for number in range(page*size, page*size+count):
            docs.append({"id": f"{number+1:x}", "metadata": {
                "songName": "<b>Literal title</b>" if number == 0 else "长标题LongTitle"*15 if number == 1 else f"Song {number}",
                "songAuthorName": "Test artist", "levelAuthorName": "Test mapper",
                "bpm": None if number == 0 else 128, "duration": None if number == 0 else 193},
                "stats": {"score": None if number == 0 else .923},
                "description": "X"*4000 if number == 1 else "<img src='file:///must-not-load'> literal description",
                "versions": [{"state": "Published", "createdAt": "2026-10-01T00:00:00Z",
                              "hash": f"{number+1:040x}",
                              "downloadURL": "https://r2cdn.beatsaver.com/never-download.zip",
                              "coverURL": "https://cdn.beatsaver.com/never-load.jpg",
                              "previewURL": "https://cdn.beatsaver.com/never-play.mp3", "diffs": []}]})
        destination.write_text(json.dumps({"docs": docs}), encoding="utf-8")


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel
    from quest_tool.messages import tr
    from quest_tool.qt_ui.download_window import DownloadWindow

    app = QApplication([])
    app.setStyle("Fusion")
    errors = []
    def record_error(*args):
        errors.append(str(args[1]))
        sys.__excepthook__(*args)

    sys.excepthook = record_error
    main_thread = threading.get_ident()
    client = ScriptedClient()
    session = SearchSession(client)
    window = DownloadWindow()
    window.move(app.primaryScreen().availableGeometry().topLeft())
    window.enable_search(session)
    window.show()
    assert QTest.qWaitForWindowExposed(window, 5000)
    browser, model = window.browser, window.song_model
    changed_threads = []
    model.modelReset.connect(lambda: changed_threads.append(threading.get_ident()))

    def wait_ready():
        end = time.monotonic()+6
        while browser.state == "loading" and time.monotonic() < end:
            QTest.qWait(10)
        assert browser.state != "loading" and not errors, (browser.state, errors)

    def search(query):
        window.search.setText(query)
        QTest.keyClick(window.search, Qt.Key.Key_Return)
        wait_ready()

    wait_ready()
    assert model.rowCount() == 21 and model.has_next and browser.state == "ready"
    assert client.calls[-1][0]["curated"] == "true"
    assert client.calls[-1][0]["pageSize"] == "21"
    assert window.current_song.duration == tr("search.unknown")
    assert window.song_title.text() == "<b>Literal title</b>"
    assert window.song_title.textFormat() == Qt.TextFormat.PlainText
    assert window.song_info.textFormat() == Qt.TextFormat.PlainText
    assert "&lt;b&gt;" in model.data(model.index(0), Qt.ItemDataRole.ToolTipRole)
    window.select_page.click()
    window.next.click()
    wait_ready()
    assert model.page == 1 and model.rowCount() == 21 and len(model.checked) == 21
    assert client.calls[-1][1] == 1
    # A draft is not committed by pagination, retry, theme or language changes.
    window.search.setText("unsubmitted draft")
    window.previous.click()
    wait_ready()
    assert client.calls[-1][0]["q"] == "" and len(model.checked) == 21
    assert model.data(model.index(0), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    before_calls = len(client.calls)
    window.retranslate("en")
    window.change_theme("dark")
    window.views["list"].click()
    window.tabs.setCurrentIndex(1)
    window.tabs.setCurrentIndex(0)
    QTest.qWait(40)
    assert len(client.calls) == before_calls and len(model.checked) == 21
    # Query change resets selections, as in the old search UI.
    search("normal")
    assert not model.checked and browser.sort.isEnabled()
    browser.sort.setCurrentIndex(browser.sort.findData("Duration"))
    wait_ready()
    assert client.calls[-1][0]["order"] == "Duration"
    window.columns.setCurrentText("6")
    window.rows.setCurrentText("15")
    wait_ready()
    assert model.rowCount() == 90 and client.calls[-1][0]["pageSize"] == "90"
    for _page in range(2):
        window.select_page.click()
        window.next.click()
        wait_ready()
    assert model.page == 2 and model.rowCount() == 25 and not model.has_next
    window.select_page.click()
    assert len(model.checked) == len(model.selected_records) == 200
    assert browser.status.text() == tr("search.limit")
    assert not window.next.isEnabled()
    search("empty")
    assert browser.state == "empty" and model.rowCount() == 0 and not model.checked
    assert window.current_song is None and window.results.empty_text == tr("search.empty")
    assert not window.next.isEnabled()
    for code in ("rate_limit", "auth", "access", "network"):
        search(code)
        assert browser.state == "error" and browser.error == code
        assert browser.retry_button.isEnabled() and browser.status.text() == tr("dl.error."+code)
    # Retry commits the stored request, not the current unsubmitted draft.
    window.search.setText("draft")
    browser.retry_button.click()
    wait_ready()
    assert client.calls[-1][0]["q"] == "network"
    search("")
    assert browser.error == "query" and not model.rowCount()
    window.search.setText("")
    presets = {"Curated": "Curated", "Week": "Rating", "Month": "Rating", "Year": "Rating",
               "Latest": "Latest", "Verified": "Latest", "Ranked": "Rating", "Random": "Random"}
    for mode, order in presets.items():
        window.categories.buttons[mode].click()
        wait_ready()
        assert client.calls[-1][0]["order"] == order and not browser.sort.isEnabled()
        if mode in ("Week", "Month", "Year"):
            assert "from" in client.calls[-1][0]
    # Supersede a blocked transport, and explicitly inject late messages.
    window.search.setText("slow")
    window.search_button.click()
    end = time.monotonic()+3
    while not client.entered.is_set() and time.monotonic() < end:
        QTest.qWait(10)
    assert client.entered.is_set()
    stale = browser.generation
    window.search.setText("newest")
    window.search_button.click()
    session.tasks.emit("search_error", payload={"generation": stale, "code": "auth"})
    browser.poll()
    assert browser.state == "loading" and not model.rowCount()
    client.release.set()
    wait_ready()
    assert browser.state == "ready" and client.calls[-1][0]["q"] == "newest"
    client.entered.clear()
    client.release.clear()
    window.search.setText("slow")
    window.search_button.click()
    end = time.monotonic()+3
    while not client.entered.is_set() and time.monotonic() < end:
        QTest.qWait(10)
    stale = browser.generation
    browser.cancel_button.click()
    assert browser.state == "cancelled" and not browser.timer.isActive()
    session.tasks.emit("search_error", payload={"generation": stale, "code": "auth"})
    browser.poll()
    assert browser.state == "cancelled"
    client.release.set()
    for thread in session.tasks._threads:
        thread.join(2)
    search("normal")
    window.columns.setCurrentText("3")
    window.rows.setCurrentText("7")
    wait_ready()
    window.results.setCurrentIndex(model.index(1))
    before_calls = len(client.calls)
    for language in ("zh", "en"):
        for mode in ("light", "dark"):
            window.retranslate(language)
            window.change_theme(mode)
            available = window.screen().availableGeometry()
            frame_width = max(0, window.frameGeometry().width()-window.width())
            frame_height = max(0, window.frameGeometry().height()-window.height())
            for requested_width, requested_height in ((1000, 760), (1280, 824), (1600, 950)):
                width = min(requested_width, available.width()-frame_width)
                height = min(requested_height, available.height()-frame_height)
                window.move(available.topLeft())
                window.resize(width, height)
                QTest.qWait(40)
                assert window.width() == width and window.height() == height
                assert window.details.horizontalScrollBar().maximum() == 0
                assert window.results.width() >= 420 and window.details.width() >= 250
                assert window.footer.geometry().bottom() < window.player.geometry().top()
    assert len(client.calls) == before_calls, "Layout/theme/translation requested the network"
    if os.environ.get("QUEST_QT_SHOTS"):
        search("normal")
        window.columns.setCurrentText("3")
        window.rows.setCurrentText("7")
        wait_ready()
        window.views["covers"].click()
        target = Path(os.environ["QUEST_QT_SHOTS"])
        target.mkdir(parents=True, exist_ok=True)
        for language, mode in (("zh", "light"), ("en", "dark")):
            window.retranslate(language)
            window.change_theme(mode)
            QTest.qWait(60)
            window.grab().save(str(target/f"m3a-{language}-{mode}-{os.environ.get('QT_SCALE_FACTOR')}.png"))
    # Close during a blocked request; no UI wait or post-close mutation.
    client.entered.clear()
    client.release.clear()
    window.search.setText("slow")
    window.search_button.click()
    end = time.monotonic()+3
    while not client.entered.is_set() and time.monotonic() < end:
        QTest.qWait(10)
    start = time.monotonic()
    window.close()
    assert time.monotonic()-start < .3
    assert browser.closed and not browser.timer.isActive()
    session.tasks.emit("search_error", payload={"generation": browser.generation, "code": "auth"})
    browser.poll()
    client.release.set()
    for thread in session.tasks._threads:
        thread.join(2)
    QTest.qWait(30)
    assert changed_threads and set(changed_threads) == {main_thread}
    assert all(thread != main_thread for _params, _page, thread in client.calls)
    assert not errors, errors
    assert "tkinter" not in sys.modules and "customtkinter" not in sys.modules
    assert "quest_tool.audio" not in sys.modules and "quest_tool.app" not in sys.modules
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label, _key in window.texts if isinstance(label, QLabel))
    print("PASS M3A: real backend/controlled HTTP, 9 modes, sorting, 21/90 paging, 200 cap, drafts, unknowns, errors, retry, cancel, stale generations, close, UI thread")


def main():
    if "--child" in sys.argv:
        child()
        return
    for factor in ("1", "1.25", "1.5"):
        result = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--child"], cwd=ROOT,
                                env=dict(os.environ, QT_SCALE_FACTOR=factor), capture_output=True, timeout=100)
        print(factor, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace"))
        if result.returncode:
            raise SystemExit(result.returncode)
    with tempfile.TemporaryDirectory(prefix="quest-qt-M3A-entry-") as folder:
        code = """
import sys
from pathlib import Path
sys.path.insert(0,str(Path('tests/qt').resolve()))
from smoke_search_browser import ScriptedClient
from quest_tool.song_search import SearchSession
from quest_tool.qt_ui import search_browser
client=ScriptedClient()
search_browser.SearchSession=lambda: SearchSession(client)
from quest_tool.search_media import MediaClient
from PIL import Image
def controlled_cover(url, destination, cancel, progress, **kwargs):
    assert url == 'https://cdn.beatsaver.com/never-load.jpg'
    Image.new('RGB',(256,256),(40,100,180)).save(destination,format='PNG')
MediaClient.fetch=staticmethod(controlled_cover)
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from quest_tool.qt_ui.download_window import DownloadWindow
from quest_tool.qt_ui.bootstrap import run
show=DownloadWindow.show
seen=[]
def inspect(window):
    if window.browser.state=='loading':
        QTimer.singleShot(10,lambda:inspect(window));return
    seen.append(window.browser.state=='ready' and window.song_model.rowCount()==21)
    QApplication.instance().closeAllWindows()
def show_and_inspect(self):
    show(self);QTimer.singleShot(20,lambda:inspect(self))
DownloadWindow.show=show_and_inspect
assert run(['--data-dir',sys.argv[1]])==0
assert seen==[True] and len(client.calls)==1
path=Path(sys.argv[1])/'QuestSongCompare-QtPreview'
assert not (path/'preview.lock').exists()
assert 'Stopped M7A2C' in (path/'preview.log').read_text()
assert 'tkinter' not in sys.modules
"""
        subprocess.run([sys.executable, "-B", "-c", code, folder], cwd=ROOT, check=True, timeout=30)
    print("PASS M3A default production entry with controlled HTTP and runtime isolation")


if __name__ == "__main__":
    main()
