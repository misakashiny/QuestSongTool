r"""adb_bridge.py —— 通过 Android Platform Tools (adb) 访问 Quest 3。

为什么需要它
------------
Quest 3 用 USB 连上 Windows 后是 **MTP 便携设备**（没有盘符），
``os.listdir`` / ``shutil.copy`` / ``tkinter.filedialog`` 都用不了。
启用「开发者模式」后，Quest 会 **同时** 暴露 adb 接口，于是可以用
``adb push`` / ``adb pull`` 直接读写设备上的文件，速度和稳定性都远好于 MTP。

本模块要求极低：只要系统里能找到 ``adb.exe``，用 ``subprocess`` 调用即可，
**不依赖任何第三方 Python 包**。找不到 adb 时所有函数都会优雅降级
（返回空结果 / 带中文说明的错误），不会抛未捕获异常。

快速使用::

    import adb_bridge as adb
    print(adb.describe_status())          # 人类可读状态
    devs = adb.list_devices()             # [AdbDevice(...)]
    if devs and devs[0].state == "device":
        adb.pull(devs[0].serial, adb.QUEST_CUSTOMLEVELS[0], "./CustomLevels")
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: Quest 上 Beat Saber 自定义歌曲目录的候选路径，**按优先级排列**。
#:
#: 重要：自 Beat Saber 1.35 / SongCore 起，歌曲不再放在
#: ``Android/data/com.beatgames.beatsaber/files/CustomLevels``，而是放在
#: ``ModData`` 下（见 Quest-SongCore 的 include/config.hpp）。
#: ``Android/data`` 那条保留为历史兼容（旧 SongLoader / BMBF 时代）。
QUEST_CUSTOMLEVELS: tuple[str, ...] = (
    "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels",
    "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongLoader/CustomLevels",
    "/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels",
)

#: 自定义歌曲的 WIP（未完成）目录候选
QUEST_CUSTOMWIPLEVELS: tuple[str, ...] = (
    "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomWIPLevels",
    "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongLoader/CustomWIPLevels",
)

#: 歌单目录候选
QUEST_PLAYLISTS: tuple[str, ...] = (
    "/sdcard/ModData/com.beatgames.beatsaber/Mods/PlaylistManager/Playlists",
)

#: Beat Saber 存档 / 配置文件目录（PlayerData.dat 等）
QUEST_SAVEDATA = "/sdcard/Android/data/com.beatgames.beatsaber/files"

#: 游戏包名
QUEST_PACKAGE = "com.beatgames.beatsaber"

#: 在 Windows 上不弹黑框
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_DEFAULT_TIMEOUT = 30.0
_TRANSFER_TIMEOUT = 600.0


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class AdbDevice:
    """一个 adb 设备。"""

    serial: str
    """设备序列号（``adb -s <serial>`` 用）。"""

    state: str = "unknown"
    """``adb devices`` 里的状态：

    * ``device``       —— 已连接且已授权（可用）
    * ``unauthorized`` —— 头显上还没点「允许 USB 调试」
    * ``offline``      —— 连接异常 / adbd 正在重启
    * ``no permissions`` / ``unknown`` —— 其他异常
    """

    details: dict[str, str] = field(default_factory=dict)
    """``adb devices -l`` 给出的附加信息，如 ``model`` / ``product`` / ``device``。"""

    @property
    def usable(self) -> bool:
        return self.state == "device"

    @property
    def description(self) -> str:
        model = self.details.get("model") or self.details.get("device") or ""
        return f"{self.serial} [{self.state}]{' ' + model if model else ''}"

    def __str__(self) -> str:
        return self.description


@dataclass
class AdbResult:
    """一次 adb 调用的结果。"""

    ok: bool
    """命令是否成功（returncode == 0 且没有超时/找不到 adb）。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    """中文错误说明（如果有）。"""

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        if self.ok:
            return self.stdout.strip()
        return self.error or self.stderr.strip() or f"adb 失败 (exit={self.returncode})"


# ---------------------------------------------------------------------------
# adb 定位
# ---------------------------------------------------------------------------

_ADB_CACHE: str | None = None
_ADB_LOOKED_UP = False


