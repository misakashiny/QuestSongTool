# Quest 3 真机只读发现验证

## 授权和执行边界

2026-10-21，用户明确授权“允许本次 Quest 3 只读验证”。本批按此前约定只做设备发现、产品身份和候选歌曲目录检查，不读取整库或少量歌曲，不启动GUI/音频，不改变ADB连接设置，不配对、安装、复制、删除或向设备创建文件。当前不实现新的采样入口；产品仍20.4、Qt M7A2C。

运行前核验160份源码/测试与上批交付相同，并核对M7A2D文档清单。执行从既有同哈希隔离副本导入AdbReadOnlyProvider和AdbBridge，不启动用户正式实例。本次调用实际ADB，不是模拟后端。只找到一台USB设备，因此没有自动在多个设备之间选择；若多设备则应先让用户明确选择。

## 实际结果

| 项目 | 结果 |
| --- | --- |
| 设备枚举 | 1台USB设备，ADB状态 device；传输标识仅公开尾号0J48 |
| 产品型号 | Quest 3 |
| 产品代号/品牌 | eureka / oculus |
| 身份判据 | 复用现有Quest判据，产品属性来自实际getprop，不使用devices的model作为身份兜底 |
| 前后身份复核 | 两次产品属性/硬件序列号检查一致 |
| 候选根检查 | 8个配置路径中4项返回目录，去重为3个实际目录，详见下表 |
| 歌曲元数据/歌曲数/封面/音频 | 未读取、未验证 |
| 分批与逐首一致性、head/base64/pipefail | 未执行，不作兼容结论 |
| 设备写入/复制/删除/无线连接改变 | 未执行 |

| 候选路径 | 本次规范路径结果 |
| --- | --- |
| `/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels` | `/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels` |
| `/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels` | 同上，属于同一个实际目录，不计为第二个库 |
| `/sdcard/ModData/com.beatgames.beatsaber/Mods/SongLoader/CustomLevels` | `/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongLoader/CustomLevels` |
| `/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomWIPLevels` | `/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomWIPLevels` |
| `/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels` | 未返回可用目录 |
| `/storage/emulated/0/Android/data/com.beatgames.beatsaber/files/CustomLevels` | 未返回可用目录 |
| `/sdcard/Android/data/com.beatgames.beatsaber.demo/files/CustomLevels` | 未返回可用目录 |
| `/sdcard/CustomLevels` | 未返回可用目录 |

目录存在不代表已确认其中有歌曲，也不代表其中Info文件可读取；没有进行文件内容访问或歌曲数量统计。“未返回可用目录”不扩大解释为设备上绝对不存在或权限已经验证。

## 命令与证据

共执行1次 `devices -l`、2次明确目标的产品属性getprop、8次明确目标的 `[ -d ]` 和 `readlink -f` 目录探测，共11次ADB调用。属性名、候选根与身份校验复用现有常量/实现；单命令不超过10秒，身份与目录检查任务预算120秒。记录中没有ls、head、exec-out、文件拉取、push、rm、mkdir、connect/pair/tcpip、kill-server等操作。

证据目录：`E:/AI-Project-Backups/P006_Quest_readonly_discovery_20261021T203258`。`inventory.json`记录原始设备枚举；`commands.json`记录10次身份/目录命令、耗时与结果；`discovery-result.json`记录汇总。原始序列号只在本地证据中保留，对外结论使用脱敏尾号。`source.json`、`runtime-before.json`及`verification.json`记录正式源码和运行数据前后哈希。

本批没有运行GUI、音频或完整产品回归；M7A2C的Qt20/20及旧完整28/28保持历史结果，不改写为本批真机歌曲读取通过。真实设备检查仅覆盖本页列出的发现/身份/目录层。

## 下一步与停止点

本批只读发现检查完成并停止。SongCore/CustomLevels可作为后续普通歌曲库验证的优先候选，但不据此断言它一定是用户实际使用的唯一歌曲库；SongLoader和WIP不自动合并。读取前应明确目标根目录。

仍建议先完成受限采样验收入口及离线测试，再对明确目录取少量元数据做分批/逐首对照。现有UI没有采样上限，不能因为目录已找到就自动点击整库读取；本批未执行该动作。原授权为本次只读检查，不扩展到设备写删、无线或系统配置。

本批只更新8份文档，源码和正式运行数据不变。`before/`与`phase-files.json`保存文档原件及前后哈希；`rollback_discovery_docs.py`默认仅预检，显式`--apply`才恢复本批文档，不执行设备操作。已执行的读取无法“撤销”，也没有需要恢复的设备内容变更。
