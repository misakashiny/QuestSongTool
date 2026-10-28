# -*- mode: python ; coding: utf-8 -*-
"""Current Qt application, Windows x64, one-file; no user data in the bundle."""
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT=Path(SPECPATH).parent
ADB=Path(os.environ.get('QUEST_ADB_SOURCE',r'C:\Android\Sdk\platform-tools'))
required=('adb.exe','AdbWinApi.dll','AdbWinUsbApi.dll','NOTICE.txt')
for name in required:
    if not (ADB/name).is_file():raise RuntimeError('Missing platform-tools file: '+name)
datas=[(str(ADB/name),'platform-tools') for name in required]
datas += [(str(ROOT/'quest_tool'/name),'quest_tool') for name in ('shell_probe.ps1','shell_copy.ps1')]
datas += [(str(ROOT/'packaging'/'THIRD_PARTY_NOTICES.txt'),'.')]
for distribution in ('PySide6','PySide6_Essentials','PySide6_Addons','shiboken6','Pillow','miniaudio','cffi'):
    datas += copy_metadata(distribution)
python_license=Path(os.__file__).resolve().parents[1]/'LICENSE.txt'
if python_license.is_file():datas.append((str(python_license),'licenses/Python'))
a=Analysis([str(ROOT/'packaging'/'entry.py')],pathex=[str(ROOT)],binaries=[],datas=datas,
           hiddenimports=collect_submodules('quest_tool.qt_ui')+['miniaudio','_miniaudio','_cffi_backend','PIL.Image'],
           hookspath=[],hooksconfig={},runtime_hooks=[],
           excludes=['tkinter','customtkinter','PyQt5','PyQt6','PySide2','numpy','pandas','matplotlib','scipy','IPython','notebook','pytest'],
           noarchive=False,optimize=0)
# This Qt wheel uses Windows' unversioned ICU API. A same-named Poppler ICU
# found through a developer PATH is ABI-incompatible; use the OS implementation.
a.binaries=[row for row in a.binaries if Path(row[0]).name.lower()!='icuuc.dll']
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='QuestSongTool',debug=False,
        bootloader_ignore_signals=False,strip=False,upx=False,console=os.environ.get('QUEST_CONSOLE')=='1',
        disable_windowed_traceback=False,argv_emulation=False,target_arch=None,
        codesign_identity=None,entitlements_file=None)
