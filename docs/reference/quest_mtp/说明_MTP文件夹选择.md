# Quest 歌单对比工具 —— 为什么选不了 Quest 文件夹，以及三种可用方案

> 对应代码：`shell_mtp.py`、`folder_picker.py`、`adb_bridge.py`、`quest_paths.py`
> 全部在本机（Windows 11 + Python 3.13.2 ×64）通过 `py_compile` 与自检。

---

## 一、根本原因：`askdirectory()` 从设计上就选不出 MTP 设备里的文件夹

### 1.1 Quest 3 不是磁盘

Quest 3 用 USB 连上 Windows 后走的是 **MTP（Media Transfer Protocol）**。
MTP 不是「U 盘」，Windows **不会**给它分配盘符。它只作为 **Shell 命名空间对象**
挂在「此电脑」下面，由 `WPD`（Windows Portable Devices）驱动栈提供内容。

后果：

| 调用 | 结果 |
| --- | --- |
| `GetLogicalDrives()` | 只返回 `C:\ D:\ E:\`，**没有** Quest |
| `GetDriveTypeW() == 2 (DRIVE_REMOVABLE)` | 永远匹配不到 Quest（它没有盘符） |
| `os.path.exists(r"Quest 3\...")` | `False` |
| `os.listdir(...)` / `open(...)` / `shutil.copy(...)` | `FileNotFoundError` / `OSError` |
| `Shell.Application.NameSpace(17).Items()` | ✅ **能**看到 Quest 3 |

所以原代码里那段「`GetLogicalDrives()` + `GetDriveTypeW()==2` + `os.listdir()`
探测移动盘」的逻辑，**对一个 MTP 设备是永远不会命中的**——不是写错了，
而是这条路在物理上就不存在。

### 1.2 为什么 `filedialog.askdirectory()` 也不行

Windows 上 Tk 8.6.3+（Python 3.6+ 附带的所有版本）用的是 Vista 新对话框
`IFileDialog` + `FOS_PICKFOLDERS`（见 Tcl TIP 432）。关键在 Tk 源码
`win/tkWinDialog.c` 的 `GetFileNameVista()`：

```c
flags |= FOS_FORCEFILESYSTEM | FOS_NOVALIDATE | FOS_PATHMUSTEXIST;
if (oper == OFN_DIR_CHOOSE) { flags |= FOS_PICKFOLDERS; }
...
hr = fdlgIf->lpVtbl->GetResult(fdlgIf, &resultIf);
hr = resultIf->lpVtbl->GetDisplayName(resultIf, SIGDN_FILESYSPATH, &wstr);
if (SUCCEEDED(hr)) { ... resultObj = ...; }
```

三个致命点：

1. **`FOS_FORCEFILESYSTEM`（0x40）** 要求返回的必须是文件系统项目
   （即带 `SFGAO_FILESYSTEM` 属性）。MTP 设备上的文件夹
   `IsFileSystem = False`，所以它们**压根就不该被选中**；真去选，对话框会提示
   「你无法在此处保存 / You can't save here.」——这就是用户看到的
   **「能点进去，但选不了」**（确定按钮变灰或点了没反应）。

2. **只试了 `SIGDN_FILESYSPATH`，没有任何回退**。把一个 Shell 项目转成路径时，
   便携设备只能给出 **解析名（parsing name）**，拿不到文件系统路径，
   于是 `GetDisplayName(SIGDN_FILESYSPATH)` 失败
   （典型 `0x80070057 E_INVALIDARG` / `0x80070005 E_ACCESSDENIED`）。
   Tk 源码里**没有** `SIGDN_DESKTOPABSOLUTEPARSING` / `SIGDN_URL` /
   `SIGDN_NORMALDISPLAY` 的兜底分支。

3. **失败后仍返回成功**。`hr` 变量还停留在上一步 `GetResult()` 的 `S_OK`，
   函数走到 `if (hr == 0) { if (resultObj) Tcl_SetObjResult(...); return TCL_OK; }`
   —— `resultObj` 是 NULL，于是 **Tcl 返回空字符串**。

**结论：Python 侧 `askdirectory()` 返回 `''`（空字符串），并且不抛任何异常。**
所以原代码的 `if folder:` 判定为假，看起来就像「点了没反应」。
另外现代 Tk 已经没有 `-xpstyle` 之类的开关能退回老的 `SHBrowseForFolder` 对话框
（8.6.0 用的还是老对话框，8.6.3 起换成了 `IFileDialog`），所以
**`askdirectory()` 这条路无法修补，只能换掉。**

### 1.3 便携设备的路径长什么样

用 Shell COM 拿到的 `FolderItem.Path` 长这样（**不是**文件系统路径）：

```
::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\?\usb#vid_2833&pid_0186#1WMHHxxxxxxxx#{6ac27878-a6fa-4155-ba85-f98f491d4f33}\SID-{10001,,123456789012}
```

- `::{20D04FE0-...}` 是「此电脑」的 CLSID
- `{6AC27878-A6FA-4155-BA85-F98F491D4F33}` 是 **GUID_DEVINTERFACE_WPD**
- 后面的 GUID 段是 WPD 对象的 persistent unique ID

把它交给 `os.listdir()` 会得到
`OSError: [WinError 123] 文件名、目录名或卷标语法不正确`。
**它只能交回 Shell COM / 资源管理器。**

---

## 二、三种可行方案对比

| | (a) 盘符路径 | (b) **adb**（推荐） | (c) Shell COM / MTP |
| --- | --- | --- | --- |
| 是否需要盘符 | 需要 | 不需要 | 不需要 |
| Quest 3 实际可用 | ❌ 不适用 | ✅ 可用 | ✅ 可枚举 |
| 读文件 | `open()` | `adb pull` | 需 WPD / Shell COM，`os` 不可用 |
| 写文件 | `shutil.copy` | `adb push` | `Folder.CopyHere`（同步、易失败） |
| 能否 `os.listdir` | ✅ | ❌（要走 `adb shell ls`） | ❌ |
| 大量小文件速度 | 快 | **快且稳定** | 很慢（每个文件一次协议协商） |
| 需要用户做什么 | — | 开发者模式 + 装 adb + 授权 | 无 |
| 保留文件时间戳 | ✅ | ❌ | ❌ |
| 代码复杂度 | 最低 | 低（`subprocess`） | 高（COM / WPD） |
| 本模块实现 | `quest_paths.find_on_drives()` | `adb_bridge.py` | `shell_mtp.py` |

**推荐组合：优先 adb；Shell COM 作为「不用装任何东西」的兜底枚举与手动选择；
盘符路径保留给少数能映射成磁盘的设备/工具。**

---

## 三、推荐方案 (b)：adb 完整配置步骤

### 3.1 在 Quest 3 上开启开发者模式

1. 手机装 **Meta Horizon**（原 Oculus）应用，用**和头显同一个** Meta 账号登录。
2. 到 Meta 开发者网站创建账号并**建立一个组织（Organization）**
   （需要先开启两步验证）。组织名字随便填。
3. 手机 App 里：**头显图标 → 头显设置 → 开发者模式 → 打开**。
   （也有版本把它放在「高级 / Advanced」里。）
   > 开发者模式开关**只在手机 App 里**，头显里没有。
4. 用 USB 线连接头显和电脑（要**数据线**，充电线不行）。
5. 戴上头显，会弹两个窗：
   - 「是否允许访问设备文件」→ **允许**（这是 MTP）
   - 「是否允许 USB 调试」→ 勾选 **一律允许 / Always allow from this computer**，
     再点允许（这是 adb）
   > 首次连接**必须戴上头显**点这个授权弹窗，所以无法完全无人值守。
   > 如果弹窗没出现：在 Meta Horizon 应用里把开发者模式关掉再打开，重新插拔 USB。
6. 头显下拉通知栏里 USB 那一项，`USB controlled by` 选 **Connected device**。
7. （可选，但推荐）让 adb 走 Wi-Fi，避免和 MTP 抢同一条 USB 线：

   ```bat
   adb tcpip 5555
   adb connect <头显的IP地址>:5555
   ```

   之后 MTP 在资源管理器里照常可用，adb 走网络。

### 3.2 在电脑上安装 adb

1. 下载 Google 官方 **SDK Platform-Tools for Windows**：
   <https://dl.google.com/android/repository/platform-tools-latest-windows.zip>
2. 解压到 `C:\platform-tools\`（里面应有 `adb.exe`、`AdbWinApi.dll`、
   `AdbWinUsbApi.dll`）。
3. 加入 PATH：

   ```powershell
   setx PATH "%PATH%;C:\platform-tools"
   ```

   （或「系统属性 → 环境变量 → Path → 新建」）
4. 验证：

   ```powershell
   adb version          # Android Debug Bridge version 1.0.41 ...
   adb devices -l       # 应看到 <序列号>  device  model:Quest_3 ...
   ```

   也可以 `winget install Google.PlatformTools` 或 `choco install adb`。
   本项目还支持把 `platform-tools\` 目录直接放在程序旁边，或设置环境变量
   `QUEST_ADB` 指向 `adb.exe`。

### 3.3 `adb devices` 状态对照

| 输出 | 含义 | 处理 |
| --- | --- | --- |
| `device` | 已连接且已授权 | 可用 |
| `unauthorized` | 头显上还没点「允许 USB 调试」 | 戴上头显点允许并勾「一律允许」 |
| `offline` | 连接异常 / adbd 正在重启 | 拔插 USB，或 `adb kill-server` 再试 |
| 空列表 | 没检测到 | 换数据线 / 确认开发者模式 / 确认 USB 模式 |

### 3.4 常用命令

```bat
:: 查看歌曲目录
adb shell ls -la /sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels

