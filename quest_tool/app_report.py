"""对比与报告：算缺失、导出 CSV / Markdown / Beat Saber 歌单。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Sequence

from . import playlist as playlist_module
from .duplicates import DEFAULT_LEVELS, find_duplicates
from .logging_setup import get_logger
from .playlist import PLAYLIST_SUFFIX
from .songs import SongMeta
from .ui_dialogs import ProgressDialog
from .workers import Token
from .messages import catalog, tr
from .reporting import render_report

logger = get_logger("app_report")


class ReportMixin:
    """对比与报告：算缺失、导出 CSV / Markdown / Beat Saber 歌单。"""

    def compare(self, missing_side: str) -> None:
        local, quest = self.libraries["local"], self.libraries["quest"]
        if not local.ready or not quest.ready:
            missing_kind = "本地" if not local.ready else "Quest"
            self.toast.warning(f"还没有载入{missing_kind}歌曲", detail=tr("report.need_load_both"))
            return

        local_names, quest_names = local.name_set, quest.name_set
        if missing_side == "quest":
            missing = sorted(local_names - quest_names, key=str.lower)
            label, highlight_list, highlight_kind = "Quest 缺失（本地有 / Quest 没有）", self.list_local, "local"
        else:
            missing = sorted(quest_names - local_names, key=str.lower)
            label, highlight_list, highlight_kind = "本地缺失（Quest 有 / 本地没有）", self.list_quest, "quest"

        self.missing = missing
        self.missing_side = missing_side
        highlight_list.set_highlight(missing)
        # 另一侧也会被筛选影响（"本地列 → 已匹配"就是拿本地名字减 Quest 名字），
        # 所以两边都要重灌，不能只刷新"缺失"列。
        self._fill_list("missing")
        self._fill_list("local")
        self._fill_list("quest")
        if not missing:
            self.list_missing.set_empty_hint("✅ 没有缺失歌曲，两边已经一致")
        text = f"{label}：{len(missing)} 首"
        self.set_status(text)
        self.progress_bar.set(1)
        self.root.after(1400, lambda: self.progress_bar.set(0))
        if missing:
            self.toast.success(f"发现 {len(missing)} 首缺失歌曲", detail=tr("report.hint_copy"))
        else:
            self.toast.success("没有缺失歌曲", detail=tr("report.identical"))

    def find_duplicates(self, kind: str = "local", *, include_loose: bool = False) -> None:
        """找出某一侧歌单里的重复歌曲（ROADMAP D3），结果列进「缺失」列表。

        复用「缺失」那一列来展示结果，是因为它本来就是"需要你处理的一批歌"，
        而且已经接好了高亮、批量删除、右键菜单 —— 不必再造一个视图。

        `include_loose=True` 才启用最松的「作者 + BPM 相同」档（默认关闭：
        误报率明显更高，而误报会让用户删掉其实不同的歌）。
        """
        library = self.libraries[kind]
        if not library.folders:
            self.toast.warning(f"还没有载入{self._label_for(kind)}",
                               detail=tr("report.need_load_dup"))
            return

        metas = [library.meta.get(name) or SongMeta(folder_name=name)
                 for name in library.folders]
        levels = list(DEFAULT_LEVELS) + (["same_author_bpm"] if include_loose else [])
        report = find_duplicates(metas, levels=levels)

        if not report.groups:
            self.missing = []
            self.missing_side = kind
            self._fill_list("missing")
            self.list_missing.set_empty_hint("✅ 没有发现重复歌曲")
            self.set_status(report.summary())
            self.toast.success("没有发现重复歌曲", detail=f"检查了 {report.scanned} 首")
            return

        # 每组只把"多余的那几首"列出来（留一首），否则用户会把整组删光
        extras: list[str] = []
        risk_note = ""
        for group in report.groups:
            extras.extend(group.folder_names[1:])
            if group.risky:
                risk_note = "（含『仅标题相同』的组，删前请确认是同一首歌）"
        extras = sorted(set(extras), key=str.lower)

        self.missing = extras
        self.missing_side = kind
        self._list_for(kind).set_highlight(extras)
        self._fill_list("missing")
        self.list_missing.set_empty_hint("没有多余的重复歌曲")
        self.set_status(report.summary())
        self.toast.success(
            f"发现 {report.group_count} 组重复，{report.duplicate_count} 首多余",
            detail=f"已在「缺失」列列出多余的 {len(extras)} 首{risk_note}",
        )

    def export_playlist(self, kind: str = "local") -> None:
        """把某一侧歌单导出成 Beat Saber 的 `.bplist`（ROADMAP D2）。

        **hash 是必须真算的** —— 游戏按 hash 找歌，hash 不对的话歌单能打开但一首都不显示。
        算 hash 要读每首歌的 `info.dat` + 全部难度文件，几百首会有明显耗时，
        所以放**后台线程**跑，并用进度窗口显示进度。
        """
        library = self.libraries[kind]
        if not library.folders:
            self.toast.warning(f"还没有载入{self._label_for(kind)}",
                               detail=tr("report.need_load_export"))
            return

        # 只有本地盘上的歌才能算 hash（设备上的歌在镜像里，难度文件不一定完整）
        if library.location is None or library.location.kind != "local":
            self.toast.warning("只能导出本地歌单",
                               detail=tr("report.export_local_only"))
            return

        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            title=tr("report.export_playlist_title"),
            defaultextension=PLAYLIST_SUFFIX,
            initialfile=f"歌单_{time.strftime('%Y%m%d')}{PLAYLIST_SUFFIX}",
            filetypes=[("Beat Saber 歌单", f"*{PLAYLIST_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return

        root_dir = Path(library.location.root)
        folders = list(library.folders)
        token = self.tasks.new_token("playlist")
        progress = ProgressDialog(
            self.root, title=tr("report.exporting_playlist"),
            message=f"正在计算 {len(folders)} 首的谱面 hash…",
            cancel_callback=lambda: token.cancel(),
        )
        self._progress_dialog = progress
        self._progress_token = token.value
        progress.show_nonblocking()

        def worker(task_token: Token) -> None:
            entries: list[tuple[str, str, str, str]] = []
            failed = 0
            for index, name in enumerate(folders, start=1):
                if not task_token.alive():
                    break
                meta = library.meta.get(name)
                try:
                    digest = playlist_module.song_hash(root_dir / name)
                except Exception as exc:  # 任何异常都不该中断整批导出
                    logger.warning("算 hash 失败 %s: %s", name, exc)
                    digest = ""
                if not digest:
                    failed += 1
                entries.append((
                    name,
                    (meta.title if meta else "") or name,
                    (meta.author if meta else "") or "",
                    digest,
                ))
                percent = int(index / max(1, len(folders)) * 100)
                self.tasks.emit("progress", f"计算 hash {index}/{len(folders)}", percent,
                                token=task_token)

            result = playlist_module.build_playlist(
                title=f"{self._label_for(kind)} {time.strftime('%Y-%m-%d')}",
                author="Quest歌单对比工具",
                entries=entries,
            )
            try:
                saved = playlist_module.save_playlist(result, path)
            except OSError as exc:
                self.tasks.emit("error", f"写入歌单失败：{exc}", 100, token=task_token)
                return
            # hash 缺失的歌在游戏里**不会显示** —— 必须明确告诉用户，别让他以为歌单坏了
            self.tasks.emit(
                "copy",
                f"歌单已导出：{saved.name}",
                100,
                payload={"summary": playlist_module.hash_report(result.songs),
                         "failed": failed},
                token=task_token,
            )

        self.tasks.submit(worker, name="export_playlist")

    def import_playlist(self, kind: str = "local") -> None:
        """读一份 `.bplist`，把里面**本地有**的歌在列表里高亮出来（ROADMAP D2）。

        导入不做复制 —— 它只是"告诉我这份歌单里我有哪些"。
        要真复制，用户接着用「复制缺失」即可（高亮已经是现成的选择）。
        """
        library = self.libraries[kind]
        if not library.folders:
            self.toast.warning(f"还没有载入{self._label_for(kind)}",
                               detail=tr("report.need_load_import"))
            return

        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title=tr("report.import_playlist_title"),
            filetypes=[("Beat Saber 歌单", f"*{PLAYLIST_SUFFIX}"), ("JSON", "*.json"),
                       ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            loaded = playlist_module.load_playlist(path)
        except ValueError as exc:
            self.toast.error("歌单读不了", detail=str(exc))
            return

        root_dir = Path(library.location.root)
        available: list[tuple[str, str]] = []
        titles: dict[str, tuple[str, str]] = {}
        for name in library.folders:
            meta = library.meta.get(name)
            try:
                digest = playlist_module.song_hash(root_dir / name)
            except Exception:
                digest = ""
            available.append((name, digest))
            if meta:
                titles[name] = (meta.title or name, meta.author or "")

        matched, missing = playlist_module.match_local(loaded, available, titles=titles)
        self._list_for(kind).set_highlight(matched)
        self.set_status(
            f"歌单「{loaded.title or Path(path).stem}」："
            f"{len(matched)}/{loaded.count} 首在{self._label_for(kind)}里"
        )
        if matched:
            self.toast.success(f"匹配到 {len(matched)} 首",
                               detail=tr("report.import_ok"))
        else:
            self.toast.warning("一首都没匹配上",
                               detail=tr("report.import_none_2"))

    def on_sort(self, kind: str, mode: str, reverse: bool) -> None:
        logger.debug("列表 %s 排序: %s reverse=%s", kind, mode, reverse)

    def export_report(self, fmt: str = "csv") -> None:
        """把当前两侧歌单的对比结果导出成 CSV / Markdown（ROADMAP B3）。

        每首歌一行：状态（仅本地 / 仅设备 / 两边都有）、文件夹名、标题、作者、
        BPM、时长、难度、评分。**CSV 用 utf-8-sig（带 BOM）** —— 否则 Excel 打开中文乱码。
        """
        local_lib, quest_lib = self.libraries["local"], self.libraries["quest"]
        if not local_lib.folders and not quest_lib.folders:
            self.toast.warning("还没有可导出的内容", detail=tr("report.need_compare"))
            return

        local_names, quest_names = set(local_lib.folders), set(quest_lib.folders)
        local_ratings, quest_ratings = self._ratings_for("local"), self._ratings_for("quest")
        rows: list[list[str]] = []
        for name in sorted(local_names | quest_names, key=str.lower):
            if name in local_names and name in quest_names:
                status = "两边都有"
                meta = local_lib.meta.get(name) or quest_lib.meta.get(name)
                rating = local_ratings.get(name, 0)
            elif name in local_names:
                status = "仅本地"
                meta = local_lib.meta.get(name)
                rating = local_ratings.get(name, 0)
            else:
                status = "仅设备"
                meta = quest_lib.meta.get(name)
                rating = quest_ratings.get(name, 0)
            rows.append([
                status,
                name,
                meta.display_title if meta else "",
                meta.display_author if meta else "",
                meta.display_bpm if meta else "",
                meta.display_duration if meta else "",
                "/".join(meta.difficulties) if meta else "",
                str(rating) if rating else "",
            ])

        from tkinter import filedialog

        is_csv = fmt == "csv"
        path = filedialog.asksaveasfilename(
            title=tr("report.export_report_title"),
            defaultextension=".csv" if is_csv else ".md",
            initialfile=f"Quest歌单对比报告.{'csv' if is_csv else 'md'}",
            filetypes=[("CSV 表格", "*.csv")] if is_csv else [("Markdown", "*.md")],
        )
        if not path:
            return
        try:
            payload = self._render_report(rows, fmt)
            # ⚠️ 必须 `newline=""`：csv.writer 写的是 `\r\n`，而文本模式在 Windows 上
            # 会把 `\n` 再转义成 `\r\n` —— 结果是 `\r\r\n`，每行后面多一个空行。
            # encoding 用 utf-8-sig（带 BOM），否则 Excel 会按 GBK 解，中文标题全乱。
            with open(path, "w", encoding="utf-8-sig" if is_csv else "utf-8", newline="") as handle:
                handle.write(payload)
        except OSError as exc:
            self.toast.error("导出失败", detail=str(exc))
            return
        self.toast.success("对比报告已导出", detail=str(path))
        self.set_status(f"报告已导出：{path}")

    def _render_report(self, rows: Sequence[Sequence[str]], fmt: str) -> str:
        if fmt == "csv":
            return render_report(rows, fmt, labels=catalog("zh"))
        local_root = self.libraries["local"].location
        quest_root = self.libraries["quest"].location
        return render_report(rows, fmt, labels=catalog("zh"),
            generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
            local_root=local_root.root if local_root else None,
            quest_root=quest_root.root if quest_root else None)
