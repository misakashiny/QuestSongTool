"""复制与传输：把歌从一侧搬到另一侧，含耗时估算与实测速率学习。"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Sequence

from .fsapi import FSError, FSUnavailable, SongFS
from .logging_setup import get_logger
from .ui_dialogs import ConfirmDialog, ProgressDialog
from .workers import GuardError, PauseGate, Token, guard_copy, human_size
from .messages import tr

logger = get_logger("app_copy")


class CopyMixin:
    """复制与传输：把歌从一侧搬到另一侧，含耗时估算与实测速率学习。"""

    def copy_missing(self, target: str) -> None:
        if not self.missing:
            self.toast.warning("没有可复制的歌曲", detail=tr("copy.need_missing"))
            return
        if self.missing_side != target:
            if not ConfirmDialog(
                self.root,
                title=tr("copy.wrong_dir_title"),
                message=tr("copy.wrong_dir_message"),
                detail=tr("copy.wrong_dir_detail"),
                confirm_text="仍然继续",
                confirm_is_danger=True,
            ).show():
                return

        source_kind = "local" if target == "quest" else "quest"
        source_library = self.libraries[source_kind]
        target_library = self.libraries[target]
        if source_library.fs is None or target_library.fs is None or target_library.location is None:
            self.toast.warning("路径未就绪", detail=tr("copy.need_both"))
            return

        songs = list(self.missing)
        total = len(songs)

        # ---- 复制前可视化：总量 / 预计耗时 / 目标剩余空间 ----
        source_location = source_library.location
        # 速率按**传输通道**取（只要有一端是设备，瓶颈就是设备那条通道）
        channel = self._channel_key(source_location.kind, target_library.location.kind)
        stats = self._estimate_copy(source_library.fs, songs, channel) if source_location else None
        detail_lines = [f"目标：{target_library.location.root}"]
        if stats is not None:
            size_text = human_size(stats["bytes"]) if stats["bytes"] else "大小未知"
            detail_lines.append(f"总大小：{size_text}（{total} 首）")
            if stats["eta"]:
                detail_lines.append(f"预计耗时：约 {stats['eta']}")
            free = None
            try:
                free = target_library.fs.free_space_bytes() if target_library.fs else None
            except Exception:
                free = None
            if free is not None and stats["bytes"]:
                if free < stats["bytes"]:
                    detail_lines.append(f"⚠️ 目标剩余空间可能不足：可用 {human_size(free)}")
                else:
                    detail_lines.append(f"目标可用空间：{human_size(free)}")
        detail_lines.append("目标中同名文件夹会被新内容替换（原内容先移走/删除）。")

        if not ConfirmDialog(
            self.root,
            title=tr("copy.confirm_title"),
            message=f"将复制 {total} 首歌曲到{self._label_for(target)}。",
            detail="\n".join(detail_lines),
            confirm_text=f"复制 {total} 首",
        ).show():
            return

        token = self.tasks.new_token("copy")
        # 复制队列可暂停（B4）：闸门在**两首歌之间**生效 —— 单首歌的传输没法中途挂起
        gate = PauseGate()
        self._copy_gate = gate
        progress = ProgressDialog(
            self.root, title=tr("copy.running"), message=f"正在复制 {total} 首歌曲…",
            cancel_callback=lambda: token.cancel(),
            pause_callback=self._toggle_copy_pause,
        )
        self._progress_dialog = progress
        self._progress_token = token.value
        progress.show_nonblocking()

        def worker(task_token: Token) -> None:
            ok = failed = skipped = 0
            failures: list[str] = []
            source_fs, target_fs = source_library.fs, target_library.fs
            started_at = time.monotonic()
            for index, song in enumerate(songs, start=1):
                if not task_token.alive() or not token.alive():
                    self.tasks.emit("copy", "已取消复制", 100,
                                    payload={"summary": "复制已取消"}, token=task_token)
                    break
                # 暂停在这里生效：暂停时阻塞，继续时立刻返回；等待期间被取消则返回 False
                if not gate.wait(task_token):
                    self.tasks.emit("copy", "已取消复制", 100,
                                    payload={"summary": "暂停等待时被取消"}, token=task_token)
                    break
                try:
                    # 设置里关掉"覆盖已存在"时，目标上已有的就跳过（不重复传输）
                    if not self.settings.copy_overwrite and target_fs.local_folder_exists(song):
                        skipped += 1
                        self.tasks.emit("copy", f"已存在，跳过 {song}", -1,
                                        payload={"status": "song_done", "song": song},
                                        token=task_token)
                    else:
                        self._copy_one(source_fs, target_fs, song)
                        ok += 1
                        self.tasks.emit("copy", f"已复制 {song}", -1,
                                        payload={"status": "song_done", "song": song}, token=task_token)
                except (GuardError, FSError, FSUnavailable, OSError) as exc:
                    failed += 1
                    failures.append(f"{song}: {exc}")
                    logger.warning("复制失败 %s: %s", song, exc)
                percent = int(index / max(1, total) * 100)
                self.tasks.emit("progress", f"复制中 {index}/{total}：{song[:28]}", percent, token=task_token)
            summary = f"复制完成：成功 {ok}，失败 {failed}"
            if skipped:
                summary += f"，跳过 {skipped}（目标已存在，设置里关了覆盖）"
            if failures:
                summary += "\n" + "\n".join(failures[:5])
                if len(failures) > 5:
                    summary += f"\n…另有 {len(failures) - 5} 首失败（详见日志）"
            # 用这次真实传输更新速率估计（下次的"预计耗时"会更准）
            if ok and stats and stats.get("bytes"):
                copied_bytes = int(stats["bytes"] * ok / max(1, total))
                self._record_transfer(channel, copied_bytes, time.monotonic() - started_at)
            self.tasks.emit("copy", summary, 100,
                            payload={"summary": summary, "failed": failed, "reload": target,
                                     "failures": failures},
                            token=task_token)
            # 任务收尾：闸门复位（下次复制是新对象，这里只是别让对话框停在"已暂停"）
            gate.resume()

        self.tasks.submit(worker, name="copy_missing")

    def _toggle_copy_pause(self) -> bool:
        """复制对话框的「暂停/继续」按钮回调：切换闸门并返回切换后的状态。"""
        gate = getattr(self, "_copy_gate", None)
        if gate is None:
            return False
        paused = gate.toggle()
        self.set_status("复制已暂停（当前这首传完后停下）" if paused else "复制已继续")
        return paused

    @staticmethod
    def _channel_key(source_kind: str, target_kind: str) -> str:
        """决定按哪条通道的速率估算：只要有一端是设备，瓶颈就是那条设备通道。"""
        for kind in (source_kind, target_kind):
            if kind in ("adb", "mtp"):
                return kind
        return "local"

    def _rate_for(self, channel: str) -> float:
        """该通道的速率（MB/s）：优先用实测值，没有就用兜底。"""
        measured = self.settings.transfer_rates.get(channel)
        if measured:
            return float(measured)
        return self._DEFAULT_RATES.get(channel, 4.0)

    def _record_transfer(self, channel: str, nbytes: int, seconds: float) -> None:
        """用一次真实传输更新速率（指数滑动平均，越用越准）。

        为什么要学：速率取决于 USB 版本、线材、是否无线 adb、设备存储速度……
        写死一个常数必然不准（v14.7 之前写死 4 MB/s，真机实测 17.4 MB/s，差 4 倍多）。
        用 EMA(0.4) 是为了不让单次抖动（比如某一首特别大）把估计带偏。
        """
        if nbytes <= 0 or seconds <= 0.3:
            return  # 太快/太短说明没真的传数据，别污染样本
        rate = nbytes / (1024 * 1024) / seconds
        if not (0.05 <= rate <= 5000):
            return
        previous = self.settings.transfer_rates.get(channel)
        self.settings.transfer_rates[channel] = (
            rate if not previous else float(previous) * 0.6 + rate * 0.4
        )
        self.settings.save()

    def _estimate_copy(
        self, fs: SongFS | None, songs: Sequence[str], channel: str = "local"
    ) -> dict[str, Any] | None:
        """估算这批歌曲的总大小与耗时（本地直接 stat；adb 用 `du`；MTP 用镜像大小）。

        `channel` 是**传输通道**（见 `_channel_key`），不是源的类型 ——
        从本地往设备复制时，瓶颈是设备那条通道。v14.7 之前这里错用了**源**的类型，
        于是"本地 → Quest"会按本地磁盘 150 MB/s 估算，把 9 秒说成 1 秒。
        """
        if fs is None or not songs:
            return None
        total_bytes = 0
        for song in songs:
            total_bytes += self._folder_bytes(fs, song)
        if not total_bytes:
            return {"bytes": 0, "eta": ""}
        rate = self._rate_for(channel) * 1024 * 1024
        seconds = max(1, int(total_bytes / rate))
        if seconds < 60:
            eta = f"{seconds} 秒"
        else:
            eta = f"{seconds // 60} 分 {seconds % 60} 秒"
        return {"bytes": total_bytes, "eta": eta}

    @staticmethod
    def _folder_bytes(fs: SongFS | None, song: str) -> int:
        """某首歌在**本地可见的那一侧**占多少字节（本地盘或镜像）。

        设备上的歌（AdbFS）本地看不到，返回 0 —— 估算只对"源在本地"这种常见方向有效。
        """
        if fs is None:
            return 0
        try:
            path_of = getattr(fs, "_folder_path", None)
            if callable(path_of):
                folder = Path(path_of(song))
            elif fs.location.kind == "local":
                folder = Path(fs.location.root) / song
            else:
                return 0
        except Exception:
            return 0
        if not folder.is_dir():
            return 0
        total = 0
        for current, _dirs, files in os.walk(folder):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(current, name))
                except OSError:
                    continue
        return total

    def _copy_one(self, source_fs: SongFS, target_fs: SongFS, song: str) -> None:
        """把一首歌从源复制到目标（跨后端自动选择 push / copytree）。"""
        source_location = source_fs.location
        target_location = target_fs.location

        if source_location.kind == "local" and target_location.kind == "local":
            guard_copy(Path(source_location.root), Path(target_location.root), song)

        name = (song or "").strip()
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise GuardError(f"非法歌曲文件夹名: {song!r}")

        if source_location.kind == "local":
            source_path = Path(source_location.root) / name
            if not source_path.is_dir():
                raise FSError(f"源目录不存在: {source_path}")
            target_fs.copy_into(name, source_path, progress=None)
        elif target_location.kind == "local":
            target_fs.copy_out(name, Path(target_location.root) / name, progress=None)
        else:
            staging_root = Path(self.settings.local_path).parent if self.settings.local_path else Path.cwd()
            staging = staging_root / f".questcopy_{int(time.time())}"
            staging.mkdir(parents=True, exist_ok=True)
            try:
                source_fs.copy_out(name, staging / name, progress=None)
                target_fs.copy_into(name, staging / name, progress=None)
            finally:
                import shutil

                shutil.rmtree(staging, ignore_errors=True)
