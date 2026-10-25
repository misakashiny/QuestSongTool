> 2026-10-25 Qt新版补充：从桌面“Quest 歌曲工具（新版曲库）”打开，在上方Quest区点“无线连接”。首次用USB转无线，先检查已授权Quest，再点击开启无线并连接；已有地址可直接重连，设备提供配对页时才用配对码。返回后选择目录、点击读取。向导中的“显示歌曲传输按钮（本次运行）”显示原有双向传输操作，仍需逐次确认。详细步骤及模拟验证边界见[无线连接说明](82_Qt无线连接与传输入口恢复.md)。下文保留旧版MTP/目录问题说明。

# Quest 3 插入后「文件夹选不了」——原因与解决办法

> 这一篇直接回答你的问题：**为什么插上 Quest 3 后，点「浏览」选不到文件夹？**

---

## 一、结论先说

不是你的操作问题，也**不是程序写错了一个小地方**，而是这类工具的"路径选择"方式从根上不适用于 Quest：

| 序号 | 原因 | 说明 |
|---|---|---|
| **1** | **Quest 3 用 USB 连接时是 MTP 便携设备，没有盘符** | 它只出现在资源管理器「此电脑」下面（比如显示成 `Quest 3`），没有 `E:\`、`F:\` 这种盘符。所以 `os.listdir()` / `os.path.exists()` / `shutil.copy()` 这些**文件系统 API 全都碰不到它**。 |
| **2** | **系统的"选择文件夹"对话框在 MTP 上根本给不出路径** | tkinter 的 `filedialog.askdirectory()` 底层调用 Windows 的 `IFileDialog`，并且固定加上 `FOS_FORCEFILESYSTEM`（只接受文件系统项目）+ `FOS_PICKFOLDERS`。MTP 目录不是文件系统项目，`GetResult()` 后再取 `SIGDN_FILESYSPATH` 会失败，Tk 此时**不报错**，只是返回一个**空字符串**。所以你看到的现象就是"能点进去、点了确定没反应/没选上"。 |
| **3** | **原程序的自动检测思路本身就是错的** | 原版用 `GetLogicalDrives()` 找到盘符、再判断 `GetDriveTypeW()==2`（可移动磁盘）来"找 Quest"。MTP 设备**不占盘符**，所以这段代码永远找不到设备 —— 它只会在你的 U 盘、读卡器上瞎找。 |
| **4** | **（附带）CustomLevels 的位置也变了** | 现在 Beat Saber 1.35+ / SongCore 用的是 `ModData/SongCore/CustomLevels`；`Android/data/com.beatgames.beatsaber/files/` 里现在主要是存档（`PlayerData.dat`、`settings.cfg` 等）。原程序只找旧路径，等于雪上加霜。 |

**一句话**：MTP 设备没有盘符 + 系统文件夹对话框只能返回盘符路径 = 选不了。这是设计限制，**改不动 `askdirectory`，只能换方案**。

---

## 二、三种可行方案（按推荐度排序）

### 方案 A（强烈推荐）：用 adb 连接

Android 调试桥（adb）走的是和 MTP 完全不同的通道，提供 `ls / cat / pull / push` 这些"类文件系统"命令，程序可以完整地读取、复制、删除，而且**批量传输比 MTP 快好几倍**。

**一次性准备（约 5 分钟）：**

1. **头显开启开发者模式**
   - 手机装 **Meta Horizon** App → 登录同一个账号 → 绑定头显；
   - 头显里：`设置 → 系统 → 开发者 → 打开「USB 连接对话框」`（有的版本叫「开发者模式」）。
2. **电脑装 adb**：下载 Google 官方 *Android Platform Tools*，解压得到 `platform-tools` 文件夹，里面有 `adb.exe`。把它所在目录加入 PATH（或者记住路径，程序设置里可以直接填）。
3. **连接与授权**：用**支持数据传输**的 USB 线连接头显（很多线只能充电，这是最常见的坑），戴上头显，在弹窗里点 **「允许 USB 调试」**（勾选"始终允许"）。
4. 验证：命令行执行 `adb devices`，应看到类似
   ```
   List of devices attached
   1WMHHxxxxxxxxx  device
   ```
   状态是 `device` 才可用；`unauthorized` 表示还没在头显里点允许；列表为空表示线/驱动/开发者模式有问题。

**在程序里使用**：点「🔌 检测设备」→ 程序会自动找到 `CustomLevels`（优先 `ModData/SongCore`，兼容旧的 `Android/data` 路径）→ 直接选用即可。

> 说明：`adb pull /sdcard/Android/data/...` 是**可行**的 —— adb 的 shell 用户（uid 2000）在 Android 11+ 的 scoped storage 限制里有明确豁免；被限制的是普通第三方 App，不是 adb。

---

### 方案 B（零安装）：程序内置的 MTP 设备浏览器

新版程序内置了一个**自己的文件夹选择器**：它通过 Windows Shell COM 接口读取"此电脑"下的便携设备目录树，可以逐层展开、点选到 `ModData\...\CustomLevels`。

- **优点**：不用装任何东西，插上就能看到设备里的目录、确认歌曲数量。
- **限制**：Windows 没有给 MTP 设备盘符，**读取文件内容仍然不行**（无法解析 `info.dat`、无法显示封面、无法直接复制）。因此这个模式只用于"浏览 + 确认路径"，程序会明确提示你改用 adb 或方案 C。

---

### 方案 C（最省事）：本地镜像目录

1. 用资源管理器打开 `Quest 3 → 内部共享存储空间 → Android/data/com.beatgames.beatsaber/files/`，把 `CustomLevels` 整个**复制到电脑**（例如 `D:\QuestSongs\CustomLevels`）；
2. 程序里：本地路径选你的 PC 歌单，Quest 路径选这个**镜像文件夹**；
3. 对比完成后，把要补的歌曲用程序（或资源管理器）复制回设备。

- **优点**：完全不依赖 adb，MTP 的复制由 Windows 负责，兼容性最好。
- **缺点**：每次都要手动同步一次，设备上的改动不会自动反映。

---

## 三、新版程序做了什么改动

| 位置 | 改动 |
|---|---|
| `quest_tool/device.py` | 重写设备探测：**盘符 → adb → MTP** 三路并行，不再依赖错误的 `DRIVE_REMOVABLE` 判断 |
| `quest_tool/device_shell.py` + `shell_probe.ps1` | 用 PowerShell 调用 `Shell.Application` COM 枚举 MTP 命名空间（**不需要 pywin32**），脚本以 UTF-8 BOM 保存以避免 PowerShell 5.1 按 GBK 解码导致的中文乱码 |
| `quest_tool/device_adb.py` | 完整 adb 后端：`ls`/`cat`/`pull`/`push`/删除，并用 `find -printf` + `tar` 单流**批量**读取上千个 `info.dat`（避免逐文件往返，速度差一个数量级） |
| `quest_tool/ui_devicepicker.py` | 自绘的"设备文件夹浏览器"：懒加载目录树、后台线程读取、可手动输入路径、一键跳到 CustomLevels |
| `quest_tool/adb.py` | 路径候选改为 **`ModData/SongCore` 优先**，`Android/data` 作为兼容兜底；所有 shell 路径都用单引号转义，避免中文/空格注入 |
| 路径输入框 | 直接粘贴 `adb:/sdcard/ModData/...` 也能用；如果粘贴的是 `::{GUID}\...` 这种 MTP 命名空间路径，程序会**明确告诉你不支持**并给出上面三种方案，而不是像原版那样抛 `FileNotFoundError` |

---

## 四、自查清单（按顺序排查）

```powershell
# 1) 环境自检（依赖 / 数据目录 / adb / 便携设备 一次看全）
python -m quest_tool --doctor

