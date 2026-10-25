"""程序入口。

用法：
    python -m quest_tool            # 启动图形界面
    python main.py                  # 等价（仓库根目录下的入口脚本）

诊断：
    python -m quest_tool --doctor   # 打印环境自检（依赖/目录/adb/设备/便携设备）
    python -m quest_tool --scan     # 只做一次 Quest 位置探测并把结果打印出来

从原版（单文件版）迁移评分/评价：
    python -m quest_tool --migrate-legacy "D:\\旧版\\songs_cache.db" --location "local:D:\\Songs"
"""

from __future__ import annotations

import argparse
import sys


def _doctor() -> int:
    """环境自检：出问题时用户把这段输出发过来即可定位。"""
    from .constants import APP_NAME, APP_VERSION
    from .logging_setup import setup_logging
    from .messages import set_language, tr
    from .storage import Settings, data_dir, database_path, logs_dir, settings_path

    # 自检输出跟随用户当前的语言偏好（``--doctor`` 是给人看的）
    set_language(Settings.load().language)
    setup_logging()
    print(f"{tr('app.name_full')} v{APP_VERSION}")
    print(f"Python: {sys.version}")
    print(f"平台: {sys.platform}")

    for module in ("customtkinter", "PIL", "tkinter"):
        try:
            imported = __import__(module)
            version = getattr(imported, "__version__", "")
            print(f"[OK]   {module} {version}".rstrip())
        except ImportError as exc:
            print(f"[缺失] {module}: {exc}")

    print(f"数据目录: {data_dir()}")
    print(f"配置文件: {settings_path()} ({'存在' if settings_path().exists() else '不存在'})")
    print(f"缓存数据库: {database_path()} ({'存在' if database_path().exists() else '不存在'})")
    print(f"日志目录: {logs_dir()}")

    # 界面偏好：用户报"界面串了/字太小/英文没生效"时，这一行直接给出答案
    try:
        from .storage import Settings
        from .theme import detect_ui_scale
        from .messages import current_language

        prefs = Settings.load()
        print(f"界面偏好: 语言={current_language()} 缩放={prefs.ui_scale} "
              f"外观={prefs.appearance} 系统 DPI 推测缩放={detect_ui_scale()}")
    except Exception as exc:
        print(f"界面偏好读取失败: {exc}")

    try:
        from .adb import adb_status

        status = adb_status()
        print(f"adb: {status['adb'] or '未找到'}")
        if status["ok"]:
            for device in status["devices"]:  # type: ignore[union-attr]
                print(f"  设备: {device.get('model')} ({device.get('serial')}) state={device.get('state')}")
        else:
            print(f"  {status['reason']} —— {status['hint']}")
    except Exception as exc:
        print(f"adb 检测失败: {exc}")

    try:
        from .device_shell import ShellBridge

        bridge = ShellBridge()
        print(f"Shell 命名空间桥: {'可用' if bridge.available else '不可用'}")
        if bridge.available:
            devices = bridge.portable_devices()
            print(f"  便携设备(MTP): {[d.name for d in devices] or '未检测到'}")
    except Exception as exc:
        print(f"Shell 桥检测失败: {exc}")

    # 设备端 shell 能力探测：真机上这些命令**本身**可能不支持或有 bug，
    # 而离线测试发现不了（假 adb 是我们自己写的，实现得太正确）。
    # 详见 device.py 里 _CAPABILITY_PROBES 的说明。
    try:
        from .device import probe_capabilities

        capabilities = probe_capabilities()
        if capabilities:
            print("设备端 shell 能力（[缺失] 的那些命令在这台设备上不可用）：")
            for item in capabilities:
                flag = "OK  " if item.ok else "缺失"
                print(f"  [{flag}] {item.name:20} 用途: {item.why}")
    except Exception as exc:
        print(f"设备能力探测失败: {exc}")
    return 0


def _scan() -> int:
    from .device import scan_quest
    from .logging_setup import setup_logging

    setup_logging()
    report = scan_quest()
    print(f"adb: {report.adb_path or '未找到'}")
    if report.adb_error:
        print(f"adb 提示: {report.adb_error} / {report.adb_hint}")
    print(f"便携设备(MTP): {report.mtp_devices or '未检测到'}")
    if not report.candidates:
        for note in report.notes:
            print(f"说明: {note}")
        return 0
    for candidate in report.candidates:
        flag = "可用" if candidate.usable else "仅可浏览"
        print(f"[{candidate.kind_label}/{flag}] {candidate.display}")
        if candidate.note:
            print(f"    {candidate.note}")
    return 0


