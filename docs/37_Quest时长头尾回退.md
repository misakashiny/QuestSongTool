> 最新（2026-10-22T21:42:29+08:00）：Quest3有效歌曲读取已恢复，快照仍不完整：真实隔离Qt窗口从SongCore读取216首，解析错误0，已知时长209、未知7，耗时66.53秒；另有1个非UTF8名称目录保留并提示，未改名或删除。分批/逐首8首及头尾时长正向复验通过。异常目录隔离与完整对比/报告/传输阻断自动化验收完成：Qt29/29、旧完整28/28退出0、默认GBK静态7项通过；181份源码/测试同隔离副本哈希，正式五项运行数据未变。真实设备验证未执行写入、复制删除或播放；电脑隔离回归包含本地真实音频测试，系统设置未更改；不宣称全部217目录完整可用。 详见根03/04及docs/47。

# M7A2F · Quest歌曲时长有界头尾回退

更新时间：2026-10-21T22:52:13+08:00。M7A2F Quest时长回退自动化通过，真机接入正向复验待完成：Qt22/22、旧完整28/28（退出0），默认GBK静态7项通过。原8首有界头尾取到正时长，后续新CLI因原SongCore根及父目录不再存在而拒绝读取；未自动改库或恢复目录。164份源码/测试与隔离副本同哈希，正式五份运行数据未变。正式20.4、Qt界面M7A2C保持，采样工具M7A2F。本批停止写入。

## 实际原因与现场证据

沿用用户已选SongCore/CustomLevels和之前8首，不自动扩到其他库。开始枚举出现另一设备，本批固定使用原Quest完整传输标识，未对另一设备探测属性或文件。实际8份Info均为v2.0.0，提供歌曲音频文件名但没有解析器支持的整曲时长字段；仅见_previewDuration，属于试听片段，不能作为整曲时长。

在同一已验证根内，每首先stat确认音频大于192KiB，再只读65536字节头及131072字节尾。8份都是OggS，复用既有duration_from_parts解析出秒数：263.839, 194.025, 288.000, 205.180, 205.335, 240.001, 215.495, 29.753。总传输1572864字节，头尾探测阶段2.419230秒；未拉取整曲、未播放或解码音频，未写设备。数据/哈希见metadata-audit.json、info-*.json、audio-head/tail-*.bin和duration-audit.json。

之后用冻结的新CLI --duration --limit 8 --execute复验，退出2、device_roots。独立复查确认原规范根、/sdcard别名和SongCore父目录均返回No such file or directory；设备仍报告Quest 3。见sample-duration-stdout.json、sample-duration-stderr.log、sample-duration-execution.json、root-recheck.json、root-diagnostics.json。回归结束后root-after-regression.json记录再次复查，原根仍不可用。原因未确认，不归因用户删除或产品操作；本批没有设备写入命令，没有自动新建、恢复目录或切换SongLoader/WIP。此前成功的原始头尾解析不等于新接线已完成真机正向验收。

## 接入方案

新增adb_duration.remote_duration，复用song_duration.audio_filename及duration_from_parts，不改音频引擎。允许根常量移入adb_metadata，provider原READ_ROOTS导入位置仍可用。名称只允许歌曲内单个文件名，拒绝空/点目录、分隔符和控制字符；每次命令检查根规范路径、目录/音频非符号链接及文件存在。只stat/head/tail，不写临时文件或拉取整曲。

Qt ADB提供者read_library在元数据解析成功且总时长未知时调用回退，已知时长不额外读音频。每首内容上限192KiB，文件不大于192KiB时跳过，避免完整读取小音频；这种小文件的时长仍未知。不支持格式、缺文件、权限/命令失败、非法stat或头尾长度不符保留未知。取消、断开、身份变化和超时继续失败退出，不发布部分快照；读取结束的设备/根复核仍保留。

独立采样工具新增显式--duration（API probe_audio=True）；默认不增加音频读取。先做原两路Info/SHA256对照，再为未知时长采样行读头尾，输出duration_sources=metadata/audio_header/unknown和audio_duration_requested，并最终复核身份。equal仍指两路Info一致，头尾时长不是独立双路解码交叉验证；原始SHA256仍是Info内容哈希，不是音频哈希。默认计划模式即使带--duration也不访问设备。

```powershell
# 使用明确设备/已确认目录；SERIAL是占位符。默认不加--execute只输出计划。
python -m quest_tool.qt_ui.adb_sample --serial SERIAL --root /storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels --limit 8 --duration
```

Qt界面阶段仍M7A2C，提供者内部行为新增回退；采样JSON阶段M7A2F。正式APP_VERSION仍20.4，旧main.py及旧Tk业务未改。

## 验证与性能边界

新增smoke_adb_duration：中英三档缩放，封闭模拟传输、禁止Popen、不播放音频；验证provider的时长回退、已知时长跳过、缺失/小文件/权限/截断/超大响应、文件名越界、根白名单、取消/超时/身份更换/断开、采样默认不读音频和显式头尾来源，以及真实Qt窗口行数据。原时长格式解析由既有duration组覆盖。符号链接拒绝条件在生成命令中保留；本批不在真实Quest上创建链接来测试。

最终Qt22/22；失败项：无。旧完整28/28退出0，默认静态7项通过。证据：E:/AI-Project-Backups/P006_Quest_duration_20261021T213329，duration-first.log、各smoke日志、results.json、full.log和static-final-default.log。未改旧业务断言、音频等待阈值、截图基线或自检隔离护栏。

每个未知时长目前最多增加stat/head/tail三次命令和192KiB内容读取，后台执行不触碰GUI；单命令10秒、任务总预算120秒不变。大库额外耗时/USB负载及整库完成率未实测，不能把8首探测耗时外推为217首性能通过；极大库可能触及既有任务预算，后续应分别度量再考虑批量/缓存/按需方案。没有为时长放宽读取超时或暗中整曲下载。

## 状态与回滚

自动化条件本轮满足，新接线真机正向验证仍待原目录恢复可用后续验；不自动改读其他库，不伪造正向通过。本批无数据库结构、下载、音频引擎、设备写删、系统设置、依赖安装、打包或提交变更。

6份代码/测试、14份文档，共20份。before/、before-hashes.json、source.json和phase-files.json保存原件与边界；164份源码/测试与隔离副本一致，正式五份运行数据SHA256不变。rollback_duration.py默认仅预检，显式--apply才按哈希恢复本批文件，不执行任何设备命令，不reset/clean。根03/04记录本批停止点。