def _candidate_adb_paths() -> list[str]:
    """常见的 adb.exe 位置（Windows 为主）。"""
    cands: list[str] = []
    env = os.environ

    # 1) 环境变量显式指定
    for var in ("QUEST_ADB", "ADB", "ANDROID_ADB"):
        v = env.get(var)
        if v:
            cands.append(v)
            cands.append(os.path.join(v, "adb.exe" if os.name == "nt" else "adb"))

    # 2) ANDROID_HOME / ANDROID_SDK_ROOT
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_SDK"):
        root = env.get(var)
        if root:
            cands.append(os.path.join(root, "platform-tools", "adb.exe"))

    if os.name == "nt":
        local = env.get("LOCALAPPDATA", "")
        userprofile = env.get("USERPROFILE", "")
        progfiles = env.get("ProgramFiles", r"C:\Program Files")
        progfiles86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        if local:
            cands.append(os.path.join(local, "Android", "Sdk", "platform-tools", "adb.exe"))
        if userprofile:
            cands.append(os.path.join(userprofile, "AppData", "Local", "Android", "Sdk",
                                      "platform-tools", "adb.exe"))
            # SideQuest 会自带一份 adb
            cands.append(os.path.join(userprofile, "AppData", "Local", "Programs", "SideQuest",
                                      "resources", "app.asar.unpacked", "build", "adb.exe"))
        # Chocolatey / winget / scoop 常见位置
        cands.append(r"C:\platform-tools\adb.exe")
        cands.append(r"C:\adb\adb.exe")
        cands.append(os.path.join(progfiles, "Android", "platform-tools", "adb.exe"))
        cands.append(os.path.join(progfiles86, "Android", "platform-tools", "adb.exe"))
        cands.append(r"C:\ProgramData\chocolatey\bin\adb.exe")
        if userprofile:
            cands.append(os.path.join(userprofile, "scoop", "shims", "adb.exe"))
        # 与本程序放在一起（推荐：随程序打包）
        here = os.path.dirname(os.path.abspath(__file__))
        cands.append(os.path.join(here, "platform-tools", "adb.exe"))
        cands.append(os.path.join(here, "adb.exe"))
        cands.append(os.path.join(os.path.dirname(here), "platform-tools", "adb.exe"))
    return cands


def find_adb(refresh: bool = False) -> str | None:
    """查找可用的 ``adb`` 可执行文件。

    查找顺序：

    1. 环境变量 ``QUEST_ADB`` / ``ADB`` / ``ANDROID_ADB``（文件或目录）
    2. ``ANDROID_HOME`` / ``ANDROID_SDK_ROOT`` / ``ANDROID_SDK`` 下的
       ``platform-tools/adb``
    3. Windows 常见安装位置（Android Studio SDK、SideQuest 自带、
       ``C:\\platform-tools``、Chocolatey、Scoop、winget）
    4. 本模块同目录下的 ``platform-tools\\adb.exe``（推荐随程序打包）
    5. ``PATH``

    Args:
        refresh: 为 True 时忽略缓存重新查找。

    Returns:
        adb 的完整路径；找不到时返回 ``None``。
    """
    global _ADB_CACHE, _ADB_LOOKED_UP
    if _ADB_LOOKED_UP and not refresh:
        return _ADB_CACHE

    found: str | None = None
    for cand in _candidate_adb_paths():
        try:
            if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
                found = cand
                break
        except OSError:
            continue

    if not found:
        which = shutil.which("adb")
        if which:
            found = which

    _ADB_CACHE = found
    _ADB_LOOKED_UP = True
    return found


def adb_available() -> bool:
    """系统里是否能找到 adb。"""
    return find_adb() is not None


def adb_version() -> str:
    """返回 ``adb version`` 的第一行；找不到 adb 时返回中文说明。"""
    exe = find_adb()
    if not exe:
        return "未找到 adb（Android Platform Tools 未安装或不在 PATH 中）"
    try:
        cp = subprocess.run(
            [exe, "version"], capture_output=True, timeout=_DEFAULT_TIMEOUT,
            creationflags=_NO_WINDOW,
        )
        text = (cp.stdout or cp.stderr or b"").decode("utf-8", "replace")
        return text.strip().splitlines()[0] if text.strip() else "adb version 无输出"
    except Exception as exc:  # noqa: BLE001
        return f"adb 执行失败: {exc}"


