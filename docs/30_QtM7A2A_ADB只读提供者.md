# Qt M7A2A · ADB只读提供者

更新时间：2026-10-21T17:55:08+08:00。M7A2A ADB只读提供者自动化验收完成：Qt18/18、旧完整28/28退出0，默认静态7项通过。新增专项中英三档覆盖真实AdbBridge参数与拦截传输、显式CLI/Qt接线、身份变更、取消及备份过滤。没有访问真实ADB设备；Qt MTP/盘符提供者、无线连接和设备写删未接入；正式20.4、Qt阶段M7A2A，本批停止写入。

## 使用和范围

在项目根目录运行：

```powershell
python main_qt.py --library --quest-adb
# 如需指定现有ADB程序：
python main_qt.py --library --quest-adb --adb-path "C:/Android/Sdk/platform-tools/adb.exe"
```

默认main.py不变；普通main_qt.py --library不启用设备提供者。仅显式--quest-adb才注入，仍不自动刷新、不自动选第一个候选、不自动读取。用户需点击刷新、选择具体设备和目录、再点击读取。--adb-path仅与--quest-adb组合，--quest-adb仅与--library组合；使用既有独立Qt预览数据和偏好。运行上述命令只是提供使用方式，本批测试没有启动真实设备实例。

本批是M7A2的ADB小批，不代表全部设备功能迁移完毕。没有MTP/盘符提供者、无线连接/配对、复制/删除或设备端写入。读取元数据，不拉取整首音频；时长仅来自元数据已有字段，缺失保持未知，封面/音频头尾补时长、更多设备后端及大库性能留后续。

## 实现与护栏

adb_provider.py复用AdbBridge.run、AdbDevice.is_quest、QUEST_PROBE_PROPS、官方候选根、decode_json_bytes及parse_info_file。独立解析devices输出以保留失败，不修改旧AdbBridge/AdbFS或传输护栏。所有设备命令使用明确-s目标，只有devices、getprop、目录检查/readlink、ls、受限head元数据读取；不调用ensure_device自动选择、push/pull、写删、connect/pair/tcpip/disconnect，也不恢复旧正式设置中的目标。

不采信devices -l的model。每次读取前后查询真实设备报告的产品属性与ro.serialno，设备键含传输标识和硬件序列号，再与canonical根一起参与既有稳定身份计算；同IP更换序列号、断开、根变化等拒绝结果。空属性保留行位置，缺少可用硬件序列号则拒绝。这是报告属性的一致性检查，不是加密硬件认证，不保证识别伪造属性的系统。

根只允许既有候选及其/sdcard到/storage/emulated/0等价别名；readlink -f确认后去重，未知规范根拒绝。读取前后复核连接/身份/根。文件夹先整体校验，再沿用隐藏目录和BACKUP_SUFFIX过滤及大小写排序。目录/Info文件符号链接不读取，引用用POSIX quoting；每份JSON最多读取1MiB+1并拒绝超过1MiB，异常/缺失JSON保留原解析错误行。目录文本超过16×1024×1024字符、超过100000行、重复/越界名称等拒绝，不把命令失败当空库。只读检查不等于跨命令原子快照或密码学防伪。

每条底层调用timeout不超过10秒、每次任务总预算120秒；最多接受16个传输条目。取消在调用前后检查，UI通过既有generation/身份护栏丢弃迟到结果；正在执行的同步ADB命令仍需等返回或超时，未实现瞬时强制终止真实ADB。逐目录读取有大库耗时限制，未宣称真实大库性能达标。

## 测试和证据

证据：E:/AI-Project-Backups/P006_Qt_M7A2A_20261021T023015。results.json、18份Qt日志与full.log分别记录18/18、28/28；默认静态记录static-acceptance-default.log。157份正式Python源码/测试与validation-final隔离副本同哈希；五份正式运行数据SHA256未变。

新增smoke_adb_provider使用实际AdbBridge.run参数构造，对subprocess.run封闭拦截，只接受临时假ADB路径，并禁止Popen，六种中英/缩放组合均通过；验证fresh属性/空行位置、硬件更换、根别名、模型伪装的非Quest、离线、元数据解析/大小限制、取消/总预算、拒绝目录失败、隐藏/覆盖备份过滤、默认无I/O、有效及无效CLI到Qt控件的接线。它证明模拟传输契约，不证明Android toybox/readlink/head或真实Quest兼容性。真实USB/已连接无线只读验收另行授权。

初轮冻结后复核发现需保留旧BACKUP_SUFFIX过滤，停止了本批自有隔离测试进程并留档stopped-owned-tests.json、source-correction.json及pre-filter-validation；补齐后重新建立validation-final并完整验收。不把中间未完成的回归算最终通过。另一次旧完整在MTP组报复制脚本无输出且未形成最终汇总，保留full-incomplete-mtp.log；同157份源码的独立MTP续验1/1通过，再执行新的完整回归。未确定这次无输出的精确原因，不称其已修复。原音频等待阈值、旧截图基线及业务断言未放宽。

本批8份代码/测试文件（含两份新增）；其余仅管理、使用说明和交接文档。adb.py、device_adb.py、device_mtp.py、fsapi.py、workers.py、音频、数据库及下载业务保持本批前哈希。没有安装升级、构建打包、提交推送或系统设置调整。

## 停止、后续与回滚

本批完成即停止。下一候选为M7A2后续只读后端/媒体与大库优化；真实设备测试、无线及写删需要单独明确范围，不自动开始。音频此前延迟根因仍未确认；本轮真实试听回归通过不代表永久修复或用户听感验收。

before/与before-hashes.json是本批前快照；phase-files.json记录变更前后哈希，rollback_m7a2a.py默认只校验预览，显式--apply才回退本批到M7A1验收状态，不reset/clean Git或覆盖其他WIP。
