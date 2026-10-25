"""Quest歌单对比工具 —— 启动脚本（双击即可运行）。

直接运行本文件即可：`python main.py`
需要图形界面依赖：pip install customtkinter pillow

## 想做成"双击就能开"？

- Windows 上双击本文件**不会**运行它（.py 默认是编辑/无关联），请用仓库根目录的
  `启动.bat`（推荐，会自动找 Python、失败时停住窗口）、`启动.vbs`（无黑框）、
  或 `启动.pyw`（无黑框，已经能双击）。
- 详见 `docs/07_怎么打开软件.md`。
"""

from __future__ import annotations

import os
import sys

# 让脚本无论从哪个目录启动都能 import quest_tool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from quest_tool.__main__ import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
