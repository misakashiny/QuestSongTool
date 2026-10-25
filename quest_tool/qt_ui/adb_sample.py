"""Explicit bounded ADB acceptance sampler; no GUI, settings or device I/O on import."""
import argparse
import hashlib
import json
import shlex
import sys
import threading
import time

from ..constants import BACKUP_SUFFIX
from ..downloads import DownloadError, check_cancel
from ..messages import tr
from ..songs import decode_json_bytes, parse_info_file
from .adb_metadata import CAPABILITY, folder_listing
from .adb_provider import AdbReadOnlyProvider, READ_ROOTS
from .device_protocol import snapshot_rows
from .adb_duration import remote_duration


def validate(serial, root, limit):
    AdbReadOnlyProvider._serial(serial)
    if root not in READ_ROOTS or type(limit) is not int or not 1<=limit<=16:
        raise DownloadError('sample_arguments')


class SampleProvider(AdbReadOnlyProvider):
    """Observe only capability and timings, never log raw device contents."""
    def __init__(self, adb_path=None):
        super().__init__(adb_path);self.capability=None;self.command_seconds=[]

    def _run(self, bridge, args, cancel, deadline, *, binary=False):
        start=time.monotonic()
        result=super()._run(bridge,args,cancel,deadline,binary=binary)
        self.command_seconds.append(round(time.monotonic()-start,6))
        if args==['exec-out',CAPABILITY]:self.capability=result
        return result


def sample(serial, root, limit=8, *, probe_audio=False, adb_path=None, cancel=None, provider_factory=SampleProvider):
    """List one root, read at most limit unique folders twice; never publish a library."""
    validate(serial,root,limit)
    if type(probe_audio) is not bool:raise DownloadError('sample_arguments')
    cancel=cancel if cancel is not None else threading.Event();check_cancel(cancel)
    provider=provider_factory(adb_path);deadline=time.monotonic()+120;start=time.monotonic()
    def verify(expected=None):
        check_cancel(cancel)
        candidate=next((item for item in provider._devices(cancel,deadline) if item.serial==serial),None)
        if candidate is None:raise DownloadError('device_disconnected')
        if not candidate.usable:
            raise DownloadError('device_unauthorized' if candidate.state=='unauthorized' else 'device_offline')
        bridge,item,hardware=provider._identify(serial,cancel,deadline)
        canonical=provider._root(bridge,root,cancel,deadline)
        if not canonical:raise DownloadError('device_roots')
        identity=provider._descriptor(item,hardware,canonical)
        if expected is not None and identity['identity']!=expected:raise DownloadError('device_rejected')
        return bridge,identity
    bridge,identity=verify()
    listing=provider._run(bridge,['shell','cd '+shlex.quote(identity['root'])+' && ls -1p'],cancel,deadline,binary=True)
    folders,skipped=folder_listing(listing)
    snapshot_rows([dict(folder=name,title='',author='',error='',duration=0) for name in folders])
    available=sorted((name for name in folders if not name.startswith('.') and not name.endswith(BACKUP_SUFFIX)),key=lambda name:(name.lower(),name))
    chosen=available[:limit]
    if not chosen:raise DownloadError('sample_empty')
    metadata={}
    def decode(pairs):
        rows=[];digests=[];names=[]
        for name,raw in pairs:
            check_cancel(cancel);names.append(name)
            data=decode_json_bytes(raw) if raw else None;metadata[name]=data
            meta=parse_info_file(data,name)
            rows.append(dict(folder=name,title=meta.title,author=meta.author,error=meta.parse_error,duration=meta.duration))
            digests.append(hashlib.sha256(raw).hexdigest())
        if names!=chosen:raise DownloadError('device_protocol')
        return snapshot_rows(rows),digests
    automatic=decode(provider._contents(bridge,identity['root'],chosen,cancel,deadline))
    bridge,_=verify(identity['identity'])
    def singles():
        for name in chosen:yield from provider._contents(bridge,identity['root'],[name],cancel,deadline)
    individual=decode(singles());verify(identity['identity']);check_cancel(cancel)
    if automatic!=individual:raise DownloadError('sample_mismatch')
    sources=[]
    for row in automatic[0]:
        source='metadata' if row['duration']>0 else 'unknown'
        if probe_audio and row['duration']<=0 and not row['error']:
            row['duration']=remote_duration(provider._run,bridge,identity['root'],row['folder'],metadata[row['folder']],cancel,deadline)
            if row['duration']>0:source='audio_header'
        sources.append(source)
    if probe_audio:verify(identity['identity'])
    capability=getattr(provider,'capability',None)
    mode='batch' if capability==b'QSC_BATCH' else 'fallback' if capability==b'QSC_SINGLE' else 'single_below_threshold'
    result = dict(stage='M7A2F',scope='sample_only',complete_library=False,serial_suffix=serial[-4:],root=identity['root'],
        limit=limit,available_folders=len(available),sampled=len(chosen),order='lower_then_name',
        automatic_mode=mode,batch_verified=mode=='batch',equal=True,rows=automatic[0],metadata_sha256=automatic[1],
        duration_sources=sources,audio_duration_requested=probe_audio,
        seconds=round(time.monotonic()-start,6),command_seconds=getattr(provider,'command_seconds',[]))
    if skipped:result['skipped_names'] = skipped
    return result


def main(argv=None, *, provider_factory=SampleProvider):
    parser=argparse.ArgumentParser(description=tr('qt.sample.cli'))
    parser.add_argument('--serial',required=True);parser.add_argument('--root',required=True)
    parser.add_argument('--limit',type=int,default=8);parser.add_argument('--adb-path')
    parser.add_argument('--execute',action='store_true',help=tr('qt.sample.execute'))
    parser.add_argument('--duration',action='store_true',help=tr('qt.sample.duration'))
    options=parser.parse_args(argv)
    try:
        validate(options.serial,options.root,options.limit)
        if not options.execute:
            result=dict(executed=False,scope='sample_only',limit=options.limit,root=options.root,serial_suffix=options.serial[-4:],audio_duration_requested=options.duration)
        else:result=sample(options.serial,options.root,options.limit,probe_audio=options.duration,adb_path=options.adb_path,provider_factory=provider_factory)
        print(json.dumps(result,ensure_ascii=True,indent=2));return 0
    except DownloadError as exc:
        print(json.dumps(dict(error=exc.code,complete_library=False),ensure_ascii=True),file=sys.stderr);return 2
    except KeyboardInterrupt:return 130


if __name__=='__main__':raise SystemExit(main())
