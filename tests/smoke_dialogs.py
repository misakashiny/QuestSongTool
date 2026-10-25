# -*- coding: utf-8 -*-
"""对话框冒烟测试：只用「隐藏的父窗口」构造全部对话框，不 show()、不进 mainloop。

为什么只构造不 show()：
``show()`` 内部是 ``wait_visibility()`` → ``grab_set()`` → ``wait_window()``，
在无人点击的自动化环境里会一直等下去。而构造阶段是"无副作用"的（不抓输入、
不起后台线程、不阻塞），所以"构造 → update_idletasks → destroy"足以覆盖
绝大多数初始化缺陷：控件参数写错、grid/pack 混用、配色不支持、抽象方法没实现等。

运行：``python tests/smoke_dialogs.py``
"""

from __future__ import annotations

import traceback

import _paths  # noqa: F401  （导入它即完成 sys.path 设置，名字本身用不到）

import customtkinter as ctk  # noqa: E402

from quest_tool.device import QuestCandidate  # noqa: E402
from quest_tool.fsapi import Location  # noqa: E402
from quest_tool.ui_devicepicker import DeviceFolderPicker  # noqa: E402
from quest_tool.ui_dialogs import (  # noqa: E402
    CommentEditDialog,
    ConfirmDialog,
    DevicePathDialog,
    InfoDialog,
    ModalDialog,
    ProgressDialog,
    TextPromptDialog,
)

BUILT: list[str] = []
FAILED: list[tuple[str, str]] = []


class _DemoModal(ModalDialog):
    """ModalDialog 是抽象基类（_create_body 未实现），用最小子类验证窗口壳本身。"""

    def __init__(self, master):
        super().__init__(master, "示例对话框", size=(420, 240))

    def _create_body(self, parent: ctk.CTkFrame) -> None:
        ctk.CTkLabel(parent, text="body", font=(self.font, 14)).pack()


def _sample_candidates() -> list[QuestCandidate]:
    """模拟主程序 scan_quest() 的候选（三种 kind 都覆盖）。"""
    return [
        QuestCandidate(
            kind="adb",
            display="/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels",
            usable=True,
            location=Location(
                kind="adb",
                root="/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels",
                label="Quest 3 · ADB",
                serial="1WMHH000X0000A",
            ),
            note="通过 adb 连接，读写最快",
        ),
        QuestCandidate(
            kind="drive",
            display=r"D:\Songs\CustomLevels",
            usable=True,
            location=Location(kind="local", root=r"D:\Songs\CustomLevels", label="本地"),
            note="以盘符方式挂载",
        ),
        QuestCandidate(
            kind="mtp",
            display="Quest 3\\内部共享存储空间\\Android\\data\\com.beatgames.beatsaber\\files\\CustomLevels",
            usable=False,
            location=Location(
                kind="mtp",
                root=r"::{GUID}\Internal shared storage\Android",
                label="Quest 3（MTP）",
                shell_path=r"::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\Quest 3\Internal shared storage",
            ),
            note="MTP 模式：可浏览，读写需要 adb",
        ),
        QuestCandidate(kind="adb", display="/sdcard/.../CustomLevels", usable=False, exists=False,
                       note="设备已连接，但未找到 CustomLevels"),
    ]


def check(name: str, factory) -> None:
    """构造 → 排一次版 → 销毁；任何异常都算失败（不是崩溃退出）。"""
    try:
        dialog = factory()
        dialog.update_idletasks()
        dialog.destroy()
    except Exception:
        FAILED.append((name, traceback.format_exc()))
        print(f"  [FAIL] {name}")
        return
    BUILT.append(name)
    print(f"  [ ok ] {name}")


