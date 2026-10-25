"""真机（adb）写/删验证 —— 会**真的**往设备写文件并删除，默认只做演练。

为什么要单独一个脚本、而且**不放进 `run_all.py`**：
这个脚本会在**真实的 Quest** 上新建并删除歌曲文件夹。`run_all.py` 的约定是
"随便跑都安全"，不能掺进会改设备数据的操作。所以：

  * 默认（不加 `--yes`）只打印"将要做什么"，一根手指都不碰设备；
  * 加 `--yes` 才真正执行，且**只碰它自己创建的那几个文件夹**，
    无论成功失败都会在 `finally` 里删干净。

它补的是 HANDOVER 阶段 A 里唯一没法离线验证的部分：
  * A3（复制到设备）—— 离线只能用假 adb 测命令拼装，测不到真机行为；
  * A4（设备端删除）—— 同上，而且删除是破坏性的，必须真机验护栏。

**这个脚本真的抓到过东西**（2026-10-02）：它第一次跑就暴露了 `copy_into` 在
"本地路径含非 ASCII"时会把设备上的文件夹名截断（见 `quest_tool/device_adb.py`
`copy_into` 的长注释）。所以它现在专门覆盖两种歌名：
  1. 纯 ASCII 歌名（本地路径仍含中文 —— 工作区本身就在中文路径下）；
  2. **中文歌名** —— 这条直接盯住上面那个缺陷，改回去就会红。

安全性设计（对着"这是用户真实歌单"来做的）：
  1. 测试歌曲名固定带 `_DSH_TEST` 前缀，一眼能看出是测试产物；
  2. 跑之前先给设备歌单拍快照，跑完比对：原歌曲必须**一首不多一首不少**；
  3. 删除护栏逐个试（`..`、含分隔符、空名…），并确认被拦下时**什么都没删**；
  4. `finally` 兜底清理，哪怕中途抛异常也不会在设备上留下垃圾。

安全性补充（v17.9）：**"没能验证"必须以非 0 退出码收场**。
原先"没有设备 / 不是 Quest / 歌单为空"三条路都是 `return 0` 并打印"[跳过]"，
外层调用者（CI、批处理、另一个 AI）看到退出码 0 会当成"真机验证通过" ——
实测踩过：只插着小米平板时脚本直接 0 退出，看起来像验过了。
现在这几种情况一律 `return 2`，并且判据改成产品自己的 `AdbDevice.is_quest`
（原先只有一句 `"quest" not in model.lower()` 子串匹配，比产品护栏还松）。

用法：
    python tests/e2e_device_real.py          # 演练：只打印计划，不动设备；退出码 0
    python tests/e2e_device_real.py --yes    # 真跑（可逆，跑完自动清理）
        -> 退出码 0 = 全部验证通过；1 = 有断言失败；2 = 什么都没验证成
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from _paths import ROOT, sandbox  # noqa: E402

sys.path.insert(0, str(ROOT))

from quest_tool import adb as adb_mod  # noqa: E402
from quest_tool.device import probe_adb  # noqa: E402
from quest_tool.device_adb import AdbFS  # noqa: E402
from quest_tool.fsapi import FSError, Location  # noqa: E402

#: 要验证的歌名。第二个是中文 —— 专盯"adb push 截断非 ASCII 名字"那个缺陷。
CASES: tuple[tuple[str, str], ...] = (
    ("ASCII 歌名（本地路径仍含中文）", "_DSH_TEST_ascii"),
    ("中文歌名（本地路径也含中文）", "_DSH_TEST_中文歌名"),
)
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def make_local_song(root: Path, song: str) -> Path:
    """造一首最小的合法歌曲（info.dat + 封面 + 音频）。"""
    folder = root / song
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / "info.dat").write_text(
        json.dumps(
            {
                "_version": "2.0.0",
                "_songName": "DSH 真机写入测试",
                "_songAuthorName": "自动化测试",
                "_beatsPerMinute": 123.5,
                "_coverImageFilename": "cover.png",
                "_difficultyBeatmapSets": [
                    {"_difficultyBeatmaps": [{"_difficulty": "Expert"}]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"dsh-test" * 8)
    (folder / "song.egg").write_bytes(b"OggS" + b"\x00" * 512)
    return folder


def find_device_fs():
    """找到 Quest 上可用的 CustomLevels。

    **返回三元组 `(fs, 路径或原因, 设备身份说明)`** —— 失败时 `fs is None`。
    从前这里失败只返回**二元组**，而 `main()` 只解二元组；v17.9 加"身份说明"时
    三条提前返回忘了跟着改，于是 `--yes` 一跑就是
    `ValueError: not enough values to unpack (expected 3, got 2)` ——
    **离线单测没抓到它，因为它把 find_device_fs 整个打桩掉了**，
    只有对着真实设备跑才暴露。已补 `test_find_device_fs` 的返回值形状断言。

    **只认 Quest**：这个脚本会往设备里**写**文件，插着的手机/平板不该被写进去。
    （实测过：只插着小米平板时，adb 也能找到一个 Android/data 下的候选路径。）

    v17.9 改判据：原先这里只做 `"quest" not in model.lower()` 子串匹配，
    比产品自己的护栏还松 —— 产品的 `ensure_device()` 用 getprop 的
    codename/brand 判据（`AdbDevice.is_quest`）。现在复用产品那段逻辑。
    """
    candidates, error, _hint = probe_adb("")
    usable = [c for c in candidates if c.usable and c.location is not None]
    if not usable:
        return None, error or "没有可用设备", ""
    location = usable[0].location
    bridge = adb_mod.AdbBridge(adb_mod.find_adb(""), serial=location.serial)
    try:
        device = bridge.ensure_device()
    except adb_mod.AdbError as exc:
        return None, str(exc), ""

    detail = f"型号={device.model or '未知'} codename={device.codename or '空'}"
    # `ensure_device()` 默认 require_quest=True，能返回就说明它已经是 Quest；
    # 但这里再独立复核一次，避免将来有人改默认值时本脚本静默失去保护。
    if not device.is_quest and "quest" not in (device.model or "").lower():
        return None, (
            f"连着的设备是 {device.model or '未知型号'}（codename={device.codename or '空'}，"
            f"brand={device.brand or '空'}），看起来不是 Quest —— "
            "这个脚本会往设备写文件，拒绝执行"
        ), detail
    return AdbFS(location, bridge=bridge), location.root, detail


def run_song_case(fs: AdbFS, before: list[str], local_root: Path, song: str, label: str) -> None:
    """复制一首歌到设备 → 校验名字与内容 → 删掉 → 确认歌单复原。"""
    print(f"=== A3/A4. {label}：{song} ===")
    local_song = make_local_song(local_root, song)
    on_device = False
    try:
        fs.copy_into(song, local_song)
        on_device = True

        listed = fs.list_song_folders()
        check(f"[{label}] 设备列表里就是这个名字（没被截断）",
              song in listed,
              f"期望 {song!r}；本次新增: {sorted(set(listed) - set(before))}")
        check(f"[{label}] 原歌曲一首没少",
              set(before) <= set(listed), f"少了 {sorted(set(before) - set(listed))[:3]}")
        check(f"[{label}] 只多了这一首",
              len(listed) == len(before) + 1, f"{len(before)} -> {len(listed)}")

        remote_files = sorted(fs.list_files(song))
        check(f"[{label}] 文件齐全", remote_files == ["cover.png", "info.dat", "song.egg"],
              str(remote_files))
        check(f"[{label}] info.dat 字节一致",
              fs.read_file(song, "info.dat") == (local_song / "info.dat").read_bytes())
        check(f"[{label}] info.dat 能解析",
              (fs.read_info_json(song) or {}).get("_songName") == "DSH 真机写入测试")

        fs.delete_folder(song)
        on_device = False
        check(f"[{label}] 删除后歌单复原", sorted(fs.list_song_folders()) == before)
    finally:
        if on_device or fs.local_folder_exists(song):
            print(f"  [清理] 设备上仍有 {song!r}，正在删除…")
            try:
                fs.delete_folder(song)
                print("  [清理] 已删除。")
            except FSError as exc:
                print(f"  [清理失败] 请手动删除设备上的 {song!r}：{exc}")


def main(argv: list[str]) -> int:
    really = "--yes" in argv
    print("=== 真机写/删验证 ===")
    if not really:
        print("  [演练模式] 没有加 --yes，只会打印计划，不会碰设备。")
        for label, song in CASES:
            print(f"  将验证：{label} -> 在设备上新建 `{song}` 再删掉")
        print("  另外会逐个试删除护栏（被拦下时必须什么都没删）。")
        print("  确认无误后加 --yes 真跑：python tests\\e2e_device_real.py --yes")
        return 0

    fs, root, detail = find_device_fs()
    if fs is None:
        print(f"  [未验证] 没有可用的 Quest（{root}）。插上 Quest 并解锁后再跑。")
        print("  退出码 2 = 本次什么都没验证 —— 不要把它当成“真机验证通过”。")
        return 2

    print(f"  设备路径: {root}")
    print(f"  设备身份: {detail}")
    before = sorted(fs.list_song_folders())
    free_before = fs.free_space_bytes()
    print(f"  跑之前设备上共 {len(before)} 首，可用空间 {free_before}")
    if not before:
        print("  [未验证] 设备歌单为空 —— 存储可能还没解锁（戴上头显解锁后重试）。")
        print("  退出码 2 = 本次什么都没验证。")
        return 2

    local_root = sandbox("e2e_device_real")
    try:
        for label, song in CASES:
            run_song_case(fs, before, local_root, song, label)

        print("=== A4-a. 删除护栏（被拦下时必须什么都没删） ===")
        probe_song = CASES[0][1]
        make_local_song(local_root, probe_song)
        fs.copy_into(probe_song, local_root / probe_song)
        try:
            for bad in ("../Song", "a/b", "", ".", "..", "Song\\A", probe_song + "/info.dat"):
                try:
                    fs.delete_folder(bad)
                    check(f"非法名被拦截: {bad!r}", False, "竟然没有拦截！")
                except FSError:
                    check(f"非法名被拦截: {bad!r}", True)
            check("护栏测试后探针歌曲仍在（没有被误删）", fs.local_folder_exists(probe_song))
            try:
                fs.delete_folder("__definitely_not_a_song__")
                check("删除不存在的歌曲会报错", False, "竟然静默成功")
            except FSError:
                check("删除不存在的歌曲会报错", True)
        finally:
            if fs.local_folder_exists(probe_song):
                fs.delete_folder(probe_song)

        after = sorted(fs.list_song_folders())
        check("全部跑完后歌单与跑之前完全一致", after == before,
              f"{len(before)} -> {len(after)}")
        free_after = fs.free_space_bytes()
        check("可用空间基本回到原值",
              free_before is not None and free_after is not None
              and abs(free_after - free_before) < 8 * 1024 * 1024,
              f"{free_before} -> {free_after}")
        print(f"  结论：设备上 {len(after)} 首，与跑之前一致。")
    finally:
        shutil.rmtree(local_root, ignore_errors=True)

    print()
    if FAILURES:
        print(f"结果：{len(FAILURES)} 项失败 -> {FAILURES}")
        return 1
    print("结果：真机写/删验证全部通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
