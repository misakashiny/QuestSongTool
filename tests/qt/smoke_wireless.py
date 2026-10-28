"""Wireless transport and Qt flow against sealed fake ADB, never a real Quest."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.adb import AdbError
    from quest_tool.downloads import DownloadError
    from quest_tool.messages import tr
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.wireless_client import WirelessClient
    from quest_tool.qt_ui.wireless_validation import validated
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.transfer_pull import AdbPullBackend
    from quest_tool.qt_ui.transfer_push import AdbPushBackend
    from quest_tool.qt_ui import bootstrap

    app = QApplication([]);app.setStyle('Fusion');errors = [];main = threading.get_ident()
    def report(kind,value,tb):errors.append(str(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook = report
    root = '/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels'
    endpoint = '192.168.1.42:5555';calls = [];external = []
    state = dict(connected=False,hardware='HW_A',ip=True,refuse=False,bad_pair=False,fail_tcp=False,
                 transport_hardware='HW_A',delay=False,exception=False,cancel_after_tcp=None)
    entered = threading.Event();release = threading.Event()
    def forbidden(*args,**kwargs):external.append(args);raise AssertionError('External I/O forbidden')
    def wait(predicate):
        end = time.monotonic()+10
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError('Wireless wait timed out')
    def rejects(fn, code=None):
        try:fn()
        except DownloadError as exc:
            if code:assert exc.code==code,(exc.code,code)
        else:raise AssertionError('Unsafe action accepted')
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp);exe = base/'fake-adb.exe';exe.write_bytes(b'never executed')
        def fake_run(args, **kwargs):
            assert args[0]==str(exe) and 0<kwargs['timeout']<=10,args
            serial = args[2] if len(args)>2 and args[1]=='-s' else ''
            command = args[3:] if serial else args[1:]
            calls.append((threading.get_ident(),serial,command))
            if command==['devices','-l']:
                output = 'List of devices attached\nUSB_A device\nphone device\nlocked unauthorized\n'
                if state['connected']:output += endpoint+' device\n'
            elif command[0]=='shell' and command[1].startswith('getprop ro.'):
                values = ['eureka','oculus','','Quest_3','eureka',state['transport_hardware'] if serial==endpoint else state['hardware']]
                if serial=='phone':values=['phone','vendor','','Phone','phone','NOT_QUEST']
                output = '\n'.join(values)+'\n'
            elif command==['shell','ip -4 addr show wlan0']:
                output = 'inet 192.168.1.42/24 brd 192.168.1.255' if state['ip'] else ''
            elif command==['shell','getprop dhcp.wlan0.ipaddress']:output = ''
            elif command==['shell','ip -4 route get 1.1.1.1']:output = ''
            elif command==['tcpip','5555']:
                assert serial=='USB_A'
                output = 'failed' if state['fail_tcp'] else 'restarting in TCP mode port: 5555'
                if state['cancel_after_tcp'] is not None:state['cancel_after_tcp'].set()
            elif command[0]=='pair':
                assert serial=='' and command[1:] == ['192.168.1.42:37123','123456']
                output = 'wrong pairing code' if state['bad_pair'] else 'Successfully paired to 192.168.1.42:37123'
            elif command==['connect',endpoint]:
                assert serial==''
                if state['exception']:raise AdbError('secret must not appear')
                if state['delay']:entered.set();assert release.wait(10)
                state['connected'] = not state['refuse']
                output = 'failed to connect to '+endpoint if state['refuse'] else 'connected to '+endpoint
            elif command[0]=='shell' and command[1].startswith('if [ -d '):output=root+'\n'
            else:raise AssertionError('Unexpected ADB command: '+repr(command))
            return subprocess.CompletedProcess(args,0,stdout=output.encode(),stderr=b'')
        with patch('subprocess.run',fake_run),patch('subprocess.Popen',forbidden),patch('socket.socket',forbidden):
            provider = AdbReadOnlyProvider(str(exe));client = WirelessClient(provider);cancel = threading.Event()
            assert not calls
            found = client.search({'mode':'scan'},0,'',cancel)['devices']
            assert len(found)==1 and found[0]['serial']=='USB_A'
            usb = dict(mode='usb',device=found[0],host='',port='5555')
            direct = dict(mode='connect',host='192.168.1.42',port='5555')
            pairing = direct | dict(mode='pair',pair_port='37123',code='123456')
            for host in ('127.0.0.1','0.0.0.0','224.0.0.1','host;rm','256.1.1.1'):
                count=len(calls);rejects(lambda:client.search(direct|dict(host=host),0,'',cancel),'wireless_address');assert len(calls)==count
            for port in ('0','65536','-1','12x'):rejects(lambda:validated(direct|dict(port=port)),'wireless_port')
            rejects(lambda:validated(pairing|dict(code='123')),'wireless_code')
            rejects(lambda:validated(usb|dict(device=dict(serial=endpoint,hardware='HW_A'))),'wireless_usb')
            state['hardware']='OTHER';count=len(calls)
            rejects(lambda:client.search(usb,0,'',cancel),'device_rejected')
            assert not any(cmd[0]=='tcpip' for _,_,cmd in calls[count:]);state['hardware']='HW_A'
            state['ip']=False;count=len(calls);rejects(lambda:client.search(usb,0,'',cancel),'wireless_address')
            assert not any(cmd[0]=='tcpip' for _,_,cmd in calls[count:]);state['ip']=True
            state['fail_tcp']=True;rejects(lambda:client.search(usb,0,'',cancel),'wireless_tcpip');state['fail_tcp']=False
            state['bad_pair']=True;count=len(calls);rejects(lambda:client.search(pairing,0,'',cancel),'wireless_pair')
            assert not any(cmd[0]=='connect' for _,_,cmd in calls[count:]);state['bad_pair']=False
            state['refuse']=True;rejects(lambda:client.search(direct,0,'',cancel),'wireless_connect');state['refuse']=False
            state['exception']=True;rejects(lambda:client.search(direct,0,'',cancel),'wireless_command');state['exception']=False
            state['transport_hardware']='OTHER';rejects(lambda:client.search(usb,0,'',cancel),'device_rejected');state['transport_hardware']='HW_A'
            state['cancel_after_tcp']=cancel;count=len(calls);rejects(lambda:client.search(usb,0,'',cancel))
            assert not any(cmd[0]=='connect' for _,_,cmd in calls[count:]);state['cancel_after_tcp']=None;cancel.clear()
            for request in (usb,direct,pairing):
                result=client.search(request,0,'',cancel);assert result['endpoint']==endpoint and result['hardware']=='HW_A'
            # The same provider lock serializes wireless with library/media I/O.
            provider._io_lock.acquire();count=len(calls);cancel.set()
            rejects(lambda:client.search(direct,0,'',cancel));provider._io_lock.release();assert len(calls)==count;cancel.clear()
            for language in ('zh','en'):
                factories=[]
                def factory():
                    factories.append(True);return AdbPullBackend(provider),AdbPushBackend(provider,base/'journals')
                window=LibraryWindow(base/language,language,device_provider=provider,wireless_client=client,transfer_factory=factory)
                count=len(calls);window.show()
                assert QTest.qWaitForWindowExposed(window,5000),(language,window.frameGeometry(),[(s.name(),s.availableGeometry()) for s in app.screens()],window.isVisible(),window.windowState())
                controller=window.wireless;browser=window.device_browser;transfers=window.transfers
                assert len(calls)==count and factories==[True] and transfers.page.isVisible()
                assert not hasattr(controller,'transfer_entry') and not hasattr(controller,'transfer_enable')
                assert not transfers.pull.isVisible() and transfers.push.isVisible()
                window.resize(1000,640);QTest.qWait(50)
                assert transfers.push.width()>=transfers.push.minimumSizeHint().width()
                assert window.rect().contains(transfers.push.mapTo(window,transfers.push.rect().center()))
                if os.environ.get('QUEST_QT_SHOTS'):
                    folder=Path(os.environ['QUEST_QT_SHOTS']);folder.mkdir(parents=True,exist_ok=True)
                    window.grab().save(str(folder/f'transfer-entry-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                assert factories==[True] and len(calls)==count and transfers.page.isVisible()
                assert not transfers.pull.isVisible() and transfers.push.isVisible()
                assert controller.button.isVisible() and controller.button.isEnabled()
                assert not transfers.pull.isEnabled() and not transfers.push.isEnabled()
                assert not (base/'journals').exists() and not transfers.jobs
                controller.button.click();form=controller.dialog;assert form is not None and len(calls)==count
                assert browser.connection_open and not browser.read.isEnabled()
                requests=[];form.requestReady.connect(requests.append)
                form.run_button.click();assert not requests and form.status.text()==tr('qt.wireless.state.wireless_usb')
                form.scan.click();wait(lambda:not controller.busy);assert form.devices.count()==1
                for mode in range(3):
                    form.mode.setCurrentIndex(mode);QTest.qWait(30)
                    assert form.fields['code'].isHidden()==(mode!=2)
                    form.fields['host'].setText('192.168.1.42')
                    form.fields['host'].setFocus();form.activateWindow();QTest.qWait(50)
                    QTest.keyClick(form.fields['host'],Qt.Key.Key_Tab);assert app.focusWidget() is form.fields['port']
                    for theme in ('light','dark'):
                        window.mode.setCurrentIndex(window.mode.findData(theme));form.resize(620,560);QTest.qWait(60)
                        for button in (form.run_button,form.cancel_button,form.close_button):
                            assert button.isVisible() and button.width()>=button.sizeHint().width()
                            assert form.rect().contains(button.mapTo(form,button.rect().center()))
                        assert form.run_button.geometry().bottom()<form.cancel_button.geometry().top()
                        for key in ('host','port'):
                            label=form.form.labelForField(form.fields[key])
                            assert label.isVisible() and label.width()>=label.sizeHint().width()
                form.mode.setCurrentIndex(0);form.fields['host'].clear()
                count=len(calls);form.run_button.click();wait(lambda:not controller.busy)
                assert controller.result is not None,(language,form.status.text(),form.devices.currentData(),requests[-1:] if requests else [])
                assert controller.result['endpoint']==endpoint and all(t!=main for t,_,_ in calls[count:])
                assert factories==[True] and not transfers.pull.isEnabled()
                shots=os.environ.get('QUEST_QT_SHOTS')
                if shots and language=='zh':
                    path=Path(shots);path.mkdir(parents=True,exist_ok=True)
                    window.mode.setCurrentIndex(window.mode.findData('light'));form.resize(700,620);QTest.qWait(100)
                    form.grab().save(str(path/'wireless-form.png'))
                form.close_button.click();wait(lambda:not browser.busy)
                assert browser.selected['device_id']==json.dumps([endpoint,'HW_A'],separators=(',',':'))
                assert browser.selected['root']==root and not window.quest_ready and browser.read.isEnabled()
                # Explicit next-step action reads the successfully connected
                # device once; merely closing above did not read anything.
                controller.button.click();form=controller.dialog;form.mode.setCurrentIndex(1)
                form.fields['host'].setText('192.168.1.42');form.run_button.click();wait(lambda:not controller.busy)
                assert form.read_button.isEnabled()
                with patch.object(provider,'read_metadata',return_value=dict(device=browser.selected,connected=True,rows=[])) as reader:
                    form.read_button.click();wait(lambda:window.quest_ready and not browser.busy and not browser.connection_open)
                    assert reader.call_count==1 and not browser.read_after_connect
                local=base/(language+'-library');local.mkdir();window.target=local;window.local_ready=True
                window.set_quest_snapshot([dict(folder='sample',title='Song',author='Artist',duration=120,error='')],browser.selected['identity'])
                window.quest_model.checked={'sample'};transfers.render();assert transfers.pull.isEnabled()
                window.quest_incomplete=True;transfers.render();assert transfers.pull.isEnabled() and not transfers.push.isEnabled() and not transfers.missing.isEnabled()
                window.quest_incomplete=False;transfers.render();count=len(calls)
                transfers.pull.click();assert transfers.dialog is not None and transfers.dialog.plan.folders==('sample',)
                assert len(calls)==count;transfers.dialog.close();QTest.qWait(30)
                # Closing while a command is running keeps the connection gate
                # until it exits, then ignores the late result. No automatic undo.
                controller.button.click();form=controller.dialog;form.mode.setCurrentIndex(1)
                form.fields['host'].setText('192.168.1.42');state['delay']=True;entered.clear();release.clear()
                form.run_button.click();wait(entered.is_set);form.close_button.click()
                assert controller.busy and browser.connection_open and controller.dialog is None
                controller.button.click();assert controller.dialog is None
                release.set();wait(lambda:not controller.busy);state['delay']=False
                assert not browser.connection_open and controller.result is None and not window.quest_ready
                # An active transfer confirmation prevents transport changes.
                transfers.dialog=object();controller.button.click();assert controller.dialog is None;transfers.dialog=None
                window.close();window.wait_closed()
            count=len(calls)
            def entry_exec(_app):
                entry=next(w for w in app.topLevelWidgets() if isinstance(w,LibraryWindow) and not w.closed)
                assert entry.wireless.button.isVisible() and entry.wireless.session is not None
                assert entry.transfers.page.isVisible() and entry.transfers.push.isVisible() and len(calls)==count
                entry.close();return 0
            with patch.object(bootstrap,'QApplication',lambda *_args:app),patch.object(QApplication,'exec',entry_exec):
                assert bootstrap.run(['--library','--quest-adb','--adb-path',str(exe),'--data-dir',str(base/'entry')])==0
            assert not external and not errors,(external,errors)
            assert not any(cmd[0] in ('push','pull','disconnect','kill-server') for _,_,cmd in calls)
    print('WIRELESS PASS: sealed ADB, identity, validation, cancellation, UI/CLI, gated transfers; scale',os.environ.get('QT_SCALE_FACTOR'))


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=100)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True)
            assert result.returncode==0
