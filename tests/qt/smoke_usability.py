"""Isolated workflow regression: real local hashes, fake device, no audio/network."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT),str(ROOT/'tests/qt')]
from smoke_verification import song


def device():
    from quest_tool.qt_ui.device_protocol import descriptor
    return descriptor(dict(kind='adb',device_id=json.dumps(['SIM','HW'],separators=(',',':')),
        root='/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels',label='Simulated Quest 3',
        state='device',model='Quest_3',codename='eureka',brand='Meta',identity_verified=True))


def service():
    from quest_tool.downloads import DownloadError
    from quest_tool.qt_ui import adb_identity as module
    info=json.dumps({'_difficultyBeatmapSets':[{'_difficultyBeatmaps':[{'_beatmapFilename':'Expert.dat'}]}]}).encode()
    data=b'{"_notes":[]}'
    expected=hashlib.sha1(info+data).hexdigest();selected=device();cancel=threading.Event()
    class Provider:
        changed=False
        commands=[]
        def _serial(self,value):assert value in ('SIM','HW')
        def _contents(self,*args):return [('renamed',info)]
        def _run(self,bridge,args,*rest,**kwargs):
            self.commands.append(args)
            assert 'readlink -f' in args[1] and '[ ! -L' in args[1]
            assert not any(word in args[1] for word in ('push ','rm ','mv ','tcpip'))
            if args[0]=='exec-out':
                assert 'head -c' in args[1]
                return b'QSV:'+str(len(data)).encode()+b'\n'+data+module.END
            assert f'= {len(data)} ] || exit 1' in args[1]
            assert args[1].index('stat -c %s') < args[1].index('sha256sum')
            return ('f'*64 if self.changed else hashlib.sha256(data).hexdigest())+'  Expert.dat'
    provider=Provider();request=[dict(folder='renamed',info_hash=hashlib.sha256(info).hexdigest())]
    with patch.object(module,'_binding',return_value=(object(),selected['root'])) as binding:
        result=module.read_identities(provider,selected,request,cancel)
        assert result['items']==[dict(folder='renamed',state='verified',digest=expected)]
        assert result['bytes']==2*(len(info)+len(data)) and binding.call_count==2
        provider.changed=True
        assert module.read_identities(provider,selected,request,cancel)['items'][0]['state']=='unknown'
    for value in (b'QSV:2\n{}bad',b'QSV:99999999\n{}'+module.END,b'QSV:2\n[]'+module.END):
        try:module.decode_map(value)
        except DownloadError:pass
        else:raise AssertionError('bad map frame accepted')
    for name in ('../outside.dat','/outside.dat','a/../../outside.dat','evil\n.dat'):
        try:module.map_command(selected['root']+'/song',name)
        except DownloadError:pass
        else:raise AssertionError('unsafe map accepted')
    cancel.set()
    try:module.read_identities(provider,selected,request,cancel)
    except DownloadError:pass
    else:raise AssertionError('cancel ignored')
    print('PASS bounded map identity: frames, content, mutation, budget, guards, cancellation')


def child():
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.downloads import DownloadError
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.library_report import report_rows
    from quest_tool.qt_ui.workspace_directory import WorkspaceDirectory
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    sys.excepthook=lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    def wait(test):
        end=time.monotonic()+12
        while time.monotonic()<end:
            app.processEvents()
            if test():return
            time.sleep(.01)
        raise AssertionError('workflow timeout: '+repr(errors))
    with tempfile.TemporaryDirectory(prefix='quest-usability-') as tmp, patch('socket.socket',side_effect=AssertionError('network forbidden')), patch('subprocess.Popen',side_effect=AssertionError('process forbidden')):
        base=Path(tmp).resolve();local=base/'local';local.mkdir();other=base/'other';other.mkdir()
        same=song(local/'renamed-on-PC');song(local/'collision')
        (local/'collision/Expert.dat').write_text('{"_notes":[1]}')
        from quest_tool.downloads import validate_song
        different=validate_song(local/'collision')[1]
        selected=device()
        rows=[dict(folder=name,title=name,author='Fixture',error='',duration=0,info_hash='a'*64) for name in ('renamed-on-Quest','collision')]
        class Provider:
            fail=False;hold=False;calls=0
            def list_devices(self,cancel):return [selected]
            def read_library(self,*args):
                if self.fail:raise DownloadError('device_disconnected')
                return dict(device=selected,connected=True,rows=copy.deepcopy(rows))
            def read_identities(self,selection,items,cancel):
                self.calls+=1
                while self.hold and not cancel.is_set():time.sleep(.01)
                if cancel.is_set():raise DownloadError('cancelled')
                return dict(identity=selected['identity'],bytes=100,items=[dict(folder=r['folder'],state='verified',digest=same if r['folder']=='renamed-on-Quest' else 'f'*40) for r in items])
        provider=Provider();language=os.environ.get('TEST_LANG','zh')
        window=LibraryWindow(base/'state',language,device_provider=provider,transfer_backend=object(),push_backend=object(),transfer_factory=lambda:(object(),object()))
        window.show()
        try:
            assert window.set_target(str(local));wait(lambda:window.local_ready and window.ratings.ready)
            browser=window.device_browser;browser.discover();wait(lambda:not browser.busy)
            browser.choices.setCurrentIndex(1);browser.read_selected();wait(lambda:not browser.busy)
            check=window.identity_checks
            assert check.button.isEnabled() and not check.complete() and not window.missing_model.rows
            window.quest_model.checked={'collision'};window.transfers.render();assert window.transfers.pull.isEnabled()
            check.button.click();wait(lambda:not check.busy)
            assert check.complete() and check.values['local']['collision']==different
            lm,qm,ld,qd=check.sets()
            assert lm=={'renamed-on-PC'} and qm=={'renamed-on-Quest'} and ld==qd=={'collision'}
            assert len(report_rows(window.reports.snapshot()))==3
            window.missing_side.setCurrentIndex(window.missing_side.findData('local'))
            window.missing_model.checked={'collision'};window.transfers.render();window.transfers.missing.click()
            dialog=window.transfers.dialog;assert dialog and dialog.before_start()
            provider.hold=True;check.start()
            assert not window.missing_model.rows and not dialog.before_start() and not window.reports.button.isEnabled()
            dialog.close();wait(lambda:provider.calls>=2)
            check.cancel();provider.hold=False;wait(lambda:not check.session.tasks.busy());check.poll()
            assert not check.complete() and not window.missing_model.rows
            for payload in ({'generation':777},{'generation':777,'result':{'items':[dict(folder='collision',state='verified',digest=None)]}}):
                check.generation=777;check.kind='local';check.pending=[];check.requested={'collision'}
                check.session.tasks.emit('search',payload=payload);check.poll()
                assert not check.complete()
            with patch('quest_tool.qt_ui.library_identity.inspect_install',side_effect=AssertionError('completed local identities were reread')):
                check.start();wait(lambda:not check.busy);assert check.complete()
            # Cancel in the gap between discovery completion and its queued
            # read-next callback. The callback must not resurrect cancelled I/O.
            callbacks=[];browser.generation=987;browser.action='list';browser.busy=True;browser.read_after_connect=True
            browser.preferred_transport=selected['device_id']
            browser.session.tasks.emit('search',payload=dict(generation=987,result=dict(action='list',devices=[selected],rejected=0)))
            with patch('quest_tool.qt_ui.device_browser.QTimer.singleShot',side_effect=lambda delay,callback:callbacks.append(callback)):
                browser.poll()
            assert len(callbacks)==1
            browser.cancel();callbacks[0]();assert not browser.busy and browser.generation is None and not window.quest_ready
            browser.read_selected();wait(lambda:not browser.busy)
            window.quest_model.checked={'collision'};provider.fail=True;browser.read_selected()
            assert window.quest_model.rows and window.quest_model.checked=={'collision'} and not window.quest_ready
            wait(lambda:not browser.busy)
            assert window.quest_stale and not window.transfers.pull.isEnabled() and not window.reports.button.isEnabled()
            provider.fail=False;browser.read_selected();wait(lambda:not browser.busy)
            assert not window.quest_stale and window.quest_model.checked=={'collision'} and not check.complete()
            # Both open windows use one selected folder; existing task targets
            # remain values captured by the existing queue service.
            with patch('quest_tool.qt_ui.download_window.DownloadWindow.enable_search'), patch('quest_tool.qt_ui.download_window.DownloadWindow.enable_covers'), patch('quest_tool.qt_ui.download_window.DownloadWindow.enable_preview'):
                window.open_downloads()
            download=window.download_window;queue=download.download_browser
            assert queue.target==local
            window.set_target(str(other));wait(lambda:window.local_ready);assert queue.target==other
            queue.set_target(str(local));wait(lambda:window.local_ready);assert window.target==local
            assert WorkspaceDirectory(base/'state').load()==local
            # Newest history is authoritative: cleared jobs never reappear.
            old=SimpleNamespace(manager=SimpleNamespace(snapshot=lambda:[dict(id='old',state='done',source='abc',title='old',target=str(local))]))
            window.download_queues.insert(0,old);window.workflow.show_tasks();app.processEvents()
            assert window.workflow.tree.topLevelItemCount()==0
            window.download_queues.remove(old)
            window.transfers.refresh_after((str(local),selected['identity']),'to_quest',dict(state='done',ok=1,total=1),'/frozen/quest/root')
            window.workflow.render_tasks();assert window.workflow.tree.topLevelItem(0).text(3)=='/frozen/quest/root'
            window.workflow.dialog.close()
            for mode in ('local','quest','compare'):
                window.workflow.view.setCurrentIndex(window.workflow.view.findData(mode));app.processEvents()
                assert window.local_card.isVisible()==(mode in ('local','compare'))
                assert window.quest_card.isVisible()==(mode in ('quest','compare'))
            download.close();wait(lambda:window.download_window is None)
            held=[True]
            finishing=SimpleNamespace(actions=SimpleNamespace(_threads=[SimpleNamespace(is_alive=lambda:held[0])]),manager=SimpleNamespace(tasks=SimpleNamespace(_threads=[])))
            window.download_queues.append(finishing)
            with patch('quest_tool.qt_ui.download_window.DownloadWindow.enable_search'), patch('quest_tool.qt_ui.download_window.DownloadWindow.enable_covers'), patch('quest_tool.qt_ui.download_window.DownloadWindow.enable_preview'):
                window.workflow.show_download_tasks();assert window.download_window is None
                held[0]=False;wait(lambda:window.download_window is not None)
            window.download_queues.remove(finishing)
            assert window.download_window.pages.currentWidget() is window.download_window.tasks_page and window.download_requested_tab is None
            window.download_window.close();wait(lambda:window.download_window is None)
            window.move(100000,100000);window.ensure_visible();app.processEvents()
            assert any(window.frameGeometry().intersects(screen.availableGeometry()) for screen in app.screens())
            for width,height in ((1000,640),(1000,760),(1440,900)):
                window.resize(width,height);QTest.qWait(80)
                assert not window.workflow.page.isVisible()
                for button in (item[2] for item in window.selection.items.values()):
                    assert button.width()>=button.minimumSizeHint().width()
                    assert window.rect().contains(button.mapTo(window,button.rect().center()))
            shots=os.environ.get('QUEST_QT_SHOTS')
            if shots:
                folder=Path(shots);folder.mkdir(parents=True,exist_ok=True)
                window.grab().save(str(folder/f'usability-{language}-{os.environ.get("QT_SCALE_FACTOR","1")}.png'))
        finally:window.close();window.wait_closed();app.processEvents()
        reopened=LibraryWindow(base/'state',language)
        try:wait(lambda:reopened.local_ready);assert reopened.target==local
        finally:reopened.close();reopened.wait_closed()
    assert not errors,errors
    assert 'miniaudio' not in sys.modules
    print('PASS workflow: real content matches, unknown/rename/conflict, stale refresh, cancelled verification, invalidated confirmation, shared folder, task cleanup, navigation, restore and close')


if __name__=='__main__':
    service();child()
