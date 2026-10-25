> V0.2 接入说明（2026-10-17）：本页保留技术与历史资料。当前任务唯一入口为根目录 03_当前任务.md，任务顺序见 02_开发计划.md，交接现场见 04_交接状态.md；本页旧的“先做”“唯一状态”及测试通过描述不构成本次授权或本次验证。

# 交接说明（HANDOVER）—— 深度参考

> **先读 [`AGENTS.md`](AGENTS.md)（最短入口）→ 再读 [`docs/00_AI接手指南.md`](docs/00_AI接手指南.md)（总说明）。**
> 本文档保留**架构细节与历史证据**，`docs/` 里的四份是"该怎么做"：
> `03_踩坑记录.md`（55 条）/ `04_迭代计划.md`（任务与检查表）/ `01`·`05`（重构史）/ `02`·`06`·`07`（界面与用户）。
>
> 接手对象：Quest 歌单对比工具 v17.7（Windows 桌面 GUI，Python 3.13 + customtkinter）
> 交接人：上一轮开发 Agent ｜ 交接日期：2026-09-25
> v14.1 修订：2026-10-02（修 4 个 v14.0 遗留缺陷 + 首次纳入 Git，见 `CHANGELOG.md`）
> v14.2 修订：2026-10-02（真机首次验证 + 载入提速 31.7 倍 + 静态检查，见 `CHANGELOG.md`）
> v14.3 修订：2026-10-02（修 P0：`adb push` 在非 ASCII 路径下写错设备文件夹名，见 `CHANGELOG.md`）
> v14.4 修订：2026-10-02（修 P0：真机 toybox 的 `find -printf '%y'` 坏掉导致封面探测失败，见 `CHANGELOG.md`）
> v14.5 修订：2026-10-02（元数据缓存子系统整体修好 + 复制后校验 + 设备能力探测，见 `CHANGELOG.md`）
> v14.6 修订：2026-10-02（修 P0：MTP 复制一首歌会把整个本地歌库推送到设备；镜像改增量，见 `CHANGELOG.md`）
> v14.7 修订：2026-10-02（修 P0：缺失列删除无效 + 封面墙缺失列永远为空；死代码/未使用 import 大扫除，见 `CHANGELOG.md`）
> v14.8 修订：2026-10-02（"预计耗时"按传输通道取速率并自我学习，见 `CHANGELOG.md`）
> v14.9 修订：2026-10-02（修 3 处后台线程直接碰 Tk + 静态检查扩到测试代码，见 `CHANGELOG.md`）
> v15.0 修订：2026-10-02（补完 copy_overwrite/quest_source/最近路径 + 备份目录不再混进歌单 + 静态检查第 5 项，见 `CHANGELOG.md`）
> v15.1 修订：2026-10-02（对比报告导出 CSV/Markdown + MTP 每次复制少起一次 PowerShell，见 `CHANGELOG.md`）
> v15.2 修订：2026-10-02（拆 app.py：2765 → 1541 行，按职责拆成 5 个 mixin + app_common/ui_settings，见 `CHANGELOG.md`）
> v15.3 修订：2026-10-02（跨页多选 B1：独立选择集 + Ctrl+Shift+A 全选所有页，见 `CHANGELOG.md`）
> v15.4 修订：2026-10-02（旧版数据库迁移 C3：`--migrate-legacy` 导入原版评分/评价，见 `CHANGELOG.md`）
> v15.5 修订：2026-10-02（PyInstaller 打包 C2：`quest_tool.spec`，已实际构建并验证 exe 可运行，见 `CHANGELOG.md`）
> v15.6 修订：2026-10-02（封面墙右键菜单 B6，与列表一致，见 `CHANGELOG.md`）
> v15.7 修订：2026-10-02（首次使用向导 C4 + 修 B1 的"普通单击没清空跨页选择"与一条偶发测试，见 `CHANGELOG.md`）
> v15.8 修订：2026-10-02（拖放按落点决定导入到哪一列 B2，不再手输"1 或 2"，见 `CHANGELOG.md`）
> v15.9 修订：2026-10-02（复制队列可暂停/继续 B4 + 把列表选择状态改成确定性的，见 `CHANGELOG.md`）
> v16.0 修订：2026-10-02（左右分栏改可拖动分割 B5 + 宽度记忆，见 `CHANGELOG.md`）
> v16.1 修订：2026-10-02（UI 截图回归 C7：固定尺寸/主题下与基线做像素比对，见 `CHANGELOG.md`）
> v16.2 修订：2026-10-02（高 DPI 适配 C1：`ui_scale` 三档 + `scaling_check.py` 实测不重叠不裁切，见 `CHANGELOG.md`）
> v16.3 修订：2026-10-02（多语言 C5：`messages.py` 126 条中英文案，只有外壳已转换，见 `CHANGELOG.md`）
> v16.4 修订：2026-10-02（文档与代码对齐：过时数字/重复编号全修；死代码清零；`--doctor` 加界面偏好）
> v16.5 修订：2026-10-02（无线调试 D4：配对码 / USB 转无线两条路，同 Wi-Fi 下免插线传歌）
> v16.6 修订：2026-10-02（重复歌曲检测 D3：三档判重规则，默认只用最可靠的两档）
> v16.7 修订：2026-10-02（歌单导入导出 D2：`.bplist` 真算谱面 hash，不依赖外部工具）
> v16.8 修订：2026-10-02（多语言收尾：**剩余 89 处内联中文全部转文案表**，英文界面零中文残留；文案表 235 条）
> v16.9 修订：2026-10-13（D1 试听：**改成真流式解码**，峰值内存 66.0 MB → 13.7 MB、580 ms → 72 ms，
> 输出逐样本一致；修 `_find_audio_file` 只收 Path、`_chain_first` 不释解码器两个缺陷）
> v17.1 修订：2026-10-13（修用户报的 4 个界面问题：试听点不动 / 详情栏封面大色块 / 配色太白 / 排版行号错位。
> 新增 `tests/theme_contrast.py`、`tests/cover_geometry.py` 两组回归，见 `CHANGELOG.md`）
> v17.2 修订：2026-10-13（修用户报的 3 个问题：**adb 会自动连到插着的手机**（新增 Quest 识别护栏，
> `ensure_device(require_quest=True)`）、路径输入后重启丢失（改成立刻落盘）、试听点了没反应
> （两个 `AttributeError` + 真曲库 83/85 首因传 `str` 静默失败）。
> 静态检查新增第 7 项「不能调用不存在的方法」，带自检，见 `CHANGELOG.md`）
> v17.3 修订：2026-10-14（试听进度条 + 时长可配（30/60/120/整曲）+ **停止不再卡界面线程**
> （`device.stop()` 实测 100~600 ms → 摘走设备丢后台线程后 0.5 ms）。见坑第 45、46 条）
> v17.4 修订：2026-10-14（**列身份统一**：`app_common.COLUMN_IDENTITIES` 一处定义图标+语义色，
> 列表/封面墙/空状态三处取用；操作按钮按副作用分四色四组；顺带修 seek 量程漂移。
> 发现并修掉一处真回归：四组横排要 1834px > 窗口 1700px，改 2×2 grid 后 1603px。
> 见坑第 47、48 条）
> v17.5 修订：2026-10-14（**【严重】修 v17.4 引入的版式事故**：操作分组标题胶囊
> `pack_propagate(False)` 后没给 height，CTkFrame 默认高 200 → 四个分组各 212px
> 共 432px，把三个歌曲列表挤到 212px（表格区只剩 28px，一行都显示不出来）。
> `scaling_check.py` 新增"列表装得下 >= 8 行"断言并配自检。见坑第 49 条）
> v17.6 修订：2026-10-14（**修真卡死：界面线程同步等 adb，插着非 Quest 设备时冻结 5.2 秒**；
> 新增 `diagnostics.py`（主线程心跳看门狗 / `slow()` / 异常接管）与「帮助 → 环境自检信息」
> 里的日志尾部（只挑 WARNING/ERROR，可一键复制）。见坑第 50~52 条）
> v17.7 修订：2026-10-14（**全软件运行逻辑体检**：用 AST 扫"写了但没人调用 / 调用了不存在的
> 东西"两类高发缺陷，抓到并修掉「对比」被打成 `KeyError: 'missing'` ——
> 「缺失」是**视图不是曲库**，`_make_item()` 却去 `libraries["missing"]` 取库。
> 另给三列加了**筛选档位**（全部 / 已匹配 / 缺失）+ 实时计数，操作栏改成横跨整宽一行。
> 见坑第 53、54 条）
> **先读 [`AGENTS.md`](AGENTS.md) 与 [`docs/00_AI接手指南.md`](docs/00_AI接手指南.md)**，本文档是深度参考；
> 「还没做什么」看 [`docs/04_迭代计划.md`](docs/04_迭代计划.md)，「踩过什么坑」看 [`docs/03_踩坑记录.md`](docs/03_踩坑记录.md)。

