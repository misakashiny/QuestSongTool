"""Plain values shared by the wireless form and its background client."""
import ipaddress
import re

from ..downloads import DownloadError


def address(value):
    try:ip = ipaddress.IPv4Address(value)
    except (ValueError, TypeError):raise DownloadError('wireless_address') from None
    if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_reserved or ip.is_link_local:
        raise DownloadError('wireless_address')
    return str(ip)


def port(value):
    if not isinstance(value,str) or re.fullmatch(r'[0-9]{1,5}',value) is None or not 1<=int(value)<=65535:
        raise DownloadError('wireless_port')
    return int(value)


def validated(request):
    if not isinstance(request,dict):raise DownloadError('wireless_input')
    mode = request.get('mode')
    if mode=='scan':return {'mode':mode}
    if mode not in ('usb','connect','pair'):raise DownloadError('wireless_input')
    item = {'mode':mode,'port':port(request.get('port'))}
    host = request.get('host','')
    if not isinstance(host,str):raise DownloadError('wireless_address')
    host = host.strip()
    item['host'] = address(host) if host or mode!='usb' else ''
    if mode=='usb':
        device = request.get('device')
        if not isinstance(device,dict) or any(not isinstance(device.get(k),str) for k in ('serial','hardware')):
            raise DownloadError('wireless_usb')
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,255}',device['serial']) is None:
            raise DownloadError('wireless_usb')
        item['device'] = dict(device)
    if mode=='pair':
        item['pair_port'] = port(request.get('pair_port'))
        code = request.get('code','')
        if not isinstance(code,str) or re.fullmatch(r'[0-9]{6}',code) is None:
            raise DownloadError('wireless_code')
        item['code'] = code
    return item
