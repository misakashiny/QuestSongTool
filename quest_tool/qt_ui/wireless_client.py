"""Explicit connection workflow with bounded ADB discovery and plain events.

Construction is passive. No socket scanning, song commands, stored credentials,
server restart, or automatic setup on application startup.
"""
import queue
import re
import time

from ..adb import AdbError, AdbUnavailable
from ..downloads import DownloadError, check_cancel
from .wireless_discovery import connect_ports, mdns_services
from .wireless_validation import address, validated


class WirelessClient:
    def __init__(self, provider, discovery_wait=8):
        self.provider = provider
        self.discovery_wait = max(0,min(15,discovery_wait))
        self.events = queue.SimpleQueue()

    def stage(self, item, state):
        if item.get('operation'):
            self.events.put({'operation':item['operation'],'state':state})

    @staticmethod
    def result(bridge, args, cancel, deadline, timeout=10):
        check_cancel(cancel)
        remaining = deadline-time.monotonic()
        if remaining<=0:raise DownloadError('device_timeout')
        try:result = bridge.run_result(args,timeout=min(timeout,remaining))
        except AdbUnavailable:raise DownloadError('device_adb_path') from None
        except AdbError as exc:
            raise DownloadError('device_timeout' if str(exc)=='ADB command timed out' else 'wireless_command') from None
        check_cancel(cancel)
        if time.monotonic()>deadline:raise DownloadError('device_timeout')
        text = result.stdout+'\n'+result.stderr
        if len(text)>65536:raise DownloadError('wireless_command')
        return result.returncode,text

    def command(self, bridge, args, cancel, deadline):
        code,text = self.result(bridge,args,cancel,deadline)
        if code:
            lowered = text.lower()
            error = ('device_unauthorized' if 'unauthorized' in lowered else 'device_offline' if 'offline' in lowered
                     else 'wireless_pair' if args[0]=='pair' else 'wireless_tcpip' if args[0]=='tcpip' else 'wireless_command')
            raise DownloadError(error)
        return text.lower()

    def mdns(self, cancel, deadline):
        code,text = self.result(self.provider._bridge(),['mdns','services'],cancel,deadline,3)
        lowered = text.lower()
        if code or any(word in lowered for word in ('unknown command','not supported','not available','failed','error:')):
            return [],'mdns_unavailable'
        return mdns_services(text),'mdns_ready'

    def scan_usb(self, cancel, deadline):
        devices = []
        with self.provider._operation(cancel):
            for candidate in self.provider._devices(cancel,deadline):
                if not candidate.usable or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,255}',candidate.serial) is None:continue
                try:_bridge,fresh,hardware = self.provider._identify(candidate.serial,cancel,deadline)
                except DownloadError as exc:
                    if exc.code=='device_rejected':continue
                    raise
                devices.append(dict(serial=fresh.serial,hardware=hardware,label=f'{fresh.model} · {fresh.serial}'))
        return devices

    def discover(self, cancel, deadline):
        rows,state = self.mdns(cancel,deadline)
        with self.provider._operation(cancel):
            for candidate in self.provider._devices(cancel,deadline):
                endpoint = re.fullmatch(r'([0-9.]+):([0-9]+)',candidate.serial)
                if not candidate.usable:continue
                if endpoint is None:
                    mapped = [row for row in rows if row['kind']=='connect' and candidate.serial==row['instance']+'._adb-tls-connect._tcp']
                    if len(mapped)!=1:continue
                    endpoint = (candidate.serial,mapped[0]['host'],str(mapped[0]['port']))
                try:
                    host = address(endpoint[1]);number = int(endpoint[2])
                    if not 1<=number<=65535:continue
                    _bridge,fresh,hardware = self.provider._identify(candidate.serial,cancel,deadline)
                except DownloadError as exc:
                    if exc.code in ('device_rejected','wireless_address'):continue
                    raise
                rows = [row for row in rows if (row['host'],row['port'])!=(host,number)]
                rows.append(dict(host=host,port=number,kind='connected',instance=fresh.model,hardware=hardware))
        return dict(mode='discover',devices=rows,discovery=state)

    def diagnose(self, cancel, deadline):
        code,text = self.result(self.provider._bridge(),['version'],cancel,deadline)
        if code:raise DownloadError('device_adb_path')
        versions = re.findall(r'(?im)^(?:Android Debug Bridge version|Version)\s+([\w.+-]{1,80})\s*$',text)
        _rows,state = self.mdns(cancel,deadline)
        return dict(mode='diagnose',version=' / '.join(versions) or 'unknown',discovery=state)

    def check(self, item, cancel, deadline):
        """Observe an existing transport; never connect, pair or read a library."""
        with self.provider._operation(cancel):
            device=next((row for row in self.provider._devices(cancel,deadline) if row.serial==item['serial']),None)
            state='disconnected' if device is None else device.state
            if state!='device':
                return dict(mode='check',state=state if state in ('offline','unauthorized') else 'disconnected')
            _bridge,_fresh,hardware=self.provider._identify(item['serial'],cancel,deadline)
            if hardware!=item['hardware']:raise DownloadError('device_rejected')
            return dict(mode='check',state='online')

    def usb(self, item, cancel, deadline):
        selected = item['device'];serial = selected['serial'];host = item['host']
        self.stage(item,'checking_usb')
        with self.provider._operation(cancel):
            if not any(d.serial==serial and d.usable for d in self.provider._devices(cancel,deadline)):
                raise DownloadError('wireless_usb')
            bridge,_fresh,hardware = self.provider._identify(serial,cancel,deadline)
            if hardware!=selected['hardware']:raise DownloadError('device_rejected')
            if not host:
                self.stage(item,'reading_ip')
                for command in ('ip -4 addr show wlan0','getprop dhcp.wlan0.ipaddress','ip -4 route get 1.1.1.1'):
                    try:output = self.command(bridge,['shell',command],cancel,deadline)
                    except DownloadError as exc:
                        if exc.code=='wireless_command':continue
                        raise
                    if 'route get' in command:tokens = re.findall(r'\bsrc\s+([0-9.]+)',output)
                    elif 'addr show' in command:tokens = re.findall(r'\binet\s+([0-9.]+)/[0-9]+',output)
                    else:tokens = [output.strip()]
                    for token in tokens:
                        try:host = address(token);break
                        except DownloadError:continue
                    if host:break
            if not host:raise DownloadError('wireless_address')
            self.stage(item,'enabling')
            output = self.command(bridge,['tcpip',str(item['port'])],cancel,deadline)
            if 'restarting in tcp mode' not in output or any(s in output for s in ('error','failed')):
                raise DownloadError('wireless_tcpip')
        return host,hardware

    def resolve_port(self, item, cancel, deadline):
        self.stage(item,'finding_port')
        end = min(deadline,time.monotonic()+self.discovery_wait)
        while True:
            try:rows,state = self.mdns(cancel,end if self.discovery_wait else deadline)
            except DownloadError as exc:
                if exc.code=='cancelled':raise
                return None,'mdns_unavailable'
            ports = connect_ports(rows,item['host'])
            if len(ports)==1:return ports[0],''
            if len(ports)>1:return None,'port_ambiguous'
            if state=='mdns_unavailable' or time.monotonic()>=end:return None,state
            cancel.wait(min(.5,max(0,end-time.monotonic())));check_cancel(cancel)
            if time.monotonic()>=end:return None,state

    def connect(self, item, host, number, expected, cancel, deadline):
        endpoint = f'{host}:{number}';self.stage(item,'connecting')
        with self.provider._operation(cancel):
            bridge = self.provider._bridge();connected = False
            for attempt in range(6 if item['mode']=='usb' else 1):
                if attempt:cancel.wait(min(.25*2**(attempt-1),1));check_cancel(cancel)
                code,output = self.result(bridge,['connect',endpoint],cancel,deadline)
                output = output.lower()
                if 'unauthorized' in output:raise DownloadError('device_unauthorized')
                connected = code==0 and 'connected to' in output and not any(s in output for s in ('unable','failed','error'))
                if connected:break
            if not connected:raise DownloadError('wireless_connect')
            self.stage(item,'verifying')
            devices = self.provider._devices(cancel,deadline);serial = endpoint
            if not any(d.serial==endpoint and d.usable for d in devices):
                rows,_state = self.mdns(cancel,deadline)
                aliases = {row['instance']+'._adb-tls-connect._tcp' for row in rows
                           if row['kind']=='connect' and (row['host'],row['port'])==(host,number)}
                matches = [d.serial for d in devices if d.usable and d.serial in aliases]
                if len(matches)!=1:raise DownloadError('wireless_connect')
                serial = matches[0]
            _bridge,_fresh,hardware = self.provider._identify(serial,cancel,deadline)
            if expected is not None and hardware!=expected:raise DownloadError('device_rejected')
        return dict(mode=item['mode'],endpoint=endpoint,serial=serial,hardware=hardware,model=_fresh.model,connected=True)

    def search(self, request, _page, _order, cancel):
        item = validated(request);deadline = time.monotonic()+60
        if item['mode']=='scan':return dict(mode='scan',devices=self.scan_usb(cancel,deadline))
        if item['mode']=='discover':return self.discover(cancel,deadline)
        if item['mode']=='diagnose':return self.diagnose(cancel,deadline)
        if item['mode']=='check':return self.check(item,cancel,min(deadline,time.monotonic()+15))
        host = item['host'];expected = item.get('hardware');number = item['port']
        if item['mode']=='usb':host,expected = self.usb(item,cancel,deadline)
        if item['mode']=='pair':
            self.stage(item,'pairing')
            with self.provider._operation(cancel):
                output = self.command(self.provider._bridge(),['pair',f"{host}:{item['pair_port']}",item['code']],cancel,deadline)
            if not any(s in output for s in ('successfully paired','already paired')) or any(s in output for s in ('failed','error')):
                raise DownloadError('wireless_pair')
        paired = item['mode'] in ('pair','paired_connect')
        if paired:
            reason = ''
            if number is None:number,reason = self.resolve_port(item,cancel,deadline)
            if number is None:
                return dict(mode=item['mode'],connected=False,paired=True,host=host,pair_port=item['pair_port'],reason=reason)
        try:return self.connect(item,host,number,expected,cancel,deadline)
        except DownloadError as exc:
            if not paired or exc.code=='cancelled':raise
            return dict(mode=item['mode'],connected=False,paired=True,host=host,pair_port=item['pair_port'],reason=exc.code)
