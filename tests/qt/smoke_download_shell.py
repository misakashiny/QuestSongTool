"""M2A real-window layout, local interaction and isolated bootstrap regression."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton
    from quest_tool.messages import catalog, tr
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.icons import line_icon

    app = QApplication([])
    app.setStyle("Fusion")
    errors = []

    def record_error(*args):
        errors.append(str(args[1]))
        sys.__excepthook__(*args)

    sys.excepthook = record_error
    results = []
    times = []
    for language in ("zh", "en"):
        for mode in ("light", "dark"):
            window = DownloadWindow(language, mode)
            screens = app.screens()
            window.move(max(screen.geometry().right() for screen in screens)+2000,
                        max(screen.geometry().bottom() for screen in screens)+2000)
            window.show()
            assert QTest.qWaitForWindowExposed(window, 5000)
            assert any(window.frameGeometry().intersects(screen.availableGeometry()) for screen in screens)
            window.hide()
            window.move(app.primaryScreen().availableGeometry().topLeft())
            window.show()
            assert QTest.qWaitForWindowExposed(window, 5000)
            QTest.qWait(40)
            assert window.columns.currentText() == "6" and window.rows.currentText() == "8"
            assert window.tabs.count() == 4
            initial_buttons = len(window.findChildren(QPushButton))
            assert len(window.categories.buttons) == 9
            assert all(not button.isEnabled() for button in window.unavailable)
            assert not window.seek.isEnabled() and not window.play.isEnabled()
            assert window.destination.isReadOnly() and window.destination.text() == ""
            window.search.setText("草稿 Draft")
            window.columns.setCurrentText("4")
            window.rows.setCurrentText("6")
            QTest.mouseClick(window.categories.buttons["Week"], Qt.MouseButton.LeftButton)
            assert window.category_title.text() == tr("browse.Week")
            QTest.mouseClick(window.views["list"], Qt.MouseButton.LeftButton)
            assert window.views["list"].isChecked() and not window.views["covers"].isChecked()
            footer_y = window.footer.mapTo(window, QPoint()).y()
            for index in (1, 2, 3, 0):
                QTest.mouseClick(window.tabs, Qt.MouseButton.LeftButton,
                                 pos=window.tabs.tabRect(index).center())
                QTest.qWait(30)
                assert window.pages.currentIndex() == index
                assert window.player.isVisible()
                # Catalog/tasks deliberately reserve their footer space for content.
                assert window.footer.isVisible()==(index in (0,3))
                if index==0:assert abs(window.footer.mapTo(window, QPoint()).y()-footer_y) <= 2
                assert window.search.text() == "草稿 Draft"
                assert window.columns.currentText() == "4" and window.rows.currentText() == "6"
            available = window.screen().availableGeometry()
            frame_width = max(0, window.frameGeometry().width()-window.width())
            frame_height = max(0, window.frameGeometry().height()-window.height())
            for requested_width, requested_height in ((1000, 760), (1280, 824), (1600, 950)):
                width = min(requested_width, available.width()-frame_width)
                height = min(requested_height, available.height()-frame_height)
                window.move(available.topLeft())
                window.resize(width, height)
                QTest.qWait(80)
                assert window.width() == width and window.height() == height, window.size()
                assert window.pages.height() >= 300
                assert window.details.horizontalScrollBar().maximum() == 0
                assert window.results.width() >= 420 and window.details.width() >= 250
                assert window.footer.geometry().bottom() < window.player.geometry().top()
                assert window.pages.geometry().bottom() < window.footer.geometry().top()
                for key,button in window.categories.buttons.items():
                    if key=="Search":continue
                    assert button.isVisible()
                    assert button.width() >= button.sizeHint().width(), (language, width, button.text(), button.size())
                    assert button.mapTo(window, QPoint()).x()+button.width() <= window.width()
                if language == "zh" and width == 1280:
                    assert window.categories.columns <= 8
                if language == "en" and width == 1000:
                    assert window.categories.columns < 9
                results.append([language, mode, width, height, window.categories.columns])
            for _ in range(10):
                for width, height in ((1000, 760), (1280, 824)):
                    start = time.perf_counter()
                    window.resize(width, height)
                    app.processEvents()
                    times.append((time.perf_counter()-start)*1000)
            # Resize the native splitter handle; neither pane may collapse.
            handle = window.splitter.handle(1)
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
            QTest.mouseMove(handle, handle.rect().center()+QPoint(-60, 0), 30)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton)
            assert min(window.splitter.sizes()) > 0
            window.retranslate("en" if language == "zh" else "zh")
            window.change_theme("dark" if mode == "light" else "light")
            QTest.qWait(50)
            assert window.search.text() == "草稿 Draft" and window.views["list"].isChecked()
            assert window.columns.currentText() == "4" and window.rows.currentText() == "6"
            assert window.category_title.text() == tr("browse.Week")
            window.retranslate(language)
            window.change_theme(mode)
            window.categories.buttons["Curated"].click()
            window.views["covers"].setChecked(True)
            window.search.clear()
            window.columns.setCurrentText("3")
            window.rows.setCurrentText("7")
            window.splitter.setSizes([900, 250])
            QTest.qWait(80)
            if os.environ.get("QUEST_QT_SHOTS"):
                target = Path(os.environ["QUEST_QT_SHOTS"])
                target.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(target / f"m2a-{language}-{mode}-{os.environ.get('QT_SCALE_FACTOR')}.png"))
            assert len(window.findChildren(QPushButton)) == initial_buttons
            window.close()
            window.deleteLater()
            QTest.qWait(10)
    assert not errors, errors
    assert "tkinter" not in sys.modules and "customtkinter" not in sys.modules
    assert "quest_tool.app" not in sys.modules and "quest_tool.audio" not in sys.modules
    assert line_icon("download", "#2563eb").cacheKey() == line_icon("download", "#2563eb").cacheKey()
    # Check literal translation references, since the legacy scanner only scans top-level modules.
    all_keys = catalog("zh")
    for path in (ROOT / "quest_tool" / "qt_ui").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    assert arg.value in all_keys, arg.value
    print(json.dumps({"scale": os.environ.get("QT_SCALE_FACTOR"), "cases": len(results),
                      "resize_samples": len(times),
                      "resize_p95_ms": sorted(times)[int(len(times)*.95)],
                      "resize_max_ms": max(times)}))


def main():
    if "--child" in sys.argv:
        child()
        return
    for factor in ("1", "1.25", "1.5"):
        result = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--child"],
                                cwd=ROOT, env=dict(os.environ, QT_SCALE_FACTOR=factor),
                                capture_output=True, timeout=90)
        print(result.stdout.decode("utf-8", "replace"))
        if result.returncode:
            print(result.stderr.decode("utf-8", "replace"))
            raise SystemExit(result.returncode)
    with tempfile.TemporaryDirectory(prefix="quest-qt-M2A-entry-") as folder:
        code = """
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from quest_tool.qt_ui.bootstrap import run
from quest_tool.qt_ui.download_window import DownloadWindow
show = DownloadWindow.show
seen = []
def close():
    app = QApplication.instance()
    seen.extend(w for w in app.topLevelWidgets() if isinstance(w, DownloadWindow))
    app.closeAllWindows()
def show_and_close(self):
    show(self)
    QTimer.singleShot(150, close)
DownloadWindow.show = show_and_close
assert run(['--data-dir', sys.argv[1], '--samples']) == 0
assert seen
path = Path(sys.argv[1])/'QuestSongCompare-QtPreview'
assert not (path/'preview.lock').exists()
assert 'Stopped M7A2C' in (path/'preview.log').read_text()
assert 'tkinter' not in sys.modules
"""
        subprocess.run([sys.executable, "-B", "-c", code, folder], cwd=ROOT,
                       check=True, timeout=30)
    print("[PASS] Qt M2A: 36 layouts, category reflow, fixed footer, tabs, local state, real entry")


if __name__ == "__main__":
    main()
