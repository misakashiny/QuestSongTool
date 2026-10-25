"""卡死诊断：慢操作记录 + 主线程心跳看门狗。

## 为什么需要这一组测试

用户报的是「使用时貌似会卡死」。这类问题**最难查的地方在于日志里什么都
看不到** —— 程序在界面线程里同步跑一个 5 秒的 adb 命令时，"最后一条日志"
之后就是一片空白。所以诊断功能本身必须被测试守住，否则它坏了也没人知道
（而它坏掉的表现恰恰是"日志里什么都没写"，与"没有卡死"完全无法区分）。

## 关键判据

* 卡顿必须被记录，**而且必须写明当时在跑什么** —— 只说"卡了 1.5 秒"没用，
  要能直接指出是哪个操作。这里特意覆盖了一个反直觉场景：卡顿的心跳回调
  排在冻结结束之后才跑，那时 `current` 已经清空，只能靠"刚跑完的操作"
  来还原（见 `diagnostics.KEEP_LAST_SECONDS`）。
* **同一秒里的大批操作不能互相污染**：`slow()` 嵌套时取最内层。
* 诊断代码本身**绝不能拖垮主程序**：Tk 根都建不起来时也要能降级。
"""

from __future__ import annotations

import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ensure_workspace_importable  # noqa: E402

ensure_workspace_importable()

import customtkinter as ctk  # noqa: E402

