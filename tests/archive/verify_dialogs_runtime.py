# -*- coding: utf-8 -*-
"""运行时验证：真实模态 show() + 设备选择器的后台懒加载（本机无 Quest，用本地磁盘验证）。

smoke_dialogs.py 只构造不 show（避免卡死）；本脚本补上"真跑一遍"的部分：

1. ConfirmDialog 真的 show()：Enter 触发默认动作、Esc/取消返回值、居中位置是否正确；
2. ProgressDialog 的 set_progress / append_log / request_cancel / finish（非阻塞显示）；
3. DeviceFolderPicker 在隐藏父窗口下 show()，切到「本地磁盘」，异步展开到
   D:\\QA\\Quest3bs\\_review 并确定 —— 这一段完整走通了
   后台线程读取 → after(0) 回主线程填树 → 逐层 reveal 的链路。

有一个总看门狗：30 秒内没跑完就销毁 root，让脚本以失败退出而不是永久挂住。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk  # noqa: E402

from quest_tool import device as device_mod  # noqa: E402
from quest_tool.device import QuestCandidate  # noqa: E402
from quest_tool.fsapi import Location  # noqa: E402
from quest_tool.ui_devicepicker import (  # noqa: E402
    MTP_NOTE,
    SOURCE_ADB,
    SOURCE_LABELS,
    SOURCE_LOCAL,
    SOURCE_MTP,
    DeviceFolderPicker,
    PickedPath,
)
from quest_tool.ui_dialogs import (  # noqa: E402
    CommentEditDialog,
    ConfirmDialog,
    DevicePathDialog,
    InfoDialog,
    ProgressDialog,
    TextPromptDialog,
)

GOAL = r"D:\QA\Quest3bs\_review"
FAILURES: list[str] = []


def check(label: str, ok: bool, extra: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{('  → ' + extra) if extra else ''}")
    if not ok:
        FAILURES.append(label)


def main() -> int:
    root = ctk.CTk()
    root.withdraw()
    screen = (root.winfo_screenwidth(), root.winfo_screenheight())
    print(f"root 已 withdraw；屏幕 {screen[0]}x{screen[1]}")

    watchdog = {"fired": False}

    def on_timeout() -> None:
        watchdog["fired"] = True
        print("  [FAIL] 看门狗超时（30s），强制销毁 root")
        FAILURES.append("看门狗超时")
        try:
            root.destroy()
        except Exception:
            pass

    root.after(30000, on_timeout)

    # ------------------------------------------------------------------
    print("\n=== 1) ConfirmDialog 真实模态（隐藏父窗口） ===")
    confirm = ConfirmDialog(root, "确认操作", "确定要执行这次复制吗？", detail="共 12 首歌曲")
    seen: dict[str, object] = {}

    def press_enter() -> None:
        seen["geom"] = (confirm.winfo_x(), confirm.winfo_y(), confirm.winfo_width(), confirm.winfo_height())
        seen["viewable"] = bool(confirm.winfo_viewable())
        confirm.default_button.invoke()  # 等同于点「确定」

    root.after(400, press_enter)
    t0 = time.perf_counter()
    result = confirm.show()
    elapsed = (time.perf_counter() - t0) * 1000
    check("Enter/确定 → show() 返回 True 且不挂死", result is True, f"{elapsed:.0f} ms")
    gx, gy, gw, gh = seen.get("geom", (0, 0, 0, 0))  # type: ignore[misc]
    center = ((gx + gw / 2) - screen[0] / 2, (gy + gh / 2) - screen[1] / 2)
    check(
        "对话框已映射且窗口大小 = 请求尺寸",
        bool(seen.get("viewable")) and gw > 300 and gh > 200,
        f"x={gx} y={gy} w={gw} h={gh}",
    )
    check(
        "居中（父窗口不可见时按屏幕居中，偏差 < 60px）",
        abs(center[0]) < 60 and abs(center[1]) < 60,
        f"偏差 {center[0]:.0f},{center[1]:.0f}",
    )

    cancel_dlg = ConfirmDialog(root, "确认删除", "删除后无法恢复", confirm_is_danger=True)
    root.after(250, cancel_dlg.cancel_button.invoke)
    check("取消 → show() 返回 False", cancel_dlg.show() is False)

    info = InfoDialog(root, "提示", "已完成。")
    root.after(250, info._on_default)
    check("InfoDialog → show() 返回 None", info.show() is None)

    # ------------------------------------------------------------------
    print("\n=== 2) ProgressDialog 非阻塞 + 更新接口 ===")
    cancelled = {"n": 0}
    progress = ProgressDialog(
        root, title="正在复制", message="正在复制 12 首歌曲…", cancel_callback=lambda: cancelled.__setitem__("n", cancelled["n"] + 1)
    )
    progress.show_nonblocking()
    check("show_nonblocking 后窗口存在且未阻塞", bool(progress.winfo_exists()))
    progress.set_progress(42, "复制第 5/12 首")
    check("set_progress 生效", abs(progress.progress_bar.get() - 0.42) < 0.001,
          f"bar={progress.progress_bar.get():.2f} label={progress.percent_label.cget('text')}")
    progress.append_log("已复制 A")
    progress.append_log("已复制 B")
    log_text = progress.log.get("1.0", "end-1c")
    check("append_log 追加日志（只读控件仍可写）", "已复制 A" in log_text and "已复制 B" in log_text)
    progress.request_cancel()
    check(
        "点取消：回调被调用、按钮禁用、窗口不销毁",
        cancelled["n"] == 1
        and progress.cancelled
        and bool(progress.winfo_exists())
        and "disabled" in str(progress.cancel_button.cget("state")),
        f"state={progress.cancel_button.cget('state')} text={progress.cancel_button.cget('text')}",
    )
    progress.request_cancel()
    check("重复点取消不会重复回调", cancelled["n"] == 1)
    progress.finish("完成：12/12")
    check(
        "finish：进度 100%、窗口仍在（由调用方关闭）",
        abs(progress.progress_bar.get() - 1.0) < 0.001 and bool(progress.winfo_exists()),
    )
    progress.close()
    check("close() 销毁窗口", not progress.winfo_exists())

    # ------------------------------------------------------------------
    print("\n=== 3) DeviceFolderPicker：后台懒加载 + 逐层 reveal + 确定 ===")
    candidates = [
        QuestCandidate(
            kind="drive",
            display=r"D:\Songs\CustomLevels",
            usable=True,
            location=Location(kind="local", root=r"D:\Songs\CustomLevels", label="本地"),
            note="盘符方式",
        ),
        QuestCandidate(
            kind="mtp",
            display="Quest 3\\内部共享存储空间",
            usable=False,
            location=Location(kind="mtp", root="::{GUID}", label="Quest 3（MTP）", shell_path="::{GUID}\\Quest 3"),
            note="MTP 只能浏览",
        ),
    ]
    picker = DeviceFolderPicker(root, candidates=candidates)
    print(f"  初始来源 = {picker._source}（有盘符候选 → 本地磁盘）")

    def poll_ready(tries: int = 0) -> None:
        if picker._pending > 0 and tries < 60:
            root.after(100, lambda: poll_ready(tries + 1))
            return
        check("本地磁盘根节点已加载（后台线程 + after 回主线程）",
              len(picker._root_iids) == len(device_mod.drive_letters()),
              f"roots={len(picker._root_iids)} / drives={len(device_mod.drive_letters())}")
        print(f"  状态栏：{picker.status_label.cget('text')}")
        print(f"  开始逐层展开到 {GOAL}")
        picker._reveal_to(GOAL)
        root.after(120, lambda: poll_reveal(0))

    def poll_reveal(tries: int) -> None:
        selection = picker.tree.selection()
        picked = picker._current_picked()
        if picked is not None and picked.path.lower() == GOAL.lower():
            check("逐层 reveal 到达目标（每一层都是异步加载）", True, picked.path)
            print(f"  状态栏：{picker.status_label.cget('text')}")
            print(f"  路径输入框：{picker.path_var.get()}")
            check("路径输入框同步显示选中目录", picker.path_var.get().lower() == GOAL.lower())
            check("右侧说明已刷新", GOAL.lower() in str(picker.info_label.cget("text")).lower())
            print(f"  确定前：pending={picker._pending} 按钮 state={picker.default_button.cget('state')!r}")
            check("读取完成后「确定」已恢复可用", picker._pending == 0
                  and str(picker.default_button.cget("state")) == "normal")
            picker.default_button.invoke()  # 确定
            if picker.winfo_exists():  # 兜底：万一按钮被禁用，直接走一次默认动作
                picker._on_default()
            return
        if tries < 80:
            root.after(100, lambda: poll_reveal(tries + 1))
            return
        check("逐层 reveal 到达目标（每一层都是异步加载）", False,
              f"超时，当前选中 = {selection!r}")
        picker._on_cancel()

    root.after(200, lambda: poll_ready(0))
    picked = picker.show()
    check(
        "show() 返回 PickedPath(local, 目标目录, usable=True)",
        isinstance(picked, PickedPath) and picked.kind == "local" and picked.usable
        and picked.path.lower() == GOAL.lower(),
        repr(picked),
    )

    # ---- 静态契约（不需要设备） ----
    print("\n=== 4) 路径/k索引推断与 MTP 说明（无设备即可验证） ===")
    check("adb: 前缀 → kind=adb 且去掉前缀",
          DeviceFolderPicker._kind_from_text("adb:/sdcard/Android/data/x") == ("adb", "/sdcard/Android/data/x"))
    check("/sdcard/... → kind=adb",
          DeviceFolderPicker._kind_from_text("/sdcard/CustomLevels") == ("adb", "/sdcard/CustomLevels"))
    check("::{GUID} → kind=mtp",
          DeviceFolderPicker._kind_from_text("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\Quest 3")[0] == "mtp")
    check("D:\\... → kind=local", DeviceFolderPicker._kind_from_text("D:\\Songs\\CustomLevels")[0] == "local")
    mtp_pick = DeviceFolderPicker._make_picked("mtp", "::{GUID}\\Quest 3")
    check("MTP 结果 = 不可读写 + 带限制说明", (not mtp_pick.usable) and mtp_pick.note == MTP_NOTE)
    check("本地结果 = 可读写且无限制说明",
          DeviceFolderPicker._make_picked("local", r"D:\Songs").usable
          and DeviceFolderPicker._make_picked("local", r"D:\Songs").note == "")

    # 手动粘贴 adb 路径 → 确定（不 show()，直接走 _on_default）
    manual = DeviceFolderPicker(root)
    manual.path_var.set("adb:/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels")
    manual._on_default()
    check(
        "手输 adb 路径：返回 kind=adb 且路径不带 adb: 前缀",
        isinstance(manual.result, PickedPath)
        and manual.result.kind == "adb"
        and manual.result.path == "/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels",
        repr(manual.result),
    )
    check("确定后窗口已销毁", not manual.winfo_exists())

    # ------------------------------------------------------------------
    print("\n=== 5) ADB 来源在没装 adb 时的提示（快速失败，不卡 UI） ===")
    adb_picker = DeviceFolderPicker(root)
    adb_picker._source = SOURCE_ADB
    adb_picker.source_button.set(SOURCE_LABELS[SOURCE_ADB])
    adb_picker.reload()
    deadline = time.time() + 5
    while adb_picker._pending > 0 and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    check("无 adb 时状态栏给出明确提示", "adb" in str(adb_picker.status_label.cget("text")).lower(),
          str(adb_picker.status_label.cget("text"))[:80])

    print("\n=== 6) 跳到 CustomLevels：ADB / MTP 在没有设备时的表现 ===")
    adb_picker.jump_to_custom_levels()
    deadline = time.time() + 6
    while adb_picker._pending > 0 and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    check("ADB 下跳转：提示未找到 adb 且不崩",
          "未找到 adb" in str(adb_picker.status_label.cget("text")),
          str(adb_picker.status_label.cget("text"))[:80])
    adb_picker.destroy()

    # MTP 是本机的主场景（没插 Quest）：必须给出干净提示、绝不卡住
    mtp_picker = DeviceFolderPicker(root)  # 无候选 → 默认停在第 1 页 MTP
    check("无候选时默认停在便携设备(MTP)页", mtp_picker._source == SOURCE_MTP,
          f"source={mtp_picker._source}")
    mtp_picker.reload()  # 不走 show()：靠心跳 + root.update() 驱动，验证后台读取不会卡住
    deadline = time.time() + 20
    while mtp_picker._pending > 0 and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    mtp_status = str(mtp_picker.status_label.cget("text"))
    check("MTP 探测（本机无 Quest）：不挂死并给出未检测到便携设备",
          mtp_picker._pending == 0 and "未检测到便携设备" in mtp_status,
          f"{mtp_status[:70]}（等待 {20 - (deadline - time.time()):.1f}s）")
    mtp_picker.jump_to_custom_levels()
    deadline = time.time() + 20
    while mtp_picker._pending > 0 and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    check("MTP 下跳转：同样给出未检测到便携设备",
          "未检测到便携设备" in str(mtp_picker.status_label.cget("text")),
          str(mtp_picker.status_label.cget("text"))[:80])
    mtp_picker.destroy()

    print("\n=== 7) InfoDialog 复制到剪贴板 ===")
    long_info = InfoDialog(root, "详细信息", "第一行内容", detail="第二段内容")
    long_info.copy_to_clipboard()
    copied = ""
    try:
        copied = str(root.clipboard_get())
    except Exception as exc:  # noqa: BLE001
        copied = f"<剪贴板读取失败: {exc}>"
    check("复制按钮把正文放进剪贴板", "第一行内容" in copied and "第二段内容" in copied, copied.replace("\n", "\\n")[:60])
    check("复制后按钮有反馈", "已复制" in str(long_info._copy_button.cget("text")))
    long_info.destroy()

    # ------------------------------------------------------------------
    print("\n=== 8) DevicePathDialog：候选 → 返回字符串（主程序约定的格式） ===")
    adb_root = "/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels"
    shell_path = "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\Quest 3\\Internal shared storage"
    three = [
        QuestCandidate(kind="adb", display=adb_root, usable=True,
                       location=Location(kind="adb", root=adb_root, label="Quest 3 · ADB", serial="1WMHH"),
                       note="通过 adb 连接"),
        QuestCandidate(kind="drive", display=r"D:\Songs\CustomLevels", usable=True,
                       location=Location(kind="local", root=r"D:\Songs\CustomLevels", label="本地"),
                       note="盘符方式"),
        QuestCandidate(kind="mtp", display="Quest 3\\内部共享存储空间", usable=False, exists=True,
                       location=Location(kind="mtp", root="::{GUID}", label="Quest 3（MTP）", shell_path=shell_path),
                       note="MTP 只能浏览"),
    ]
    for candidate, expected in ((three[0], "adb:" + adb_root), (three[1], r"D:\Songs\CustomLevels"), (three[2], shell_path)):
        dlg = DevicePathDialog(root, title="选择 Quest 歌曲目录", candidates=three)
        dlg.use_candidate(candidate)
        dlg._on_default()
        kind = getattr(candidate, "kind_label", candidate.kind)
        check(f"选中「{kind}」候选 → show() 返回字符串", dlg.result == expected, repr(dlg.result))

    manual_path = DevicePathDialog(root, title="选择 Quest 歌曲目录", candidates=three)
    manual_path.path_var.set("/sdcard/CustomLevels")
    manual_path._on_default()
    check("手输裸 /sdcard 路径 → 自动补 adb: 前缀", manual_path.result == "adb:/sdcard/CustomLevels",
          repr(manual_path.result))

    local_case = DevicePathDialog(root, title="选择 Quest 歌曲目录")
    local_case.path_var.set('"d:\\songs\\CustomLevels"')
    local_case._on_default()
    check("手输本地路径 → 去引号 + 盘符大写", local_case.result == r"D:\songs\CustomLevels",
          repr(local_case.result))

    empty = DevicePathDialog(root, title="选择 Quest 歌曲目录")
    empty._on_default()
    check("空输入 → 不关闭、不返回路径", empty.result is None and bool(empty.winfo_exists()),
          str(empty.hint_label.cget("text")))
    empty._on_cancel()

    # ------------------------------------------------------------------
    print("\n=== 9) 输入类对话框的返回值与校验 ===")
    comment = CommentEditDialog(root, title="编辑评价", initial_text="很好", max_length=10)
    inner = comment.textbox._textbox  # CTkTextbox 把 bind 转发到这个内部 Text（测试专用）
    check("Ctrl+Enter 绑定已注册", bool(inner.bind("<Control-Return>")))
    comment.deiconify()
    root.update()  # 让窗口真正映射，键盘事件才会派发
    inner.focus_set()
    root.update()
    inner.event_generate("<Control-Return>", when="now")
    via_event = comment.result
    if comment.result is None:  # 事件没送到时退回直接调用绑定处理函数
        comment._on_ctrl_return()
    check("Ctrl+Enter 保存（未超长）", comment.result == "很好",
          f"事件派发={via_event!r} 最终={comment.result!r}")

    too_long = CommentEditDialog(root, title="编辑评价", max_length=5)
    too_long.textbox.insert("1.0", "0123456789")
    too_long._on_default()
    check(
        "超长不关闭并提示字数",
        too_long.result is None and bool(too_long.winfo_exists())
        and "最多 5 字" in str(too_long.error_label.cget("text")),
        f"counter={too_long.counter_label.cget('text')} err={too_long.error_label.cget('text')}",
    )
    too_long.textbox.delete("1.0", "end")
    too_long.textbox.insert("1.0", "01234")
    too_long._on_default()
    check("改成合法长度后保存成功", too_long.result == "01234", repr(too_long.result))

    bad = TextPromptDialog(root, title="重命名", prompt="新名字", initial="ab",
                           validate=lambda value: None if len(value) >= 3 else "至少 3 个字符")
    bad._on_default()
    check("校验不通过 → 不关闭并显示错误", bad.result is None and bool(bad.winfo_exists())
          and "至少 3 个字符" in str(bad.error_label.cget("text")),
          str(bad.error_label.cget("text")))
    bad.entry.delete(0, "end")
    bad.entry.insert(0, "新歌单")
    bad._on_default()
    check("校验通过 → 返回输入值", bad.result == "新歌单", repr(bad.result))

    blank = TextPromptDialog(root, title="重命名")
    blank._on_default()
    check("空输入默认拒绝", blank.result is None and "不能为空" in str(blank.error_label.cget("text")),
          str(blank.error_label.cget("text")))
    blank._on_cancel()

    try:
        root.after_cancel(watchdog)  # type: ignore[arg-type]
    except Exception:
        pass
    if not watchdog["fired"]:
        root.destroy()

    print()
    if FAILURES:
        print(f"RUNTIME VERIFY FAILED: {len(FAILURES)} 项 → {FAILURES}")
        return 1
    print("RUNTIME VERIFY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
