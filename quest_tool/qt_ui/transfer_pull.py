"""Explicit Quest -> Windows import; remote operations are read-only.

Reuse AdbFS.copy_out inside a new private local staging directory. Never pass
the user's final destination to its replacing copy_out implementation.
"""
import os
from pathlib import Path
import shlex
import shutil
import tempfile
import threading
import time

from ..constants import INFO_FILE_NAMES
from ..device_adb import AdbFS
from ..fsapi import Location
from ..workers import GuardError
from .adb_provider import AdbReadOnlyProvider
from .transfer_binding import adb_binding
from .transfer_queue import folder_name


class AdbPullBackend:
    def __init__(self, provider: AdbReadOnlyProvider):
        self.provider = provider

    def _binding(self, plan):
        return adb_binding(self.provider,plan,'to_local')

    def revalidate(self, plan):
        return self._binding(plan)[1]

    def exists(self, plan, folder):
        if folder_name(folder) not in plan.folders:raise GuardError('transfer_selection')
        self._binding(plan)
        return os.path.lexists(Path(plan.local)/folder_name(folder))

    def copy_verified(self, plan, folder):
        name = folder_name(folder)
        if name not in plan.folders:raise GuardError('transfer_selection')
        bridge, fresh = self._binding(plan)
        destination = Path(plan.local)/name
        if os.path.lexists(destination):
            raise GuardError('transfer_target_exists')
        remote = fresh['root']+'/'+name
        quoted = shlex.quote(remote)
        command = (f'[ ! -L {quoted} ] && [ -d {quoted} ] && '
            f'[ "$(readlink -f {quoted})" = {quoted} ] && find {quoted} -type l -print')
        links = self.provider._run(bridge,['shell',command],threading.Event(),time.monotonic()+120)
        if links.strip():
            raise GuardError('transfer_source_link')
        fs = AdbFS(Location(kind='adb',root=fresh['root'],serial=bridge.serial),bridge=bridge)
        before = fs.remote_file_stats(name)
        if before[0]<=0:
            raise GuardError('transfer_empty_source')
        # Same-volume staging permits Windows rename without replacing an existing
        # directory. Existing songs/partial directories are never deleted.
        stage = Path(tempfile.mkdtemp(prefix='.quest-pull-',dir=plan.local))
        inode = stage.stat().st_ino
        try:
            payload = stage/'payload'
            fs.copy_out(name,payload)
            for path in (payload,*payload.rglob('*')):
                if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(stage.resolve()):
                    raise GuardError('transfer_local_link')
            if not any((payload/info).is_file() for info in INFO_FILE_NAMES):
                raise GuardError('transfer_missing_info')
            self._binding(plan)
            fs.verify_copy(name,payload)
            if fs.remote_file_stats(name)!=before:
                raise GuardError('transfer_source_changed')
            plan.validate()
            if stage.is_symlink() or stage.is_junction() or stage.stat().st_ino!=inode:
                raise GuardError('transfer_staging')
            if os.path.lexists(destination):
                raise GuardError('transfer_target_exists')
            # Windows os.rename raises FileExistsError if another writer wins.
            # os.replace/shutil.move are deliberately not used for publication.
            os.rename(payload,destination)
            return True
        finally:
            # Do not remove an unrecognised path after root/staging substitution.
            try:
                plan.validate()
                if (stage.parent.resolve()==Path(plan.local) and not stage.is_symlink()
                        and not stage.is_junction() and stage.stat().st_ino==inode):
                    shutil.rmtree(stage)
            except (OSError, GuardError):
                pass