---

## 1. 一分钟摘要

| 项 | 内容 |
|---|---|
| 这是什么 | 对比 PC 本地与 Meta Quest 头显上的 Beat Saber 自定义歌曲，支持评分/评价、搜索排序、一键补齐缺失歌曲、设备端删除 |
| 上一轮做了什么 | v17.7：**全软件运行逻辑体检**（AST 扫"写了但没人调用 / 调用了不存在的东西"），修掉「对比」被打成 `KeyError: 'missing'`（「缺失」是视图不是曲库）；三列新增**筛选档位**（全部 / 已匹配 / 缺失）+ 实时计数；操作栏改成横跨整宽一行。上一轮 v17.5 修的是 `pack_propagate(False)` 漏给 `height` 导致四个分组各被撑成 212px、把列表挤到 212px（表格只剩 28px） |
| 现在能不能用 | **能，真机的读 + 写 + 删都实测过了**。`python main.py` 可启动；`python tests\run_all.py` **19/19 组通过**；Quest 3（217 首）端到端实测：`--scan`、**载入冷启动 1.47 秒 / 第二次 0.88 秒（缓存全命中）**、封面 217/217 探测成功、复制到设备（含中文歌名）文件齐全且复制后校验通过、设备端删除与护栏全部通过，跑完歌单复原 |
| 还缺什么 | **MTP 通道未实测**（本机 Quest 未枚举出 MTP，Meta 私有接口缺驱动；走 adb 不受影响）；**批量复制（≥50 首）的耗时与进度百分比未单独计时**；MTP 只能比文件数（Shell 的"大小"列是四舍五入的字符串）。**其余全部见 [`docs/04_迭代计划.md`](docs/04_迭代计划.md)** |
| 先看哪个文件 | 想跑起来 → `README.md`；想懂代码 → 第 4 节；想知道哪没做完 → `docs/04_迭代计划.md`；用户报障 → `docs/06_Quest3连接与文件夹选择.md` |

