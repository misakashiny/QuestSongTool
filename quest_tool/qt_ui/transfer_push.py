"""Journaled staged push. No automatic backend, overwrite UI, or remote deletion."""
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
import threading
import time
import uuid

from ..constants import INFO_FILE_NAMES
from ..adb import _quote
from ..device_adb import AdbFS
from ..fsapi import Location
from ..workers import GuardError, guard_copy
from .transfer_binding import adb_binding
from .transfer_queue import folder_name


class StagingBridge:
    """Constrain legacy copy_into to ASCII payload inside our claimed stage."""
    def __init__(self, backend, plan, stage, bridge):
        self.backend,self.plan,self.stage,self.bridge = backend,plan,stage,bridge

    def exists(self, path):
        if path!=self.stage+'/payload':raise GuardError('transfer_staging')
        return self.bridge.exists(path)

    def is_dir(self, path):
        if path!=self.stage+'/payload':raise GuardError('transfer_staging')
        return self.bridge.is_dir(path)

    def push(self, local, parent):
        if parent!=self.stage or local.name!='payload' or not str(local).isascii():
            raise GuardError('transfer_staging')
        self.backend._binding(self.plan)
        if self.backend._command(self.plan,self.stage,'',probe=True)!='QSC_STAGE':
            raise GuardError('transfer_staging')
        self.bridge.run(['push',str(local),parent],timeout=300)

    def shell(self, command, **kwargs):
        expected = f"find {_quote(self.stage+'/payload')} -type f -printf '%s\\n' 2>/dev/null"
        if command!=expected:raise GuardError('transfer_staging')
        return self.bridge.shell(command,**kwargs)

    def remove_tree(self, _path):
        raise GuardError('transfer_no_delete')

    def rename(self, _source, _target):
        raise GuardError('transfer_staging')


