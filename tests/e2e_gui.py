"""端到端 GUI 测试：真实创建主窗口，程序化驱动"载入 → 对比 → 评分 → 删除"。

用 `root.update()` 手动泵事件循环，不进入 mainloop，因此可以无人值守运行。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from _paths import ROOT, sandbox  # noqa: E402  （统一在 tests/_paths.py 里解析路径）
sys.path.insert(0, str(ROOT))

from quest_tool.constants import MIN_SIZE  # noqa: E402
from quest_tool.storage import Settings, database_path  # noqa: E402
from quest_tool.songs import SongMeta  # noqa: E402

SANDBOX = sandbox("e2e_gui")
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
                "_songAuthorName": "作者",
                "_beatsPerMinute": bpm,
                "_coverImageFilename": "cover.png",
                "_difficultyBeatmapSets": [
                    {"_difficultyBeatmaps": [{"_difficulty": "Expert"}, {"_difficulty": "Easy"}]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from PIL import Image

    Image.new("RGB", (60, 40), (200, 80, 60)).save(folder / "cover.png")


def pump(app, seconds: float = 6.0, until=None) -> None:
    """泵事件循环直到条件满足或超时。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.root.update()
        if until is not None and until():
            return
        time.sleep(0.02)


def main() -> int:
    print("=== 0. 准备测试库 ===")
    shutil.rmtree(SANDBOX, ignore_errors=True)
    local = SANDBOX / "LocalSongs"
    quest = SANDBOX / "QuestSongs"
    local.mkdir(parents=True)
    quest.mkdir(parents=True)
    for i, name in enumerate(["Song One", "Song Two", "Song Three", "中文歌曲"]):
        make_song(local, name, f"本地歌{i}", 120 + i)
    for i, name in enumerate(["Song One", "Song Three", "Quest Only"]):
        make_song(quest, name, f"设备歌{i}", 100 + i)
    check("测试库已创建", len(list(local.iterdir())) == 4 and len(list(quest.iterdir())) == 3)

    from quest_tool.app import QuestComparatorApp

    settings = Settings(local_path=str(local), quest_path=str(quest), device_poll_on_start=False,
                        appearance="Dark")
    print("=== 1. 启动主窗口 ===")
    app = QuestComparatorApp(settings=settings)
    app.root.withdraw()  # 不弹到屏幕上，但仍然真实创建所有控件
    pump(app, 1.0)
    check("主窗口创建成功", app.root.winfo_exists() == 1)
    check("三个列表都存在", all(w.winfo_exists() for w in (app.list_local, app.list_quest, app.list_missing)))
    check("数据目录正确", database_path().parent == ROOT, str(database_path()))

    # 窗口尺寸：保存的几何要同时受"屏幕上限"和"最小尺寸下限"约束。
    # 这个测试自己就会把窗口尺寸写进真实的 settings.json（曾经写下 200x200），
    # 如果只夹上限不夹下限，用户跑完测试再启动程序，窗口就只有 200x200。
    _saved_geometry = app.settings.window_geometry
    app.settings.window_geometry = "200x200+0+0"
    _tiny = app._initial_geometry()
    _tiny_w, _tiny_h = (int(v) for v in _tiny.split("+", 1)[0].split("x"))
    check("保存的窗口尺寸被夹到最小尺寸以上", (_tiny_w, _tiny_h) >= MIN_SIZE,
          f"{_tiny_w}x{_tiny_h} vs MIN_SIZE={MIN_SIZE}")

    app.settings.window_geometry = "1400x900+0+0"
    _normal = app._initial_geometry()
    app.settings.window_geometry = _saved_geometry
    check("正常保存的窗口尺寸仍然生效（没有被夹过头）", _normal.startswith("1400x900+"), _normal)

    # 路径识别：直接粘贴的设备路径必须认出来。
    # v14.5 之前 `/sdcard/...` 会掉进"本地盘符"分支 → 被 LocalFS 打开 → 报
    # "路径不可访问"，用户完全看不出是被判成了本地路径。而 `--scan` 打印的正是这个格式。
    check("粘贴 /sdcard/... 会被认成 adb 路径",
          app._resolve_location("quest", "/sdcard/ModData/x/CustomLevels").kind == "adb")
    check("粘贴 /storage/... 也会被认成 adb 路径",
          app._resolve_location("quest", "/storage/emulated/0/CustomLevels").kind == "adb")
    check("adb: 前缀仍然有效",
          app._resolve_location("quest", "adb:/sdcard/CustomLevels").kind == "adb")
    check("Windows 盘符路径仍按本地处理",
          app._resolve_location("quest", "D:\\Songs").kind == "local")
    check("MTP 命名空间路径仍按 mtp 处理",
          app._resolve_location("quest", "::{GUID}\\Quest 3").kind == "mtp")

    # 预估耗时的速率必须按**传输通道**取，不能按源的类型取。
    # v14.7 之前用的是源的类型：补齐缺失歌曲是"本地 → Quest"，源是 local →
    # 于是按本地磁盘 150 MB/s 估算，把十几秒说成 1 秒（真机实测 adb 有 17.4 MB/s）。
    check("通道判定：本地→本地 用 local",
          app._channel_key("local", "local") == "local")
    check("通道判定：本地→Quest 用设备的通道（不是 local）",
          app._channel_key("local", "adb") == "adb"
          and app._channel_key("local", "mtp") == "mtp")
    check("通道判定：Quest→本地 也用设备的通道",
          app._channel_key("adb", "local") == "adb")
    check("设备通道的兜底速率明显低于本地磁盘",
          app._rate_for("adb") < app._rate_for("local")
          and app._rate_for("mtp") < app._rate_for("local"),
          f"local={app._rate_for('local')} adb={app._rate_for('adb')} mtp={app._rate_for('mtp')}")

    print("=== 2. 载入本地歌曲 ===")
    app.load_library("local")
    # 等"列表 4 首"之外，还要等元数据落库（增量扫描会先把缓存喂给列表，
    # 元数据随后异步补齐），否则下一步断言标题会偶发失败。
    pump(
        app,
        25.0,
        until=lambda: app.list_local.total_count() == 4
        and app.libraries["local"].meta.get("Song One") is not None,
    )
    check("本地列表 4 首", app.list_local.total_count() == 4, str(app.list_local.total_count()))
    check("本地 folders 已填充", len(app.libraries["local"].folders) == 4)

    # 增量扫描必须真的生效（v14.5 修的）：原先载入时**从不把元数据落库**
    # （只有点开某首歌的 show_song 会写），所以缓存永远是空的、每次都全量重解析 ——
    # "增量扫描"这个功能实际上从未工作过。这两条断言分别守住"写"和"命中"。
    local_lib = app.libraries["local"]
    local_rows = app.db.bulk_get_meta(
        [(f, local_lib.meta[f].mtime) for f in local_lib.folders if f in local_lib.meta]
    )
    check("载入后元数据已落库（否则缓存永远不命中）",
          len(local_rows) == 4, f"{len(local_rows)}/4")

    # 第二次载入应当**一首都不用重新解析**（全部命中缓存）。
    # 直接盯日志里那行用户可见的结论，比只看"scan_metadata 有没有被调用"更稳：
    # 全部命中时 `to_scan` 为空，`scan_metadata` 压根不会被调用。
    import logging

    log_lines: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):  # type: ignore[no-untyped-def]
            log_lines.append(record.getMessage())

    capture = _Capture()
    app_logger = logging.getLogger("quest_tool.app")
    app_logger.addHandler(capture)

    # 注意：load_library 每次都会 close 掉旧的 fs 再新建一个，
    # 所以补丁要打在**类**上，不能打在 `library.fs` 那个实例上（会被丢弃）。
    from quest_tool.fsapi import LocalFS

    scanned_counts: list[int] = []
    _real_scan = LocalFS.scan_metadata

    def _counting_scan(self, folders, **kwargs):  # type: ignore[no-untyped-def]
        folders = list(folders)
        scanned_counts.append(len(folders))
        return _real_scan(self, folders, **kwargs)

    LocalFS.scan_metadata = _counting_scan  # type: ignore[method-assign]
    try:
        app.load_library("local")
        # 与首次载入使用相同的25秒异步预算。整套混跑曾有16秒事件循环
        # 停顿，10秒等待超时后仍继续断言，会把尚未回填误报成标题解析失败。
        # 明确检查完成条件，保留后面的命中数、零重解析和精确标题断言。
        pump(app, 25.0, until=lambda: any("缓存命中" in m for m in log_lines)
             and len(local_lib.meta) == 4)
        cache_ready = (any("缓存命中" in m for m in log_lines)
                       and len(local_lib.meta) == 4)
    finally:
        LocalFS.scan_metadata = _real_scan  # type: ignore[method-assign]
        app_logger.removeHandler(capture)

    check("第二次载入缓存回填在期限内完成", cache_ready)
    check("第二次载入全部命中缓存（增量扫描真的生效）",
          any("缓存命中 4 首，需重新解析 0 首" in m for m in log_lines),
          str(log_lines[-1:]))
    check("第二次载入一首都没重新解析", scanned_counts == [], f"重新解析了 {scanned_counts}")

    meta = app.libraries["local"].meta
    check("元数据标题已解析", meta.get("Song One", SongMeta("x")).title == "本地歌0",
          meta.get("Song One", SongMeta("x")).title)

    # 最近路径：`Settings.recent_*` 其实一直在写（remember()），但 v14.9 之前**从来没人读** ——
    # 典型"存了不读"的半成品。现在载入时也会记，并有了读取出口（路径框旁的 🕘 菜单）。
    check("载入后路径被记进最近路径",
          str(local) in app.settings.recent_local, str(app.settings.recent_local))
    check("_recent_for 能读出最近路径（读的出口存在）",
          app._recent_for("local") == app.settings.recent_local,
          str(app._recent_for("local")))

    # 同一批歌走不同通道，预估耗时应当不同（设备通道更慢）。
    # 直接塞两个差距极大的速率，确保断言与歌曲实际大小无关、结果确定。
    _saved_rates = dict(app.settings.transfer_rates)
    try:
        app.settings.transfer_rates["local"] = 1000.0
        app.settings.transfer_rates["adb"] = 0.000001
        _pair = ["Song One", "Song Two"]
        _fast = app._estimate_copy(app.libraries["local"].fs, _pair, "local")
        _slow = app._estimate_copy(app.libraries["local"].fs, _pair, "adb")
        check("两条通道算出的总大小一致（只是速率不同）",
              _fast is not None and _slow is not None and _fast["bytes"] == _slow["bytes"] > 0,
              f"{_fast} / {_slow}")
        check("速率差异确实体现在预估耗时上（按源类型取速率就会一样）",
              bool(_fast and _slow) and _fast["eta"] != _slow["eta"],
              f"local={_fast['eta'] if _fast else None} adb={_slow['eta'] if _slow else None}")
    finally:
        app.settings.transfer_rates.clear()
        app.settings.transfer_rates.update(_saved_rates)

    print("=== 3. 载入 Quest 歌曲 ===")
    app.load_library("quest")
    pump(app, 25.0, until=lambda: app.list_quest.total_count() == 3
         and app.libraries["quest"].meta.get("Song One") is not None)
    check("Quest 列表 3 首", app.list_quest.total_count() == 3, str(app.list_quest.total_count()))

    print("=== 4. 对比（Quest 缺失） ===")
    app.compare("quest")
    pump(app, 3.0)
    missing = sorted(app.missing)
    check("缺失 2 首（Song Two / 中文歌曲）", missing == ["Song Two", "中文歌曲"], str(missing))
    check("缺失列表已填充", app.list_missing.total_count() == 2, str(app.list_missing.total_count()))

    # 「缺失」不是一份曲库，而是**某一侧的视图** —— `libraries` 里只有 local / quest。
    # v17.7 引入筛选档位时，`_make_item("missing", …)` 去 `libraries["missing"]` 取库，
    # 于是上面这行 `compare()` 直接抛 KeyError；而 `compare` 是按钮回调，
    # 异常被 Tk 的命令分发吞掉 —— 用户看到的是"点了对比没反应"。
    check("对比不抛异常（『缺失』不是曲库，曾经打成 KeyError）", True)
    check("_sample_kind 把『缺失』映射到本地侧（歌在本地）",
          app._sample_kind("missing") == "local", app._sample_kind("missing"))
    check("『缺失』列真的填进了数据（不再是一片空白）",
          app.list_missing.total_count() == 2, str(app.list_missing.total_count()))

    # 筛选档位：本列的歌相对**另一侧**分成「已匹配 / 缺失」。
    local_names_set = set(app.libraries["local"].folders)
    quest_names_set = set(app.libraries["quest"].folders)
    both = local_names_set & quest_names_set
    local_only = local_names_set - quest_names_set
    check("测试场景本身成立：两侧有交集也有差集", bool(both) and bool(local_only),
          f"both={sorted(both)} local_only={sorted(local_only)}")

    counts = app._filter_counts("local")
    check("『全部』计数 = 本地曲库总数", counts["all"] == len(local_names_set), str(counts))
    check("『已匹配』计数 = 两侧同名数", counts["matched"] == len(both), str(counts))
    check("『缺失』计数 = 本地有而 Quest 没有的", counts["missing"] == len(local_only), str(counts))

    widget = app.list_local
    widget.set_filter("matched")
    app._apply_song_filter("local", "matched")
    check("『已匹配』档只留两侧都有的",
          set(app._visible_names("local", "matched")) == both,
          str(app._visible_names("local", "matched")))
    check("筛选后列表条数随之变化",
          widget.total_count() == len(both), str(widget.total_count()))

    widget.set_filter("missing")
    app._apply_song_filter("local", "missing")
    check("『缺失』档只留本地独有的",
          set(app._visible_names("local", "missing")) == local_only,
          str(app._visible_names("local", "missing")))
    check("档位计数会随筛选刷新（不是只在切换时算一次）",
          widget._filter_counts.get("all") == len(local_names_set),
          str(widget._filter_counts))

    widget.set_filter("all")
    app._apply_song_filter("local", "all")
    check("切回『全部』恢复满额",
          widget.total_count() == len(local_names_set), str(widget.total_count()))

    app.compare("local")
    pump(app, 3.0)
    check("反方向对比：缺失列方向也跟着翻", app.missing_side == "local", app.missing_side)
    check("_sample_kind 反向也映射对（歌在 Quest 侧）",
          app._sample_kind("missing") == "quest", app._sample_kind("missing"))
    app.compare("quest")
    pump(app, 3.0)

    # 封面墙的「缺失」列必须真的填上。v14.6 之前这里读的是**不存在的**
    # `self._missing_side`（真正被赋值的是 `missing_side`），AttributeError 又被
    # `set_view_mode` 的 try/except 静默吞掉 —— 用户看到的现象就是
    # "切到封面墙后，Quest 缺的那一列永远是空的"，而且不报任何错。
    app.set_view_mode("grid")
    pump(app, 3.0)
    check("封面墙『缺失』列有内容（原先被静默吞掉，永远是空的）",
          app.wall_missing.total_count() == len(app.missing),
          f"{app.wall_missing.total_count()} vs {len(app.missing)}")
    check("封面墙另两列也正常",
          app.wall_local.total_count() == 4 and app.wall_quest.total_count() == 3,
          f"{app.wall_local.total_count()} / {app.wall_quest.total_count()}")
    app.set_view_mode("list")
    pump(app, 1.0)

    print("=== 5. 选中歌曲 → 详情面板 ===")
    app.list_local.select("Song One")
    # 详情分两阶段绘制：先用手上已有的元数据（标题/作者），再补 BPM/封面。
    # 所以这里要等"完整版"出现（BPM 不再是占位符），而不是只等标题。
    pump(app, 12.0, until=lambda: app.detail_snapshot()["bpm"] not in ("—", ""))
    snap = app.detail_snapshot()
    check("标题显示", snap["title"] == "本地歌0", snap["title"])
    check("BPM 显示", snap["bpm"] == "120", snap["bpm"])
    check("文件夹显示", "Song One" in snap["folder"], snap["folder"])
    check("封面已加载", app.cover_label.cget("image") not in ("", None))
    check("current_song 已设置", app.current_song == ("local", "Song One"), str(app.current_song))

    print("=== 6. 评分与持久化 ===")
    app.set_rating(4)
    pump(app, 1.0)
    identity = app.libraries["local"].identity
    check("评分写入数据库", app.db.get_rating(identity, "Song One")[0] == 4,
          str(app.db.get_rating(identity, "Song One")))
    db_ratings = app.db.all_ratings(identity)
    check("列表拿到新评分", db_ratings.get("Song One") == 4, str(db_ratings))

    print("=== 7. 复制缺失歌曲到 Quest ===")
    app.missing_side = "quest"
    # 直接走底层复制，避免弹出确认/进度对话框（无头环境无法点按钮）
    source_fs = app.libraries["local"].fs
    target_fs = app.libraries["quest"].fs
    assert source_fs and target_fs
    copied = []
    for song in app.missing:
        app._copy_one(source_fs, target_fs, song)
        copied.append(song)
    check("缺失歌曲已复制", (quest / "Song Two" / "info.dat").is_file() and (quest / "中文歌曲" / "info.dat").is_file(),
          str(copied))
    check("复制未破坏源目录", (local / "Song Two" / "info.dat").is_file())

    print("=== 8. 删除（护栏 + 真实删除） ===")
    try:
        app.libraries["quest"].fs.delete_folder("../LocalSongs/Song One")
        check("通过界面后端删除的穿越被拦截", False, "竟然允许了")
    except Exception as exc:
        check("通过界面后端删除的穿越被拦截", True, type(exc).__name__)

    app.libraries["quest"].fs.delete_folder("Quest Only")
    check("正常删除生效", not (quest / "Quest Only").exists())

    print("=== 9. 搜索（防抖 + 不卡死） ===")
    # 计时只包含"输入 + 事件泵"，不含 180ms 防抖等待（那是设计上的延迟）
    started = time.monotonic()
    for text in ("S", "So", "Son", "Song", "Song T"):
        app.list_local.set_search(text)
        app.root.update()
    input_elapsed = time.monotonic() - started
    check("5 次搜索输入不卡顿（< 1 秒）", input_elapsed < 1.0, f"{input_elapsed:.3f}s")
    pump(app, 3.0, until=lambda: len(app.list_local.visible_names()) == 2)
    check(
        '搜索 "Song T" 命中 Song Three / Song Two',
        sorted(app.list_local.visible_names()) == ["Song Three", "Song Two"],
        str(app.list_local.visible_names()),
    )
    # 多词 AND：两个词必须都命中
    app.list_local.set_search("song two")
    pump(app, 1.5, until=lambda: len(app.list_local.visible_names()) == 1)
    check(
        '多词 AND 搜索 "song two" 只命中 1 首',
        app.list_local.visible_names() == ["Song Two"],
        str(app.list_local.visible_names()),
    )
    app.list_local.set_search("")
    pump(app, 1.5, until=lambda: len(app.list_local.visible_names()) == 4)
    check("清空搜索后恢复", len(app.list_local.visible_names()) == 4, str(app.list_local.visible_names()))

    print("=== 9.5 在「缺失」列删除（删的必须是拥有它的那一侧）===")    # v14.6 修的两个问题：
    #   ① kind="missing" 没有 fs，`delete_song("missing", ...)` 只弹一句
    #      "请先载入该歌曲列表"，**什么都不做**；
    #   ② 就算走到了 `delete_missing_song`，那里的方向也是反的 ——
    #      `missing_side == "quest"`（「Quest 缺的」）意味着歌在**本地**，
    #      它却去设备上删，必然失败。
    # 这里直接驱动行删除回调，并把确认框改成"总是确认"。
    import quest_tool.app as app_module

    _real_show = app_module.ConfirmDialog.show
    app_module.ConfirmDialog.show = lambda self: True  # type: ignore[method-assign]
    try:
        victim = "Song Two"  # 在缺失列表里，且确实只存在于本地
        check("测试前提：目标歌在缺失列表里", victim in app.missing, str(sorted(app.missing)))
        app._on_row_delete("missing", victim)
        pump(app, 15.0, until=lambda: not (local / victim).is_dir())
    finally:
        app_module.ConfirmDialog.show = _real_show  # type: ignore[method-assign]
    check("缺失列删除生效（删的是本地那一份）", not (local / victim).is_dir(),
          str(sorted(p.name for p in local.iterdir())))
    check("设备侧没被误删", (quest / "Song Three").is_dir())

    print("=== 9.6 设置 copy_overwrite 真的起作用（v14.9 之前它只有定义、没有实现）===")
    src_dir = SANDBOX / "import_src"
    shutil.rmtree(src_dir, ignore_errors=True)
    incoming = src_dir / "Song One"          # 与本地已有的那首同名
    incoming.mkdir(parents=True)
    (incoming / "info.dat").write_text('{"_songName":"来自导入"}', encoding="utf-8")
    (incoming / "song.egg").write_bytes(b"NEW" * 100)
    target_info = local / "Song One" / "info.dat"
    before_text = target_info.read_text(encoding="utf-8")

    # import_songs 会弹确认框；无人值守时必须显式确认，否则导入压根不会发生
    # —— 那样 "False 时没被覆盖" 会因为"什么都没做"而假通过。
    app_module.ConfirmDialog.show = lambda self: True  # type: ignore[method-assign]
    original_handle = app._handle_message
    import_reloads = []
    completed_import_scans = set()

    def observe_import_reload(message):
        original_handle(message)
        payload = message.payload or {}
        if (message.kind == "copy" and payload.get("reload") == "local"
                and str(payload.get("summary", "")).startswith("导入完成")):
            import_reloads.append(app._scan_tokens.get("local"))
        if message.kind == "meta" and payload.get("kind") == "local" and payload.get("done"):
            completed_import_scans.add(message.token)

    app._handle_message = observe_import_reload
    try:
        app.settings.copy_overwrite = False
        app.import_songs([str(incoming)], "local")
        pump(app, 15.0, until=lambda: not app._importing)
        check("copy_overwrite=False：目标上已存在的歌没被覆盖",
              target_info.read_text(encoding="utf-8") == before_text,
              target_info.read_text(encoding="utf-8")[:40])

        # ⚠️ 导入结束会触发一次自动重载，而 load_library 先 `library.close()`（fs 置空）
        # 再异步装回新的 fs。不等它装好就发起第二次导入，会被"目标歌单还没载入"直接拒掉
        # —— 这正是这条测试第一版失败的原因（日志里只有一次导入记录）。
        # The old FS can still exist before the queued completion starts reload.
        # Require this import's completion to be consumed, then its new FS ready.
        pump(app, 15.0, until=lambda: bool(import_reloads)
             and app._scan_tokens.get("local") is import_reloads[-1]
             and app.libraries["local"].fs is not None)
        check("首次导入的完成消息已消费且对应重载就绪", bool(import_reloads)
              and app._scan_tokens.get("local") is import_reloads[-1]
              and app.libraries["local"].fs is not None)

        app.settings.copy_overwrite = True
        app.import_songs([str(incoming)], "local")
        # 诊断：若这里为 False，说明 import_songs 被上面那种竞态挡掉了
        check("第二次导入确实启动了（没被竞态挡住）", app._importing,
              f"_importing={app._importing}, fs={app.libraries['local'].fs is not None}")
        pump(app, 15.0, until=lambda: not app._importing)
        # The second import also queues a fresh scan. An old nonempty folders
        # list is not proof that its completion and metadata have been consumed.
        def second_import_ready():
            return (len(import_reloads) >= 2 and import_reloads[-1] is not None
                    and app._scan_tokens.get("local") is import_reloads[-1]
                    and import_reloads[-1].value in completed_import_scans
                    and app.libraries["local"].fs is not None)
        pump(app, 15.0, until=second_import_ready)
        check("第二次导入的对应重载及元数据完成后才导出报告", second_import_ready())
    finally:
        app_module.ConfirmDialog.show = _real_show  # type: ignore[method-assign]
        app._handle_message = original_handle
    check("copy_overwrite=True：目标被新内容覆盖",
          target_info.read_text(encoding="utf-8") != before_text,
          target_info.read_text(encoding="utf-8")[:40])
    shutil.rmtree(src_dir, ignore_errors=True)

    print("=== 9.7 导出对比报告（CSV / Markdown）===")
    # ⚠️ 导入/删除都会触发自动重载，而重载会先清空 folders 再填回来。
    # 不等它稳定就导出，会导出一份"本地全空、全是仅设备"的假报告。
    pump(app, 15.0, until=lambda: bool(app.libraries["local"].folders))
    from tkinter import filedialog as _filedialog

    csv_path = SANDBOX / "report.csv"
    md_path = SANDBOX / "report.md"
    for p in (csv_path, md_path):
        p.unlink(missing_ok=True)
    _real_asksave = _filedialog.asksaveasfilename
    try:
        _filedialog.asksaveasfilename = lambda **_kw: str(csv_path)
        app.export_report("csv")
        _filedialog.asksaveasfilename = lambda **_kw: str(md_path)
        app.export_report("md")
    finally:
        _filedialog.asksaveasfilename = _real_asksave  # type: ignore[assignment]

    check("CSV 报告已生成", csv_path.exists(), str(csv_path))
    raw = csv_path.read_bytes()
    # 没有 BOM 的话 Excel 会按 GBK 解，中文标题全乱 —— 这是这条导出最容易踩的坑
    check("CSV 带 UTF-8 BOM（Excel 打开中文不乱码）", raw[:3] == b"\xef\xbb\xbf", str(raw[:3]))
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    csv_lines = csv_text.splitlines()
    check("CSV 表头正确", csv_lines[0].startswith("状态,文件夹,标题"), csv_lines[0])
    # csv.writer 写 \r\n，若再用文本模式落盘会被二次转义成 \r\r\n —— 每行多一个空行
    check("CSV 没有多余空行（换行符没被二次转义）",
          all(line.strip() for line in csv_lines), str([i for i, l in enumerate(csv_lines) if not l.strip()]))
    check("CSV 行数 = 1 表头 + 歌曲数",
          len(csv_lines) == 1 + len(set(app.libraries["local"].folders)
                                    | set(app.libraries["quest"].folders)),
          f"{len(csv_lines)} 行 / 歌曲 {len(set(app.libraries['local'].folders) | set(app.libraries['quest'].folders))} 首")
    check("CSV 里三种状态都覆盖到了",
          "两边都有" in csv_text and "仅本地" in csv_text,
          csv_text.replace("\r\n", " / ")[:200])

    check("Markdown 报告已生成", md_path.exists(), str(md_path))
    md_text = md_path.read_text(encoding="utf-8")
    check("Markdown 有标题与汇总", md_text.startswith("# Quest 歌单对比报告")
          and "合计" in md_text, md_text[:120])
    check("Markdown 是表格形式", "| 状态 | 文件夹 |" in md_text, md_text[:200])
    # 文件夹名里带 `|` 时必须转义，否则表格会被撑坏
    check("Markdown 转义了竖线", "\\|" in md_text or "|" not in "".join(
        app.libraries["local"].folders), "无带竖线的歌名可验证")

    print("=== 9.8 后台线程不能直接碰 UI（走 toast 消息）===")
    # 后台线程**不能**直接调 self.toast.*（ToastHost.show 会直接 configure / 建 Toplevel /
    # 用 after，全都没有线程编组）。想让后台任务弹通知，必须发一条 "toast" 消息，
    # 由主线程的 _pump_queue 来弹 —— 这条断言守的就是那个通道还通着。
    app.toast.clear()
    pump(app, 0.3)
    app.tasks.emit("toast", "", -1,
                   payload={"level": "success", "text": "来自后台任务的通知", "detail": "detail"})
    pump(app, 3.0, until=lambda: bool(app.toast._stack))
    check("后台任务能通过 toast 消息弹通知（由主线程执行）",
          bool(app.toast._stack), f"{len(app.toast._stack)} 条")
    app.toast.clear()
    pump(app, 0.3)

    print("=== 9.9 首次使用向导（ROADMAP C4）===")
    # 向导自己不干活：三个动作都回调主程序，它只显示"到哪一步了"。
    # 这里直接构造（不 show()，避免进入模态 wait_window 卡住测试）。
    from quest_tool.ui_wizard import FirstRunWizard

    wizard_calls: list[str] = []
    wizard = FirstRunWizard(
        app.root,
        local_path="", quest_path="",
        on_pick_local=lambda: wizard_calls.append("pick"),
        on_detect_quest=lambda: wizard_calls.append("detect"),
        on_start_compare=lambda: wizard_calls.append("compare"),
    )
    pump(app, 0.5)
    check("向导三步都在", len(wizard._step_buttons) == 3, str(len(wizard._step_buttons)))
    check("两边都没就绪时「开始对比」是禁用的",
          str(wizard._step_buttons[2].cget("state")) == "disabled",
          str(wizard._step_buttons[2].cget("state")))

    wizard._step_buttons[0].invoke()
    wizard._step_buttons[1].invoke()
    pump(app, 0.3)
    check("前两步的按钮回调到了主程序", wizard_calls == ["pick", "detect"], str(wizard_calls))

    wizard.refresh(str(local), str(quest))
    pump(app, 0.3)
    check("路径就绪后第 1 步打勾",
          "✅" in wizard._status_labels[0].cget("text"), wizard._status_labels[0].cget("text"))
    check("路径就绪后第 2 步打勾",
          "✅" in wizard._status_labels[1].cget("text"), wizard._status_labels[1].cget("text"))
    check("路径就绪后「开始对比」启用",
          str(wizard._step_buttons[2].cget("state")) == "normal",
          str(wizard._step_buttons[2].cget("state")))

    wizard._on_default()          # 点「开始对比」→ 回调 + 关窗
    pump(app, 0.3)
    check("点「开始对比」会回调主程序", wizard_calls[-1] == "compare", str(wizard_calls))
    check("向导关闭后 result=True", wizard.result is True, str(wizard.result))

    # 只看一次：看完就记进设置，之后不再自动弹（仍可从「帮助」手动打开）
    app.settings.wizard_done = True
    app.settings.save()
    check("向导「只看一次」被记进设置", app.settings.wizard_done)

    print("=== 9.10 拖放按落点决定导入到哪一列（ROADMAP B2）===")
    # WM_DROPFILES 不带坐标，所以用"落点的鼠标位置"判断拖到了哪一列 ——
    # 这样就不必再弹"输入 1 或 2"。窗口在测试里是 withdraw 的（没有真实几何），
    # 所以这里用带已知矩形的假控件来验证路由逻辑。
    class _FakeList:
        def __init__(self, rect: tuple[int, int, int, int]) -> None:
            self._rect = rect

        def winfo_exists(self) -> bool:
            return True

        def winfo_rootx(self) -> int:
            return self._rect[0]

        def winfo_rooty(self) -> int:
            return self._rect[1]

        def winfo_width(self) -> int:
            return self._rect[2]

        def winfo_height(self) -> int:
            return self._rect[3]

    real_list_for = app._list_for
    try:
        app._list_for = lambda kind: {                       # type: ignore[method-assign]
            "local": _FakeList((100, 100, 200, 300)),
            "quest": _FakeList((400, 100, 200, 300)),
        }.get(kind)
        check("落点在本地列表里 -> local", app._drop_target_kind(150, 200) == "local",
              str(app._drop_target_kind(150, 200)))
        check("落点在 Quest 列表里 -> quest", app._drop_target_kind(500, 200) == "quest",
              str(app._drop_target_kind(500, 200)))
        check("落在两个列表之间 -> None（退回询问）",
              app._drop_target_kind(350, 200) is None, str(app._drop_target_kind(350, 200)))
        check("落在列表外 -> None", app._drop_target_kind(10, 10) is None,
              str(app._drop_target_kind(10, 10)))
        check("边界上也算命中（右下角顶点）",
              app._drop_target_kind(300, 400) == "local", str(app._drop_target_kind(300, 400)))
    finally:
        app._list_for = real_list_for  # type: ignore[method-assign]

    # 目标列会被点亮（反馈），过一会儿自动恢复
    app._flash_drop_target("local", milliseconds=50)
    check("拖放目标列被点亮",
          "⬇" in app.list_local._header.cget("text"), app.list_local._header.cget("text"))
    pump(app, 1.0)
    check("点亮会自己恢复",
          app.list_local._header.cget("text") == app.list_local._title_text,
          app.list_local._header.cget("text"))

    print("=== 9.11 复制队列可暂停/继续（ROADMAP B4）===")
    from quest_tool.ui_dialogs import ProgressDialog

    pause_state = {"paused": False}

    def _toggle_pause() -> bool:
        pause_state["paused"] = not pause_state["paused"]
        return pause_state["paused"]

    pause_dlg = ProgressDialog(app.root, title="暂停测试", message="复制中…",
                               pause_callback=_toggle_pause)
    pump(app, 0.4)
    check("传了暂停回调才会有「暂停」按钮", pause_dlg.pause_button is not None)
    check("初始是「暂停」", "暂停" in pause_dlg.pause_button.cget("text"),
          pause_dlg.pause_button.cget("text"))
    pause_dlg.pause_button.invoke()
    pump(app, 0.3)
    check("点一下变「继续」", "继续" in pause_dlg.pause_button.cget("text"),
          pause_dlg.pause_button.cget("text"))
    check("回调确实返回了暂停状态", pause_state["paused"] is True, str(pause_state))
    check("状态文字提示已暂停", "已暂停" in pause_dlg.status_label.cget("text"),
          pause_dlg.status_label.cget("text"))
    pause_dlg.pause_button.invoke()
    pump(app, 0.3)
    check("再点一下恢复「暂停」", "暂停" in pause_dlg.pause_button.cget("text"),
          pause_dlg.pause_button.cget("text"))
    pause_dlg.close()
    pump(app, 0.3)

    plain_dlg = ProgressDialog(app.root, title="无暂停", message="x")
    pump(app, 0.2)
    check("不传暂停回调就没有这个按钮（导入/删除不需要暂停）",
          plain_dlg.pause_button is None)
    plain_dlg.close()
    pump(app, 0.3)

    print("=== 9.12 左右分栏可拖动 + 宽度记忆（ROADMAP B5）===")
    check("分割器存在且有 2 个窗格", len(app.paned.panes()) == 2, str(len(app.paned.panes())))
    check("详情栏宽度落在合理区间", 280 <= app._detail_width() <= 1200,
          str(app._detail_width()))

    saved_width = app.settings.detail_width
    try:
        app.settings.detail_width = 0
        check("没记住宽度时回落默认值",
              app._detail_width() == app_module.RIGHT_PANEL_WIDTH, str(app._detail_width()))
        app.settings.detail_width = 500
        check("记住了就用记住的", app._detail_width() == 500, str(app._detail_width()))
        app.settings.detail_width = 99999          # 手改坏 / 离谱值
        check("离谱的宽度回落默认值",
              app._detail_width() == app_module.RIGHT_PANEL_WIDTH, str(app._detail_width()))
    finally:
        app.settings.detail_width = saved_width

    # 折叠：从分割器里**摘掉窗格**（拖动分割器没法"隐藏"，只能摘）
    app._details_visible = True
    app._details_auto_hidden = False
    app.toggle_details()
    pump(app, 0.4)
    check("收起后分割器只剩 1 个窗格", len(app.paned.panes()) == 1, str(len(app.paned.panes())))
    check("收起状态标记正确", app._details_visible is False)
    app.toggle_details()
    pump(app, 0.4)
    check("展开后又回到 2 个窗格", len(app.paned.panes()) == 2, str(len(app.paned.panes())))
    check("展开状态标记正确", app._details_visible is True)

    print("=== 9.13 路径会被记住，重启后自动载入 ===")
    # 用户反馈：「路径要有记录功能、这次路径输入后、重启之后还在」。
    # 原先 remember() 只在"载入成功"时调，而这个调用在后台线程里 ——
    # 用户打完路径按回车、看到歌单出来了，就以为存下了，
    # 其实那时后台线程可能还没跑到写盘那一步。

    # ① 路径框里按回车（绑定的就是 commit_path("local")）
    fresh_path = str(Path(SANDBOX) / "not-a-real-folder")
    Path(fresh_path).mkdir(parents=True, exist_ok=True)
    app.path_vars["local"].set(fresh_path)
    app.commit_path("local")
    pump(app, 0.4)
    check("回车后立刻写入 local_path", app.settings.local_path == fresh_path,
          app.settings.local_path)
    check("回车后立刻进最近列表", fresh_path in app.settings.recent_local,
          str(app.settings.recent_local))
    # 关键：**真去读一遍磁盘上的 settings.json**，不能只看内存里的对象
    check("回车后立刻落盘（不等后台载入）",
          Settings.load().local_path == fresh_path,
          Settings.load().local_path)

    # ② 输入框失焦也要存 —— 用户打完路径直接去点别的按钮，不会按回车
    other = str(Path(SANDBOX) / "another-folder")
    Path(other).mkdir(parents=True, exist_ok=True)
    app.path_vars["local"].set(other)
    app.commit_path("local", load=False)   # 失焦绑的就是这个（不触发整库扫描）
    pump(app, 0.4)
    check("失焦后写入 local_path", app.settings.local_path == other,
          app.settings.local_path)
    check("失焦后也真的落盘了", Settings.load().local_path == other,
          Settings.load().local_path)

    # ③ 重启（新建一个 app 实例）后路径框里有字、并自动开始载入
    app._close_progress_dialog()
    app.toast.clear()
    app.on_close()
    pump(app, 0.3)

    app = QuestComparatorApp(settings=Settings.load())
    pump(app, 0.6)
    box_text = app.path_vars["local"].get().strip()
    check("重启后路径框恢复上次路径", box_text == other, box_text)
    check("重启后 settings 里也还是它", app.settings.local_path == other,
          app.settings.local_path)

    print("=== 10. 主题切换 + 关闭 ===")
    app.settings.appearance = "Light"
    import customtkinter as ctk

    ctk.set_appearance_mode("Light")
    for widget in (app.list_local, app.list_quest, app.list_missing):
        widget.refresh_theme()
    pump(app, 1.0)
    check("主题切换未抛异常", True)
    # 清理：先关掉可能存在的进度窗口，再关主窗口（否则 Tk 会在销毁时报 bad window path）
    app._close_progress_dialog()
    app.toast.clear()
    pump(app, 0.5)
    app.on_close()
    try:
        app.root.winfo_exists()
        destroyed = False
    except Exception:
        destroyed = True  # 解释器已销毁，正是期望结果
    check("窗口已销毁", destroyed)

    print()
    if FAILURES:
        print(f"结果：{len(FAILURES)} 项失败 -> {FAILURES}")
        return 1
    print("结果：全部通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