# ---------------------------------------------------------------------------
# 低层调用
# ---------------------------------------------------------------------------


def run_adb(
    args: Sequence[str],
    serial: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    binary: bool = False,
) -> AdbResult:
    """执行一条 adb 命令。

    Args:
        args: adb 的参数列表，例如 ``["shell", "ls", "/sdcard"]``。
        serial: 指定设备序列号；为 None 时不加 ``-s``。
        timeout: 超时秒数。
        binary: 为 True 时不解码输出（仅用于 ``exec-out`` 等二进制场景，
            此时 ``stdout`` 字段为空字符串，请改用 :func:`exec_out`）。

    Returns:
        :class:`AdbResult`。**任何情况下都不会抛异常**。
    """
    exe = find_adb()
    if not exe:
        return AdbResult(
            ok=False,
            returncode=-1,
            error=(
                "未找到 adb。请安装 Android Platform Tools 并把 adb.exe 加入 PATH，"
                "或把 platform-tools 目录放到程序旁边；"
                "也可以设置环境变量 QUEST_ADB 指向 adb.exe。"
            ),
        )
    cmd = [exe] + (["-s", serial] if serial else []) + list(args)
    try:
        cp = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW
        )
    except subprocess.TimeoutExpired:
        return AdbResult(
            ok=False, returncode=-1,
            error=f"adb 命令超时（{timeout:.0f}s）: {' '.join(cmd[1:])}",
        )
    except FileNotFoundError:
        return AdbResult(ok=False, returncode=-1, error=f"adb 可执行文件不存在: {exe}")
    except OSError as exc:
        return AdbResult(ok=False, returncode=-1, error=f"adb 无法启动: {exc}")

    def dec(b: bytes | None) -> str:
        if not b:
            return ""
        return b.decode("utf-8", "replace")

    out = dec(cp.stdout)
    err = dec(cp.stderr)
    ok = cp.returncode == 0
    error = ""
    if not ok:
        error = err.strip() or out.strip() or f"adb 返回 {cp.returncode}"
    return AdbResult(ok=ok, returncode=cp.returncode, stdout=out, stderr=err, error=error)


def exec_out(serial: str | None, shell_cmd: str, timeout: float = _TRANSFER_TIMEOUT) -> bytes:
    """用 ``adb exec-out`` 取原始二进制输出（不做 CRLF 转换）。

    ``adb shell`` 会分配 pty，二进制内容会被破坏；``exec-out`` 不会。
    """
    exe = find_adb()
    if not exe:
        return b""
    cmd = [exe] + (["-s", serial] if serial else []) + ["exec-out", shell_cmd]
    try:
        cp = subprocess.run(cmd, capture_output=True, timeout=timeout,
                            creationflags=_NO_WINDOW)
        return cp.stdout or b""
    except Exception:  # noqa: BLE001
        return b""


# ---------------------------------------------------------------------------
# 设备
# ---------------------------------------------------------------------------


def start_server() -> AdbResult:
    """启动 adb 后台服务（``adb start-server``）。"""
    return run_adb(["start-server"], timeout=60.0)


def kill_server() -> AdbResult:
    """关闭 adb 后台服务（``adb kill-server``）。连接异常时可用来复位。"""
    return run_adb(["kill-server"], timeout=30.0)


def list_devices() -> list[AdbDevice]:
    """``adb devices -l`` 解析结果。

    Returns:
        :class:`AdbDevice` 列表；找不到 adb 时返回空列表。
    """
    if not adb_available():
        return []
    res = run_adb(["devices", "-l"])
    if not res.ok:
        return []
    devices: list[AdbDevice] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        details: dict[str, str] = {}
        for token in parts[2:]:
            if ":" in token:
                k, _, v = token.partition(":")
                details[k] = v
        devices.append(AdbDevice(serial=serial, state=state, details=details))
    return devices


def quest_device() -> AdbDevice | None:
    """挑一个「看起来像 Quest 且可用」的设备。"""
    devs = list_devices()
    usable = [d for d in devs if d.usable]
    for d in usable:
        model = (d.details.get("model", "") + d.details.get("device", "")).lower()
        if "quest" in model or "oculus" in model or "monterey" in model or "hollywood" in model:
            return d
    return usable[0] if usable else None