# 2) 只做一次 Quest 位置探测
python -m quest_tool --scan

# 3) adb 层面单独确认
adb devices -l
adb shell ls /sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels
```

| 现象 | 原因 | 处理 |
|---|---|---|
| `askdirectory` 能进设备但选不中 | MTP 无盘符（本文第一条） | 用 adb 或本地镜像目录 |
| `adb devices` 为空 | 线只充电 / 未开开发者模式 / 未装驱动 | 换数据线、开启开发者模式、装官方驱动 |
| 状态是 `unauthorized` | 头显里没点"允许 USB 调试" | 戴上头显点允许；必要时 `adb kill-server` 重试 |
| 程序里"检测设备"找不到目录 | 头显里还没运行过 Beat Saber / 没装 SongCore | 先在头显里启动一次游戏 |
| 只能看到设备、点不了确定 | 你选的是 MTP 模式 | 改成 adb，或按方案 C 复制到本地 |
| **复制时弹出"目标已包含名为 X 的文件夹 / 替换或跳过"** | 见下一节 | 已修复，升级到 v14.0 |

---

## 五、"执行时老是弹出复制/跳过弹窗"是怎么回事（已修复）

### 现象

复制歌曲到设备（或从设备复制回来）时，Windows 弹出
**「目标已包含名为 XXX 的文件夹」→ 替换 / 跳过** 的对话框，
必须手动点一下才能继续；如果没人点，程序就一直卡在那里。

### 根因（实测数据）

程序在 MTP 模式下用 Windows Shell 的 `Folder.CopyHere()` 复制文件，而**它的语义不是"覆盖"**：
目标已存在同名文件夹时，它只会**跳过冲突的文件**（旧 `info.dat` 原样保留 = 歌曲没更新），
是否弹窗则完全取决于传给它的标志位。我在 `_review/probe_copyhere.ps1` 里把各个标志实测了一遍：

| flags | 实测结果 |
|---|---|
| `4`（只有 FOF_SILENT） | 旧 `info.dat` **没被覆盖**，且耗时 **38 秒** —— 就是在等用户点那个"跳过"对话框 |
| `16` / `20` | 覆盖了 `info.dat`，但目标里旧的额外文件**残留**（歌曲目录不干净） |
| `512` | 又回到"没覆盖 + 5.8 秒等待" |
| **`1556`**（SILENT + NOCONFIRMATION + NOCONFIRMMKDIR + NOERRORUI） | **不弹窗**，但仍会跳过已存在的同名项 |

也就是说，光调标志位只能消掉弹窗，**消不掉"已存在就跳过"** —— 复制会静默变成空操作。

### 修法（v14.0）

1. **标志位改成 1556**：任何情况下都不再弹窗（缺 `FOF_NOCONFIRMATION` 就是弹窗的直接原因）；
2. **真正的"覆盖更新"改用"改名挪开"实现**：
   复制前先把目标里的同名文件夹改名为 `<名字>__旧版本_questbak`，再复制新内容进去，
   这样"已存在"这个前提被消除，复制必定生效；
3. **不自动删除那个备份**：实测删除 shell/MTP 条目要 **10~16 秒**，而且 Windows 还会弹一次确认框
   （`_review/probe_delete.ps1` 三种删除动词全部 10 秒以上且未真正删除），
   所以旧版本保留在原地，界面会提示你确认无误后自己在资源管理器里删掉。

修复后实测（`_review/probe_copyhere.ps1` + `e2e_mtp` 测试）：
普通复制 3 秒完成、`-Replace` 复制 3 秒完成，都**不再弹窗**，
且替换后目标目录里只剩新版本的文件（旧的多余文件不会残留）。
