"""路径与设备：浏览/粘贴路径、检测 Quest、最近路径。"""
from __future__ import annotations

import os
import tkinter as tk
from typing import Any, Sequence

from .app_common import shorten_path
from .device import QuestCandidate, scan_quest
from .diagnostics import slow
from .fsapi import FSUnavailable, Location
from .logging_setup import get_logger
from .theme import T
from .ui_dialogs import DevicePathDialog, InfoDialog
from .workers import Token
from .messages import tr

logger = get_logger("app_device")


class DeviceMixin:
    """路径与设备：浏览/粘贴路径、检测 Quest、最近路径。"""

    def commit_path(self, kind: str, load: bool = True) -> None:
        """把路径框里的内容记进设置（可选同时载入）。

        为什么单独做这个动作：路径原先**只在 `on_close()` 才保存**，
        程序崩溃/被强杀/任务管理器结束时用户刚输的路径就没了 ——
        "重启之后路径不在了"有一半来自这里。回车与失焦各触发一次，
        保证"输完就记下了"。

        `load=False` 用于失焦：用户可能只是点了别的按钮，不该在这里触发
        一次完整的库扫描（那会突然转圈）。
        """
        value = self.path_vars[kind].get().strip()
        if kind == "local":
            already = self.settings.local_path == value
        else:
            already = self.settings.quest_path == value
        if already:
            if load:
                self.load_library(kind)
            return
        if kind == "local":
            self.settings.local_path = value
        else:
            self.settings.quest_path = value
        if value:
            self.settings.remember(kind, value)
        self.settings.save()
        self._refresh_wizard()
        if load:
            self.load_library(kind)

    def choose_folder(self, kind: str) -> None:
        """浏览按钮：本地用系统对话框；Quest 用自绘的"设备文件夹选择器"。"""
        if kind == "local":
            from tkinter import filedialog

            current = self.path_vars["local"].get().strip()
            chosen = filedialog.askdirectory(
                title=tr("device.pick_local_title"), mustexist=True, initialdir=current or None
            )
            if chosen:
                self._accept_local_path(chosen)
            return
        self._open_quest_picker()

    def _accept_local_path(self, path: str) -> None:
        path = os.path.abspath(path)
        self.path_vars["local"].set(path)
        self.settings.local_path = path
        self.settings.remember("local", path)
        self.settings.save()
        self._refresh_wizard()  # 向导开着的话，把第 1 步打上勾
        self.load_library("local")

    def _open_quest_picker(self, candidates: Sequence[QuestCandidate] = ()) -> None:
        from .ui_devicepicker import pick_device_folder

        picked = pick_device_folder(self.root, adb_path=self.settings.adb_path, candidates=list(candidates))
        if picked is None:
            return
        value = picked.path if picked.kind != "adb" else f"adb:{picked.path}"
        self.path_vars["quest"].set(value)
        self.settings.quest_path = value
        self.settings.remember("quest", value)
        self.settings.save()
        if picked.kind == "mtp":
            self.toast.info("已选择 MTP 设备路径", detail=tr("device.mirror_first_time"))
        self._refresh_wizard()  # 向导开着的话，把第 2 步打上勾
        self.load_library("quest")

    def _recent_for(self, kind: str) -> list[str]:
        return list(self.settings.recent_local if kind == "local" else self.settings.recent_quest)

    def _show_recent_menu(self, kind: str) -> None:
        """弹出「最近用过的路径」菜单。

        `Settings.recent_local/quest` 其实**一直在写**（`remember()`），
        但 v14.9 之前从来没人读 —— 典型"存了不读"的半成品。这个菜单就是它的出口。
        """
        current = self.path_vars[kind].get().strip()
        recent = [p for p in self._recent_for(kind) if p and p != current]
        if not recent:
            self.toast.info("还没有最近路径", detail=tr("device.remember_hint"))
            return
        button = self.recent_buttons.get(kind)
        menu = tk.Menu(self.root, tearoff=0)
        for path in recent:
            menu.add_command(label=shorten_path(path),
                             command=lambda p=path, k=kind: self._use_recent(k, p))
        menu.add_separator()
        menu.add_command(label=tr("device.clear_recent"), command=lambda k=kind: self._clear_recent(k))
        try:
            if button is not None:
                menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
            else:
                menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def _use_recent(self, kind: str, path: str) -> None:
        self.path_vars[kind].set(path)
        self.settings.remember(kind, path)
        self.settings.save()
        self.load_library(kind)

    def _clear_recent(self, kind: str) -> None:
        if kind == "local":
            self.settings.recent_local = []
        else:
            self.settings.recent_quest = []
        self.settings.save()
        self.toast.info("已清空最近路径")

    def connect_wireless(self) -> None:
        """无线连接 Quest（ROADMAP D4）：配对码 / USB 转无线，连上后自动载入歌单。

        连上之后把 `host:port` 当作 adb 的 **serial** 用 —— `adb -s` 对无线设备同样有效，
        所以**后面所有复制/删除逻辑一行都不用改**，直接复用现有 adb 通道。
        """
        from .ui_wireless import WirelessDialog

        def on_connected(address: str) -> None:
            self.settings.adb_serial = address
            self.settings.save()
            self.toast.success("无线已连接", detail=f"{address}，正在载入设备歌单…")

        address = WirelessDialog(
            self.root, adb_path=self.settings.adb_path, on_connected=on_connected
        ).show()
        if not address:
            return
        self.set_status(f"无线已连接：{address}")
        self.detect_device("quest", serial_override=address)

    def detect_device(self, kind: str = "quest", silent: bool = False,
                      serial_override: str = "") -> None:
        self.set_status("正在检测设备（盘符 / adb / MTP）…")
        self.toast.info("正在检测设备…", detail=tr("device.try_all"))

        def worker(token: Token) -> None:
            self.tasks.emit("progress", "正在检测设备…", 10, token=token)
            with slow("检测设备（adb / MTP / 盘符）"):
                report = scan_quest(adb_path=self.settings.adb_path,
                                    serial=serial_override or self.settings.adb_serial)
            # 顺手把 adb 设备列表缓存下来给界面线程用（见 _known_adb_serials）。
            # 放在这里是因为本函数本来就跑在 worker 线程上，而"列出 adb 设备"
            # 正是几秒级的操作 —— 界面线程绝不能自己调。
            try:
                from .adb import AdbBridge

                serials = [d.serial for d in AdbBridge(self.settings.adb_path).list_devices()
                           if d.usable]
                self.tasks.emit("adb_serials", "", -1, payload={"serials": serials}, token=token)
            except Exception as exc:
                logger.debug("缓存 adb 设备列表失败：%s", exc)
            self.tasks.emit("device", "检测完成", 100, payload=report, token=token)

        self.tasks.submit(worker, name="detect_device")

    def _apply_device_report(self, report: Any) -> None:
        candidates: list[QuestCandidate] = list(getattr(report, "candidates", []))
        # 用户在设置里指定了偏好的连接方式时：把它排到最前，并**直接采用**（这就是"偏好"的意义）
        prefer = self.settings.quest_source
        if prefer in ("drive", "adb", "mtp"):
            candidates.sort(key=lambda c: 0 if c.kind == prefer else 1)
            preferred = [c for c in candidates if c.usable and c.kind == prefer
                         and c.location is not None]
            if preferred:
                self._use_candidate(preferred[0])
                self.set_status(f"已按设置偏好使用「{preferred[0].kind_label}」连接")
                return
        usable = [c for c in candidates if c.usable]
        if not candidates:
            hint = getattr(report, "adb_hint", "") or ""
            notes = "\n".join(getattr(report, "notes", []))
            InfoDialog(
                self.root,
                title=tr("device.not_found_title"),
                message=(
                    "没有找到可用的 Quest 歌曲目录。\n\n"
                    f"{notes}\n\n"
                    "排查顺序：\n"
                    "1) 头显用数据线连接并解锁，戴上头显点「允许 USB 调试」；\n"
                    "2) Quest 设置 → 系统 → 开发者 → 打开「USB 连接对话框」；\n"
                    f"3) 安装 adb（Android Platform Tools）。{hint}\n"
                    "4) 头显里至少启动过一次 Beat Saber，让 CustomLevels 目录被创建。"
                ),
            ).show()
            self.set_status("未检测到 Quest 设备")
            self.toast.warning("未检测到 Quest 设备", detail=tr("device.see_diagnostics"))
            return

        if len(usable) == 1 and usable[0].location is not None:
            self._use_candidate(usable[0])
            return

        chosen = DevicePathDialog(
            self.root,
            title=tr("device.pick_quest_title"),
            candidates=candidates,
            adb_path=getattr(report, "adb_path", ""),
        ).show()
        if not chosen:
            self.set_status("已取消设备检测")
            return
        for candidate in candidates:
            options = {candidate.display}
            if candidate.location is not None:
                options.update({candidate.location.root, candidate.location.shell_path})
                if candidate.kind == "adb":
                    options.add(f"adb:{candidate.location.root}")
            if chosen in options:
                self._use_candidate(candidate)
                return
        self._set_quest_path_text(chosen)

    def _use_candidate(self, candidate: QuestCandidate) -> None:
        if candidate.location is None:
            self._set_quest_path_text(candidate.display)
            return
        value = candidate.location.root if candidate.kind != "adb" else f"adb:{candidate.location.root}"
        self._set_quest_path_text(value, note=candidate.note)

    def _set_quest_path_text(self, value: str, note: str = "") -> None:
        self.path_vars["quest"].set(value)
        self.settings.quest_path = value
        self.settings.remember("quest", value)
        self.settings.save()
        self.set_status(f"Quest 路径已设置：{value}")
        self.toast.success(f"已连接：{value.split(':')[0] if ':' in value[:6] else 'Quest'}", detail=note or value)
        self._refresh_wizard()  # 向导开着的话，把第 2 步打上勾
        self.load_library("quest")

    def _resolve_location(self, kind: str, raw: str) -> Location:
        text = (raw or "").strip().strip('"')
        if not text:
            raise FSUnavailable("路径为空，请先选择文件夹")
        if kind == "local":
            return Location(kind="local", root=text, label=tr("device.local"))

        if text.lower().startswith("adb:"):
            root = text[4:].strip()
            if not root:
                raise FSUnavailable("adb 路径为空")
            serial = next(iter(self._known_adb_serials()), "")
            return Location(kind="adb", root=root, label="Quest（adb）", serial=serial)

        if text.startswith("::") or text.split("\\", 1)[0] in ("此电脑", "This PC"):
            return Location(kind="mtp", root=text, label="Quest（MTP）", shell_path=text)

        # 直接粘贴的设备路径（`/sdcard/...`、`/storage/...`）也要认出来。
        # v14.5 之前这类路径会掉进最后那个 `local`（"Quest（盘符）"）分支，被当成本地
        # 盘符去 open，报一句"路径不可访问" —— 用户完全看不出是被判成了本地路径。
        # 而 `--scan` 打印出来的路径正是这个格式，照着粘贴是很自然的操作。
        # 这两个前缀是 Android 专有的，Windows 上不可能是本地路径。
        if text.startswith(("/sdcard", "/storage/")):
            serial = next(iter(self._known_adb_serials()), "")
            return Location(kind="adb", root=text, label="Quest（adb）", serial=serial)

        return Location(kind="local", root=text, label=tr("device.quest_drive"))

    def _known_adb_serials(self) -> list[str]:
        """列出可用 adb 设备的 serial（**有缓存，界面线程绝不真调 adb**）。

        这里原本是同步 `list_devices()`，而 adb 命令可能要好几秒 ——
        实测最坏情况是 adb server 没在跑时的**冷启动 5.2 秒**（`adb
        kill-server` 之后立刻调用就是这个数）。而本方法的调用点在
        `_resolve_location()` 里，也就是**用户往路径框粘贴一个
        `/sdcard/...` 路径并回车时**，界面线程会被冻住 5 秒 ——
        这正是用户报的"使用时貌似会卡死"。

        所以拆成两半：
        * `note_adb_devices()` 由**后台线程**（设备检测）调，把结果记下来；
        * 本方法只读那份缓存，界面线程调用永远不阻塞。
        缓存为空时返回空串 serial —— `AdbFS` 自己会在后台线程里重新
        `ensure_device()`，不依赖这里给的值。
        """
        return list(self._adb_serials)

    def note_adb_devices(self, serials: list[str]) -> None:
        """由后台线程在设备检测后调用，更新上面那份缓存。"""
        self._adb_serials = list(serials)

    def _update_path_indicator(self, kind: str, ok: bool | None = None, pending: bool = False) -> None:
        """路径输入框后面的小圆点：绿=可用、黄=扫描中、红=不可用、灰=未设置。"""
        label = self.path_status.get(kind)
        if label is None:
            return
        text = self.path_vars[kind].get().strip()
        if pending:
            state, icon, role = "pending", "◌", "WARNING"
        elif ok is False:
            state, icon, role = "bad", "●", "DANGER"
        elif not text:
            state, icon, role = "empty", "○", "TEXT_MUTED"
        elif self.libraries[kind].fs is not None:
            state, icon, role = "ok", "●", "SUCCESS"
        else:
            state, icon, role = "unknown", "○", "TEXT_MUTED"
        try:
            label.configure(text=icon, text_color=T[role])
            label.configure(text_color=T[role])
        except Exception:
            pass
        if kind == "quest":
            self._update_device_badge(state, text)

    def _update_device_badge(self, state: str, path: str) -> None:
        mapping = {
            "ok": (tr("device.badge.ok"), "SUCCESS"),
            "pending": (tr("device.badge.pending"), "WARNING"),
            "bad": (tr("device.badge.bad"), "DANGER"),
            "unknown": (tr("device.none"), "TEXT_MUTED"),
            "empty": (tr("device.none"), "TEXT_MUTED"),
        }
        text, role = mapping.get(state, (tr("device.none"), "TEXT_MUTED"))
        try:
            self.device_badge.configure(text=text, text_color=T[role])
        except Exception:
            pass
