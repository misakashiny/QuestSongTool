"""Invalid UTF-8 names stay visible as an incomplete read, never guessed paths."""
import json,os,subprocess,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))

def child():
    from PySide6.QtWidgets import QApplication
    from quest_tool.adb import AdbDevice
    from quest_tool.downloads import DownloadError
    from quest_tool.qt_ui.adb_metadata import folder_listing
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.device_protocol import DeviceClient
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.messages import tr
    assert folder_listing(b'good/\r\n\xe7\x9f/\r\n\xef\xbf\xbd/\r\nfile\n')==(['good','\ufffd'],1)
    assert folder_listing('good/\nfile\n')==(['good'],0)
    for raw in (None,b'x'* (16*1024*1024+1),b'bad\x01/\n'):
        try:folder_listing(raw)
        except DownloadError:pass
        else:raise AssertionError('invalid listing accepted')
    root='/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels'
    class Provider(AdbReadOnlyProvider):
        invalid=True
        def __init__(self):super().__init__();self.read_names=[]
        def _devices(self,*args):return [AdbDevice('SIM','device','Quest 3','eureka','oculus')]
        def _identify(self,*args):return None,self._devices()[0],'HW'
        def _root(self,*args):return root
        def _run(self,bridge,args,*unused,**kw):
            assert args==['shell','cd '+root+' && ls -1p'] and kw['binary'] is True
            return b'good/\r\n'+(b'\xe7\x9f/\r\n' if self.invalid else b'')
        def _contents(self,bridge,path,folders,*args):
            assert folders==['good'];self.read_names.extend(folders)
            yield 'good',json.dumps({'_songName':'Good','_songDuration':123}).encode()
        def list_devices(self,cancel):return [self._descriptor(self._devices()[0],'HW',root)]
    provider=Provider();selected=provider.list_devices(None)[0];cancel=threading.Event()
    result=DeviceClient(provider).search({'action':'read','device':selected},0,'',cancel)
    assert result['skipped_names']==1 and len(result['rows'])==1
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    sys.excepthook=lambda *args:(errors.append(repr(args[1])),sys.__excepthook__(*args))
    def wait(done):
        end=time.monotonic()+12
        while time.monotonic()<end:
            app.processEvents()
            if done():return
            time.sleep(.005)
        raise AssertionError('names UI timeout')
    with tempfile.TemporaryDirectory() as temporary:
        base=Path(temporary);local=base/'local';(local/'good').mkdir(parents=True)
        (local/'good'/'Info.dat').write_text('{"_songName":"Good","_songDuration":123}',encoding='utf-8')
        window=LibraryWindow(base/'preview',os.environ['TEST_LANG'],device_provider=provider,transfer_backend=object(),push_backend=object())
        try:
            window.show();window.set_target(str(local));wait(lambda:window.local_ready and window.ratings.ready)
            browser=window.device_browser;browser.discover();wait(lambda:not browser.busy)
            browser.choices.setCurrentIndex(1);browser.read_selected();wait(lambda:not browser.busy)
            assert browser.state=='partial' and browser.skipped_names==1
            assert window.quest_ready and window.quest_incomplete and window.quest_model.rowCount()==1
            assert browser.media_deferred
            assert browser.status.text().startswith(tr('qt.device.state.partial',count=1,songs=1)+' · ')
            assert window.missing_model.rowCount()==0 and window.reports.binding() is None and window.transfers.binding() is None
            window.quest_model.checked={'good'};window.model.checked={'good'};window.transfers.render()
            assert not window.transfers.pull.isEnabled() and not window.transfers.push.isEnabled()
            assert not window.reports.button.isEnabled()
            provider.invalid=False;browser.read_selected();wait(lambda:not browser.busy)
            assert browser.state=='ready' and not window.quest_incomplete
            wait(lambda:window.reports.button.isEnabled())
            assert window.transfers.binding() is not None
            window.set_quest_snapshot(result['rows'],selected['identity'],skipped=1)
            assert window.quest_incomplete
            browser.cancel();assert not window.quest_ready and not window.quest_incomplete and browser.skipped_names==0
            assert provider.read_names and all(name=='good' for name in provider.read_names)
            assert not errors,errors
        finally:window.close();window.wait_closed()
    print('PASS binary names: no replacement path, partial warning, comparison/report/transfer blocked, clean reread and clear')

if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for language in ('zh','en'):
            for scale in ('1','1.25','1.5'):
                p=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,TEST_LANG=language,QT_SCALE_FACTOR=scale),capture_output=True,timeout=60)
                print(language,scale,p.stdout.decode('utf-8','replace'),p.stderr.decode('utf-8','replace'),flush=True)
                assert p.returncode==0
