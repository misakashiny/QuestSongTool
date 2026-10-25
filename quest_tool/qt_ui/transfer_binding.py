"""Fresh hardware/root binding shared by explicitly enabled transfer adapters."""
import json
import os
import threading
import time

from ..workers import GuardError
from .device_protocol import descriptor


def adb_binding(provider, plan, direction):
    plan.validate()
    if plan.direction != direction or os.name != 'nt':
        raise GuardError('transfer_direction')
    selected = dict(plan.device)
    try:
        identity = json.loads(selected['device_id'])
        if not isinstance(identity,list) or len(identity)!=2:
            raise ValueError('identity')
        serial, hardware = identity
    except (TypeError, ValueError):
        raise GuardError('transfer_identity') from None
    provider._serial(serial)
    provider._serial(hardware)
    cancel = threading.Event()
    deadline = time.monotonic()+120
    if not any(item.serial==serial and item.usable for item in provider._devices(cancel,deadline)):
        raise GuardError('transfer_disconnected')
    bridge, item, fresh_hardware = provider._identify(serial,cancel,deadline)
    root = provider._root(bridge,selected['root'],cancel,deadline)
    if not root:
        raise GuardError('transfer_device_root')
    fresh = descriptor(provider._descriptor(item,fresh_hardware,root))
    if any(fresh[key]!=value for key,value in plan.device):
        raise GuardError('transfer_identity')
    return bridge, fresh
