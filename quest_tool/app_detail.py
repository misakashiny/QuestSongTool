"""歌曲详情栏：封面、元信息、难度、评分与评价。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

import customtkinter as ctk

from .app_common import decorate_cover, meta_from_row, meta_row, parse_meta
from .audio import PREVIEW_SECONDS
from .audio import _find_audio_file as audio_find_audio_file
from .audio import available as audio_available
from .audio import player as audio_player
from .audio import probe_duration as audio_probe_duration
from .audio import stop_all as audio_stop_all
from .diagnostics import slow
from .fsapi import FSError, SongFS
from .logging_setup import get_logger
from .songs import SongMeta
from .theme import FONT, RADIUS, SPACE, T, color
from .ui_dialogs import CommentEditDialog
from .workers import Token, human_size
from .messages import tr

logger = get_logger("app_detail")


class DetailMixin:
    """歌曲详情栏：封面、元信息、难度、评分与评价。"""

    def show_missing_song(self, folder: str) -> None:
        """缺失列表里的歌曲实际存在于"缺失的另一侧"。"""
        kind = "local" if self.missing_side == "quest" else "quest"
        if folder not in self.libraries[kind].folders:
            other = "quest" if kind == "local" else "local"
            if folder in self.libraries[other].folders:
                kind = other
        self.show_song(kind, folder)

    def show_song(self, kind: str, folder: str) -> None:
        library = self.libraries[kind]
        if library.fs is None or library.location is None:
            return
        if self.current_song != (kind, folder):
            audio_stop_all()
            self._preview_pending = None
            self._set_preview_button(False)
            self._hide_preview_progress()
        self.current_song = (kind, folder)
        location = library.location

        # 先把已知信息立刻画出来（列表里已有标题/作者），避免"点了没反应"
        known = library.meta.get(folder)
        if known is not None:
            self._paint_detail(kind, folder, known, rating=self._rating_map.get(folder, 0),
                               comment="", size=None, cover_path="", cover_bytes=None, partial=True)

        def worker(token: Token) -> None:
            fs = library.fs
            if fs is None:
                return
            meta = library.meta.get(folder)
            mtime = meta.mtime if meta else fs.stat_mtime(folder)
            cached = self.db.get_meta(folder, mtime) if mtime else None

            if cached is not None:
                info = meta_from_row(folder, cached)
            else:
                data = fs.read_info_json(folder)
                cover = fs.cover_name(folder, data)
                info = parse_meta(data, folder, mtime, cover)
                self.db.save_meta(meta_row(info))

            if info.duration <= 0:
                info.duration = fs.read_duration(folder)
                if info.duration > 0:
                    self.db.save_meta(meta_row(info))
            library.meta[folder] = info
            self.tasks.emit("meta", "", -1,
                            payload={"kind": kind, "meta": {folder: info}, "done": False}, token=token)

            rating, comment = self.db.get_rating(location.identity, folder)
            cover_bytes = None
            cover_path = ""
            if info.cover_file:
                if location.kind == "local":
                    cover_path = str(Path(location.root) / folder / info.cover_file)
                else:
                    try:
                        cover_bytes = fs.read_file(folder, info.cover_file)
                    except FSError as exc:
                        logger.debug("读取远程封面失败: %s", exc)
            size = self._folder_size(fs, folder)
            self.tasks.emit(
                "cover", "", -1,
                payload={"kind": kind, "folder": folder, "info": info, "rating": rating,
                         "comment": comment, "cover_path": cover_path, "cover_bytes": cover_bytes,
                         "size": size},
                token=token,
            )

        self.tasks.submit(worker, name=f"info_{folder}")

    def _folder_size(self, fs: SongFS, folder: str) -> int | None:
        if fs.location.kind != "local":
            return None
        total = 0
        root = Path(fs.location.root) / folder
        for current, _dirs, files in os.walk(root):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(current, name))
                except OSError:
                    continue
        return total

    def _apply_cover(self, payload: dict[str, Any]) -> None:
        info: SongMeta = payload["info"]
        folder = payload["folder"]
        # 这里会同步解码封面（真封面实测最慢 58.7 ms，3000×3000 的大图）。
        # 套上 slow() 是为了让"点歌后界面一顿"能在日志里对上号。
        with slow("显示歌曲详情"):
            self._paint_detail(
                payload.get("kind", ""), folder, info,
                rating=payload.get("rating", 0), comment=payload.get("comment", ""),
                size=payload.get("size"), cover_path=payload.get("cover_path") or "",
                cover_bytes=payload.get("cover_bytes"), partial=False,
            )
        self.set_status(f"已加载：{info.display_title}")

    def _paint_detail(
        self,
        kind: str,
        folder: str,
        info: SongMeta,
        *,
        rating: int,
        comment: str,
        size: int | None,
        cover_path: str,
        cover_bytes: bytes | None,
        partial: bool,
    ) -> None:
        """把歌曲信息画到详情栏（UI 线程调用）。"""
        self.song_title.configure(text=info.display_title)
        self.song_subtitle.configure(text=info.subtitle or "")
        if info.subtitle:
            self.song_subtitle.pack(fill="x", after=self.song_title)
        else:
            self.song_subtitle.pack_forget()
        self.song_author.configure(text=info.display_author if info.author else "")
        self.detail_source.configure(text=self._label_for(kind) if kind else "")
        self.folder_label.configure(text=f"文件夹：{folder}")

        if partial:
            return

        metrics = (
            ("BPM", info.display_bpm, "ACCENT"),
            ("时长", info.display_duration, "INFO"),
            ("格式", info.version or "—", "NEUTRAL"),
            ("大小", human_size(size) if size else "—", "NEUTRAL"),
        )
        for child in self.metrics_row.winfo_children():
            child.destroy()
        self.metric_labels = {}
        self.metrics_row.grid_columnconfigure(1, weight=1)
        for index, (label, value, role) in enumerate(metrics):
            metric_heading = ctk.CTkLabel(self.metrics_row, text=label, width=52, height=20, anchor="w",
                         font=(self.font_family, FONT.SMALL), text_color=T["TEXT_MUTED"])
            if index != 2:
                metric_heading.grid(row=index if index < 2 else index - 1, column=0, sticky="w", pady=1)
            value_label = ctk.CTkLabel(self.metrics_row, text=value, anchor="w", height=20,
                                       font=(self.font_family, FONT.BODY), text_color=T["TEXT_PRIMARY"])
            if index != 2:
                value_label.grid(row=index if index < 2 else index - 1, column=1, sticky="w", padx=SPACE.L)
            self.metric_labels[label] = value_label

        self._update_difficulties(info.difficulties)
        self._render_rating(rating)
        self.comment_label.configure(text=comment or "未评价")
        self._render_cover(folder, cover_path, cover_bytes, info.display_title)

    def _update_difficulties(self, difficulties: Sequence[str]) -> None:
        for child in self.difficulty_frame.winfo_children():
            child.destroy()
        if not difficulties:
            ctk.CTkLabel(self.difficulty_frame, text=tr("detail.unknown"), font=(self.font_family, FONT.SMALL),
                         text_color=T["TEXT_MUTED"]).pack(side="left")
            return
        role_map = {
            "Easy": "SUCCESS",
            "Normal": "ACCENT",
            "Hard": "WARNING",
            "Expert": "DANGER",
            "ExpertPlus": "INFO",
        }
        for name in difficulties:
            role = role_map.get(name, "NEUTRAL")
            ctk.CTkLabel(
                self.difficulty_frame,
                text=f" {name} ",
                font=(self.font_family, FONT.SMALL, "bold"),
                text_color=T[role],
                fg_color=T["BG_ELEVATED"],
                corner_radius=RADIUS.CHIP,
                padx=SPACE.S,
                pady=SPACE.XXS,
            ).pack(side="left", padx=(0, SPACE.XS))

    def _render_rating(self, rating: int) -> None:
        for index, button in enumerate(self.star_buttons, start=1):
            filled = index <= rating
            button.configure(
                text="★" if filled else "☆",
                fg_color=T["ACCENT_SOFT"] if filled else T["BG_ELEVATED"],
            )

    def detail_snapshot(self) -> dict[str, str]:
        """当前详情栏显示的内容（测试与自动化用，避免依赖具体控件名）。"""
        return {
            "title": self.song_title.cget("text"),
            "subtitle": self.song_subtitle.cget("text"),
            "author": self.song_author.cget("text"),
            "bpm": self.metric_labels.get("BPM", self.metric_labels.get("bpm")).cget("text")  # type: ignore[union-attr]
            if self.metric_labels else "",
            "duration": self.metric_labels.get("时长", self.metric_labels.get("duration")).cget("text")  # type: ignore[union-attr]
            if self.metric_labels else "",
            "comment": self.comment_label.cget("text"),
            "folder": self.folder_label.cget("text"),
            "preview": self.preview_hint.cget("text") if hasattr(self, "preview_hint") else "",
        }

    # ------------------------------------------------------------------ 试听

    def _preview_folder(self) -> Path | None:
        """当前详情栏里那首歌的**本地**目录；设备侧或未选中返回 None。

        **试听只支持本地歌曲**：
        - 设备上的歌要先把 4~13 MB 的音频拉下来才能放，那是"为听 5 秒
          传一个文件"，不如让用户先复制到本地；
        - 无线 adb 更慢。
        所以这里明确只认本地，界面上给一句清楚的原因，而不是静默失败。
        """
        current = getattr(self, "current_song", None)
        if not current:
            return None
        kind, folder = current
        library = self.libraries.get(kind)
        if library is None or library.fs is None or library.location is None:
            return None
        if library.location.kind != "local":
            return None
        return Path(library.location.root) / folder

    def _preview_hint(self, text: str) -> None:
        if hasattr(self, "preview_hint"):
            self.preview_hint.configure(text=text)

    def _on_preview_volume(self, value: float) -> None:
        audio_player().set_volume(value)

    def _set_preview_button(self, playing: bool) -> None:
        if not hasattr(self, "preview_button"):
            return
        self.preview_button.configure(
            text="■" if playing else "▶"
        )

    # ------------------------------------------------------------ 试听进度条

    @staticmethod
    def _format_clock(seconds: float) -> str:
        """把秒数写成 `3:07`（超过一小时写 `1:02:03`）。"""
        total = max(0, int(seconds))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _preview_span(self) -> float:
        """进度条的量程 —— **不是歌曲总长，是这次试听的终点**。

        默认只放 30 秒，若进度条按整曲（161 秒）算，前 30 秒只能爬到 18%，
        看起来像"卡住了"。所以量程 = 起点 + 时长；选了"整曲"（0）才用总长。

        ## 用 `end_offset()` 而不是 `start_offset + limit_seconds`

        两者数值相同，但**语义不同**：`limit_seconds()` 是"这一段有多长"
        （30 秒档永远是 30），`end_offset()` 才是"这一段放到第几秒为止"
        （15 秒起播 30 秒 → 45.0）。
        v17.3 一开始把它写反过一次，后果是**每拖一次进度条量程就涨一截**：
        30 → 45 → 52.5 → 56.25，界面显示 `0:16 / 0:45`，
        而且目标位置按虚高的量程算，拖到一半结果跳到 22.5 秒 —— 越拖越偏。
        """
        player = audio_player()
        end = player.end_offset()
        if end > 0:
            return max(0.1, end)
        # 「整曲」档：没有终点，只能靠真实总长。
        duration = self._preview_duration()
        if duration > 0:
            return max(0.1, duration)
        return max(0.1, player.start_offset)

    def _preview_duration(self) -> float:
        """当前试听这首音频的总时长（秒）；不知道就返回 0。"""
        return float(getattr(self, "_preview_total", 0.0))

    def _refresh_preview_progress(self) -> None:
        """主线程定时器：把播放位置搬到进度条上。

        ## 为什么不在音频回调里更新
        音频回调在**另一个线程**，而 Tk 不是线程安全的（本项目踩过多次）。
        这里只是"读一个数然后设一个控件"，放主线程的 `after` 里最安全，
        200 ms 一次的开销可以忽略。
        """
        if not hasattr(self, "preview_progress"):
            # 控件还没建好（理论上不会）—— 保底再排一次，别让定时器断掉。
            self._preview_progress_job = self.root.after(200, self._refresh_preview_progress)
            return
        player = audio_player()
        if not player.is_playing():
            # 放完了（`_on_end` 清了状态）：把进度条推到终点并收起来。
            self._finish_preview_progress()
            return
        if self._seeking:
            # 用户正在拖动：**别用播放位置覆盖他手里的位置**，
            # 否则滑块会被来回拽，根本拖不动。
            self._preview_progress_job = self.root.after(200, self._refresh_preview_progress)
            return
        span = self._preview_span()
        position = min(player.position(), span)
        self.preview_progress.set(position / span)
        self.preview_time.configure(
            text=f"{self._format_clock(position)} / {self._format_clock(span)}"
        )
        self._preview_progress_job = self.root.after(200, self._refresh_preview_progress)

    def _finish_preview_progress(self) -> None:
        """试听结束：进度条走到头、按钮复位、提示清掉。"""
        self._preview_progress_job = None
        if not hasattr(self, "preview_progress"):
            return
        self.preview_progress.set(1.0)
        self._set_preview_button(False)
        self._preview_hint("")
        self._hide_preview_progress()

    def _show_preview_progress(self, span: float) -> None:
        if not hasattr(self, "preview_progress_row"):
            return
        self.preview_progress.configure(to=1)
        self.preview_progress.set(0)
        self.preview_time.configure(text=f"{self._format_clock(0)} / {self._format_clock(span)}")
        self.preview_progress_row.pack(fill="x", pady=(SPACE.S, 0))

    def _hide_preview_progress(self) -> None:
        if hasattr(self, "preview_progress_row"):
            self.preview_progress.set(0)
            self.preview_time.configure(text="00:00 / 00:30")
        job = getattr(self, "_preview_progress_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:  # pragma: no cover - 定时器可能已经跑完
                pass
            self._preview_progress_job = None

    def _on_seek_drag(self, value: float) -> None:
        """用户拖动进度条：先记下目标位置，松手才真的跳。

        **拖动过程中不能跳**：每移动一像素就 `stop()` + 重新解码，既卡又刺耳。
        `CTkSlider` 没有"松手"回调，所以用 `<ButtonRelease-1>` 收尾
        （见 `__init__` 里的 bind）。

        ## 量程在**按下那一刻冻结**
        `values` 是 0~1 的比例，要乘量程才变成秒数。
        量程在拖动期间**绝不能重取**：松手后播放器就换成"新的一遍"，
        量程随之改变，于是同一个比例会被换算成不同的秒数 —— 越拖越偏。
        """
        if not hasattr(self, "preview_progress"):
            return
        if not self._seeking:
            # 只在"按下"这一次取量程，拖动过程中不再重取。
            self._seek_span = self._preview_span()
        self._seeking = True
        span = self._seek_span
        # 存**比例**而不是秒数：秒数要等松手时结合本次片段的起点才能算出来，
        # 而起点在拖动期间是不动的、松手后才变。
        self._seek_target = max(0.0, min(1.0, float(value)))
        begin = audio_player().start_offset
        shown = begin + self._seek_target * max(0.0, span - begin)
        self.preview_time.configure(
            text=f"{self._format_clock(shown)} / {self._format_clock(span)}"
        )

    def _on_seek_release(self, _event=None) -> None:
        """松手：从拖动到的位置重新起播。

        ## 目标位置要**相对本次片段**算，不能当成歌曲绝对位置

        进度条的量程是 `片段起点 → 片段起点 + 时长`。
        所以拖到 50%、量程是 `15 → 45` 时，目标是 **30 秒**，不是 15 秒：

            目标 = 量程起点 + 比例 × (量程终点 - 量程起点)

        一开始漏了「加上量程起点」，直接把 `比例 × 量程终点` 当成歌曲位置，
        于是拖到 50% 落到 22.5 秒（而不是 30 秒），一次比一次偏。
        """
        if not getattr(self, "_seeking", False):
            return
        self._seeking = False
        player = audio_player()
        folder = self._preview_folder()
        current = player.playing_path
        if folder is None or current is None or current.parent != folder:
            return
        # 量程用拖动开始时冻结的那个，别在这里重取（播放器状态马上要变）。
        end = float(getattr(self, "_seek_span", 0.0)) or self._preview_span()
        begin = player.start_offset
        ratio = max(0.0, min(1.0, float(getattr(self, "_seek_target", 0.0))))
        # 注意 `_seek_target` 存的是**比例**（0~1），不是秒数。
        target = begin + ratio * max(0.0, end - begin)
        # 落到最后 1 秒内就当"放完了"，不要拿一个几乎空的片段去起播。
        if end - target < 1.0:
            audio_stop_all()
            self._finish_preview_progress()
            return
        self._start_preview(folder, start=target)

    def toggle_preview(self) -> None:
        """点一次播放，再点一次停（按钮文案跟着变）。"""
        if not audio_available():
            self._preview_hint(tr("detail.preview.unavailable"))
            return

        folder = self._preview_folder()
        if folder is None:
            current = getattr(self, "current_song", None)
            if current and self.libraries[current[0]].location is not None \
                    and self.libraries[current[0]].location.kind != "local":
                self._preview_hint(tr("detail.preview.local_only"))
            else:
                self._preview_hint(tr("detail.preview.pick_first"))
            return

        # 正在放这首歌 → 再点一下就是停
        playing = audio_player().playing_path
        if (playing is not None and playing.parent == folder) or getattr(self, "_preview_pending", None) == folder:
            audio_stop_all()
            self._preview_pending = None
            self._set_preview_button(False)
            self._preview_hint("")
            self._hide_preview_progress()
            return

        self._start_preview(folder)

    def _start_preview(self, folder: Path, *, start: float = 0.0) -> None:
        """从 `start` 秒起播这首歌的试听片段（进度条拖动也走这里）。"""
        # 开播前先停掉上一首（否则两首重叠）。**在主线程做**：
        # 它内部要 join 音频回调线程，不能从回调线程调。
        audio_stop_all()
        seconds = int(getattr(self.settings, "preview_seconds", 30))
        request_id = audio_player().request_id
        self._preview_pending = folder
        # 试听时长选"整曲"（0）时，量程得靠真实总长；这里先取一次
        # （要知道总长必须把文件读完解一遍，只在选整曲时才付这个代价）。
        total = 0.0

        self._preview_total = total
        name = folder.name
        self._set_preview_button(False)
        self._preview_hint(tr("detail.preview.loading"))

        def worker(task_token: Token) -> None:
            # 解码约 0.1~0.4 秒，放工作线程，别卡界面。
            if not task_token.alive():
                return
            total = self._probe_preview_total(folder) if seconds <= 0 or start > 0 else 0.0
            played = audio_player().play_song(folder, seconds=seconds, start=start,
                                               request_id=request_id)
            if not task_token.alive():
                return
            self.tasks.emit(
                "preview", "", -1,
                payload={"ok": played is not None, "folder": name, "request_id": request_id,
                         "path": str(played) if played else "",
                         "seconds": seconds, "start": start,
                         "total": total},
                token=task_token,
            )

        # 令牌由任务层自己创建并管理，这里**不需要**先要一个。
        #
        # 早先这里写的是 `token = self.tasks.token()` —— `TaskRunner` 上根本
        # 没有 `token()` 这个方法（只有 `new_token()` / `submit()`），于是
        # 点击试听**一进来就 AttributeError**。而按钮回调的异常被 Tk 的
        # 异常分发吞掉，用户看到的就是"点了完全没反应"：按钮不变、
        # 提示不变、没有任何报错。这就是"试听没反应"的真正原因。
        self.tasks.submit(worker, name=f"preview_{name}")

    def _probe_preview_total(self, folder: Path) -> float:
        """取这首歌音频的总时长（秒）；取不到返回 0。

        只在"整曲"档位或拖动定位时需要 —— 平常 30/60/120 秒档
        量程就是起点+时长，不必把整曲读完解一遍。
        """
        try:
            path = audio_find_audio_file(folder)
            if path is None:
                return 0.0
            return float(audio_probe_duration(path) or 0.0)
        except Exception as exc:  # pragma: no cover - 探测失败不该影响播放
            logger.debug("探测试听总长失败：%s", exc)
            return 0.0

    def _on_preview_result(self, payload: dict[str, Any]) -> None:
        """试听结果回到界面线程（由 `_pump_queue` 调）。"""
        request_id = payload.get("request_id")
        if request_id is not None and request_id != audio_player().request_id:
            return
        self._preview_pending = None
        if not payload.get("ok"):
            self._set_preview_button(False)
            self._preview_hint(tr("detail.preview.failed"))
            self._hide_preview_progress()
            return
        self._set_preview_button(True)
        seconds = int(payload.get("seconds", PREVIEW_SECONDS) or 0)
        total = float(payload.get("total", 0.0) or 0.0)
        if total > 0:
            self._preview_total = total
        if seconds <= 0:
            self._preview_hint(tr("detail.preview.playing_whole"))
        else:
            self._preview_hint(tr("detail.preview.playing", seconds=seconds))
        self._show_preview_progress(self._preview_span())
        # 起一个主线程定时器把播放位置搬到进度条上（**不在音频回调里碰 Tk**）。
        self._hide_preview_progress_job()
        self._preview_progress_job = self.root.after(200, self._refresh_preview_progress)

    def _hide_preview_progress_job(self) -> None:
        """只停定时器，不动控件（`_hide_preview_progress` 会连控件一起收）。"""
        job = getattr(self, "_preview_progress_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:  # pragma: no cover
                pass
            self._preview_progress_job = None

    def _render_cover(self, folder: str, path: str, data: bytes | None, title: str) -> None:
        self._last_cover_source = (folder, path, data, title)
        # 缓存键必须带主题：同一张图在浅色/深色下的补边底色不同，
        # 不带主题的话切换外观会一直显示上一种外观的图。
        key = f"{path}|{folder}|{ctk.get_appearance_mode()}"
        cached = self._cover_cache.get(key)
        if cached is not None:
            self.cover_label.configure(image=cached, text="")
            self.cover_label.image = cached
            return
        if not path and not data:
            self.cover_label.configure(image=None, text=tr("detail.no_cover"))
            self.cover_label.image = None
            return
        try:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(data)) if data else Image.open(path)
            image.load()
            # 补边底色用当前面板色：写死深色的话，浅色主题下封面两侧
            # 会露出一大块深色，也就是用户说的"预览图后面特别大的黑边"。
            decorated = decorate_cover(
                image.convert("RGB"), max_size=(216, 216), background=color("BG_CARD")
            )
            widget_image = ctk.CTkImage(
                light_image=decorated, dark_image=decorated, size=decorated.size
            )
            self.cover_label.configure(image=widget_image, text="")
            # 引用要挂在**长期存活**的对象上，否则 CTkImage 被 GC 时
            # 它内部的 TkinterPhotoImage 会把 Tk 图片名一并销毁，之后
            # Tk 再绘制就报 `image "pyimageN" doesn't exist`。
            #
            # 实测过：`del ctk_image; gc.collect()` 之后，`root.tk.call("image","names")`
            # 里那个名字真的消失了。所以 `self.cover_label.image` 与
            # `_cover_cache` 这两处引用都要保留。
            #
            # 注意别把这个报错怪到别处：同进程里若建了两个 `ctk.CTk()` 根，
            # 图片名属于各自的解释器，跨根取用也会报同样的错 —— 那是测试脚本
            # 的问题（本项目的一次真实误判），不是这里的缺陷。
            self.cover_label.image = widget_image
            if len(self._cover_cache) > 24:
                self._cover_cache.clear()
            self._cover_cache[key] = widget_image
        except Exception as exc:
            logger.warning("加载封面失败 %s: %s", folder, exc, exc_info=True)
            self.cover_label.configure(image=None, text=tr("detail.cover_failed"))
            self.cover_label.image = None

    def set_rating(self, rating: int) -> None:
        if not self.current_song:
            self.toast.warning("请先选择一首歌曲")
            return
        kind, folder = self.current_song
        library = self.libraries[kind]
        identity = library.identity or f"{kind}:{library.location.root if library.location else ''}"
        self.db.set_rating(identity, folder, rating)
        self._render_rating(rating)
        self._rating_map[folder] = rating
        self._list_for(kind).set_rating(folder, rating)
        self.toast.success(f"{folder} → {rating} 星" if rating else f"{folder} → 取消评分")

    def edit_comment(self) -> None:
        if not self.current_song:
            self.toast.warning("请先选择一首歌曲")
            return
        kind, folder = self.current_song
        identity = self.libraries[kind].identity
        _rating, comment = self.db.get_rating(identity, folder)
        result = CommentEditDialog(self.root, initial_text=comment).show()
        if result is None:
            return
        self.db.set_comment(identity, folder, result)
        self.comment_label.configure(text=result or "未评价")
        self.toast.success("评价已保存")
