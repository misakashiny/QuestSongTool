"""后台任务执行层。

原版有 11 处工作线程**直接调用 `self.root.after(...)`**，而 Tk 不是线程安全的 ——
这类代码在负载高时会随机崩溃或死锁。这里把规则钉死：

* 工作线程只允许 `queue.put()`（线程安全）与纯计算/IO；
* **只有** UI 线程轮询队列并操作控件；
* 每个任务带唯一 token，旧任务的迟到消息会被丢弃（避免"切了路径还弹旧错误"）。

同时把"危险操作"的护栏放在这一层（复制/删除前校验），因为它们属于业务规则，
不该散落在界面回调里。
"""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .logging_setup import get_logger

logger = get_logger("tasks")


# ======================================================================================
# 消息
# ======================================================================================
@dataclass
class TaskMessage:
    kind: str  # progress | status | folders | meta | cover | compare | copy | done | error | device
    text: str = ""
    percent: int = -1
    payload: Any = None
    token: str = ""
    error: str = ""


# ======================================================================================
# 令牌
# ======================================================================================
class Token:
    """任务令牌：用于丢弃过期任务的回调。"""

    __slots__ = ("_value", "_alive")

    def __init__(self, value: str) -> None:
        self._value = value
        self._alive = True

    @property
    def value(self) -> str:
        return self._value

    def cancel(self) -> None:
        self._alive = False

    def alive(self) -> bool:
        return self._alive


# ======================================================================================
# 暂停闸门
# ======================================================================================
class PauseGate:
    """可暂停的闸门（B4）。

    工作线程在每个"可中断的步骤之间"调 `wait(token)` —— 暂停时它会阻塞在那里，
    继续时立刻返回。**暂停粒度是"两首歌之间"**：单首歌的传输没法中途挂起
    （底层是 `adb push` / CopyHere，没有取消接口），所以点了暂停之后
    当前这首会先传完。

    用短超时轮询而不是纯 `Event.wait()`：这样**暂停期间仍然能响应取消**，
    否则用户点了暂停再点取消就会卡住不动。
    """

    __slots__ = ("_paused", "_lock")

    #: 暂停期间多久醒一次去检查取消
    POLL_SECONDS = 0.2

    def __init__(self) -> None:
        self._paused = False
        self._lock = threading.Lock()

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def toggle(self) -> bool:
        """切换并返回**切换后**的状态（给对话框按钮用）。"""
        with self._lock:
            self._paused = not self._paused
            return self._paused

    def wait(self, token: "Token") -> bool:
        """暂停时阻塞；返回 False 表示等待期间被取消了（调用方应当中止）。"""
        while self.paused:
            if not token.alive():
                return False
            time.sleep(self.POLL_SECONDS)
        return token.alive()


# ======================================================================================
# 任务运行器
# ======================================================================================
class TaskRunner:
    """所有后台任务的统一入口（线程池 + 消息队列）。"""

    def __init__(self, max_workers: int = 6) -> None:
        self.messages: "queue.Queue[TaskMessage]" = queue.Queue()
        self._tokens: list[Token] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._max_workers = max_workers
        self._last_poll = time.monotonic()
        self._poll_count = 0

    # ---------------- 令牌管理 ----------------
    def new_token(self, name: str = "") -> Token:
        token = Token(f"{name or 'task'}-{time.monotonic_ns()}")
        with self._lock:
            # 只保留最近 8 个活动令牌，防止列表无限增长
            self._tokens = [t for t in self._tokens if t.alive()][-7:]
            self._tokens.append(token)
        return token

    def cancel_all(self) -> None:
        with self._lock:
            for token in self._tokens:
                token.cancel()
            self._tokens.clear()

    # ---------------- 提交 ----------------
    def submit(self, func: Callable[..., Any], *args: Any, name: str = "", **kwargs: Any) -> Token:
        token = self.new_token(name or getattr(func, "__name__", "task"))

        def wrapper() -> None:
            try:
                func(token, *args, **kwargs)
            except Exception as exc:  # 后台异常必须变成消息，绝不能静默吞掉
                logger.error("后台任务 %s 失败: %s", token.value, exc, exc_info=True)
                if token.alive():
                    self.messages.put(
                        TaskMessage(kind="error", text=f"任务失败: {exc}", token=token.value, error=str(exc))
                    )

        thread = threading.Thread(target=wrapper, name=token.value, daemon=True)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
        thread.start()
        return token

    # ---------------- 发送 ----------------
    def emit(
        self,
        kind: str,
        text: str = "",
        percent: int = -1,
        payload: Any = None,
        token: Token | None = None,
        error: str = "",
    ) -> None:
        self.messages.put(
            TaskMessage(
                kind=kind,
                text=text,
                percent=percent,
                payload=payload,
                token=token.value if token else "",
                error=error,
            )
        )

    # ---------------- 消费 ----------------
    def drain(self, alive_tokens: set[str] | None = None) -> list[TaskMessage]:
        """取出所有待处理消息（UI 线程调用）。过期令牌的消息被丢弃。"""
        out: list[TaskMessage] = []
        self._poll_count += 1
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if alive_tokens is not None and message.token and message.token not in alive_tokens:
                continue
            out.append(message)
        return out

    def alive_token_values(self) -> set[str]:
        with self._lock:
            return {t.value for t in self._tokens if t.alive()}

    def busy(self) -> bool:
        with self._lock:
            return any(t.is_alive() for t in self._threads)


