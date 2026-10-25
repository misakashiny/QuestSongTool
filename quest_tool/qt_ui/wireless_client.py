"""Explicit wireless setup. Construction never starts ADB or discovers devices.

Only a user-requested action may change transport state. No song file commands,
network scanning, remembered credentials or automatic setup on startup.
"""
import re
import time

from ..adb import AdbError, AdbUnavailable
from ..downloads import DownloadError, check_cancel
from .wireless_validation import address, validated


class WirelessClient:
    def __init__(self, provider):
        self.provider = provider

    @staticmethod
    def command(bridge, args, cancel, deadline):
        # Error messages never include raw ADB output or the pairing code.
        check_cancel(cancel)
        remaining = deadline-time.monotonic()
        if remaining<=0:raise DownloadError('device_timeout')
        try:text = bridge.run(args,timeout=min(10,remaining),check=False)
        except AdbUnavailable:raise DownloadError('device_adb_path') from None
        except AdbError:raise DownloadError('wireless_command') from None
        check_cancel(cancel)
        if time.monotonic()>deadline:raise DownloadError('device_timeout')
        if not isinstance(text,str) or len(text)>65536:raise DownloadError('wireless_command')
        return text.lower()

    def search(self, request, _page, _order, cancel):
        item = validated(request);provider = self.provider
        with provider._operation(cancel):
            deadline = time.monotonic()+60
            if item['mode']=='scan':
                devices = []
                for candidate in provider._devices(cancel,deadline):
                    if not candidate.usable or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,255}',candidate.serial) is None:continue
                    try:_bridge,fresh,hardware = provider._identify(candidate.serial,cancel,deadline)
                    except DownloadError as exc:
                        if exc.code=='device_rejected':continue
                        raise
                    devices.append(dict(serial=fresh.serial,hardware=hardware,label=f'{fresh.model} · {fresh.serial}'))
                return {'mode':'scan','devices':devices}
            host = item['host'];expected = None
            if item['mode']=='usb':
                selected = item['device'];serial = selected['serial']
                if not any(d.serial==serial and d.usable for d in provider._devices(cancel,deadline)):
                    raise DownloadError('wireless_usb')
                bridge,_fresh,expected = provider._identify(serial,cancel,deadline)
                if expected!=selected['hardware']:raise DownloadError('device_rejected')
                if not host:
                    for command in ('ip -4 addr show wlan0','getprop dhcp.wlan0.ipaddress'):
                        output = self.command(bridge,['shell',command],cancel,deadline)
                        for token in re.findall(r'(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])',output):
                            try:host = address(token);break
                            except DownloadError:continue
                        if host:break
                if not host:raise DownloadError('wireless_address')
                output = self.command(bridge,['tcpip',str(item['port'])],cancel,deadline)
                if 'restarting in tcp mode' not in output or any(s in output for s in ('error','failed')):
                    raise DownloadError('wireless_tcpip')
            bridge = provider._bridge()
            if item['mode']=='pair':
                output = self.command(bridge,['pair',f"{host}:{item['pair_port']}",item['code']],cancel,deadline)
                if not any(s in output for s in ('successfully paired','already paired')) or any(s in output for s in ('failed','error')):
                    raise DownloadError('wireless_pair')
            endpoint = f"{host}:{item['port']}"
            # A USB adbd restart may not be ready immediately. Bounded retries
            # target this exact address only; no reconnect loop after completion.
            connected = False
            for attempt in range(3 if item['mode']=='usb' else 1):
                if attempt:
                    cancel.wait(.5);check_cancel(cancel)
                output = self.command(bridge,['connect',endpoint],cancel,deadline)
                connected = 'connected to' in output and not any(s in output for s in ('unable','failed','error'))
                if connected:break
            if not connected:raise DownloadError('wireless_connect')
            # A successful connect response does not prove the model or identity.
            if not any(d.serial==endpoint and d.usable for d in provider._devices(cancel,deadline)):
                raise DownloadError('wireless_connect')
            _bridge,_fresh,hardware = provider._identify(endpoint,cancel,deadline)
            if expected is not None and hardware!=expected:raise DownloadError('device_rejected')
            return {'mode':item['mode'],'endpoint':endpoint,'hardware':hardware}
