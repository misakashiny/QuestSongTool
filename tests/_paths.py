"""测试与工具脚本的统一路径入口。

为什么要有这个文件：测试脚本原本散在 `tests/` 里，各自用
`Path(__file__).resolve().parent.parent` 猜工作区根目录，一旦挪动目录就会静默写错位置。
现在所有脚本统一从这里取 `ROOT` / `TESTS` / `SANDBOX`，与脚本放在哪一层无关。

约定：
* 需要写临时文件时用 `sandbox("名字")`，它会把内容放在 `tests/.tmp/<名字>` 下，
  并在同名目录已存在时先清空 —— 保持工作区整洁，也便于随时整体删除。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

#: 仓库根目录（tests/ 的上一级）
ROOT: Path = Path(__file__).resolve().parent.parent
#: 测试目录自身
TESTS: Path = Path(__file__).resolve().parent
#: 临时沙箱根目录（已在 .gitignore 中忽略）
TMP: Path = TESTS / ".tmp"
#: 截图输出目录
SHOTS: Path = TESTS / ".shots"


def ensure_workspace_importable() -> None:
    """把仓库根目录加入 sys.path，让测试可以 `import quest_tool`。"""
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def sandbox(name: str, *, clean: bool = True) -> Path:
    """取一个干净的临时沙箱目录 `tests/.tmp/<name>`。"""
    path = TMP / name
    if clean and path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


ensure_workspace_importable()
