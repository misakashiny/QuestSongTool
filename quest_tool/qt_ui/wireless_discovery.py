"""Bounded mDNS text decoding. Never opens sockets or starts processes."""
import re

from ..downloads import DownloadError
from .wireless_validation import address, port

SERVICES = {'_adb._tcp':'legacy', '_adb-tls-connect._tcp':'connect',
            '_adb-tls-pairing._tcp':'pairing'}


def mdns_services(text):
    if not isinstance(text,str) or len(text)>65536:
        raise DownloadError('wireless_command')
    found = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields)<3:continue
        service = fields[1].removesuffix('.').removesuffix('.local')
        if service not in SERVICES:continue
        match = re.fullmatch(r'([0-9.]+):([0-9]+)',fields[2])
        if match is None:continue  # This first version supports IPv4 only.
        try:host,number = address(match[1]),port(match[2])
        except DownloadError:continue
        if len(fields[0])>256 or any(ord(c)<32 for c in fields[0]):continue
        kind = SERVICES[service]
        found[(host,number,kind)] = dict(host=host,port=number,kind=kind,instance=fields[0])
        if len(found)>128:raise DownloadError('wireless_command')
    return sorted(found.values(),key=lambda item:(item['host'],item['kind'],item['port']))


def connect_ports(rows, host):
    """TLS pairing must never fall back to the legacy 5555 service."""
    return sorted({row['port'] for row in rows if row['host']==host and row['kind']=='connect'})
