"""ADB 文件系统后端（Quest 侧的推荐方案）。

性能设计（这是与 MTP 最大的差别）：
* 扫描列表用 **一次** `ls`；拿 mtime 用 **一次** `find -printf`；解析元数据用
  `tar -cf -` 把上百个 info.dat 打包成**一个流**批量读回，避免"每首歌两次往返"。
  3000 首歌如果逐文件读，在 USB 上要几分钟；批量后通常几秒。
* 复制用 `adb push/pull`（内部是同步协议，比 MTP 的资源复制快数倍）。
* 每个操作都有超时，设备掉线不会把界面挂死。

安全设计（原版的教训）：
* 删除前强制校验"目标必须是 root 的直接子目录"，且不允许空名、`.`、`..`、含分隔符；
* 复制前校验目标目录不是源目录的父/子关系，避免源被删；
* 所有路径都用单引号包裹后交给 shell，避免空格/中文/特殊字符引发命令注入。
"""

from __future__ import annotations

import itertools
import os
import posixpath
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

from .adb import AdbBridge, AdbError, _quote
from .constants import BACKUP_SUFFIX, INFO_FILE_NAMES
from .fsapi import FSError, FSUnavailable, Location, ProgressFn, SongFS
from .logging_setup import get_logger
from .songs import SongMeta, decode_json_bytes, parse_info_file
from .storage import data_dir

logger = get_logger("adb_fs")

#: 推送暂存用的目录名（纯 ASCII，见 AdbFS.copy_into 的说明）
_STAGING_NAME = "song"
#: 暂存目录编号：同一个进程里连续推送时避免撞名
_STAGING_COUNTER = itertools.count(1)


def _safe_child_name(folder: str) -> str:
    """校验歌曲文件夹名，防止 `rm -rf` 打到别处。"""
    name = (folder or "").strip().strip("/")
    if not name or name in (".", ".."):
        raise FSError("非法文件夹名")
    if "/" in name or "\\" in name:
        raise FSError(f"文件夹名不能包含路径分隔符: {folder}")
    return name


