"""无线调试对话框（ROADMAP D4）。

让用户**不用插线**就能连上 Quest。两条路都支持，因为适用场景不同：

| 方式 | 前提 | 什么时候用 |
|---|---|---|
| **配对码**（`adb pair`） | Android 11+ / Quest 3 的「无线调试」页面 | **完全不用线**；但配对码会过期，每次重连可能要重来 |
| **USB 转无线**（`adb tcpip`） | 得先插一次线 | 只要插一次，之后一直能用；最省事、最不容易出错 |

界面按"先选方式 → 再填它要的东西 → 点连接"来组织，每一步都在界面上写清楚
**在头显里该怎么操作**，而不是让用户去猜。

为什么不用一句"请输入 IP"糊过去：Quest 的无线调试页面给的是
`IP:端口` + 6 位配对码，而**配对端口和连接端口不是同一个** ——
不把这层说清楚，用户必然会拿配对的端口去 connect，然后卡住。
"""
from __future__ import annotations

import threading
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from .adb import DEFAULT_WIRELESS_PORT, AdbBridge
from .logging_setup import get_logger
from .theme import FONT, RADIUS, SIZE, SPACE, T
from .ui_dialogs import BODY_FONT_SIZE, TITLE_FONT_SIZE, ModalDialog
from .messages import tr

logger = get_logger("wireless")

#: 「配对码」模式的两个输入框提示
PAIR_HINT = (
    "在头显里打开：设置 → 系统 → 开发者 → 无线调试 → 使用配对码配对设备。\n"
    "把屏幕上显示的「IP 地址和端口」和 6 位配对码填到下面。"
)
#: 「USB 转无线」模式的说明
TCPIP_HINT = (
    "先用数据线把头显连上电脑（并允许 USB 调试），点下面的按钮。\n"
    "成功后就可以拔掉线 —— 之后只要头显和电脑在同一个 Wi-Fi 下就能连。"
)