def main() -> int:
    root = ctk.CTk()
    root.withdraw()  # 隐藏的父窗口：所有对话框都必须能在这种情况下构造
    print(f"ctk {ctk.__version__} / Tk {root.tk.call('info', 'patchlevel')} / parent={root}")

    print("\n构造对话框（只构造，不 show、不进 mainloop）：")
    check("ModalDialog(示例子类)", lambda: _DemoModal(root))
    check(
        "ConfirmDialog(普通)",
        lambda: ConfirmDialog(root, title="确认操作", message="确定要复制吗？", detail="共 12 首歌曲"),
    )
    check(
        "ConfirmDialog(危险+下次不再询问)",
        lambda: ConfirmDialog(
            root,
            title="确认删除",
            message="确定要删除选中的 3 首歌吗？",
            detail="删除后无法恢复",
            confirm_text="确定删除",
            cancel_text="取消",
            confirm_is_danger=True,
            show_again_checkbox=True,
            checkbox_text="下次不再询问",
        ),
    )
    check("InfoDialog(短文本)", lambda: InfoDialog(root, title="提示", message="已完成。"))
    check(
        "InfoDialog(长文本→只读文本框)",
        lambda: InfoDialog(
            root,
            title="详细信息",
            message="\n".join(f"第 {i} 行：这是一段较长的说明文字。" for i in range(30)),
            detail="复制按钮会把全部内容放进剪贴板",
        ),
    )
    check(
        "CommentEditDialog",
        lambda: CommentEditDialog(root, title="编辑评价", initial_text="这首谱面手感很好", max_length=500),
    )
    check(
        "TextPromptDialog(带校验)",
        lambda: TextPromptDialog(
            root,
            title="重命名",
            prompt="请输入新的歌单名称",
            initial="我的歌单",
            placeholder="例如：练习曲",
            validate=lambda value: None if len(value) <= 20 else "最多 20 个字符",
        ),
    )
    check(
        "DevicePathDialog(带候选)",
        lambda: DevicePathDialog(
            root,
            title="选择 Quest 歌曲目录",
            candidates=_sample_candidates(),
            adb_path="",
        ),
    )
    check("DevicePathDialog(无候选)", lambda: DevicePathDialog(root, title="选择 Quest 歌曲目录"))
    check(
        "ProgressDialog",
        lambda: ProgressDialog(root, title="正在复制", message="正在复制 12 首歌曲…",
                               cancel_callback=lambda: None),
    )
    check(
        "DeviceFolderPicker",
        lambda: DeviceFolderPicker(root, adb_path="", candidates=_sample_candidates()),
    )
    check("DeviceFolderPicker(无候选)", lambda: DeviceFolderPicker(root))

    # 返回值契约（不 show() 也能验证"取消"的默认值）
    print("\n取消默认值：")
    checks: list[tuple[str, object]] = []
    from quest_tool.ui_devicepicker import PickedPath  # noqa: PLC0415
    checks.append(("ConfirmDialog.result is False", ConfirmDialog(root, "t", "m").result is False))
    checks.append(("InfoDialog.result is None", InfoDialog(root, "t", "m").result is None))
    checks.append(("CommentEditDialog.result is None", CommentEditDialog(root).result is None))
    checks.append(("TextPromptDialog.result is None", TextPromptDialog(root).result is None))
    checks.append(("DevicePathDialog.result is None", DevicePathDialog(root).result is None))
    checks.append(("ProgressDialog.result is None", ProgressDialog(root).result is None))
    checks.append(("DeviceFolderPicker.result is None", DeviceFolderPicker(root).result is None))
    checks.append(("PickedPath.display(adb)", PickedPath("adb", "/sdcard/x", "l", True).display == "adb:/sdcard/x"))
    for label, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            FAILED.append((label, "断言失败"))

    # 勾选框联动（纯内存操作，不需要窗口显示）
    again = ConfirmDialog(root, "t", "m", show_again_checkbox=True)
    before = again.dont_ask_again
    again.again_var.set(True)
    after = again.dont_ask_again
    again.destroy()
    ok = before is False and after is True
    print(f"  [{'ok' if ok else 'FAIL'}] dont_ask_again: 未勾选={before} 勾选后={after}")
    if not ok:
        FAILED.append(("dont_ask_again", f"{before} / {after}"))

    root.update_idletasks()
    root.destroy()

    print()
    if FAILED:
        for name, tb in FAILED:
            print(f"---- {name} ----\n{tb}")
        print(f"DIALOG SMOKE FAILED: {len(FAILED)} 项")
        return 1
    print("DIALOG SMOKE OK")
    print("已构造的对话框：")
    for name in BUILT:
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
