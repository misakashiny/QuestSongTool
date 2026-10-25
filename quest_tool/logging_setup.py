"""日志初始化（懒加载，避免 import 副作用）。

原版 `setup_logging()` 在模块导入时就执行 `os.makedirs("logs")` 并配置 root logger，
在打包/非预期工作目录下会失败或把日志写到奇怪的位置；重复调用还会叠加 handler。
这里改成显式调用一次，并且带轮转。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .storage import logs_dir

_CONFIGURED = False
LOGGER_NAME = "quest_tool"


def setup_logging(level: int = logging.INFO, console: bool = True) -> logging.Logger:
    """配置并返回应用 logger；重复调用不会重复添加 handler。"""
    global _CONFIGURED
    logger = logging.getLogger(LOGGER_NAME)
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(threadName)s - %(name)s - %(message)s"
    )

    try:
        log_file: Path = logs_dir() / "quest_comparator.log"
        file_handler = RotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError as exc:  # 磁盘只读等情况下降级为仅控制台
        print(f"[日志] 无法创建日志文件: {exc}", file=sys.stderr)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        logger.addHandler(stream)

    _CONFIGURED = True
    return logger


def get_logger(module: str | None = None) -> logging.Logger:
    """获取子 logger，未初始化时自动初始化。"""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(f"{LOGGER_NAME}.{module}" if module else LOGGER_NAME)
