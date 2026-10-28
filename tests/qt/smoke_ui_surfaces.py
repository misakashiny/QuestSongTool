"""Native page geometry, wrapping actions and passive navigation in isolation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QWidget
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.wireless_form import WirelessForm
    from quest_tool.qt_ui.surfaces import WrapLayout

    app = QApplication([]);app.setStyle('Fusion');errors = []
    def error(kind, value, tb):
        errors.append(repr(value));sys.__excepthook__(kind, value, tb)
    sys.excepthook = error
    output = Path(os.environ['QUEST_QT_SHOTS']);output.mkdir(parents=True, exist_ok=True)

    def check_strips(window):
        for frame in window.findChildren(QFrame):
            if not frame.isVisible() or not isinstance(frame.layout(), WrapLayout):continue
            rectangles = []
            for i in range(frame.layout().count()):
                widget = frame.layout().itemAt(i).widget()
                if not widget.isVisible():continue
                rect = QRect(widget.mapTo(frame, QPoint()), widget.size())
                assert frame.rect().contains(rect), ('action outside footer', widget.text(), rect, frame.size())
                assert widget.width() >= widget.minimumSizeHint().width(), ('truncated action', widget.text())
                assert not any(rect.intersects(other) for other in rectangles), ('overlap', widget.text())
                rectangles.append(rect)

    with tempfile.TemporaryDirectory(prefix='ui-surfaces-') as temporary, \
            patch('socket.socket', side_effect=AssertionError('network forbidden')), \
            patch('subprocess.Popen', side_effect=AssertionError('device/process forbidden')), \
            patch('quest_tool.audio.player', side_effect=AssertionError('audio forbidden')):
        base = Path(temporary)
        for language, mode in (('zh', 'light'), ('en', 'dark')):
            library = LibraryWindow(base/('library-'+language), language, mode)
            download = DownloadWindow(language, mode, songs=[])
            download.enable_downloads(base/('download-'+language))
            try:
                library.show();QTest.qWait(100)
                library.playlists.show();QTest.qWait(180)
                lists = library.playlists.dialog
                lists.resize(700, 520);QTest.qWait(100)
                assert lists.grab().save(str(output/f'surfaces-local-compact-{language}.png'))
                assert lists.width() <= 700 and lists.height() <= 520, ('local playlist minimum', lists.size())
                assert lists.table.viewport().height() >= 65, lists.table.viewport().size()
                check_strips(lists)
                before = len(lists.findChildren(QWidget))
                for width in (1000, 700, 800, 700):
                    lists.resize(width, 520);QTest.qWait(30);check_strips(lists)
                assert len(lists.findChildren(QWidget)) == before
                assert not lists.download.isEnabled()
                QTest.mouseClick(lists.help_button,Qt.MouseButton.LeftButton)
                assert lists.hint.isVisible() and lists.hint.text()==lists.help_button.toolTip()
                QTest.mouseClick(lists.help_button,Qt.MouseButton.LeftButton)
                assert not lists.hint.isVisible()
                lists.close()
                library.workflow.show_tasks();QTest.qWait(100)
                tasks = library.workflow.dialog;tasks.resize(650, 420);QTest.qWait(50);check_strips(tasks)
                assert not library.workflow.task_buttons['ux.transfer_details'].isEnabled()
                tasks.close();assert not library.workflow.timer.isActive()
                wireless = WirelessForm(library);wireless.show();wireless.resize(480, 560);QTest.qWait(80)
                wireless.mode.setCurrentIndex(2);QTest.qWait(30)
                assert wireless.fields['code'].isVisible() and not wireless.read_button.isEnabled()
                assert wireless.run_button.mapTo(wireless, QPoint()).y() < wireless.cancel_button.mapTo(wireless, QPoint()).y()
                wireless.close()
                download.show();download.resize(1000, 760);QTest.qWait(100)
                download.pages.setCurrentWidget(download.import_page);QTest.qWait(100)
                importer = download.download_browser.importer
                check_strips(download)
                assert importer.table.viewport().height() >= 90
                importer.input.setPlainText('abc\ndef')
                for width in (1400, 1000, 1250, 1000):
                    download.resize(width, 760);QTest.qWait(25);check_strips(download)
                assert importer.input.toPlainText() == 'abc\ndef'
                importer.open_playlist();QTest.qWait(100)
                dialog = importer.playlist_dialog;dialog.resize(620, 440);QTest.qWait(100)
                assert dialog.width() <= 620 and dialog.height() <= 440, ('playlist import minimum', dialog.size())
                check_strips(dialog)
                assert not dialog.start.isEnabled() and not download.download_browser.manager.snapshot()
                # Keyboard focus still travels through the original native actions.
                dialog.read.setFocus();QTest.keyClick(dialog.read, Qt.Key.Key_Tab)
                assert isinstance(app.focusWidget(), QPushButton)
                assert dialog.grab().save(str(output/f'surfaces-import-{language}.png'))
                dialog.close()
                assert download.grab().save(str(output/f'surfaces-page-{language}.png'))
                download.show_tasks();QTest.qWait(60)
                assert download.download_browser.table.viewport().height() >= 90
                assert not errors, errors
            finally:
                download.close();download.download_browser.wait_closed()
                library.close();library.wait_closed()
                app.processEvents()
    assert not errors, errors
    print('PASS native surfaces: compact bilingual pages, wrapping, keyboard, stable drafts/widgets, passive navigation and close')


if __name__ == '__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1', '1.25', '1.5'):
            subprocess.run([sys.executable, '-B', __file__, '--child'], cwd=ROOT,
                           env=dict(os.environ, QT_SCALE_FACTOR=scale), check=True, timeout=100)
