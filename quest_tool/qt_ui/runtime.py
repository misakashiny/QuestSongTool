"""Isolated preview data and logging, never use storage.data_dir()."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths

from ..messages import tr


class PreviewRuntime:
    def __init__(self, base: Path | None = None):
        # Always append a dedicated leaf, including for an explicit CLI path.
        parent = base if base is not None else Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation))
        self.path = (parent / "QuestSongCompare-QtPreview").resolve()
        if any((self.path / name).exists() for name in
               ("settings.json", "songs_cache.db", "downloads.json")):
            raise ValueError(tr("qt.data_conflict"))
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(self.path / "preview.lock"))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            raise RuntimeError(tr("qt.already_running"))
        self.handler = None
        self.logger = logging.getLogger("quest_tool.qt_preview")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        try:
            self.handler = RotatingFileHandler(self.path / "preview.log",
                                               maxBytes=1024 * 1024, backupCount=2,
                                               encoding="utf-8")
            self.handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.logger.addHandler(self.handler)
        except Exception:
            self.lock.unlock()
            raise

    def close(self):
        if self.handler is not None:
            self.logger.removeHandler(self.handler)
            self.handler.close()
            self.handler = None
        self.lock.unlock()