class AdbFS(SongFS):
    """把 adb 包装成 SongFS。"""

    def __init__(self, location: Location, bridge: AdbBridge | None = None, adb_path: str = ""):
        super().__init__(location)
        self.bridge = bridge or AdbBridge(adb_path or None, serial=location.serial)
        self._folder_cache: list[str] | None = None
        self._lock = threading.Lock()

    # ---------------- 可用性 ----------------
    def available(self) -> bool:
        try:
            return self.bridge.exists(self.location.root) and self.bridge.is_dir(self.location.root)
        except AdbError:
            return False

    def ensure(self) -> None:
        if not self.bridge.available:
            raise FSUnavailable(
                "未找到 adb。请安装 Android Platform Tools 并加入 PATH，或在设置中指定 adb.exe。"
            )
        try:
            self.bridge.ensure_device()
        except AdbError as exc:
            raise FSUnavailable(str(exc)) from exc
        if not self.bridge.is_dir(self.location.root):
            raise FSUnavailable(f"设备上不存在该目录: {self.location.root}")

    # ---------------- 列表 ----------------
    def list_song_folders(self, progress: ProgressFn | None = None) -> list[str]:
        self.ensure()
        if progress:
            progress("正在读取设备目录…", 10)
        try:
            # 用 `cd <dir> && ls -1p`：比 `ls -1p <dir>` 兼容性更好
            # （toybox/GNU/coreutils 对目录参数的处理略有差异）
            quoted = "'" + self.location.root.rstrip("/").replace("'", "'\\''") + "'"
            output = self.bridge.shell(f"cd {quoted} && ls -1p 2>/dev/null", timeout=60, check=False)
            names = self._parse_ls_dirs(output)
        except AdbError as exc:
            raise FSUnavailable(f"读取设备目录失败: {exc}") from exc
        if names is None:
            names = self.bridge.list_dirs(self.location.root)
        # 过滤掉覆盖复制留下的旧版本备份 —— 它们不是歌曲
        backups = [n for n in names if n.endswith(BACKUP_SUFFIX)]
        if backups:
            logger.info("设备上有 %d 个旧版本备份目录（已从歌单隐藏）：%s",
                        len(backups), ", ".join(backups[:3]))
        folders = sorted(
            (n for n in names if n and not n.startswith(".") and not n.endswith(BACKUP_SUFFIX)),
            key=str.lower,
        )
        if progress:
            progress(f"设备上共 {len(folders)} 个目录", 30)
        self._folder_cache = folders
        return folders

    @staticmethod
    def _parse_ls_dirs(output: str) -> list[str] | None:
        """从 `ls -1p` 输出里取目录名；输出为空/异常时返回 None 让调用方换方式。"""
        if not output.strip():
            return None
        return [line[:-1] for line in output.splitlines() if line.rstrip("\r").endswith("/")]

    def _remote(self, folder: str, *parts: str) -> str:
        base = self.location.root.rstrip("/")
        return posixpath.join(base, _safe_child_name(folder), *parts)

    def list_files(self, folder: str) -> list[str]:
        """列出歌曲目录里的文件（不递归）。

        **不要**改回 `find ... -printf '%f\\t%T@\\t%y\\n'`（v14.3 修的就是这个）：
        真机 Quest 3 / Android 14 / toybox 0.8.9 上 **`%y` 是坏的** ——
            单独 `-printf '%y\\n'`      -> 输出 0 行（应为 4 行）
            组合 `'%f\\t%T@\\t%y\\n'`   -> 整个输出被截成 **1 行**
        （`%f` / `%p` / `%T@` / `%s` 单独或两两组合都正常，只有沾上 `%y` 就坏。）
        后果是**每个目录只返回 1 个文件**，而且因为结果非空，
        原先那条"为空才退回 ls"的兜底永远不会触发 —— 属于静默截断：
        `SongFS.cover_name()` 依赖这个方法，于是 Quest 歌曲的**封面会随机探测不到**。
        `ls -1p` 在真机上完全正常，所以固定用它。
        """
        try:
            return self.bridge.list_files(self._remote(folder))
        except AdbError as exc:
            raise FSError(f"无法列出 {folder}: {exc}") from exc

    def stat_mtime(self, folder: str) -> float:
        remote = self._remote(folder)
        for name in INFO_FILE_NAMES:
            mtime = self.bridge.mtime(f"{remote}/{name}")
            if mtime:
                return mtime
        return 0.0

    def stat_mtimes(self, folders: Sequence[str]) -> dict[str, float]:
        """一次 `find -printf` 拿全部 info.dat 的 mtime。

        **不要**退回逐首 `stat_mtime`：那在 adb 上是每首一次 shell 往返，
        真机 Quest 3 / 217 首实测 **17.6 秒**；这个方法只要 **0.20 秒（90 倍）**。
        `scan_metadata` 里的 find 也是同一个套路，两处格式保持一致。
        """
        self.ensure()
        wanted = list(folders)
        if not wanted:
            return {}
        names_pattern = " -o ".join(f"-name {n}" for n in INFO_FILE_NAMES)
        command = (
            f"find {_quote(self.location.root)} -mindepth 2 -maxdepth 2 -type f "
            f"\\( {names_pattern} \\) -printf '%p\\t%T@\\n' 2>/dev/null"
        )
        listing = self.bridge.shell(command, timeout=180, check=False)
        by_folder: dict[str, float] = {}
        for line in listing.splitlines():
            parts = line.rstrip("\r").split("\t")
            if len(parts) != 2:
                continue
            remote_path, mtime_text = parts
            folder = posixpath.basename(posixpath.dirname(remote_path))
            try:
                by_folder[folder] = float(mtime_text.split(".")[0])
            except ValueError:
                by_folder[folder] = 0.0
        return {folder: by_folder.get(folder, 0.0) for folder in wanted}

    def read_info_json(self, folder: str) -> dict | None:
        remote = self._remote(folder)
        for name in INFO_FILE_NAMES:
            try:
                raw = self.bridge.read_bytes(f"{remote}/{name}", timeout=30)
            except AdbError:
                continue
            if not raw:
                continue
            return _loads_json_bytes(raw)
        return None

    def read_duration(self, folder: str, data: dict | None = None) -> float:
        from .song_duration import audio_filename, duration_from_parts, HEAD_LIMIT, TAIL_LIMIT
        name = audio_filename(data or self.read_info_json(folder))
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            return 0.0
        try:
            path = _quote(self._remote(folder, name))
            head = self.bridge.shell_bytes(f"head -c {HEAD_LIMIT} {path}", timeout=15)
            tail = self.bridge.shell_bytes(f"tail -c {TAIL_LIMIT} {path}", timeout=15)
            return duration_from_parts(head[:HEAD_LIMIT], tail[-TAIL_LIMIT:])
        except (AdbError, ValueError):
            return 0.0

    def read_file(self, folder: str, relative: str) -> bytes:
        try:
            return self.bridge.read_bytes(self._remote(folder, relative), timeout=120)
        except AdbError as exc:
            raise FSError(f"读取设备文件失败: {exc}") from exc

    def local_folder_exists(self, folder: str) -> bool:
        try:
            return self.bridge.is_dir(self._remote(folder))
        except AdbError:
            return False

    # ---------------- 批量元数据（性能关键） ----------------
    def scan_metadata(
        self,
        folders: Sequence[str],
        progress: ProgressFn | None = None,
        cancel=None,
        workers: int = 4,
    ) -> dict[str, SongMeta]:
        self.ensure()
        folders = list(folders)
        if not folders:
            return {}

        if progress:
            progress("正在批量读取设备元数据…", 5)

        # 一次 shell 调用拿全部 info.dat 的路径 + mtime（避免逐首 stat）
        names_pattern = " -o ".join(f"-name {n}" for n in INFO_FILE_NAMES)
        command = (
            f"find '{self.location.root}' -mindepth 2 -maxdepth 2 -type f \\( {names_pattern} \\) "
            f"-printf '%p\\t%T@\\n' 2>/dev/null"
        )
        listing = self.bridge.shell(command, timeout=180, check=False)

        mtime_by_folder: dict[str, float] = {}
        paths: list[tuple[str, str]] = []  # (远端路径, folder)
        for line in listing.splitlines():
            parts = line.rstrip("\r").split("\t")
            if len(parts) != 2:
                continue
            remote_path, mtime_text = parts
            folder = posixpath.basename(posixpath.dirname(remote_path))
            try:
                mtime_by_folder[folder] = float(mtime_text.split(".")[0])
            except ValueError:
                mtime_by_folder[folder] = 0.0
            paths.append((remote_path, folder))

        if not paths:
            # find -printf 不可用：退化为逐首读取（慢，但能工作）
            logger.warning("设备 find 不支持 -printf，退化为逐首读取")
            return super().scan_metadata(folders, progress=progress, cancel=cancel, workers=workers)

        # 批量取回 info.dat 内容（tar 单流）
        chunks: list[list[tuple[str, str]]] = []
        chunk_size = 400
        for i in range(0, len(paths), chunk_size):
            chunks.append(paths[i : i + chunk_size])

        if progress:
            progress(f"发现 {len(paths)} 个 info.dat，开始批量下载…", 20)

        contents: dict[str, bytes] = {}
        done_chunks = 0
        chunk_lock = threading.Lock()

        def fetch(chunk: list[tuple[str, str]]) -> dict[str, bytes]:
            nonlocal done_chunks
            try:
                result = self.bridge.bulk_read_files(chunk, timeout=300)
            except AdbError as exc:
                logger.warning("批量读取分片失败: %s", exc)
                result = {}
            with chunk_lock:
                done_chunks += 1
                if progress:
                    percent = 20 + int(done_chunks / max(1, len(chunks)) * 70)
                    progress(f"读取设备元数据 {done_chunks}/{len(chunks)}", percent)
                return result

        if len(chunks) == 1:
            contents.update(fetch(chunks[0]))
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
                for part in pool.map(fetch, chunks):
                    contents.update(part)

        # 解析（本地 CPU 任务，用线程池并行）
        # 注意：bulk_read_files 返回的 key 就是这里传入的 folder（不是设备路径），
        # 所以直接用 folder 取值即可（曾误用设备路径查找，导致所有标题为空）。
        result: dict[str, SongMeta] = {}

        def build(folder: str) -> SongMeta:
            raw = contents.get(folder)
            data = _loads_json_bytes(raw) if raw else None
            return parse_info_file(data, folder, mtime_by_folder.get(folder, 0.0))

        with ThreadPoolExecutor(max_workers=8) as pool:
            for meta in pool.map(build, folders):
                result[meta.folder_name] = meta

        if progress:
            progress(f"设备元数据解析完成（{len(result)} 首）", 100)
        return result

    # ---------------- 传输 ----------------
    def copy_into(self, folder: str, local_src: Path, progress: ProgressFn | None = None) -> None:
        name = _safe_child_name(folder)
        if not local_src.is_dir():
            raise FSError(f"本地源目录不存在: {local_src}")
        remote_parent = self.location.root.rstrip("/")
        remote_target = posixpath.join(remote_parent, name)
        if self.bridge.exists(remote_target):
            self.bridge.remove_tree(remote_target)
        if progress:
            progress(f"正在推送到设备: {name}", 0)

        # adb.exe 会**从本地路径自己推导**设备上的目标名，而它在 Windows 上
        # 处理非 ASCII 路径有 bug（真机 Quest 3 实测）：路径里每有一个非 ASCII
        # 字符，设备上的名字就被截掉 1.5 个字符 ——
        #     …\音乐\www.vrmoo.net_1                    -> www.vrmoo.ne
        #     …\我的文档\音乐\BeatSaber\www.vrmoo.net_1  -> www.vr
        #     …\自定义歌曲目录\My Long Name               -> 自定义歌曲目录\My Very L
        # 后果有两个，都很严重：
        #   ① 歌曲落在错误的名字下，随即报"复制后未找到目标目录"；
        #   ② 截断若落在多字节字符中间，会产生**非法 UTF-8 文件名**，
        #      那种名字在 Android FUSE 上删不掉（rm / mv / find -delete 全部失效，
        #      实测只能靠重启设备清掉）。
        # 纯 ASCII 路径则完全正常（实测到 221 字符都没问题）。
        #
        # 而 `adb shell` 通道处理中文是正常的（实测 mv 到中文名结果正确），所以：
        #   ① 纯 ASCII 暂存 → ② push（名字必然正确）→ ③ shell mv 改成真实名字
        if str(local_src).isascii():
            staging_root: Path | None = None
            pushed_name = name
        else:
            staging_root, pushed_name = self._make_ascii_staging(local_src)

        try:
            self.bridge.push(
                (staging_root / pushed_name) if staging_root is not None else local_src,
                remote_parent,
            )
            if pushed_name != name:
                pushed_path = posixpath.join(remote_parent, pushed_name)
                try:
                    self.bridge.rename(pushed_path, remote_target)
                except AdbError as exc:
                    # 改名失败就别把暂存名那份留在设备上
                    self.bridge.remove_tree(pushed_path)
                    raise FSError(
                        f"设备端改名失败（{pushed_name} -> {name}）: {exc}"
                    ) from exc
        except AdbError as exc:
            raise FSError(f"adb push 失败: {exc}") from exc
        finally:
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)

        if not self.bridge.is_dir(remote_target):
            raise FSError(f"复制后未在设备上找到目标目录: {remote_target}")
        # 光"目录存在"不够 —— 必须比文件数与字节数（见 SongFS.verify_copy 的说明）
        self.verify_copy(name, local_src)
        if progress:
            progress(f"已复制 {name}", 100)

    def remote_file_stats(self, folder: str) -> tuple[int, int]:
        """设备端该目录的 (文件数, 总字节)，递归。

        用 `find <dir> -type f -printf '%s\\n'` —— 真机实测它与本地逐字节统计**完全一致**
        （`du -sk` 是块对齐的，会差几千字节，不能用）。
        注意 `%s` 是好的，`%y` 才是坏的（见 `list_files` 的说明）。
        """
        remote = self._remote(folder)
        out = self.bridge.shell(
            f"find {_quote(remote)} -type f -printf '%s\\n' 2>/dev/null",
            timeout=120,
            check=False,
        )
        sizes = [int(x) for x in out.splitlines() if x.strip().isdigit()]
        return len(sizes), sum(sizes)

    def _make_ascii_staging(self, local_src: Path) -> tuple[Path, str]:
        """把本地歌曲复制到一个**纯 ASCII** 路径下，返回 (暂存父目录, 目录名)。

        调用方负责在 `finally` 里删掉返回的暂存父目录。
        只在本地路径含非 ASCII 时才走这条路（见 `copy_into` 的说明）。
        """
        root = data_dir() / "push_staging"
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f"p{os.getpid()}_{next(_STAGING_COUNTER)}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        shutil.copytree(local_src, staging / _STAGING_NAME)
        return staging, _STAGING_NAME

    def copy_out(self, folder: str, local_dst: Path, progress: ProgressFn | None = None) -> None:
        remote = self._remote(folder)
        if not self.bridge.is_dir(remote):
            raise FSError(f"设备上不存在: {remote}")
        # 先拉到临时同级目录，再改名，保证不会留下半截目录
        staging = local_dst.parent / f".{local_dst.name}.partial"
        if staging.exists():
            import shutil

            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        if progress:
            progress(f"正在从设备拉取: {folder}", 0)
        try:
            self.bridge.pull(remote, staging)
        except AdbError as exc:
            raise FSError(f"adb pull 失败: {exc}") from exc
        pulled = staging / posixpath.basename(remote)
        if not pulled.is_dir():
            raise FSError(f"拉取结果异常: {pulled}")
        if local_dst.exists():
            import shutil

            shutil.rmtree(local_dst, ignore_errors=True)
        os.replace(pulled, local_dst)
        try:
            staging.rmdir()
        except OSError:
            pass
        if progress:
            progress(f"已复制 {folder} 到本地", 100)

    def delete_folder(self, folder: str) -> None:
        name = _safe_child_name(folder)
        remote = posixpath.join(self.location.root.rstrip("/"), name)
        # 双重护栏：路径必须是 root 的直接子目录
        if posixpath.dirname(remote) != self.location.root.rstrip("/"):
            raise FSError(f"拒绝删除非歌曲目录: {remote}")
        if not self.bridge.is_dir(remote):
            raise FSError(f"设备上不存在: {remote}")
        try:
            self.bridge.remove_tree(remote)
        except AdbError as exc:
            raise FSError(f"删除失败: {exc}") from exc
        if self.bridge.exists(remote):
            raise FSError(f"删除后目录仍然存在: {remote}")

    def free_space_bytes(self) -> int | None:
        return self.bridge.free_space(self.location.root)


def _loads_json_bytes(raw: bytes | None) -> dict | None:
    """解码并解析 info.dat：兼容 UTF-8 BOM 与 GBK（原版两种都会失败）。"""
    return decode_json_bytes(raw)


def adb_fs_from_location(location: Location, adb_path: str = "", **_ignored: object) -> AdbFS:
    return AdbFS(location, adb_path=adb_path)
