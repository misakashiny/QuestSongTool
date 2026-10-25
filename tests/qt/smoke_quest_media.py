"""Metadata appears before a blocked media worker; visible-only Quest details."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QStyleOptionViewItem
    from quest_tool.downloads import DownloadError,check_cancel
    from quest_tool.messages import tr
    from quest_tool.qt_ui.device_protocol import descriptor
    from quest_tool.qt_ui.library_window import LibraryWindow

    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def report(kind,value,tb):errors.append(str(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    art=io.BytesIO();Image.new('RGB',(256,256),'#2582df').save(art,format='PNG');png=art.getvalue()
    device=descriptor(dict(kind='adb',device_id='fixture-A',root='/sdcard/CustomLevels',label='Quest fixture',
        state='device',model='Quest 3',codename='eureka',brand='oculus',identity_verified=True))
    rows=[dict(folder=f'{i:03}',title=f'Song {i:03}',author='Artist',error='',duration=0,bpm=155,
        difficulties=('Standard:ExpertPlus',),subtitle='From Quest',version='2.1.0',cover_file='cover.png',info_hash='a'*64) for i in range(80)]
    class Provider:
        def __init__(self):self.calls=[];self.gate=threading.Event();self.entered=threading.Event();self.error='';self.skipped=0
        def list_devices(self,cancel):check_cancel(cancel);return [device]
        def read_library(self,*args):raise AssertionError('UI waited for full audio scan')
        def read_metadata(self,selected,cancel):
            check_cancel(cancel);return dict(device=device,connected=True,rows=rows,skipped_names=self.skipped)
        def read_assets(self,selected,items,cancel):
            assert len(items)<=4 and threading.current_thread() is not threading.main_thread()
            self.calls.append([dict(row) for row in items]);self.entered.set()
            while not self.gate.wait(.01):check_cancel(cancel)
            check_cancel(cancel)
            if self.error:raise DownloadError(self.error)
            return dict(identity=selected['identity'],items=[dict(folder=row['folder'],image=png if row['need_cover'] else None,
                duration=123.5,cover_done=row['need_cover'],duration_done=row['need_duration']) for row in items])
    def wait(predicate):
        end=time.monotonic()+10
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError('Quest media timeout')
    def index(window,folder):
        return next(window.quest_proxy.index(i,0) for i in range(window.quest_proxy.rowCount())
            if window.quest_proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder']==folder)
    def forbidden(*args,**kwargs):raise AssertionError('Unexpected external I/O')
    with tempfile.TemporaryDirectory() as temporary,patch('subprocess.Popen',forbidden),patch('socket.socket',forbidden):
        for language in ('zh','en'):
            provider=Provider();window=LibraryWindow(Path(temporary)/language,language,device_provider=provider)
            window.show();assert QTest.qWaitForWindowExposed(window,5000)
            browser=window.device_browser;media=window.quest_media
            try:
                window.quest_table.sortByColumn(2,Qt.SortOrder.AscendingOrder)
                browser.discover();wait(lambda:not browser.busy);browser.choices.setCurrentIndex(1)
                browser.read.click();wait(lambda:window.quest_ready)
                wait(provider.entered.is_set)
                assert window.quest_model.rowCount()==80 and not browser.busy and media.busy
                assert all(row['duration']==0 for row in window.quest_model.rows)
                assert not window.quest_incomplete and len(provider.calls)==1 and len(provider.calls[0])<=4
                current=provider.calls[0][0]['folder'];cell=index(window,current)
                window.quest_table.setCurrentIndex(cell);window.details.select('quest',cell)
                assert window.details.fields['bpm'].text()=='155'
                assert window.details.fields['version'].text()=='2.1.0' and window.details.subtitle.text()=='From Quest'
                assert 'ExpertPlus' in window.details.fields['difficulties'].text()
                option=QStyleOptionViewItem();window.quest_table.itemDelegateForColumn(0).initStyleOption(option,cell)
                assert '155 BPM' in option.text
                source_index=window.quest_proxy.mapToSource(cell)
                window.quest_model.setData(source_index,Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
                captured=window.snapshot_notice.started;scroll=window.quest_table.verticalScrollBar().value()
                positions=[row['folder'] for row in (window.quest_proxy.index(i,0).data(Qt.ItemDataRole.UserRole) for i in range(80))]
                provider.gate.set();wait(lambda:media.image_bytes(current) is not None)
                wait(lambda:not media.busy and not media.debounce.isActive())
                assert window.quest_model.checked=={current} and window.snapshot_notice.started==captured
                assert window.quest_table.verticalScrollBar().value()==scroll
                assert window.quest_table.currentIndex().data(Qt.ItemDataRole.UserRole)['folder']==current
                assert positions==[window.quest_proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['folder'] for i in range(80)]
                assert window.details.fields['duration'].text()=='2:03' and window.details.cover.ready
                assert sum(len(call) for call in provider.calls)<80
                visible=set(media.candidates())
                assert all(row['folder'] in visible for call in provider.calls for row in call)
                # Cache hits do not perform more remote I/O; local origin never gets this cover.
                count=len(provider.calls);media.schedule();QTest.qWait(250);assert len(provider.calls)==count
                media.apply(dict(folder=current,image=None,duration=0,cover_done=True,duration_done=False,error='metadata'))
                assert media.notice=='failed'
                media.apply(dict(folder=current,image=None,duration=0,cover_done=True,duration_done=True,error='changed'))
                media.apply(dict(folder=current,image=None,duration=0,cover_done=True,duration_done=True,error='cover'))
                assert media.notice=='changed'
                window.quest_table.sortByColumn(2,Qt.SortOrder.DescendingOrder)
                durations=[window.quest_proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['duration'] for i in range(80)]
                assert durations==sorted(durations,reverse=True)
                window.search.setText('Song '+current)
                assert window.quest_proxy.rowCount()==1
                window.search.clear();assert window.quest_proxy.rowCount()==80
                legacy=dict(folder=current,title='Legacy',author='Artist',duration=60,error='')
                window.local_ready=True;window.target=Path(temporary);window.model.replace([legacy]);window.update_comparison()
                local=window.proxy.index(0,0);window.details.select('local',local)
                assert window.details.data['image'] is None
                # Current Quest detail and Quest-origin missing rows reuse enriched rows.
                window.details.select('quest',index(window,current));assert window.details.cover.ready
                window.missing_side.setCurrentIndex(1)
                missing=window.missing_proxy.index(0,0);window.details.select('missing',missing)
                assert window.details.key[0]=='quest' and window.details.fields['bpm'].text()=='155'
                # Scroll requests a bounded new visible batch, not all remaining songs.
                provider.gate.clear();provider.entered.clear()
                window.quest_table.scrollToBottom();media.schedule();wait(provider.entered.is_set)
                old_generation=media.generation;old_epoch=media.epoch
                browser.clear.click();assert not window.quest_ready and not media.busy and not media.encoded
                media.session.tasks.emit('search',payload={'generation':old_generation,'result':dict(identity=device['identity'],epoch=old_epoch,
                    items=[dict(folder=current,image=png,duration=999,cover_done=True,duration_done=True)])})
                media.poll();assert not window.quest_ready and not media.encoded and window.details.key is None
                provider.gate.set();provider.skipped=1;browser.read.click();wait(lambda:window.quest_ready)
                assert window.quest_incomplete and not window.missing_side.isEnabled()
                wait(lambda:not media.busy and not media.debounce.isActive())
                # A confirmed device loss invalidates the snapshot through the original guard.
                provider.error='device_disconnected';browser.read.click()
                wait(lambda:browser.state=='disconnected');assert not window.quest_ready and not media.encoded
                # A legacy five-field snapshot remains unknown, with no guessed remote path.
                window.set_quest_snapshot([legacy],'legacy-fixture')
                assert window.details.key is None
                window.details.select('quest',window.quest_proxy.index(0,0))
                assert window.details.fields['bpm'].text()==tr('search.unknown') and not window.details.cover.ready
                if os.environ.get('QUEST_QT_SHOTS'):
                    provider.error='';provider.skipped=0;browser.read.click();wait(lambda:window.quest_ready)
                    window.details.select('quest',window.quest_proxy.index(0,0))
                    wait(lambda:window.details.cover.ready)
                    output=Path(os.environ['QUEST_QT_SHOTS']);output.mkdir(parents=True,exist_ok=True)
                    assert window.grab().save(str(output/f'quest-media-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                window.close();window.wait_closed()
                assert media.closed and not media.timer.isActive() and not media.debounce.isActive()
                assert not any(thread.is_alive() for thread in media.session.tasks._threads)
                assert not errors,errors
            finally:provider.gate.set();window.close();window.wait_closed()
    print('PASS metadata before blocked media, BPM/difficulties/version, bounded visible covers, durations, cache, source/epoch/cancel/disconnect, completeness and close')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
