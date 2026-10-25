"""adb（Android Platform Tools）桥接层。

为什么需要它：
Quest 3 用 USB 连接时是 **MTP 便携设备**，Windows 不给盘符，`os.listdir` / `shutil.copy`
一概无效。adb 走的是完全不同的通道（USB 调试），提供 `ls/cat/pull/push` 这些"类文件系统"
命令，且**比 MTP 快得多**，是这类工具最实用的方案。

本模块只依赖标准库：
* adb 可执行文件从设置、PATH、常见安装目录中查找；
* 所有命令都带超时，绝不无限卡死 UI；
* 找不到 adb / 设备未授权时抛出明确的 `AdbUnavailable`，由上层提示用户。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

# Quest 上 CustomLevels 的候选路径**只有一份定义**，在 constants.py。
# 这里 import 进来是为了让 `adb.QUEST_CUSTOMLEVELS_CANDIDATES` 继续可用
# （device.py 就是这么引用的）。v14.5 之前两边各写一份、内容完全相同。
from .constants import QUEST_CUSTOMLEVELS_CANDIDATES  # noqa: F401
from .constants import QUEST_DEVICE_CODENAMES, QUEST_PROBE_PROPS, QUEST_PRODUCT_KEYWORDS
from .logging_setup import get_logger

logger = get_logger("adb")

IS_WINDOWS = os.name == "nt"
# Windows 下隐藏子进程控制台窗口，否则每次调用都会闪黑框
_CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

DEFAULT_TIMEOUT = 30.0
TRANSFER_TIMEOUT = 3600.0

#: 单次批量 tar 命令的字节预算（详见 `_chunk_by_command_budget`）。
#: 上限是 Windows CreateProcess 的 32767 字符，这里留一半余量。
_BULK_COMMAND_BUDGET = 16000


class AdbError(RuntimeError):
    """adb 命令执行失败。"""


class AdbUnavailable(AdbError):
    """找不到 adb 或没有可用设备。"""


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str  # device / unauthorized / offline ...
    model: str = ""
    #: `getprop` 探测出来的身份（可能为空 —— 只有真去问过才有）。
    #: 见 `AdbBridge.identify()`。
    codename: str = ""
    brand: str = ""

    @property
    def usable(self) -> bool:
        return self.state == "device"

    @property
    def label(self) -> str:
        return f"{self.model or 'Android 设备'} ({self.serial})"

    @property
    def is_quest(self) -> bool:
        """这台设备是不是 Quest。

        **没探测过身份时返回 False**（宁可"当作不是"，也不要对着手机做读写删）。
        `identify()` 会把结果写进 `codename` / `brand`。
        """
        return _matches_quest(self.codename, self.brand, self.model)


def _matches_quest(*values: str) -> bool:
    """判断若干 `getprop` 值里有没有 Quest 的特征。

    Quest 各代的 codename（eureka / panther / hollywood / seacliff / monterey）
    是最可靠的判据；品牌字样作为兜底（有些系统把 codename 抹掉了）。
    """
    haystack = " ".join(v for v in values if v).lower()
    if not haystack:
        return False
    if any(code in haystack for code in QUEST_DEVICE_CODENAMES):
        return True
    return any(word in haystack for word in QUEST_PRODUCT_KEYWORDS)


def _resolve_executable(candidate: Path | str) -> str | None:
    """把一个"可能是 adb 可执行文件"的路径解析成可执行路径。

    注意：`shutil.which("adb")` 在 Windows 上**不会**匹配 `adb.bat`/`adb.cmd`
    （PATHEXT 只在 which 收到不带扩展名的名字时生效，而且它不会主动加 .bat）。
    用户手工填路径时又经常填到 .bat / .cmd 包装脚本上，所以这里显式补齐。
    """
    path = Path(str(candidate).strip().strip('"'))
    try:
        if path.is_file():
            return str(path)
    except OSError:
        return None
    if IS_WINDOWS and not path.suffix:
        for ext in (".exe", ".bat", ".cmd", ".com", ".ps1"):
            try:
                if path.with_suffix(ext).is_file():
                    return str(path.with_suffix(ext))
            except OSError:
                continue
    return None


def find_adb(explicit: str = "") -> str | None:
    """按 设置 -> PATH -> 常见安装位置 的顺序查找 adb。"""
    candidates: list[Path] = []
    if explicit:
        resolved = _resolve_executable(explicit)
        if resolved:
            return resolved
        candidates.append(Path(explicit))
    if IS_WINDOWS:
        for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "platform-tools" / "adb.exe")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
        candidates.extend(
            [
                Path(r"C:\platform-tools\adb.exe"),
                Path(r"C:\adb\adb.exe"),
                Path.home() / "platform-tools" / "adb.exe",
                Path.home() / "Downloads" / "platform-tools" / "adb.exe",
                Path.home() / "Desktop" / "platform-tools" / "adb.exe",
            ]
        )
    else:
        candidates.extend(
            [
                Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
                Path("/usr/local/bin/adb"),
                Path("/usr/bin/adb"),
                Path("/opt/homebrew/bin/adb"),
            ]
        )

    for candidate in candidates:
        resolved = _resolve_executable(candidate)
        if resolved:
            return resolved

    for name in ("adb", "adb.exe", "adb.bat", "adb.cmd"):
        found = shutil.which(name)
        if found:
            return found
    return None


#: 经典无线调试的默认端口（`adb tcpip 5555` 之后就用它连）。
#: 注意 Android 11+ 的「无线调试」用的是**动态端口**，而且配对端口 ≠ 连接端口，
#: 那两个数字只能从设备屏幕上抄（见 AdbBridge.pair）。
DEFAULT_WIRELESS_PORT = 5555

#: 从设备上读 Wi-Fi IP 的几种取法（不同 Android 版本可用命令不一样，逐个试）
_IP_COMMANDS = (
    "ip route | grep -m1 'wlan0' | awk '{{print $9}}'",
    "ip -4 addr show wlan0 | grep -m1 inet | awk '{{print $2}}' | cut -d/ -f1",
    "getprop dhcp.wlan0.ipaddress",
)


def _is_ipv4(text: str) -> bool:
    """判断是不是一个合法的 IPv4 点分串（严格四段 0~255）。"""
    parts = text.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or not 0 <= int(part) <= 255:
            return False
    return True


class AdbBridge:
    """一个 adb 会话（可指定设备序列号）。"""
    def __init__(self, adb_path: str | None = None, serial: str = "", timeout: float = DEFAULT_TIMEOUT):
        self.adb_path = adb_path or find_adb() or ""
        self.serial = serial
        self.timeout = timeout
        self._lock = threading.Lock()  # adb 服务端对同一设备并发写会互相干扰

    # ---------------- 无线调试（ROADMAP D4） ----------------
    def device_ip(self) -> str:
        """读设备当前的 Wi-Fi IP（读不到返回空串）。

        三种取法逐个试 —— `ip route` / `ip addr` / `getprop` 在不同 Android 版本上
        可用性不一样，真机上不能只赌一条。这也是"无线调试"能自动填 IP 的前提。
        """
        for command in _IP_COMMANDS:
            try:
                output = str(self.shell(command, timeout=15, check=False)).strip()
            except (AdbError, AdbUnavailable):
                continue
            # 结果里只可能是 IPv4；顺手把 `\r` 和多余空白清掉
            for token in output.split():
                candidate = token.strip().strip("\r\n")
                if _is_ipv4(candidate):
                    return candidate
        return ""

    def start_tcpip(self, port: int = DEFAULT_WIRELESS_PORT) -> str:
        """让设备端的 adbd 监听 TCP 端口（经典无线方式，**需要先 USB 连着**）。

        `adb tcpip <port>` 只是让设备重开一个 TCP 监听口，**不会断开当前的 USB**。
        之后拔线、`adb connect <设备IP>:<port>` 即可。
        返回 adb 的原始输出（通常是 `restarting in TCP mode port: 5555`）。
        """
        output = self.run(["tcpip", str(port)], timeout=30)
        return str(output).strip()

    def connect(self, host: str, port: int = DEFAULT_WIRELESS_PORT, timeout: float = 15.0) -> bool:
        """连到无线设备。返回是否连上（adb 连不上时**不抛异常**，只回 False）。"""
        address = f"{host}:{port}"
        try:
            output = str(self.run(["connect", address], timeout=timeout, check=False))
        except (AdbError, AdbUnavailable):
            return False
        # adb 的失败也是退出码 0，只能看文字：`unable to connect` / `failed to connect`
        lowered = output.lower()
        return "connected to" in lowered and "unable to" not in lowered and "failed" not in lowered

    def disconnect(self, host: str = "", port: int = DEFAULT_WIRELESS_PORT) -> None:
        """断开无线连接（host 为空 = 断开全部）。失败不抛异常。"""
        args = ["disconnect"] + ([f"{host}:{port}"] if host else [])
        try:
            self.run(args, timeout=15, check=False)
        except (AdbError, AdbUnavailable):
            pass

    def pair(self, host: str, port: int, code: str, timeout: float = 30.0) -> tuple[bool, str]:
        """用**配对码**配对（Android 11+ / Quest 3 的「无线调试」，全程不需要线）。

        返回 (是否成功, 给用户看的说明)。配对端口和连接端口**不是同一个** ——
        头显里「使用配对码配对设备」会给一个 6 位码和一个 IP:端口，
        配对完还要用「无线调试」页面上另一个 IP:端口去 `connect`。
        """
        address = f"{host}:{port}"
        try:
            output = str(self.run(["pair", address, code], timeout=timeout, check=False))
        except AdbError as exc:
            return False, str(exc)
        except AdbUnavailable as exc:
            return False, str(exc)
        lowered = output.lower()
        if "successfully paired" in lowered:
            return True, output.strip()
        # 最常见的两种失败，直接给中文原因，省得用户去猜
        if "cannot find" in lowered or "failed to connect" in lowered or "refused" in lowered:
            return False, "连不上配对端口 —— 确认头显的「无线调试」页面还开着，IP 和端口没抄错"
        if "invalid" in lowered or "incorrect" in lowered or "wrong" in lowered:
            return False, "配对码不对 —— 配对码会过期，回退一步重新点「使用配对码配对设备」"
        if "already paired" in lowered:
            return True, output.strip()
        return False, output.strip() or "配对失败（原因不明）"

    # ---------------- 基础 ----------------
    @property
    def available(self) -> bool:
        return bool(self.adb_path) and Path(self.adb_path).exists()

    def _base_cmd(self) -> list[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def run(
        self,
        args: Sequence[str],
        timeout: float | None = None,
        check: bool = True,
        binary: bool = False,
    ) -> bytes | str:
        """执行 adb 命令。返回 bytes（binary=True）或 str。"""
        if not self.available:
            raise AdbUnavailable(
                "未找到 adb。请安装 Android Platform Tools 并把 adb 加入 PATH，"
                "或在「设置」里手动指定 adb.exe 路径。"
            )
        cmd = self._base_cmd() + list(args)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout or self.timeout,
                creationflags=_CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"adb 命令超时: {' '.join(args[:2])}") from exc
        except OSError as exc:
            raise AdbUnavailable(f"无法执行 adb: {exc}") from exc

        if check and proc.returncode != 0:
            message = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
            raise AdbError(f"adb 失败({proc.returncode}): {message or ' '.join(args)}")
        if binary:
            return proc.stdout
        return proc.stdout.decode("utf-8", "replace")

    def shell(self, command: str, timeout: float | None = None, check: bool = True) -> str:
        """执行设备端 shell 命令。"""
        return str(self.run(["shell", command], timeout=timeout, check=check))

    def shell_bytes(self, command: str, timeout: float | None = None) -> bytes:
        """执行设备端命令并取原始字节（用于 tar 流）。"""
        return self.run(["exec-out", command], timeout=timeout, binary=True)  # type: ignore[return-value]

    # ---------------- 设备 ----------------
    def list_devices(self) -> list[AdbDevice]:
        if not self.available:
            return []
        try:
            output = str(self.run(["devices", "-l"]))
        except AdbError as exc:
            logger.warning("adb devices 失败: %s", exc)
            return []
        devices: list[AdbDevice] = []
        for line in output.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            model = ""
            for token in parts[2:]:
                if token.startswith("model:"):
                    model = token.split(":", 1)[1].replace("_", " ")
            devices.append(AdbDevice(serial=serial, state=state, model=model))
        return devices

    def identify(self, device: AdbDevice) -> AdbDevice:
        """问一次 `getprop`，补全这台设备的 codename / brand / model。

        为什么要单独走一趟：`adb devices -l` 里的 `model:` 是厂商随便写的，
        靠它认不出 Quest；而 `ro.product.device`（codename）是硬编码的，
        Quest 3 就是 `eureka`。**只问 5 个字段、一次 shell**，开销可忽略。
        """
        if device.codename or device.brand:
            return device
        props: dict[str, str] = {}
        # 一次 shell 里问完，比逐条 getprop 快得多
        cmd = "; ".join(f"getprop {name}" for name in QUEST_PROBE_PROPS)
        try:
            output = self.shell(cmd, timeout=10, check=False)
        except AdbError as exc:
            logger.debug("getprop 探测失败（%s）: %s", device.serial, exc)
            return device
        # 输出顺序与 QUEST_PROBE_PROPS 一一对应，但空值会少行 —— 所以按"非空行"配对
        values = [line.strip() for line in output.splitlines() if line.strip()]
        for name, value in zip(QUEST_PROBE_PROPS, values):
            props[name] = value
        codename = props.get("ro.product.device") or props.get("ro.product.name") or ""
        brand = props.get("ro.product.brand") or props.get("ro.product.manufacturer") or ""
        model = props.get("ro.product.model") or device.model
        return AdbDevice(
            serial=device.serial,
            state=device.state,
            model=model,
            codename=codename,
            brand=brand,
        )

    def ensure_device(self, require_quest: bool = True) -> AdbDevice:
        """确保有可用的**目标**设备，否则抛出带解决步骤的异常。

        为什么要 `require_quest`：adb 会把**任何**开着 USB 调试的 Android 设备
        都列出来。原先这里取"第一个 usable 的设备"，于是同时插着手机/平板时，
        工具会抓错设备（用户实测反馈："怎么自动连到我手机了"）。
        现在默认只认 Quest，非 Quest 设备会被跳过并写进报错信息里。

        传 `require_quest=False` 可保留旧行为（只给测试/诊断用）。
        """
        devices = self.list_devices()
        if not devices:
            raise AdbUnavailable(
                "adb 没有检测到任何设备。请检查：\n"
                "1) 头显已用 USB 线连接并在头显里点「允许 USB 调试」；\n"
                "2) 已在 Quest 设置 → 开发者 中打开「USB 连接对话框/开发者模式」；\n"
                "3) 换一根支持数据传输的 USB 线（很多线只能充电）。"
            )
        # 指定了 serial 就直接用，别再挑（用户明确要求哪台就是哪台）
        if self.serial:
            for device in devices:
                if device.serial == self.serial:
                    if not device.usable:
                        raise AdbUnavailable(
                            f"设备 {self.serial} 当前状态是 {device.state}，"
                            "请在头显内戴上设备点击「允许 USB 调试」。"
                        )
                    return self.identify(device)
            known = ", ".join(d.serial for d in devices)
            raise AdbUnavailable(
                f"设置里指定的设备 {self.serial} 不在已连接列表里（当前：{known}）。"
            )

        usable = [d for d in devices if d.usable]
        if not usable:
            states = ", ".join(f"{d.serial}={d.state}" for d in devices)
            raise AdbUnavailable(
                f"设备未授权或离线（{states}）。请在头显内戴上设备点击「允许 USB 调试」，"
                "必要时执行 adb kill-server 后重试。"
            )

        if not require_quest:
            chosen = self.identify(usable[0])
            self.serial = chosen.serial
            return chosen

        others: list[str] = []
        for device in usable:
            identified = self.identify(device)
            if identified.is_quest:
                self.serial = identified.serial
                return identified
            others.append(identified.label)
        detail = ("；已跳过非 Quest 设备：" + "、".join(others)) if others else ""
        raise AdbUnavailable(
            f"连接了 {len(usable)} 台 Android 设备，但没有一台是 Quest{detail}。\n"
            "本工具只对 Quest 读写；请拔掉其它设备，或在「设置 → 设备选择」里手动指定。"
        )

    # ---------------- 文件操作 ----------------
    def exists(self, remote: str) -> bool:
        quoted = _quote(remote)
        out = self.shell(f"[ -e {quoted} ] && echo YES || echo NO", check=False)
        return "YES" in out

    def is_dir(self, remote: str) -> bool:
        quoted = _quote(remote)
        out = self.shell(f"[ -d {quoted} ] && echo YES || echo NO", check=False)
        return "YES" in out

    def list_dirs(self, remote: str) -> list[str]:
        """列出目录下的子目录名（含隐藏目录，由调用方过滤）。"""
        quoted = _quote(remote)
        out = self.shell(f"ls -1p {quoted} 2>/dev/null", check=False, timeout=60)
        names: list[str] = []
        for line in out.splitlines():
            line = line.rstrip("\r")
            if not line:
                continue
            if line.endswith("/"):
                names.append(line[:-1])
        return names

    def list_files(self, remote: str) -> list[str]:
        quoted = _quote(remote)
        out = self.shell(f"ls -1p {quoted} 2>/dev/null", check=False, timeout=60)
        names: list[str] = []
        for line in out.splitlines():
            line = line.rstrip("\r")
            if line and not line.endswith("/"):
                names.append(line)
        return names

    def mtime(self, remote: str) -> float:
        """取文件 mtime（epoch 秒）；取不到返回 0。"""
        quoted = _quote(remote)
        # toybox/GNU stat 两种写法都试
        out = self.shell(f"stat -c %Y {quoted} 2>/dev/null || echo 0", check=False)
        try:
            return float(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return 0.0

    def read_bytes(self, remote: str, timeout: float | None = None) -> bytes:
        quoted = _quote(remote)
        result = self.shell_bytes(f"cat {quoted}", timeout=timeout)
        return bytes(result)

    def mkdirs(self, remote: str) -> None:
        self.shell(f"mkdir -p {_quote(remote)}", check=False)

    def remove_tree(self, remote: str) -> None:
        self.shell(f"rm -rf {_quote(remote)}", timeout=300)

    def push(self, local: Path, remote_dir: str, timeout: float = TRANSFER_TIMEOUT) -> None:
        """把本地文件/目录推送到设备目录。"""
        if not local.exists():
            raise AdbError(f"本地路径不存在: {local}")
        self.mkdirs(remote_dir)
        with self._lock:
            self.run(["push", str(local), remote_dir], timeout=timeout)

    def pull(self, remote: str, local_dir: Path, timeout: float = TRANSFER_TIMEOUT) -> None:
        if not self.is_dir(remote) and not self.exists(remote):
            raise AdbError(f"设备上不存在: {remote}")
        local_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.run(["pull", remote, str(local_dir)], timeout=timeout)

    def rename(self, src: str, dst: str, timeout: float = 60.0) -> None:
        """设备端改名（`mv`）。

        **必须走 shell 通道**，不能用 `adb push` 来"顺便"起名字：
        Windows 上的 adb.exe 从本地路径推导目标名时处理非 ASCII 有 bug
        （路径里每有一个非 ASCII 字符，设备上的名字就被截掉 1.5 个字符，
        详见 `device_adb.AdbFS.copy_into` 的注释）。
        而 `adb shell` 通道的中文是完全正常的（真机实测：`mv` 到 `中文名` 结果正确）。

        调用方需保证 `dst` 不存在 —— `mv` 的语义是"dst 是已存在目录就移进去"。
        """
        self.shell(f"mv {_quote(src)} {_quote(dst)}", timeout=timeout, check=True)

    def free_space(self, remote: str) -> int | None:
        out = self.shell(f"df -k {_quote(remote)} 2>/dev/null | tail -n 1", check=False)
        parts = out.split()
        # Filesystem 1K-blocks Used Available Use% Mounted
        for token in reversed(parts):
            if token.isdigit():
                return int(token) * 1024
        return None

    def version(self) -> str:
        try:
            return self.shell("getprop ro.build.version.release", check=False).strip()
        except AdbError:
            return ""

    def model(self) -> str:
        for prop in ("ro.product.model", "ro.product.vendor.model", "ro.product.device"):
            value = self.shell(f"getprop {prop}", check=False).strip()
            if value:
                return value
        return ""

    # ---------------- 批量读取（性能关键） ----------------
    def bulk_read_files(
        self, entries: Iterable[tuple[str, str]], timeout: float = 180.0
    ) -> dict[str, bytes]:
        """一次性读取设备上多个文件（例如上千个 info.dat）。

        用 `tar -cf - -C / <设备绝对路径去掉前导斜杠>...` 把**任意目录下**的
        多个小文件打包成**一个**流返回，比逐文件 `cat` 少掉成百上千次进程/协议往返。

        **不要**改成"按目录分组、每组一次 tar"（v14.0 就是这么写的）：
        Beat Saber 是"一首歌一个文件夹"，每个 `info.dat` 都在不同目录里，
        按目录分组会退化成"每首歌一次 adb 往返"。真机（Quest 3，216 首）实测：
            按目录分组  78 ms/首 → 整库 18.18 秒
            单次 tar   2.66 ms/首 → 整库  0.57 秒   （约 31 倍）
        所以这里固定用 `-C /` + 根相对路径，让一次调用覆盖任意目录。

        另一个坑：**不能**用 `tar -T -` 从 stdin 传文件列表 ——
        `adb exec-out` 不转发 stdin（实测会一直挂住直到超时），
        因此只能走命令行，并按字节预算分片（见 `_chunk_by_command_budget`）。

        参数:
            entries: [(设备端文件绝对路径, 返回结果使用的 key)]
        返回:
            {key: bytes} —— 注意 key 是调用方传入的 key，**不是**设备路径；
            失败的文件直接缺席。
        """
        result: dict[str, bytes] = {}
        entries = list(entries)
        if not entries:
            return result

        import io
        import tarfile

        # 设备绝对路径（去掉前导斜杠）-> 调用方的 key
        key_by_rel: dict[str, str] = {}
        for path, key in entries:
            rel = str(path).replace("\\", "/").lstrip("/")
            if rel:
                key_by_rel[rel] = key

        for chunk in _chunk_by_command_budget(list(key_by_rel)):
            names = " ".join(_quote(name) for name in chunk)
            command = f"tar -cf - -C / {names} 2>/dev/null"
            try:
                raw = self.shell_bytes(command, timeout=timeout)
            except AdbError as exc:
                logger.warning("批量读取失败（%d 个文件）: %s", len(chunk), exc)
                continue
            if not raw:
                continue
            try:
                with tarfile.open(fileobj=io.BytesIO(bytes(raw)), mode="r|") as tar:
                    for member in tar:
                        if not member.isfile():
                            continue
                        extracted = tar.extractfile(member)
                        if extracted is None:
                            continue
                        # 设备端 tar 的成员名就是传入的 posix 相对路径（真机核对过），
                        # 所以直接回查 key；不要在 Windows 上用 os.path.basename
                        # （会得到反斜杠，key 对不上）。
                        rel = member.name.replace("\\", "/").lstrip("/")
                        key = key_by_rel.get(rel)
                        if key is not None:
                            result[key] = extracted.read()
            except tarfile.TarError as exc:
                logger.warning("解析 tar 流失败（%d 个文件）: %s", len(chunk), exc)

        return {key: result[key] for _, key in entries if key in result}


def _chunk_by_command_budget(paths: list[str]) -> list[list[str]]:
    """按"拼出来的 tar 命令不超过 `_BULK_COMMAND_BUDGET` 字节"把路径分片。

    上限来自 **Windows 而不是 Android**：我们从 Windows 上 spawn `adb.exe`，
    `CreateProcess` 的命令行总长上限是 32767 字符，超了直接抛
    `FileNotFoundError: [WinError 206] 文件名或扩展名太长`
    （真机实测：32 KB 的命令正常，64 KB 复现该错误）。
    所以按**字节预算**分片，而不是按文件个数 —— 歌曲目录名长度不可控。
    """
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for path in paths:
        cost = len(path) + 3  # 两个引号 + 一个空格
        if current and size + cost > _BULK_COMMAND_BUDGET:
            chunks.append(current)
            current, size = [], 0
        current.append(path)
        size += cost
    if current:
        chunks.append(current)
    return chunks


def _quote(path: str) -> str:
    """安全的 shell 单引号转义（Android toybox sh 兼容）。"""
    return "'" + str(path).replace("'", "'\\''") + "'"


def parse_adb_devices(output: str) -> list[AdbDevice]:
    """解析 `adb devices -l` 输出（便于单测）。"""
    devices: list[AdbDevice] = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        model = ""
        for token in parts[2:]:
            if token.startswith("model:"):
                model = token.split(":", 1)[1].replace("_", " ")
        devices.append(AdbDevice(serial=parts[0], state=parts[1], model=model))
    return devices


def adb_status(adb_path: str = "", serial: str = "") -> dict[str, object]:
    """给 UI 用的一键状态查询。"""
    path = find_adb(adb_path)
    if not path:
        return {
            "ok": False,
            "adb": "",
            "reason": "未找到 adb",
            "hint": "安装 Android Platform Tools 并把 adb 加入 PATH，或在设置中指定 adb.exe",
            "devices": [],
        }
    bridge = AdbBridge(path, serial)
    try:
        devices = bridge.list_devices()
    except AdbError as exc:
        return {"ok": False, "adb": path, "reason": str(exc), "hint": "", "devices": []}
    usable = [d for d in devices if d.usable]
    if not usable:
        hint = (
            "在头显中戴上设备并点击「允许 USB 调试」；确认已开启开发者模式；"
            "换用支持数据传输的 USB 线。"
            if devices
            else "adb 未检测到设备：请连接头显、开启 USB 调试，或换一根数据线。"
        )
        return {
            "ok": False,
            "adb": path,
            "reason": "没有已授权的设备",
            "hint": hint,
            "devices": [d.__dict__ for d in devices],
        }
    return {
        "ok": True,
        "adb": path,
        "reason": "",
        "hint": "",
        "devices": [d.__dict__ for d in usable],
    }