def describe_status() -> str:
    """一句中文状态说明，适合直接显示在界面上。"""
    exe = find_adb()
    if not exe:
        return (
            "未检测到 adb。" + os.linesep
            + "如需使用 adb 方式（推荐），请：① 在头显上开启开发者模式；"
            "② 安装 Android Platform Tools；③ 把 adb.exe 加入 PATH。"
        )
    devs = list_devices()
    if not devs:
        return (
            f"adb 已就绪（{exe}），但未检测到设备。" + os.linesep
            + "请用 USB 连接 Quest、戴上头显点「允许 USB 调试」，然后重试。"
        )
    lines = [f"adb 已就绪（{exe}），检测到 {len(devs)} 个设备："]
    for d in devs:
        hint = ""
        if d.state == "unauthorized":
            hint = "  <- 请戴上头显，在「允许 USB 调试」弹窗中点「一律允许」"
        elif d.state == "offline":
            hint = "  <- 连接异常，可尝试拔插 USB 或执行 adb kill-server"
        lines.append(f"  {d.description}{hint}")
    return os.linesep.join(lines)


# ---------------------------------------------------------------------------
# 目录 / 文件操作
# ---------------------------------------------------------------------------


def exists(serial: str | None, remote_path: str) -> bool:
    """远程路径是否存在（只用 ``ls``，兼容性最好）。"""
    if not remote_path:
        return False
    res = run_adb(["shell", "ls", "-d", _q(remote_path)], serial=serial)
    return res.ok


def is_dir(serial: str | None, remote_path: str) -> bool:
    """远程路径是否是目录。"""
    res = run_adb(["shell", "ls", "-ld", _q(remote_path)], serial=serial)
    if not res.ok:
        return False
    first = res.stdout.strip().splitlines()[0] if res.stdout.strip() else ""
    return first.startswith("d")


def list_dir(serial: str | None, remote_path: str) -> list[str]:
    """列出远程目录下的名字（``adb shell ls -1``）。

    Returns:
        名字列表（不含路径）；失败时返回空列表。
    """
    res = run_adb(["shell", "ls", "-1", _q(remote_path)], serial=serial)
    if not res.ok:
        return []
    names = []
    for line in res.stdout.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("ls:") or "No such file" in line:
            continue
        names.append(line)
    return names


