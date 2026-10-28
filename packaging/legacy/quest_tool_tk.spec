# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（ROADMAP C2）。

用法（在仓库根目录执行）::

    pip install pyinstaller
    pyinstaller packaging/legacy/quest_tool_tk.spec --noconfirm

产物：``dist/QuestSongCompare/QuestSongCompare.exe``（onedir）。

**为什么用 onedir 而不是 onefile**：onefile 每次启动都要把几十 MB 解包到临时目录，
冷启动要好几秒；这个工具的启动速度本来就只有 1 秒多，不值得。
onedir 直接双击文件夹里的 exe 即可，也方便把 `logs/` 一起交给用户排查。

调试时想看控制台输出（`--doctor` / `--scan`）：``set QUEST_CONSOLE=1`` 再打包。

三个容易漏的点（都写进 HANDOVER 了）：

1. **customtkinter 自带主题 JSON**，不在 .py 里 —— 不 `collect_data_files` 的话，
   打包后一启动就报找不到主题文件；
2. **两个 `.ps1` 是运行时按路径读取的**（`Path(__file__).with_name(...)`），
   不是 import —— 必须显式 `datas`，否则 MTP 相关功能全废；
3. **`console=False` 后崩溃信息就看不见了**，只能看 `logs/quest_comparator.log`
   （`app_dir()` 已处理"程序目录不可写时回退到 %LOCALAPPDATA%"）。
"""
import os
from pathlib import Path
ROOT=Path(SPECPATH).parents[1]

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

CONSOLE = os.environ.get("QUEST_CONSOLE", "") == "1"

# 1) customtkinter 的主题/资源 JSON
datas = collect_data_files("customtkinter")

# 2) 两个 PowerShell 脚本：运行时按 `quest_tool/` 相对路径读取
datas += [
    (str(ROOT/"quest_tool/shell_probe.ps1"), "quest_tool"),
    (str(ROOT/"quest_tool/shell_copy.ps1"), "quest_tool"),
]

# customtkinter 有若干按名字动态导入的子模块，静态分析扫不到
hiddenimports = collect_submodules("customtkinter")

a = Analysis(
    [str(ROOT/"main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 这些都没用到，显式排除能小几十 MB。
    # ⚠️ **千万别把 setuptools 也排除掉**：customtkinter 会用到 pkg_resources，
    # 而 PyInstaller 的 `pyi_rth_pkgres` 运行时钩子依赖 setuptools 的 jaraco.text。
    # 排掉之后 exe 一启动就 ModuleNotFoundError: No module named 'jaraco.text'
    # （v15.5 构建时真的踩到了，所以这条注释留着）。
    excludes=[
        "numpy", "matplotlib", "scipy", "pandas", "pytest",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
        "IPython", "notebook", "pip",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QuestSongCompare",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX 常被杀软误报，不值得
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 中文名放到窗口标题里（见 constants.APP_NAME），exe 名保持 ASCII，
    # 免得打包工具链/杀软/命令行在中文名上出幺蛾子
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="QuestSongCompare",
)
