"""Windows Shell 命名空间桥（MTP 便携设备浏览）。

**这是"Quest 插入后文件夹选不了"的直接解药。**

原理：MTP 设备没有盘符，但它挂在 Windows Shell 命名空间里（"此电脑" 之下）。
`tkinter.filedialog.askdirectory` 走的是 IFileDialog，只能返回**文件系统路径**，
对命名空间内的 MTP 目录，它拿不到结果 —— 表现就是"能点进去、但确定后没反应/选不中"。
这里改为用 Shell.Application COM 自己枚举命名空间，把目录树读出来给自绘对话框用。

实现细节（两个坑，都已处理）：
1. 机器上可能没有 pywin32 —— 于是通过调用 PowerShell 的 COM 来完成，零第三方依赖；
2. PowerShell 5.1 读取"无 BOM 的 UTF-8"脚本会按 GBK 解码，中文参数会乱码 ——
   所以 shell_probe.ps1 以 **UTF-8 with BOM** 保存，并且在 Python 里也做兜底。

注意：命名空间路径（`::{GUID}\\...` 或 `此电脑\\Quest 3\\...`）**不能**给 `os`/`shutil` 用，
真正的文件复制/读取请走 adb（见 device_adb.py）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .logging_setup import get_logger

logger = get_logger("shell")

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
THIS_PC = "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"  # ssfDRIVES
TIMEOUT = 45.0

# 便携设备（MTP）在 Shell 里通常呈现的类型名
PORTABLE_TYPE_HINTS = ("便携", "Portable", "可攜", "MTP", "媒体播放", "Media player", "手机", "Phone")


@dataclass
class ShellItem:
    """Shell 命名空间中的一个项。"""

    name: str
    shell_path: str
    is_folder: bool
    type: str = ""
    size: str = ""
    modified: str = ""

    @property
    def is_file_system(self) -> bool:
        """是否是真实文件系统路径（有盘符 / UNC），可直接给 os 用。"""
        if not self.shell_path or self.shell_path.startswith("::"):
            return False
        return os.path.isabs(self.shell_path) or self.shell_path.startswith("\\\\")


def _powershell() -> str | None:
    """找一个可用的 PowerShell（优先 pwsh 7，其次 Windows PowerShell）。"""
    for exe in ("pwsh", "powershell"):
        found = shutil.which(exe)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


class ShellBridge:
    """调用 shell_probe.ps1 枚举命名空间。带缓存与串行锁（COM 不是线程安全的）。"""

    def __init__(self, script: Path | None = None) -> None:
        self.script = script or Path(__file__).with_name("shell_probe.ps1")
        self._ps = _powershell()
        self._lock = threading.Lock()
        self._cache: dict[str, list[ShellItem]] = {}
        self.last_error = ""

    # ---------------- 可用性 ----------------
    @property
    def available(self) -> bool:
        return bool(self._ps) and self.script.exists() and IS_WINDOWS

    # ---------------- 枚举 ----------------
    def list_items(self, namespace_path: str = THIS_PC, use_cache: bool = True) -> list[ShellItem]:
        """列出命名空间目录内容；失败抛 RuntimeError（调用方转成用户提示）。"""
        if not IS_WINDOWS:
            raise RuntimeError("Shell 命名空间浏览仅在 Windows 上可用")
        if not self.available:
            raise RuntimeError("找不到 PowerShell，无法浏览便携设备")
        if use_cache and namespace_path in self._cache:
            return self._cache[namespace_path]

        with self._lock:
            out = self._run_probe(namespace_path)
        items = self._parse(out)
        if use_cache:
            self._cache[namespace_path] = items
        return items

    def _run_probe(self, namespace_path: str) -> str:
        cmd = [
            str(self._ps),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script),
            "-Path",
            namespace_path or THIS_PC,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=TIMEOUT, creationflags=CREATE_NO_WINDOW
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("枚举设备目录超时（设备可能已断开）") from exc
        except OSError as exc:
            raise RuntimeError(f"无法启动 PowerShell: {exc}") from exc

        raw = proc.stdout.decode("utf-8-sig", "replace").strip()
        if not raw:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"Shell 枚举没有输出: {err[:200]}")
        return raw

    def _parse(self, raw: str) -> list[ShellItem]:
        # PowerShell 在管道里可能输出多行，取最后一行有效 JSON
        payload = raw.splitlines()[-1] if "\n" in raw else raw
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"解析 Shell 输出失败: {exc}") from exc
        if not data.get("ok"):
            self.last_error = str(data.get("error", "未知错误"))
            raise RuntimeError(self.last_error)
        items = []
        for entry in data.get("items", []):
            items.append(
                ShellItem(
                    name=str(entry.get("name", "")),
                    shell_path=str(entry.get("shell_path", "")),
                    is_folder=bool(entry.get("is_folder")),
                    type=str(entry.get("type", "")),
                    size=str(entry.get("size", "")),
                    modified=str(entry.get("modified", "")),
                )
            )
        return items

    # ---------------- 高层用法 ----------------
    def list_this_pc(self) -> list[ShellItem]:
        return self.list_items(THIS_PC, use_cache=False)

    def portable_devices(self) -> list[ShellItem]:
        """找出所有便携设备（MTP 手机/头显）。

        判定依据：不是文件系统路径（没有盘符）且不是网络位置 —— 这类项就是 MTP 设备。
        另外用类型名再兜一层，避免把"控制面板"之类的虚拟项算进来。
        """
        devices: list[ShellItem] = []
        for item in self.list_this_pc():
            if item.is_file_system:
                continue
            if not item.shell_path.startswith("::"):
                continue  # 虚拟文件夹（此电脑下的快捷入口）也以 :: 开头，但类型不同
            if any(hint.lower() in item.type.lower() for hint in PORTABLE_TYPE_HINTS) or item.type == "":
                devices.append(item)
        return devices

    def find_path(self, segments: Iterable[str], start: str = THIS_PC) -> ShellItem | None:
        """按名字层级查找，例如 ["Quest 3", "内部共享存储空间", "Android", ...]。"""
        current_path = start
        current: ShellItem | None = None
        for segment in segments:
            found = None
            for child in self.list_items(current_path):
                if child.name.lower() == segment.lower():
                    found = child
                    break
            if found is None:
                return None
            current = found
            current_path = found.shell_path
        return current

    def clear_cache(self) -> None:
        self._cache.clear()


# --------------------------------------------------------------------------------------
# 便捷函数
# --------------------------------------------------------------------------------------
def shell_available() -> bool:
    return ShellBridge().available
