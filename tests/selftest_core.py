"""新模块的快速自检（不需要 GUI）。"""
from __future__ import annotations

from pathlib import Path

from _paths import sandbox

from quest_tool.database import Database, MetaRow  # noqa: E402
from quest_tool.songs import parse_info_file  # noqa: E402

v2 = {
    "_version": "2.0.0",
    "_songName": "Test Song",
    "_songAuthorName": "Author A",
    "_beatsPerMinute": 128.5,
    "_coverImageFilename": "cover.png",
    "_difficultyBeatmapSets": [
        {"_difficultyBeatmaps": [{"_difficulty": "Expert"}, {"_difficulty": "Easy"}]}
    ],
}
m = parse_info_file(v2, "song1", 1.0)
assert m.display_title == "Test Song", m
assert m.display_bpm == "128.5", m.display_bpm
assert m.difficulties == ("Easy", "Expert"), m.difficulties
assert m.cover_file == "cover.png"

v4 = {
    "version": "4.0.0",
    "song": {"title": "T4", "author": "B"},
    "audio": {"bpm": 90, "songDuration": 125},
    "difficultyBeatmaps": [{"difficulty": "ExpertPlus"}],
}
m4 = parse_info_file(v4, "song2", 2.0)
assert m4.display_title == "T4" and m4.display_duration == "2:05", m4
assert m4.difficulties == ("ExpertPlus",), m4.difficulties

broken = parse_info_file(None, "song3", 0.0)
assert broken.title == "" and broken.parse_error == "no-info"
assert broken.display_title == "song3" and broken.display_author == "未知作者"

with Database(":memory:") as db:
    db.save_meta(MetaRow("f1", 1.0, title="t1", difficulties="Easy"))
    got = db.get_meta("f1", 1.0)
    assert got is not None and got.title == "t1", got
    assert db.get_meta("f1", 5.0) is None, "mtime 变化必须视为缓存失效"
    assert list(db.bulk_get_meta([("f1", 1.0), ("f2", 9.0)])) == ["f1"]
    db.set_rating("local:C:/songs", "f1", 4)
    db.set_comment("local:C:/songs", "f1", "很好玩")
    assert db.all_ratings("local:C:/songs") == {"f1": 4}
    assert db.all_comments("local:C:/songs") == {"f1": "很好玩"}
    assert db.get_rating("local:C:/songs", "f1") == (4, "很好玩")
    # 同名歌曲在另一个位置互不影响
    assert db.get_rating("adb:/sdcard/CustomLevels", "f1") == (0, "")
    db.set_rating("adb:/sdcard/CustomLevels", "f1", 2)
    assert db.get_rating("local:C:/songs", "f1")[0] == 4
    assert db.all_titles() == {"f1": "t1"}
    db.save_meta_bulk([MetaRow(f"b{i}", 1.0, title=f"t{i}") for i in range(50)])
    assert db.stats()["meta"] == 51, db.stats()

    # prune_meta：v14.5 修过 —— keep_names 原先方向反了（DELETE ... IN (...)），
    # 传进去要保留的反而全被删；而且它此前从未被调用，库只增不减。
    before = db.stats()["meta"]
    db.save_meta_bulk([MetaRow("dup", 1.0, title="old"), MetaRow("dup", 2.0, title="new")])
    assert db.stats()["meta"] == before + 2
    db.prune_meta()  # 去重：每个 folder_name 只留 mtime 最新的那条
    assert db.stats()["meta"] == before + 1, db.stats()
    assert db.get_meta("dup", 2.0) is not None and db.get_meta("dup", 1.0) is None, "应只剩最新 mtime"

    db.save_meta_bulk([MetaRow("keepme", 1.0), MetaRow("dropme", 1.0)])
    db.prune_meta(keep_names=["keepme"])
    assert db.get_meta("keepme", 1.0) is not None, "要保留的不能被删掉"
    assert db.get_meta("dropme", 1.0) is None, "keep_names 之外的行应被删掉"

