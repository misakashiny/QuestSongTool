"""用"假 adb"完整验证 AdbFS（无需真机）。

覆盖：
* list_song_folders / list_files / stat_mtime / read_info_json
* scan_metadata 的批量路径：find -printf 拿 mtime + tar 单流拿内容
* copy_into（push）/ copy_out（pull，含 staging 改名）
* delete_folder 的护栏与真实删除
* 路径转义（含空格、中文、单引号）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from _paths import ROOT, sandbox  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

FAKE_DIR = Path(__file__).resolve().parent / "fixtures" / "fakeadb"
FAKE_ADB = FAKE_DIR / "adb.bat"
DEVICE = FAKE_DIR / "device"
REMOTE_ROOT = "/sdcard/ModData/com.beatgames.beatsaber/Mods/SongCore/CustomLevels"

os.environ["FAKE_DEVICE_ROOT"] = str(DEVICE)

from quest_tool.adb import (  # noqa: E402
    _BULK_COMMAND_BUDGET,
    AdbBridge,
    _chunk_by_command_budget,
    find_adb,
    parse_adb_devices,
)
from quest_tool.constants import BACKUP_SUFFIX  # noqa: E402
from quest_tool.device_adb import AdbFS  # noqa: E402
from quest_tool.device import probe_adb  # noqa: E402
from quest_tool.fsapi import FSError, FSUnavailable, Location, local_file_stats  # noqa: E402
from quest_tool.storage import data_dir  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def device_path(*parts: str) -> Path:
    return DEVICE.joinpath(*REMOTE_ROOT.strip("/").split("/")[1:], *parts)


def make_song(name: str, title: str, bpm: float) -> None:
    folder = device_path(name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "info.dat").write_text(
        json.dumps(
            {
                "_version": "2.0.0",
                "_songName": title,
                "_songAuthorName": "设备作者",
                "_beatsPerMinute": bpm,
                "_coverImageFilename": "cover.png",
                "_difficultyBeatmapSets": [
                    {"_difficultyBeatmaps": [{"_difficulty": "Expert"}, {"_difficulty": "Normal"}]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    (folder / "song.egg").write_bytes(b"OGG" + b"1" * 512)


def main() -> int:
    print("=== 0. 准备假设备 ===")
    shutil.rmtree(DEVICE, ignore_errors=True)
    device_path().mkdir(parents=True, exist_ok=True)
    make_song("Song A", "设备歌曲A", 128)
    make_song("Song B", "设备歌曲B", 95.5)
    make_song("中文 名字 '特殊'", "特殊字符测试", 180)
    (device_path("Song C")).mkdir(exist_ok=True)  # 无 info.dat
    check("假设备目录已创建", device_path().is_dir(), str(device_path()))

    print("=== 1. adb 桥基础 ===")
    check("adb.bat 存在", FAKE_ADB.is_file(), str(FAKE_ADB))
    bridge = AdbBridge(str(FAKE_ADB))
    check("bridge.available", bridge.available)
    devices = bridge.list_devices()
    check("解析到设备", len(devices) == 1 and devices[0].usable, str(devices))
    check("ensure_device 通过", bridge.ensure_device().serial == "FAKEQUEST1234")
    check("is_dir 真", bridge.is_dir(REMOTE_ROOT))
    check("is_dir 假", not bridge.is_dir(REMOTE_ROOT + "/不存在"))
    check("exists 文件", bridge.exists(REMOTE_ROOT + "/Song A/info.dat"))
    check("mtime > 0", bridge.mtime(REMOTE_ROOT + "/Song A/info.dat") > 0)
    check("free_space 解析", (bridge.free_space(REMOTE_ROOT) or 0) == 40000000 * 1024)

    print("=== 2. AdbFS 读取 ===")
    location = Location(kind="adb", root=REMOTE_ROOT, label="假 Quest", serial="FAKEQUEST1234")
    fs = AdbFS(location, bridge=bridge)
    check("available", fs.available())
    folders = fs.list_song_folders()
    check("列出 4 个目录", sorted(folders) == sorted(["Song A", "Song B", "Song C", "中文 名字 '特殊'"]), str(folders))
    check("list_files", set(fs.list_files("Song A")) == {"info.dat", "cover.png", "song.egg"}, str(fs.list_files("Song A")))
    check("read_info_json", (fs.read_info_json("Song A") or {}).get("_songName") == "设备歌曲A")
    check("无 info.dat 返回 None", fs.read_info_json("Song C") is None)
    check("封面名探测", fs.cover_name("Song A") == "cover.png", fs.cover_name("Song A"))
    cover = fs.read_cover_bytes("Song A")
    check("封面字节", bool(cover) and cover[:8] == b"\x89PNG\r\n\x1a\n")
    check("特殊字符目录可读", (fs.read_info_json("中文 名字 '特殊'") or {}).get("_songName") == "特殊字符测试")

    print("=== 3. 批量元数据（性能关键路径） ===")
    metas = fs.scan_metadata(folders)
    check("解析出 4 首", len(metas) == 4, str(list(metas)))
    check("标题正确（走 tar 批量流）", metas["Song A"].title == "设备歌曲A", metas["Song A"].title)
    check("mtime 来自 find -printf", metas["Song A"].mtime > 0, str(metas["Song A"].mtime))
    check("BPM", metas["Song B"].display_bpm == "95.5", metas["Song B"].display_bpm)
    check("难度归一化", metas["Song A"].difficulties == ("Normal", "Expert"), str(metas["Song A"].difficulties))
    check("无 info.dat 的歌曲有占位", metas["Song C"].folder_name == "Song C")

    # 性能回归护栏：Beat Saber 是"一首歌一个文件夹"，所以这几首歌各在不同目录。
    # 批量读必须**只发一次** adb 调用；v14.0 按目录分组，这里会是 4 次，
    # 上千首就是上千次往返（真机 Quest 3 实测 78 ms/首 → 整库 18 秒，
    # 改成单次 tar 后 1.4 ms/首 → 0.3 秒）。
    bulk_entries = [
        (f"{REMOTE_ROOT}/{name}/info.dat", name)
        for name in folders
        if (device_path(name) / "info.dat").is_file()
    ]
    shell_calls: list[str] = []
    _real_shell_bytes = bridge.shell_bytes

    def _counting_shell_bytes(command, timeout=None):  # type: ignore[no-untyped-def]
        shell_calls.append(command)
        return _real_shell_bytes(command, timeout=timeout)

    bridge.shell_bytes = _counting_shell_bytes  # type: ignore[method-assign]
    try:
        bulk = bridge.bulk_read_files(bulk_entries)
    finally:
        bridge.shell_bytes = _real_shell_bytes  # type: ignore[method-assign]

    check("批量读只发 1 次 adb 调用（不是每目录一次）",
          len(shell_calls) == 1, f"实际 {len(shell_calls)} 次")
    check("批量读用 `-C /` + 根相对路径",
          bool(shell_calls) and "-C /" in shell_calls[0], (shell_calls[0][:70] if shell_calls else ""))
    check("批量读覆盖了不同目录下的文件",
          len(bulk) == len(bulk_entries) and b"_songName" in bulk.get("Song A", b""),
          f"{len(bulk)}/{len(bulk_entries)}")
    check("批量读对不存在的文件安静跳过",
          bridge.bulk_read_files([(f"{REMOTE_ROOT}/Song C/info.dat", "Song C")]) == {})

    # 分片必须"不丢路径、且每片都塞得进 Windows 的命令行"。
    # 上限 32767 是 CreateProcess 的限制（实测 64 KB 时抛 WinError 206）。
    long_paths = ["x" * 100] * 500
    chunked = _chunk_by_command_budget(long_paths)
    sizes = [sum(len(p) + 3 for p in chunk) for chunk in chunked]
    check("分片不丢路径且每片都在预算内",
          sum(len(c) for c in chunked) == len(long_paths)
          and max(sizes) <= _BULK_COMMAND_BUDGET
          and max(sizes) + 103 > _BULK_COMMAND_BUDGET,  # 确实塞满了，不是切得过碎
          f"{len(chunked)} 片，最大 {max(sizes)} 字节 / 预算 {_BULK_COMMAND_BUDGET}")

    print("=== 4. 复制：push / pull ===")
    local_src = sandbox("e2e_adb") / "local" / "New Song"
    shutil.rmtree(local_src.parent, ignore_errors=True)
    (local_src).mkdir(parents=True)
    (local_src / "info.dat").write_text('{"_songName":"新歌"}', encoding="utf-8")
    (local_src / "song.egg").write_bytes(b"x" * 1024)

    fs.copy_into("New Song", local_src)
    check("push 后设备上出现目录", device_path("New Song").is_dir())
    check("push 后文件内容一致", (device_path("New Song") / "info.dat").read_text(encoding="utf-8") == '{"_songName":"新歌"}')
    check("push 后源仍在", (local_src / "info.dat").exists())

    pull_dst = sandbox("e2e_adb", clean=False) / "pull"
    shutil.rmtree(pull_dst, ignore_errors=True)
    fs.copy_out("Song B", pull_dst / "Song B")
    check("pull 后本地出现目录", (pull_dst / "Song B").is_dir())
    check("pull 内容一致", (pull_dst / "Song B" / "song.egg").stat().st_size == 515
          and (pull_dst / "Song B" / "info.dat").stat().st_size == 256,
          str([(p.name, p.stat().st_size) for p in (pull_dst / "Song B").iterdir()]))
    check("pull 未留下 .partial", not (pull_dst / ".Song B.partial").exists())

    print("=== 5. 删除与护栏 ===")
    fs.delete_folder("New Song")
    check("删除生效", not device_path("New Song").exists())
    for bad in ("../Song A", "Song A/info.dat", "", ".", "..", "Song\\A"):
        try:
            fs.delete_folder(bad)
            check(f"非法名被拦截: {bad!r}", False, "竟然没有拦截")
        except FSError:
            check(f"非法名被拦截: {bad!r}", True)

    print("=== 6. 设备未授权时的报错 ===")
    class DeadBridge(AdbBridge):
        def list_devices(self):  # type: ignore[override]
            return []

    dead = AdbFS(location, bridge=DeadBridge(str(FAKE_ADB)))
    try:
        dead.list_song_folders()
        check("无设备时抛出 FSUnavailable", False, "竟然没抛")
    except (FSError, FSUnavailable) as exc:
        check("无设备时抛出 FSUnavailable", True, str(exc).splitlines()[0])

    print("=== 7. 纯函数 ===")
    check("parse_adb_devices", len(parse_adb_devices("List of devices attached\nABC\tdevice model:Quest_3\nXYZ\tunauthorized")) == 2)
    real = find_adb(str(FAKE_ADB))
    check("find_adb 接受显式路径", real is not None, str(real))

    print("=== 8. 兜底搜索：非标准位置也能找到 CustomLevels ===")
    # v14.0 的兜底是 `find /sdcard -maxdepth 7 ...`，但真机上 /sdcard 是符号链接
    # （-> /storage/self/primary），toybox find 从它出发**返回 0 行**（加 -H 也一样），
    # 所以那段兜底从来没生效过。现在改成 `cd <根> && find . ...` 并补回根前缀。
    #
    # 注意：假 adb 会把 /sdcard 与 /storage/emulated/0 都映射到设备根，
    # 所以它**无法复现符号链接那个坑**；这条测试守的是"命令形式 + 路径前缀拼接"
    # 这两件容易写错的事。符号链接本身已在真机（Quest 3 / Android 14）核对过。
    weird = DEVICE / "WeirdPlace" / "Deep" / "CustomLevels"
    weird.mkdir(parents=True, exist_ok=True)
    try:
        found_candidates, _err, _hint = probe_adb(str(FAKE_ADB))
        displays = [c.display for c in found_candidates]
        check("兜底搜索找到非标准位置的 CustomLevels",
              any(d.endswith("/WeirdPlace/Deep/CustomLevels") for d in displays), str(displays))
        check("兜底搜索给出的绝对路径可被 is_dir 验证",
              any(d.startswith(("/sdcard/", "/storage/emulated/0/")) and c.usable
                  for d, c in zip(displays, found_candidates)
                  if d.endswith("/WeirdPlace/Deep/CustomLevels")),
              str([(c.display, c.usable) for c in found_candidates][-2:]))
    finally:
        shutil.rmtree(DEVICE / "WeirdPlace", ignore_errors=True)

    print("=== 9. 复制到设备：非 ASCII 本地路径必须走 ASCII 暂存 + 改名 ===")
    # 真机上 adb.exe 会从本地路径自己推导设备上的目标名，且处理非 ASCII 有 bug：
    # 路径里每有一个非 ASCII 字符，设备上的名字就被截掉 1.5 个字符
    # （…\音乐\www.vrmoo.net_1 -> www.vrmoo.ne；截断还可能落在多字节字符中间，
    #  产生 Android FUSE 删不掉的非法 UTF-8 名）。纯 ASCII 路径则完全正常。
    # 所以含非 ASCII 的本地路径必须先暂存成纯 ASCII 再 push，然后 shell mv 改回真名。
    #
    # 假 adb 是 Python 写的、处理 Unicode 没问题，**复现不了 adb.exe 那个 bug**，
    # 所以这条测试守的是**修复机制**：push 用的 basename 必须是纯 ASCII，
    # 且随后必须发一条 mv 把名字改成真实的（可能是中文的）歌曲名。
    cn_dir = sandbox("e2e_adb", clean=False) / "中文路径" / "歌曲库"
    shutil.rmtree(cn_dir.parent, ignore_errors=True)
    cn_song = cn_dir / "中文歌名"
    cn_song.mkdir(parents=True)
    (cn_song / "info.dat").write_text('{"_songName":"中文路径测试"}', encoding="utf-8")
    (cn_song / "song.egg").write_bytes(b"OGG" + b"2" * 256)

    pushed_names: list[str] = []
    mv_commands: list[str] = []
    _real_push = bridge.push
    _real_shell = bridge.shell

    def _spy_push(local, remote_dir, timeout=None):  # type: ignore[no-untyped-def]
        pushed_names.append(Path(local).name)
        return _real_push(local, remote_dir, timeout=timeout)

    def _spy_shell(command, timeout=None, check=True):  # type: ignore[no-untyped-def]
        if command.strip().startswith("mv "):
            mv_commands.append(command)
        return _real_shell(command, timeout=timeout, check=check)

    bridge.push = _spy_push  # type: ignore[method-assign]
    bridge.shell = _spy_shell  # type: ignore[method-assign]
    try:
        fs.copy_into("中文歌名", cn_song)
    finally:
        bridge.push = _real_push  # type: ignore[method-assign]
        bridge.shell = _real_shell  # type: ignore[method-assign]

    check("非 ASCII 本地路径下 push 用的是纯 ASCII 暂存名",
          bool(pushed_names) and pushed_names[0].isascii(), str(pushed_names))
    check("随后发了一条 mv 把名字改成真实歌曲名",
          any("中文歌名" in c for c in mv_commands), str(mv_commands)[:140])
    check("设备上最终就是真实的中文名字", fs.local_folder_exists("中文歌名"))
    check("复制后内容正确",
          (fs.read_info_json("中文歌名") or {}).get("_songName") == "中文路径测试")
    leftover = [p.name for p in (data_dir() / "push_staging").glob("*")] \
        if (data_dir() / "push_staging").is_dir() else []
    check("暂存目录没有残留", not leftover, str(leftover))
    fs.delete_folder("中文歌名")
    shutil.rmtree(cn_dir.parent, ignore_errors=True)

    # 纯 ASCII 路径走快路径：不该产生暂存目录，也不该多发 mv。
    # 注意：工作区本身就在中文路径下（…\Quest3项目\beatsaber歌曲转移\…），
    # 所以必须借系统临时目录造一个**真正**的纯 ASCII 路径才测得到快路径。
    import tempfile

    ascii_root = Path(tempfile.mkdtemp(prefix="dsh_ascii_"))
    ascii_song = ascii_root / "Plain Song"
    ascii_song.mkdir(parents=True)
    (ascii_song / "info.dat").write_text('{"_songName":"ASCII"}', encoding="utf-8")
    check("测试前提：这个本地路径确实是纯 ASCII",
          str(ascii_song).isascii(), str(ascii_song))

    pushed_names.clear()
    mv_commands.clear()
    bridge.push = _spy_push  # type: ignore[method-assign]
    bridge.shell = _spy_shell  # type: ignore[method-assign]
    try:
        fs.copy_into("Plain Song", ascii_song)
    finally:
        bridge.push = _real_push  # type: ignore[method-assign]
        bridge.shell = _real_shell  # type: ignore[method-assign]
    check("纯 ASCII 路径直接 push 原目录（不走暂存、不发 mv）",
          pushed_names == ["Plain Song"] and not mv_commands,
          f"push={pushed_names} mv={mv_commands}")
    check("纯 ASCII 路径复制结果正确", fs.local_folder_exists("Plain Song"))
    fs.delete_folder("Plain Song")
    shutil.rmtree(ascii_root, ignore_errors=True)

    print("=== 10. 列出歌曲文件：真机 toybox 的 `%y` 是坏的 ===")
    # 真机 Quest 3 / Android 14 / toybox 0.8.9 上 `find -printf '%y'` 坏掉：
    # 单独用输出 0 行，与别的格式符组合时整个输出被截成 1 行。
    # 原先 list_files 走的正是 `'%f\t%T@\t%y\n'`，于是**每个目录只返回 1 个文件**，
    # 而且因为结果非空、那条"为空才退回 ls"的兜底永远不触发（静默截断）。
    # SongFS.cover_name() 依赖 list_files，用户看到的现象是"Quest 歌曲封面随机不显示"。
    # 假 adb 已忠实模拟这个缺陷（见 fixtures/fakeadb/fake_adb.py 的 cmd_find），
    # 所以这几条断言真的有区分度：改回 find -printf 就会红。
    check("多文件歌曲能列出全部文件",
          sorted(fs.list_files("Song A")) == ["cover.png", "info.dat", "song.egg"],
          str(sorted(fs.list_files("Song A"))))
    check("封面探测不再随机失败（cover_name）",
          fs.cover_name("Song A") == "cover.png", fs.cover_name("Song A"))
    check("带特殊字符的目录也能列全",
          sorted(fs.list_files("中文 名字 '特殊'")) == ["cover.png", "info.dat", "song.egg"],
          str(sorted(fs.list_files("中文 名字 '特殊'"))))
    check("封面字节真的读得出来",
          fs.read_cover_bytes("Song A")[:8] == b"\x89PNG\r\n\x1a\n")

    print("=== 11. 复制后校验：必须比文件数与字节数，不能只看目录在不在 ===")
    # 真机上出过两次"静默传坏"，原先只校验"目标目录存在"，两次都发现不了：
    #   * adb push 把设备上的目录名截断（目标目录压根不存在，报错方向还带偏）；
    #   * toybox %y 坏掉，list_files 每个目录只看到 1 个文件。
    # 所以校验改成"文件数 + 总字节"，并且假 adb 支持 FAKE_ADB_PUSH_SKIP 故障注入，
    # 让"少传了文件"这个场景真的能被端到端测到。
    v_src = sandbox("e2e_adb", clean=False) / "verify_src" / "Verify Song"
    shutil.rmtree(v_src.parent, ignore_errors=True)
    v_src.mkdir(parents=True)
    (v_src / "info.dat").write_text('{"_songName":"校验用"}', encoding="utf-8")
    (v_src / "cover.png").write_bytes(b"\x89PNG" + b"c" * 300)
    (v_src / "song.egg").write_bytes(b"OggS" + b"a" * 900)

    fs.copy_into("Verify Song", v_src)
    local_stats = local_file_stats(v_src)
    remote_stats = fs.remote_file_stats("Verify Song")
    check("正常复制后：设备端统计与本地完全一致",
          local_stats == remote_stats and local_stats[0] == 3 and local_stats[1] > 1000,
          f"本地 {local_stats} / 设备 {remote_stats}")

    # 直接制造"设备上少一个文件"，verify_copy 必须拦下
    (device_path("Verify Song") / "cover.png").unlink()
    try:
        fs.verify_copy("Verify Song", v_src)
        check("设备少一个文件时 verify_copy 必须报错", False, "竟然没报错")
    except FSError as exc:
        check("设备少一个文件时 verify_copy 必须报错",
              "校验不通过" in str(exc), str(exc)[:90])
    fs.delete_folder("Verify Song")

    # 端到端故障注入：让 push 少传一个文件，copy_into 自己就该失败
    os.environ["FAKE_ADB_PUSH_SKIP"] = "cover.png"
    try:
        try:
            fs.copy_into("Verify Song", v_src)
            check("push 少传文件时 copy_into 必须报错", False, "竟然没报错")
        except FSError as exc:
            check("push 少传文件时 copy_into 必须报错",
                  "校验不通过" in str(exc), str(exc)[:90])
    finally:
        os.environ.pop("FAKE_ADB_PUSH_SKIP", None)
    # 校验失败后不该把半截目录留在设备上给用户看
    if fs.local_folder_exists("Verify Song"):
        fs.delete_folder("Verify Song")
    shutil.rmtree(v_src.parent, ignore_errors=True)

    print("=== 12. 批量取 mtime：必须一次往返（逐首 stat 真机要 17.6 秒）===")
    # load_library 原先对每首歌调一次 `fs.stat_mtime` —— 在 adb 上就是每首一次
    # shell 往返。真机 Quest 3 / 217 首实测 **17.6 秒**，而一次 `find -printf`
    # 只要 **0.20 秒（90 倍）**。更糟的是那 17.6 秒毫无收益（缓存从来没被写过）。
    all_folders = fs.list_song_folders()
    bulk_calls: list[str] = []
    _real_shell_bulk = bridge.shell

    def _counting_shell(command, timeout=None, check=True):  # type: ignore[no-untyped-def]
        bulk_calls.append(command)
        return _real_shell_bulk(command, timeout=timeout, check=check)

    bridge.shell = _counting_shell  # type: ignore[method-assign]
    try:
        bulk_mtimes = fs.stat_mtimes(all_folders)
    finally:
        bridge.shell = _real_shell_bulk  # type: ignore[method-assign]

    per_folder = {folder: fs.stat_mtime(folder) for folder in all_folders}
    find_calls = [c for c in bulk_calls if "find " in c]
    check("批量 mtime 只发 1 次 find（不是每首一次）",
          len(find_calls) == 1, f"实际 {len(find_calls)} 次 find / {len(bulk_calls)} 次 shell")
    check("批量 mtime 与逐首取的结果一致",
          set(bulk_mtimes) == set(all_folders)
          and all(abs(bulk_mtimes[f] - per_folder[f]) < 1.0 for f in all_folders),
          f"{bulk_mtimes} vs {per_folder}")
    check("没有 info.dat 的歌曲 mtime 为 0（会退回全量解析）",
          bulk_mtimes.get("Song C") == 0.0, str(bulk_mtimes.get("Song C")))

    print("=== 13. 旧版本备份目录不能混进歌单 ===")
    # 覆盖复制时旧版本会被改名挪到 `<名字>__旧版本_questbak`（见 shell_copy.ps1）。
    # 它**不是歌曲** —— 不过滤的话每替换一首歌就会多出一条"无 info.dat 的占位歌曲"。
    backup_dir = device_path("Song A" + BACKUP_SUFFIX)
    shutil.rmtree(backup_dir, ignore_errors=True)
    backup_dir.mkdir(parents=True)
    (backup_dir / "info.dat").write_text('{"_songName":"旧版本"}', encoding="utf-8")
    try:
        listed = fs.list_song_folders()
        check("备份目录没有出现在歌单里",
              not any(n.endswith(BACKUP_SUFFIX) for n in listed), str(listed))
        check("正常歌曲仍然都在",
              {"Song A", "Song B", "Song C", "中文 名字 '特殊'"} <= set(listed), str(listed))
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)

    print("=== 14. 设备识别：只能认 Quest，别的 Android 设备必须跳过 ===")
    # 真实反馈："怎么自动连到我手机了"。根因是 `ensure_device()` 取"第一个
    # usable 的设备"，完全不看是不是 Quest；同时插着手机/平板时必然抓错。
    # 现在它默认 `require_quest=True`，非 Quest 一律拒绝。
    from quest_tool.adb import AdbDevice, _matches_quest

    quest_cases = [
        ("Quest 3", "eureka", "oculus", "Quest 3"),
        ("Quest 2", "hollywood", "oculus", "Quest 2"),
        ("Quest Pro", "seacliff", "oculus", "Quest Pro"),
        ("Quest 3S", "panther", "oculus", "Quest 3S"),
    ]
    for label, code, brand, model in quest_cases:
        dev = AdbDevice(serial="x", state="device", model=model, codename=code, brand=brand)
        check(f"{label} 被认出来", dev.is_quest, f"{code}/{brand}/{model}")

    # 真机实测值（小米平板 M2105K81AC）
    others = [
        ("小米平板 M2105K81AC", "elish", "Xiaomi", "M2105K81AC"),
        ("三星手机", "beyond1", "samsung", "SM-G973F"),
        ("未知 VR 头显", "", "", "Unknown VR Headset"),
        ("完全空", "", "", ""),
    ]
    for label, code, brand, model in others:
        dev = AdbDevice(serial="x", state="device", model=model, codename=code, brand=brand)
        check(f"{label} 不被误认成 Quest", not dev.is_quest, f"{code}/{brand}/{model}")

    # 没探测过身份（codename/brand 都空）必须是 False ——
    # 宁可提示"没找到 Quest"，也不要对着手机做读写删
    fresh = AdbDevice(serial="x", state="device", model="M2105K81AC")
    check("未探测身份时不当 Quest（保守）", not fresh.is_quest)

    check("型号里带 quest 字样也算（品牌兜底）",
          _matches_quest("", "", "Oculus Quest 3"))
    check("大小写不敏感", _matches_quest("EUREKA", "", ""))

    print()
    if FAILURES:
        print(f"结果：{len(FAILURES)} 项失败 -> {FAILURES}")
        return 1
    print("结果：全部通过 ✔")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
