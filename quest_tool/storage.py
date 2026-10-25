"""运行目录解析与配置持久化。

原版的问题：
* 数据库 `songs_cache.db`、`settings.json`、`logs/` 都写死在**当前工作目录**，
  双击 bat / 计划任务启动时 cwd 可能是 C:\\Windows\\System32，导致文件散落各处甚至无权限。
* 设置文件用 `json.load` 无校验，损坏后整个程序行为异常。
* 写入不是原子的，断电/崩溃后 settings.json 变空文件。

这里统一改成"程序所在目录（或用户数据目录）"，并使用临时文件 + 原子替换写入。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 注意：这里不能用 quest_tool.logging_setup.get_logger —— logging_setup 需要
# 先导入本模块取日志目录，直接互相导入会形成循环导入。用标准 logger 即可
# （root 配置由 logging_setup.setup_logging() 完成）。
logger = logging.getLogger("quest_tool.storage")

#: 试听时长可选档位（秒），0 = 整曲。
#:
#: **必须与 `audio.PREVIEW_LENGTH_CHOICES` 一致**，但故意各写一份：
#: 本模块是最底层（`logging_setup` 要从这里取 `logs_dir`），
#: 导入 `audio` 会成环（见 `Settings.load()` 里的注释）。
#: 两边一致性由 `tests/smoke_features.py` 断言守住。
PREVIEW_SECONDS_CHOICES: tuple[int, ...] = (30, 60, 120, 0)

APP_DIR_NAME = "QuestSongComparator"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 等打包环境。"""
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """配置文件/日志/数据库所在的根目录。

    * 打包后：可执行文件所在目录；
    * 源码运行：项目根目录（本文件的上一级）。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def data_dir() -> Path:
    """可写目录：优先程序目录，不可写时退回用户数据目录。"""
    candidate = app_dir()
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return candidate
    except OSError:
        fallback = _user_data_dir()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def settings_path() -> Path:
    return data_dir() / "settings.json"


def database_path() -> Path:
    return data_dir() / "songs_cache.db"


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> bool:
    """原子写文本：先写同目录临时文件，再 os.replace 覆盖。

    不用 `tempfile.mkstemp`（某些受限环境/沙箱下会阻塞或被拒），
    直接用固定后缀的临时文件，失败时退回普通写入，保证设置仍能保存。
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        try:
            with open(path, "w", encoding=encoding, newline="\n") as fh:
                fh.write(text)
            return True
        except OSError:
            return False


