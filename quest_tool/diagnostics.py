"""卡死诊断：慢操作记录 + 主线程心跳看门狗。

## 为什么需要这个模块

普通的日志只能记录"发生了什么"，**记录不了"什么没发生"**。程序在界面
线程里同步跑一个 5 秒的 adb 命令时，日志上完全看不出来 —— 最后一条正常
记录之后就是一片空白，用户看到的是"卡死了"，而日志里查不到任何线索。

这里补两样东西：

1. **慢操作记录**（`slow()` 上下文管理器）：任何可能阻塞的操作都能套上，
   超过阈值就打一条 WARNING，写明操作名、耗时、**是不是跑在界面线程上**。
2. **主线程心跳看门狗**（`Watchdog`）：界面线程每 50ms 自报一次。两次心跳
   之间隔了很久，就说明界面线程在那段时间里被占住了 —— 这正是"卡死"的定义。
   心跳断了还会把**当前正在跑的操作名**一起写进日志（取自 `_current`），
   所以卡住的时候日志会明确说"卡在 Xxx 上"。

## 典型用法

    if not watchdog_started():
        start_watchdog(root)          # 应用启动时一次
    with slow("检测设备"):
        report = scan_quest(...)

看门狗必须在**界面线程**里启动（它用 `root.after` 排队）。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

# 注意：这里**不能**用 `logging_setup.get_logger()`。本模块会被
# `app.py` 在很早期导入，而 `get_logger()` 会顺带触发 `setup_logging()`，
# 那时日志文件 handler 还没挂上 —— 于是"看门狗已启动"这类**恰恰最关键的
# 开局记录会丢失**（实测：日志文件里只有卡顿记录，没有启动记录）。
# 和 `storage.py` 一样用标准 logger，root 配置由 `logging_setup` 完成。
logger = logging.getLogger("quest_tool.diagnostics")

#: 超过这个秒数的操作会被记一条 WARNING。
SLOW_SECONDS = 0.8

#: 心跳间隔（秒）。卡顿判据是"两次心跳间隔 > 心跳间隔 × 这个倍数"。
TICK_SECONDS = 0.05
STALL_FACTOR = 5.0

#: 同一条卡顿最多重复记几次（避免日志被同一条刷爆）。
MAX_STALL_REPORTS = 50


class _State:
    """模块级共享状态（跨线程读，只在界面线程写）。"""

    __slots__ = ("current", "depth", "stalls", "last", "last_at")

    def __init__(self) -> None:
        self.current: str = ""      # 当前正在跑的操作名
        self.depth: int = 0         # 嵌套层数，用于还原 current
        self.stalls: int = 0        # 已经记过多少条卡顿
        #: 最近**结束**的操作名。为什么需要它：卡顿发生时界面线程是冻结的，
        #: 心跳回调排在它后面；等心跳终于跑起来，`current` 早就被清空了 ——
        #: 于是日志只能说"当时没有登记任何操作"，恰好丢掉唯一有用的线索。
        #: 所以操作结束后再保留一会儿（见 KEEP_LAST_SECONDS）。
        self.last: str = ""
        self.last_at: float = 0.0


_state = _State()
_state_lock = threading.Lock()


def main_thread_id() -> int:
    return threading.main_thread().ident or 0


def on_main_thread() -> bool:
    """当前线程是不是界面线程。

    这是慢操作记录里最关键的一个字段：**同样耗时 1 秒，跑在界面线程上就是
    卡死，跑在 worker 上只是慢**。日志里必须区分开，否则看日志的人会去
    优化一个根本不影响体验的路径。
    """
    return threading.get_ident() == main_thread_id()


def current_operation() -> str:
    return _state.current


@contextmanager
def slow(what: str, *, seconds: float = SLOW_SECONDS) -> Iterator[None]:
    """给可能阻塞的操作套上计时；超时就记 WARNING。

    嵌套时 `current_operation()` 取最内层 —— 卡住时最内层才是真凶。
    """
    started = time.monotonic()
    thread = threading.current_thread().name
    on_main = on_main_thread()
    with _state_lock:
        previous = _state.current
        _state.current = what
        _state.depth += 1
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        with _state_lock:
            _state.depth -= 1
            _state.current = previous if _state.depth > 0 else ""
            # 记下"刚跑完的操作"：卡顿的心跳往往在这之后才醒过来，
            # 那时 current 已经空了，只有这里能说明卡在哪。
            _state.last = what
            _state.last_at = time.monotonic()
        if elapsed >= seconds:
            logger.warning(
                "慢操作：%s 用了 %.0f ms（线程=%s，界面线程=%s）",
                what, elapsed * 1000, thread, "是" if on_main else "否",
            )


#: 「最近结束的操作」保留多久（秒）。要略大于最大心跳间隔，
#: 否则卡顿的心跳醒来时它已经过期了。
KEEP_LAST_SECONDS = 5.0


def _stall_context() -> str:
    """卡顿发生时，尽量说清卡在哪个操作上。"""
    if _state.current:
        return f"，当时正在跑「{_state.current}」"
    if _state.last and time.monotonic() - _state.last_at <= KEEP_LAST_SECONDS:
        return f"，多半卡在刚跑完的「{_state.last}」上"
    return "，当时没有登记任何操作"


def _report_stall(gap: float) -> None:
    with _state_lock:
        if _state.stalls >= MAX_STALL_REPORTS:
            return
        _state.stalls += 1
        count = _state.stalls
    detail = _stall_context()
    logger.warning("界面线程卡顿 %.1f 秒%s", gap, detail)
    if count == MAX_STALL_REPORTS:
        logger.warning("卡顿记录已达 %d 条，后续同类不再重复记录", MAX_STALL_REPORTS)


class Watchdog:
    """界面线程心跳。心跳断了 = 界面卡住。"""

    def __init__(self, root: Any) -> None:
        self._root = root
        self._last = 0.0
        self._ticks = 0
        self._worst = 0.0
        self._job: str | None = None
        self._stopped = False

    def start(self) -> None:
        self._last = time.monotonic()
        self._job = self._root.after(int(TICK_SECONDS * 1000), self._beat)
        logger.info("看门狗已启动（心跳 %.0f ms）", TICK_SECONDS * 1000)

    def stop(self) -> None:
        self._stopped = True
        if self._job is not None:
            try:
                self._root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        logger.info("看门狗停止：心跳 %d 次，最长卡顿 %.0f ms", self._ticks, self._worst * 1000)

    def _beat(self) -> None:
        if self._stopped:
            return
        now = time.monotonic()
        gap = now - self._last
        self._last = now
        self._ticks += 1
        if gap > self._worst:
            self._worst = gap
        if gap > TICK_SECONDS * STALL_FACTOR:
            _report_stall(gap)
            # 每 20 秒汇总一次，避免长时间卡顿刷屏
            if self._ticks % 400 == 0:
                logger.info("看门狗：心跳 %d 次，最长卡顿 %.0f ms", self._ticks, self._worst * 1000)
        try:
            self._job = self._root.after(int(TICK_SECONDS * 1000), self._beat)
        except Exception:
            self._stopped = True


_watchdog: Watchdog | None = None
_watchdog_lock = threading.Lock()


def start_watchdog(root: Any) -> Watchdog | None:
    """启动看门狗（必须由界面线程调用，且在 Tk 根建好之后）。"""
    global _watchdog
    with _watchdog_lock:
        if _watchdog is not None:
            return _watchdog
        try:
            dog = Watchdog(root)
            dog.start()
        except Exception as exc:  # 诊断功能绝不拖垮主程序
            logger.warning("看门狗启动失败：%s", exc)
            return None
        _watchdog = dog
        return dog


def stop_watchdog() -> None:
    global _watchdog
    with _watchdog_lock:
        dog, _watchdog = _watchdog, None
    if dog is not None:
        dog.stop()


def watchdog_running() -> bool:
    return _watchdog is not None


def install_excepthook() -> None:
    """把未捕获异常也写进日志。

    按钮回调里抛的异常会被 Tk 的命令分发吞掉（用户看到的是"点了没反应"），
    `threading.excepthook` 同理、默认只打 stderr 而窗口程序没有 stderr。
    这里统一转进日志文件，免得"什么都没发生"变成无头案。
    """
    if getattr(sys.excepthook, "_quest_wrapped", False):
        return

    def _hook(exc_type: Any, exc: Any, tb: Any) -> None:
        logger.error("未捕获异常", exc_info=(exc_type, exc, tb))

    def _thread_hook(args: Any) -> None:
        logger.error(
            "线程 %s 里未捕获异常", getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    _hook._quest_wrapped = True  # type: ignore[attr-defined]
    sys.excepthook = _hook
    threading.excepthook = _thread_hook  # type: ignore[assignment]
    logger.info("已接管未捕获异常（含线程）")