# --------------------------------------------------------------------------------------
# 旧版数据库迁移（ROADMAP C3）
# 原版是单表 `songs`（folder_name 唯一），评分混在元数据里；新版拆到 userdata，
# 键是 (location, folder_name)。所以迁移必须带"位置标识"。
# --------------------------------------------------------------------------------------
import sqlite3  # noqa: E402

_legacy_dir = sandbox("selftest_core")
_legacy_dir.mkdir(parents=True, exist_ok=True)
_legacy_path = _legacy_dir / "legacy_songs_cache.db"
if _legacy_path.exists():
    _legacy_path.unlink()

# 造一个"原版格式"的库
_conn = sqlite3.connect(_legacy_path)
_conn.execute(
    """CREATE TABLE songs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, folder_name TEXT UNIQUE,
        title TEXT, subtitle TEXT, author TEXT, version TEXT, bpm REAL,
        difficulties TEXT, path TEXT, cover_path TEXT, last_modified TIMESTAMP,
        file_mtime REAL, rating INTEGER DEFAULT 0, comment TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
)
_conn.executemany(
    "INSERT INTO songs(folder_name, title, rating, comment) VALUES (?,?,?,?)",
    [
        ("LegacyA", "老歌 A", 5, "神曲"),
        ("LegacyB", "老歌 B", 3, ""),
        ("LegacyC", "老歌 C", 0, "只是备注"),   # 只有评价也要迁
        ("LegacyD", "老歌 D", 0, ""),            # 什么都没 -> 不该迁
    ],
)
_conn.commit()
_conn.close()
_legacy_before = _legacy_path.read_bytes()

with Database(":memory:") as db:
    stats = db.import_legacy(_legacy_path, "local:D:/Songs")
    assert stats["found"] == 3, f"只有带评分/评价的才该被发现：{stats}"
    assert stats["imported"] == 3, stats
    assert db.get_rating("local:D:/Songs", "LegacyA") == (5, "神曲")
    assert db.get_rating("local:D:/Songs", "LegacyC") == (0, "只是备注"), "只有评价的也要迁"
    assert db.get_rating("local:D:/Songs", "LegacyD") == (0, ""), "空记录不该迁"

    # 幂等：再跑一次不会重复，也不会覆盖新版里改过的值
    db.set_rating("local:D:/Songs", "LegacyB", 1)  # 用户在新版里改了分
    again = db.import_legacy(_legacy_path, "local:D:/Songs")
    assert again["imported"] == 0 and again["skipped"] == 3, again
    assert db.get_rating("local:D:/Songs", "LegacyB")[0] == 1, "不能被旧库覆盖回 3"
    assert db.all_ratings("local:D:/Songs")["LegacyA"] == 5

    # 换个位置再迁：同一首歌在两个位置各自独立
    other = db.import_legacy(_legacy_path, "adb:/sdcard/CustomLevels")
    assert other["imported"] == 3, other
    assert db.get_rating("adb:/sdcard/CustomLevels", "LegacyA")[0] == 5
    assert db.get_rating("local:D:/Songs", "LegacyA")[0] == 5

# 旧库必须原封不动（那是用户唯一的退路）
assert _legacy_path.read_bytes() == _legacy_before, "迁移不该改动旧数据库！"

# 不是原版格式的库要给出明确错误，而不是静默成功
_bogus = _legacy_dir / "bogus.db"
_bogus.unlink(missing_ok=True)
sqlite3.connect(_bogus).execute("CREATE TABLE t(x)").connection.commit()
with Database(":memory:") as db:
    try:
        db.import_legacy(_bogus, "local:X")
        raise AssertionError("不是原版格式的库必须报错")
    except ValueError as exc:
        assert "songs" in str(exc), str(exc)

print("结果：旧库迁移断言全部通过")

# --------------------------------------------------------------------------------------
# 复制队列的暂停闸门（ROADMAP B4）
# 要点：暂停时阻塞、继续后立刻放行、**暂停期间被取消不能卡死**。
# --------------------------------------------------------------------------------------
import threading  # noqa: E402
import time as _time  # noqa: E402

from quest_tool.workers import PauseGate, Token  # noqa: E402

_gate = PauseGate()
_tok = Token("pause-test")
assert _gate.paused is False, "初始不该是暂停"
assert _gate.wait(_tok) is True, "未暂停时应当立刻通过"
assert _gate.toggle() is True and _gate.paused, "toggle 应返回切换后的状态"
assert _gate.toggle() is False and not _gate.paused, "再 toggle 应恢复"

# 暂停时 wait 必须阻塞，继续后放行
_gate.pause()
_started = _time.monotonic()
_released: list[bool] = []
_t = threading.Thread(target=lambda: _released.append(_gate.wait(_tok)), daemon=True)
_t.start()
_time.sleep(0.4)
assert _released == [], "暂停时 wait 必须阻塞（不能空转通过）"
_gate.resume()
_t.join(timeout=2.0)
assert _released == [True], f"继续后应当放行：{_released}"
assert _time.monotonic() - _started >= 0.4, "至少该阻塞这么久"

# 暂停期间被取消 -> 立即返回 False（否则"先暂停再取消"会永远卡住）
_gate.pause()
_tok2 = Token("pause-cancel")
_result: list[bool] = []
_t2 = threading.Thread(target=lambda: _result.append(_gate.wait(_tok2)), daemon=True)
_t2.start()
_time.sleep(0.3)
_tok2.cancel()
_t2.join(timeout=2.0)
assert _result == [False], f"暂停等待期间被取消应当返回 False：{_result}"
assert not _t2.is_alive(), "取消后线程必须退出"
_gate.resume()

# 已取消的令牌在未暂停时也应立刻返回 False
assert _gate.wait(_tok2) is False, "已取消的令牌不该放行"

print("结果：暂停闸门断言全部通过")

from quest_tool.storage import Settings  # noqa: E402
import shutil  # noqa: E402

tmp_root = sandbox("selftest_core")
shutil.rmtree(tmp_root, ignore_errors=True)
tmp_root.mkdir(parents=True, exist_ok=True)
try:
    sp = tmp_root / "settings.json"
    s = Settings(local_path="D:/Songs", quest_path="adb:/sdcard/x")
    s.remember("local", "D:/Songs")
    s.remember("local", "D:/Other")
    s.remember("local", "D:/Songs")
    assert s.recent_local == ["D:/Songs", "D:/Other"], s.recent_local
    assert s.save(sp)
    assert Settings.load(sp).quest_path == "adb:/sdcard/x"
    sp.write_text("{ broken json", encoding="utf-8")
    assert Settings.load(sp).local_path == "", "损坏的设置必须回落默认值"
    assert (tmp_root / "settings.json.bak").exists()
finally:
    shutil.rmtree(tmp_root, ignore_errors=True)

from quest_tool.fsapi import is_selector_like, normalize_user_path, Location  # noqa: E402

assert is_selector_like("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\Quest 3\\内部共享存储空间")
assert is_selector_like("此电脑\\Quest 3\\内部共享存储空间")
assert not is_selector_like("D:\\Songs")
assert normalize_user_path('  "d:\\songs"  ') == "D:\\songs"
loc = Location(kind="adb", root="/sdcard/Android/data/com.beatgames.beatsaber/files/CustomLevels")
assert loc.child("Song A").root.endswith("/CustomLevels/Song A")

# ---------------- 设备探测（盘符路径） ----------------
# v14.0 在这里同时踩了两个坑，而且两个都不在原来的测试覆盖里：
#   ① device.py 用了没导入的 CUSTOM_LEVELS_RELATIVE → NameError；
#   ② max_depth 只有 4，而真实设备树是 6 层 → 就算 import 修好了也一层都到不了。
# 两处都被 scan_quest() 吞成一条 warning，界面上完全看不出来，所以必须有断言盯着。
print("=== 设备探测：盘符路径 ===")

from quest_tool import device  # noqa: E402

DEVICE_SANDBOX = sandbox("selftest_device")


def device_tree(*segments: str) -> tuple[Path, Path]:
    """造一棵 `沙箱/Quest 3/<segments>` 的假设备树，返回 (设备根, CustomLevels 路径)。

    注意返回的"设备根"是 `Quest 3` 那一层（盘符挂载时设备根下面还有一层设备名），
    不是沙箱目录本身 —— 否则被测路径会平白多出一层，测不到真实层级。
    """
    shutil.rmtree(DEVICE_SANDBOX, ignore_errors=True)
    base = DEVICE_SANDBOX / "Quest 3"
    target = base.joinpath(*segments)
    target.mkdir(parents=True)
    return base, target


# 真实设备树正好 6 层：两种布局 + 英文系统名都要能直接命中
for layout in (
    ("内部共享存储空间", "Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"),
    ("内部共享存储空间", "ModData", "com.beatgames.beatsaber", "Mods", "SongCore", "CustomLevels"),
    ("Internal shared storage", "ModData", "com.beatgames.beatsaber", "Mods", "SongCore", "CustomLevels"),
):
    base, target = device_tree(*layout)
    assert device.find_custom_levels_under(base) == [str(target)], f"6 层设备树没找到: {layout}"

# 藏在无关键名后面时靠 os.walk 兜底（这条同时守住 max_depth 不会再被调小）
base, target = device_tree("Download", "stuff", "CustomLevels")
assert device.find_custom_levels_under(base) == [str(target)], "os.walk 兜底失效"

# 整条链路：盘符 → 认作 Quest 根 → 找到 CustomLevels → 产出可用候选
base, target = device_tree(
    "内部共享存储空间", "Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"
)
_real_drive_letters = device.drive_letters
try:
    device.drive_letters = lambda: [str(base.parent)]
    candidates = device.probe_drives()
finally:
    device.drive_letters = _real_drive_letters
assert len(candidates) == 1, f"probe_drives 应产出 1 个候选，实际 {candidates}"
assert candidates[0].kind == "drive" and candidates[0].usable, candidates[0]
assert candidates[0].display == str(target), candidates[0].display
assert candidates[0].location is not None and candidates[0].location.kind == "local"

# 探不到设备是正常情况，必须安静返回空列表而不是抛异常
assert device.find_custom_levels_under(DEVICE_SANDBOX / "nope") == []
shutil.rmtree(DEVICE_SANDBOX, ignore_errors=True)

# --------------------------------------------------------------------------------------
# best()：连接方式的优先级（v14.9 之前这个方法**从没被调用过**，
# 而 app 用的是 candidates[0] —— 那会优先选到"盘符"，与文档推荐的 adb 相反）
# --------------------------------------------------------------------------------------
def _cand(kind: str) -> device.QuestCandidate:
    return device.QuestCandidate(
        kind=kind, display=kind, usable=True,
        location=Location(kind=kind, root=f"/{kind}"),
    )


# scan_quest 的收集顺序是"盘符 → adb → MTP"，所以 candidates[0] 是盘符
_report = device.DeviceReport(candidates=[_cand("drive"), _cand("adb"), _cand("mtp")])
assert _report.candidates[0].kind == "drive"
assert _report.best() is not None and _report.best().kind == "adb", \
    "默认必须优先 adb，不能退化成 candidates[0]（盘符）"
# 用户指定偏好时，偏好优先
assert _report.best("mtp").kind == "mtp"
assert _report.best("drive").kind == "drive"
# 偏好的那种不可用时，回落到默认顺序
_only_drive = device.DeviceReport(candidates=[_cand("drive")])
assert _only_drive.best("mtp").kind == "drive"
# 全不可用 -> None
_unusable = device.DeviceReport(candidates=[device.QuestCandidate(kind="adb", display="x", usable=False)])
assert _unusable.best() is None and _unusable.best("adb") is None

print("结果：设备探测断言全部通过")

print("ALL SELF-CHECKS PASSED")
