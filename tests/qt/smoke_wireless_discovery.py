"""Sealed ADB workflow: discovery, paired continuation and native device UI."""
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
    from quest_tool.downloads import DownloadError
    from quest_tool.messages import tr
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.wireless_client import WirelessClient
    from quest_tool.qt_ui.wireless_discovery import connect_ports,mdns_services
    from quest_tool.qt_ui.wireless_validation import validated

    def rejects(function,code):
        try:function()
        except DownloadError as exc:assert exc.code==code,(exc.code,code)
        else:raise AssertionError('Expected rejection: '+code)

    source = ('List of discovered mdns services\n'
        'adb-HW-A _adb._tcp 192.168.1.42:5555\n'
        'studio-random _adb-tls-pairing._tcp 192.168.1.42:37123\n'
        'adb-HW-A-random _adb-tls-connect._tcp 192.168.1.42:41666\n'
        'adb-HW-A-random _adb-tls-connect._tcp.local. 192.168.1.42:41666\n'
        'unrelated _http._tcp 192.168.1.7:80\n'
        'invalid _adb._tcp 127.0.0.1:5555\n'
        'invalid _adb._tcp 192.168.1.99:65536\n'
        'invalid _adb._tcp 999.168.1.99:5555\n'
        'ipv6 _adb-tls-connect._tcp [fe80::1]:41666\n')
    decoded = mdns_services(source)
    assert len(decoded)==3 and connect_ports(decoded,'192.168.1.42')==[41666]
    assert not connect_ports(decoded,'192.168.1.43')
    rejects(lambda:mdns_services('x'*65537),'wireless_command')
    rejects(lambda:mdns_services('\n'.join(f'a{i} _adb._tcp 192.168.1.42:{i+1}' for i in range(129))),'wireless_command')
    assert validated(dict(mode='pair',host='192.168.1.42',port='',pair_port='37123',code='123456'))['port'] is None
    rejects(lambda:validated(dict(mode='pair',host='192.168.1.42',port='bad',pair_port='37123',code='123456')),'wireless_port')
    rejects(lambda:validated(dict(mode='pair',host='192.168.1.42',port='',pair_port='37123',code='bad')),'wireless_code')

    app = QApplication([]);app.setStyle('Fusion');errors = [];main = threading.get_ident()
    sys.excepthook = lambda kind,value,tb:(errors.append(str(value)),sys.__excepthook__(kind,value,tb))
    calls = [];state = dict(mdns=source,mdns_error=False,connected=False,alias=False,endpoint='',hardware='HW_A',
                            pairs=0,connects=0,refusals=0,cancel=None,ip_fallback=False,pair_error=False)
    def forbidden(*_args,**_kwargs):raise AssertionError('Real external I/O forbidden')
    def wait(predicate):
        end = time.monotonic()+10
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError(('timeout',errors))

    with tempfile.TemporaryDirectory(prefix='wireless-discovery-') as temporary:
        base = Path(temporary);exe = base/'sealed-adb.exe';exe.write_bytes(b'not executable')
        def fake_run(args,**kwargs):
            assert args[0]==str(exe) and 0<kwargs['timeout']<=10
            serial = args[2] if len(args)>2 and args[1]=='-s' else ''
            command = args[3:] if serial else args[1:]
            calls.append((threading.get_ident(),serial,tuple(command)))
            out,err,code = '','',0
            if command==['version']:
                out='Android Debug Bridge version 1.0.41\nVersion 35.0.2-12147458\nInstalled as C:/private/path/adb.exe\n'
            elif command==['mdns','services']:
                out=state['mdns']
                if state['mdns_error']:code=1;err='unknown command mdns';out=''
                if state['cancel'] is not None:state['cancel'].set()
            elif command==['devices','-l']:
                out='List of devices attached\nUSB_A device usb:1-1\n'
                if state['connected']:
                    transport='adb-HW-A-random._adb-tls-connect._tcp' if state['alias'] else state['endpoint']
                    out+=transport+' device\n'
            elif command[0]=='shell' and command[1].startswith('getprop ro.'):
                out='\n'.join(['eureka','oculus','','Quest_3','eureka',state['hardware']])+'\n'
            elif command==['shell','ip -4 addr show wlan0']:
                if state['ip_fallback']:code=1;err='device wlan0 not found'
                else:out='inet 192.168.1.42/24 brd 192.168.1.255'
            elif command==['shell','getprop dhcp.wlan0.ipaddress']:out=''
            elif command==['shell','ip -4 route get 1.1.1.1']:out='1.1.1.1 via 192.168.1.1 dev wlan9 src 192.168.1.42'
            elif command==['tcpip','5555']:err='restarting in TCP mode port: 5555'
            elif command[0]=='pair':
                assert command[1:]==['192.168.1.42:37123','123456'];state['pairs']+=1
                err='Successfully paired to 192.168.1.42:37123'
                if state['pair_error']:code=1;err='wrong pairing code 123456'
            elif command[0]=='connect':
                state['connects']+=1
                if state['refusals']:
                    state['refusals']-=1;code=1;err='failed to connect'
                else:
                    state['endpoint']=command[1];state['connected']=True;err='already connected to '+command[1]
            else:raise AssertionError(('Unexpected command',serial,command))
            return subprocess.CompletedProcess(args,code,stdout=out.encode(),stderr=err.encode())

        with patch('subprocess.run',fake_run),patch('subprocess.Popen',forbidden),patch('socket.socket',forbidden):
            provider = AdbReadOnlyProvider(str(exe));client = WirelessClient(provider,discovery_wait=0);cancel=threading.Event()
            assert not calls
            pairing=dict(mode='pair',host='192.168.1.42',port='',pair_port='37123',code='123456')
            result=client.search(pairing,0,'',cancel)
            assert result['connected'] and result['endpoint']=='192.168.1.42:41666' and state['pairs']==1
            # Successful pairing with only a legacy service must not guess 5555.
            state.update(mdns='legacy _adb._tcp 192.168.1.42:5555\n',connected=False)
            before=state['connects'];result=client.search(pairing,0,'',cancel)
            assert result['paired'] and not result['connected'] and state['connects']==before
            start=time.monotonic()
            assert WirelessClient(provider,discovery_wait=.05).search(pairing,0,'',cancel)['paired']
            assert time.monotonic()-start<1 and state['connects']==before
            pairs=state['pairs'];resume=pairing|dict(mode='paired_connect',port='41666',code='')
            assert client.search(resume,0,'',cancel)['connected'] and state['pairs']==pairs
            # Multiple services on one IP are not enough to choose a port.
            state['mdns']=source+'other _adb-tls-connect._tcp 192.168.1.42:41667\n'
            before=state['connects'];result=client.search(pairing,0,'',cancel)
            assert result['reason']=='port_ambiguous' and state['connects']==before
            # Older ADB remains usable through explicit ports.
            state['mdns_error']=True;result=client.search(pairing,0,'',cancel)
            assert result['paired'] and result['reason']=='mdns_unavailable'
            assert client.search(resume,0,'',cancel)['connected']
            state.update(mdns_error=False,mdns=source,alias=True)
            result=client.search(pairing,0,'',cancel)
            assert result['serial']=='adb-HW-A-random._adb-tls-connect._tcp'
            assert any(row['kind']=='connected' and row['port']==41666 for row in client.search(dict(mode='discover'),0,'',cancel)['devices'])
            state.update(alias=False,connected=False,refusals=2)
            usb=dict(mode='usb',device=dict(serial='USB_A',hardware='HW_A'),host='',port='5555',operation='usb-test')
            assert client.search(usb,0,'',cancel)['hardware']=='HW_A'
            stages=[]
            while not client.events.empty():stages.append(client.events.get_nowait()['state'])
            assert stages==['checking_usb','reading_ip','enabling','connecting','verifying']
            state['ip_fallback']=True
            assert client.search(usb|dict(operation=''),0,'',cancel)['endpoint']=='192.168.1.42:5555'
            state['ip_fallback']=False
            state['hardware']='OTHER'
            rejects(lambda:client.search(usb,0,'',cancel),'device_rejected');state['hardware']='HW_A'
            state['cancel']=cancel;before=state['connects']
            rejects(lambda:client.search(pairing,0,'',cancel),'cancelled')
            assert state['connects']==before;state['cancel']=None;cancel.clear()
            state['pair_error']=True
            rejects(lambda:client.search(pairing,0,'',cancel),'wireless_pair');state['pair_error']=False
            diagnostic=client.search(dict(mode='diagnose'),0,'',cancel)
            assert diagnostic['version']=='1.0.41 / 35.0.2-12147458' and 'private' not in repr(diagnostic)
            assert client.search(dict(mode='discover'),0,'',cancel)['devices']

            for language in ('zh','en'):
                state.update(mdns='',connected=False)
                w=LibraryWindow(base/language,language,device_provider=provider,wireless_client=client)
                w.show();assert QTest.qWaitForWindowExposed(w,5000)
                controller=w.wireless;count=len(calls)
                QTest.mouseClick(controller.button,Qt.MouseButton.LeftButton)
                form=controller.dialog;assert form and len(calls)==count and not hasattr(form,'transfer')
                QTest.mouseClick(form.mode,Qt.MouseButton.LeftButton,pos=form.mode.tabRect(2).center())
                assert form.connection_mode()=='pair' and not form.fields['port'].text()
                form.fields['host'].setText('192.168.1.42');form.fields['pair_port'].setText('37123');form.fields['code'].setText('123456')
                count=len(calls);form.run_button.click();wait(lambda:not controller.busy)
                assert form.paired==('192.168.1.42','37123') and not form.fields['code'].text()
                assert not form.read_button.isEnabled() and controller.result is None
                assert tr('qt.wireless.state.paired_wait') in form.status.text()
                assert all(thread!=main for thread,_serial,_command in calls[count:])
                shots=os.environ.get('QUEST_QT_SHOTS')
                if shots:form.grab().save(str(Path(shots)/f'paired-wait-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                pairs=state['pairs'];form.fields['port'].setFocus();QTest.keyClicks(form.fields['port'],'41666')
                form.run_button.click();wait(lambda:not controller.busy)
                assert state['pairs']==pairs and form.read_button.isEnabled() and controller.result['connected']
                assert not form.run_button.isEnabled()
                old_operation=controller.operation
                form.diagnose.click();wait(lambda:not controller.busy)
                assert '35.0.2' in form.diagnostic.text() and form.read_button.isEnabled()
                text=form.status.text();client.events.put(dict(operation=old_operation,state='verifying'));controller.poll()
                assert form.status.text()==text
                form.fields['host'].setFocus();QTest.keyClick(form.fields['host'],Qt.Key.Key_End);QTest.keyClicks(form.fields['host'],'9')
                assert controller.result is None and not form.read_button.isEnabled() and form.paired is None
                assert form.run_button.isEnabled()
                state.update(mdns=source,connected=False)
                form.mode.setCurrentIndex(1);form.discover.click();wait(lambda:not controller.busy)
                assert form.discovered.count()==4
                index=next(i for i in range(1,form.discovered.count()) if form.discovered.itemData(i)['kind']=='pairing')
                before=len(calls);form.choose_discovered(index)
                assert form.connection_mode()=='pair' and form.fields['pair_port'].text()=='37123' and len(calls)==before
                for mode in ('light','dark'):
                    w.mode.setCurrentIndex(w.mode.findData(mode));form.resize(620,560);QTest.qWait(75)
                    for button in (form.run_button,form.read_button,form.cancel_button,form.close_button):
                        assert button.isVisible() and button.width()>=button.sizeHint().width(),(language,button.text(),button.width(),button.sizeHint())
                    if shots:form.grab().save(str(Path(shots)/f'manager-{language}-{mode}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                form.close_button.click();assert not w.device_browser.connection_open
                w.close();w.wait_closed()
            assert not errors,errors
            assert not any(command[0] in ('push','pull','disconnect','kill-server','install') for _thread,_serial,command in calls)
    print('PASS wireless discovery: services/limits, stderr and exit codes, automatic TLS port, paired continuation, ambiguity, legacy ADB fallback, aliases, retries, identity, cancellation, bilingual native UI')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True)
            assert result.returncode==0