class AdbPushBackend:
    def __init__(self, provider, journal_dir, *, replace_existing=False):
        self.provider = provider
        self.journal_dir = Path(journal_dir)
        self.replace_existing = replace_existing

    def _binding(self, plan):
        return adb_binding(self.provider,plan,'to_quest')

    def revalidate(self, plan):
        return self._binding(plan)[1]

    def _run(self, bridge, command):
        return self.provider._run(bridge,['shell',command],threading.Event(),time.monotonic()+120)

    def _command(self, plan, stage, command, *, probe=False):
        bridge,fresh = self._binding(plan)
        root = shlex.quote(fresh['root']);quoted = shlex.quote(stage)
        if not stage.startswith(fresh['root']+'/.quest-transfer-') or '/' in stage[len(fresh['root'])+1:]:
            raise GuardError('transfer_staging')
        guard = (f'[ ! -L {root} ] && [ "$(readlink -f {root})" = {root} ] && '
            f'[ ! -L {quoted} ] && [ -d {quoted} ] && [ "$(readlink -f {quoted})" = {quoted} ]')
        return self._run(bridge,guard+(' && printf QSC_STAGE' if probe else ' && '+command))

    def exists(self, plan, folder):
        name = folder_name(folder)
        if name not in plan.folders:raise GuardError('transfer_selection')
        bridge,fresh = self._binding(plan)
        target = shlex.quote(fresh['root']+'/'+name)
        text = self._run(bridge,f'if [ -e {target} ] || [ -L {target} ]; then printf QSC_EXISTS; else printf QSC_EMPTY; fi')
        if text not in ('QSC_EXISTS','QSC_EMPTY'):raise GuardError('transfer_protocol')
        return text=='QSC_EXISTS'

    def _move(self, plan, stage, source, target):
        src,dst = shlex.quote(source),shlex.quote(target)
        command = (f'[ ! -e {dst} ] && [ ! -L {dst} ] && mv -nT {src} {dst} && '
            f'[ ! -e {src} ] && [ ! -L {src} ] && [ -d {dst} ] && [ ! -L {dst} ] && printf QSC_MOVED')
        if self._command(plan,stage,command)!='QSC_MOVED':raise GuardError('transfer_publish')

    def copy_verified(self, plan, folder):
        name = folder_name(folder)
        if name not in plan.folders:raise GuardError('transfer_selection')
        bridge,fresh = self._binding(plan)
        # Conservative capability check BEFORE any remote mutation/backup.
        help_text = self._run(bridge,'mv --help')
        if '-T' not in help_text or '-n' not in help_text:
            raise GuardError('transfer_mv_capability')
        source = Path(plan.local)/name
        if not source.is_dir() or source.is_symlink() or source.is_junction() or source.resolve().parent!=Path(plan.local):
            raise GuardError('transfer_source')
        for path in source.rglob('*'):
            if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(source.resolve()):
                raise GuardError('transfer_source_link')
        if not any((source/info).is_file() for info in INFO_FILE_NAMES):raise GuardError('transfer_missing_info')
        transaction = uuid.uuid4().hex
        stage = fresh['root']+'/.quest-transfer-'+transaction
        target = fresh['root']+'/'+name
        self.journal_dir.mkdir(parents=True,exist_ok=True)
        journal = self.journal_dir/(transaction+'.json')
        record = dict(transaction=transaction,device=dict(plan.device),folder=name,stage=stage,
            target=target,backup=stage+'/previous',replace_existing=self.replace_existing,state='prepared')
        def save(state):
            record['state']=state
            temporary=journal.with_suffix('.tmp')
            temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(temporary,journal)
        save('prepared')
        # A private ASCII snapshot avoids legacy storage.data_dir()/push_staging.
        with tempfile.TemporaryDirectory(prefix='quest-push-') as directory:
            temporary = Path(directory)
            if not str(temporary).isascii():raise GuardError('transfer_ascii_staging')
            guard_copy(Path(plan.local),temporary,name)
            snapshot=temporary/'payload';shutil.copytree(source,snapshot,symlinks=True)
            for path in (snapshot,*snapshot.rglob('*')):
                if path.is_symlink() or path.is_junction():raise GuardError('transfer_source_link')
            try:
                bridge,fresh=self._binding(plan)
                root=shlex.quote(fresh['root']);quoted=shlex.quote(stage)
                save('stage_intent')
                created=self._run(bridge,f'[ ! -L {root} ] && [ "$(readlink -f {root})" = {root} ] && mkdir {quoted} && printf QSC_CREATED')
                if created!='QSC_CREATED':raise GuardError('transfer_staging')
                save('staged')
                fs=AdbFS(Location(kind='adb',root=stage,serial=bridge.serial),
                    bridge=StagingBridge(self,plan,stage,bridge))
                fs.copy_into('payload',snapshot)
                save('verified')
                if self.exists(plan,name):
                    if not self.replace_existing:raise GuardError('transfer_target_exists')
                    bridge,_fresh=self._binding(plan)
                    quoted_target=shlex.quote(target)
                    checked=self._run(bridge,f'[ -d {quoted_target} ] && [ ! -L {quoted_target} ] && '
                        f'[ "$(readlink -f {quoted_target})" = {quoted_target} ] && printf QSC_DIRECTORY')
                    if checked!='QSC_DIRECTORY':raise GuardError('transfer_target_type')
                    save('backup_intent')
                    self._move(plan,stage,target,stage+'/previous')
                    record['backup_created']=True
                    save('backed_up')
                save('publish_intent')
                self._move(plan,stage,stage+'/payload',target)
                bridge,_fresh=self._binding(plan)
                final_fs=AdbFS(Location(kind='adb',root=fresh['root'],serial=bridge.serial),bridge=bridge)
                final_fs.verify_copy(name,snapshot)
                save('committed')
                if not record.get('backup_created'):
                    try:
                        cleaned=self._command(plan,stage,f'rmdir {shlex.quote(stage)} && printf QSC_CLEAN')
                        record['cleanup']='removed' if cleaned=='QSC_CLEAN' else 'pending'
                    except Exception:
                        record['cleanup']='pending'
                    save('committed')
                # Backup-containing stages and failed transactions are retained.
                # rmdir above can remove only this transaction's empty directory.
                return True
            except Exception:
                # Last acknowledged/intent state remains usable after lost replies.
                record['recovery_required']=True
                try:save(record['state'])
                except OSError:pass
                raise
