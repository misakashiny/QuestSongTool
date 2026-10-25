"""真机脚本护栏的**离线**自检（不需要 Quest）。

为什么需要这一组：`e2e_device_real.py` 会往设备上写文件，它的保护逻辑
（"这台到底是不是 Quest" / "没验证成要报什么"）以前**没有任何测试覆盖** ——
只有插上真设备才知道对不对。而恰恰是"插着非 Quest 设备"这个场景，
原先的判据比产品自己的护栏还松（只看 `model` 里有没有 "quest" 字样）。

本组用假 adb 把三种情况都造出来，断言：
  1. 非 Quest 设备（型号中带 quest 的**例外**也要拦住）→ 拒绝执行；
  2. Quest 设备（codename 命中，但 model 不含 quest）→ 放行；
  3. "没能验证"必须是非 0 退出码（退出码 2），不能伪装成通过。

判据来源必须是**产品自己的** `AdbDevice.is_quest`（走 getprop 的
codename/brand），不是测试脚本自己再写一套。
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from _paths import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT))

from quest_tool.adb import AdbDevice, _matches_quest  # noqa: E402
from quest_tool.constants import QUEST_DEVICE_CODENAMES  # noqa: E402

# 让 `tests/e2e_device_real.py` 可以被当模块导入（它内部会用 `_paths`）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_device_real as edr  # noqa: E402

CHECKS = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


# ======================================================================================
# 1. 判据本身：产品逻辑 vs 原先的松判据
# ======================================================================================
def test_judgement() -> None:
    print("=== 1. Quest 判据（产品 AdbDevice.is_quest）===")

    # 真 Quest 各代：model 可能**不含** quest 字样，但 codename 命中 —— 必须放行
    for code in QUEST_DEVICE_CODENAMES[:4]:
        device = AdbDevice(serial="X", state="device", model="", codename=code, brand="oculus")
        check(f"codename={code} 判为 Quest", device.is_quest)

    # Quest 3 真机形态：model 是 "Quest 3"
    quest3 = AdbDevice(serial="X", state="device", model="Quest 3", codename="eureka", brand="oculus")
    check("Quest 3 判为 Quest", quest3.is_quest)

    # 实测过的真实非 Quest 设备：小米平板 M2105K81AC
    xiaomi = AdbDevice(serial="X", state="device", model="M2105K81AC", codename="elish", brand="Xiaomi")
    check("小米平板判为**不是** Quest", not xiaomi.is_quest)

    # 关键回归：**没探测过身份**的未知设备绝不能被当成 Quest
    unknown = AdbDevice(serial="X", state="device", model="Some Android Phone")
    check("没探测过身份的未知设备判为**不是** Quest", not unknown.is_quest)

    # 加固的**实际**收益：原先的判据只看 `model` 子串，而加固后判据来自
    # getprop（codename/brand）。注意 `_matches_quest` 的 brand 兜底**有意**会认
    # "quest" 字样（有些系统把 codename 抹掉了），所以这里不能拿"带 quest 字样"
    # 当反例 —— 真正被挡住的是"型号里带 quest、但 getprop 三项都不像 Quest"。
    #
    # 三个具体收益，逐个断言：
    # (a) codename 命中就放行 —— 而 Quest 的 model 常常是空的，松判据会误**拦**
    codename_only = AdbDevice(serial="X", state="device", model="", codename="eureka", brand="oculus")
    check("加固收益：model 为空但 codename=eureka 能放行（松判据会误拦真 Quest）",
          codename_only.is_quest, f"model={codename_only.model!r}")

    # (b) 未探测身份的设备不再被当成 Quest（松判据看 model，严判据要求 getprop 有据）
    unidentified = AdbDevice(serial="X", state="device", model="", codename="", brand="")
    check("加固收益：完全没探测出身份时不放行", not unidentified.is_quest)

    # (c) 实测过的真实手机/平板被正确排除
    samsung = AdbDevice(serial="X", state="device", model="SM-G991B", codename="o1s", brand="samsung")
    check("加固收益：真实手机（samsung/o1s）被排除", not samsung.is_quest)

    # 空值不能误判
    check("全空判为不是 Quest", not _matches_quest("", "", ""))


# ======================================================================================
# 2. find_device_fs()：非 Quest 必须连理由一起拒绝
# ======================================================================================
class _FakeCandidate:
    def __init__(self, root: str) -> None:
        self.usable = True
        self.location = type("Loc", (), {"serial": "FAKESERIAL", "root": root})()


def _run_find_device_fs(device: AdbDevice, root: str = "/sdcard/CustomLevels"):
    """把 find_device_fs 里的依赖全部打桩，只测它的判断分支。

    注意作用域：`e2e_device_real` 里是 `from quest_tool import adb as adb_mod`
    **和** `from quest_tool.device import probe_adb` —— 两者都绑在
    `e2e_device_real` 自己的模块命名空间上，所以要 patch 的是它，不是 `quest_tool.*`。
    """
    bridge_holder: dict[str, AdbDevice] = {"device": device}
    saved = (edr.probe_adb, edr.adb_mod, edr.AdbFS)

    class FakeBridge:
        def __init__(self, *a, **kw) -> None:
            pass

        def ensure_device(self, *a, **kw) -> AdbDevice:
            return bridge_holder["device"]

    class FakeAdbError(Exception):
        pass

    edr.probe_adb = lambda _: ([_FakeCandidate(root)], "", "")
    # 用真实的 quest_tool.adb 模块打底（这样 AdbError / find_adb 都是真的），
    # 只把 AdbBridge 换成假的 —— 比整体伪造一个模块更不容易失真。
    edr.adb_mod = type(
        "PatchedAdbMod",
        (),
        {
            "AdbBridge": FakeBridge,
            "find_adb": lambda _: "adb",
            "AdbError": FakeAdbError,
            "AdbDevice": AdbDevice,
        },
    )
    edr.AdbFS = lambda location, bridge=None: "FS"
    try:
        return edr.find_device_fs()
    finally:
        edr.probe_adb, edr.adb_mod, edr.AdbFS = saved


def test_find_device_fs() -> None:
    print("\n=== 2. find_device_fs() 的分支 ===")

    xiaomi = AdbDevice(serial="X", state="device", model="M2105K81AC", codename="elish", brand="Xiaomi")
    result = _run_find_device_fs(xiaomi)
    check("非 Quest：拒绝执行（fs 为 None）", result[0] is None)
    check("非 Quest：理由里点名了设备型号", "M2105K81AC" in str(result[1]), str(result[1]))

    quest3 = AdbDevice(serial="X", state="device", model="Quest 3", codename="eureka", brand="oculus")
    result = _run_find_device_fs(quest3)
    check("Quest：放行", result[0] is not None, f"reason={result[1]}")
    check("Quest：返回了身份说明（便于写进验证记录）",
          len(result) > 2 and "eureka" in str(result[2]), str(result))

    # 返回值形状必须是**每一路都一致**的三元组。
    # 这里踩过真坑：v17.9 加第三个元素时，三条提前返回忘了改，
    # 于是 `--yes` 一跑就 `ValueError: not enough values to unpack`。
    # 上面几个断言用 result[0]/[1] 取值，**形状错了也照样通过** ——
    # 所以必须单独断言 len()，否则这类 bug 又会被漏掉。
    for label, dev in (("非 Quest", xiaomi), ("Quest", quest3)):
        check(f"{label}：返回值是三元组（形状一致）",
              len(_run_find_device_fs(dev)) == 3,
              f"实际长度 {len(_run_find_device_fs(dev))}")

    # 没有可用设备 / 探测抛异常这两条路也要是三元的
    saved = edr.probe_adb
    edr.probe_adb = lambda _: ([], "没有可用设备", "")
    try:
        check("无设备：返回三元组且 fs 为 None",
              len(edr.find_device_fs()) == 3 and edr.find_device_fs()[0] is None)
    finally:
        edr.probe_adb = saved

    # (d) 不替换 find_device_fs/AdbBridge 的真实控制流，只封闭底层传输。
    # 旧测试依赖现场插着小米平板，连接真实Quest会误报且访问设备。
    calls = []
    def fake_run(_bridge, args, **_kwargs):
        calls.append(args)
        if args == ["devices", "-l"]:
            return "List of devices attached\nFAKESERIAL device model:M2105K81AC\n"
        if args[0] == "shell" and args[1].startswith("getprop "):
            return "elish\nXiaomi\nXiaomi\nM2105K81AC\nelish\n"
        raise AssertionError(f"Unexpected offline command: {args!r}")
    with patch.object(edr, "probe_adb", lambda _: ([_FakeCandidate('/sdcard/CustomLevels')], '', '')), \
            patch.object(edr.adb_mod, "find_adb", lambda _: sys.executable), \
            patch.object(edr.adb_mod.AdbBridge, "run", fake_run):
        real = edr.find_device_fs()
        check("真实调用 find_device_fs 返回三元组（main 能解包）",
              isinstance(real, tuple) and len(real) == 3,
              f"实际 {type(real).__name__} 长度 {len(real) if isinstance(real, tuple) else 'N/A'}")
        check("模拟传输实际调用：非 Quest 设备时 fs 为 None",
              real[0] is None, f"reason={str(real[1])[:80]}")
        check("模拟传输覆盖真实设备枚举和属性解析", len(calls) == 2, str(calls))


# ======================================================================================
# 3. 退出码语义：“没能验证” 必须是 2，不能是 0
# ======================================================================================
def test_exit_codes() -> None:
    print("\n=== 3. 退出码语义 ===")

    # (a) 不加 --yes：演练模式，应当 0 退出且不碰设备
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = edr.main([])
    check("演练模式（无 --yes）退出码 0", code == 0, f"得到 {code}")

    # (b) 加 --yes 但没有 Quest：必须 2，绝不能是 0
    saved = edr.find_device_fs
    edr.find_device_fs = lambda: (None, "设备是 M2105K81AC，看起来不是 Quest", "")
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = edr.main(["--yes"])
        output = buf.getvalue()
    finally:
        edr.find_device_fs = saved
    check("没有 Quest 时退出码是 2（不是 0）", code == 2, f"得到 {code}")
    check("输出里明确写了“未验证”", "未验证" in output, output.strip()[:120])

    # (c) 空歌单：同样是 2
    class _EmptyFS:
        def list_song_folders(self):
            return []

        def free_space_bytes(self):
            return 0

    saved = edr.find_device_fs
    edr.find_device_fs = lambda: (_EmptyFS(), "/sdcard/CustomLevels", "型号=Quest 3 codename=eureka")
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = edr.main(["--yes"])
    finally:
        edr.find_device_fs = saved
    check("设备歌单为空时退出码是 2", code == 2, f"得到 {code}")


def main() -> int:
    print("=" * 70)
    print("真机脚本护栏自检（离线，不碰任何设备）")
    print("=" * 70)
    # 整组禁止真实子进程，未来漏掉模拟时必须失败，不能悄悄访问设备。
    with patch("subprocess.Popen", side_effect=AssertionError("Offline guard test forbids real processes")):
        test_judgement()
        test_find_device_fs()
        test_exit_codes()
    print("\n" + "=" * 70)
    if FAILURES:
        print(f"结果：{CHECKS - len(FAILURES)}/{CHECKS} 项通过，失败 {len(FAILURES)} 项 -> {FAILURES}")
        return 1
    print(f"结果：{CHECKS}/{CHECKS} 项通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
