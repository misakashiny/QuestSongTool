"""全局常量与文案。

原版把这些值散落在各处（同一个蓝色 `#3498db` 出现十几次），集中到这里后改一处即可。

**这里必须是唯一来源**：真机上踩过两次"同一个东西写两份、值还不一样"的坑 ——
`WINDOW_SIZE` / `MIN_SIZE` 曾在 `app.py` 又写一份（且值不同），
`QUEST_CUSTOM_PATHS` 与 `adb.QUEST_CUSTOMLEVELS_CANDIDATES` 则完全重复。
**加常量前先搜一遍有没有现成的。**

v14.5 清理：删掉 23 个零引用常量 —— 15 个旧 `COLOR_*`、3 个 `ROW_BG*`、
`APP_TITLE`、`COVER_SIZE`、`BEAT_SABER_PACKAGES`、`AUDIO_EXTENSIONS`、
`COVER_CACHE_LIMIT`，以及与 adb 候选表完全重复的 `QUEST_CUSTOM_PATHS`。
配色早在重构时就迁到 `theme.py` 的语义令牌了，这些旧常量是当时忘了删的。
"""

from __future__ import annotations

import os

# ---------------- 应用信息 ----------------
#: 程序标识（ASCII），用于**打包名 / 数据目录 / 日志名**，不要翻译。
APP_ID = "QuestSongCompare"
#: 显示用名称。**不要在 import 期调 tr()** —— 那样切语言后不会变；
#: 界面统一用 messages.tr("app.name")，这里只作为兜底与回退。
APP_NAME = "Quest歌单对比工具"
APP_VERSION = "20.4"

# ---------------- 窗口尺寸 ----------------
# 这里必须是**唯一**定义：原先 constants.py 抄了原版的 (1500x950 / 1280x820)，
# 而 app.py 又自己写了 (1620x1000 / 1240x780)，两份值不一样且没人发现 ——
# app.py 用的才是实际生效的那份。已合并到这里，app.py 直接引用。
WINDOW_SIZE = "1620x1000"
MIN_SIZE = (1240, 780)

# ---------------- 字体 ----------------
# 用户可在设置里改字体，Windows 缺失时回落到系统默认
FONT_FAMILY = "Microsoft YaHei"
FONT_FALLBACKS = ("Microsoft YaHei UI", "微软雅黑", "SimHei", "TkDefaultFont")


def resolve_font(root=None) -> str:
    """挑选一个当前系统真实存在的字体，避免中文显示成方块。"""
    try:
        import tkinter.font as tkfont

        families = set(tkfont.families(root)) if root is not None else set()
        if not families:
            return FONT_FAMILY
        if FONT_FAMILY in families:
            return FONT_FAMILY
        for name in FONT_FALLBACKS:
            if name in families:
                return name
    except Exception:  # pragma: no cover - 无 GUI 环境下直接回落
        pass
    return FONT_FAMILY


# ---------------- 业务常量 ----------------
# Quest 上 CustomLevels 的绝对路径，按"现代 -> 历史"排序。
# 注意：现版 Beat Saber(1.35+)/SongCore 用的是 ModData/SongCore；
#       而 /sdcard/Android/data/com.beatgames.beatsaber/files/ 里现在主要是存档
#       （PlayerData.dat / AvatarData.dat / settings.cfg），仅作兼容兜底。
# 这是**唯一**一份候选表（原先 constants.py 与 adb.py 各有一份、内容完全相同）。
QUEST_CUSTOMLEVELS_CANDIDATES = (
    "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels",
    "/storage/emulated/0/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels",
    "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongLoader/CustomLevels",
    "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomWIPLevels",
    "/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels",
    "/storage/emulated/0/Android/data/com.beatgames.beatsaber/files/CustomLevels",
    "/sdcard/Android/data/com.beatgames.beatsaber.demo/files/CustomLevels",
    "/sdcard/CustomLevels",
)

CUSTOM_LEVELS_RELATIVE = os.path.join(
    "Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"
)
# MTP 下逐层下钻时优先尝试的相对路径
MTP_CUSTOM_LEVELS_RELATIVE = (
    ("ModData", "com.beatgames.beatsaber", "Mods", "SongCore", "CustomLevels"),
    ("ModData", "com.beatgames.beatsaber", "Mods", "SongLoader", "CustomLevels"),
    ("Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"),
)
# Quest 上该目录的几种写法（不同 Android 版本/语言）
QUEST_FOLDER_HINTS = (
    "内部共享存储空间",
    "內部共用儲存空間",
    "Internal shared storage",
    "内部存储",
    "sdcard",
)
INFO_FILE_NAMES = ("info.dat", "Info.dat", "INFO.DAT", "info.json")

# ---------------- 设备（Quest）识别 ----------------
# 为什么需要这个：本工具只对 Quest 有效，但 adb 会把**任何**开着 USB 调试的
# Android 设备都列出来。原先 `ensure_device()` 取"第一个 usable 的设备"，
# 于是同时插着手机和平板时，工具会抓错设备 —— 表现为"怎么连到我手机了"。
#
# 判据取自 `getprop`（比 `adb devices -l` 里的 model 可靠，后者会被厂商改写）：
#   Quest 2 = hollywood / Quest Pro = seacliff / Quest 3 = eureka / Quest 3S = panther
# 实测对照（小米平板 M2105K81AC）：brand=Xiaomi, device=elish, characteristics=tablet
# —— 三个字段都不命中，会被正确排除。
#
# **未知设备不当作 Quest**（宁可提示"没找到 Quest"，也不要对着手机做读写删）。
QUEST_DEVICE_CODENAMES = (
    "eureka",      # Quest 3
    "panther",     # Quest 3S
    "hollywood",   # Quest 2
    "seacliff",    # Quest Pro
    "monterey",    # Quest 1
    "vrheadset",   # 部分系统的通用值
)
QUEST_PRODUCT_KEYWORDS = (
    "oculus",
    "meta",
    "quest",
)
#: `getprop` 里用来判断"这是不是 Quest"的字段（按可靠性排序）。
QUEST_PROBE_PROPS = (
    "ro.product.device",
    "ro.product.brand",
    "ro.product.manufacturer",
    "ro.product.model",
    "ro.product.name",
)
#: MTP 覆盖复制时，旧版本会被**改名挪到**这个后缀（见 `shell_copy.ps1` 的 `-BackupSuffix`）。
#: 它**不是歌曲** —— 列歌单时必须过滤掉，否则每替换一首歌就会多出一条
#: "无 info.dat 的占位歌曲"，用户还会以为复制出问题了。
#: ⚠️ 改这里的话，`shell_copy.ps1` 里的默认值要一起改（两处必须一致）。
BACKUP_SUFFIX = "__旧版本_questbak"
DIFFICULTY_ORDER = ("Easy", "Normal", "Hard", "Expert", "ExpertPlus")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")

# 保存进度/状态
QUEUE_POLL_MS = 80
SEARCH_DEBOUNCE_MS = 180