---

## 2. 接手第一步：怎么确认环境是好的

```powershell
cd "D:\AI Dsh\Quest3项目\beatsaber歌曲转移"

# ① 依赖自检（Python / ctk / PIL / adb / 便携设备 一次看全）
python -m quest_tool --doctor

# ② 跑全部 19 组测试（每组前自动清 __pycache__，约 3 分钟，其中 mtp/adb 组较慢）
python tests\run_all.py

# ③ 只探测 Quest 位置（不需要开界面）
python -m quest_tool --scan

# ④ 启动界面
python main.py
```

期望结果：`--doctor` 全 `[OK]`（adb 显示"未找到"是正常的，本机没装）；
`run_all.py` 输出 `19/19 组通过`；`main.py` 打开 1620×1000 窗口。

> 后加的三组是 v16.x 补的"可验证性"基建：`i18n`（文案表）、`scaling`（高 DPI）、
> `shots`（截图回归）。**`shots` 会弹一个真实窗口并置顶**，所以它排在最后一组。

**如果测试失败**：先跑 `python tests\run_all.py <组名>` 单独定位，
再看该组脚本头部的中文注释（每个测试都写了"为什么这么测"）。

---

## 3. 目录结构与每个文件干什么

```
D:\QA\Quest3bs\
├─ main.py                      启动入口（双击/命令行都能用）
├─ quest_tool\                  ★ 全部产品代码（31 个模块）
│  ├─ __main__.py               命令行入口：启动 / --doctor / --scan / --migrate-legacy
│  ├─ app.py                    界面骨架：构建控件、菜单、消息泵、列表/封面墙、载入扫描
│  ├─ app_copy.py               CopyMixin   复制/传输 + 耗时估算 + 实测速率学习
│  ├─ app_delete.py             DeleteMixin 删除（含「缺失」列要删对侧）
│  ├─ app_device.py             DeviceMixin 路径浏览/粘贴、设备检测、最近路径
│  ├─ app_detail.py             DetailMixin 详情栏、封面、难度、评分与评价
│  ├─ app_report.py             ReportMixin 对比与报告导出
│  ├─ app_common.py             上面 5 个 mixin 共用的纯函数与常量（**mixin 不能反向 import app.py**）
│  ├─ ui_settings.py            设置窗口（从 app.py 搬出来的）
│  ├─ ui_wizard.py              首次使用向导（三步：选目录 → 检测 → 对比）
│  ├─ ui_wireless.py            ★ 无线调试对话框（配对码 / USB 转无线）
│  ├─ duplicates.py             ★ 重复歌曲检测（归一化 + 三档判重，纯逻辑好测）
│  ├─ messages.py               ★ 文案表（中英两套）+ tr()，见 ROADMAP C5
│  ├─ constants.py              常量、字体、Quest 路径候选、备份目录后缀
│  ├─ theme.py                  ★ 设计令牌 + DPI 缩放（apply_ui_scale）
│  ├─ storage.py                运行目录解析、settings.json（原子写 + 损坏自愈）
│  ├─ logging_setup.py          日志（懒初始化 + 2MB 轮转）
│  ├─ database.py               SQLite 缓存：meta + userdata，含旧库迁移 import_legacy
│  ├─ songs.py                  info.dat 解析（v1~v4 版本 + 多编码兜底）
│  ├─ fsapi.py                  ★ 文件系统抽象层：SongFS 接口 + LocalFS + verify_copy
│  ├─ adb.py                    adb 桥：查找 adb、设备、ls/cat/pull/push、tar 批量读
│  ├─ device_adb.py             adb 后端实现（批量元数据、删除护栏）
│  ├─ device_mtp.py             MTP 后端实现（Shell CopyHere 增量镜像到本地）
│  ├─ device_shell.py           Shell 命名空间桥（调 PowerShell 枚举便携设备）
│  ├─ device.py                 设备探测：盘符 → adb → MTP 三路
│  ├─ workers.py                ★ 后台任务队列 + 危险操作护栏 + PauseGate（暂停闸门）
│  ├─ ui_songlist.py            歌曲列表控件（Treeview + 分页 + 搜索防抖 + 跨页多选 + 拖放高亮）
│  ├─ ui_coverwall.py           封面墙控件（Canvas 网格 + 虚拟化 + 异步封面 + 右键菜单）
│  ├─ ui_dialogs.py             统一对话框（ModalDialog 基类 + 7 个具体对话框）
│  ├─ ui_devicepicker.py        MTP/adb 设备文件夹浏览器
│  ├─ ui_dnd.py                 拖拽导入（ctypes + WM_DROPFILES，无第三方依赖）
│  ├─ ui_toast.py               右下角非模态通知
│  ├─ shell_probe.ps1           ↑ device_shell 用的 PowerShell（**UTF-8 BOM 保存**）
│  └─ shell_copy.ps1            ↑ device_mtp 用的复制脚本（标志位 1556 + -Replace）
├─ tests\                       ★ 19 组测试 + 排查工具
│  ├─ run_all.py                一键跑全部（第一个是秒级的静态检查）
│  ├─ _paths.py                 统一路径入口（ROOT / sandbox()）
│  ├─ static_check.py           ★ 静态 7 项（见 `docs/03_踩坑记录.md`，零依赖、秒级）
│  ├─ baseline\                 截图回归的基线图（**要提交**，见 screenshot_regression.py）
│  ├─ e2e_core.py              解析/缓存/对比/护栏/真实复制删除
│  ├─ e2e_adb.py               假 adb 驱动完整读写删
│  ├─ e2e_mtp.py               MTP 镜像 + 解析
│  ├─ e2e_gui.py               真实窗口驱动整条主流程
│  ├─ selftest_core.py         单元级：解析器/数据库/设置/路径判定
│  ├─ smoke_songlist.py        列表控件（112 项断言，含性能/高亮/跨页多选）
│  ├─ smoke_features.py        多选/列宽/悬停/封面墙右键/拖放落点/设置往返
│  ├─ smoke_dialogs.py         对话框可构造性
│  ├─ smoke_i18n.py            ★ 多语言：文案表一致 / 切换生效 / 不许退回内联中文
│  ├─ smoke_wireless.py        ★ 无线：假 adb 驱动配对/连接全流程
│  ├─ smoke_duplicates.py      ★ 重复检测：归一化 / 三档规则 / 不误报
│  ├─ smoke_audio.py           ★ 试听：真音频解码 / 流式限长 / 优雅失败（会开真声卡）
│  ├─ smoke_diagnostics.py     ★ 卡死诊断：慢操作记录 / 主线程看门狗 / 异常接管 / 日志尾部
│  ├─ theme_contrast.py        ★ 配色：WCAG 对比度 + 背景层次分离（防「太白」回归）
│  ├─ cover_geometry.py        ★ 封面：装饰后不留大块补边（防「大黑边」回归）
│  ├─ scaling_check.py         ★ 高 DPI：1.0/1.25/1.5 下不重叠不裁切、列表装得下 >= 8 行
│  ├─ screenshot_regression.py ★ 截图回归（**会弹真实窗口并置顶，排在最后一组**）
│  ├─ e2e_device_real.py       ★ 真机写/删验证（**默认只演练**，--yes 才真跑；不在 run_all 里）
│  ├─ tools\                   排查工具（**不参与日常测试**，见该目录 README）
│  │  ├─ screenshot.py         无人值守出图（回归用 screenshot_regression.py）
│  │  ├─ diag_boxes.py         洪水填充定位截图上"哪个控件画错了"
│  │  └─ diag_layout3.py       打印各区域实际几何，定位溢出/重叠
│  ├─ fixtures\fakeadb\        假 adb（把设备端命令映射到本地目录）
│  └─ archive\                 一次性排查脚本 + 取证脚本（见该目录 README）
└─ docs\
   ├─ README.md                ★ 文档索引
   ├─ 00_AI接手指南.md          ★ **总入口**（技术栈/架构/命令/功能范围/AI 开发规范）
   ├─ 01_代码审查-原版问题清单.md   原版逐行审查（71KB，含行号与证据）
   ├─ 02_UI设计说明.md              设计令牌 + 版式 + 后续 UI 待办
   ├─ 03_踩坑记录.md           ★ **55 条**（限制 1–9 + 坑 10–55），遇到怪现象先搜这里
   ├─ 04_迭代计划.md           ★ 剩余任务/优先级/验收标准/操作手册/交接检查表
   ├─ 05_改进记录-重构前后对照.md   15 项缺陷 → 修复对照 + 实测性能数据
   ├─ 06_Quest3连接与文件夹选择.md  ← 用户报"选不了文件夹/老弹窗"看这里
   ├─ 07_怎么打开软件.md           用户：三种启动方式 + 首次使用流程
   ├─ reference\源代码_原始版-单文件.py     原版（只读，用于对照）
   ├─ reference\quest_mtp\                 另一套 MTP 实现（纯 ctypes Shell COM，无 pywin32）
   └─ images\                              界面截图（启动方式/深色/浅色/封面墙/对话框）
```