class WirelessDialog(ModalDialog):
    """无线连接向导。``show()`` 返回连接成功时的设备地址（失败/取消返回 None）。"""

    ICON = "📶"
    DEFAULT_TEXT = "连接"
    CANCEL_TEXT = "取消"

    def __init__(
        self,
        master: tk.Misc | None,
        *,
        adb_path: str = "",
        on_connected: Callable[[str], None] | None = None,
        size: tuple[int, int] = (720, 560),
    ) -> None:
        # 建体之前就得备好：_create_body() 会读它们
        self._adb_path = adb_path
        self._on_connected = on_connected
        self.DEFAULT_TEXT = "连接"
        self.CANCEL_TEXT = "取消"
        self._busy = False
        #: 连上之后的地址，`show()` 返回它
        self.connected_address = ""

        super().__init__(master, "无线连接 Quest", size=size, resizable=(False, False))
        self.result = None

    # ------------------------------------------------------------------
    # 内容
    # ------------------------------------------------------------------
    def _create_body(self, parent: ctk.CTkFrame) -> None:
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", pady=(2, SPACE.S))
        ctk.CTkLabel(head, text=self.ICON, font=(self.font, 30)).pack(side="left", padx=(2, 10))
        ctk.CTkLabel(head, text=tr("wireless.intro"),
                     font=(self.font, TITLE_FONT_SIZE, "bold"), anchor="w").pack(side="left")

        # ---- 方式选择 ----
        self.mode_var = ctk.StringVar(value="pair")
        modes = ctk.CTkFrame(parent, fg_color="transparent")
        modes.pack(fill="x", pady=(0, SPACE.S))
        for value, label, hint in (
            ("pair", "配对码（完全不用线）", "推荐：头显上打开「无线调试」即可"),
            ("tcpip", "USB 转无线（先插一次线）", "最省事：插一次线，之后都不用插"),
        ):
            row = ctk.CTkFrame(modes, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkRadioButton(
                row, text=label, value=value, variable=self.mode_var,
                font=(self.font, BODY_FONT_SIZE), fg_color=T["ACCENT"],
                hover_color=T["ACCENT_HOVER"], text_color=T["TEXT_PRIMARY"],
                command=self._on_mode_change,
            ).pack(side="left")
            ctk.CTkLabel(row, text=hint, font=(self.font, FONT.TINY),
                         text_color=T["TEXT_MUTED"]).pack(side="left", padx=(SPACE.M, 0))

        self.hint_label = ctk.CTkLabel(
            parent, text=PAIR_HINT, font=(self.font, FONT.SMALL),
            text_color=T["TEXT_SECONDARY"], justify="left", anchor="w", wraplength=560,
        )
        self.hint_label.pack(fill="x", pady=(SPACE.S, SPACE.S))

        # ---- 输入区 ----
        form = ctk.CTkFrame(parent, corner_radius=RADIUS.CARD, fg_color=T["BG_CARD"],
                            border_width=1, border_color=T["BORDER"])
        form.pack(fill="x")
        form.grid_columnconfigure(1, weight=1)

        # ★ 必须在这里记下行号：`grid_remove()` 会把 `grid_info()` 清空，
        #   之后再也问不出"这个控件原本在第几行"，就恢复不回来了
        #   （第一版就栽在这上面：切成 tcpip 模式后设备 IP 那一行再也显示不出来）。
        self._row_of: dict[tk.Misc, int] = {}

        def add_row(row: int, label: str, placeholder: str, attr: str) -> ctk.CTkEntry:
            label_widget = ctk.CTkLabel(form, text=label, font=(self.font, FONT.SMALL),
                                        text_color=T["TEXT_SECONDARY"], width=88, anchor="w")
            label_widget.grid(row=row, column=0, sticky="w",
                              padx=(SPACE.M, SPACE.XS), pady=SPACE.XS)
            var = ctk.StringVar()
            entry = ctk.CTkEntry(
                form, textvariable=var, height=SIZE.INPUT_H, corner_radius=RADIUS.CONTROL,
                font=(self.font, FONT.SMALL), fg_color=T["BG_INPUT"],
                border_color=T["BORDER_STRONG"], placeholder_text=placeholder,
            )
            entry.grid(row=row, column=1, sticky="ew", padx=(0, SPACE.M), pady=SPACE.XS)
            setattr(self, f"{attr}_var", var)
            setattr(self, f"{attr}_entry", entry)
            self._row_of[label_widget] = row
            self._row_of[entry] = row
            return entry

        # 配对模式的四个框：配对 IP / 配对端口 / 配对码 / 连接端口
        add_row(0, "配对 IP", "头显上显示的 IP，例如 192.168.1.23", "pair_host")
        add_row(1, "配对端口", "配对码上面那行的端口（通常是 3xxxx）", "pair_port")
        add_row(2, "配对码", "6 位数字", "pair_code")
        # tcpip 模式只需要一个 IP
        add_row(3, "设备 IP", "USB 模式会自动读取；也可手动填", "tcpip_host")
        add_row(4, "连接端口", f"默认 {DEFAULT_WIRELESS_PORT}", "connect_port")

        self._rows = {
            "pair": (0, 1, 2),
            "tcpip": (3, 4),
        }
        # 「连接端口」两种模式都要用（配对成功后还得连它），所以两边都显示
        self._rows["pair"] = (0, 1, 2, 4)
        self._form = form
        self._show_rows()

        def _remember_mode(*_args: object) -> None:
            self._on_mode_change()

        self.mode_var.trace_add("write", _remember_mode)

        # ---- 日志 ----
        self.log = ctk.CTkTextbox(parent, height=120, wrap="word",
                                  font=(self.font, FONT.TINY))
        self.log.pack(fill="both", expand=True, pady=(SPACE.S, 0))
        self.log.configure(state="disabled")
        self._say("选一种方式，按上面的说明在头显里操作，然后点「连接」。")

    def _create_buttons(self, parent: ctk.CTkFrame) -> None:
        self.cancel_button = ctk.CTkButton(
            parent, text=self.CANCEL_TEXT, width=120, height=SIZE.BUTTON_H,
            font=(self.font, BODY_FONT_SIZE), fg_color=T["NEUTRAL"],
            hover_color=T["NEUTRAL_HOVER"], command=self._on_cancel,
        )
        self.cancel_button.pack(side="right", padx=(10, 0))
        self.default_button = ctk.CTkButton(
            parent, text=self.DEFAULT_TEXT, width=150, height=SIZE.BUTTON_H,
            font=(self.font, BODY_FONT_SIZE, "bold"), fg_color=T["ACCENT"],
            hover_color=T["ACCENT_HOVER"], command=self._on_default,
        )
        self.default_button.pack(side="right")
        self.confirm_button = self.default_button

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _say(self, text: str) -> None:
        try:
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except tk.TclError:  # pragma: no cover
            pass

    def _on_mode_change(self) -> None:
        self._show_rows()
        self.hint_label.configure(text=PAIR_HINT if self.mode_var.get() == "pair" else TCPIP_HINT)

    def _show_rows(self) -> None:
        """按当前方式显示/隐藏输入行。

        用建体时记下的 `_row_of` 判断，**不能**用 `grid_info()` ——
        `grid_remove()` 会把它清空，之后所有被隐藏的行都"查无此行"。
        """
        wanted = set(self._rows.get(self.mode_var.get(), ()))
        for widget, row in self._row_of.items():
            try:
                if row in wanted:
                    widget.grid()
                else:
                    widget.grid_remove()
            except tk.TclError:  # pragma: no cover - 窗口已销毁
                pass

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    def _warn(self, title: str, text: str) -> None:
        """校验不通过时的提示。

        **刻意不用 `messagebox`**：那是模态的，会阻塞到用户点确定为止 ——
        对话框自己已经有日志区，把它写进去既不打断操作，也方便用户回看
        （自动化测试更不会因为等人的点击而卡死）。
        """
        self._say(f"⚠️ {title}：{text}")
        try:
            self.bell()
        except tk.TclError:  # pragma: no cover
            pass

    def _on_default(self) -> None:
        if self._busy:
            return
        if self.mode_var.get() == "pair":
            self._do_pair()
        else:
            self._do_tcpip()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        try:
            self.default_button.configure(state="disabled" if busy else "normal",
                                          text=text or self.DEFAULT_TEXT)
        except tk.TclError:  # pragma: no cover
            pass

    def _do_pair(self) -> None:
        host = self.pair_host_var.get().strip()
        port_text = self.pair_port_var.get().strip()
        code = self.pair_code_var.get().strip()
        if not host or not port_text or not code:
            self._warn("还差信息",
                       "配对模式要填三个：配对 IP、配对端口、配对码。"
                       "三个都在头显「使用配对码配对设备」那一页上。")
            return
        if not port_text.isdigit():
            self._warn("端口不对", "配对端口应该是纯数字。")
            return
        # 连上之后要用**连接端口**，没填就默认 5555
        connect_port = self.connect_port_var.get().strip()
        connect_port_number = int(connect_port) if connect_port.isdigit() else DEFAULT_WIRELESS_PORT

        self._set_busy(True, "配对中…")
        self._say(f"正在配对 {host}:{port_text} …")

        def work() -> None:
            bridge = AdbBridge(self._adb_path)
            ok, detail = bridge.pair(host, int(port_text), code)
            if ok:
                self.after(0, lambda: self._after_pair(host, connect_port_number, detail))
            else:
                self.after(0, lambda: self._finish_fail(f"配对失败：{detail}"))

        threading.Thread(target=work, daemon=True, name="wireless-pair").start()

    def _after_pair(self, host: str, port: int, detail: str) -> None:
        self._say(f"配对成功：{detail}")
        self._say(f"接下来连到 {host}:{port} …")
        self._try_connect(host, port, via="配对码")

    def _do_tcpip(self) -> None:
        host = self.tcpip_host_var.get().strip()
        port_text = self.connect_port_var.get().strip()
        port = int(port_text) if port_text.isdigit() else DEFAULT_WIRELESS_PORT

        self._set_busy(True, "处理中…")
        self._say("正在让设备打开 TCP 监听口（这一步需要 USB 连着）…")

        def work() -> None:
            bridge = AdbBridge(self._adb_path)
            try:
                output = bridge.start_tcpip(port)
            except Exception as exc:  # AdbError / AdbUnavailable 等
                self.after(0, lambda e=exc: self._finish_fail(
                    f"开 TCP 口失败：{e}\n"
                    "请确认数据线连着、头显里点了「允许 USB 调试」。"))
                return
            self.after(0, lambda: self._say(f"设备已响应：{output or '（无输出）'}"))
            # 没填 IP 就自动从设备读一个
            target = host
            if not target:
                target = bridge.device_ip()
                self.after(0, lambda t=target: self._say(
                    f"自动读到设备 IP：{t}" if t else "读不到设备 IP，请手动填「设备 IP」。"))
            if not target:
                self.after(0, lambda: self._finish_fail(
                    "读不到设备 IP。请在「设备 IP」里手动填上头显的 Wi-Fi 地址"
                    f"（通常是 192.168.x.x），然后点「连接」。"))
                return
            self.after(0, lambda t=target: self.tcpip_host_var.set(t))
            self.after(0, lambda: self._try_connect(target, port, via="USB 转无线"))

        threading.Thread(target=work, daemon=True, name="wireless-tcpip").start()

    def _try_connect(self, host: str, port: int, via: str) -> None:
        self._say(f"正在连接 {host}:{port} …（{via}）")

        def work() -> None:
            bridge = AdbBridge(self._adb_path)
            ok = bridge.connect(host, port)
            self.after(0, lambda: self._finish(ok, host, port, via))

        threading.Thread(target=work, daemon=True, name="wireless-connect").start()

    def _finish(self, ok: bool, host: str, port: int, via: str) -> None:
        if not ok:
            self._finish_fail(
                f"连不上 {host}:{port}。\n"
                "排查：① 头显和电脑在同一个 Wi-Fi？② 头显的「无线调试」还开着？\n"
                "③ 配对端口 ≠ 连接端口 —— 连接用的是「无线调试」页面上的那个端口。\n"
                "④ 有些路由器开了「AP 隔离」，会让同网段设备互相访问不了。")
            return
        self.connected_address = f"{host}:{port}"
        self._say(f"✅ 已连接：{self.connected_address}（{via}）")
        self._set_busy(False, "已连接")
        if self._on_connected is not None:
            try:
                self._on_connected(self.connected_address)
            except Exception as exc:  # 回调失败不该把对话框搞崩
                logger.warning("无线连接回调异常: %s", exc)
        # 连上了就自动关，主程序会接着去载入设备歌单
        self.after(300, lambda: self._finish_ok())

    def _finish_ok(self) -> None:
        self.result = self.connected_address
        self._destroy_self()

    def _finish_fail(self, text: str) -> None:
        self._say("❌ " + text)
        self._set_busy(False)
