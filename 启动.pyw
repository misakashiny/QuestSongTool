"""Quest歌单对比工具 —— 双击启动（.pyw 没有控制台窗口）。

`.pyw` 在 Windows 上默认关联到 pythonw.exe，所以**可以直接双击运行**。
这与改 `main.py` 不同：`.py` 在很多机器上关联到编辑器/无关联，双击不开程序。

找不到 Python 环境（依赖缺失、解释器异常）时用弹窗提示，
因为 .pyw 没有控制台，异常只会静默消失。
"""

from __future__ import annotations

import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _alert(message: str, detail: str = "") -> None:
    text = message + (("\n\n" + detail) if detail else "")
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "Quest歌单对比工具", 0x10)
    except Exception:
        sys.stderr.write(text + "\n")


def main() -> int:
    try:
        from quest_tool.__main__ import main as run
    except ImportError as exc:
        _alert(
            "缺少运行依赖，程序无法启动。\n\n"
            "请在命令行里执行一次：\n    pip install customtkinter pillow\n\n"
            "或者直接双击同目录下的 启动.bat（它会自动安装）。",
            str(exc),
        )
        return 1
    try:
        return run()
    except Exception:
        _alert("程序启动时发生意外错误。", traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
