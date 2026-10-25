"""Quest歌单对比工具 - 优化重构版（模块化包）。

原版是单文件 4155 行脚本，这里拆分为职责单一的模块：

    constants.py  常量 / 主题色 / 应用信息
    logging_setup.py  日志（延迟初始化，不再在 import 时写相对目录）
    storage.py    运行目录解析 + settings.json 读写（原子写）
    database.py   SQLite 缓存（单连接 + WAL + 批量查询）
    songs.py      info.dat 解析（Beat Saber v2/v3/v4）与歌曲数据结构
    fsapi.py      本地 / Quest 设备统一的文件系统抽象
    device.py     设备发现（盘符 / adb / MTP 便携设备）
    clipboard.py  封面图等二进制数据缓存
"""

from .constants import APP_VERSION as __version__  # 版本号只在 constants.py 里写一份

__all__ = ["__version__"]
