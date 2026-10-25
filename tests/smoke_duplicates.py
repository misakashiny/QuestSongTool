"""重复歌曲检测（ROADMAP D3）。

判重是"最容易凭感觉写错、又最难在界面上发现"的那类逻辑：
**漏报**只是没帮上忙，**误报**会让用户删掉其实不同的歌 —— 那是数据损失。
所以这个测试的重点一半在"该报的要报"，另一半在"**不该报的绝不能报**"。

覆盖：

1. 标题归一化（全角/大小写/标点/难度/画质/版本标记/feat）；
2. `exact`（标题 + 作者都相同）；
3. `same_title`（标题同、作者不同 —— 要标成"有风险"）；
4. `same_author_bpm`（默认**关闭**，显式开启才生效）；
5. **不误报**：只标题像但作者不同且只开了 exact 时不该报；
   同一首歌不该出现在两个组里；
6. 真实感场景：作者为空的歌、只有文件夹名的歌、BPM 写法差异（174 / 174.0 / 173.99）。
"""
from __future__ import annotations

import sys

from _paths import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT))

from quest_tool.duplicates import (  # noqa: E402
    DEFAULT_LEVELS,
    find_duplicates,
    level_label,
    normalize_author,
    normalize_title,
)
from quest_tool.songs import SongMeta  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  -> ' + detail) if detail else ''}")


def song(folder: str, title: str = "", author: str = "", bpm: float = 0.0) -> SongMeta:
    return SongMeta(folder_name=folder, title=title, author=author, bpm=bpm)


# ======================================================================================
# 1) 标题归一化
# ======================================================================================
def check_normalize() -> None:
    print("\n--- 标题归一化 ---")
    same = [
        ("Song Name", "song name"),
        ("  Song  Name  ", "Song Name"),
        ("Song - Name", "Song Name"),
        ("Song（Full）Name", "Song Name"),
        ("Song Name (feat. Someone)", "Song Name"),
        ("Song Name [HD]", "Song Name"),
        ("Song Name - Expert+", "Song Name"),
        ("Song Name (Expert)", "Song Name"),
        ("Song Name (Full Version)", "Song Name"),
        ("Song Name (完整版)", "Song Name"),
        ("歌名（完整版）", "歌名"),
        ("Ｓｏｎｇ Ｎａｍｅ", "song name"),      # 全角字母
    ]
    bad = [(a, b, normalize_title(a), normalize_title(b))
           for a, b in same if normalize_title(a) != normalize_title(b)]
    check(f"同一首歌的不同写法归一后相等（{len(same)} 组）", not bad, str(bad[:2]))

    different = [("Song One", "Song Two"), ("歌名A", "歌名B"), ("Alpha", "Alph")]
    wrongly = [(a, b) for a, b in different
               if normalize_title(a) == normalize_title(b)]
    check("不同的歌不会被归一成同一个", not wrongly, str(wrongly))

    check("作者归一化：去空白 + 转小写",
          normalize_author("  Test  Author ") == "test author",
          normalize_author("  Test  Author "))
    check("空标题归一化为空串", normalize_title("") == "" and normalize_title("()") == "",
          repr(normalize_title("()")))


# ======================================================================================
# 2) exact：标题 + 作者都相同
# ======================================================================================
def check_exact() -> None:
    print("\n--- exact（标题 + 作者都相同）---")
    songs = [
        song("A", "My Song", "Mapper X"),
        song("B", "my song", "mapper x"),          # 大小写不同，仍算同一首
        song("C", "My Song (Full)", "Mapper X"),   # 版本标记不同，仍算同一首
        song("D", "Other Song", "Mapper X"),
        song("E", "My Song", "Mapper Y"),          # 标题同作者不同 -> 不是 exact
    ]
    report = find_duplicates(songs, levels=["exact"])
    check("exact 找到 1 组", report.group_count == 1, str(report.group_count))
    if report.groups:
        group = report.groups[0]
        check("组内 3 首（A/B/C）", group.count == 3, str(group.folder_names))
        check("exact 组不标风险（可以放心）", not group.risky)
        check("多余 = 2 首", report.duplicate_count == 2, str(report.duplicate_count))
    check("只开 exact 时，同标题不同作者不算重复",
          "E" not in [n for g in report.groups for n in g.folder_names])


