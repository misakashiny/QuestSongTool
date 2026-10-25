"""Offline device error states and retry controls; no external process or device."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from quest_tool.adb import AdbDevice, AdbError, AdbUnavailable
    from quest_tool.downloads import DownloadError
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.device_browser import directory_presentation
    from quest_tool.qt_ui.device_protocol import descriptor
    from quest_tool.messages import tr, set_language
    app=QApplication([]);app.setStyle('Fusion');main=threading.get_ident();errors=[]
    sys.excepthook=lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    def forbidden(*args,**kwargs):raise AssertionError('No external process allowed')
    original=subprocess.Popen;subprocess.Popen=forbidden
    def expect(call,code):
        try:call()
        except DownloadError as exc:assert exc.code==code,(exc.code,code);return
        raise AssertionError('Expected '+code)
    class Bridge:
        def __init__(self,error):self.error=error
        def run(self,*args,**kwargs):raise self.error
    cancel=threading.Event()
    timeout=AdbError('bounded command');timeout.__cause__=subprocess.TimeoutExpired('fake',10)
    for error,code in ((AdbUnavailable('not executable'),'device_adb_path'),(timeout,'device_timeout'),
            (AdbError('device unauthorized'),'device_unauthorized'),(AdbError('device offline'),'device_disconnected'),
            (AdbError('permission denied'),'device_command')):
        expect(lambda:AdbReadOnlyProvider._run(Bridge(error),['devices','-l'],cancel,time.monotonic()+10),code)
    def unavailable(*args,**kwargs):raise AdbUnavailable('missing')
    expect(lambda:AdbReadOnlyProvider(bridge_factory=unavailable)._bridge(),'device_adb_path')
    cancel.set();expect(lambda:AdbReadOnlyProvider._run(Bridge(AdbError('bad')),[],cancel,time.monotonic()+10),'cancelled');cancel.clear()
    class Discovery(AdbReadOnlyProvider):
        def __init__(self,states,root=False,rejected=False):
            super().__init__();self.states=states;self.root=root;self.rejected=rejected
        def _devices(self,*args):return [AdbDevice(str(i),state) for i,state in enumerate(self.states)]
        def _identify(self,serial,*args):
            if self.rejected:raise DownloadError('device_rejected')
            return None,AdbDevice(serial,'device','Quest 3','eureka','oculus'),'HW'+serial
        def _root(self,bridge,root,*args):return root if self.root else ''
    for provider,code in ((Discovery(['unauthorized']),'device_unauthorized'),(Discovery(['offline']),'device_offline'),
            (Discovery(['device']),'device_roots'),(Discovery(['device'],rejected=True),'device_rejected')):
        expect(lambda:provider.list_devices(cancel),code)
    assert Discovery([]).list_devices(cancel)==[]
    assert Discovery(['device','unauthorized','offline'],root=True).list_devices(cancel)
    def item(serial):return dict(kind='adb',device_id=serial,root='/sdcard/CustomLevels',label='Quest '+serial,
        state='device',model='Quest 3',codename='eureka',brand='oculus',identity_verified=True)
    class Provider:
        def __init__(self):self.code=None;self.calls=[];self.hold=False;self.release=threading.Event();self.started=threading.Event()
        def list_devices(self,cancel):
            self.calls.append(threading.get_ident())
            if self.code:raise DownloadError(self.code)
            return [item('A'),item('B')]
        def read_library(self,selected,cancel):
            self.calls.append(threading.get_ident());self.started.set()
            if self.hold:self.release.wait(5)
            if self.code:raise DownloadError(self.code)
            return dict(device=copy.deepcopy(selected),connected=True,rows=[])
    def wait(test):
        end=time.monotonic()+8
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if test():return
        raise AssertionError('state transition timeout')
    with tempfile.TemporaryDirectory() as tmp:
        for language in ('zh','en'):
            provider=Provider();window=LibraryWindow(Path(tmp)/language,language,device_provider=provider);window.show();panel=window.device_browser
            try:
                assert not provider.calls
                # Presentation must not change the selected root, identity, or provider label.
                catalog=[]
                for alias in ('/sdcard/', '/storage/emulated/0/'):
                    for suffix,key in (('SongCore/CustomLevels','songcore'),('SongLoader/CustomLevels','songloader'),('SongCore/CustomWIPLevels','wip')):
                        value=item('A');value['root']=alias+'ModData/com.beatgames.beatsaber/Mods/'+suffix
                        value=descriptor(value);catalog.append(value)
                        label,hint=directory_presentation(value)
                        assert label.startswith(tr('qt.device.directory.'+key)) and value['root'] in hint
                unknown=item('B');assert directory_presentation(unknown)[0]==unknown['label']
                lookalike=dict(catalog[0],root='/other'+catalog[0]['root'])
                assert directory_presentation(lookalike)[0]==lookalike['label']
                mtp=dict(catalog[0],kind='mtp');assert directory_presentation(mtp)[0]==mtp['label']
                original_catalog=copy.deepcopy(catalog);panel.devices=catalog;panel.populate()
                assert panel.choices.currentData() is None and panel.selected is None
                panel.choices.setCurrentIndex(2);selected=panel.selected;generation=panel.generation
                for lang in ('en','zh',language):
                    set_language(lang);panel.retranslate()
                    assert panel.selected is selected and panel.generation==generation
                    assert panel.choices.currentIndex()==2 and panel.choices.currentData()==1
                    assert panel.choices.currentText().startswith(tr('qt.device.directory.songloader'))
                    assert selected['root'] in panel.choices.toolTip() and selected['label'] in panel.choices.accessibleDescription()
                    assert panel.choices.itemData(2,Qt.ItemDataRole.ToolTipRole)==panel.choices.toolTip()
                assert catalog==original_catalog and not provider.calls
                panel.devices=[];panel.selected=None;panel.populate();panel.render()
                for code,state in (('device_unauthorized','unauthorized'),('device_offline','offline'),('device_adb_path','adb_path'),
                        ('device_roots','roots'),('device_timeout','timeout'),('device_command','command')):
                    provider.code=code;panel.refresh.click();assert panel.busy and panel.clear.text()==tr('qt.device.cancel_short')
                    wait(lambda:not panel.busy)
                    assert panel.state==state and panel.refresh.isEnabled() and not panel.read.isEnabled()
                    assert panel.status.text()==tr('qt.device.state.'+state,count=0)
                    assert panel.clear.text()==tr('qt.device.clear_short') and not window.quest_ready
                provider.code=None;panel.refresh.click();wait(lambda:not panel.busy)
                assert panel.state=='choose' and panel.selected is None and '2' in panel.status.text()
                panel.choices.setCurrentIndex(1);provider.code='device_timeout';panel.read.click();wait(lambda:not panel.busy)
                assert panel.state=='timeout' and panel.read.isEnabled()
                provider.code=None;provider.hold=True;provider.started.clear();panel.read.click();wait(provider.started.is_set)
                generation=panel.generation;panel.clear.click()
                assert panel.state=='cancelled' and panel.read.isEnabled() and panel.refresh.isEnabled()
                provider.hold=False;provider.release.set();panel.read.click();wait(lambda:not panel.busy)
                assert panel.state=='empty' and window.quest_ready
                panel.session.tasks.emit('search_error',payload={'generation':generation,'code':'device_disconnected'})
                panel.poll();assert window.quest_ready and panel.state=='empty'
                window.resize(1100,720);app.processEvents()
                assert panel.status.wordWrap() and panel.status.width()>100
                assert all(value!=main for value in provider.calls)
            finally:provider.release.set();window.close();window.wait_closed()
    assert not errors,errors
    subprocess.Popen=original
    print('PASS offline ADB errors, discovery distinctions, bilingual states, retry/cancel/late/buttons/close')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            p=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
            print(scale,p.stdout.decode('utf-8','replace'),p.stderr.decode('utf-8','replace'));assert p.returncode==0