:: 拉取到本地（批量、带目录结构）
adb pull /sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels "D:\QuestSongs"

:: 推送新歌曲
adb push "D:\NewMap" /sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels/

:: 备份存档
adb pull /sdcard/Android/data/com.beatgames.beatsaber/files/PlayerData.dat .
```

---

## 四、⚠️ 重要：歌曲目录位置可能已经变了

原代码假设的路径是：

```
内部共享存储空间\Android\data\com.beatgames.beatsaber\files\CustomLevels
```

**这个路径在当前的 Beat Saber / SongCore 上已经不是歌曲目录了。**
从 Beat Saber 1.35 + SongCore 时代开始，自定义歌曲放在 `ModData` 下
（依据 Quest-SongCore 的 `include/config.hpp`、QuestSongManager、BMBF 技术 wiki）：

| 优先级 | 路径 | 说明 |
| --- | --- | --- |
| 1 | `/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels` | **当前主用** |
| 2 | `/sdcard/ModData/com.beatgames.beatsaber/Mods/SongLoader/CustomLevels` | 旧 SongLoader |
| 3 | `/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels` | 更早的 BMBF 时代，保留兼容 |

而 `/sdcard/Android/data/com.beatgames.beatsaber/files/` 现在主要放
**存档**：`PlayerData.dat`、`AvatarData.dat`、`settings.cfg`。

本项目的 `quest_paths.CUSTOMLEVELS_RELATIVE` 和
`adb_bridge.QUEST_CUSTOMLEVELS` 已按上表顺序排列。
**建议把原程序的查找顺序也改成这样**，否则即使连上了也可能找不到歌。

另外：这些目录需要设备上装了 **SongCore / SongLoader** 模组才会被 Beat Saber 读取，
原版（未装模组）不会扫描它们。WIP 歌在 `CustomWIPLevels`，歌单在
`Mods/PlaylistManager/Playlists`。

---

## 五、方案 (c)：Shell COM 枚举（无需安装任何东西）

不用装 adb 时的兜底方案，已在 `shell_mtp.py` 中实现并实测通过。

### 5.1 pywin32 写法（有 pywin32 时优先使用）

```python
import win32com.client

