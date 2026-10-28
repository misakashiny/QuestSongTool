"""Atomic local recovery records for explicit file operations."""
import json
from pathlib import Path
import re

from PySide6.QtCore import QIODevice, QSaveFile

from ..downloads import DownloadError


def save_operation(directory, transaction, record):
    directory = Path(directory)
    if not re.fullmatch('[0-9a-f]{32}',transaction):raise DownloadError('target')
    path = directory/(transaction+'.json')
    if not directory.is_absolute() or any(p.is_symlink() or p.is_junction() for p in (path,directory,*directory.parents)):
        raise DownloadError('target')
    directory.mkdir(parents=True,exist_ok=True)
    raw = json.dumps(record,ensure_ascii=False,indent=2).encode('utf-8')
    file = QSaveFile(str(path))
    if not file.open(QIODevice.OpenModeFlag.WriteOnly):raise OSError('operation record open')
    if file.write(raw) != len(raw):
        file.cancelWriting();raise OSError('operation record write')
    if not file.commit():raise OSError('operation record commit')
