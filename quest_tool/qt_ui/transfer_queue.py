"""Transfer orchestration with an explicitly injected backend and no GUI I/O.

Backends own device/copy/backup/verification guards; the queue has no default
provider, never requests overwrite, and reports only generation-bound messages.
"""
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import threading
from typing import Protocol

from ..constants import BACKUP_SUFFIX
from ..workers import GuardError, PauseGate, TaskRunner, Token
from .adb_metadata import READ_ROOTS
from .device_protocol import descriptor


def folder_name(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or value.startswith('.') or value.endswith(('.', BACKUP_SUFFIX))
            or any(ord(c) < 32 or c in '/\\:<>"|?*' for c in value)
            or PureWindowsPath(value).is_reserved()):
        raise GuardError('transfer_folder')
    return value


def local_root(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise GuardError('transfer_local_root')
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise GuardError('transfer_local_root')
    return str(resolved)


@dataclass(frozen=True)
class TransferPlan:
    direction: str
    local: str
    device: tuple
    folders: tuple[str, ...]

    @classmethod
    def create(cls, direction, local, device, folders):
        if direction not in ('to_quest', 'to_local'):
            raise GuardError('transfer_direction')
        selected = descriptor(device)
        if selected['kind'] != 'adb' or selected['root'] not in READ_ROOTS:
            raise GuardError('transfer_device_root')
        names = tuple(folder_name(name) for name in folders)
        if not names or len(names) > 100000 or len(set(n.casefold() for n in names)) != len(names):
            raise GuardError('transfer_selection')
        # Immutable hardware/root binding; labels are deliberately excluded.
        fields = ('identity', 'kind', 'device_id', 'root', 'model', 'codename', 'brand')
        return cls(direction, local_root(local), tuple((key, selected[key]) for key in fields), names)

    def validate(self):
        selected = dict(self.device)
        selected.update(label='Quest', state='device', identity_verified=True)
        rebuilt = self.create(self.direction, self.local, selected, self.folders)
        if rebuilt != self:
            raise GuardError('transfer_plan')


class TransferBackend(Protocol):
    """Future production adapter must reuse SongFS, backup and verify guards.

    revalidate must freshly verify connection, hardware AND the selected root.
    copy_verified returns True only after verification; it cannot touch GUI.
    Cancellation/pause take effect BETWEEN songs, never midway through a copy.
    """
    def revalidate(self, plan: TransferPlan) -> dict: ...
    def exists(self, plan: TransferPlan, folder: str) -> bool: ...
    def copy_verified(self, plan: TransferPlan, folder: str) -> bool: ...


class TransferQueue:
    def __init__(self, backend: TransferBackend):
        if backend is None:
            raise GuardError('transfer_backend_required')
        self.backend = backend
        self.tasks = TaskRunner(max_workers=1)
        self.token = None
        self.gate = PauseGate()
        self.generation = 0
        self.closed = False
        self._lock = threading.Lock()

    def start(self, plan):
        with self._lock:
            if self.closed or self.tasks.busy():
                raise GuardError('transfer_busy')
            plan.validate()
            self.generation += 1
            generation = self.generation
            self.token = Token(f'transfer-{generation}')
            token = self.token
            self.gate.resume()

            def worker(_task_token):
                ok = skipped = 0
                failures = []
                fatal = ''
                cancelled = False
                try:
                    for index, folder in enumerate(plan.folders):
                        if not self.gate.wait(token):
                            cancelled = True
                            break
                        # Recheck local root and hardware/root BEFORE even an exists probe.
                        plan.validate()
                        fresh = descriptor(self.backend.revalidate(plan))
                        if any(fresh[key] != value for key, value in plan.device):
                            raise GuardError('transfer_identity')
                        if not token.alive():
                            cancelled = True
                            break
                        self.tasks.emit('transfer', payload=dict(generation=generation, state='running',
                            folder=folder, completed=index, total=len(plan.folders)))
                        try:
                            exists = self.backend.exists(plan, folder)
                            if type(exists) is not bool:
                                raise GuardError('transfer_backend_result')
                            if not token.alive():
                                cancelled = True
                                break
                            if exists:
                                skipped += 1  # M7B1 never requests overwrite.
                            elif self.backend.copy_verified(plan, folder) is True:
                                ok += 1
                            else:
                                failures.append((folder, 'transfer_verification'))
                        except GuardError:
                            raise
                        except Exception as exc:
                            failures.append((folder, str(exc)))
                        self.tasks.emit('transfer', payload=dict(generation=generation, state='progress',
                            completed=index+1, total=len(plan.folders), ok=ok, skipped=skipped,
                            failures=tuple(failures)))
                    cancelled = cancelled or not token.alive()
                except Exception as exc:
                    fatal = str(exc)
                finally:
                    self.gate.resume()
                    self.tasks.emit('transfer', payload=dict(generation=generation,
                        state='blocked' if fatal else 'cancelled' if cancelled else 'done',
                        ok=ok, skipped=skipped, failures=tuple(failures), error=fatal,
                        total=len(plan.folders)))

            self.tasks.submit(worker, name='qt_transfer')
            return generation

    def toggle_pause(self):
        if self.closed or self.token is None or not self.token.alive() or not self.tasks.busy():
            return False
        return self.gate.toggle()

    def cancel(self):
        if self.token is not None:
            self.token.cancel()
        self.gate.resume()

    def close(self):
        self.closed = True
        self.cancel()

    def wait_closed(self):
        for thread in tuple(self.tasks._threads):
            thread.join()
