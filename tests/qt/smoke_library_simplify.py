"""Annotated layout, scoped checks and guarded transfer UI; fake device only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT),str(ROOT/'tests/qt')]
from smoke_verification import song
from smoke_usability import device


def child():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.messages import tr
    from quest_tool.qt_ui.library_window import LibraryWindow
    app = QApplication([]);app.setStyle('Fusion');errors = []
    sys.excepthook = lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    def wait(predicate):
        end = time.monotonic()+10
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if predicate():return
        raise AssertionError(('timeout',errors))
    def contains(widget,parent):
        point = widget.mapTo(parent,QPoint())
        assert parent.rect().contains(point) and point.x()+widget.width()<=parent.width()+1
        assert point.y()+widget.height()<=parent.height()+1
        assert widget.width()>=widget.minimumSizeHint().width(),(widget.text(),widget.geometry(),widget.minimumSizeHint(),parent.geometry(),w.header.wrapped,language,mode,w.size())
    with tempfile.TemporaryDirectory(prefix='library-layout-') as tmp, patch('socket.socket',side_effect=AssertionError('network forbidden')), patch('subprocess.Popen',side_effect=AssertionError('process forbidden')):
        base = Path(tmp);local = base/'songs';local.mkdir();selected = device()
        digests = {}
        from quest_tool.downloads import validate_song
        for n in range(12):
            folder = local/f'song-{n:02}';song(folder)
            info = json.loads((folder/'Info.dat').read_text(encoding='utf-8'))
            info.update(_songName=('Blue Horizon','星海回声','Electric Garden')[n%3]+f' {n+1:02}',
                        _songAuthorName='Studio Demo',_beatsPerMinute=128,_songDuration=193)
            (folder/'Info.dat').write_text(json.dumps(info),encoding='utf-8')
            (folder/'Expert.dat').write_text(json.dumps({'_notes':[n]}),encoding='utf-8')
            digests[folder.name] = validate_song(folder)[1]
        remote = [dict(folder=name,title=name,author='Test artist',duration=193,error='',info_hash='a'*64) for name in list(digests)[:8]]
        class Provider:
            def __init__(self):self.calls = [];self.hold = False
            def list_devices(self,cancel):self.calls.append('list');return [selected]
            def read_library(self,*args):self.calls.append('read');return dict(device=selected,connected=True,rows=remote)
            def read_identities(self,selection,items,cancel):
                self.calls.append('identities')
                while self.hold and not cancel.is_set():time.sleep(.01)
                if cancel.is_set():
                    from quest_tool.downloads import DownloadError
                    raise DownloadError('cancelled')
                return dict(identity=selected['identity'],bytes=1,items=[dict(folder=r['folder'],state='verified',digest=digests[r['folder']]) for r in items])
        for language in ('zh','en'):
            for mode in ('light','dark'):
                provider = Provider();factories = []
                def factory():factories.append(True);return object(),SimpleNamespace(journal_dir=base/'journals')
                w = LibraryWindow(base/(language+mode),language,mode,device_provider=provider,transfer_factory=factory)
                w.show()
                try:
                    assert QTest.qWaitForWindowExposed(w,5000)
                    w.set_target(str(local));wait(lambda:w.local_ready and w.ratings.ready)
                    assert not provider.calls and factories == [True]
                    retired = (w.reports.button,w.reports.status,w.help_button,w.workflow.page,
                        w.local_filter,w.quest_filter,w.missing_side,w.select_visible,w.clear,w.rating_state,
                        w.transfers.select,w.transfers.clear,w.transfers.pull,w.transfers.missing)
                    assert all(not c.isVisible() for c in retired)
                    assert factories==[True] and not provider.calls
                    assert not hasattr(w.wireless,'transfer_enable') and w.transfers.push.isVisible()
                    assert all(not c.isVisible() for c in retired)
                    w.device_browser.discover();wait(lambda:not w.device_browser.busy)
                    w.device_browser.choices.setCurrentIndex(1);w.device_browser.read_selected();wait(lambda:w.quest_ready)
                    assert provider.calls==['list','read'] and not w.missing_model.rows
                    checks = w.identity_checks
                    assert checks.button.isVisible() and w.empty_states.check_panel.isVisible()
                    QTest.qWait(60)
                    contains(checks.button,w.missing_table.viewport())
                    QTest.mouseClick(checks.button,Qt.MouseButton.LeftButton)
                    try:wait(checks.complete)
                    except AssertionError:
                        print('CHECK DEBUG',checks.busy,checks.values,provider.calls,checks.button.geometry(),flush=True)
                        if os.environ.get('QUEST_QT_SHOTS'):w.grab().save(str(Path(os.environ['QUEST_QT_SHOTS'])/'check-failed.png'))
                        raise
                    assert len(w.missing_model.rows)==4 and not w.empty_states.check_panel.isVisible()
                    for key,(model,proxy,button) in w.selection.items.items():
                        QTest.mouseClick(button,Qt.MouseButton.LeftButton)
                        assert model.checked=={r['folder'] for r in model.rows}
                        assert button.text()==tr('ui.library.uncheck_all')
                        query = 'song-00' if key!='missing' else 'song-08'
                        outside = set(model.checked)-{query}
                        w.search.setText(query);QTest.qWait(20)
                        assert proxy.rowCount()==1
                        QTest.mouseClick(button,Qt.MouseButton.LeftButton)
                        assert model.checked==outside
                        assert button.text()==tr('ui.library.check_all')
                        w.search.setText('no results');QTest.qWait(20);assert not button.isEnabled()
                        w.search.clear();QTest.qWait(20)
                        # Returning to full results and individual check changes update the toggle.
                        QTest.mouseClick(button,Qt.MouseButton.LeftButton)
                        proxy.setData(proxy.index(0,0),Qt.CheckState.Unchecked,Qt.ItemDataRole.CheckStateRole)
                        wait(lambda:button.text()==tr('ui.library.check_all'))
                        QTest.mouseClick(button,Qt.MouseButton.LeftButton)
                    # Metadata-only updates must not trigger additional selection-list scans.
                    QTest.qWait(20)
                    with patch.object(w.selection.timer,'start',side_effect=AssertionError('metadata selection refresh')):
                        w.quest_model.dataChanged.emit(w.quest_model.index(0,0),w.quest_model.index(0,0),[Qt.ItemDataRole.UserRole])
                        assert not w.selection.dirty
                    for width,height in ((1000,640),(1400,850)):
                        w.resize(width,height);QTest.qWait(100)
                        wait(lambda:w.width()<=width and w.height()<=height)
                        if (w.width()>width or w.height()>height) and os.environ.get('QUEST_QT_SHOTS'):
                            path=Path(os.environ['QUEST_QT_SHOTS']);path.mkdir(parents=True,exist_ok=True)
                            w.grab().save(str(path/'minimum-failed.png'))
                        assert w.width()<=width and w.height()<=height,(language,mode,width,height,w.size(),w.minimumSizeHint())
                        assert all(not c.isVisible() for c in retired)
                        for key,(_model,_proxy,button) in w.selection.items.items():
                            contains(button,{'local':w.local_card,'quest':w.quest_card,'missing':w.missing_card}[key])
                        for button in (w.transfers.push,w.transfers.push_missing,w.transfers.history):contains(button,w.transfers.push_actions)
                        assert w.transfers.push.geometry().right()<w.transfers.push_missing.geometry().left()
                        assert w.transfers.push_missing.geometry().right()<w.transfers.history.geometry().left()
                        heights = [t.viewport().height() for t in (w.table,w.quest_table,w.missing_table)]
                        if min(heights)<120 and os.environ.get('QUEST_QT_SHOTS'):
                            path=Path(os.environ['QUEST_QT_SHOTS']);path.mkdir(parents=True,exist_ok=True)
                            w.grab().save(str(path/'compact-failed.png'))
                        assert min(heights)>=120,(language,mode,width,height,heights)
                    assert w.transfers.push.isEnabled() and w.transfers.push_missing.isEnabled()
                    w.transfers.push_missing.click();dialog=w.transfers.dialog
                    assert dialog and dialog.before_start() and not dialog.queue.tasks.busy()
                    w.clear_quest_snapshot();assert not dialog.before_start()
                    dialog.close();wait(lambda:w.transfers.dialog is None)
                    assert not (base/'journals').exists()
                    # Returning from download/local results never hides the other two lists.
                    w.workflow.show_local(str(local));wait(lambda:w.local_ready)
                    assert w.local_card.isVisible() and w.quest_card.isVisible() and w.missing_card.isVisible()
                    w.device_browser.read_selected();wait(lambda:w.quest_ready)
                    calls = len(provider.calls)
                    for size in ((1000,640),(1400,850)):
                        w.resize(*size);QTest.qWait(80);contains(checks.button,w.missing_table.viewport())
                    assert len(provider.calls)==calls
                    provider.hold = True
                    QTest.mouseClick(checks.button,Qt.MouseButton.LeftButton)
                    wait(lambda:len(provider.calls)>calls)
                    assert checks.cancel_button.isVisible() and not checks.button.isVisible()
                    QTest.mouseClick(checks.cancel_button,Qt.MouseButton.LeftButton)
                    wait(lambda:not checks.busy and not checks.session.tasks.busy())
                    assert not checks.complete() and not w.missing_model.rows
                    provider.hold = False
                    QTest.mouseClick(checks.button,Qt.MouseButton.LeftButton);wait(checks.complete)
                    QTest.mouseClick(w.selection.items['missing'][2],Qt.MouseButton.LeftButton)
                    w.table.setCurrentIndex(w.proxy.index(0,0));wait(lambda:not w.details.loading)
                    shots = os.environ.get('QUEST_QT_SHOTS')
                    if shots:
                        path=Path(shots);path.mkdir(parents=True,exist_ok=True)
                        w.grab().save(str(path/f'simplified-{language}-{mode}-{os.environ.get("QT_SCALE_FACTOR","1")}.png'))
                    assert not errors,errors
                finally:w.close();w.wait_closed()
    assert 'miniaudio' not in sys.modules
    print('PASS simplified library: removed entries, select-all/search/partial checks, explicit verification, safe transfer confirmation, resize, bilingual themes, no live I/O')


if __name__=='__main__':child()
