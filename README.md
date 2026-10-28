# Quest 歌曲工具

Windows 桌面工具，管理 PC 与 Quest 的 Beat Saber 曲库，支持发现歌曲、歌单、试听、版本比较及确认后传输。当前界面为 **PySide6 + Qt Widgets**，版本由 `quest_tool/constants.py` 定义。

## 启动

- **单文件版**：双击 `dist/QuestSongTool.exe`，不需要另装 Python、Qt 或 ADB。`dist/` 为本机构建产物，GitHub 通过 [Releases](https://github.com/misakashiny/p006-quest-song-tool/releases) 提供下载。
- **源码版**：双击 [启动.vbs](启动.vbs)；[创建桌面快捷方式.vbs](创建桌面快捷方式.vbs) 可创建曲库和下载页入口。
- EXE 默认打开曲库，右上角进入下载歌曲；命令行 `QuestSongTool.exe --downloads` 直接打开下载页。

## 文件目录

| 位置 | 用途 |
| --- | --- |
| `quest_tool/` | 当前 Qt 界面与共享业务源码；兼容代码保留，避免破坏回退及原测试 |
| `tests/` | 功能、边界、UI和音频测试，以及测试夹具、图像基线 |
| `docs/` | 使用说明、架构、验证、变更与归档资料，文档统一入口 |
| `packaging/` | 当前 Qt 单文件打包配置、独立入口、构建依赖和第三方声明 |
| `packaging/legacy/` | 旧 Tk 打包配置，保留历史构建用途 |
| `tools/` | 可复用构建脚本 |
| `dist/`、`build/` | 本机可再生成的发行与构建产物，不进入源码仓库 |

## 文档

- [打开软件与日常使用](docs/07_怎么打开软件.md)
- [单文件版、构建与发布](docs/单文件版与发布.md)
- [开发与架构](docs/开发与架构.md)
- [验证与已知问题](docs/验证与已知问题.md)
- [变更摘要](docs/变更摘要.md)
- [归档与恢复](docs/归档与恢复.md)
- [完整文档目录](docs/README.md)

## 构建

现有构建环境下运行 `python tools/build_release.py`。依赖版本见 `packaging/requirements-build.txt`；构建脚本不会自动安装或升级依赖。可用 `--adb-source` 指定 Android platform-tools 目录，`--output` 与 `--work` 指定产物和中间目录。

单文件 EXE 会在启动时解出运行组件，设置与评分继续保存在电脑的独立运行目录；移动 EXE 不会打包或迁移个人数据。只打开程序不会自动连接、读取或写入 Quest。

源码上传到已有的[私有 GitHub 仓库](https://github.com/misakashiny/p006-quest-song-tool)。配置、数据库、下载记录、日志、歌曲、运行镜像和传输暂存不进入源码包；本机完整备份另行保留。项目不设置多 AI 独占开发限制。
