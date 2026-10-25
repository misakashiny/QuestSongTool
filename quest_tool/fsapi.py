"""统一文件系统抽象层（本地路径 / Quest 设备）。

这是本次重构的**核心**：原版所有代码都直接调用 `os.path.exists` / `os.listdir` /
`shutil.copytree`，这只对"有盘符的普通路径"成立。Quest 3 通过 USB 连接时是 **MTP 便携设备**，
Windows Explorer 里能看到、却没有盘符，任何 `os.*` 调用都会失败——这正是"文件夹选不了/选了没用"
的根因。把访问方式抽象成 Location 之后，UI 与业务逻辑完全不关心底层是本地磁盘还是设备。

约定：
* `list_song_folders()` 返回**文件夹名**列表（不是完整路径）。
* 其余方法都以"文件夹名"为参数，内部再拼成完整路径。
* 所有回调（progress）都可能在**工作线程**中被调用，调用方负责切回 UI 线程。
"""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .constants import INFO_FILE_NAMES, IMAGE_EXTENSIONS
from .logging_setup import get_logger
from .songs import SongMeta, load_info_file, parse_info_file

logger = get_logger("fs")

ProgressFn = Callable[[str, int], None]
CancelFn = Callable[[], bool]


class FSUnavailable(RuntimeError):
    """底层访问方式不可用（设备未连接、adb 缺失、路径不存在……）。"""


class FSError(RuntimeError):
    """文件操作失败。"""


@dataclass
class Location:
    """一个歌曲库位置：kind 决定用哪个后端，root 是后端自己的路径表示。"""

    kind: str  # "local" | "adb" | "mtp"
    root: str
    label: str = ""
    identity: str = ""  # 稳定的缓存键，例如 "local:D:\\Songs" 或 "adb:/sdcard/..."
    serial: str = ""  # 设备序列号（adb 用）
    shell_path: str = ""  # MTP 下的 Shell 命名空间路径（仅供浏览/显示）

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.root
        if not self.identity:
            self.identity = f"{self.kind}:{self.root}"

    def child(self, folder: str) -> "Location":
        """派生出一个子目录位置（用于直接打开某首歌）。"""
        sep = "/" if self.kind in ("adb", "mtp") else os.sep
        return Location(
            kind=self.kind,
            root=self.root.rstrip("/\\") + sep + folder,
            label=f"{self.label}/{folder}",
            identity=self.identity,
            serial=self.serial,
            shell_path=self.shell_path,
        )


# --------------------------------------------------------------------------------------
# 抽象基类
# --------------------------------------------------------------------------------------
def local_file_stats(path: Path) -> tuple[int, int]:
    """递归统计本地目录的 (文件数, 总字节)。

    用 `rglob` 而不是 `os.walk`：歌曲目录层级很浅，而且这样和
    设备端 `find <dir> -type f -printf '%s\\n'` 的口径一致（都递归、都只算普通文件）。
    """
    count = 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
                count += 1
        except OSError:
            continue
    return count, total


