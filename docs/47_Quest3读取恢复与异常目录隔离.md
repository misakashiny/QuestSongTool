# Quest3读取恢复与异常目录隔离

更新时间：2026-10-22T21:42:29+08:00。Quest3有效歌曲读取已恢复，快照仍不完整：真实隔离Qt窗口从SongCore读取216首，解析错误0，已知时长209、未知7，耗时66.53秒；另有1个非UTF8名称目录保留并提示，未改名或删除。分批/逐首8首及头尾时长正向复验通过。异常目录隔离与完整对比/报告/传输阻断自动化验收完成：Qt29/29、旧完整28/28退出0、默认GBK静态7项通过；181份源码/测试同隔离副本哈希，正式五项运行数据未变。真实设备验证未执行写入、复制删除或播放；电脑隔离回归包含本地真实音频测试，系统设置未更改；不宣称全部217目录完整可用。

## 现场原因及处理

1. 头显最初未开机，ADB仅枚举到模拟器；开机后识别为Quest 3/eureka/oculus、已授权USB ADB。没有对模拟器读取歌曲或变更任何连接设置。
2. 开机后当前用户为RUNNING_LOCKED，/storage/self/primary不可用，/storage/emulated/0仅为空挂载点，候选目录均不可见。用户在头显进入主界面后变为RUNNING_UNLOCKED，SongCore路径恢复。仅能确认本次现场原因，不能据此倒推所有历史目录消失都由锁定导致。
3. 当前实际根：/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels。发现3个去重候选，仅选择既有SongCore根读取。217个候选中216个名称为有效UTF8；另1目录原始名称字节E79F无法按UTF8解码。该目录真实存在并包含21字节info.dat，未删除、改名或改内容。
4. 原AdbBridge文本解码使用replace，把该名称变为U+FFFD。首轮实际GUI读取末批返回8帧、预期9帧，decode_batch以device_protocol拒绝，整库未发布。命令退出码为0不代表帧完整；没有放宽原完整帧断言。原始commands/last-batch-stdout及attempt-1保留。外部首次GUI异常钩子未打印失败栈，后续独立末批重放确认帧不完整；不把首轮说成通过。

## 改动与保护

folder_listing对二进制目录列表逐项严格UTF8解码；无法解码的名称不会被猜测、替换为另一个路径或用于远端命令。合法中文和真实U+FFFD字符保持原样。大小、数量、控制字符、身份、根、取消和超时限制不放宽。

提供者与采样CLI透传skipped_names；DeviceClient严格校验计数。DeviceBrowser显示已读歌曲数、异常目录数和不完整提示，正常快照保持既有两参数回调，不破坏旧包装器。LibraryWindow保存不完整标记并清空完整差异结果；报告与传输协调器在不完整时拒绝生成任务。清除或成功完整重读能恢复原状态，设备本身不被修改。

这是一份216首可浏览的不完整快照，不是217个目录均已读成功。完整对比、报告及传输当前暂不可用，不能绕过标记。后续可单独研究无损读取异常原始文件名；如需改设备目录名称或删除，必须先准备明确对象及可回退方式并获得授权。本批没有以假目录名补齐数量。

## 实机验证

- 最终同版本实际Qt DeviceBrowser执行发现→选择SongCore→读取，216首出现在quest_model与表格，0个解析错误，66.53秒；209首时长已知，7首保留未知。该次GUI链路记录683条只读ADB调用；这不是本轮全部探索命令的总数。
- 最终CLI默认8首，分批与逐首Info/SHA256/解析一致；8首均由有界音频头尾得到正时长，skipped_names=1，available_folders=216。结果sample-final.json。先前8首采样在异常目录之前，因此旧采样通过不等于完整读取通过。
- 未知7首的文件头均含Ogg Theora标识；已有有界解析器针对Vorbis/Opus等，当前未得到有效结果。只补读各64字节文件头和文件大小以核对，没有整曲下载、解码或播放，也没有把未知改成猜测值。详见unknown-duration-audit.json。
- GUI验证使用E盘独立预览数据目录及显式目标过滤，只识别所选Quest；没有正式实例、复制/导入/推送/删除/无线连接操作。Qt后台仅消息，主线程更新模型。
- 稳定布局在1000/1400逻辑宽度下提示所需与实际高度均48像素；首次即时截图尚未完成布局，稳定截图另存，不把首帧截断误记为产品修复。原生USB其余接口驱动状态不作为ADB链路成功与否的替代结论。

## 自动化结果与文件

最终默认GBK静态7项通过；Qt29/29、旧完整28/28退出0，未改旧专项断言、等待阈值、截图基线或依赖。181份正式源码/测试与隔离副本同哈希，settings.json/downloads.json/数据库及WAL、SHM五项不变。

代码/测试10文件：

- `quest_tool/qt_ui/adb_metadata.py`
- `quest_tool/qt_ui/adb_provider.py`
- `quest_tool/qt_ui/device_protocol.py`
- `quest_tool/qt_ui/device_browser.py`
- `quest_tool/qt_ui/library_window.py`
- `quest_tool/qt_ui/library_report.py`
- `quest_tool/qt_ui/library_transfer.py`
- `quest_tool/qt_ui/adb_sample.py`
- `quest_tool/messages.py`
- `tests/qt/smoke_adb_names.py`

另同步17份文档，精确27文件由phase-files.json记录。正式APP_VERSION20.4、默认Qt标识M7A2C保持，未切换默认入口或构建EXE。

## 使用方式

Quest开机后先在头显完成解锁并进入主界面，再打开桌面“Quest 歌曲工具（新版曲库）”。在Quest栏点“刷新候选”，选择SongCore/CustomLevels，再点“读取快照”。本次216首约67秒，期间保持USB连接。只刷新候选不会读取整库；读取快照会扫描所选目录。

若有异常目录，先浏览正常歌曲并查看提示；不要把不完整状态当作整库完成，不自动清理设备。若刚开机找不到目录，先完成头显解锁后重试；其他缺失原因仍需现场核对。

## 证据与回滚

证据目录：`E:/AI-Project-Backups/P006_Quest_read_verify_20261022T210521`。gui-result.json/snapshot.json/commands-final.json/live-final.log为最终实机记录；sample-final.json为最终8首对照；results.json及full.log为本批回归；layout-review.json和quest-library-settled-*.png为稳定界面证据；source-final.json/runtime-before.json为哈希边界。

首轮回归旧回调包装不接受skipped关键字的问题已修正为正常路径不传新增参数；原断言保留，并重新从头跑全套。attempt-1保留失败和中止日志。attempt-notes.json保留外部脚本与截图时序问题，不隐去失败。

rollback_read.py默认只预检27文件，显式--apply才按交付哈希恢复；不触碰设备或运行数据，不reset/clean。实际回滚未执行。异常目录和7首未知时长继续登记，不扩展到歌单或真实传输任务。