def _migrate_legacy(legacy: str, location: str) -> int:
    """把原版单文件版的 `songs_cache.db` 里的评分/评价导入新库（ROADMAP C3）。

    原版把评分混在 `songs` 表里、只按 `folder_name` 唯一；新版拆到了 `userdata`，
    键是 `(location, folder_name)` —— 所以必须告诉它"这些歌属于哪个位置"。
    幂等：重复执行不会产生重复行，也不会覆盖新版里改过的评分。
    """
    from .database import Database
    from .logging_setup import setup_logging
    from .storage import database_path

    setup_logging()
    db = Database(database_path())
    try:
        stats = db.import_legacy(legacy, location)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"迁移失败：{exc}")
        return 2
    finally:
        db.close()
    print(f"旧库：{legacy}")
    print(f"目标库：{database_path()}")
    print(f"位置标识：{location}")
    print(
        f"结果：旧库里有评分/评价 {stats['found']} 条，"
        f"新导入 {stats['imported']} 条，跳过 {stats['skipped']} 条（新版已存在）"
    )
    if stats["found"] == 0:
        print("提示：旧库里没有带评分或评价的记录，无需迁移。")
    return 0


def main(argv: list[str] | None = None) -> int:
    from .constants import APP_NAME

    parser = argparse.ArgumentParser(prog="quest_tool", description=APP_NAME)
    parser.add_argument("--doctor", action="store_true", help="环境自检并退出")
    parser.add_argument("--scan", action="store_true", help="只探测 Quest 位置并退出")
    parser.add_argument("--migrate-legacy", metavar="旧库路径",
                        help="把原版 songs_cache.db 的评分/评价导入新库（需配合 --location）")
    parser.add_argument("--location", default="",
                        help="迁移时这些歌属于哪个位置，例如 local:D:\\Songs 或 adb:/sdcard/...")
    args = parser.parse_args(argv)

    if args.doctor:
        return _doctor()
    if args.scan:
        return _scan()
    if args.migrate_legacy:
        if not args.location:
            print("迁移需要同时指定 --location，例如：")
            print('  python -m quest_tool --migrate-legacy "D:\\旧版\\songs_cache.db" '
                  '--location "local:D:\\Songs"')
            return 2
        return _migrate_legacy(args.migrate_legacy, args.location)

    from .logging_setup import setup_logging

    logger = setup_logging()
    try:
        from .app import QuestComparatorApp
    except ImportError as exc:
        message = ("启动失败：缺少依赖。请执行：pip install customtkinter pillow\n"
                   f"详细信息：{exc}")
        logger.critical("启动失败（依赖缺失）: %s", exc, exc_info=True)
        _report_startup_failure("启动失败", message)
        return 2

    try:
        app = QuestComparatorApp()
        app.run()
        return 0
    except Exception as exc:
        logger.critical("程序异常退出: %s", exc, exc_info=True)
        message = (f"程序异常退出：{exc}\n"
                   "详细日志见 logs/quest_comparator.log")
        _report_startup_failure("程序异常退出", message)
        return 1


def _report_startup_failure(title: str, message: str) -> None:
    """把启动失败同时告诉终端用户和双击用户。

    ## 为什么必须弹窗

    从 `启动.bat` / `启动.vbs` / `启动.pyw` 启动时用的是 **pythonw.exe**，
    它**没有控制台** —— `print()` 的输出会被直接丢掉。于是"缺少依赖""界面
    初始化失败"这类启动期错误对用户来说是**完全静默**的：双击没反应，
    既没有窗口也没有提示，连日志都不会想到去翻。

    这里用 ctypes 直接弹一个 MessageBox（不依赖 tkinter —— 启动失败时
    tkinter 本身可能就没装好）。弹窗失败也不让它影响返回码。
    """
    print(message)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, f"Quest歌单对比工具 · {title}", 0x10)
    except Exception:  # 弹不出来就算了，退出码仍然如实返回
        pass


if __name__ == "__main__":
    raise SystemExit(main())
