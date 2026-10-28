"""Recent Quest, passive disconnect detection and identity-bound recovery, sealed I/O."""
from contextlib import contextmanager
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
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.adb import AdbDevice, AdbResult
    from quest_tool.downloads import DownloadError, check_cancel
    from quest_tool.messages import tr
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.wireless_client import WirelessClient
    from quest_tool.qt_ui.wireless_history import WirelessHistory
    from quest_tool.qt_ui.transfer_push import AdbPushBackend

    app=QApplication([]);app.setStyle('Fusion');errors=[]
    sys.excepthook=lambda kind,value,tb:(errors.append(str(value)),sys.__excepthook__(kind,value,tb))
    def wait(predicate):
        end=time.monotonic()+8
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError(('timeout',errors))
    def rejects(function,code='device_rejected'):
        try:function()
        except DownloadError as exc:assert exc.code==code,(exc.code,code)
        else:raise AssertionError('Expected '+code)
    def forbidden(*_args,**_kwargs):raise AssertionError('Real process/network access forbidden')

    endpoint='192.168.1.42:5555';hardware='HW_A'
    record=dict(host='192.168.1.42',port=5555,hardware=hardware,model='Quest_3')
    class Provider:
        def __init__(self):
            self.calls=[];self.state='device';self.hardware=hardware;self.entered=threading.Event();self.release=threading.Event();self.delay=False
            self._io_lock=threading.Lock()
        @contextmanager
        def _operation(self,cancel):
            while not self._io_lock.acquire(timeout=.01):check_cancel(cancel)
            try:check_cancel(cancel);yield
            finally:self._io_lock.release()
        def _devices(self,cancel,deadline):
            self.calls.append(('devices',threading.get_ident()))
            if self.delay:
                self.entered.set();assert self.release.wait(5)
            check_cancel(cancel)
            return [] if self.state=='missing' else [AdbDevice(endpoint,self.state)]
        def _identify(self,serial,cancel,deadline):
            self.calls.append(('identify',serial));check_cancel(cancel)
            return self,AdbDevice(serial,'device','Quest_3','eureka','Meta'),self.hardware
        def _bridge(self):return self
        def run_result(self,args,**kwargs):
            self.calls.append(tuple(args));assert args==['connect',endpoint]
            return AdbResult(0,'connected to '+endpoint,'')
        def device(self):
            return AdbReadOnlyProvider._descriptor(AdbDevice(endpoint,'device','Quest_3','eureka','Meta'),hardware,
                '/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels')
        def list_devices(self,cancel):self.calls.append(('list',));check_cancel(cancel);return [self.device()]
        def read_library(self,device,cancel):
            self.calls.append(('read',));check_cancel(cancel)
            assert self.hardware==hardware and self.state=='device'
            return dict(connected=True,device=self.device(),rows=[dict(folder='song',title='Song',author='Artist',duration=120,error='')])

    with tempfile.TemporaryDirectory(prefix='wireless-recovery-') as temporary:
        root=Path(temporary);store=WirelessHistory(root/'records')
        assert store.load()==[] and not store.path.exists()
        for index in range(10):store.remember(dict(record,hardware=f'HW_{index}',code='123456',private='excluded'))
        saved=store.load();assert len(saved)==8 and saved[0]['hardware']=='HW_9'
        assert b'123456' not in store.path.read_bytes() and b'private' not in store.path.read_bytes()
        store.remember(dict(record,hardware='HW_5',port=41666));assert store.load()[0]['port']==41666 and len(store.load())==8
        store.forget('HW_5');assert len(store.load())==7
        for raw in (b'broken',b'{"version":2,"devices":[]}',b'x'*16385):
            store.path.write_bytes(raw)
            try:store.remember(record)
            except ValueError:pass
            else:raise AssertionError('Invalid history replaced')
            assert store.path.read_bytes()==raw

        with patch('subprocess.run',forbidden),patch('subprocess.Popen',forbidden),patch('socket.socket',forbidden):
            provider=Provider();client=WirelessClient(provider);cancel=threading.Event()
            request=dict(record,mode='check',port='5555',serial=endpoint)
            for state in ('offline','unauthorized','missing','device'):
                provider.state=state;start=len(provider.calls)
                result=client.search(request,0,'',cancel)
                assert result['state']==('disconnected' if state=='missing' else 'online' if state=='device' else state)
                assert not any(call[0]=='connect' for call in provider.calls[start:])
            provider.hardware='OTHER';rejects(lambda:client.search(request,0,'',cancel))
            rejects(lambda:client.search(dict(request,mode='reconnect'),0,'',cancel))
            provider.hardware=hardware
            assert client.search(dict(request,mode='reconnect'),0,'',cancel)['hardware']==hardware
            # TLS aliases are checked exactly; the endpoint remains the recovery address.
            alias='adb-HW-A-test._adb-tls-connect._tcp'
            with patch.object(provider,'_devices',return_value=[AdbDevice(alias,'device')]):
                assert client.search(dict(request,serial=alias),0,'',cancel)['state']=='online'
            for invalid in ('USB_A','192.168.1.99:5555','bad;serial'):
                rejects(lambda:client.search(dict(request,serial=invalid),0,'',cancel))

            for language in ('zh','en'):
                folder=root/language;provider=Provider();client=WirelessClient(provider)
                window=LibraryWindow(folder,language,device_provider=provider,wireless_client=client,
                    push_backend=AdbPushBackend(provider,folder/'journals'))
                window.show();QTest.qWait(80)
                owner=window.wireless;browser=window.device_browser;recovery=owner.recovery
                assert not provider.calls and not recovery.store.path.exists() and not recovery.timer.isActive()
                owner.show();form=owner.dialog
                assert not provider.calls and form.recent.count()==1
                form.mode.setCurrentIndex(1);form.fields['host'].setText(record['host']);form.run_button.click()
                wait(lambda:not owner.busy);assert owner.result and recovery.store.load()==[record]
                assert form.read_button.isEnabled() and not recovery.button.isVisible()
                # Closing does not read songs; an explicit Read is still required.
                form.close_button.click();wait(lambda:not browser.busy)
                assert browser.selected and not window.quest_ready and not any(call[0]=='read' for call in provider.calls)
                browser.read_selected();wait(lambda:window.quest_ready and not browser.busy)
                assert len(window.quest_model.rows)==1
                window.target=folder/'songs';window.target.mkdir();window.local_ready=True
                window.model.replace([dict(folder='local-song',title='Local song',author='Artist',duration=120,error='')])
                window.model.checked={'local-song'};window.update_comparison()
                assert window.transfers.push.isEnabled() and not (folder/'journals').exists()
                recovery.timer.stop();count=len(provider.calls);provider.state='offline'
                recovery.check();wait(lambda:recovery.state=='offline')
                assert len(provider.calls)>count and window.quest_stale and not window.quest_ready
                assert len(window.quest_model.rows)==1 and recovery.button.isVisible()
                assert not window.transfers.push.isEnabled() and not window.transfers.missing.isEnabled()
                if os.environ.get('QUEST_QT_SHOTS'):
                    window.grab().save(str(Path(os.environ['QUEST_QT_SHOTS'])/f'offline-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                count=len(provider.calls);recovery.check();QTest.qWait(80)
                assert len(provider.calls)==count and not recovery.timer.isActive()
                # Reused IP: connection succeeds but identity is rejected and history is unchanged.
                before=recovery.store.path.read_bytes();provider.state='device';provider.hardware='OTHER'
                recovery.button.click();wait(lambda:not owner.busy)
                form=owner.dialog
                assert owner.result is None and form.status.text()==tr('qt.wireless.state.device_rejected')
                assert not form.read_button.isEnabled() and recovery.store.path.read_bytes()==before
                assert window.quest_stale and len(window.quest_model.rows)==1
                # Same hardware reconnects once, without automatic library read or transfer.
                provider.hardware=hardware;count=sum(call[0]=='connect' for call in provider.calls)
                form.run_button.click();wait(lambda:not owner.busy)
                assert owner.result and sum(call[0]=='connect' for call in provider.calls)==count+1
                assert window.quest_stale and not window.quest_ready
                form.read_button.click();wait(lambda:window.quest_ready and not browser.busy and not browser.connection_open)
                assert not window.quest_stale and recovery.state=='online'
                recovery.timer.stop()
                # No polling during a transfer dialog, media load, device action or hidden window.
                for control in ('transfer','media','manager','hidden'):
                    count=len(provider.calls)
                    if control=='transfer':window.transfers.dialog=object()
                    elif control=='media':window.quest_media.busy=True
                    elif control=='manager':browser.connection_open=True
                    else:window.hide()
                    recovery.check();assert len(provider.calls)==count
                    window.transfers.dialog=None;window.quest_media.busy=False;browser.connection_open=False;window.show()
                # Late failure cannot invalidate a newer read or selected transport.
                provider.delay=True;provider.entered.clear();provider.release.clear();provider.state='offline'
                recovery.check();wait(provider.entered.is_set);browser.read_epoch+=1
                provider.release.set();wait(lambda:recovery.generation is None)
                assert window.quest_ready and not window.quest_stale
                provider.delay=False;provider.state='device'
                # Recents load locally; field edits remove the old identity binding.
                count=len(provider.calls);owner.show();form=owner.dialog
                assert len(provider.calls)==count and form.recent_match()['hardware']==hardware
                form.fields['host'].setText('192.168.1.43');form.edited('host');assert not form.recent_match()
                form.recent.setCurrentIndex(1);form.choose_recent(1)
                assert form.recent_match()['hardware']==hardware
                for theme in ('light','dark'):
                    window.mode.setCurrentIndex(window.mode.findData(theme));form.resize(620,560);QTest.qWait(50)
                    for button in (form.run_button,form.cancel_button,form.close_button,form.read_button):
                        assert button.isVisible() and button.width()>=button.sizeHint().width()
                        assert form.rect().contains(button.mapTo(form,button.rect().center()))
                    if os.environ.get('QUEST_QT_SHOTS'):
                        form.grab().save(str(Path(os.environ['QUEST_QT_SHOTS'])/f'recovery-{language}-{theme}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                if language=='zh' and os.environ.get('QUEST_QT_SHOTS') and os.environ.get('QT_SCALE_FACTOR')=='1':
                    window.mode.setCurrentIndex(window.mode.findData('light'));form.resize(700,700);QTest.qWait(50)
                    form.grab().save(str(Path(os.environ['QUEST_QT_SHOTS'])/'wireless-recent.png'))
                count=len(provider.calls);form.forget.click()
                assert recovery.store.load()==[] and len(provider.calls)==count
                form.close_button.click();browser.cancel()
                assert recovery.active is None and not recovery.timer.isActive()
                # Corrupt records stay intact and do not prevent a manual connection.
                recovery.store.path.write_bytes(b'broken');recovery.reload();owner.show()
                assert owner.dialog.history_note.text()==tr('qt.wireless.history_error')
                owner.dialog.close_button.click();assert recovery.store.path.read_bytes()==b'broken'
                window.close();window.wait_closed();assert not recovery.timer.isActive() and recovery.closed
            # Restart restores an address but neither probes nor connects automatically.
            folder=root/'restart';WirelessHistory(folder).remember(record);provider=Provider()
            window=LibraryWindow(folder,device_provider=provider,wireless_client=WirelessClient(provider))
            window.show();QTest.qWait(80);owner=window.wireless;recovery=owner.recovery;browser=window.device_browser
            assert not provider.calls and recovery.active is None and not recovery.timer.isActive()
            owner.show();assert owner.dialog.recent_match()['hardware']==hardware and not provider.calls
            owner.dialog.run_button.click();wait(lambda:not owner.busy)
            owner.dialog.read_button.click();wait(lambda:window.quest_ready and not browser.busy)
            recovery.timer.stop();provider.delay=True;provider.entered.clear();provider.release.clear()
            recovery.check();wait(provider.entered.is_set)
            window.close();provider.release.set();window.wait_closed();QTest.qWait(40)
            assert recovery.closed and recovery.generation is None and not recovery.poll_timer.isActive()
            assert not errors,errors
    print('PASS recovery: bounded atomic history, no startup I/O, passive checks, identity-bound reconnect, stale snapshots, late results, busy gates, bilingual DPI layouts')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True)
            assert result.returncode==0
