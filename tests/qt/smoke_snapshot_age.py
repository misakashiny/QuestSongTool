"""Offline snapshot age, translation, geometry and device lifecycle validation."""
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.messages import tr
    from quest_tool.qt_ui.library_window import LibraryWindow
    app = QApplication([]);app.setStyle('Fusion')
    errors = []
    sys.excepthook = lambda kind, value, tb: (errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    def wait(predicate):
        deadline = time.monotonic()+5
        while not predicate() and time.monotonic() < deadline:
            app.processEvents();time.sleep(.003)
        assert predicate() and not errors, errors
    class Provider:
        def __init__(self):self.calls = [];self.failed = False;self.partial = False
        def list_devices(self, _cancel):
            self.calls.append('list')
            return [dict(kind='adb',device_id='fixture',root='/sdcard/CustomLevels',label='Quest fixture',
                         state='device',model='Quest 3',codename='eureka',brand='oculus',identity_verified=True)]
        def read_library(self, selected, _cancel):
            self.calls.append('read')
            if self.failed:raise OSError('controlled offline failure')
            return dict(connected=True, device=selected,
                        rows=[dict(folder='one',title='Song',author='Artist',error='',duration=120)],
                        skipped_names=1 if self.partial else 0)
    with tempfile.TemporaryDirectory(prefix='quest-snapshot-age-') as directory:
        provider = Provider()
        with patch('subprocess.Popen', side_effect=AssertionError('No actual device process')):
            window = LibraryWindow(Path(directory), device_provider=provider)
            window.show();QTest.qWait(30)
            notice = window.snapshot_notice; browser = window.device_browser
            assert not notice.label.isVisible() and not notice.timer.isActive() and provider.calls == []
            tick = [100.0]
            notice.clock = lambda: tick[0]
            notice.wall_clock = lambda: datetime(2026,10,23,12,0,0,tzinfo=timezone.utc)
            browser.discover();wait(lambda:not browser.busy)
            browser.choices.setCurrentIndex(1);browser.read_selected()
            wait(lambda:not browser.busy)
            assert window.quest_ready and not notice.label.isVisible() and notice.timer.isActive()
            assert '2026-10-23 12:00:00 +0000' in window.quest_summary.toolTip()
            assert '2026-10-23 12:00:00 +0000' in notice.label.toolTip()
            assert tr('qt.snapshot.just_now') in notice.label.text()
            calls = list(provider.calls)
            window.local_ready = True
            window.model.replace([dict(folder='two',title='Local',author='',error='',duration=0)])
            window.update_comparison()
            assert window.missing_side.isEnabled()
            for seconds in (59,60,899,900,86400):
                tick[0] = 100+seconds
                notice.timer.timeout.emit()
                expected = 'qt.snapshot.old' if seconds >= 900 else 'qt.snapshot.cached'
                assert tr(expected) in notice.label.text()
                assert window.missing_side.isEnabled(), 'age must not change comparison policy'
            assert provider.calls == calls
            # Changing the wall clock or rolling back an injected elapsed clock
            # never rewrites the original completion stamp or shows negative age.
            notice.wall_clock = lambda: datetime(2030,1,1,tzinfo=timezone.utc)
            notice.render();assert '2026-10-23 12:00:00 +0000' in notice.label.toolTip()
            tick[0] = 50;notice.render();assert tr('qt.snapshot.just_now') in notice.label.text()
            for language in ('en','zh'):
                window.language.setCurrentIndex(window.language.findData(language))
                for mode in ('light','dark'):
                    window.mode.setCurrentIndex(window.mode.findData(mode))
                    window.resize(1000,720);QTest.qWait(20)
                    assert not notice.label.isVisible()
                    assert tr('qt.snapshot.cached_short') in window.quest_summary.toolTip()
                    window.resize(1000,640);QTest.qWait(20)
                    assert window.quest_table.viewport().height() >= 70
                    window.resize(1000,760);QTest.qWait(20)
                    assert not notice.label.isVisible()
                    assert tr('qt.snapshot.cached') in window.quest_summary.toolTip()
                    assert window.quest_summary.height() >= window.quest_summary.heightForWidth(window.quest_summary.width())
            # Invalid replacement input must not stamp or erase a valid snapshot.
            captured = notice.completed_at
            try:window.set_quest_snapshot([{'bad': True}], 'bad')
            except ValueError:pass
            else:raise AssertionError('invalid snapshot accepted')
            assert notice.completed_at == captured
            provider.partial = True
            browser.read_selected();assert not notice.label.isVisible()
            wait(lambda:not browser.busy)
            assert window.quest_incomplete and tr('qt.snapshot.partial') in notice.label.text()
            assert not window.missing_side.isEnabled()
            assert notice.completed_at.startswith('2030-01-01')
            provider.failed = True
            browser.read_selected();wait(lambda:not browser.busy)
            assert not window.quest_ready and not notice.label.isVisible() and not notice.timer.isActive()
            provider.failed = False;provider.partial = False
            browser.read_selected();wait(lambda:not browser.busy)
            assert window.quest_ready
            browser.cancel()
            assert notice.completed_at == '' and not notice.timer.isActive()
            browser.read_selected();wait(lambda:not browser.busy)
            QTest.qWait(20)
            shots = os.environ.get('QUEST_QT_SHOTS')
            if shots:
                path = Path(shots);path.mkdir(parents=True,exist_ok=True)
                assert window.grab().save(str(path/('snapshot-age-'+os.environ.get('QT_SCALE_FACTOR','1')+'.png')))
            window.close();window.wait_closed();app.processEvents()
            assert notice.closed and not notice.timer.isActive()
            assert not errors and 'miniaudio' not in sys.modules, errors
    print('PASS timestamp/elapsed/15-minute hint, no automatic calls, original comparison policy, partial/failure/cancel/re-read/close, translations and geometry')


def main():
    if '--child' in sys.argv:child();return
    for scale in ('1','1.25','1.5'):
        result = subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
            env=dict(os.environ,QT_SCALE_FACTOR=scale,PYTHONIOENCODING='utf-8'),capture_output=True,timeout=45)
        print(scale,result.stdout.decode('utf-8',errors='replace'),result.stderr.decode('utf-8',errors='replace'),flush=True)
        assert result.returncode == 0


if __name__ == '__main__':main()
