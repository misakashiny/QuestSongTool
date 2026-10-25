"""M1 Windows Qt smoke test; only own windows and temporary preview data."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def child():
    from PySide6.QtCore import Qt, qVersion
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMainWindow
    from quest_tool.messages import tr
    from quest_tool.qt_ui.main_window import MainWindow
    from quest_tool.qt_ui.runtime import PreviewRuntime
    from static_check import QT_WINDOW_METHODS, qt_inherited_calls

    assert all(hasattr(QMainWindow, name) for name in QT_WINDOW_METHODS)
    good = ast.parse("from PySide6.QtWidgets import QMainWindow\nclass View(QMainWindow):\n def f(self): self.resize(1,2); self.reszie(1,2)")
    assert len(qt_inherited_calls(good)) == 1
    assert not qt_inherited_calls(ast.parse("class View:\n def f(self): self.resize(1,2)"))
    app = QApplication([])
    app.setStyle("Fusion")
    errors = []
    def record_error(*args):
        errors.append(str(args[1]))
        sys.__excepthook__(*args)

    sys.excepthook = record_error
    with tempfile.TemporaryDirectory(prefix="quest-qt-M1-") as folder:
        base = Path(folder)
        sentinel = base / "settings.json"
        sentinel.write_text("production sentinel", encoding="utf-8")
        runtime = PreviewRuntime(base)
        try:
            assert runtime.path.parent == base.resolve()
            try:
                PreviewRuntime(base)
            except RuntimeError:
                pass
            else:
                raise AssertionError("second writer accepted")
            runtime.logger.info("foundation test")
        finally:
            runtime.close()
        again = PreviewRuntime(base)
        again.close()
        assert sentinel.read_text(encoding="utf-8") == "production sentinel"
        assert not (runtime.path / "preview.lock").exists()
        assert not (base / "songs_cache.db").exists()
        (runtime.path / "downloads.json").write_text("[]", encoding="utf-8")
        try:
            PreviewRuntime(base)
        except ValueError:
            pass
        else:
            raise AssertionError("production data accepted")
        timings = []
        for language in ("zh", "en"):
            for mode in ("light", "dark"):
                window = MainWindow(language, mode)
                assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
                window.move(app.primaryScreen().availableGeometry().topLeft())
                window.show()
                assert QTest.qWaitForWindowExposed(window, 5000)
                window.activateWindow()
                QTest.qWait(30)
                window.input.setFocus()
                QTest.keyClicks(window.input, "sample")
                QTest.keyClick(window.input, Qt.Key.Key_Tab)
                assert app.focusWidget() is window.check
                QTest.keyClick(window.check, Qt.Key.Key_Space)
                assert not window.check.isChecked()
                QTest.mouseClick(window.apply, Qt.MouseButton.LeftButton)
                assert window.result.text() == tr("qt.applied")
                QTest.mouseClick(window.reset, Qt.MouseButton.LeftButton)
                assert window.input.text() == "" and window.check.isChecked()
                assert not window.unavailable.isEnabled()
                window.input.setText("草稿 Draft")
                window.language.setCurrentIndex(1-window.language.currentIndex())
                window.mode.setCurrentIndex(1-window.mode.currentIndex())
                assert window.input.text() == "草稿 Draft"
                for width, height in ((640, 480), (1000, 720), (1280, 824)):
                    window.resize(width, height)
                    QTest.qWait(30)
                    for control in (window.input, window.mode, window.language,
                                    window.apply, window.reset, window.exit_button):
                        assert control.width() >= control.minimumSizeHint().width()
                        assert control.height() >= 30
                    assert window.centralWidget().horizontalScrollBar().maximum() == 0
                for index in range(30):
                    start = time.perf_counter()
                    window.move(40+index, 40+index)
                    window.resize(900+index, 650+index)
                    app.processEvents()
                    timings.append((time.perf_counter()-start)*1000)
                window.showMaximized()
                QTest.qWait(30)
                assert window.isMaximized()
                window.showMinimized()
                QTest.qWait(30)
                assert window.isMinimized()
                window.showNormal()
                QTest.qWait(30)
                assert not window.isMinimized() and not window.isMaximized()
                if os.environ.get("QUEST_QT_SHOTS"):
                    target = Path(os.environ["QUEST_QT_SHOTS"])
                    target.mkdir(parents=True, exist_ok=True)
                    shot = f"m1-{window.language.currentData()}-{window.mode.currentData()}-{os.environ.get('QT_SCALE_FACTOR')}.png"
                    assert window.grab().save(str(target / shot))
                window.close()
                app.processEvents()
                assert not window.isVisible()
                window.deleteLater()
                QTest.qWait(10)
        assert not errors, errors
        assert "tkinter" not in sys.modules and "customtkinter" not in sys.modules
        assert "quest_tool.app" not in sys.modules
        print(json.dumps({"qt": qVersion(), "platform": app.platformName(),
                          "scale": os.environ.get("QT_SCALE_FACTOR"),
                          "cases": 4, "resize_samples": len(timings),
                          "p95_ms": sorted(timings)[int(len(timings)*.95)],
                          "median_ms": statistics.median(timings), "max_ms": max(timings)}))


def main():
    if "--child" in sys.argv:
        child()
        return
    for factor in ("1", "1.25", "1.5"):
        env = dict(os.environ, QT_SCALE_FACTOR=factor)
        result = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--child"],
                                env=env, cwd=ROOT, capture_output=True, timeout=90)
        print(result.stdout.decode("utf-8", "replace"))
        if result.returncode:
            print(result.stderr.decode("utf-8", "replace"))
            raise SystemExit(result.returncode)
    # Exercise the real bootstrap event loop and finally block in a fresh process.
    with tempfile.TemporaryDirectory(prefix="quest-qt-entry-") as folder:
        code = """
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from quest_tool.qt_ui.bootstrap import run
original = sys.excepthook
timer = QTimer()
def finish():
    app = QApplication.instance()
    assert app and any(w.isVisible() for w in app.topLevelWidgets())
    app.closeAllWindows()
timer.timeout.connect(finish)
# QTimer needs an event dispatcher; schedule when the window is first shown.
from quest_tool.qt_ui.main_window import MainWindow
show = MainWindow.show
def show_and_schedule(self):
    show(self)
    QTimer.singleShot(150, finish)
MainWindow.show = show_and_schedule
assert run(['--data-dir', sys.argv[1], '--lang', 'en', '--components']) == 0
assert sys.excepthook is original
assert 'tkinter' not in sys.modules
assert not (Path(sys.argv[1])/'QuestSongCompare-QtPreview'/'preview.lock').exists()
assert 'Stopped ' in (Path(sys.argv[1])/'QuestSongCompare-QtPreview'/'preview.log').read_text()
"""
        subprocess.run([sys.executable, "-B", "-c", code, folder], cwd=ROOT,
                       check=True, timeout=30)
    print("[PASS] Qt M1: 3 scales, 12 windows, isolated paths, locks, keyboard, lifecycle")


if __name__ == "__main__":
    main()
