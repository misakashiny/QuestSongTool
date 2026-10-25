"""Offline explicit startup preference and corrupt-file preservation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]


def child():
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.messages import tr
    from smoke_prefetch import Session

    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for language in ('zh', 'en'):
            folder = root/language
            def open_window():
                session = Session()
                window = DownloadWindow(language=language)
                window.browse_sandbox = folder
                window.enable_search(session)
                assert len(session.calls) == 1
                return window, session, window.browser.preferences
            window, session, prefs = open_window()
            assert window.browser.category() == 'Curated' and not prefs.path.exists()
            window.categories.buttons['Latest'].click()
            window.search.setText('unsaved query')
            window.browser.filters = {'minRating': 80}
            calls = len(session.calls)
            prefs.save.trigger()
            assert prefs.load() == 'Latest' and len(session.calls) == calls
            assert json.loads(prefs.path.read_text()) == {'version': 1, 'category': 'Latest'}
            window.retranslate('en' if language == 'zh' else 'zh')
            assert tr('browse.Latest') in prefs.title.text() and len(session.calls) == calls
            window.close()
            window, session, prefs = open_window()
            assert window.browser.category() == 'Latest'
            assert window.browser.params[1] == 'Latest' and window.browser.filters == {}
            assert session.calls[0][0] == '' and session.calls[0][1] == 0
            for category in ('Search', 'Random'):
                window.categories.buttons[category].click()
                prefs.refresh()
                raw = prefs.path.read_bytes()
                assert not prefs.save.isEnabled() and not prefs.commit(category)
                assert prefs.path.read_bytes() == raw
            calls = len(session.calls)
            prefs.reset.trigger()
            assert prefs.load() == 'Curated' and len(session.calls) == calls
            assert window.browser.category() == 'Random'  # Next-open only.
            window.close()
            for raw in (b'{broken', b'\xff', b'x'*4097,
                        b'{"version":2,"category":"Latest"}',
                        b'{"version":true,"category":"Latest"}',
                        b'{"version":1,"category":"Search"}',
                        b'{"version":1,"category":"Random"}',
                        b'{"version":1,"category":"Latest","query":"secret"}'):
                prefs.path.write_bytes(raw)
                window, session, prefs = open_window()
                assert prefs.error and window.browser.category() == 'Curated'
                calls = len(session.calls)
                assert not prefs.commit('Latest') and prefs.path.read_bytes() == raw
                assert len(session.calls) == calls
                window.close()
            prefs.path.unlink()
        assert not errors
    print('startup preference persistence, single initial request, draft isolation, reset and corrupt files: PASS')


if __name__ == '__main__':
    if '--child' in sys.argv:
        child()
    else:
        for scale in ('1', '1.25', '1.5'):
            subprocess.run([sys.executable, '-B', __file__, '--child'],
                           env=dict(os.environ, QT_SCALE_FACTOR=scale), check=True)