---

## 4. 架构与关键设计（改代码前必读）

### 4.1 分层

```
界面层     app.py / ui_*.py         只碰控件；所有 UI 更新都在主线程
任务层     workers.py               后台线程 + 消息队列 + 危险操作护栏
数据访问   fsapi.py / device_*.py   统一成 SongFS 接口，屏蔽"本地盘符 vs Quest 设备"
功能       audio.py / duplicates.py / playlist.py / cache.py
持久层     database.py / storage.py SQLite 缓存 + JSON 设置
基础       songs.py / theme.py / messages.py / constants.py / logging_setup.py
```

**为什么要有 `fsapi.SongFS`**：Quest 走 MTP 时没有盘符，`os`/`shutil` 完全用不了；
`SongFS` 把"列歌曲 / 读 info.dat / 读封面 / 复制进出 / 删除 / 剩余空间"抽象出来，
上层（扫描、对比、复制、删除）完全不关心底下是本地磁盘、adb 还是 MTP。

### 4.2 线程规约（**最容易踩的坑**）

> **工作线程只允许 `queue.put()`，绝不允许碰 Tk 控件或调 `root.after()`。**

原版有 11 处违反了这条（工作线程直接 `root.after(...)`），在负载高时会随机崩溃。
现在的做法：

* 所有后台任务经 `workers.TaskRunner.submit()` 提交；
* 任务用 `TaskRunner.emit(kind, text, percent, payload, token)` 发消息；
* 主线程 `app._pump_queue()` 每 80ms 排空队列并更新 UI —— **这是唯一的 UI 更新入口**；
* 每个任务带 `Token`，旧任务的迟到消息会被丢弃（避免切了路径还弹旧错误）。