from quest_tool import diagnostics  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(what: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((what, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {what}" + (f"  -> {detail}" if detail else ""))


def close_logger() -> None:
    """关掉测试日志的句柄 —— Windows 上文件还被打开时临时目录删不掉。

    实测：`removeHandler` + `flush` + `close` 三件套就够了，文件随即能删。
    **不要**用 `logging.shutdown()`：那会把全局 handler 列表一起关掉，
    影响同进程里其他测试。
    """
    logger = logging.getLogger("quest_tool.diagnostics")
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        try:
            existing.flush()
            existing.close()
        except Exception:
            pass


def fresh_logger(log_file: Path) -> logging.Logger:
    close_logger()
    logger = logging.getLogger("quest_tool.diagnostics")
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def main() -> int:
    print("=== 卡死诊断（慢操作 / 看门狗）===")

    print("\n-- 1. 线程归属判定 --")
    check("主线程被认成界面线程", diagnostics.on_main_thread())
    seen: list[bool] = []

    import threading

    thread = threading.Thread(target=lambda: seen.append(diagnostics.on_main_thread()))
    thread.start()
    thread.join()
    check("worker 线程不被认成界面线程", seen == [False], f"得到 {seen}")

    print("\n-- 2. slow() 记录慢操作 --")
    diagnostics._state.stalls = 0
    with tempfile.TemporaryDirectory() as tmp:
        log_file = Path(tmp) / "d.log"
        fresh_logger(log_file)

        with diagnostics.slow("快操作", seconds=0.5):
            time.sleep(0.02)
        text = log_file.read_text(encoding="utf-8")
        check("快的操作不记日志", "快操作" not in text)

        with diagnostics.slow("慢操作甲", seconds=0.3):
            time.sleep(0.35)
        text = log_file.read_text(encoding="utf-8")
        check("慢的操作记 WARNING", "慢操作甲" in text and "WARNING" in text)
        check("慢操作写明界面线程=是", "界面线程=是" in text)
        check("慢操作写了毫秒数", " ms" in text)

        # 嵌套：卡住时最内层才是真凶
        with diagnostics.slow("外层", seconds=0.3):
            with diagnostics.slow("内层", seconds=0.3):
                check("嵌套时 current 取最内层",
                      diagnostics.current_operation() == "内层",
                      diagnostics.current_operation())
                time.sleep(0.35)
        check("嵌套退出后 current 清空", diagnostics.current_operation() == "")
        # 必须在 `with` 退出**之前**关句柄：Windows 上临时目录删除时
        # 文件还开着会直接 `PermissionError: [WinError 32]`。
        close_logger()

    print("\n-- 3. 看门狗抓到卡死并指出卡在哪 --")
    root = ctk.CTk()
    root.geometry("300x200+10+10")
    root.deiconify()

    with tempfile.TemporaryDirectory() as tmp:
        log_file = Path(tmp) / "wd.log"
        fresh_logger(log_file)

        dog = diagnostics.start_watchdog(root)
        check("看门狗启动成功", dog is not None)
        check("watchdog_running() 为真", diagnostics.watchdog_running())
        check("重复启动返回同一个实例", diagnostics.start_watchdog(root) is dog)

        def pump(seconds: float) -> None:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                root.update()
                time.sleep(0.005)

        pump(0.4)
        check("正常运行时没有卡顿记录", "卡顿" not in log_file.read_text(encoding="utf-8"))

        # 真冻结：这期间没有 root.update()，心跳排在被冻结的回调后面
        with diagnostics.slow("假装在调 adb", seconds=0.3):
            time.sleep(1.2)
        pump(0.5)

        text = log_file.read_text(encoding="utf-8")
        check("日志里有「界面线程卡顿」", "界面线程卡顿" in text)
        check("卡顿写明了卡在哪个操作上", "假装在调 adb" in text,
              [line for line in text.splitlines() if "卡顿" in line][:1])
        check("卡顿秒数合理（≥1.0）", any(
            "卡顿 1." in line or "卡顿 2." in line for line in text.splitlines()))

        # 卡顿记录条数受控，不会把日志刷爆
        before = text.count("界面线程卡顿")
        check("单次冻结只记一条卡顿", before == 1, f"得到 {before}")

        diagnostics.stop_watchdog()
        check("停止后 watchdog_running() 为假", not diagnostics.watchdog_running())
        pump(0.3)
        check("停止后不再新增记录",
              log_file.read_text(encoding="utf-8").count("界面线程卡顿") == before)

        # KEEP_LAST_SECONDS 是"刚跑完的操作"能用来解释卡顿的时间窗
        check("KEEP_LAST_SECONDS 大于心跳间隔",
              diagnostics.KEEP_LAST_SECONDS > diagnostics.TICK_SECONDS * 2,
              f"{diagnostics.KEEP_LAST_SECONDS} > {diagnostics.TICK_SECONDS}")
        check("STALL_FACTOR 大于 1", diagnostics.STALL_FACTOR > 1.0)
        check("SLOW_SECONDS 是正数且不超过 3 秒",
              0 < diagnostics.SLOW_SECONDS <= 3.0, str(diagnostics.SLOW_SECONDS))
        close_logger()

    print("\n-- 4. 异常接管 --")
    diagnostics.install_excepthook()
    import threading as _th

    check("sys.excepthook 已被接管", getattr(sys.excepthook, "_quest_wrapped", False))
    check("threading.excepthook 已被接管",
          _th.excepthook is not None and _th.excepthook.__name__ == "_thread_hook",
          getattr(_th.excepthook, "__name__", "?"))

    print("\n-- 5. 诊断绝不能拖垮主程序 --")
    broken = object()  # 没有 .after，watchdog 必然失败
    diagnostics._watchdog = None
    result = diagnostics.start_watchdog(broken)
    check("Tk 根不可用时 start_watchdog 返回 None 而不抛异常", result is None)
    check("失败后 watchdog_running() 仍为假", not diagnostics.watchdog_running())
    diagnostics.stop_watchdog()  # 没启动时停止也不该抛

    # ------------------------------------------------------------------
    # 6. 诊断对话框里的「日志尾部」段落
    #
    # 用户报"卡死"时，光有一堆日志文件没用 —— 他要能**在界面里**看到最近的
    # 警告/错误，并且能一键复制发出来。这一段负责把日志挑干净。
    # 判据：只挑 WARNING/ERROR/CRITICAL，INFO 噪声一条都不许混进来。
    # ------------------------------------------------------------------
    print("\n-- 6. 诊断信息里的日志尾部 --")
    import re as _re
    from quest_tool.app import QuestComparatorApp
    from quest_tool.storage import Settings

    tail_dir = Path(tempfile.mkdtemp(prefix="quest_diag_tail_"))
    fake_log = tail_dir / "quest_comparator.log"
    fake_log.write_text(
        "\n".join([
            "2026-01-01 00:00:00,000 - INFO - MainThread - x - 普通信息",
            "2026-01-01 00:00:01,000 - WARNING - MainThread - quest_tool.diagnostics - 慢操作：载入曲库 用了 900 ms",
            "2026-01-01 00:00:02,000 - INFO - MainThread - x - 又一条普通信息",
            "2026-01-01 00:00:03,000 - ERROR - MainThread - quest_tool.app - 未捕获异常",
            "2026-01-01 00:00:04,000 - INFO - MainThread - x - 更多噪声",
        ]) + "\n",
        encoding="utf-8",
    )

    app_settings = Settings()
    app_settings.local_path = ""
    app_settings.quest_path = ""
    app_settings.device_poll_on_start = False
    app_settings.wizard_done = True
    app = QuestComparatorApp(settings=app_settings)
    app_root = app.root
    app_root.withdraw()

    real_logs_dir = None
    try:
        import quest_tool.storage as _storage
        real_logs_dir = _storage.logs_dir
        _storage.logs_dir = lambda: tail_dir  # type: ignore[assignment]
        # `_log_tail_section` 内部是 `from .storage import logs_dir`，改模块属性生效
        section = app._log_tail_section()
    finally:
        if real_logs_dir is not None:
            _storage.logs_dir = real_logs_dir  # type: ignore[assignment]

    text = "\n".join(section)
    check("日志尾部段落有内容", bool(section), f"{len(section)} 行")
    check("挑出了 WARNING 行", "慢操作：载入曲库" in text)
    check("挑出了 ERROR 行", "未捕获异常" in text)
    check("INFO 噪声没混进来", "普通信息" not in text and "更多噪声" not in text)
    check("写明找不到日志时不崩", isinstance(app._log_tail_section.__doc__, str))
    # 条数与路径要交代清楚，否则用户不知道这屏是不是被截断了
    check("段落写明共几条", any("共" in line and "条" in line for line in section))
    check("段落给出完整日志路径", any("quest_comparator.log" in line for line in section))
    # limit 要真的生效（默认 40，超长日志不能被整个倒出来）
    check("limit 参数生效", len(app._log_tail_section(limit=1)) < len(section),
          f"{len(app._log_tail_section(limit=1))} < {len(section)}")
    check("警告行格式被原样保留",
          any(_re.match(r"^\d{4}-\d{2}-\d{2} .+ - WARNING - ", line) for line in section))

    app._closing = True
    try:
        app.on_close()
    except Exception:
        pass

    try:
        root.destroy()
    except Exception:
        pass

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{'=' * 60}")
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    if failed:
        print("失败：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
