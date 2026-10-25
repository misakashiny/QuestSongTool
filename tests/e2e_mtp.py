"""验证 MTP 后端：用真实目录模拟"没有盘符的设备"，走 Shell CopyHere 镜像 + 解析。"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from _paths import ROOT, sandbox  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

from quest_tool.constants import BACKUP_SUFFIX  # noqa: E402
from quest_tool.device_mtp import MtpFS, copy_shell_items  # noqa: E402
from quest_tool.fsapi import Location  # noqa: E402

SANDBOX = sandbox("e2e_mtp")
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def make_song(root: Path, name: str, title: str, bpm: float) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "info.dat").write_text(
        json.dumps(
            {
                "_version": "2.0.0",
                "_songName": title,
                "_songAuthorName": "MTP作者",
                "_beatsPerMinute": bpm,
                "_coverImageFilename": "cover.png",
                "_difficultyBeatmapSets": [
                    {"_difficultyBeatmaps": [{"_difficulty": "Hard"}, {"_difficulty": "Expert"}]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"m" * 32)
    (folder / "song.egg").write_bytes(b"OGG" + b"d" * 256)


def main() -> int:
    print("=== 0. 准备模拟设备 ===")
    shutil.rmtree(SANDBOX, ignore_errors=True)
    device = SANDBOX / "Quest 3" / "内部共享存储空间" / "ModData" / "com.beatgames.beatsaber" / "Mods" / "SongCore" / "CustomLevels"
    device.mkdir(parents=True, exist_ok=True)
    make_song(device, "Song A", "MTP歌曲A", 128)
    make_song(device, "Song B", "MTP歌曲B", 99.5)
    check("模拟设备目录已创建", device.is_dir(), str(device))

    print("=== 1. Shell 复制原语 ===")
    raw_target = SANDBOX / "_raw"
    shutil.rmtree(raw_target, ignore_errors=True)
    raw_target.mkdir(parents=True)
    info = copy_shell_items("to_local", str(device), str(raw_target), timeout=60)
    check("CopyHere 无错误", not info.error, info.error)
    check("复制到本地成功", (raw_target / "Song A" / "info.dat").is_file() and (raw_target / "Song B" / "info.dat").is_file(),
          str(sorted(p.name for p in raw_target.iterdir())))

    print("=== 2. MtpFS 镜像 + 解析 ===")
    location = Location(kind="mtp", root=str(device), label="模拟Quest", shell_path=str(device))
    fs = MtpFS(location)
    print(f"  镜像目录: {fs.mirror_root}")
    shutil.rmtree(fs.mirror_root, ignore_errors=True)

    folders = fs.list_song_folders()
    check("列出 2 首（走真实 CopyHere 镜像）", sorted(folders) == ["Song A", "Song B"], str(folders))
    check("镜像目录已生成", fs.mirrored, str(fs.mirror_root))

    metas = fs.scan_metadata(folders)
    check("解析标题", metas["Song A"].title == "MTP歌曲A", metas["Song A"].title)
    check("解析 BPM", metas["Song B"].display_bpm == "99.5", metas["Song B"].display_bpm)
    check("解析难度", metas["Song A"].difficulties == ("Hard", "Expert"), str(metas["Song A"].difficulties))
    check("mtime 有效", metas["Song A"].mtime > 0, str(metas["Song A"].mtime))
    cover = fs.read_cover_bytes("Song A")
    check("封面可读", bool(cover) and cover[:8] == b"\x89PNG\r\n\x1a\n")
    check("第二次 list 使用缓存镜像", fs.list_song_folders() == folders)

    print("=== 3. 单曲复制到本地 ===")
    out_dir = SANDBOX / "_out" / "Song A"
    shutil.rmtree(SANDBOX / "_out", ignore_errors=True)
    fs.copy_out("Song A", out_dir)
    check("copy_out 得到完整目录", (out_dir / "info.dat").is_file() and (out_dir / "song.egg").is_file(),
          str(sorted(p.name for p in out_dir.iterdir())))

    print("=== 4. 安全行为 ===")
    try:
        fs.delete_folder("Song A")
        check("MTP 删除被拒绝", False, "竟然允许了")
    except Exception as exc:
        check("MTP 删除被拒绝", True, str(exc).splitlines()[0])
    for bad in ("../Song A", "a/b", "", ".."):
        try:
            fs.list_files(bad)
            check(f"非法名被拦截 {bad!r}", False, "竟然允许了")
        except Exception:
            check(f"非法名被拦截 {bad!r}", True)

    print("=== 5. 复制到设备（本地 -> MTP） ===")
    new_song = SANDBOX / "NewSong"
    new_song.mkdir(parents=True, exist_ok=True)
    (new_song / "info.dat").write_text('{"_songName":"来自PC"}', encoding="utf-8")
    fs.copy_into("NewSong", new_song)
    check("设备上出现新目录", (device / "NewSong" / "info.dat").is_file(),
          str(sorted(p.name for p in device.iterdir())))
    check("复制后镜像已刷新", (fs.mirror_root / "NewSong" / "info.dat").is_file())
    # ★ 关键回归：CopyHere 的源是"歌曲库目录"，**必须**用 -Only 限定那一首。
    # v14.5 之前没有 -Only，于是复制一首歌会把**整个本地歌库**都推过去 ——
    # new_song 在 SANDBOX 里的兄弟（_raw / _out / Quest 3）就是会被误传的证据。
    # （注意 Song A/Song B 是设备上原有的歌，不算兄弟。）
    siblings = [n for n in ("_raw", "_out", "Quest 3") if (device / n).exists()]
    check("设备上**没有**多出源目录的兄弟条目（曾把整个歌库推过去）",
          siblings == [], f"多出来的: {siblings}；设备现有: {sorted(p.name for p in device.iterdir())}")

    print("=== 6. 增量镜像 + 复制后不整份重镜像（v14.5 修的两项）===")
    # 这两条是本文件最重要的断言：
    #   * 原先 copy_into 每复制一首就 `sync_mirror(force=True)` —— 把设备上**全部**歌曲
    #     重拉一遍，复制 N 首 = N 次全量镜像；
    #   * 原先 sync_mirror 只有"镜像存在且非空就用缓存"，所以设备上新增/删除的歌
    #     **永远不反映**，只能整份重拉。
    # 用 spy 数 `copy_shell_items` 的调用，看它到底拉了什么。
    import quest_tool.device_mtp as mtp_mod

    calls: list[tuple[str, str, str, str]] = []
    _real_copy = mtp_mod.copy_shell_items

    def _spy(direction, source, dest, timeout=900, replace=False, only=""):  # type: ignore[no-untyped-def]
        calls.append((direction, str(source), str(dest), only))
        return _real_copy(direction, source, dest, timeout=timeout, replace=replace, only=only)

    def _full_mirror_calls() -> list[tuple[str, str, str, str]]:
        """挑出"拉整份 CustomLevels"的调用（source 是设备根、且没限定 only）。"""
        return [c for c in calls if c[0] == "to_local" and c[1] == str(device) and not c[3]]

    mtp_mod.copy_shell_items = _spy  # type: ignore[assignment]
    try:
        # 6a. 复制一首到设备：应当只有 1 次 to_device，**没有**整份重镜像
        calls.clear()
        song_d = SANDBOX / "SongD"
        song_d.mkdir(parents=True, exist_ok=True)
        (song_d / "info.dat").write_text('{"_songName":"D"}', encoding="utf-8")
        (song_d / "cover.png").write_bytes(b"\x89PNG" + b"d" * 40)
        fs.copy_into("SongD", song_d)
        check("复制到设备只发 1 次 to_device",
              len([c for c in calls if c[0] == "to_device"]) == 1, str(calls))
        check("复制到设备时用 -Only 限定了那一首（否则会把整个歌库推过去）",
              any(c[0] == "to_device" and c[3] == "SongD" for c in calls), str(calls))
        check("复制后**没有**整份重镜像（原先每复制一首就重拉一遍）",
              _full_mirror_calls() == [], str(_full_mirror_calls()))
        check("复制后这首歌已在镜像里", (fs.mirror_root / "SongD" / "info.dat").is_file())

        # 6b. 设备上新增一首 -> 增量同步只拉这一首，不整份重拉
        calls.clear()
        make_song(device, "Song E", "MTP歌曲E", 150)
        folders_now = fs.list_song_folders()
        check("设备上新增的歌被同步进来", "Song E" in folders_now, str(folders_now))
        check("增量同步只拉了新增那一首（没有整份重拉）",
              _full_mirror_calls() == []
              and any(c[0] == "to_local" and c[3] == "Song E" for c in calls),
              str(calls))
        calls.clear()
        fs.list_song_folders()
        check("已是最新时不再发任何复制调用", calls == [], str(calls))

        # 6c. 设备上删掉一首 -> 镜像里也要消失（原先永远不反映）
        calls.clear()
        shutil.rmtree(device / "Song E", ignore_errors=True)
        folders_now = fs.list_song_folders()
        check("设备上删掉的歌也从镜像里消失", "Song E" not in folders_now, str(folders_now))
        check("删除是从本地镜像删的（不需要重拉）", _full_mirror_calls() == [], str(calls))

        # 6d. force=True 仍然整份重拉（"重新扫描"按钮走这条）
        calls.clear()
        fs.sync_mirror(force=True)
        check("force=True 时才整份重拉", len(_full_mirror_calls()) == 1, str(_full_mirror_calls()))
    finally:
        mtp_mod.copy_shell_items = _real_copy  # type: ignore[assignment]

    print("=== 7. MTP 的复制后校验（比设备上的文件数，不拿镜像充数）===")
    # Shell 的"大小"列是四舍五入后的字符串（实测 3.00 MB），比不了字节；
    # 所以 MTP 退化成"比顶层文件数"。关键是它枚举的是**设备**，不是我们自己的镜像。
    probe = SANDBOX / "VerifyMtp"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "info.dat").write_text('{"_songName":"V"}', encoding="utf-8")
    (probe / "cover.png").write_bytes(b"\x89PNG" + b"v" * 30)
    fs.copy_into("VerifyMtp", probe)
    check("正常复制后校验通过", fs.remote_file_stats("VerifyMtp") == (2, None),
          str(fs.remote_file_stats("VerifyMtp")))

    (device / "VerifyMtp" / "cover.png").unlink()
    try:
        fs.verify_copy("VerifyMtp", probe)
        check("设备上少一个文件时 MTP 校验必须报错", False, "竟然没报错")
    except Exception as exc:
        check("设备上少一个文件时 MTP 校验必须报错",
              "校验不通过" in str(exc), str(exc)[:80])
    shutil.rmtree(device / "VerifyMtp", ignore_errors=True)
    shutil.rmtree(fs.mirror_root / "VerifyMtp", ignore_errors=True)

    print("=== 8. 旧版本备份目录不能混进歌单（也不能被镜像进来）===")
    # 覆盖复制时旧版本会被改名挪到 `<名字>__旧版本_questbak`。
    # 它**不是歌曲** —— 不过滤的话，用户每替换一首歌就会在歌单里多出一条
    # "无 info.dat 的占位歌曲"，而且镜像还会白拉一份。
    backup_name = "Song A" + BACKUP_SUFFIX
    shutil.rmtree(device / backup_name, ignore_errors=True)
    shutil.rmtree(fs.mirror_root / backup_name, ignore_errors=True)
    make_song(device, backup_name, "旧版本", 100)
    try:
        listed = fs.list_song_folders()
        check("备份目录没有出现在歌单里",
              not any(n.endswith(BACKUP_SUFFIX) for n in listed), str(listed))
        check("备份目录没有被镜像进来（白拉一份）",
              not (fs.mirror_root / backup_name).exists())
        check("正常歌曲仍然都在",
              {"Song A", "Song B"} <= set(listed), str(listed))
    finally:
        shutil.rmtree(device / backup_name, ignore_errors=True)
        shutil.rmtree(fs.mirror_root / backup_name, ignore_errors=True)

    print()
    if FAILURES:
        print(f"结果：{len(FAILURES)} 项失败 -> {FAILURES}")
        return 1
    print("结果：全部通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