# ======================================================================================
# 3) same_title：标题同、作者不同 -> 要标风险
# ======================================================================================
def check_same_title() -> None:
    print("\n--- same_title（标题同、作者不同）---")
    songs = [
        song("A", "My Song", "Mapper X"),
        song("B", "My Song", "Mapper Y"),
    ]
    report = find_duplicates(songs, levels=["same_title"])
    check("same_title 找到 1 组", report.group_count == 1, str(report.group_count))
    if report.groups:
        check("该组被标为『有风险』（可能是不同 mapper 的版本）", report.groups[0].risky)

    # 默认档位：exact + same_title，两首都算重复
    report2 = find_duplicates(songs, levels=list(DEFAULT_LEVELS))
    check("默认档位下也会报出来", report2.group_count == 1, str(report2.group_count))


# ======================================================================================
# 4) 作者为空的歌：不能冒充 exact
# ======================================================================================
def check_missing_author() -> None:
    print("\n--- 作者读不出来时 ---")
    songs = [
        song("A", "Same Title", ""),
        song("B", "Same Title", ""),
    ]
    exact_only = find_duplicates(songs, levels=["exact"])
    check("作者都为空时，exact 不报（不能拿『空 == 空』当铁证）",
          exact_only.group_count == 0, str(exact_only.group_count))

    by_title = find_duplicates(songs, levels=["same_title"])
    check("但 same_title 会报出来（标题确实一样）",
          by_title.group_count == 1, str(by_title.group_count))


# ======================================================================================
# 5) same_author_bpm：默认关闭，开启才生效；BPM 容差
# ======================================================================================
def check_author_bpm() -> None:
    print("\n--- same_author_bpm（默认关闭）---")
    songs = [
        song("A", "Song One", "Mapper X", 174),
        song("B", "Song Two", "Mapper X", 174.0),
        song("C", "Song Three", "Mapper X", 173.99),   # 容差内
        song("D", "Song Four", "Mapper X", 180),       # 差太远，不该并进来
    ]
    default_report = find_duplicates(songs)                    # 用 DEFAULT_LEVELS
    check("默认档位下不启用最松的那档",
          all(g.level != "same_author_bpm" for g in default_report.groups),
          str([g.level for g in default_report.groups]))

    loose = find_duplicates(songs, levels=["same_author_bpm"])
    check("显式开启后能找到 1 组", loose.group_count == 1, str(loose.group_count))
    if loose.groups:
        names = loose.groups[0].folder_names
        check("BPM 容差内的 3 首都进组（174 / 174.0 / 173.99）",
              names == ["A", "B", "C"], str(names))
        check("BPM 差太远的 D 不并进来", "D" not in names)


# ======================================================================================
# 6) 一首歌只能属于一个组；空输入与退化输入
# ======================================================================================
def check_invariants() -> None:
    print("\n--- 不变量与边界 ---")
    songs = [
        song("A", "Both", "Mapper X", 174),
        song("B", "Both", "Mapper X", 174),      # 既符合 exact 也符合 other
        song("C", "Only Title", "Mapper X", 200),
        song("D", "Only Title", "Mapper Y", 200),
    ]
    report = find_duplicates(songs, levels=["exact", "same_title", "same_author_bpm"])
    seen: list[str] = []
    for group in report.groups:
        seen.extend(group.folder_names)
    check("同一首歌不会出现在两个组里（否则用户会重复劳动）",
          len(seen) == len(set(seen)), str(seen))

    check("空输入不报错", find_duplicates([]).group_count == 0)
    check("单首歌不算重复", find_duplicates([song("A", "T", "X")]).group_count == 0)

    # 只有文件夹名、没有 title 时，退回用文件夹名判重
    folder_only = [song("Song (Full)", ""), song("Song", "")]
    title_key = find_duplicates(folder_only, levels=["same_title"])
    check("标题为空时退回用文件夹名判重", title_key.group_count == 1,
          str(title_key.group_count))

    # 空文件夹名 + 空标题 -> 跳过，并计入 skipped
    degenerated = [SongMeta(folder_name="", title=""), song("B", "T", "X")]
    report3 = find_duplicates(degenerated)
    check("完全没有标识的记录被跳过并计数", report3.skipped == 1, str(report3.skipped))

    check("summary 能给出人话", "没有发现重复" in find_duplicates([]).summary(),
          find_duplicates([]).summary())
    check("有重复时 summary 含组数与多余数",
          "组重复" in find_duplicates([song("A", "T", "X"), song("B", "T", "X")]).summary())

    check("档位说明文案齐全",
          all(level_label(lv) != lv for lv in ("exact", "same_title", "same_author_bpm")))


def main() -> int:
    print("=" * 78)
    print("重复歌曲检测（归一化 / 三档规则 / 不误报 / 不变量）")
    print("=" * 78)
    check_normalize()
    check_exact()
    check_same_title()
    check_missing_author()
    check_author_bpm()
    check_invariants()

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