封面墙的封面加载也遵循同一套：线程池 → `queue.Queue` → 主线程 `after(80ms)` 轮询贴图。

### 4.3 数据与缓存

* `meta` 表：以 `(folder_name, mtime)` 为键存元数据，**mtime 变了才重新解析** → 增量扫描；
* `userdata` 表：以 `(位置标识, folder_name)` 为键存评分/评价 ——
  位置标识形如 `local:D:\Songs`、`adb:/sdcard/ModData/...`；
  这样本地与 Quest 的**同名歌曲不会互相串号**（原版按 folder_name 全局唯一，是 bug）；
* 数据库单连接 + WAL + 互斥锁，`save_meta_bulk` 用 `executemany` 批量提交。

### 4.4 危险操作护栏（`workers.py`）

* `guard_copy(src_root, dst_root, song)`：拦截"源==目标 / 互为父子目录 / 非法名字"。
  原版在用户把两个路径填成同一目录时会**先删源歌曲再复制**（实测丢数据）。
* `guard_delete(root, song)`：强制绝对路径 + 必须是 root 的直接子目录 + 拒绝盘符根。
  原版 `os.path.join('', 'song')` 会变成相对路径，实测会删掉当前工作目录下的文件夹。
* MTP 后端**故意不实现删除**（MTP 删除无法可靠确认且设备无回收站），只提示手动处理。