# ======================================================================================
# 危险操作护栏（原版数据丢失事故的修复点）
# ======================================================================================
class GuardError(RuntimeError):
    """护栏拦截：继续执行会造成数据风险。"""


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def guard_copy(src_root: Path, dst_root: Path, song: str) -> tuple[Path, Path]:
    """校验一次"歌曲文件夹复制"是否安全，返回 (源歌曲目录, 目标歌曲目录)。

    原版实测事故：两个路径框填成同一个目录时，代码先 `rmtree(目标)` 再复制 ——
    **等于直接删掉源歌曲**；目标已存在时也会被静默清空，没有备份、没有提示。
    这里在动手之前就拦住。
    """
    name = (song or "").strip()
    if not name or name in (".", "..") or os.sep in name or (os.altsep and os.altsep in name):
        raise GuardError(f"非法歌曲文件夹名: {song!r}")

    src = src_root / name
    dst = dst_root / name

    if not src_root.exists():
        raise GuardError(f"源目录不存在: {src_root}")
    if not src.is_dir():
        raise GuardError(f"源歌曲目录不存在: {src}")

    # 源与目标同一位置：直接拒绝，这是原版删源歌曲的根因
    try:
        same_root = src_root.resolve() == dst_root.resolve()
    except OSError:
        same_root = str(src_root) == str(dst_root)
    if same_root:
        raise GuardError(
            "本地路径与 Quest 路径指向同一个文件夹，复制会删除源歌曲。\n请把其中一个改成真正的目标位置。"
        )
    if _is_within(dst_root, src) or _is_within(src, dst):
        raise GuardError("源目录与目标目录存在包含关系，已阻止复制以免误删。")
    if dst_root == dst_root.parent:
        raise GuardError(f"目标路径异常: {dst_root}")
    return src, dst


def guard_delete(root: Path, song: str) -> Path:
    """校验删除目标：必须是 root 的直接子目录，且 root 不能是盘符根/空/相对路径。"""
    name = (song or "").strip()
    if not name or name in (".", "..") or os.sep in name or (os.altsep and os.altsep in name):
        raise GuardError(f"非法歌曲文件夹名: {song!r}")

    root_text = str(root or "").strip()
    if not root_text:
        raise GuardError("歌曲根目录为空，已阻止删除（原版在这种情况下会删掉相对路径）")
    if not root.is_absolute():
        raise GuardError(f"歌曲根目录不是绝对路径，已阻止删除: {root}")
    try:
        if root.resolve() == root.resolve().parent:
            raise GuardError(f"拒绝在磁盘根目录下删除: {root}")
    except OSError as exc:
        raise GuardError(f"路径校验失败: {exc}") from exc

    target = root / name
    if not target.is_dir():
        raise GuardError(f"目标目录不存在: {target}")
    try:
        if target.resolve().parent != root.resolve():
            raise GuardError(f"拒绝删除非歌曲目录: {target}")
    except OSError as exc:
        raise GuardError(f"路径校验失败: {exc}") from exc
    return target


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} PB"
