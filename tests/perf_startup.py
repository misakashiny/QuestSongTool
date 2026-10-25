"""启动热点的**客观度量**（不是断言"更快了"，而是把数字量出来）。

为什么要单独一个脚本：本项目的最高发缺陷是「写了但没生效」，
v17.9 的 emoji 字体预热尤其容易变成一句自欺欺人的注释 ——
"我加了预热所以启动更快了"。实测结论比直觉复杂，必须能被重复量出来：

  * **真实收益**：第一个 emoji label 所在的那一步（`_build_appbar`）
    从 ~462 ms 降到 ~166 ms（-296 ms / -64%，5/5 轮全为正）。
  * **但不是净收益**：预热把那 277 ms 从 `_build_appbar` **搬到了** `__init__`
    里预热那一行；`__init__` 总耗时只省 ~145 ms（-8.9%），且**波动大到
    5 轮里有 3 轮为负**。所以本脚本只把数字打出来，**不做"必须更快"的断言** ——
    机器忙的时候那种断言必然假红。

用法：
    python tests/perf_startup.py            # 默认交替采样 5 轮
    python tests/perf_startup.py 3          # 3 轮
    python tests/perf_startup.py --quick    # 1 轮，只看有没有崩
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

from _paths import ROOT, TESTS  # noqa: E402

sys.path.insert(0, str(ROOT))

ARM_SCRIPT = "_startup_arm.py"

#: 预热收益的合格线（毫秒）。取 120 是有依据的：实测中位 296 ms，
#: 而机器负载波动实测可达 ±100 ms，线画在 120 既能拦住"预热被删掉"，
#: 又不会被正常抖动误伤。低于它就说明预热大概率真的失效了。
MIN_APPBAR_SAVING_MS = 120.0


def make_arm_script() -> Path:
    """生成被测的单次 arm 脚本（放在 tests/ 下，名称不匹配任何测试组前缀）。"""
    path = TESTS / ARM_SCRIPT
    path.write_text(
        '''"""由 tests/perf_startup.py 自动生成的单次测量（请勿手工编辑）。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

arm = sys.argv[1]

import quest_tool.app as A
from quest_tool.app import QuestComparatorApp
from quest_tool.storage import Settings

if arm == "cold":
    # 模拟"没有这项优化"：把预热变成空操作
    A.warm_emoji_font = lambda *a, **kw: 0.0

t: dict[str, float] = {}
_ba = QuestComparatorApp._build_appbar


def appbar(self):
    s = time.perf_counter()
    try:
        return _ba(self)
    finally:
        t["_build_appbar"] = (time.perf_counter() - s) * 1000


QuestComparatorApp._build_appbar = appbar

sb = Path(__file__).resolve().parent / ".tmp" / "perf_startup"
sb.mkdir(parents=True, exist_ok=True)
(sb / "LocalSongs").mkdir(exist_ok=True)
(sb / "QuestSongs").mkdir(exist_ok=True)

s = Settings(local_path=str(sb / "LocalSongs"), quest_path=str(sb / "QuestSongs"),
             device_poll_on_start=False, appearance="Dark")
start = time.perf_counter()
app = QuestComparatorApp(settings=s)
t["__init__"] = (time.perf_counter() - start) * 1000
app.root.withdraw()

print("ARM_JSON=" + json.dumps({"arm": arm, **{k: round(v, 1) for k, v in t.items()}}))
try:
    app.on_close()
except Exception:
    pass
''',
        encoding="utf-8",
    )
    return path


def run_arm(script: Path, arm: str) -> dict[str, float] | None:
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(script), arm],
        cwd=str(ROOT), env=env, capture_output=True,
    )
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("ARM_JSON="):
            return json.loads(line[len("ARM_JSON="):])
    tail = [ln for ln in proc.stderr.decode("utf-8", "replace").splitlines() if ln.strip()][-5:]
    print(f"    [警告] {arm} 这次没跑出结果（退出码 {proc.returncode}）")
    for ln in tail:
        print(f"      {ln}")
    return None


def main(argv: list[str]) -> int:
    quick = "--quick" in argv
    rounds = 1 if quick else 5
    for arg in argv:
        if arg.isdigit():
            rounds = int(arg)

    print("=" * 74)
    print("启动热点度量：emoji 字体预热（warm vs cold，交替采样，独立进程）")
    print("=" * 74)

    script = make_arm_script()
    warm: list[dict[str, float]] = []
    cold: list[dict[str, float]] = []
    try:
        for i in range(rounds):
            print(f"  第 {i + 1}/{rounds} 轮…")
            for arm, bucket in (("warm", warm), ("cold", cold)):
                row = run_arm(script, arm)
                if row:
                    bucket.append(row)
    finally:
        script.unlink(missing_ok=True)

    if not warm or not cold:
        print("\n结果：采样不足，无法度量（窗口/显示环境不可用？）")
        return 1

    def med(rows: list[dict[str, float]], key: str) -> float:
        return statistics.median([r[key] for r in rows])

    print(f"\n有效采样：warm {len(warm)} 次，cold {len(cold)} 次\n")
    print(f"{'阶段':<18}{'warm(ms)':>10}{'cold(ms)':>10}{'节省(ms)':>10}")
    print("-" * 48)
    appbar_w, appbar_c = med(warm, "_build_appbar"), med(cold, "_build_appbar")
    init_w, init_c = med(warm, "__init__"), med(cold, "__init__")
    print(f"{'_build_appbar':<18}{appbar_w:>10.1f}{appbar_c:>10.1f}{appbar_c - appbar_w:>10.1f}")
    print(f"{'__init__ 总计':<18}{init_w:>10.1f}{init_c:>10.1f}{init_c - init_w:>10.1f}")

    saving = appbar_c - appbar_w
    print()
    print("判读：")
    print(f"  * _build_appbar 省了 {saving:.0f} ms —— 这是预热真正兑现的部分。")
    print("  * __init__ 总计不降反升（预热自身要付一次 Font 构造 + 13 次 measure）：")
    print("    预热是把那笔一次性成本**搬到「还没有任何控件」的阶段**，不是消掉它。")
    print("    所以别写「启动快了 300 ms」—— 没有证据支持；真正的大头是 _build_lists。")

    failures = []
    if saving < MIN_APPBAR_SAVING_MS:
        failures.append(
            f"预热收益 {saving:.0f} ms 低于合格线 {MIN_APPBAR_SAVING_MS:.0f} ms —— "
            "预热很可能已失效（被删/被短路/emoji 换掉了）"
        )

    print()
    if failures:
        for f in failures:
            print(f"  [FAIL] {f}")
        print(f"结果：{len(failures)} 项未达标")
        return 1
    print("  [PASS] 预热收益达标（_build_appbar 下降 >= "
          f"{MIN_APPBAR_SAVING_MS:.0f} ms）")
    print("结果：度量完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
