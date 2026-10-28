"""Offline preset persistence, removal semantics and native dialog interactions."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.filter_presets import FilterPresets, conditions, without
    from quest_tool.search_filters import filter_params
    from quest_tool.messages import tr
    from smoke_prefetch import Session

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    values = dict(curated=False, chroma=False, automapper='human', leaderboard='BeatLeader',
                  tags={'tech': True, 'pop': False}, environments=['DefaultEnvironment'],
                  characteristics=['Standard', 'OneSaber'], minDuration=0, maxDuration=240,
                  minRating=90, **{'from': '2020-01-01', 'to': '2025-01-01'})
    original = copy.deepcopy(values)
    groups = conditions(values)
    assert len(groups) == 13 and conditions({}) == [] and conditions({'automapper': 'all'}) == []
    assert without(values, ('minDuration', 'maxDuration')).get('maxDuration') is None
    assert without(values, ('tags', 'tech'))['tags'] == {'pop': False}
    assert without(values, ('characteristics', 'Standard'))['characteristics'] == ['OneSaber']
    assert values == original
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = FilterPresets(root)
        assert store.load() == {} and not store.path.exists()
        store.save('日常', values)
        assert FilterPresets(root).load()['日常'] == values
        raw = store.path.read_bytes()
        for name in ('日常', '', 'a'*41, 'bad\nname'):
            try: store.save(name, {})
            except ValueError: pass
            else: raise AssertionError(name)
            assert store.path.read_bytes() == raw
        store.save('ABC', {})
        try: store.save('abc', {})
        except ValueError: pass
        else: raise AssertionError('casefold duplicate')
        for index in range(18): store.save(str(index), {})
        assert len(store.load()) == 20
        try: store.save('limit', {})
        except ValueError: pass
        else: raise AssertionError('limit')
        store.delete('ABC')
        assert 'ABC' not in FilterPresets(root).load()
        for invalid in (b'{broken', b'{"version":2,"presets":{}}', b'{"version":true,"presets":{}}', b'x'*65537):
            store.path.write_bytes(invalid)
            try: store.save('safe', {})
            except ValueError: pass
            else: raise AssertionError('corrupt file overwritten')
            assert store.path.read_bytes() == invalid
        store.path.unlink()
        for language, theme in (('zh', 'light'), ('en', 'dark')):
            front = Session()
            window = DownloadWindow(language=language, mode=theme)
            window.enable_search(front)
            browser = window.browser
            tools = browser.filter_tools
            tools.configure(root/language)
            window.categories.buttons['Latest'].click()
            window.search.setText('query draft')
            assert tools.commit(values)
            assert browser.params[0] == 'query draft' and len(tools.menu.actions()) == 13
            assert window.filter_button.text() == tr('filter.active', count=13)
            front.finish(0); browser.poll()
            calls = len(front.calls)
            tools.refresh()
            window.retranslate(language)
            assert len(front.calls) == calls
            window.show()
            window.resize(1000, 760)
            QTest.qWait(50)
            assert tools.summary.geometry().right() < tools.clear.geometry().left()
            assert tools.summary.height() == tools.clear.height()
            assert tools.row.height() < window.height()/6 and tools.preset_action.isEnabled()
            menu=window.options.menu();menu.popup(window.options.mapToGlobal(window.options.rect().bottomLeft()))
            QTest.qWait(30)
            QTest.mouseClick(menu,Qt.MouseButton.LeftButton,pos=menu.actionGeometry(tools.preset_action).center())
            dialog = tools.dialog
            dialog.name.setText('daily')
            QTest.mouseClick(dialog.save, Qt.MouseButton.LeftButton)
            assert tools.store.load()['daily'] == values and len(front.calls) == calls
            dialog.list.setCurrentRow(0)
            dialog.reject(); QTest.qWait(10)
            assert tools.dialog is None and browser.filters == values
            tools.menu.actions()[0].trigger()
            assert 'curated' not in browser.filters and browser.filters['chroma'] is False
            assert len(front.calls) == calls+1
            tools.open_presets()
            dialog = tools.dialog
            dialog.list.setCurrentRow(0)
            QTest.mouseClick(dialog.load, Qt.MouseButton.LeftButton)
            QTest.qWait(10)
            assert tools.dialog is None and browser.filters == values
            # Browse constraints reject a conflicting preset before changing anything.
            window.categories.buttons['Curated'].click()
            before = copy.deepcopy(browser.filters)
            calls = len(front.calls)
            tools.open_presets(); dialog = tools.dialog
            dialog.list.setCurrentRow(0)
            dialog.load.click()
            assert tools.dialog is dialog and dialog.error.text()
            assert browser.filters == before and len(front.calls) == calls
            dialog.delete.click()
            assert not tools.store.load() and browser.filters == before
            dialog.reject(); QTest.qWait(10)
            # Original filter drafts still survive tab switches and cancel.
            browser.open_filters(); draft = browser.filter_window
            draft.bounds['minRating'].setText('80')
            draft.tabs.setCurrentIndex(1); draft.tabs.setCurrentIndex(3)
            assert draft.bounds['minRating'].text() == '80'
            draft.reject(); QTest.qWait(10)
            assert browser.filters == before
            tools.clear.click()
            assert browser.filters == {} and filter_params(browser.filters) == filter_params({})
            tools.open_presets()
            shot_dir = os.environ.get('QUEST_QT_SHOTS')
            if shot_dir:
                tools.dialog.reject(); QTest.qWait(10)
                window.categories.buttons['Latest'].click()
                assert tools.commit(values)
                front.finish(0); browser.poll()
                QTest.qWait(20)
                Path(shot_dir).mkdir(parents=True, exist_ok=True)
                window.grab().save(str(Path(shot_dir)/('filter-summary-'+theme+'.png')))
                tools.open_presets()
                tools.dialog.name.setText('Daily filters')
                tools.dialog.save.click()
                QTest.qWait(20)
                tools.dialog.grab().save(str(Path(shot_dir)/('filter-presets-'+theme+'.png')))
            window.close(); QTest.qWait(10)
            assert tools.dialog is None and front.closed
        assert 'miniaudio' not in sys.modules and not errors, errors
    print('PASS persistent presets, limits/corrupt preservation, summary removal, category conflict, draft cancel, native actions and close')


def main():
    if '--child' in sys.argv:
        child(); return
    for scale in ('1', '1.25', '1.5'):
        result = subprocess.run([sys.executable, '-B', __file__, '--child'], cwd=ROOT,
            env=dict(os.environ, QT_SCALE_FACTOR=scale, PYTHONIOENCODING='utf-8'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        print(scale, result.stdout.decode('utf-8', errors='replace'), flush=True)
        assert result.returncode == 0


if __name__ == '__main__': main()
