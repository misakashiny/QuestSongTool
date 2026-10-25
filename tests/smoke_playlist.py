"""Beat Saber 歌单读写（ROADMAP D2）。

这个模块最容易出的错不是"崩了"，而是**写出一份看起来正常、游戏里却一首都不显示的
歌单** —— 因为游戏按 `hash` 找歌，而 hash 是算出来的（`info.dat` + 各难度文件依次拼接取 SHA-1）。
所以测试的重点：

1. **hash 算法本身**：与独立计算的 SHA-1 对照；顺序参与计算；缺文件时**判为不可计算**
   （绝不能返回一个错的 hash）；
2. **格式往返**：写出去再读回来，字段一个不少；中文不转义；base64 封面能还原；
   不认识的字段尽量保留；
3. **容错**：坏的 JSON、顶层不是对象、hash 格式不对、base64 坏了，都不能崩；
4. **匹配**：优先按 hash；只在标题 + 谱师**都**相同时才退回按名字匹配
   （只按标题会把歌单对到错误的歌上，比"没对上"更糟）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from _paths import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT))

from quest_tool.playlist import (  # noqa: E402
    LEVEL_ID_PREFIX,
    Playlist,
    PlaylistSong,
    build_playlist,
    difficulty_files,
    dump_playlist,
    hash_report,
    load_playlist,
    match_local,
    parse_playlist,
    save_playlist,
    song_hash,
)

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  -> ' + detail) if detail else ''}")


def make_song(root: Path, name: str, *, title: str = "T", author: str = "A",
              easy: bytes = b'{"e":1}', expert: bytes = b'{"x":2}') -> Path:
    """造一个最小可用的谱面文件夹。"""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    info = {
        "_version": "2.0.0",
        "_songName": title,
        "_songAuthorName": author,
        "_levelAuthorName": "Mapper",
        "_beatsPerMinute": 174.0,
        "_difficultyBeatmapSets": [{
            "_beatmapCharacteristicName": "Standard",
            "_difficultyBeatmaps": [
                {"_difficulty": "Easy", "_beatmapFilename": "Easy.dat"},
                {"_difficulty": "Expert", "_beatmapFilename": "Expert.dat"},
            ],
        }],
    }
    (folder / "info.dat").write_bytes(json.dumps(info, ensure_ascii=False).encode("utf-8"))
    (folder / "Easy.dat").write_bytes(easy)
    (folder / "Expert.dat").write_bytes(expert)
    return folder


# ======================================================================================
# 1) hash
# ======================================================================================
def check_hash(tmp: Path) -> None:
    print("\n--- 谱面 hash ---")
    folder = make_song(tmp, "SongA")
    info_bytes = (folder / "info.dat").read_bytes()
    expected = hashlib.sha1(info_bytes + b'{"e":1}' + b'{"x":2}').hexdigest()

    got = song_hash(folder)
    check("hash 与独立计算的 SHA-1 一致", got == expected, f"{got} vs {expected}")
    check("hash 是 40 位十六进制", len(got) == 40 and all(c in "0123456789abcdef" for c in got), got)

    # 顺序参与计算：交换难度顺序，hash 必须变
    info = json.loads(info_bytes.decode("utf-8"))
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"].reverse()
    (folder / "info.dat").write_bytes(json.dumps(info, ensure_ascii=False).encode("utf-8"))
    check("难度顺序颠倒后 hash 不同（顺序确实参与计算）", song_hash(folder) != got,
          song_hash(folder))
    (folder / "info.dat").write_bytes(info_bytes)

    # 差一个字节 -> hash 必须变
    (folder / "Easy.dat").write_bytes(b'{"e":2}')
    check("难度文件内容改一个字节，hash 就变", song_hash(folder) != got)
    (folder / "Easy.dat").write_bytes(b'{"e":1}')

    check("难度文件列表按 info.dat 的顺序解析",
          difficulty_files(json.loads(info_bytes.decode("utf-8"))) == ["Easy.dat", "Expert.dat"])

    # 缺难度文件 -> 空串（不能给错 hash）
    (folder / "Expert.dat").unlink()
    check("缺少难度文件时返回空串（宁可没有，也不给错的）", song_hash(folder) == "",
          repr(song_hash(folder)))
    (folder / "Expert.dat").write_bytes(b'{"x":2}')

    # 没有 info.dat / 坏 JSON / 带 BOM
    empty = tmp / "NoInfo"
    empty.mkdir()
    check("没有 info.dat 返回空串", song_hash(empty) == "")

    broken = tmp / "Broken"
    broken.mkdir()
    (broken / "info.dat").write_bytes(b"{ not json")
    check("info.dat 不是合法 JSON 返回空串", song_hash(broken) == "")

    bom = make_song(tmp, "WithBom")
    raw = (bom / "info.dat").read_bytes()
    (bom / "info.dat").write_bytes(b"\xef\xbb\xbf" + raw)
    check("info.dat 带 BOM 时仍能算出 hash（且与不带 BOM 不同——BOM 也是内容）",
          song_hash(bom) != "" and song_hash(bom) != song_hash(folder), song_hash(bom)[:12])

    check("不存在的目录返回空串", song_hash(tmp / "nope") == "")


# ======================================================================================
# 2) 格式往返
# ======================================================================================
def test_path(tmp: Path) -> None:
    print("\n--- 格式往返 ---")
    digest = "a" * 40
    playlist = build_playlist(
        "我的歌单", "测试作者",
        [("FolderA", "歌曲一", "谱师甲", digest),
         ("FolderB", "歌曲二", "谱师乙", "")],           # 第二首算不出 hash
        cover=b"\x89PNG\r\n\x1a\nFAKE",
    )
    check("构造出的歌单有 2 首", playlist.count == 2)
    check("只有 1 首带有效 hash", playlist.hashed == 1, str(playlist.hashed))

    text = dump_playlist(playlist)
    check("中文不被转义成 \\uXXXX", "我的歌单" in text and "歌曲一" in text)
    check("levelId 是 custom_level_ + 大写 hash",
          f"{LEVEL_ID_PREFIX}{digest.upper()}" in text, text[:120])

    path = save_playlist(playlist, tmp / "out")
    check("没写后缀时自动补 .bplist", path.suffix == ".bplist", path.name)
    check("文件真的写到磁盘了", path.is_file())

    back = load_playlist(path)
    check("标题往返一致", back.title == "我的歌单", back.title)
    check("作者往返一致", back.author == "测试作者", back.author)
    check("首数往返一致", back.count == 2, str(back.count))
    check("第一首的 hash 往返一致", back.songs[0].hash == digest, back.songs[0].hash)
    check("第一首的标题与谱师往返一致",
          back.songs[0].songName == "歌曲一" and back.songs[0].levelAuthorName == "谱师甲")
    check("封面 base64 能还原", back.cover == b"\x89PNG\r\n\x1a\nFAKE", repr(back.cover[:8]))
    check("算不出 hash 的那首没有伪造 hash",
          back.songs[1].hash == "" and back.songs[1].levelId == "", repr(back.songs[1].hash))

    # 不认识的字段要保留（别的工具写的 bplist 可能有额外字段）
    raw = json.loads(text)
    raw["someToolField"] = {"nested": [1, 2, 3]}
    raw["description"] = "别的工具写的"
    again = parse_playlist(json.dumps(raw, ensure_ascii=False))
    check("不认识的字段被保留下来", "someToolField" in again.extra and "description" in again.extra,
          str(list(again.extra)))
    check("回写时那些字段还在",
          "someToolField" in json.loads(dump_playlist(again)), str(list(again.extra)))


# ======================================================================================
# 3) 容错
# ======================================================================================
def test_tolerance() -> None:
    print("\n--- 容错 ---")
    for label, text in (("不是 JSON", "{ not json"), ("顶层是数组", "[1,2,3]"),
                        ("空字符串", ""), ("顶层是数字", "42")):
        try:
            parse_playlist(text)
            check(f"{label} 应当报 ValueError", False, "没报错")
        except ValueError:
            check(f"{label} 报 ValueError（不崩、能给用户提示）", True)

    # 字段缺失 / 类型不对
    p = parse_playlist('{"songs": [{"songName": "只有标题"}]}')
    check("缺字段的歌也能读出来", p.count == 1 and p.songs[0].hash == "")
    p2 = parse_playlist('{"songs": "不是数组"}')
    check("songs 不是数组时不崩、当作空", p2.count == 0)
    p3 = parse_playlist('{"songs": [{"songName": "x", "hash": "太短了"}]}')
    check("hash 格式不对时清空（不给游戏一个坏 hash）", p3.songs[0].hash == "",
          repr(p3.songs[0].hash))
    p4 = parse_playlist('{"songs": [{"hash": "ABCDEF0123456789ABCDEF0123456789ABCDEF01"}]}')
    check("大写 hash 归一成小写", p4.songs[0].hash == "abcdef0123456789abcdef0123456789abcdef01",
          p4.songs[0].hash)
    p5 = parse_playlist('{"image": "!!!not-base64!!!"}')
    check("坏 base64 封面不崩（当作没有封面）", p5.cover == b"", repr(p5.cover))

    data_uri = "data:image/png;base64," + base64.b64encode(b"hello").decode()
    p6 = parse_playlist(json.dumps({"image": data_uri}))
    check("data URI 形式的封面也能解", p6.cover == b"hello", repr(p6.cover))

    # 空歌单
    empty = parse_playlist("{}")
    check("空对象解析成空歌单", empty.count == 0 and empty.title == "")


# ======================================================================================
# 4) 匹配本地
# ======================================================================================
def test_match() -> None:
    print("\n--- 匹配本地歌库 ---")
    digest = "b" * 40
    playlist = build_playlist("P", "me", [
        ("FromHash", "Song X", "Mapper X", digest),
        ("FromName", "Song Y", "Mapper Y", ""),                 # 没有 hash，靠名字
        ("NotFound", "Song Z", "Mapper Z", ""),                 # 都没有
    ])
    available = [("LocalX", digest), ("LocalY", "")]
    titles = {"LocalY": ("Song Y", "Mapper Y")}

    matched, missing = match_local(playlist, available, titles=titles)
    check("按 hash 命中一首", "LocalX" in matched, str(matched))
    check("按『标题+谱师』命中一首", "LocalY" in matched, str(matched))
    check("匹配不到的进 missing", [s.songName for s in missing] == ["Song Z"],
          str([s.songName for s in missing]))

    # 只按标题相同、但谱师不同 -> 不该匹配（比"没对上"更糟的是对错歌）
    only_title = build_playlist("P", "me", [("F", "Song Y", "别的谱师", "")])
    matched2, missing2 = match_local(only_title, available, titles=titles)
    check("标题相同但谱师不同时**不**匹配（避免对到错误的歌）",
          matched2 == [] and len(missing2) == 1, f"{matched2} / {len(missing2)}")

    # 不给 titles 时，没有 hash 的歌一律算没匹配上
    matched3, _ = match_local(playlist, available)
    check("不提供标题映射时只有 hash 能匹配", matched3 == ["LocalX"], str(matched3))

    # 空歌单
    check("空歌单匹配结果为空", match_local(Playlist(), available) == ([], []))


# ======================================================================================
# 5) hash 报告（必须让用户知道有几首不会被游戏识别）
# ======================================================================================
def test_report() -> None:
    print("\n--- hash 报告 ---")
    good = [PlaylistSong(songName="A", hash="c" * 40)]
    bad = [PlaylistSong(songName="B", hash="")]
    check("全部有 hash 时说明『都能识别』", "都能被游戏识别" in hash_report(good), hash_report(good))
    report = hash_report(good + bad)
    check("有缺失时明确说明『不会显示』", "不会显示" in report, report)
    check("报告里点名了具体是哪首（前 3 首）", "B" in report, report)
    check("空歌单也有话说", "没有歌曲" in hash_report([]), hash_report([]))


def main() -> int:
    print("=" * 78)
    print("Beat Saber 歌单读写（hash 算法 / 格式往返 / 容错 / 匹配）")
    print("=" * 78)
    tmp = Path(tempfile.mkdtemp(prefix="playlist-test-"))
    try:
        check_hash(tmp)
        test_path(tmp)
        test_tolerance()
        test_match()
        test_report()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78)
    failed = [r for r in RESULTS if not r[1]]
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    for label, _ok, detail in failed:
        print(f"  FAIL  {label}  {detail}")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