def list_dir_long(serial: str | None, remote_path: str) -> list[tuple[str, bool, int]]:
    """列出远程目录，返回 ``(名字, 是否目录, 字节大小)``。

    大小对目录无意义（为 0）。
    """
    res = run_adb(["shell", "ls", "-l", _q(remote_path)], serial=serial)
    if not res.ok:
        return []
    out: list[tuple[str, bool, int]] = []
    for line in res.stdout.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("total") or line.startswith("ls:"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        perms = parts[0]
        try:
            size = int(parts[4])
        except (ValueError, IndexError):
            size = 0
        name = " ".join(parts[5:]) if len(parts) > 5 else parts[-1]
        out.append((name, perms.startswith("d"), size))
    return out


def mkdirs(serial: str | None, remote_path: str) -> AdbResult:
    """递归创建远程目录（``mkdir -p``）。"""
    return run_adb(["shell", "mkdir", "-p", _q(remote_path)], serial=serial)


def pull(serial: str | None, remote_path: str, local_path: str,
         timeout: float = _TRANSFER_TIMEOUT) -> AdbResult:
    """``adb pull``：从设备拉取文件/目录到本地。

    Args:
        serial: 设备序列号。
        remote_path: 设备上的路径。
        local_path: 本地目标路径。
        timeout: 超时秒数（批量传输默认 600 秒）。

    Returns:
        :class:`AdbResult`。
    """
    parent = os.path.dirname(os.path.abspath(local_path))
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            return AdbResult(ok=False, returncode=-1, error=f"无法创建本地目录 {parent}: {exc}")
    return run_adb(["pull", remote_path, local_path], serial=serial, timeout=timeout)


def push(serial: str | None, local_path: str, remote_path: str,
         timeout: float = _TRANSFER_TIMEOUT) -> AdbResult:
    """``adb push``：把本地文件/目录推到设备。

    注意：**没有** 办法用 adb 保留文件的修改时间（Android 的 MTP/adb
    实现不完整），这一点与 MTP 相同。
    """
    if not os.path.exists(local_path):
        return AdbResult(ok=False, returncode=-1, error=f"本地路径不存在: {local_path}")
    return run_adb(["push", local_path, remote_path], serial=serial, timeout=timeout)


def shell(serial: str | None, shell_cmd: str, timeout: float = _DEFAULT_TIMEOUT) -> AdbResult:
    """执行任意 shell 命令。"""
    return run_adb(["shell", shell_cmd], serial=serial, timeout=timeout)


def content_query(serial: str | None, uri: str,
                  projection: str = "_id,_display_name,_size") -> AdbResult:
    """``adb shell content query`` —— 通过 MediaProvider 查询（只读）。

    说明：``content`` 命令以 ``com.android.shell`` 的身份运行，**不会提权**，
    受各 Provider 自己的权限约束；它**不是**读取应用私有文件的万能钥匙。
    """
    return run_adb(["shell", "content", "query", "--uri", uri,
                    "--projection", projection], serial=serial, timeout=60.0)


def find_customlevels(serial: str | None = None) -> list[str]:
    """在设备上查找存在的 CustomLevels 目录。

    Returns:
        真实存在的路径列表（按 :data:`QUEST_CUSTOMLEVELS` 的顺序）。
    """
    dev = quest_device() if serial is None else None
    ser = serial or (dev.serial if dev else None)
    if ser is None:
        return []
    found = []
    for cand in QUEST_CUSTOMLEVELS:
        if is_dir(ser, cand):
            found.append(cand)
    return found


def _q(path: str) -> str:
    """给远程路径加引号，避免空格和特殊字符出问题。"""
    if not path:
        return "''"
    return "'" + path.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def selftest(verbose: bool = True) -> int:
    """打印本机 adb 情况。

    Returns:
        0 表示「本模块工作正常」（即使没装 adb，只要优雅降级了也算正常）；
        只有在 adb 存在却无法执行时才返回 1。
    """
    out = print if verbose else (lambda *a, **k: None)
    out("=" * 72)
    out("adb_bridge 自检")
    out("=" * 72)
    exe = find_adb()
    out(f"adb 路径      : {exe or '未找到'}")
    out(f"adb 版本      : {adb_version()}")
    out(f"可用          : {adb_available()}")

    broken = False
    if exe:
        ver = adb_version()
        if "执行失败" in ver or "无输出" in ver:
            out("!! adb 存在但无法执行，请检查文件是否完整、是否有杀软拦截。")
            broken = True

    out("")
    out("--- Quest 候选路径（按优先级）---")
    for p in QUEST_CUSTOMLEVELS:
        out(f"  {p}")
    out("")
    out("--- 设备 ---")
    devs = list_devices()
    if not devs:
        out("  未检测到 adb 设备。")
        if not adb_available():
            out("  原因：本机没有 adb（属正常降级，未安装 Platform Tools）。")
        else:
            out("  原因：没有设备连接，或头显未授权 USB 调试。")
    for d in devs:
        out(f"  {d.description}  details={d.details}")
        out(f"    usable={d.usable}")
    out("")
    out("--- 在设备上查找 CustomLevels ---")
    found = find_customlevels()
    if found:
        for f in found:
            out(f"  ✓ {f}")
    else:
        out("  未找到（未连接设备时为正常结果）。")
    out("")
    out("--- 状态摘要 ---")
    out(describe_status())
    out("")
    out(f"--- 结论：{'模块异常' if broken else '模块正常（降级可用）'} ---")
    out("=" * 72)
    return 1 if broken else 0


def _main(argv: list[str]) -> int:
    if "--selftest" in argv or "-t" in argv:
        return selftest()
    if "--help" in argv or "-h" in argv:
        print(
            "adb_bridge —— 通过 adb 访问 Quest\n"
            "\n"
            "用法:\n"
            "  python adb_bridge.py --selftest    # 自检（打印 adb 路径/版本/设备）\n"
            "\n"
            "作为库使用:\n"
            "  import adb_bridge\n"
            "  dev = adb_bridge.quest_device()\n"
            "  adb_bridge.push(dev.serial, r'D:\\\\Map', adb_bridge.QUEST_CUSTOMLEVELS[0])\n"
        )
        return 0
    return selftest()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
