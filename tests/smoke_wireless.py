"""无线调试检查（ROADMAP D4）。

**为什么这个测试特别重要**：无线功能是本项目**唯一完全无法在本机验证**的东西 ——
没有 Quest、没有同网段设备，`adb pair` / `adb connect` 的成败只能等真机。
所以这里的策略是：

1. **把不依赖设备的纯逻辑测透**：IP 判定、地址拼装、配对输出解析
   （`adb pair` 的成功/失败**也是退出码 0**，只能靠文字判断，这是最容易写错的地方）；
2. **用假的 adb 可执行文件**驱动整条链路 —— 它按参数返回预设输出，
   于是"配对成功 → 自动连接 → 连上后的地址"这条流程可以完整跑一遍，
   包括**失败路径**（端口不通 / 配对码过期 / 读不到 IP）；
3. **对话框层面**：两种方式切换后输入行必须正确显示/隐藏
   （这里踩过一个真坑，见 `_show_rows` 的注释）。

真机上要验的三件事写进 HANDOVER，不在本测试里假装验过。
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
from pathlib import Path

from _paths import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  -> ' + detail) if detail else ''}")


# ======================================================================================
# 1) 纯逻辑：IP 判定
# ======================================================================================
def check_ip_parsing() -> None:
    from quest_tool.adb import _is_ipv4

    print("\n--- IP 判定 ---")
    good = ["192.168.1.20", "10.0.0.1", "255.255.255.255", "0.0.0.0", "172.16.31.9"]
    bad = ["", "abc", "192.168.1", "192.168.1.1.5", "256.1.1.1", "192.168.1.",
           "-1.2.3.4", "192.168.1.a", " 192.168.1.1"]
    wrong = [t for t in good if not _is_ipv4(t)] + [t for t in bad if _is_ipv4(t)]
    check(f"IPv4 判定（{len(good)} 合法 + {len(bad)} 非法）", not wrong, str(wrong))


# ======================================================================================
# 2) 假 adb：驱动 pair / connect / tcpip 三条命令
# ======================================================================================
FAKE_ADB = '''#!{python}
"""假 adb：只回放测试预设的输出，用来把无线流程整条跑通。"""
import os, sys

args = sys.argv[1:]
joined = " ".join(args)
# 让测试能按场景改行为
scenario = os.environ.get("FAKE_ADB_SCENARIO", "ok")

def out(text):
    sys.stdout.write(text + "\\n")
    return 0

if args[:1] == ["pair"]:
    if scenario == "pair_fail":
        sys.exit(out("failed to connect to 192.168.1.50:37123: Connection refused"))
    if scenario == "pair_badcode":
        sys.exit(out("Enter pairing code: 000000\\nInvalid pairing code"))
    if scenario == "pair_ok":
        sys.exit(out("Successfully paired to 192.168.1.50:37123 [guid=adb-XXX]"))

if args[:1] == ["connect"]:
    if scenario in ("connect_fail", "pair_ok_noconnect"):
        sys.exit(out("failed to connect to 192.168.1.50:5555"))
    sys.exit(out("connected to 192.168.1.50:5555"))

if args[:1] == ["tcpip"]:
    if scenario == "tcpip_fail":
        sys.exit(out("error: device offline"))
    sys.exit(out("restarting in TCP mode port: 5555"))

if args[:1] == ["disconnect"]:
    sys.exit(out("disconnected 192.168.1.50:5555"))

if args[:2] == ["shell", "ip route | grep -m1 'wlan0' | awk '{{print $9}}'"] or "ip route" in joined:
    if scenario == "no_ip":
        sys.exit(out(""))
    sys.exit(out("192.168.1.50"))

if args[:1] == ["shell"]:
    sys.exit(out(""))

if args[:1] == ["devices"]:
    sys.exit(out("List of devices attached\\n192.168.1.50:5555\\tdevice"))

sys.exit(out(""))
'''


class FakeAdb:
    """造一个假的 adb 可执行文件（.py + .cmd 包装，Windows 上也能直接调）。"""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="fakeadb-wireless-"))
        script = self.dir / "fake_adb.py"
        script.write_text(FAKE_ADB.format(python=sys.executable), encoding="utf-8")
        if os.name == "nt":
            wrapper = self.dir / "adb.cmd"
            wrapper.write_text(
                f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
            )
            self.path = str(wrapper)
        else:
            wrapper = self.dir / "adb"
            wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
                               encoding="utf-8")
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
            self.path = str(wrapper)

    def cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)


def check_bridge_commands(fake: FakeAdb) -> None:
    from quest_tool.adb import AdbBridge

    print("\n--- AdbBridge 的无线命令 ---")

    def bridge() -> AdbBridge:
        return AdbBridge(fake.path)

    # 配对成功
    os.environ["FAKE_ADB_SCENARIO"] = "pair_ok"
    ok, detail = bridge().pair("192.168.1.50", 37123, "123456")
    check("配对成功时 pair() 返回 True", ok, detail[:60])

    # 配对失败：adb 的**退出码同样是 0**，只能靠文字判断 —— 这是最容易写错的地方
    os.environ["FAKE_ADB_SCENARIO"] = "pair_fail"
    ok, detail = bridge().pair("192.168.1.50", 37123, "123456")
    check("端口不通时 pair() 返回 False（不看退出码）", not ok, detail[:40])
    check("失败原因给了人话提示", "端口" in detail or "IP" in detail, detail[:40])

    os.environ["FAKE_ADB_SCENARIO"] = "pair_badcode"
    ok, detail = bridge().pair("192.168.1.50", 37123, "000000")
    check("配对码错时给出「码过期」的提示", not ok and "配对码" in detail, detail[:40])

    # 连接
    os.environ["FAKE_ADB_SCENARIO"] = "ok"
    check("connect() 成功返回 True", bridge().connect("192.168.1.50", 5555))
    os.environ["FAKE_ADB_SCENARIO"] = "connect_fail"
    check("connect() 失败返回 False（不抛异常）",
          bridge().connect("192.168.1.50", 5555) is False)

    # tcpip
    os.environ["FAKE_ADB_SCENARIO"] = "ok"
    output = bridge().start_tcpip(5555)
    check("start_tcpip() 返回设备原始响应", "TCP mode" in output, output[:50])

    # 读设备 IP
    check("device_ip() 能从 ip route 解析出 IP",
          bridge().device_ip() == "192.168.1.50", bridge().device_ip())
    os.environ["FAKE_ADB_SCENARIO"] = "no_ip"
    check("读不到 IP 时返回空串（不抛异常）", bridge().device_ip() == "", repr(bridge().device_ip()))

    # 连不上设备时不抛异常（可用性差也不能崩）
    os.environ["FAKE_ADB_SCENARIO"] = "ok"
    missing = AdbBridge(str(fake.dir / "does-not-exist"))
    check("adb 不存在时 connect() 返回 False 而不是抛异常",
          missing.connect("192.168.1.50", 5555) is False)
    check("adb 不存在时 disconnect() 不抛异常", _silent(missing.disconnect, "1.2.3.4"))


def _silent(func, *args) -> bool:
    try:
        func(*args)
        return True
    except Exception:
        return False


# ======================================================================================
# 3) 对话框：模式切换时的输入行
# ======================================================================================
def check_dialog() -> None:
    import customtkinter as ctk

    from quest_tool.ui_wireless import WirelessDialog

    print("\n--- 无线对话框 ---")
    root = ctk.CTk()
    root.withdraw()
    dialog = WirelessDialog(root, adb_path="")
    root.update()

    def visible_rows() -> list[int]:
        return sorted({row for widget, row in dialog._row_of.items() if widget.winfo_manager()})

    check("配对模式显示：配对3项 + 连接端口",
          visible_rows() == [0, 1, 2, 4], str(visible_rows()))
    check("默认方式是配对码", dialog.mode_var.get() == "pair", dialog.mode_var.get())

    dialog.mode_var.set("tcpip")
    _pump(root, 0.3)
    check("切到 USB 转无线：显示设备 IP + 端口",
          visible_rows() == [3, 4], str(visible_rows()))
    check("说明文字跟着换", "数据线" in dialog.hint_label.cget("text"),
          dialog.hint_label.cget("text")[:20])

    dialog.mode_var.set("pair")
    _pump(root, 0.3)
    check("切回配对模式：行全都回来（grid_remove 会清空 grid_info，必须自己记行号）",
          visible_rows() == [0, 1, 2, 4], str(visible_rows()))

    # 缺信息时不该真的去连，也不该弹**模态**框（模态会阻塞到有人点确定 ——
    # 自动化里就是死等）。产品代码已改成把提示写进日志区，这里再兜一层：
    # 万一以后有人改回 messagebox，测试会立刻红而不是挂死。
    import tkinter.messagebox as _mb

    popped: list[str] = []
    real_showinfo = _mb.showinfo
    _mb.showinfo = lambda *a, **k: popped.append(a[0] if a else "?")
    try:
        dialog._on_default()
        _pump(root, 0.4)
    finally:
        _mb.showinfo = real_showinfo
    check("什么都没填时点连接不会崩、也不会真的去连",
          dialog.connected_address == "" and not dialog._busy)
    check("校验提示走日志区，不弹模态框（模态会把自动化测试挂死）",
          not popped, f"弹了：{popped}")
    check("日志里能看到缺信息的提示",
          "还差信息" in dialog.log.get("1.0", "end"),
          dialog.log.get("1.0", "end")[-60:].replace("\n", " "))

    # 日志能写（直接读控件内容）
    dialog._say("测试")
    check("日志区可写", "测试" in dialog.log.get("1.0", "end"), dialog.log.get("1.0", "end")[:30])

    # 连上后的收尾：result 与回调
    seen: list[str] = []
    dialog._on_connected = seen.append
    dialog._finish(True, "192.168.1.50", 5555, "测试")
    _pump(root, 0.8)
    check("连上后回调收到地址", seen == ["192.168.1.50:5555"], str(seen))
    check("连上后地址被记录", dialog.connected_address == "192.168.1.50:5555",
          dialog.connected_address)

    root.destroy()


def _pump(root, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            root.update()
        except Exception:
            return
        time.sleep(0.02)


def main() -> int:
    print("=" * 78)
    print("无线调试检查（纯逻辑 + 假 adb 驱动全流程 + 对话框）")
    print("=" * 78)

    check_ip_parsing()
    fake = FakeAdb()
    try:
        check_bridge_commands(fake)
    finally:
        fake.cleanup()
        os.environ.pop("FAKE_ADB_SCENARIO", None)
    check_dialog()

    print()
    print("=" * 78)
    failed = [r for r in RESULTS if not r[1]]
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    for label, _ok, detail in failed:
        print(f"  FAIL  {label}  {detail}")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