### 4.5 MTP 的两个硬约束（写进代码注释了，改之前别忘）

1. `Folder.CopyHere()` **不会覆盖**已存在的同名文件夹，只会跳过冲突文件；
   缺 `FOF_NOCONFIRMATION` 时还会弹「替换/跳过」对话框**一直等人点**（这就是用户报的弹窗）。
   现在的做法：标志位用 `1556`（不弹窗）+ `-Replace` 先把目标同名文件夹**改名挪开**再复制。
2. 删除 shell/MTP 条目要 **10~16 秒**且 Windows 还会弹确认框，所以 `-Replace`
   **不自动删除**备份，旧内容留在 `<名字>__旧版本_questbak` 由用户清理。

细节与实测数据见 `docs/06_Quest3连接与文件夹选择.md`。

### 4.6 PowerShell 脚本必须存成 UTF-8 **带 BOM**

`shell_probe.ps1` / `shell_copy.ps1` 里有中文路径与中文提示。
Windows PowerShell 5.1 读"无 BOM 的 UTF-8"会按 GBK 解码 → 中文参数变乱码 → COM 调用失败。
**改这两个脚本后务必确认仍是 UTF-8 BOM**。
`tests/static_check.py` 第 2 项已自动守这条（既查"少了 BOM"也查"多了 BOM"），
**编辑工具会静默去掉 BOM**，被它抓到过两次，改完直接跑 `static_check` 即可。

