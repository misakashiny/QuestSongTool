# 当前DPI只读核验与原生拖动阻塞

时间：2026-10-24T21:13:39+08:00。状态：当前DPI只读测量完成；原生移动/缩放/残影未执行验收。

## 结果

当前DPI只读核验已记录，原生拖动现场未验收。无残留上批探针；正式pythonw曲库实例25972保留，不作为输入目标。两次独立隔离下载窗口在XG27ACG报告2560×1440、原生DPI96、DPR1、per-monitor awareness2；首次show/150ms及退出前Qt逻辑尺寸与原生客户区一致，没有复现尺寸放大。computer-use两次激活均报foreground window did not report a process id；第二次辅助功能树标题正确但截图不是目标，未发鼠标/键盘输入，无进入/退出移动缩放消息。两窗口约40/42秒提前退出，第二次捕获WM_CLOSE，关闭来源未确定；不能把它归为产品崩溃或工具唯一根因。首个外部探针退出存在事件过滤器访问已删除控件警告，已在外部脚本卸载过滤器，1.5秒正常退出自检无警告；不是产品修复。221份源码/测试及五项正式运行数据未变；无网络、音频、设备、系统设置或正式实例输入/停止操作，不运行完整回归，不宣称高DPI/拖动/残影已解决。

当前单窗口DPI96对应100%比例；不能外推其他显示器或Windows125%/175%的行为，也不解释历史首次resize异常。没有加除以倍率、禁用DPI或强制二次resize补丁。

## 实际执行

证据：`E:/AI-Project-Backups/P006_native_drag_20261024T210057`。从当前源码复制221份到validation，下载窗口只使用上一批已保存的21首公开元数据与封面缓存副本；不绑定搜索、下载、音频或设备。原生Win32 API仅用于读取客户区、窗口矩形、DPI和awareness。所有拟议桌面输入仅通过computer-use的sky API，未绕过为Win32输入或其他桌面操控。

1. 执行前从磁盘读五件套、核对Git和源文件，发现正式pythonw实例25972（main_qt.py --library --quest-adb）在运行并保留。未发现上批公开联网探针残留。
2. 首次list_apps重复返回同一个隔离HWND，未据此操作；改用list_windows取得唯一HWND3739894。捕获不是目标、激活报错，刷新后无目标候选。首个进程在约40.18秒退出（自动超时设为480秒），关闭来源未知。
3. 首个探针进程退出码0并打印PASS只表示测量程序退出，不代表拖动通过；其atexit有SongView已删除时事件过滤器调用警告。initial保留脚本和结果，外部探针增加退出时卸载过滤器及WM_CLOSE记录。
4. 短重试HWND1315468、PID28324，辅助功能树清楚列出隔离标题/标题栏/下载控件，截图仍不匹配。再次激活同样报错，未依据错误截图使用坐标或输入按键。download-final记录约42.48秒收到WM_CLOSE、42.55秒退出；未记录WM_ENTERSIZEMOVE/WM_EXITSIZEMOVE/WM_SIZING/WM_MOVING。不能断定WM_CLOSE由哪个进程或用户发起。
5. 清理前已核对本批PID及命令路径；两个互动探针在执行停止检查前均已退出，所以没有实际调用Stop-Process。owned-process-before-stop.json不存在，不应虚记为强杀。另以1.5秒自动关闭的独立探针验证卸载过滤器的正常退出路径，lifecycle.log退出0、无原atexit警告。没有继续发桌面输入。

## 只读尺寸与心跳

两次首次显示均1280×824逻辑像素、1280×824原生客户区，DPR1、GetDpiForWindow96、awareness2；150ms后保持一致，屏幕名XG27ACG。Qt6.12.0，无QT_环境变量，记录__COMPAT_LAYER=DetectorsAppHealth，不认定其作用或来源。

|观察进程|观察时长|10ms心跳P95|最大间隔|控件数量|原生拖动|
|---|---:|---:|---:|---|---|
|首次|40.18秒|16.58ms|106.57ms|104 → 104|未执行|
|短重试|42.55秒|16.88ms|99.81ms|104 → 104|未执行|

这些间隔包含空闲、状态捕获和关闭过程；不是鼠标拖动输入延迟或显示帧率。没有以任意阈值判性能通过。由于无法可靠获取目标画面，不能评价拖动中的桌面残影、逐帧重绘或外部区域残留。曲库5000行原生拖动分支未启动，不记为验过。

## 工具边界与后续停点

使用[computer-use SKILL.md](C:/Users/Administrator/.codex/plugins/cache/openai-bundled/computer-use/26.1016.62342/skills/computer-use/SKILL.md)，其[guidance.md](C:/Users/Administrator/.codex/plugins/cache/openai-bundled/computer-use/26.1016.62342/docs/guidance.md)要求：

> If state capture or window activation fails, stop using prior coordinates or element indexes.

并要求刷新选择后只重试一次、失败报告原错误。因此本批停止输入，不将另一窗口的画面作为坐标依据，也不使用其他方式绕过。输入阻塞记录见input-blocker.json；截图不一致与提前退出均为观测，根因待确认，不直接归咎GUI框架。

后续仅在能够稳定选择/捕获同一个隔离窗口的桌面会话继续：先验证标题和画面一致，再做标题栏移动、窗口边缘缩放及滚动，核对原生进入/退出移动缩放消息；最后才讨论残影或性能补丁。不自动改显示/DPI/远程控制设置、不重启正式程序。若工具仍不可用，不重复生成相同无效验收或扩大新功能。

## 源码、数据与回滚

产品/测试221份同前态与隔离副本SHA256，AST全部可解析，五项正式settings/cache/WAL/SHM/downloads存在性/哈希不变。上批Qt51/51、旧28/28只作同字节历史基线，本批未重跑完整回归。正式实例25972在收尾仍运行；本批没有向它发出输入或停止命令，无真实网络/设备/播放，也未改系统设置。

仅更新8份文档：本报告、AGENTS、根02/03/04、CHANGELOG、docs索引和迭代计划。before保留原字节，phase-files.json列前后哈希，rollback.py默认只预检，显式--apply且最终哈希仍一致才恢复本批文档。保留全部用户既有修改；不构建/安装/提交。
