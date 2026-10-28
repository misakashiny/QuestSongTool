"""Qt filter parity, real interactions, layout and existing search transport."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QStyle, QStyleOptionComboBox
    from quest_tool.messages import set_language, tr
    from quest_tool.search_catalog import ENVIRONMENTS, TAGS
    from quest_tool.search_filters import CHARACTERISTICS, FLAGS, RANGES, filter_params
    from quest_tool.qt_ui.filter_dialog import CheckOption, FilterDialog
    from quest_tool.qt_ui.filter_presets import conditions
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.theme import apply_theme
    from quest_tool.song_search import SearchSession
    from smoke_search_browser import ScriptedClient

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def record_error(kind, value, traceback):
        errors.append(repr(value))
        sys.__excepthook__(kind, value, traceback)
    sys.excepthook = record_error
    output = Path(os.environ['QUEST_QT_SHOTS'])
    output.mkdir(parents=True, exist_ok=True)
    measurements = []
    # Every exposed value, including False (not confused with "Any").
    values = {'automapper': 'ai', 'leaderboard': 'BeatLeader',
              **{key: bool(i % 2) for i, key in enumerate(FLAGS)},
              'tags': {key: bool(i % 2) for i, key in enumerate(TAGS)},
              'characteristics': list(CHARACTERISTICS), 'environments': list(ENVIRONMENTS),
              'from': '2026-01-01', 'to': '2026-10-01'}
    for name, limit in RANGES.items():
        values['min'+name] = '0'
        values['max'+name] = str(limit)
    immutable = copy.deepcopy(values)
    set_language('zh')
    apply_theme(app)
    dialog = FilterDialog(values=values)
    applied = []
    dialog.applied.connect(applied.append)
    dialog.show()
    assert QTest.qWaitForWindowExposed(dialog, 5000)
    assert len(dialog.tri) == 8 and len(dialog.modes) == 8
    assert len(dialog.tags) == 45 and len(dialog.env) == 47 and len(dialog.bounds) == 16
    assert dialog.draft() == values and filter_params(dialog.draft()) == filter_params(values)
    identities = {id(widget) for widget in dialog.findChildren(QComboBox)}
    for index in (1, 2, 3, 0)*4:
        dialog.tabs.setCurrentIndex(index)
        QTest.qWait(2)
        assert dialog.draft() == values
    assert identities == {id(widget) for widget in dialog.findChildren(QComboBox)}
    assert values == immutable and not applied
    # All tri-state options keep enum identity, regardless of translated labels.
    for key, menu in dialog.tri.items():
        for index in range(3):
            menu.setCurrentIndex(index)
            item = dialog.draft()
            assert (key not in item) if index == 0 else item[key] is (index == 1)
    dialog.reject()
    assert not applied and values == immutable

    fresh = FilterDialog()
    assert fresh.ai.currentData() == 'human'
    fresh.reset_button.click()
    expected = {'automapper': 'all', 'leaderboard': 'All', 'tags': {},
                'environments': [], 'characteristics': []}
    assert fresh.draft() == expected
    fresh.applied.connect(applied.append)
    fresh.show()
    assert QTest.qWaitForWindowExposed(fresh, 5000)
    for fields in ({'minBpm': '200', 'maxBpm': '100'}, {'minNps': 'nan'},
                   {'maxDuration': '1.5'}, {'minRating': '101'},
                   {'from': '2026-02-30'}, {'from': '2026-10-01', 'to': '2026-01-01'}):
        fresh.reset()
        for key, value in fields.items():
            fresh.bounds[key].setText(value)
        fresh.apply_button.click()
        QTest.qWait(5)
        assert fresh.isVisible() and fresh.error.isVisible() and not applied
        assert all(fresh.bounds[key].text() == value for key, value in fields.items())
    fresh.reset()
    assert fresh.error.isHidden()
    # Real Space and mouse on the wrapped label both toggle the native checkbox.
    fresh.tabs.setCurrentIndex(0)
    check = fresh.modes['Standard']
    cell = check.parentWidget()
    assert isinstance(cell, CheckOption)
    fresh.panels['general'].ensureWidgetVisible(cell)
    QTest.qWait(20)
    check.setFocus()
    QTest.keyClick(check, Qt.Key.Key_Space)
    assert check.isChecked()
    QTest.mouseClick(cell.label, Qt.MouseButton.LeftButton)
    assert not check.isChecked()
    check.setEnabled(False)
    QTest.mouseClick(cell.label, Qt.MouseButton.LeftButton)
    assert not check.isChecked()
    check.setEnabled(True)
    check.setFocus()
    QTest.keyClick(check, Qt.Key.Key_Tab)
    assert app.focusWidget() is not check
    fresh.reset()
    fresh.tabs.setCurrentIndex(3)
    fresh.bounds['minBpm'].setFocus()
    QTest.keyClicks(fresh.bounds['minBpm'], '120')
    QTest.keyClick(fresh.bounds['minBpm'], Qt.Key.Key_Return)
    QTest.qWait(10)
    assert len(applied) == 1 and applied[-1]['minBpm'] == '120' and not fresh.isVisible()

    # Both languages, themes, narrow/normal/wide, all four pages at each process DPI.
    for language in ('zh', 'en'):
        set_language(language)
        for theme in ('light', 'dark'):
            apply_theme(app, theme)
            layout_dialog = FilterDialog(values=values)
            layout_dialog.show()
            assert QTest.qWaitForWindowExposed(layout_dialog, 5000)
            available = layout_dialog.screen().availableGeometry()
            frame_width = max(0, layout_dialog.frameGeometry().width()-layout_dialog.width())
            frame_height = max(0, layout_dialog.frameGeometry().height()-layout_dialog.height())
            for requested_width, requested_height in ((460, 420), (760, 680), (1000, 800)):
                # Exercise real usable sizes; Windows can clamp an off-screen
                # 800px request at 150% before the exact-geometry assertion.
                width = min(requested_width, available.width()-frame_width)
                height = min(requested_height, available.height()-frame_height)
                layout_dialog.move(available.topLeft())
                layout_dialog.resize(width, height)
                for index, (key, panel) in enumerate(layout_dialog.panels.items()):
                    layout_dialog.tabs.setCurrentIndex(index)
                    QTest.qWait(25)
                    assert layout_dialog.width() == width and layout_dialog.height() == height
                    assert layout_dialog.draft() == values
                    assert panel.horizontalScrollBar().maximum() == 0, (language, theme, width, key)
                    assert layout_dialog.footer.geometry().top() >= layout_dialog.tabs.geometry().bottom()
                    assert layout_dialog.footer.geometry().bottom() < layout_dialog.height()
                    for button in (layout_dialog.reset_button, layout_dialog.cancel_button, layout_dialog.apply_button):
                        assert button.width() >= button.sizeHint().width()
                        assert button.height() >= 30
                    for label in panel.widget().findChildren(QLabel):
                        assert label.textFormat() == Qt.TextFormat.PlainText
                        assert label.width() > 0 and label.height() > 0
                        assert label.heightForWidth(label.width()) <= label.height()+2, (language, key, width, label.text(), label.size())
                    for menu in panel.widget().findChildren(QComboBox):
                        option = QStyleOptionComboBox()
                        option.initFrom(menu)
                        option.rect = menu.rect()
                        field = menu.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                                            QStyle.SubControl.SC_ComboBoxEditField, menu)
                        assert field.width() >= max(menu.fontMetrics().horizontalAdvance(menu.itemText(i))
                                                    for i in range(menu.count())), (language, menu.accessibleName(), field)
                    measurements.append((language, theme, width, height, key, layout_dialog.devicePixelRatioF()))
                    panel.verticalScrollBar().setValue(0)
                    QTest.qWait(2)
                    if width == 760:
                        assert layout_dialog.grab().save(str(output/f'{language}-{theme}-{key}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                    panel.verticalScrollBar().setValue(panel.verticalScrollBar().maximum())
                    QTest.qWait(2)
                layout_dialog.panels['general'].verticalScrollBar().setValue(0)
            layout_dialog.reject()

    # Real SearchSession/SearchClient plus controlled HTTP, never real media or ZIP.
    set_language('zh')
    window = DownloadWindow()
    window.columns.setCurrentText('3');window.rows.setCurrentText('7')
    client = ScriptedClient()
    window.enable_search(SearchSession(client))
    window.show()
    assert QTest.qWaitForWindowExposed(window, 5000)
    browser, model = window.browser, window.song_model
    def wait_ready():
        end = time.monotonic()+6
        while browser.state == 'loading' and time.monotonic() < end:
            QTest.qWait(10)
        assert browser.state != 'loading', errors
    wait_ready()
    model.select_page()
    calls = len(client.calls)
    window.filter_button.click()
    first = browser.filter_window
    browser.open_filters()
    assert browser.filter_window is first
    first.ai.setCurrentIndex(first.ai.findData('ai'))
    first.modes['OneSaber'].setChecked(True)
    first.cancel_button.click()
    QTest.qWait(5)
    assert browser.filter_window is None and not browser.filters
    assert len(client.calls) == calls and len(model.checked) == 21
    window.filter_button.click()
    dialog = browser.filter_window
    assert dialog.ai.currentData() == 'human'
    dialog.ai.setCurrentIndex(dialog.ai.findData('ai'))
    dialog.modes['OneSaber'].setChecked(True)
    dialog.tags['tech'].setCurrentIndex(2)
    dialog.env['DefaultEnvironment'].setChecked(True)
    dialog.bounds['minRating'].setText('80')
    dialog.bounds['from'].setText('2026-01-01')
    dialog.apply_button.click()
    wait_ready()
    assert not model.checked and browser.filter_window is None
    params, page, _thread = client.calls[-1]
    assert page == 0 and params['automapper'] == 'false'
    assert params['characteristics'] == 'OneSaber' and params['tags'] == '!tech'
    assert params['environments'] == 'DefaultEnvironment' and params['minRating'] == '0.8'
    assert params['from'] == '2026-01-01T00:00:00Z'
    assert window.filter_button.text() == tr('filter.active', count=len(conditions(browser.filters)))
    model.select_page()
    window.next.click()
    wait_ready()
    assert model.page == 1 and len(model.checked) == 21
    assert client.calls[-1][0]['tags'] == '!tech'
    window.filter_button.click()
    dialog = browser.filter_window
    saved = copy.deepcopy(browser.filters)
    dialog.reset_button.click()
    dialog.close()
    QTest.qWait(5)
    assert browser.filters == saved
    window.filter_button.click()
    assert browser.filter_window.draft() == saved
    browser.filter_window.reject()
    # Blank search + Apply follows old Latest fallback; unsubmitted draft stays on cancel.
    window.search.clear()
    window.categories.buttons['Search'].setChecked(True)
    window.update_category()
    browser.open_filters()
    browser.filter_window.reset()
    browser.filter_window.apply()
    wait_ready()
    assert browser.category() == 'Latest' and browser.filters == expected
    assert client.calls[-1][0]['order'] == 'Latest'
    # Conflict is handled by existing backend error path without transport requests.
    browser.open_filters()
    browser.filter_window.tri['curated'].setCurrentIndex(2)
    browser.filter_window.apply()
    wait_ready()
    calls = len(client.calls)
    window.categories.buttons['Curated'].click()
    wait_ready()
    assert browser.state == 'error' and browser.error == 'filters' and len(client.calls) == calls
    browser.open_filters()
    browser.filter_window.reset()
    browser.filter_window.apply()
    wait_ready()
    assert browser.state == 'ready'
    # Applying filters replaces an in-flight request; stale results cannot win.
    window.search.setText('slow')
    browser.search()
    end = time.monotonic()+3
    while not client.entered.is_set() and time.monotonic() < end:
        QTest.qWait(5)
    assert client.entered.is_set()
    browser.open_filters()
    browser.filter_window.ai.setCurrentIndex(browser.filter_window.ai.findData('human'))
    window.search.setText('after_filter')
    browser.filter_window.apply()
    client.release.set()
    wait_ready()
    assert browser.state == 'ready' and client.calls[-1][0]['q'] == 'after_filter'
    assert 'automapper' not in client.calls[-1][0]
    browser.open_filters()
    window.close()
    QTest.qWait(10)
    assert browser.closed and browser.filter_window is None
    assert not errors, errors
    assert not any(name in sys.modules for name in ('tkinter', 'customtkinter', 'quest_tool.app', 'quest_tool.audio'))
    (output/f'layout-{os.environ.get("QT_SCALE_FACTOR")}.json').write_text(json.dumps(measurements), encoding='utf-8')
    print('PASS M3B: full catalog/validator parity, draft/reset/cancel/apply, keyboard, 48 layouts, transport params/paging/conflict/close, no Tk/audio')


if __name__ == '__main__':
    if '--child' in sys.argv:
        child()
    else:
        with tempfile.TemporaryDirectory(prefix='quest-filter-shots-') as folder:
            for scale in ('1', '1.25', '1.5'):
                env = dict(os.environ, QT_SCALE_FACTOR=scale,QUEST_QT_SHOTS=os.environ.get('QUEST_QT_SHOTS',folder))
                subprocess.run([sys.executable, '-B', __file__, '--child'], env=env, cwd=ROOT, check=True, timeout=180)