shell = win32com.client.Dispatch("Shell.Application")
pc = shell.NameSpace(17)              # 17 = ssfDRIVES = 「此电脑」
for item in pc.Items():
    print(item.Name, repr(item.Path))  # item.Path 就是 Shell 解析名
    ns = shell.NameSpace(item)         # 把设备当命名空间递归
    if ns is not None:
        for sub in ns.Items():
            print("   ", sub.Name, repr(sub.Path))
```

要点：

- `Shell.NameSpace(17)` 等价于 `NameSpace("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}")`
- 递归用 `NameSpace(FolderItem)`，**不是** `NameSpace(路径字符串)`
- 按名字查找用 `Folder.ParseName("内部共享存储空间")`
- **`item.Path` 拿到的是 `::{GUID}\...`，不能给 `os` / `shutil`**
- `item.Size` 对便携设备常常是 0；要大小得用 `Folder.GetDetailsOf(item, 列号)`

### 5.2 纯 ctypes 回退（没装 pywin32 也能用）

`shell_mtp.py` 里的 `_CtypesShell` 直接用 `ctypes` 调
`IShellFolder` / `IEnumIDList`，只依赖 `ole32.dll` / `shell32.dll`。
本机（**没装 pywin32**）实测可用。踩过的坑都写在代码注释里：

1. `IShellFolder::BindToObject` 的 `pbc` **不能传 NULL**，必须传真实的
   `IBindCtx`（`CreateBindCtx`），否则**直接崩进程**（访问冲突）。
2. `riid` 参数也不能传 NULL，必须显式传 `IID_IShellFolder`
   （`{000214E6-0000-0000-C000-000000000046}`）。
3. vtable 槽位必须用 `ctypes.c_void_p.from_address()` 按定长指针读取；
   用 `POINTER(c_void_p)` 索引会把**低 32 位为 0 的地址读成 `None`**。
4. `STRRET` 的 union 成员要声明成 `c_void_p` 而不是 `c_wchar_p`：
   用了 `_anonymous_` 之后 `c_wchar_p` 字段会自动转成 Python `str`，
   再拿它去 `CoTaskMemFree` 就会传垃圾指针并崩进程。
5. `SHCreateItemFromParsingName` 返回的 `IShellItem`（来自
   `Windows.Storage.dll`）**不支持** `BHID_EnumItems`，
   枚举必须走 `IShellFolder`。
6. 判断是不是文件夹要用 `IShellFolder::GetAttributesOf` 的
   `SFGAO_FOLDER` 位，**不能**用「`BindToObject` 是否成功」——
   对文件调用 `BindToObject` 也会成功。便携设备项目还要一并看
   `SFGAO_HASSUBFOLDER` / `SFGAO_STREAM`。

---

## 六、方案 (a)：盘符路径（保留，但基本不适用）

`quest_paths.find_on_drives()` 仍然实现了 `GetLogicalDrives()` +
`GetDriveTypeW() == 2 (DRIVE_REMOVABLE)` 的探测，用于少数情况：
设备被第三方工具映射成盘符、或用 `MTPdrive` 之类的软件做了映射。
另外支持环境变量 `QUEST_DRIVE` 手工指定。

**注意它对本机的 Quest 3 一定是找不到的** —— 这不是 bug，是 MTP 的性质。

---

## 七、已知限制（必须知道）

1. **Shell 命名空间路径不能给 `os` / `shutil`。**
   `item.Path` 得到的 `::{GUID}\...` 只能交回 Shell COM 或资源管理器。
   `os.path.exists()` / `open()` / `shutil.copy()` 全部报错
   （`WinError 123` 语法不正确）。Qt/`win32com` 也一样。

2. **MTP 传大量小文件极慢。**
   MTP 是「有状态、一问一答」的协议，每个文件都要走一次
   `SendObjectInfo` + `SendObject` 协商，**文件之间没有流水线**，
   所以传输速率会随着文件数线性下降（一个文件 45 MB/s，第二个 30 MB/s，
   第三个更慢）。Beat Saber 一首歌就是 `info.dat` + `song.ogg`
   + `cover.jpg` + 若干难度 `.dat`，几百首歌就是几千个文件——
   这正是应该用 adb 的核心原因。

3. **MTP 有硬限制**：单文件 ≥ 4 GiB 无法通过 MTP 传输
   （Android 侧 `MAX_FILE_SIZE_FOR_TRANSCODING = 4GB - 1`），
   文件名上限 255 字节；Windows 的 MTP 客户端还有已知的
   截断/尺寸误用问题。另外 **Android 端不完整实现 MTP，无法设置文件的
   创建/修改时间**——拷过去的歌曲时间戳一律变成「现在」。

4. **`Android/data` 的访问限制（Android 11+）**
   - 对**普通第三方 App**：不能读别的 App 的 `Android/data`、`Android/obb`。
   - 对 **adb 的 shell 用户（uid 2000）**：**被明确豁免**。
     AOSP `MediaProviderWrapper.cpp` 里有
     `shouldBypassMediaProvider(uid) { return uid == SHELL_UID || uid == ROOT_UID; }`，
     所以 `adb pull /sdcard/Android/data/<包名>/...` **是可以用的**。
   - `adb shell content` 只是一个 Provider 客户端，以
     `com.android.shell` 身份运行，**不会提权**，不是万能钥匙。
   - `adb shell run-as <包名>` 只对 `debuggable=true` 的 App 有效；
     Beat Saber 是正式签名，**用不了**。
   - `adb backup` 已被废弃（Android 12 起基本不可用），别依赖它。

5. **MTP 的设备争用：Windows 上不是「独占单会话」，而是可重试的 `ERROR_BUSY`。**
   这点很重要，很容易搞错：

   - Windows 上 MTP 是**驱动中介、多客户端**模型。资源管理器和你的程序
     **可以同时**连接同一台设备。争用表现为
     `0x800700AA ERROR_BUSY`（官方说明：可能是 WMP 正在同步，
     或 **资源管理器正在刷新并枚举设备内容**）。
   - 微软官方的明确建议：把 `ERROR_BUSY` 当成**临时错误并重试**，
     **不要**弹错误框当成硬失败。
     > "just don't treat it as a hard error and present the user with an error
     > dialog since the user won't understand why it broke."
   - 「同一时刻只能有一个程序连接 MTP 设备」这条规则是
     **macOS / Linux 上 libmtp** 的行为，**不适用于 Windows**。
   - 真正会失败的其它错误码：
     `0x80070964 ERROR_DEVICE_IN_USE`（初始化时设备被别的进程占用）、
     `0x802A0001 E_WPD_DEVICE_ALREADY_OPENED`（同一接口对象重复 `Open`）。
   - 注意：**不存在 `WPD_E_DEVICE_BUSY` 这个常量**；
     `0x800420xx` 是 **MTP 响应码**区间（如 `0x8004201E` = Session Already Open），
     不是「设备忙」。

   本模块的做法：`shell_mtp.list_children()` 对空结果自动重试 3 次（间隔递增），
   `shell_mtp.is_transient_hresult()` 可判断某个 HRESULT 是否属于可重试类别。

   如果确实要减少争用：关掉资源管理器里该设备的窗口，或者走下面的 adb TCP 方案。

6. **adb 和 MTP 可以同时用（同一条线）。**
   Android 的 USB gadget 是**复合设备**：AOSP 的
   `UsbDeviceManager.applyAdbFunction()` 会把 adb 追加到当前数据功能上，
   所以「传输文件 + USB 调试」会得到 `sys.usb.config = "mtp,adb"` ——
   一条线上两个接口。

   - MTP：USB class `0x06`(Still Image) / subclass `1` / protocol `1`
     （另有微软 OS 描述符 `CompatibleID = {'M','T','P'}`，这就是它没有盘符的原因）
   - adb：独立的 FunctionFS 实例，class `0xFF` / subclass `0x42` / proto `0x01`

   但要注意：
   - **切换 USB 功能会撕裂并重新枚举 gadget**，MTP 会短暂断开，
     资源管理器里已打开的句柄可能失效。
   - 真正会冲突的是「谁占用 adb 接口」：**同一时刻只能有一个进程拥有 adb**。
     MBF 甚至自带 `mbf-adb-killer` 去杀掉正在运行的 adb server；
     QuestPatcher 也专门处理过「和 SideQuest 互相杀对方 adb server」的问题。
     规则：**同一时间只开一个用 adb 的工具**；交接给 WebUSB 工具前先
     `adb kill-server`。
   - **最干净的办法**：`adb tcpip 5555` + `adb connect <设备IP>`，
     让 adb 走 Wi-Fi，USB 上的 MTP 完全不受影响。
     （端口：主机 server 5037，设备端 transport 5555。）

6. **adb 授权是一次性的**。换电脑、重装系统、点了「撤销 USB 调试授权」
   之后都要重新在头显里点允许。程序里要能显示 `unauthorized` 状态并给提示。
   `adb devices` 的状态来自 AOSP `ConnectionState`：
   `device` / `unauthorized` / `no permissions` / `offline`
   （另有 `bootloader` / `recovery` / `sideload` / `rescue`）。

7. **`ModData` 路径是普通共享存储，不受 scoped storage 限制。**
   这其实是好消息：`/sdcard/ModData/...` 不在 `/sdcard/Android/` 下面，
   所以 **既能用 adb 访问，也能通过 MTP / Shell COM 访问**，
   完全绕开了 `Android/data` 的隔离问题。
   这也正是 Quest 生态自己采用的方案（把文件从 `Android/data` 搬出去）。
   所以：如果歌曲目录确实在 `ModData` 下，那么
   **不装 adb、不开开发者模式，仅靠 Shell COM 也能读**——
   只是仍然不能给 `os` / `shutil` 用，要用 `Folder.CopyHere` 之类的方式。

8. **`MANAGE_EXTERNAL_STORAGE`（所有文件访问权限）也没用。**
   即使文件管理器拿到「所有文件访问权限」，**仍然不允许**访问
   `/Android` 及其子目录 `/Android/obb`、`/Android/data`。
   所以不要指望这条路。

9. **不要用 `adb backup`。** Android 12（API 31）起已废弃，
   需要设备端交互确认；有项目实测在 API 34 上「报告成功但根本没生成
   `backup.ab`」。

10. **依赖情况**：
   - `pywin32`：PyPI 有 cp313 的 win_amd64 wheel，`pip install pywin32` 可用；
     但它**不含 WPD 模块**，只有 `win32com` / `win32com.shell`。
   - `comtypes`：纯 Python，≥1.4.8 才支持 Python 3.13。是纯 pip 走 WPD 的
     唯一现实选择。
   - `pymtp` / `libmtp`：Windows 上实际不可用（需要 MinGW 编译 + 用
     Zadig/WinUSB 换驱动，会把 Explorer 用的 WPD 驱动顶掉，得不偿失）。
   - PyPI 上名为 `mtp` 的包**不是** MTP 库（是「Many-Time Pad」加密工具）；
     GitHub 上的 `Heribert17/mtp` 才是 WPD 实现（依赖 comtypes，
     需从 git 装）。注意 `pip install mtp` 装错是这个领域的老坑。
   - 本项目的 Shell COM 回退路线**不需要任何第三方包**。

---

## 八、怎么用这套代码

```python
# 1) 一键自动查找（盘符 → adb → Shell COM），并给出体检报告
import quest_paths
print(quest_paths.describe_search())
best = quest_paths.best_quest_customlevels()
if best:
    print(best.kind, best.display, best.usable)