@dataclass
class Settings:
    """所有可持久化的用户设置（带默认值与类型收敛）。"""

    local_path: str = ""
    quest_path: str = ""
    quest_source: str = "auto"  # auto | drive | adb | mtp
    appearance: str = "System"  # System | Light | Dark
    adb_path: str = ""
    #: 上次无线连接的地址（`host:port`）。无线设备的"序列号"就是这个地址，
    #: 用它可以跳过"多个设备该连哪个"的选择（插着线又连着无线时很有用）。
    adb_serial: str = ""
    verify_before_delete: bool = True
    copy_overwrite: bool = True
    device_poll_on_start: bool = True
    recent_local: list[str] = field(default_factory=list)
    recent_quest: list[str] = field(default_factory=list)
    window_geometry: str = ""
    #: 三个列表的列宽 {"local": {...}, "quest": {...}, "missing": {...}}
    column_widths: dict[str, dict[str, int]] = field(default_factory=dict)
    #: 封面墙 / 列表视图模式（"list" | "grid"）
    view_mode: str = "list"
    #: 首次使用向导是否已经看过（看过之后不再自动弹；仍可从「帮助」里手动打开）
    wizard_done: bool = False
    #: 详情栏宽度（用户拖动左右分割器后记住；0 = 用默认值，见 B5）
    detail_width: int = 0
    #: 界面缩放档位（C1）："auto" = 按系统 DPI 自动；也可写死 "1.0" / "1.25" / "1.5" / "2.0"
    ui_scale: str = "auto"
    #: 界面语言（C5）："zh" / "en"
    language: str = "zh"
    #: 试听时长（秒）。30 / 60 / 120，或 0 = 整曲。
    #: 用 int 而不是 float：这是一组**离散档位**而不是连续量，
    #: 读取时会被 `audio.normalize_preview_seconds()` 收敛到合法档位。
    preview_seconds: int = 30
    download_columns: int = 3
    download_rows: int = 7
    #: 实测传输速率 {位置类型: MB/s}，用来把"预计耗时"算准。
    #: 每复制一批就用实测值更新一次（指数滑动平均），所以会越用越准。
    #: v14.7 之前是写死的 4 MB/s —— 真机实测 adb 有 17.4 MB/s，估计值偏悲观 4 倍多。
    transfer_rates: dict[str, float] = field(default_factory=dict)

    MAX_RECENT = 8

    # ---------- 读取 ----------
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 损坏的设置文件：备份后使用默认值，绝不因此崩溃
            try:
                path.replace(path.with_suffix(".json.bak"))
            except OSError:
                pass
            return cls()
        if not isinstance(raw, dict):
            return cls()

        kwargs: dict[str, Any] = {}
        for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
            if f.name not in raw:
                continue
            value = raw[f.name]
            if f.type in ("str", str) and not isinstance(value, str):
                value = str(value)
            if f.name in ("verify_before_delete", "copy_overwrite", "device_poll_on_start",
                          "wizard_done"):
                value = bool(value)
            if f.name == "detail_width":
                # 洗掉离谱的宽度（手改坏了会让窗口一开就是怪比例）
                try:
                    value = max(0, min(2000, int(value)))
                except (TypeError, ValueError):
                    continue
            if f.name in ("recent_local", "recent_quest"):
                value = [str(v) for v in value if isinstance(v, (str, int))][: cls.MAX_RECENT]
            if f.name == "column_widths" and isinstance(value, dict):
                cleaned: dict[str, dict[str, int]] = {}
                for key, widths in value.items():
                    if not isinstance(widths, dict):
                        continue
                    try:
                        cleaned[str(key)] = {
                            str(k): int(v) for k, v in widths.items()
                            if isinstance(v, (int, float, str)) and 40 <= int(v) <= 2000
                        }
                    except (TypeError, ValueError):
                        continue
                value = cleaned
            if f.name == "transfer_rates" and isinstance(value, dict):
                cleaned_rates: dict[str, float] = {}
                for key, rate in value.items():
                    try:
                        rate_f = float(rate)
                    except (TypeError, ValueError):
                        continue
                    # MB/s 的合理区间：挡住手改坏的值（0 或 1e9 都会让预估变成笑话）
                    if 0.05 <= rate_f <= 5000:
                        cleaned_rates[str(key)] = rate_f
                value = cleaned_rates
            kwargs[f.name] = value
        settings = cls(**kwargs)
        if settings.appearance not in ("System", "Light", "Dark"):
            settings.appearance = "System"
        if settings.quest_source not in ("auto", "drive", "adb", "mtp"):
            settings.quest_source = "auto"
        if settings.view_mode not in ("list", "grid"):
            settings.view_mode = "list"
        if settings.ui_scale not in ("auto", "1.0", "1.25", "1.5", "2.0"):
            settings.ui_scale = "auto"
        if settings.language not in ("zh", "en"):
            settings.language = "zh"
        # 试听时长只认 4 个档位。**必须在读取时收敛**：手改 settings.json
        # 写成 -5 会让 `stream_preview(seconds=-5)` 走成"不限长"（语义反了），
        # 写成 1e9 又像是程序卡住了。
        #
        # **这里刻意不调 `audio.normalize_preview_seconds()`**：
        # `storage` 是最底层的模块（`logging_setup` 要从它取 `logs_dir`），
        # 而 `audio` → `logging_setup` → `storage`，一导入就成环，
        # 实测报 `ImportError: cannot import name 'logs_dir' from partially
        # initialized module 'quest_tool.storage'`。
        # 档位表很小且不会变，就地写一份；`audio` 里那份负责校验，
        # 两边的一致性由 `tests/smoke_features.py` 断言守住。
        try:
            preview_seconds = int(settings.preview_seconds)
        except (TypeError, ValueError):
            preview_seconds = 30
        settings.preview_seconds = (
            preview_seconds if preview_seconds in PREVIEW_SECONDS_CHOICES else 30
        )
        for attr, default, maximum in (("download_columns", 3, 6), ("download_rows", 7, 15)):
            value = getattr(settings, attr)
            setattr(settings, attr, value if type(value) is int and 1 <= value <= maximum else default)
        return settings

    # ---------- 保存 ----------
    def save(self, path: Path | None = None) -> bool:
        path = path or settings_path()
        payload = json.dumps(asdict(self), ensure_ascii=False, indent=2)
        ok = atomic_write_text(path, payload)
        if not ok:
            logger.warning("设置保存失败: %s", path)
        return ok

    # ---------- 辅助 ----------
    def remember(self, kind: str, path: str) -> None:
        """记录最近使用路径（去重、限量、最新在前）。"""
        if not path:
            return
        attr = "recent_local" if kind == "local" else "recent_quest"
        items: list[str] = list(getattr(self, attr))
        if path in items:
            items.remove(path)
        items.insert(0, path)
        setattr(self, attr, items[: self.MAX_RECENT])
