"""M2B real input, stable selection and model boundary regressions."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.song_model import SongModel, sample_songs
    from quest_tool.qt_ui.song_view import sample_cover

    app = QApplication([])
    app.setStyle("Fusion")
    errors = []
    sys.excepthook = lambda *_args: errors.append(str(_args[1]))
    model = SongModel(sample_songs())
    assert model.rowCount() == 21 and model.page_count == 3
    assert not model.data(model.index(-1))
    try:
        SongModel((model.songs[0], model.songs[0]))
        raise AssertionError("Duplicate keys accepted")
    except ValueError:
        pass
    window = DownloadWindow()
    window.move(app.primaryScreen().availableGeometry().topLeft())
    window.show()
    assert QTest.qWaitForWindowExposed(window, 5000)
    QTest.qWait(60)
    view = window.results
    model = window.song_model
    index = model.index(0)
    rect = view.visualRect(index).adjusted(4, 4, -4, -4)
    check, play = view.itemDelegate().controls(rect)
    assert check.size() == play.size() and check.width() == 32
    assert check.bottom() < play.top()
    assert view.visualRect(model.index(2)).top() == view.visualRect(index).top()
    assert view.visualRect(model.index(3)).top() > view.visualRect(index).top()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=check.center())
    assert model.checked == {model.songs[0].key}
    assert window.current_song.key == model.songs[0].key
    QTest.keyClick(view, Qt.Key.Key_Space)
    assert not model.checked
    QTest.keyClick(view, Qt.Key.Key_Space)
    QTest.mouseClick(window.next, Qt.MouseButton.LeftButton)
    assert model.page == 1 and len(model.checked) == 1
    QTest.mouseClick(window.select_page, Qt.MouseButton.LeftButton)
    assert len(model.checked) == 22
    QTest.mouseClick(window.previous, Qt.MouseButton.LeftButton)
    assert model.data(model.index(0), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    current = window.current_song.key
    QTest.mouseClick(window.views["list"], Qt.MouseButton.LeftButton)
    QTest.qWait(30)
    assert not view.covers and window.current_song.key == current
    rect = view.visualRect(model.index(0)).adjusted(4, 4, -4, -4)
    check, play = view.itemDelegate().controls(rect)
    assert check.bottom() < play.top() and check.size() == play.size()
    assert rect.contains(check) and rect.contains(play)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=play.center())
    assert len(model.checked) == 22 and not window.play.isEnabled()
    window.columns.setCurrentText("4")
    window.rows.setCurrentText("6")
    assert model.page_size == 24 and model.rowCount() == 24
    window.retranslate("en")
    window.change_theme("dark")
    for page in (1, 2, 0):
        window.tabs.setCurrentIndex(page)
    assert len(model.checked) == 22 and window.current_song.key == current
    model.paginate(999, 21)
    assert model.page == 2 and model.rowCount() == 6
    window.refresh_page()
    QTest.mouseClick(window.clear, Qt.MouseButton.LeftButton)
    assert not model.checked and not window.clear.isEnabled()
    assert len(window.findChildren(QPushButton)) < 40
    window.columns.setCurrentText("3")
    window.rows.setCurrentText("7")
    window.refresh_page(0)
    QTest.mouseClick(window.views["covers"], Qt.MouseButton.LeftButton)
    if os.environ.get("QUEST_QT_SHOTS"):
        folder = Path(os.environ["QUEST_QT_SHOTS"])
        folder.mkdir(parents=True, exist_ok=True)
        for language, mode in (("zh", "light"), ("en", "dark")):
            window.retranslate(language)
            window.change_theme(mode)
            QTest.qWait(80)
            window.grab().save(str(folder/f"m2b-{language}-{mode}-{os.environ.get('QT_SCALE_FACTOR')}.png"))
        window.views["list"].click()
        QTest.qWait(40)
        window.grab().save(str(folder/f"m2b-list-{os.environ.get('QT_SCALE_FACTOR')}.png"))
    assert sample_cover.cache_info().currsize <= 64
    window.close()
    empty = DownloadWindow(songs=[])
    assert empty.song_model.rowCount() == 0
    assert not empty.select_page.isEnabled() and not empty.next.isEnabled()
    assert empty.current_song is None
    empty.close()
    assert not errors, errors
    assert "quest_tool.audio" not in sys.modules and "quest_tool.app" not in sys.modules
    assert "tkinter" not in sys.modules and "customtkinter" not in sys.modules
    print("PASS M2B: model, real check/space, cross-page selection, 3x7, 4x6, list, details, empty, no business")


if __name__ == "__main__":
    if "--child" in sys.argv:
        child()
    else:
        for scale in ("1", "1.25", "1.5"):
            result = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--child"],
                                    cwd=ROOT, env=dict(os.environ, QT_SCALE_FACTOR=scale),
                                    capture_output=True, timeout=60)
            print(scale, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace"))
            if result.returncode:
                raise SystemExit(result.returncode)
