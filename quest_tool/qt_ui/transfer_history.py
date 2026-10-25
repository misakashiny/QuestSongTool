"""Bounded local journal inspection. Records never authorize remote actions."""
import itertools
import json
import os
from pathlib import Path
import re

from ..workers import GuardError
from .adb_metadata import READ_ROOTS
from .device_protocol import descriptor
from .transfer_queue import folder_name


STATES = frozenset(('prepared', 'stage_intent', 'staged', 'verified', 'backup_intent',
    'backed_up', 'publish_intent', 'committed'))


def validate_record(value, stem):
    if not re.fullmatch('[0-9a-f]{32}', stem) or not isinstance(value, dict):
        raise GuardError('transfer_journal')
    if value.get('transaction') != stem or value.get('state') not in STATES:
        raise GuardError('transfer_journal')
    raw = value.get('device')
    if not isinstance(raw, dict):raise GuardError('transfer_journal')
    fresh = descriptor(dict(raw, label='Quest', state='device', identity_verified=True))
    if (fresh['kind'] != 'adb' or fresh['root'] not in READ_ROOTS
            or any(raw.get(key) != fresh[key] for key in
                ('identity', 'kind', 'device_id', 'root', 'model', 'codename', 'brand'))):
        raise GuardError('transfer_journal')
    name = folder_name(value.get('folder'))
    stage = fresh['root']+'/.quest-transfer-'+stem
    if (value.get('stage') != stage or value.get('target') != fresh['root']+'/'+name
            or value.get('backup') != stage+'/previous'
            or type(value.get('replace_existing')) is not bool
            or any(key in value and type(value[key]) is not bool for key in ('backup_created','recovery_required'))
            or value.get('cleanup', 'pending') not in ('pending', 'removed')):
        raise GuardError('transfer_journal')
    # Display only known, validated fields; ignore arbitrary extra JSON content.
    result = {key:value[key] for key in ('transaction','folder','stage','target','backup',
        'replace_existing','state','backup_created','recovery_required','cleanup') if key in value}
    result['device'] = {key:fresh[key] for key in ('identity','kind','device_id','root','model','codename','brand')}
    return result


def read_journals(directory):
    """Read at most 100 records from the first 1000 directory entries, never mkdir.

    Malformed/truncated/oversized/link records are diagnostic rows, not plans.
    No device provider is imported or called. Run this function on a worker.
    """
    root = Path(directory).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (root, *root.parents)):
        raise GuardError('transfer_journal_link')
    if not root.exists():return {'rows':[], 'limited':False}
    with os.scandir(root) as entries:
        bounded = list(itertools.islice(entries, 1001))
    candidates = sorted((entry.name for entry in bounded[:1000] if entry.name.endswith('.json')))
    limited = len(bounded)>1000 or len(candidates)>100
    rows = []
    for name in candidates[:100]:
        path = root/name
        try:
            before = path.lstat()
            if path.is_symlink() or path.is_junction() or not path.is_file() or before.st_size>65536:
                raise GuardError('transfer_journal')
            with path.open('rb') as stream:raw = stream.read(65537)
            after = path.lstat()
            if len(raw)>65536 or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) != (
                    after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
                raise GuardError('transfer_journal')
            record = validate_record(json.loads(raw.decode('utf-8')), path.stem)
            rows.append({'file':name, 'record':record})
        except (OSError, ValueError, TypeError, GuardError):
            rows.append({'file':name, 'record':None})
        except Exception:
            # descriptor rejects invalid hardware as DownloadError, also unreadable.
            rows.append({'file':name, 'record':None})
    return {'rows':rows, 'limited':limited}
