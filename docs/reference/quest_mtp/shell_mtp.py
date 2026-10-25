"""shell_mtp.py —— 通过 Windows Shell COM 枚举便携设备（MTP）文件夹树。

背景
----
MTP 便携设备（例如 Meta Quest 3）在 Windows 上 **没有盘符**，只以 Shell
命名空间对象的形式出现在「此电脑」下面。因此：

* ``os.listdir()`` / ``os.path.exists()`` / ``open()`` / ``shutil.copy()`` 全部无效；
* ``tkinter.filedialog.askdirectory()`` 也无法从中选出一个文件夹（详见
  ``说明_MTP文件夹选择.md``）。

本模块用两种方式访问同一套 Shell 命名空间：

1. **首选**：``pywin32`` 的 ``win32com.client.Dispatch("Shell.Application")``
   （代码最直观，等价于 ``Shell.NameSpace(17)`` = ssfDRIVES，
   再用 ``NameSpace(item)`` 递归、``Folder.ParseName``、``FolderItem.Path``）。
2. **回退**：纯 ``ctypes`` 直接调用 Shell COM（``IShellFolder`` / ``IEnumIDList``）。
   不需要安装任何第三方库，只需要 ``ole32.dll`` / ``shell32.dll``。
   本机实测（Windows 11 + Python 3.13）可用。

两种方式对每个节点都同时返回：

* ``display``：人类可读的路径（用于界面显示、写进配置）
* ``shell_path``：Shell 解析名（``item.Path``），形如
  ``::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\Quest 3\\内部共享存储空间\\...``
  —— **这个字符串不能交给 os / shutil 使用**，只能交回 Shell COM 或 Explorer。

对外主要接口
------------
* :func:`list_roots` —— 列出「此电脑」下的所有根节点（本地盘 + 便携设备）
* :func:`list_portable_devices` —— 只列出便携设备
* :func:`list_children` —— 列出某个节点的直接子项（懒加载，界面用它防卡死）
* :func:`to_shell_path` —— 把文件系统路径或混合路径规范化成 Shell 解析名
* :func:`is_portable_device` —— 猜测某个解析名是否属于便携设备
* :func:`find_customlevels` —— 在便携设备里找 CustomLevels
* :func:`selftest` —— 自检，打印本机情况
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SHELL_PC_GUID = "{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
"""「此电脑」/ ssfDRIVES 的 CLSID。Shell.NameSpace(17) 与它是同一个东西。"""

THIS_PC_PARSING = "::" + SHELL_PC_GUID
"""「此电脑」的 Shell 解析名。"""

#: Beat Saber 自定义歌曲目录在 Quest 上的相对路径
QUEST_CUSTOMLEVELS_REL = os.path.join(
    "Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"
)

#: 便携设备内部存储常见的挂载名（中英文）
PORTABLE_STORAGE_NAMES = (
    "内部共享存储空间",
    "内部存储",
    "Internal shared storage",
    "Internal storage",
    "内部存储空间",
)

#: 设备名可能包含的关键字
PORTABLE_DEVICE_HINTS = ("quest", "oculus", "meta", "android", "pico", "vive", "手机", "phone")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ShellNode:
    """Shell 命名空间中的一个节点（设备 / 文件夹 / 文件）。

    注意：``shell_path`` 是 Shell 解析名，**不能**用于 os / shutil。
    """

    display: str
    """人类可读的完整显示路径，例如
    ``此电脑\\Quest 3\\内部共享存储空间\\Android``。"""

    shell_path: str
    """Shell 解析名（= ``FolderItem.Path`` / ``IShellItem`` 的
    SIGDN_DESKTOPABSOLUTEPARSING），例如
    ``::{20D04FE0-...}\\Quest 3\\内部共享存储空间``。"""

    name: str = ""
    """本节点的名字（最后一段）。"""

    is_folder: bool = True
    """是否为文件夹（可以继续展开）。"""

    fs_path: str | None = None
    """如果该节点恰好同时存在于文件系统中，这里是真实路径；否则为 ``None``。
    便携设备上的节点恒为 ``None``。"""

    kind: str = "unknown"
    """``"drive"`` / ``"mtp"`` / ``"folder"`` / ``"file"`` / ``"special"``。"""

    backend: str = ""
    """产出该节点的后端：``"pywin32"`` 或 ``"ctypes"``。"""

    @property
    def usable(self) -> bool:
        """是否可以直接用 os / open / shutil 操作（只有真实盘符路径才行）。"""
        return bool(self.fs_path) and os.path.isdir(self.fs_path or "")

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"ShellNode({self.kind}, {self.display!r}, usable={self.usable})"


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------


def is_shell_parsing_name(path: str) -> bool:
    """判断字符串是否是 Shell 命名空间解析名（而非文件系统路径）。

    便携设备上的路径都是解析名，例如
    ``::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\Quest 3\\内部共享存储空间``。
    这类字符串 **不能** 交给 ``os`` / ``open`` / ``shutil``。

    Examples:
        >>> is_shell_parsing_name("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\\\Quest 3")
        True
        >>> is_shell_parsing_name("C:\\\\Windows")
        False
    """
    if not path:
        return False
    if path.startswith("::"):
        return True
    low = path.lower()
    if low.startswith("\\\\?\\usb#"):
        return True
    return "usb#vid_" in low and "mi_" in low


#: 向后兼容的私有别名
_is_shell_parsing_name = is_shell_parsing_name


def to_shell_path(path: str) -> str:
    """把路径规范化成 Shell 解析名（可用于 ``Shell.NameSpace()``）。

    支持三种输入：

    * 普通文件系统路径：``C:\\Windows`` -> 原样返回（去掉尾部反斜杠）
    * 便携设备的「友好路径」：``Quest 3\\内部共享存储空间\\Android``
      -> ``::{20D04FE0-...}\\Quest 3\\内部共享存储空间\\Android``
    * 已经是 Shell 解析名的：原样返回

    Args:
        path: 待转换的路径。

    Returns:
        规范化后的 Shell 解析名字符串。

    Examples:
        >>> to_shell_path("C:/Windows/")
        'C:\\\\Windows'
        >>> to_shell_path("Quest 3/内部共享存储空间")[:2]
        '::'
    """
    if not path:
        return ""
    p = str(path).strip().strip('"').replace("/", "\\")
    while len(p) > 3 and p.endswith("\\"):
        p = p[:-1]
    if not p:
        return ""

    # 已经是 Shell 解析名
    if _is_shell_parsing_name(p):
        return p

    # 真实盘符 / UNC 路径
    if len(p) >= 2 and p[1] == ":":
        return p
    if p.startswith("\\\\"):
        return p

    # 形如 "此电脑\Quest 3\..." 或 "Quest 3\内部共享存储空间\..."
    parts = [seg for seg in p.split("\\") if seg]
    if parts and parts[0] in ("此电脑", "This PC", "计算机", "Computer"):
        parts = parts[1:]
    if not parts:
        return THIS_PC_PARSING
    return THIS_PC_PARSING + "\\" + "\\".join(parts)


def to_friendly_path(shell_path: str) -> str:
    """把 Shell 解析名转成「友好路径」字符串（去掉 ``::{GUID}`` 前缀）。

    友好路径只是给人看 / 存配置用的，**不能**给 os 使用。
    """
    if not shell_path:
        return ""
    p = shell_path
    if p.startswith("::" + SHELL_PC_GUID):
        p = p[len("::" + SHELL_PC_GUID):].lstrip("\\")
        return p or "此电脑"
    return p


def is_portable_device(shell_path: str, fs_path: str | None = None) -> bool:
    """猜测一个 Shell 解析名是否指向 MTP 便携设备。

    判据（任意一条成立即认为是便携设备）：

    * 解析名以 ``::{`` 或 ``\\\\?\\usb#`` 开头（没有盘符的 Shell 命名空间）
    * 解析名里含 ``usb#vid_``
    * 提供了 ``fs_path`` 但为空 / 不是有效的盘符路径

    Args:
        shell_path: Shell 解析名。
        fs_path: 对应的文件系统路径（便携设备上为 ``None``）。

    Returns:
        是否像是便携设备。
    """
    if _is_shell_parsing_name(shell_path):
        return True
    if fs_path is not None and not fs_path:
        return True
    return False


def _looks_like_portable_name(name: str) -> bool:
    low = (name or "").lower()
    return any(h in low for h in PORTABLE_DEVICE_HINTS)


# ---------------------------------------------------------------------------
# 后端 A：纯 ctypes Shell COM（无需第三方库）
# ---------------------------------------------------------------------------

_IS_WINDOWS = sys.platform == "win32"

#: WPD / Shell 在设备忙时返回的错误码。官方建议 **重试**，不要当成硬错误。
#:
#: * ``0x800700AA`` ``ERROR_BUSY`` —— 驱动正忙（资源管理器可能在刷新设备内容）
#: * ``0x80070964`` ``ERROR_DEVICE_IN_USE`` —— 初始化时设备被别的进程占用
#: * ``0x802A0001`` ``E_WPD_DEVICE_ALREADY_OPENED`` —— 同一接口对象重复 Open
#:
#: 注意：**不存在** ``WPD_E_DEVICE_BUSY`` 这个常量；
#: ``0x800420xx`` 是 MTP 的响应码区间（如 ``0x8004201E`` = Session Already Open），
#: 不是「设备忙」。Windows 上 MTP 不是单会话独占锁——资源管理器和本程序
#: 可以同时连接，冲突表现为可重试的 ``ERROR_BUSY``。
#: （单会话独占是 macOS / Linux 上 libmtp 的行为。）
TRANSIENT_HRESULTS = frozenset({
    0x800700AA,  # ERROR_BUSY
    0x80070964,  # ERROR_DEVICE_IN_USE
    0x802A0001,  # E_WPD_DEVICE_ALREADY_OPENED
    0x8004201E,  # MTP Session Already Open
    0x8007001F,  # ERROR_GEN_FAILURE（设备热插拔中途常见）
})


def is_transient_hresult(hr: int) -> bool:
    """该 HRESULT 是否属于「设备忙，稍后重试即可」的临时错误。"""
    return (hr & 0xFFFFFFFF) in TRANSIENT_HRESULTS


class _CtypesShell:
    """纯 ctypes 实现的 Shell 命名空间枚举器。

    只使用 ``ole32.dll`` / ``shell32.dll``。所有指针都以 **整数** 形式传递，
    避免 ctypes 自动类型转换带来的问题；vtable 调用统一走 :meth:`_call`。

    实现要点（踩过的坑，勿改）：

    * ``IShellFolder::BindToObject`` 的 ``pbc`` 参数 **不能为 NULL**，
      必须传一个真实的 ``IBindCtx``，否则会崩进程（访问冲突）。
    * ``riid`` 参数也不能为 NULL，必须显式传 ``IID_IShellFolder``。
    * vtable 槽位必须用定长指针类型读取（``c_void_p.from_address``），
      用 ``POINTER(c_void_p)`` 索引会把低 32 位为 0 的地址读成 ``None``。
    * ``IShellItem``（``SHCreateItemFromParsingName`` 返回的那个来自
      ``Windows.Storage.dll``）**不支持** ``BHID_EnumItems``，
      所以枚举必须走 ``IShellFolder``。
    """

    # ---- IShellFolder 方法在 vtable 中的下标 ----
    _SF_ENUM_OBJECTS = 4
    _SF_BIND_TO_OBJECT = 5
    _SF_GET_ATTRIBUTES_OF = 9
    _SF_GET_DISPLAY_NAME_OF = 11
    # ---- IEnumIDList ----
    _ENUM_NEXT = 3
    _RELEASE = 2

    CSIDL_DRIVES = 17
    SHCONTF_FOLDERS = 0x20
    SHCONTF_NONFOLDERS = 0x40
    SHCONTF_INCLUDEHIDDEN = 0x80
    SHGDN_NORMAL = 0x0000
    SHGDN_FORPARSING = 0x8000
    COINIT_APARTMENTTHREADED = 0x2

    #: ``SFGAO_FOLDER`` —— 该属性位为 1 才是文件夹。
    #: 注意：**不能**用 ``BindToObject`` 是否成功来判断是不是文件夹，
    #: 因为对文件调用 ``BindToObject`` 也会成功（返回的是父级视图）。
    SFGAO_FOLDER = 0x20000000
    #: ``SFGAO_FILESYSTEM`` —— 有真实文件系统路径（便携设备项目为 0）。
    SFGAO_FILESYSTEM = 0x40000000
    #: ``SFGAO_STREAM`` —— 容器（zip 等），可展开但不是普通文件夹。
    SFGAO_STREAM = 0x00400000
    #: ``SFGAO_HASSUBFOLDER`` —— 有子文件夹（便携设备/特殊命名空间项目常有此位而
    #: 没有 ``SFGAO_FOLDER``，所以判断「能不能展开」时要一并考虑）。
    SFGAO_HASSUBFOLDER = 0x80000000
    #: ``SFGAO_STORAGE`` / ``SFGAO_STORAGEANCESTOR`` —— 存储卷相关。
    SFGAO_STORAGE = 0x00000008
    SFGAO_STORAGEANCESTOR = 0x00800000
    #: 一次性查询的属性集合（Windows 会忽略不认识的位）
    SFGAO_QUERY_MASK = (
        SFGAO_FOLDER | SFGAO_FILESYSTEM | SFGAO_STREAM
        | SFGAO_HASSUBFOLDER | SFGAO_STORAGE | SFGAO_STORAGEANCESTOR
    )

    def __init__(self) -> None:
        import ctypes  # 局部导入：非 Windows 上不应在导入期就失败

        self._ctypes = ctypes
        self._ole32 = ctypes.WinDLL("ole32")
        self._shell32 = ctypes.WinDLL("shell32")
        self._hr = ctypes.c_long
        self._pv = ctypes.c_void_p
        self._ul = ctypes.c_ulong
        self._hwnd = ctypes.c_void_p
        self._co_initialized = False
        self._bind_ctx = 0
        self._proto_cache: dict[tuple[int, tuple], Any] = {}
        self._configure_signatures()
        self._iid_shellfolder = self._guid("{000214E6-0000-0000-C000-000000000046}")

    # -- 初始化 ---------------------------------------------------------

    def _configure_signatures(self) -> None:
        ct = self._ctypes
        hr, pv, ul, hwnd = self._hr, self._pv, self._ul, self._hwnd
        for fn, argtypes, restype in (
            (self._ole32.CoInitializeEx, [pv, ul], hr),
            (self._ole32.CoUninitialize, [], None),
            (self._ole32.IIDFromString, [ct.c_wchar_p, pv], hr),
            (self._ole32.CreateBindCtx, [ul, pv], hr),
            (self._ole32.CoTaskMemFree, [pv], None),
            (self._shell32.SHGetDesktopFolder, [pv], hr),
            (self._shell32.SHGetSpecialFolderLocation, [hwnd, ct.c_int, pv], hr),
            (self._shell32.SHParseDisplayName, [ct.c_wchar_p, pv, pv, ul, pv], hr),
        ):
            fn.argtypes = argtypes
            fn.restype = restype

    def _guid(self, s: str) -> Any:
        ct = self._ctypes

        class GUID(ct.Structure):
            _fields_ = [
                ("Data1", ct.c_ulong),
                ("Data2", ct.c_ushort),
                ("Data3", ct.c_ushort),
                ("Data4", ct.c_ubyte * 8),
            ]

        g = GUID()
        if self._ole32.IIDFromString(s, ct.addressof(g)) != 0:
            raise OSError(f"无效的 GUID: {s!r}")
        return g

    def open(self) -> None:
        """初始化 COM（幂等）。"""
        import ctypes

        if not self._co_initialized:
            hr = self._ole32.CoInitializeEx(None, self.COINIT_APARTMENTTHREADED)
            # S_OK=0, S_FALSE=1, RPC_E_CHANGED_MODE=0x80010106（已被别处初始化过，可接受）
            if hr not in (0, 1) and (hr & 0xFFFFFFFF) != 0x80010106:
                raise OSError(f"CoInitializeEx 失败 hr=0x{hr & 0xFFFFFFFF:08X}")
            self._co_initialized = True
        if not self._bind_ctx:
            bc = ctypes.c_void_p()
            hr = self._ole32.CreateBindCtx(0, ctypes.addressof(bc))
            if hr != 0 or not bc.value:
                raise OSError(f"CreateBindCtx 失败 hr=0x{hr & 0xFFFFFFFF:08X}")
            self._bind_ctx = bc.value

    def close(self) -> None:
        """释放 COM（幂等）。"""
        if self._co_initialized:
            try:
                self._ole32.CoUninitialize()
            except Exception:
                pass
            self._co_initialized = False

    # -- 低层 vtable 调用 ------------------------------------------------

    def _slot(self, addr: int, index: int) -> int:
        ct = self._ctypes
        vtable = ct.c_void_p.from_address(addr).value
        if not vtable:
            raise OSError("COM 对象指针无效")
        return ct.c_void_p.from_address(vtable + index * ct.sizeof(ct.c_void_p)).value

    def _call(self, addr: int, index: int, argtypes: tuple, *args) -> int:
        ct = self._ctypes
        proto = self._proto_cache.get((index, argtypes))
        if proto is None:
            proto = ct.WINFUNCTYPE(self._hr, self._pv, *argtypes)
            self._proto_cache[(index, argtypes)] = proto
        fn = proto(self._slot(addr, index))
        return fn(addr, *args) & 0xFFFFFFFF

    def _release(self, addr: int) -> None:
        if addr:
            try:
                self._call(addr, self._RELEASE, ())
            except Exception:
                pass

    # -- Shell 操作 ------------------------------------------------------

    def drives_folder(self) -> int:
        """返回「此电脑」(ssfDRIVES) 的 ``IShellFolder*``（调用方负责 Release）。"""
        import ctypes

        self.open()
        desktop = ctypes.c_void_p()
        hr = self._shell32.SHGetDesktopFolder(ctypes.addressof(desktop))
        if hr != 0 or not desktop.value:
            raise OSError(f"SHGetDesktopFolder 失败 hr=0x{hr & 0xFFFFFFFF:08X}")
        pidl = ctypes.c_void_p()
        hr = self._shell32.SHGetSpecialFolderLocation(
            None, self.CSIDL_DRIVES, ctypes.addressof(pidl)
        )
        if hr != 0 or not pidl.value:
            self._release(desktop.value)
            raise OSError(f"SHGetSpecialFolderLocation(ssfDRIVES) 失败 hr=0x{hr & 0xFFFFFFFF:08X}")
        out = ctypes.c_void_p()
        try:
            hr = self._call(
                desktop.value,
                self._SF_BIND_TO_OBJECT,
                (self._pv, self._pv, self._pv, self._pv),
                pidl.value,
                self._bind_ctx,
                ctypes.addressof(self._iid_shellfolder),
                ctypes.addressof(out),
            )
        finally:
            self._release(desktop.value)
            self._ole32.CoTaskMemFree(pidl.value)
        if hr != 0 or not out.value:
            raise OSError(f"BindToObject(此电脑) 失败 hr=0x{hr:08X}")
        return out.value

    def folder_from_shell_path(self, shell_path: str) -> int:
        """由 Shell 解析名得到对应文件夹的 ``IShellFolder*``（调用方负责 Release）。

        注意：便携设备上的项目用 ``SHParseDisplayName`` 也能解析。
        """
        import ctypes

        self.open()
        pidl = ctypes.c_void_p()
        eaten = ctypes.c_ulong(0)
        hr = self._shell32.SHParseDisplayName(
            shell_path, self._bind_ctx, ctypes.addressof(pidl), 0, ctypes.addressof(eaten)
        )
        if hr != 0 or not pidl.value:
            raise OSError(f"无法解析 Shell 路径 {shell_path!r} hr=0x{hr & 0xFFFFFFFF:08X}")
        desktop = ctypes.c_void_p()
        hr = self._shell32.SHGetDesktopFolder(ctypes.addressof(desktop))
        if hr != 0 or not desktop.value:
            self._ole32.CoTaskMemFree(pidl.value)
            raise OSError(f"SHGetDesktopFolder 失败 hr=0x{hr & 0xFFFFFFFF:08X}")
        out = ctypes.c_void_p()
        try:
            hr = self._call(
                desktop.value,
                self._SF_BIND_TO_OBJECT,
                (self._pv, self._pv, self._pv, self._pv),
                pidl.value,
                self._bind_ctx,
                ctypes.addressof(self._iid_shellfolder),
                ctypes.addressof(out),
            )
        finally:
            self._release(desktop.value)
            self._ole32.CoTaskMemFree(pidl.value)
        if hr != 0 or not out.value:
            raise OSError(f"无法打开文件夹 {shell_path!r} hr=0x{hr:08X}")
        return out.value

    def list_children(self, shell_path: str) -> list[ShellNode]:
        """列出 ``shell_path`` 文件夹的直接子项。

        Args:
            shell_path: Shell 解析名，或 ``""`` / ``"此电脑"`` 代表「此电脑」。

        Returns:
            :class:`ShellNode` 列表。不可枚举时返回空列表（不抛异常）。
        """
        import ctypes

        if not shell_path or shell_path in ("此电脑", "This PC", THIS_PC_PARSING):
            folder = self.drives_folder()
        else:
            folder = self.folder_from_shell_path(to_shell_path(shell_path))
        try:
            return self._list_folder(folder, shell_path or THIS_PC_PARSING)
        finally:
            self._release(folder)

    def _list_folder(self, folder: int, parent_shell_path: str) -> list[ShellNode]:
        import ctypes

        enum_raw = ctypes.c_void_p()
        flags = self.SHCONTF_FOLDERS | self.SHCONTF_NONFOLDERS | self.SHCONTF_INCLUDEHIDDEN
        hr = self._call(
            folder, self._SF_ENUM_OBJECTS, (self._hwnd, self._ul, self._pv),
            None, flags, ctypes.addressof(enum_raw),
        )
        if hr != 0 or not enum_raw.value:
            return []
        enum = enum_raw.value
        nodes: list[ShellNode] = []
        try:
            child = ctypes.c_void_p()
            fetched = ctypes.c_ulong(0)
            while True:
                child.value = None
                fetched.value = 0
                hr = self._call(
                    enum, self._ENUM_NEXT, (self._ul, self._pv, self._pv),
                    1, ctypes.addressof(child), ctypes.addressof(fetched),
                )
                if hr != 0 or fetched.value == 0 or not child.value:
                    break
                pidl = child.value
                try:
                    name = self._display_name(folder, pidl, self.SHGDN_NORMAL)
                    parse = self._display_name(folder, pidl, self.SHGDN_FORPARSING)
                    attrs = self._attributes_of(folder, pidl)
                    is_folder = self._is_container(attrs)
                    nodes.append(
                        self._make_node(
                            name=name or "?",
                            parse=parse,
                            is_folder=is_folder,
                            parent_shell_path=parent_shell_path,
                        )
                    )
                finally:
                    self._ole32.CoTaskMemFree(pidl)
        finally:
            self._release(enum)
        return nodes

    def _display_name(self, folder: int, pidl: int, flags: int) -> str:
        """读取 PIDL 的显示名 / 解析名（安全处理 STRRET 三种形式）。

        注意：union 成员必须声明为 ``c_void_p`` 而不是 ``c_wchar_p``。
        因为用了 ``_anonymous_``，``c_wchar_p`` 字段在被访问时会自动转成
        Python ``str``，再拿去做 ``CoTaskMemFree`` 就会传进垃圾指针并崩进程。
        """
        import ctypes

        class _U(ctypes.Union):
            _fields_ = [
                ("pOleStr", ctypes.c_void_p),
                ("uOffset", ctypes.c_uint),
                ("cStr", ctypes.c_char * 260),
            ]

        class STRRET(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("uType", ctypes.c_uint), ("u", _U)]

        sr = STRRET()
        ctypes.memset(ctypes.addressof(sr), 0, ctypes.sizeof(sr))
        hr = self._call(
            folder, self._SF_GET_DISPLAY_NAME_OF, (self._pv, self._ul, self._pv),
            pidl, flags, ctypes.addressof(sr),
        )
        if hr != 0:
            return ""
        if sr.uType == 0:  # STRRET_WSTR：pOleStr 由 Shell 分配，必须由我们释放
            ptr = sr.pOleStr
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                self._ole32.CoTaskMemFree(ptr)
        if sr.uType == 1:  # STRRET_OFFSET：字符串在 PIDL 内部
            try:
                return ctypes.wstring_at(pidl + sr.uOffset)
            except Exception:
                return ""
        if sr.uType == 2:  # STRRET_CSTR
            return sr.cStr.decode("mbcs", "replace")
        return ""

    def _attributes_of(self, folder: int, pidl: int) -> int:
        """读取某个子项在父文件夹里的属性位（``IShellFolder::GetAttributesOf``）。

        Args:
            folder: 父文件夹的 ``IShellFolder*``。
            pidl: 子项的 **相对** PIDL。

        Returns:
            属性位掩码（``SFGAO_*`` 的组合）；失败时返回 0。
        """
        import ctypes

        # apidl 是「PIDL 数组」：这里只有一个元素，指向该元素的指针即 pidl 本身。
        arr = (ctypes.c_void_p * 1)(pidl)
        rgf = ctypes.c_ulong(self.SFGAO_QUERY_MASK)
        hr = self._call(
            folder, self._SF_GET_ATTRIBUTES_OF, (self._ul, self._pv, self._pv),
            1, ctypes.addressof(arr), ctypes.addressof(rgf),
        )
        if hr != 0:
            return 0
        return int(rgf.value)

    def _is_container(self, attrs: int) -> bool:
        """根据 ``SFGAO_*`` 属性位判断该项目能否当文件夹展开。

        **只用 ``SFGAO_FOLDER`` 判断。** 实测（Windows 11 / shell32）：

        * 普通文件  ``attrs=0x40400177`` → ``SFGAO_FOLDER`` 为 0，
          但 ``SFGAO_STREAM`` 为 **1**（资源管理器的「文件也是流」语义）
        * 文件夹    ``attrs=0xF080016F`` → ``SFGAO_FOLDER`` 为 1

        所以 ``SFGAO_STREAM`` **不能**用来判断文件夹，否则所有文件都会被当成目录。

        另外：如果 Shell 一个属性都没返回（``attrs == 0``，说明查询失败），
        为了不把本来能展开的便携设备项目误判成文件，这里返回 True 让用户
        可以尝试展开（展开失败只是列表为空，代价很小）。
        """
        if attrs == 0:
            return True
        return bool(attrs & self.SFGAO_FOLDER)

    def _make_node(
        self, name: str, parse: str, is_folder: bool, parent_shell_path: str
    ) -> ShellNode:
        fs_path: str | None = None
        if not _is_shell_parsing_name(parse):
            fs_path = parse or None
        # 「此电脑」的直接子项：有盘符的是本地盘，没有盘符的就是 MTP 便携设备。
        at_this_pc = (not parent_shell_path) or parent_shell_path in (
            THIS_PC_PARSING,
            "此电脑",
            "This PC",
        ) or parent_shell_path.startswith("::" + SHELL_PC_GUID)
        if at_this_pc and is_folder:
            kind = "drive" if fs_path else "mtp"
        elif is_folder:
            kind = "folder"
        else:
            kind = "file"
        display = to_friendly_path(parse) or name
        return ShellNode(
            display=display,
            shell_path=parse,
            name=name,
            is_folder=is_folder,
            fs_path=fs_path,
            kind=kind,
            backend="ctypes",
        )


# ---------------------------------------------------------------------------
# 后端 B：pywin32 Shell.Application（存在时优先使用）
# ---------------------------------------------------------------------------


class _Pywin32Shell:
    r"""用 ``win32com.client.Dispatch("Shell.Application")`` 枚举。

    与 Shell COM 的等价关系::

        shell = Dispatch("Shell.Application")
        folder = shell.NameSpace(17)          # ssfDRIVES == 「此电脑」
        for item in folder.Items():
            child = shell.NameSpace(item)     # 递归：把 FolderItem 当命名空间
            for sub in child.Items():
                sub.Path                      # 便携设备上是 ::{GUID}\... 形式
    """

    _SSF_DRIVES = 17

    def __init__(self) -> None:
        import win32com.client  # type: ignore[import-not-found]

        self._client = win32com.client
        self._shell = win32com.client.Dispatch("Shell.Application")
        self._co_initialized = True

    def open(self) -> None:
        return None

    def close(self) -> None:
        self._shell = None

    # -- 内部 ------------------------------------------------------------

    def _namespace(self, folder_item: Any) -> Any:
        """把一个 FolderItem（设备 / 文件夹）转成 Folder 命名空间。"""
        return self._shell.NameSpace(folder_item)

    def _children_of(self, folder: Any, parent_shell_path: str) -> list[ShellNode]:
        nodes: list[ShellNode] = []
        try:
            items = folder.Items()
        except Exception:
            return nodes
        try:
            count = int(items.Count)
        except Exception:
            return nodes
        for i in range(count):
            try:
                item = items.Item(i)
            except Exception:
                continue
            try:
                name = item.Name
            except Exception:
                name = "?"
            try:
                parse = item.Path or ""
            except Exception:
                parse = ""
            # 判断能否当文件夹继续展开
            is_folder = False
            try:
                is_folder = self._shell.NameSpace(item) is not None
            except Exception:
                is_folder = False
            if not parse:
                is_folder = False
            fs_path = None if _is_shell_parsing_name(parse) else (parse or None)
            if parent_shell_path in (THIS_PC_PARSING, "", "此电脑"):
                kind = "drive" if fs_path else "mtp"
            elif is_folder:
                kind = "folder"
            else:
                kind = "file"
            nodes.append(
                ShellNode(
                    display=to_friendly_path(parse) or name,
                    shell_path=parse,
                    name=name,
                    is_folder=is_folder,
                    fs_path=fs_path,
                    kind=kind,
                    backend="pywin32",
                )
            )
        return nodes

    # -- 对外 ------------------------------------------------------------

    def list_children(self, shell_path: str) -> list[ShellNode]:
        if not shell_path or shell_path in ("此电脑", "This PC", THIS_PC_PARSING):
            folder = self._shell.NameSpace(self._SSF_DRIVES)
            return self._children_of(folder, THIS_PC_PARSING)
        sp = to_shell_path(shell_path)
        folder = None
        for candidate in (sp, sp.replace("::" + SHELL_PC_GUID, "")):
            try:
                folder = self._shell.NameSpace(candidate)
            except Exception:
                folder = None
            if folder is not None:
                break
        if folder is None:
            return []
        return self._children_of(folder, sp)


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

_BACKEND: Any = None
_BACKEND_NAME: str = ""


def backend_name() -> str:
    """返回当前使用的后端名：``"pywin32"`` / ``"ctypes"`` / ``""``（不可用）。"""
    _ensure_backend()
    return _BACKEND_NAME


def _ensure_backend() -> Any:
    """惰性创建后端；优先 pywin32，失败则回退纯 ctypes。"""
    global _BACKEND, _BACKEND_NAME
    if _BACKEND is not None:
        return _BACKEND
    if not _IS_WINDOWS:
        _BACKEND_NAME = ""
        return None
    try:
        _BACKEND = _Pywin32Shell()
        _BACKEND_NAME = "pywin32"
        return _BACKEND
    except Exception:
        pass
    try:
        _BACKEND = _CtypesShell()
        _BACKEND_NAME = "ctypes"
        return _BACKEND
    except Exception:
        _BACKEND = None
        _BACKEND_NAME = ""
        return None


def backend_status() -> str:
    """返回一句人类可读的后端状态说明（中文）。"""
    _ensure_backend()
    if not _IS_WINDOWS:
        return "非 Windows 平台，Shell COM 枚举不可用。"
    if _BACKEND_NAME == "pywin32":
        return "Shell COM 后端：pywin32 (win32com.client.Dispatch('Shell.Application'))。"
    if _BACKEND_NAME == "ctypes":
        return (
            "Shell COM 后端：纯 ctypes（未安装 pywin32）。功能等价，"
            "推荐 `pip install pywin32` 以获得更好的稳定性。"
        )
    return "Shell COM 后端不可用：无法创建 Shell 命名空间对象。"


def list_roots() -> list[ShellNode]:
    """列出「此电脑」下的全部根节点（本地盘 + 便携设备）。

    Returns:
        节点列表；后端不可用时返回空列表。
    """
    backend = _ensure_backend()
    if backend is None:
        return []
    try:
        backend.open()
        return backend.list_children("")
    except Exception:
        return []


def list_portable_devices() -> list[ShellNode]:
    """只列出「此电脑」下的便携设备（MTP，无盘符）。"""
    return [n for n in list_roots() if n.kind == "mtp"]


def list_children(node_or_path: ShellNode | str) -> list[ShellNode]:
    """列出某个节点的直接子项（**懒加载**，供界面逐层展开使用）。

    遇到 ``ERROR_BUSY``（``0x800700AA``）这类「设备忙」错误会**自动重试**，
    因为官方明确建议不要把设备忙当成硬错误。

    Args:
        node_or_path: :class:`ShellNode` 或 Shell 解析名字符串。

    Returns:
        子节点列表；失败时返回空列表（不抛异常）。
    """
    backend = _ensure_backend()
    if backend is None:
        return []
    shell_path = node_or_path.shell_path if isinstance(node_or_path, ShellNode) else node_or_path
    if not shell_path:
        return []
    try:
        backend.open()
    except Exception:
        return []

    # ERROR_BUSY 是临时状态，重试 3 次（间隔递增）。
    for attempt in range(3):
        try:
            children = backend.list_children(shell_path)
        except Exception:
            children = []
        if children:
            return children
        if attempt < 2:
            time.sleep(0.4 * (attempt + 1))
    return children


def iter_tree(
    shell_path: str = "", max_depth: int = 2, limit_per_dir: int = 200
) -> Iterator[ShellNode]:
    """深度优先遍历 Shell 命名空间（**小心：MTP 很慢**，请限制 ``max_depth``）。

    Yields:
        :class:`ShellNode`，按深度优先顺序。
    """
    stack: list[tuple[str, int]] = [(shell_path, 0)]
    while stack:
        path, depth = stack.pop()
        if depth > max_depth:
            continue
        children = list_children(path)[:limit_per_dir]
        for node in children:
            yield node
            if node.is_folder and depth < max_depth:
                stack.append((node.shell_path, depth + 1))


def find_customlevels(max_depth: int = 6) -> list[ShellNode]:
    """在所有便携设备里查找 ``Android/data/com.beatgames.beatsaber/files/CustomLevels``。

    只在 **便携设备** 里找（盘符路径由 :mod:`quest_paths` 负责），
    逐层按名字匹配，避免整棵树遍历。

    Args:
        max_depth: 每个设备下最多下钻的层数。

    Returns:
        找到的 ``CustomLevels`` 节点列表（可能为空）。
    """
    want = ["android", "data", "com.beatgames.beatsaber", "files", "customlevels"]
    found: list[ShellNode] = []

    for dev in list_portable_devices():
        for storage in list_children(dev):
            if not storage.is_folder:
                continue
            # storage 通常是「内部共享存储空间」
            results = _descend(storage, want, max_depth)
            found.extend(results)
    return found


def _descend(node: ShellNode, want: list[str], max_depth: int) -> list[ShellNode]:
    """按名字路径 ``want`` 从 ``node`` 往下钻。"""
    if not want or max_depth <= 0:
        return [node] if not want else []
    target = want[0]
    out: list[ShellNode] = []
    for child in list_children(node):
        if not child.is_folder:
            continue
        if child.name.strip().lower() == target:
            if len(want) == 1:
                out.append(child)
            else:
                out.extend(_descend(child, want[1:], max_depth - 1))
    return out


def find_storage_folders() -> list[ShellNode]:
    """列出所有便携设备的第一层存储（例如「内部共享存储空间」）。"""
    out: list[ShellNode] = []
    for dev in list_portable_devices():
        out.extend([c for c in list_children(dev) if c.is_folder])
    return out


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def selftest(verbose: bool = True) -> int:
    """在本机枚举 Shell 命名空间并打印结果。

    Returns:
        0 表示 Shell COM 后端可用；1 表示后端不可用。
    """
    out = print if verbose else (lambda *a, **k: None)
    out("=" * 72)
    out("shell_mtp 自检")
    out("=" * 72)
    out(f"平台           : {sys.platform}")
    out(f"Python         : {sys.version.split()[0]}")
    out(f"后端状态       : {backend_status()}")

    if not _IS_WINDOWS:
        out("非 Windows，跳过枚举。")
        return 1

    try:
        import win32com.client  # noqa: F401

        out("pywin32        : 已安装")
    except Exception:
        out("pywin32        : 未安装（将使用纯 ctypes 回退，功能等价）")

    out("")
    out("--- 1) to_shell_path() 单元检查 ---")
    cases = [
        ("C:/Windows/", "C:\\Windows"),
        ("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}", None),
        ("Quest 3/内部共享存储空间/Android", None),
        ("此电脑/Quest 3", None),
        ("", ""),
    ]
    for src, expect in cases:
        got = to_shell_path(src)
        flag = "" if expect is None else ("  <== 期望 %r" % expect if got != expect else "  OK")
        out(f"  {src!r:52} -> {got!r}{flag}")

    out("")
    out("--- 2) 枚举「此电脑」---")
    roots = list_roots()
    if not roots:
        out("  未枚举到任何根节点。")
        out("  （若是非 Windows 或 Shell 后端不可用，属正常。）")
    for node in roots:
        out(
            "  [%-6s] %-22s shell_path=%r fs=%r 可枚举=%s"
            % (node.kind, node.name, node.shell_path, node.fs_path, node.is_folder)
        )

    devices = [n for n in roots if n.kind == "mtp"]
    out("")
    out(f"--- 3) 便携设备（MTP）: {len(devices)} 个 ---")
    if not devices:
        out("  未发现便携设备（MTP）。")
        out("  请确认：Quest 已用 USB 连接、头显内已选「允许访问文件」、")
        out("  且 Windows 资源管理器「此电脑」下能看到该设备。")
    for dev in devices:
        out(f"  >>> {dev.name}  shell_path={dev.shell_path!r}")
        kids = list_children(dev)
        out(f"      第一层 {len(kids)} 项:")
        for k in kids[:20]:
            out(f"        - [{'D' if k.is_folder else 'F'}] {k.name}")
        hits = _descend(dev, ["android"], 1)
        out(f"      含 Android 目录: {bool(hits)}")

    out("")
    out("--- 4) 查找 CustomLevels ---")
    found = find_customlevels(max_depth=6)
    if found:
        for f in found:
            out(f"  命中: {f.display}")
            out(f"        shell_path = {f.shell_path!r}")
            out(f"        usable(os) = {f.usable}")
    else:
        out("  未找到 CustomLevels（设备未连接时为正常结果）。")

    out("")
    out("--- 5) 结论 ---")
    if _BACKEND_NAME:
        out(f"  Shell COM 后端可用: {_BACKEND_NAME}。")
    else:
        out("  Shell COM 后端不可用。")
    out("=" * 72)
    return 0 if _BACKEND_NAME else 1


def _main(argv: list[str]) -> int:
    if "--selftest" in argv or "-t" in argv:
        return selftest()
    if "--help" in argv or "-h" in argv:
        print(
            "shell_mtp —— 通过 Windows Shell COM 枚举便携设备（MTP）\n"
            "\n"
            "用法:\n"
            "  python shell_mtp.py --selftest    # 自检（枚举「此电脑」）\n"
            "\n"
            "作为库使用:\n"
            "  import shell_mtp\n"
            "  for dev in shell_mtp.list_portable_devices():\n"
            "      for child in shell_mtp.list_children(dev):\n"
            "          print(child.name, child.shell_path)\n"
        )
        return 0
    return selftest()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
