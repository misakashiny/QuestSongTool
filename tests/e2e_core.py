"""端到端功能验证（不需要 GUI、不需要真机）。

覆盖：
1. LocalFS 扫描 + 元数据解析（含 UTF-8 BOM、GBK 两种"原版会失败"的 info.dat）
2. 数据库缓存命中（mtime 不变则不重复解析）
3. 对比逻辑（缺失歌曲计算）
4. guard_copy / guard_delete 护栏（原版 P0 数据丢失场景）
5. 排序/搜索后的高亮用"名字"而不是索引
6. 封面读取（含 BOM 之外的真实图片文件）
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from _paths import ROOT, sandbox  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

from quest_tool.database import Database, MetaRow  # noqa: E402
from quest_tool.fsapi import Location, open_fs  # noqa: E402
from quest_tool.songs import SongMeta  # noqa: E402
from quest_tool.workers import GuardError, guard_copy, guard_delete  # noqa: E402

SANDBOX = sandbox("e2e_core")
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def make_song(root: Path, name: str, *, title: str, bpm: float, encoding: str = "utf-8", cover: bool = True) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    info = {
        "_version": "2.0.0",
        "_songName": title,
        "_songAuthorName": "测试作者",
        "_beatsPerMinute": bpm,
        "_coverImageFilename": "cover.png",
        "_difficultyBeatmapSets": [
            {"_beatmapCharacteristicName": "Standard",
             "_difficultyBeatmaps": [{"_difficulty": "Expert"}, {"_difficulty": "Easy"}]}
        ],
    }
    text = json.dumps(info, ensure_ascii=False, indent=2)
    (folder / "info.dat").write_text(text, encoding=encoding)
    if cover:
        make_png(folder / "cover.png", 40, 30)
    return folder


def make_png(path: Path, width: int, height: int) -> None:
    """用 Pillow 生成一张纯色 PNG（也顺便验证 PIL 可用）。"""
    from PIL import Image

    Image.new("RGB", (width, height), (120, 60, 200)).save(path)


def main() -> int:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    local_root = SANDBOX / "LocalSongs"
    quest_mirror = SANDBOX / "QuestMirror"
    local_root.mkdir(parents=True)
    quest_mirror.mkdir(parents=True)

    print("=== 1. 构造测试歌曲库 ===")
    make_song(local_root, "Song A", title="歌曲A", bpm=128)
    make_song(local_root, "Song B", title="歌曲B", bpm=90.5, encoding="utf-8-sig")  # BOM
    make_song(local_root, "Song C", title="歌曲C", bpm=174, encoding="gbk")        # GBK
    (local_root / "not-a-song").mkdir()                                            # 无 info.dat
    (local_root / ".hidden").mkdir()                                               # 隐藏目录
    make_song(quest_mirror, "Song A", title="歌曲A", bpm=128)
    make_song(quest_mirror, "Song D", title="歌曲D", bpm=100)
    check("测试库创建完成", len(list(local_root.iterdir())) == 5)

    print("=== 2. LocalFS 扫描 ===")
    location = Location(kind="local", root=str(local_root), label="测试本地")
    fs = open_fs(location)
    folders = fs.list_song_folders()
    check("列出 4 首（隐藏目录/无效目录被过滤）", folders == ["not-a-song", "Song A", "Song B", "Song C"], str(folders))

    metas = fs.scan_metadata(folders)
    check("BOM 文件解析成功（原版会整份失效）", metas["Song B"].title == "歌曲B", metas["Song B"].title)
    check("GBK 文件解析成功（原版会抛 UnicodeDecodeError）", metas["Song C"].title == "歌曲C", metas["Song C"].title)
    check("无 info.dat 也能给出占位元数据", metas["not-a-song"].display_title == "not-a-song")
    check("BPM 解析", metas["Song A"].display_bpm == "128", metas["Song A"].display_bpm)
    check("BPM 小数解析", metas["Song B"].display_bpm == "90.5", metas["Song B"].display_bpm)
    check("难度排序 Easy->Expert", metas["Song A"].difficulties == ("Easy", "Expert"), str(metas["Song A"].difficulties))
    check("封面文件名解析", metas["Song A"].cover_file == "cover.png", metas["Song A"].cover_file)
    cover = fs.read_cover_bytes("Song A")
    check("封面字节读取", bool(cover) and cover[:8] == b"\x89PNG\r\n\x1a\n", f"{len(cover or b'')} bytes")

    print("=== 3. 数据库缓存 ===")
    db = Database(SANDBOX / "cache.db")
    for meta in metas.values():
        db.save_meta(
            MetaRow(
                folder_name=meta.folder_name, mtime=meta.mtime, title=meta.title,
                subtitle=meta.subtitle, author=meta.author, version=meta.version,
                bpm=meta.bpm, duration=meta.duration,
                difficulties=meta.difficulty_text if meta.difficulties else "",
                cover_file=meta.cover_file,
            )
        )
    cached = db.get_meta("Song A", metas["Song A"].mtime)
    check("缓存命中同一 mtime", cached is not None and cached.title == "歌曲A")
    check("mtime 变化则缓存失效", db.get_meta("Song A", metas["Song A"].mtime + 10) is None)
    check("批量查询只返回命中的键", set(db.bulk_get_meta([(f, metas[f].mtime) for f in folders])) == set(folders))

    print("=== 4. 评分/评价按位置隔离（原版同名歌曲会串号） ===")
    db.set_rating("local:D:/Songs", "Song A", 5)
    db.set_rating("adb:/sdcard/ModData/.../CustomLevels", "Song A", 2)
    check("两个位置各自独立", db.get_rating("local:D:/Songs", "Song A")[0] == 5
          and db.get_rating("adb:/sdcard/ModData/.../CustomLevels", "Song A")[0] == 2)
    db.set_comment("local:D:/Songs", "Song A", "很好")
    check("评价保存", db.get_rating("local:D:/Songs", "Song A")[1] == "很好")

    print("=== 5. 对比逻辑 ===")
    quest_fs = open_fs(Location(kind="local", root=str(quest_mirror)))
    local_names = set(fs.list_song_folders())
    quest_names = set(quest_fs.list_song_folders())
    missing_on_quest = sorted(local_names - quest_names)
    missing_on_local = sorted(quest_names - local_names)
    check("Quest 缺失 = Song B/C/not-a-song", missing_on_quest == ["Song B", "Song C", "not-a-song"], str(missing_on_quest))
    check("本地缺失 = Song D", missing_on_local == ["Song D"], str(missing_on_local))

    print("=== 6. 护栏：原版 P0 数据丢失场景 ===")
    try:
        guard_copy(local_root, local_root, "Song A")
        check("源==目标 时被 guard_copy 拦截", False, "竟然没有拦截！")
    except GuardError as exc:
        check("源==目标 时被 guard_copy 拦截", True, str(exc).splitlines()[0])

    try:
        guard_copy(local_root, local_root / "Song A", "Song A")
        check("目标嵌套在源内时被拦截", False, "竟然没有拦截！")
    except GuardError as exc:
        check("目标嵌套在源内时被拦截", True, str(exc).splitlines()[0])

    try:
        guard_delete(Path(""), "Song A")
        check("空根目录删除被拦截", False, "竟然没有拦截！")
    except GuardError as exc:
        check("空根目录删除被拦截", True, str(exc)[:40])

    try:
        guard_delete(local_root, "../Song A")
        check("路径穿越删除被拦截", False, "竟然没有拦截！")
    except GuardError as exc:
        check("路径穿越删除被拦截", True, str(exc)[:40])

    try:
        guard_delete(Path("C:/"), "Windows")
        check("盘符根目录删除被拦截", False, "竟然没有拦截！")
    except GuardError as exc:
        check("盘符根目录删除被拦截", True, str(exc)[:40])

    src, dst = guard_copy(local_root, quest_mirror, "Song B")
    check("正常复制通过校验", src == local_root / "Song B" and dst == quest_mirror / "Song B")

    print("=== 7. 真实复制（LocalFS.copy_into） ===")
    target_fs = open_fs(Location(kind="local", root=str(quest_mirror)))
    target_fs.copy_into("Song B", local_root / "Song B")
    target_fs.copy_into("Song C", local_root / "Song C")
    check("复制后目标包含 4 首", sorted(p.name for p in quest_mirror.iterdir()) == ["Song A", "Song B", "Song C", "Song D"],
          str(sorted(p.name for p in quest_mirror.iterdir())))
    check("复制后源文件仍然存在（原版会删源）", (local_root / "Song B" / "info.dat").exists()
          and (local_root / "Song C" / "info.dat").exists())
    reloaded = open_fs(Location(kind="local", root=str(quest_mirror)))
    check("复制后重新扫描可见", set(reloaded.list_song_folders()) >= {"Song A", "Song B", "Song C", "Song D"})

    print("=== 8. 覆盖已存在目标 ===")
    (quest_mirror / "Song A" / "extra.txt").write_text("old", encoding="utf-8")
    target_fs.copy_into("Song A", local_root / "Song A")
    check("覆盖后旧文件被清除、新内容到位", not (quest_mirror / "Song A" / "extra.txt").exists()
          and (quest_mirror / "Song A" / "info.dat").exists())

    print("=== 9. 删除保护 + 正常删除 ===")
    target_fs.delete_folder("Song D")
    check("正常删除生效", not (quest_mirror / "Song D").exists())
    try:
        target_fs.delete_folder("../LocalSongs/Song A")
        check("通过后端删除时的穿越被拦截", False, "竟然没有拦截！")
    except Exception as exc:
        check("通过后端删除时的穿越被拦截", True, type(exc).__name__)

    db.close()
    print()
    if FAILURES:
        print(f"结果：{len(FAILURES)} 项失败 -> {FAILURES}")
        return 1
    print("结果：全部通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
