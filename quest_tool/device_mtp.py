"""MTP（Windows 便携设备）后端 —— **不需要 adb 也能读取歌曲**。

关键事实（决定了这里的设计）：
1. MTP 设备没有盘符，Python 的 `os`/`shutil` 完全用不了它的命名空间路径；
2. 但 Shell 的 `Folder.CopyHere()` 可以**复制**命名空间里的条目 —— 这是不装 adb
   也能把设备上的歌读到本地的唯一途径；
3. 重要的是：现版 SongCore 的歌曲目录在 `/sdcard/ModData/...`，
   **不在** `Android/data` 里，所以既不受 Android 11+ scoped storage 限制，
   也不需要开发者模式/adb。

因此本后端的策略是"**镜像**"：
    首次访问时用 CopyHere 把设备上的歌曲目录整份复制到一个本地镜像目录
    （`<数据目录>/mtp_mirror/<设备>/...`），之后所有解析、封面、对比都在本地做。
* 优点：一次复制，后续全部本地高速访问；不需要 adb；不需要开发者模式；
* 缺点：设备上新增/删除的歌不会自动出现，需要点「重新扫描」再镜像一次；
      且 MTP 传输本身较慢（比 adb 慢数倍）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .constants import BACKUP_SUFFIX, INFO_FILE_NAMES
from .fsapi import FSError, FSUnavailable, Location, ProgressFn, SongFS
from .logging_setup import get_logger
from .songs import SongMeta, load_info_file
from .storage import data_dir

logger = get_logger("mtp")

HINT = (
    "该位置是 Windows 便携设备（MTP）。程序需要先把它镜像到本地才能解析歌曲信息。\n\n"
    "如果镜像失败，可以：\n"
    "1) 安装 Android Platform Tools（adb）—— 速度最快、功能最全（推荐）；\n"
    "2) 或者用资源管理器手动把 CustomLevels 复制到电脑上的一个文件夹，"
    "再把那个本地文件夹选为 Quest 路径。"
)

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
_COPY_SCRIPT = Path(__file__).with_name("shell_copy.ps1")


@dataclass
class MirrorInfo:
    """镜像状态。"""

    source: str
    target: Path
    copied: int = 0
    expected: int = 0
    seconds: float = 0.0
    error: str = ""


def _powershell() -> str | None:
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


def copy_shell_items(
    direction: str,
    source: str,
    dest: str,
    timeout: int = 900,
    replace: bool = False,
    only: str = "",
) -> MirrorInfo:
    """调用 shell_copy.ps1 做一次命名空间复制。direction: to_local / to_device。

    `only`：**只复制源目录里这个名字的条目**。复制"某一首歌"时必须用它 ——
    CopyHere 的语义是"把源文件夹里的条目放进目标文件夹"，不指定的话会把
    **源目录的全部内容**都复制过去（v14.5 之前就是这样：复制一首歌等于把整个
    本地歌库推送到设备）。

    `replace=True` 时脚本会先把目标里的同名条目改名挪开再复制 —— 这是**唯一**能真正
    覆盖更新的办法：Windows 的 Folder.CopyHere 遇到同名文件夹只会"跳过冲突文件"
    （不替换），而如果标志里没有 FOF_NOCONFIRMATION 还会弹「替换/跳过」对话框一直等人点。
    """
    info = MirrorInfo(source=source, target=Path(dest))
    if not IS_WINDOWS:
        info.error = "Shell 复制仅支持 Windows"
        return info
    ps = _powershell()
    if ps is None or not _COPY_SCRIPT.exists():
        info.error = "找不到 PowerShell 或 shell_copy.ps1"
        return info

    cmd = [
        ps,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(_COPY_SCRIPT),
        "-Direction",
        direction,
        "-Source",
        source,
        "-Dest",
        dest,
        "-TimeoutSeconds",
        str(timeout),
    ]
    if replace:
        cmd.append("-Replace")
    if only:
        cmd += ["-Only", only]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 60, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        info.error = f"复制超时（>{timeout} 秒）"
        return info
    except OSError as exc:
        info.error = f"无法启动 PowerShell: {exc}"
        return info

    raw = proc.stdout.decode("utf-8-sig", "replace").strip()
    if not raw:
        info.error = proc.stderr.decode("utf-8", "replace").strip()[:300] or "复制脚本没有输出"
        return info
    try:
        payload = json.loads(raw.splitlines()[-1])
    except json.JSONDecodeError as exc:
        info.error = f"解析复制结果失败: {exc}"
        return info

    info.copied = int(payload.get("copied", 0) or 0)
    info.expected = int(payload.get("expected", 0) or 0)
    info.seconds = float(payload.get("waited_seconds", 0) or 0)
    if not payload.get("ok"):
        info.error = str(payload.get("error", "未知错误"))
    return info


class MtpFS(SongFS):
    """把 MTP 目录镜像到本地后按普通本地目录使用。"""

    def __init__(self, location: Location, prefer_adb: bool = False, adb_path: str = "") -> None:
        super().__init__(location)
        from .device_shell import ShellBridge, ShellItem

        self.bridge = ShellBridge()
        self.prefer_adb = prefer_adb
        self.adb_path = adb_path
        #: 最近一次枚举到的"设备上这首歌的条目" {名字: ShellItem}。
        #: 每次起 PowerShell 约 1.3 秒，所以能复用就复用（见 remote_file_stats）。
        self._last_items: dict[str, ShellItem] = {}
        self.mirror_root = self._mirror_root()
        self._mirror_ready = False
        self._lock = threading.RLock()

    # ---------------- 镜像目录 ----------------
    def _mirror_root(self) -> Path:
        """镜像根目录：<数据目录>/mtp_mirror/<设备标识>。"""
        label = self.location.label or self.location.root
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)[:40]
        return data_dir() / "mtp_mirror" / (safe or "device")

    def _target(self, folder: str | None = None) -> Path:
        return self.mirror_root / folder if folder else self.mirror_root

    @property
    def mirrored(self) -> bool:
        return self._target().is_dir() and any(self._target().iterdir())

    # ---------------- 可用性 ----------------
    def available(self) -> bool:
        if self.mirrored:
            return True
        return self.bridge.available and bool(self.location.root)

    # ---------------- 镜像 ----------------
    def _manifest_path(self) -> Path:
        """镜像清单文件：{歌曲名: Shell 报的"修改日期"}。

        放在 `mirror_root` **外面** —— 放进镜像目录会被当成一首歌。
        清单只用来判断"哪些歌变了"，丢了最坏情况是下次多拉一遍，不影响正确性。
        """
        return self.mirror_root.with_name(self.mirror_root.name + ".manifest.json")

    def _load_manifest(self) -> dict[str, str]:
        try:
            data = json.loads(self._manifest_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def _save_manifest(self, manifest: dict[str, str]) -> None:
        try:
            path = self._manifest_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.debug("写镜像清单失败（不影响使用）: %s", exc)

    def _device_listing(self) -> dict[str, str] | None:
        """枚举**设备上**的歌曲目录 -> {名字: 修改日期}；枚举不了返回 None。

        只列名字，不传内容，所以很快 —— 这是"增量镜像"能成立的前提。
        """
        try:
            items = self.bridge.list_items(self.location.shell_path, use_cache=False)
        except RuntimeError as exc:
            logger.debug("Shell 枚举设备目录失败: %s", exc)
            return None
        return {
            i.name: i.modified
            for i in items
            if i.is_folder and not i.name.startswith(".") and not i.name.endswith(BACKUP_SUFFIX)
        }

    def sync_mirror(self, progress: ProgressFn | None = None, force: bool = False) -> MirrorInfo:
        """把设备上的歌曲目录镜像到本地。

        v14.5 起是**增量**的：先用 Shell 枚举设备上的目录（只列名字，不传内容），
        只拉"本地缺的"和"改动过的"，并删掉设备上已经不在的。
        此前只有一句"镜像存在且非空就用缓存"，所以设备上新增/删除的歌**永远不反映**，
        用户只能整份重拉。
        """
        target = self._target()
        if not self.bridge.available:
            raise FSUnavailable(HINT)

        if force or not self.mirrored:
            return self._full_mirror(progress)

        listing = self._device_listing()
        if listing is None:
            # 枚举不了（Shell 不可用/设备断开）：保住已有镜像，绝不动用户的数据
            if progress:
                progress(f"使用本地镜像（{len(list(target.iterdir()))} 项，设备暂不可枚举）", 100)
            return MirrorInfo(source=self.location.root, target=target, copied=0)

        local_folders = {p.name for p in target.iterdir() if p.is_dir()}
        if not listing and local_folders:
            # 设备上一首都没有、镜像里却有 —— 更可能是枚举出了问题（锁屏/刚断开），
            # 而不是用户真的把歌全删了。保守起见不动镜像。
            logger.warning("设备上枚举不到歌曲，但镜像里有 %d 项，保守起见不改动镜像", len(local_folders))
            if progress:
                progress(f"使用本地镜像（{len(local_folders)} 项）", 100)
            return MirrorInfo(source=self.location.root, target=target, copied=0)

        manifest = self._load_manifest()
        missing = [n for n in listing if n not in local_folders]
        changed = [n for n in listing if n in local_folders and manifest.get(n) != listing[n]]
        stale = [n for n in local_folders if n not in listing]
        todo = sorted(set(missing) | set(changed))

        if not todo and not stale:
            if progress:
                progress(f"镜像已是最新（{len(local_folders)} 项）", 100)
            return MirrorInfo(source=self.location.root, target=target, copied=0)

        started = time.time()
        copied = 0
        for index, name in enumerate(todo, start=1):
            if progress:
                progress(f"更新镜像 {index}/{len(todo)}：{name[:28]}",
                         int(index / max(1, len(todo)) * 100))
            if self._pull_one(name):
                copied += 1
        for name in stale:
            shutil.rmtree(target / name, ignore_errors=True)
            manifest.pop(name, None)

        self._save_manifest(
            {n: listing[n] for n in listing if (target / n).is_dir()}
        )
        self._mirror_ready = True
        info = MirrorInfo(
            source=self.location.root,
            target=target,
            copied=copied,
            expected=len(listing),
            seconds=time.time() - started,
        )
        logger.info("MTP 镜像增量更新：新增/更新 %d 项，删除 %d 项，用时 %.1f 秒",
                    copied, len(stale), info.seconds)
        if progress:
            progress(f"镜像已更新：新增/更新 {copied} 项，删除 {len(stale)} 项", 100)
        return info

    def _pull_one(self, name: str) -> bool:
        """从设备单独拉一首歌进镜像（增量用；失败只记日志，不中断整轮）。"""
        info = copy_shell_items("to_local", self.location.shell_path, str(self.mirror_root),
                                timeout=1800, replace=True, only=name)
        if info.error:
            logger.warning("拉取 %s 失败: %s", name, info.error)
            return False
        return self._target(name).is_dir()

    def _full_mirror(self, progress: ProgressFn | None = None) -> MirrorInfo:
        """整份重拉：首次镜像、用户点「重新扫描」（force）、或增量不可用时。"""
        target = self._target()
        if progress:
            progress("正在从设备复制歌曲目录（MTP，可能需要几分钟）…", 5)
        started = time.time()

        # 复制整份 CustomLevels 到 <mirror_root>/CustomLevels
        staging = target.parent / (target.name + "__incoming")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            info = copy_shell_items("to_local", self.location.root, str(staging), timeout=1800)
            if info.error:
                raise FSUnavailable(f"镜像失败：{info.error}\n\n{HINT}")

            # CopyHere 的目标是"父目录"，因此结果可能是 staging/CustomLevels 或直接铺在 staging 下
            candidate = staging / "CustomLevels"
            source = candidate if candidate.is_dir() else staging
            if not any(source.iterdir()):
                raise FSUnavailable(f"镜像后目录为空：{source}\n\n{HINT}")

            shutil.rmtree(target, ignore_errors=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        info.seconds = time.time() - started
        info.target = target
        self._mirror_ready = True
        listing = self._device_listing() or {}
        self._save_manifest(
            {p.name: listing.get(p.name, "") for p in target.iterdir() if p.is_dir()}
        )
        logger.info("MTP 镜像完成：%s（%s 项，%.1f 秒）", target, info.copied, info.seconds)
        if progress:
            progress(f"镜像完成：{info.copied} 项，用时 {info.seconds:.0f} 秒", 100)
        return info

    # ---------------- 只读接口（基于镜像） ----------------
    def list_song_folders(self, progress: ProgressFn | None = None) -> list[str]:
        self.sync_mirror(progress)
        root = self._target()
        try:
            entries = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
            # 覆盖复制留下的旧版本备份不是歌曲，别混进歌单
            backups = [p.name for p in entries if p.name.endswith(BACKUP_SUFFIX)]
            if backups:
                logger.info("镜像里有 %d 个旧版本备份目录（已从歌单隐藏）：%s",
                            len(backups), ", ".join(backups[:3]))
            return sorted(
                (p.name for p in entries if not p.name.endswith(BACKUP_SUFFIX)),
                key=str.lower,
            )
        except OSError as exc:
            raise FSUnavailable(f"读取镜像目录失败: {exc}") from exc

    def _folder_path(self, folder: str) -> Path:
        name = (folder or "").strip()
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise FSError(f"非法文件夹名: {folder!r}")
        return self._target(name)

    def list_files(self, folder: str) -> list[str]:
        try:
            return [p.name for p in self._folder_path(folder).iterdir()]
        except OSError as exc:
            raise FSError(f"无法列出 {folder}: {exc}") from exc

    def stat_mtime(self, folder: str) -> float:
        path = self._folder_path(folder)
        for name in INFO_FILE_NAMES:
            candidate = path / name
            if candidate.is_file():
                try:
                    return candidate.stat().st_mtime
                except OSError:
                    break
        return 0.0

    def read_info_json(self, folder: str) -> dict | None:
        path = self._folder_path(folder)
        for name in INFO_FILE_NAMES:
            candidate = path / name
            if candidate.is_file():
                data = load_info_file(candidate)
                if data is not None:
                    return data
        return None

    def read_duration(self, folder: str, data: dict | None = None) -> float:
        from .song_duration import local_song_duration
        return local_song_duration(self._folder_path(folder), data or self.read_info_json(folder))

    def read_file(self, folder: str, relative: str) -> bytes:
        try:
            return (self._folder_path(folder) / relative).read_bytes()
        except OSError as exc:
            raise FSError(f"读取文件失败: {exc}") from exc

    def local_folder_exists(self, folder: str) -> bool:
        try:
            return self._folder_path(folder).is_dir()
        except FSError:
            return False

    def scan_metadata(
        self,
        folders: Sequence[str],
        progress: ProgressFn | None = None,
        cancel=None,
        workers: int = 8,
    ) -> dict[str, SongMeta]:
        """镜像已在本地，直接按本地文件解析（比逐文件 COM 读取快得多）。"""
        return super().scan_metadata(folders, progress=progress, cancel=cancel, workers=workers)

    # ---------------- 写操作：复制 / 删除 ----------------
    def copy_into(self, folder: str, local_src: Path, progress: ProgressFn | None = None) -> None:
        """本地 -> 设备（通过 Shell CopyHere，异步）。

        CopyHere 的语义是"把**源文件夹里的条目**放进目标文件夹"，所以两个选择都不对：
          * 源只传歌曲目录 → 它的**内容**会被平铺进 CustomLevels（info.dat 散在根下）；
          * 源只传歌曲库目录 → **整个歌库**都会被推过去。
        正确做法是"源传歌曲库目录 + `-Only <歌曲名>`"，只复制那一个条目。
        v14.5 之前是第二种（源传库目录、没有 -Only）—— **复制一首歌等于把用户
        上千首歌全传到设备上**，而且因为 replace=True 还会把设备上的同名歌曲全部
        改名挪开重传一遍。
        """
        if not local_src.is_dir():
            raise FSError(f"本地源目录不存在: {local_src}")

        parent_shell = self.location.shell_path
        if progress:
            progress(f"正在复制到设备（MTP）: {folder}", 0)
        # source 传"歌曲库目录" + only=歌曲名 —— **不能**只传库目录，
        # 那会把整个本地歌库都推过去（见 copy_shell_items 的说明）。
        # replace=True：设备上已有同名歌曲时，先把它改名挪开再复制，
        # 否则 Windows 只会"跳过"旧文件，用户会以为更新了其实没有。
        info = copy_shell_items("to_device", str(local_src.parent), parent_shell,
                                timeout=1800, replace=True, only=local_src.name)
        if info.error:
            raise FSError(f"MTP 复制到设备失败：{info.error}")

        # 让界面能看到新歌：**只把这一首放进本地镜像**，不要整份重镜像。
        # v14.5 之前这里是 `sync_mirror(force=True)` —— 每复制一首就把设备上
        # 全部歌曲重拉一遍，复制 N 首 = N 次全量镜像。
        self.verify_copy(folder, local_src)
        self._mirror_entry(folder, local_src)
        if progress:
            progress(f"已复制 {folder} 到设备", 100)

    def _mirror_entry(self, folder: str, local_src: Path) -> None:
        """把一首歌直接写进本地镜像（不重拉整个设备目录）。"""
        target = self._target(folder)
        try:
            shutil.rmtree(target, ignore_errors=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(local_src, target)
        except OSError as exc:
            logger.warning("更新本地镜像失败（界面可能看不到这首歌）: %s", exc)
            return
        # 清单同步更新，否则下次同步会以为这首"变了"又拉一遍。
        # 修改日期直接复用 verify_copy 刚枚举到的结果（省掉一次 PowerShell 启动）。
        manifest = self._load_manifest()
        item = self._last_items.get(folder)
        if item is not None:
            manifest[folder] = item.modified
        elif folder not in manifest:
            manifest[folder] = ""
        self._save_manifest(manifest)
        self.bridge.clear_cache()

    def remote_file_stats(self, folder: str) -> tuple[int, int | None]:
        """通过 Shell 枚举**设备上**该目录，返回 (顶层文件数, None)。

        故意**不拿本地镜像充数** —— 镜像是我们自己写的，比它等于自说自话。
        Shell 枚举的是设备真实内容，只是拿不到字节：它的"大小"列是四舍五入后的
        字符串（实测 `3.00 MB` / `1.20 KB`），所以只能比文件数。
        这也够抓住 CopyHere 最典型的失败：**同名冲突时静默跳过文件**（歌曲没真正更新）。

        顺手把这次枚举到的条目记下来 —— 紧接着的 `_mirror_entry` 要写清单，
        正需要"设备上这首歌的修改日期"，复用就不必再起一次 PowerShell
        （每次约 1.3 秒，一次复制原本要起 3 次）。
        """
        items = self.bridge.list_items(self._device_shell_path(folder), use_cache=False)
        self._last_items = {i.name: i for i in items}
        return len([i for i in items if not i.is_folder]), None

    def _device_shell_path(self, folder: str) -> str:
        """拼出设备上某首歌的 Shell 命名空间路径（同时做名字校验）。"""
        name = (folder or "").strip()
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise FSError(f"非法文件夹名: {folder!r}")
        sep = "\\" if "\\" in self.location.shell_path else "/"
        return self.location.shell_path.rstrip("\\/") + sep + name

    def copy_out(self, folder: str, local_dst: Path, progress: ProgressFn | None = None) -> None:
        """设备 -> 本地（直接复制单首歌，无需整份镜像）。"""
        if self.mirrored:
            source_path = self._folder_path(folder)
            if source_path.is_dir():
                if local_dst.exists():
                    shutil.rmtree(local_dst, ignore_errors=True)
                shutil.copytree(source_path, local_dst)
                if progress:
                    progress(f"已从镜像复制 {folder}", 100)
                return
        if progress:
            progress(f"正在从设备复制（MTP）: {folder}", 0)
        local_dst.parent.mkdir(parents=True, exist_ok=True)
        # 源传"设备上的歌曲库目录" + only=歌曲名（不能只传歌曲目录，否则内容被平铺；
        # 也不能不带 only，否则会把设备上**全部**歌曲拉到本地）
        # 本地目标若已有同名文件夹，同样用 replace 语义（本地清理是瞬时的）
        info = copy_shell_items("to_local", self.location.shell_path, str(local_dst.parent),
                                timeout=1800, replace=local_dst.exists(), only=folder)
        if info.error:
            raise FSError(f"MTP 复制到本地失败：{info.error}\n\n{HINT}")
        if not local_dst.is_dir():
            raise FSError(f"复制后未找到目标目录: {local_dst}")
        if progress:
            progress(f"已复制 {folder} 到本地", 100)

    def delete_folder(self, folder: str) -> None:
        """MTP 删除风险高（无回收站、无法可靠确认），一律拒绝并给出替代方案。"""
        raise FSUnavailable(
            "出于安全考虑，本程序不会通过 MTP 删除设备文件"
            "（MTP 删除无法可靠确认结果，而且设备上没有回收站）。\n"
            "请用资源管理器手动删除，或改用 adb 连接后再删除。"
        )

    def free_space_bytes(self) -> int | None:
        try:
            return shutil.disk_usage(self.mirror_root.parent).free
        except OSError:
            return None


def mtp_fs_from_location(location: Location, adb_path: str = "", **_ignored: object) -> MtpFS:
    return MtpFS(location, adb_path=adb_path)