class SongFS(ABC):
    """歌曲库访问接口。"""

    def __init__(self, location: Location) -> None:
        self.location = location
        self._cover_index: dict[str, str] | None = None

    # ---------- 基本信息 ----------
    @abstractmethod
    def available(self) -> bool:
        """位置当前是否可访问。"""

    @abstractmethod
    def list_song_folders(self, progress: ProgressFn | None = None) -> list[str]:
        """列出所有歌曲文件夹名（已排序、已过滤隐藏目录）。"""

    # ---------- 元数据 ----------
    @abstractmethod
    def stat_mtime(self, folder: str) -> float:
        """info.dat 的修改时间；取不到返回 0（0 表示不进缓存）。"""

    def stat_mtimes(self, folders: Sequence[str]) -> dict[str, float]:
        """批量取 mtime，返回 {folder: mtime}（取不到的为 0.0）。

        **子类应该覆盖这个方法，把它做成"一次往返"**。默认实现是逐首调用，
        在 adb 上等于每首一次 shell 往返 —— 真机 Quest 3 / 217 首实测 **17.6 秒**，
        而一次 `find -printf` 拿全部只要 **0.20 秒（90 倍）**。
        更糟的是原先这个开销**没有任何收益**：缓存从来没被写过，所以每次都全量重解析
        （v14.5 一并修了写缓存与批量查缓存，见 `app.load_library`）。
        """
        result: dict[str, float] = {}
        for folder in folders:
            try:
                result[folder] = self.stat_mtime(folder)
            except (FSError, OSError):
                result[folder] = 0.0
        return result

    @abstractmethod
    def read_info_json(self, folder: str) -> dict | None:
        """读取并解析 info.dat，失败返回 None。"""

    def scan_metadata(
        self,
        folders: Sequence[str],
        progress: ProgressFn | None = None,
        cancel: CancelFn | None = None,
        workers: int = 8,
    ) -> dict[str, SongMeta]:
        """批量解析歌曲元数据。

        默认实现逐首解析（本地磁盘足够快）；远程后端会覆盖成批处理版本。
        """
        result: dict[str, SongMeta] = {}
        total = len(folders) or 1
        for i, folder in enumerate(folders, 1):
            if cancel and cancel():
                break
            try:
                mtime = self.stat_mtime(folder)
                data = self.read_info_json(folder)
                result[folder] = parse_info_file(data, folder, mtime, self.cover_name(folder, data))
                if result[folder].duration <= 0:
                    result[folder].duration = self.read_duration(folder, data)
            except Exception as exc:  # 单首失败不能拖垮整次扫描
                logger.warning("解析歌曲失败 %s: %s", folder, exc)
                result[folder] = SongMeta(folder_name=folder)
            if progress and (i % 10 == 0 or i == len(folders)):
                progress(f"解析歌曲信息 {i}/{len(folders)}", int(i / total * 100))
        return result

    def read_duration(self, folder: str, data: dict | None = None) -> float:
        return 0.0

    def cover_name(self, folder: str, data: dict | None = None) -> str:
        """返回封面文件名（相对于歌曲文件夹），找不到返回 ""。"""
        if data:
            for key in ("_coverImageFilename", "coverImageFilename"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
        index = self._cover_index_for(folder)
        return index

    def read_cover_bytes(self, folder: str) -> bytes | None:
        """读取封面图片字节流；没有封面返回 None。"""
        name = self._cover_index_for(folder)
        if not name:
            return None
        try:
            return self.read_file(folder, name)
        except FSError:
            return None

    # ---------- 文件读写 ----------
    @abstractmethod
    def read_file(self, folder: str, relative: str) -> bytes:
        """读取歌曲文件夹内的文件内容。"""

    @abstractmethod
    def local_folder_exists(self, folder: str) -> bool:
        """该歌曲文件夹当前是否真实存在。"""

    # ---------- 传输 ----------
    @abstractmethod
    def copy_into(self, folder: str, local_src: Path, progress: ProgressFn | None = None) -> None:
        """把本地目录 local_src 的内容复制到设备的 <root>/<folder>。"""

    @abstractmethod
    def copy_out(self, folder: str, local_dst: Path, progress: ProgressFn | None = None) -> None:
        """把设备的 <root>/<folder> 复制到本地目录 local_dst。"""

    @abstractmethod
    def delete_folder(self, folder: str) -> None:
        """删除整首歌曲文件夹。"""

    @abstractmethod
    def free_space_bytes(self) -> int | None:
        """目标剩余空间；未知返回 None。"""

    # ---------- 内部工具 ----------
    def _cover_index_for(self, folder: str) -> str:
        """在歌曲文件夹里找图片文件（大小写不敏感，按常见命名优先）。"""
        if self._cover_index is None:
            self._cover_index = {}
        if folder in self._cover_index:
            return self._cover_index[folder]
        name = ""
        try:
            names = self.list_files(folder)
        except FSError:
            names = []
        lowered = {n.lower(): n for n in names}
        for candidate in ("cover.png", "cover.jpg", "cover.jpeg", "cover.webp", "cover.gif"):
            if candidate in lowered:
                name = lowered[candidate]
                break
        else:
            for original in names:
                if original.lower().endswith(IMAGE_EXTENSIONS):
                    name = original
                    break
        self._cover_index[folder] = name
        return name

    @abstractmethod
    def list_files(self, folder: str) -> list[str]:
        """列出歌曲文件夹内的文件名（不递归）。"""

    # ---------------- 复制后校验 ----------------
    @abstractmethod
    def remote_file_stats(self, folder: str) -> tuple[int, int | None]:
        """返回该歌曲目录在**目标端**的 (文件数, 总字节)。

        总字节允许为 `None` —— MTP 拿不到字节：Shell 的"大小"列是**四舍五入后**的
        字符串（实测 `3.00 MB` / `1.20 KB`），而 MTP 又读不到内容。
        这种情况下 `verify_copy` 会退化成"比顶层文件数"（见下）。
        """

    def verify_copy(self, folder: str, local_src: Path) -> None:
        """校验复制结果：目标端的内容必须与本地源一致。

        为什么非要有这一步（v14.5 补的）：真机上出现过多次**静默传坏**，
        而原先只校验"目标目录存在"，全都发现不了 ——
          * `adb push` 在非 ASCII 路径下把设备上的目录名截断：目标目录压根不存在，
            报的却是"未找到目标目录"，把方向带偏；
          * 真机 toybox 的 `find -printf '%y'` 坏掉：`list_files` 每个目录只返回 1 个
            文件，界面上的"文件齐全"检查形同虚设；
          * MTP 的 `CopyHere` 遇到同名冲突会**静默跳过文件**，用户以为更新了其实没有。

        能拿到字节时比 (文件数, 总字节)；拿不到时（MTP）退化成比顶层文件数。
        """
        remote_count, remote_bytes = self.remote_file_stats(folder)
        if remote_bytes is None:
            local_count = len([p for p in local_src.iterdir() if p.is_file()])
            if local_count != remote_count:
                raise FSError(
                    f"复制后校验不通过（{folder}）：本地 {local_count} 个文件，"
                    f"目标端只有 {remote_count} 个。"
                    "这次复制没有传全，请重新复制。"
                )
            return
        local_count, local_bytes = local_file_stats(local_src)
        if (local_count, local_bytes) != (remote_count, remote_bytes):
            raise FSError(
                f"复制后校验不通过（{folder}）：本地 {local_count} 个文件 / {local_bytes} 字节，"
                f"目标端 {remote_count} 个文件 / {remote_bytes} 字节。"
                "这次复制没有传全，请重新复制；若反复失败请换一种连接方式"
                "（adb ↔ 本地镜像目录）再试。"
            )

    def close(self) -> None:
        """释放资源（默认无操作）。"""


# --------------------------------------------------------------------------------------
# 本地磁盘实现
# --------------------------------------------------------------------------------------
class LocalFS(SongFS):
    """普通盘符路径 / UNC 路径。"""

    def read_duration(self, folder: str, data: dict | None = None) -> float:
        from .song_duration import local_song_duration
        return local_song_duration(self._folder_path(folder), data or self.read_info_json(folder))

    @property
    def root_path(self) -> Path:
        return Path(self.location.root)

    def available(self) -> bool:
        return self.root_path.is_dir()

    def list_song_folders(self, progress: ProgressFn | None = None) -> list[str]:
        root = self.root_path
        if not root.is_dir():
            raise FSUnavailable(f"目录不存在或不可访问: {root}")
        folders: list[str] = []
        try:
            with os.scandir(root) as it:
                entries = [e for e in it]
        except OSError as exc:
            raise FSUnavailable(f"无法读取目录 {root}: {exc}") from exc

        total = len(entries) or 1
        for i, entry in enumerate(entries, 1):
            try:
                if entry.is_dir() and not entry.name.startswith("."):
                    folders.append(entry.name)
            except OSError:
                continue
            if progress and (i % 50 == 0 or i == total):
                progress(f"扫描 {entry.name[:24]}", int(i / total * 100))
        folders.sort(key=str.lower)
        return folders

    def _folder_path(self, folder: str) -> Path:
        return self.root_path / folder

    def list_files(self, folder: str) -> list[str]:
        try:
            with os.scandir(self._folder_path(folder)) as it:
                return [e.name for e in it]
        except OSError as exc:
            raise FSError(f"无法列出 {folder}: {exc}") from exc

    def remote_file_stats(self, folder: str) -> tuple[int, int]:
        path = self._folder_path(folder)
        if not path.is_dir():
            raise FSError(f"目标目录不存在: {path}")
        return local_file_stats(path)

    def stat_mtime(self, folder: str) -> float:
        path = self._folder_path(folder)
        for name in INFO_FILE_NAMES:
            candidate = path / name
            try:
                if candidate.is_file():
                    return candidate.stat().st_mtime
            except OSError:
                continue
        # 没有 info.dat：用目录本身的 mtime，仍然让缓存能失效
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def read_info_json(self, folder: str) -> dict | None:
        path = self._folder_path(folder)
        for name in INFO_FILE_NAMES:
            candidate = path / name
            if not candidate.is_file():
                continue
            # 用 songs.load_info_file：多编码兼容（UTF-8 / BOM / GBK），
            # 原版这里 write/read 用 utf-8 硬编码，BOM 与 GBK 文件都会失败。
            data = load_info_file(candidate)
            if data is not None:
                return data
            logger.warning("解析 info.dat 失败（已尝试多种编码）: %s", candidate)
        return None

    def read_file(self, folder: str, relative: str) -> bytes:
        path = self._folder_path(folder) / relative
        try:
            return path.read_bytes()
        except OSError as exc:
            raise FSError(f"读取文件失败 {path}: {exc}") from exc

    def local_folder_exists(self, folder: str) -> bool:
        return self._folder_path(folder).is_dir()

    def copy_into(self, folder: str, local_src: Path, progress: ProgressFn | None = None) -> None:
        dst = self._folder_path(folder)
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(local_src, dst)
        # 本地->本地也校验：磁盘写满 / 中断都可能留下半截目录
        self.verify_copy(folder, local_src)
        if progress:
            progress(f"已复制 {folder}", 100)

    def copy_out(self, folder: str, local_dst: Path, progress: ProgressFn | None = None) -> None:
        if local_dst.exists():
            shutil.rmtree(local_dst, ignore_errors=True)
        shutil.copytree(self._folder_path(folder), local_dst)
        if progress:
            progress(f"已复制 {folder}", 100)

    def delete_folder(self, folder: str) -> None:
        target = self._folder_path(folder)
        # 安全护栏：只能删除 root 的直接子目录
        try:
            if target.parent.resolve() != self.root_path.resolve():
                raise FSError(f"拒绝删除非歌曲目录: {target}")
        except OSError as exc:
            raise FSError(f"路径校验失败: {exc}") from exc
        if not target.is_dir():
            raise FSError(f"目录不存在: {target}")
        shutil.rmtree(target)

    def free_space_bytes(self) -> int | None:
        try:
            return shutil.disk_usage(self.root_path).free
        except OSError:
            return None


# --------------------------------------------------------------------------------------
# 工厂
# --------------------------------------------------------------------------------------
def open_fs(location: Location, **kwargs: object) -> SongFS:
    """根据位置类型创建后端。adb/mtp 后端在此处延迟导入，避免强依赖。"""
    if location.kind == "local":
        return LocalFS(location)
    if location.kind == "adb":
        from .device_adb import adb_fs_from_location

        return adb_fs_from_location(location, **kwargs)  # type: ignore[arg-type]
    if location.kind == "mtp":
        from .device_mtp import mtp_fs_from_location

        return mtp_fs_from_location(location, **kwargs)  # type: ignore[arg-type]
    raise FSUnavailable(f"未知的位置类型: {location.kind}")


def is_selector_like(path: str) -> bool:
    """判断一个字符串是否是 Windows Shell 命名空间路径（MTP 设备专用）。

    形如 `::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\...` 或 `此电脑\\Quest 3\\...`，
    这种路径 **不能** 交给 os/shutil 使用。
    """
    if not path:
        return False
    if path.startswith("::"):
        return True
    head = path.split("\\", 1)[0].split("/", 1)[0]
    return head in ("此电脑", "This PC", "计算机", "網路", "Network", "网络")


def normalize_user_path(raw: str) -> str:
    """清理用户手工粘贴的路径：去引号、去首尾空白、统一分隔符。"""
    text = (raw or "").strip().strip('"').strip("'")
    if len(text) > 1 and text[1] == ":":
        text = text[0].upper() + text[1:]
    return text