# 2) 图形界面选择（可浏览 MTP 设备，懒加载不怕大目录）
import folder_picker
picked = folder_picker.ask_quest_folder_smart()      # 先自动探测，失败才弹窗
if picked:
    print(picked.display)        # 给用户看 / 存配置
    print(picked.shell_path)     # 交给 Shell COM
    print(picked.kind, picked.usable)

# 3) adb 传文件
import adb_bridge
dev = adb_bridge.quest_device()
if dev:
    adb_bridge.push(dev.serial, r"D:\NewMap",
                    "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels/")
```

### 替换原程序里那一段

把 `select_folder()` 里 `folder_type == "Quest"` 的分支：

```python
# ❌ 旧：永远选不出 Quest
folder = filedialog.askdirectory(title=..., mustexist=True)

# ✅ 新：优先自动探测；失败则弹出可浏览 MTP 的对话框
import folder_picker
picked = folder_picker.ask_quest_folder_smart(self.root)
if picked is None:
    return
if picked.usable:
    path_var.set(picked.display)              # 真实盘符路径，os 可直接用
elif picked.kind == "adb":
    path_var.set(picked.remote_path)          # /sdcard/...，走 adb_bridge
else:
    path_var.set(picked.display)              # 友好路径，仅用于显示
```

### 各模块自检

```bat
python selftest_all.py     :: 一键全量自检（编译 + 导入 + 各模块自检 + 懒加载实测）
python shell_mtp.py        :: 枚举「此电脑」/ 便携设备 / 找 CustomLevels
python adb_bridge.py       :: adb 路径、版本、设备、歌曲目录
python quest_paths.py      :: 三条路线 + 体检报告
python folder_picker.py    :: tkinter 可用性 + 对话框构建（不弹窗阻塞）
```

每个模块都支持 `--selftest` 与 `--help`：

```bat
python shell_mtp.py --selftest
python adb_bridge.py --selftest
python quest_paths.py --help
```

`selftest_all.py` 的退出码：全部通过返回 `0`，有失败项返回 `1`。
各模块单独自检时，`0` 表示「模块工作正常」——
**即使没有连接 Quest、没有安装 adb，也返回 0**（优雅降级算正常）。

---

## 九、参考

- Tk 源码 `win/tkWinDialog.c`（`GetFileNameVista`，Tcl/Tk main）
  <https://raw.githubusercontent.com/tcltk/tk/main/win/tkWinDialog.c>
- Tcl TIP 432：Windows Vista 文件对话框改造（Tcl 8.6.3）
  <https://core.tcl-lang.org/tips/doc/main/tip/432.md>
- Raymond Chen：`FOS_FORCEFILESYSTEM` 与非文件系统文件夹
  <https://devblogs.microsoft.com/oldnewthing/20231011-00/?p=108884>
- Microsoft Learn：`IFileDialog` / `FOS_*` / `SIGDN` / `SFGAO_*` / `IShellFolder`
- Microsoft Learn：Windows Portable Devices（WPD）API
- Quest-SongCore `include/config.hpp`（歌曲目录真实位置）
  <https://raw.githubusercontent.com/raineaeternal/Quest-SongCore/main/include/config.hpp>
- BSMG Wiki（Quest 模组 / 开发者模式 / 路径）
  <https://bsmg.wiki/quest-modding.html>
- AOSP `MediaProviderWrapper.cpp`（shell 用户被豁免的证明）
  <https://raw.githubusercontent.com/aosp-mirror/platform_packages_providers_MediaProvider/main/jni/MediaProviderWrapper.cpp>
- Heribert17/mtp（comtypes + WPD 的参考实现）
  <https://github.com/Heribert17/mtp>
- Google SDK Platform-Tools（adb 下载）
  <https://developer.android.com/tools/releases/platform-tools>