---

## 5. 常见任务怎么做（操作手册）

> **已移到 [`docs/04_迭代计划.md`](docs/04_迭代计划.md) 第 5 节** —— 那里和"剩余任务"放在一起，
> 避免"要做什么"和"怎么做"分居两份文档。

---

## 6. 已知限制与踩坑记录

> **已全部移到 [`docs/03_踩坑记录.md`](docs/03_踩坑记录.md)**（全文档连续编号 1–54：
> 第 1–9 条是「已知限制」，第 10–55 条是「踩过的坑」）。
>
> **遇到怪现象先搜那一份**，本文档不再重复维护这份清单 ——
> 曾经两边各写一份，结果本文档里的编号一度重复（两个 `21.`，其后 12 条全部错位），
> 而 `docs/03` 里的更新又漏了过来。**同一份信息只留一个地方**。

---

## 7. 迭代清单（ROADMAP）

> **已移到 [`docs/04_迭代计划.md`](docs/04_迭代计划.md)** ——
> 那里是迁移前的任务状态来源；现仅保留候选与历史清单（剩余任务 / 优先级 / 验收标准 / 已完成归档 / 交接检查表）。

---

## 8. 交接检查表（离开前请逐项确认）

> **已移到 [`docs/04_迭代计划.md`](docs/04_迭代计划.md) 第 6 节。**

---

## 9. 联系上下文（为什么会有这些设计决策）

如果对某个决策有疑问，先查这几处，通常都能找到"当时实测到了什么"：

| 疑问 | 去哪看 |
|---|---|
| 为什么要重构成 22 个模块？ | `docs/01_代码审查-原版问题清单.md` |
| 原版到底有哪些坑？有证据吗？ | 同上（含行号、复现脚本、实测输出） |
| 为什么复制前要校验"源≠目标"？ | `docs/05_改进记录-重构前后对照.md` 第 1 节 |
| 为什么颜色要抽成令牌？ | `docs/02_UI设计说明.md` 第二节 |
| 为什么 MTP 要改名挪开而不是覆盖？ | `docs/06_Quest3连接与文件夹选择.md` 第五节 |
| 为什么不用 `windnd`/`tkinterdnd2`？ | `quest_tool/ui_dnd.py` 头部注释（自建 WM_DROPFILES，零依赖） |
| 为什么封面墙要虚拟化？ | `quest_tool/ui_coverwall.py` 头部注释（实测 40 首 2.27 秒 → 852 ms） |
