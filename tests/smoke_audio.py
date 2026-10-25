"""D1 试听功能测试。

## 为什么这组测试要造**真 OGG 文件**

试听踩到的坑（见 `quest_tool/audio.py` 头部注释）有一个共同点：
**它们全都不报错**，只是"没声音"。用假的/空的文件测，会得到
"返回了非 None" 就通过的假绿灯 —— 而实际上设备一帧数据都没收到。

所以这里用 `miniaudio` **真的解一遍**，断言解出来的帧数对得上，
再把生成器手工喂一遍（模拟播放设备拉数据）确认没有回调异常。

## 覆盖范围

1. 找音频文件：`song.egg` / 按歌名命名 / `.ogg` / 找不到
2. 解码：真 OGG 能解出正确帧数；损坏文件返回 None 不抛
3. 限长：请求 N 秒就正好给 N 秒（**交错立体声，差一倍是易错点**）
4. 生成器：预热一次、喂完自动收尾、`on_end` 恰好调一次
5. 播放器状态：`is_playing` 在开播后为真、`stop()` 后为假
6. 播放器不崩：目录没音频 / 文件损坏 / 设备开不起来都返回 None
7. 单例：`player()` 每次都返回同一个，`stop_all()` 不抛
"""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quest_tool import audio  # noqa: E402

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f"  -> {detail}" if detail else ""))
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  [FAIL] {label}" + (f"  -> {detail}" if detail else ""))


def make_wav_bytes(seconds: float, rate: int = 44100) -> bytes:
    """造一段真 WAV（miniaudio 能解），用来做不依赖外部素材的确定性用例。"""
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = int(seconds * rate)
        data = b"".join(
            struct.pack("<hh", int(3000 * math.sin(i * 0.05)), int(3000 * math.sin(i * 0.05)))
            for i in range(frames)
        )
        handle.writeframes(data)
    return buf.getvalue()


print("=" * 76)
print("D1 试听：音频解码与播放")
print("=" * 76)

# ---------------------------------------------------------------- 1. 依赖状态
print("\n--- 1. 依赖状态 ---")
if not audio.available():
    print("  [SKIP] 未安装 miniaudio，本组跳过")
    print("\n结果：跳过")
    sys.exit(0)

check("miniaudio 可用", audio.available() is True)
check("可用时 unavailable_reason() 为空", audio.unavailable_reason() == "")

# ---------------------------------------------------------------- 2. 找音频
print("\n--- 2. 找音频文件 ---")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # 标准命名
    case = root / "case_standard"
    case.mkdir()
    (case / "song.egg").write_bytes(b"x")
    found = audio._find_audio_file(case)
    check("优先找 song.egg", found is not None and found.name == "song.egg",
          found.name if found else "None")

    # 按歌名命名（真机上有 `Daddy.egg` 这种）
    case2 = root / "case_named"
    case2.mkdir()
    (case2 / "Daddy.egg").write_bytes(b"x")
    found = audio._find_audio_file(case2)
    check("没有 song.egg 时按扩展名找", found is not None and found.name == "Daddy.egg",
          found.name if found else "None")

    # .ogg
    case3 = root / "case_ogg"
    case3.mkdir()
    (case3 / "song.ogg").write_bytes(b"x")
    check("认 .ogg", audio._find_audio_file(case3) is not None)

    # 空目录 / 不存在
    empty = root / "case_empty"
    empty.mkdir()
    check("空目录返回 None", audio._find_audio_file(empty) is None)
    check("不存在的目录返回 None", audio._find_audio_file(root / "nope") is None)

    # 只有封面没有音频
    case4 = root / "case_cover_only"
    case4.mkdir()
    (case4 / "cover.png").write_bytes(b"x")
    check("只有封面时返回 None（不误把封面当音频）",
          audio._find_audio_file(case4) is None)

# ---------------------------------------------------------------- 3. 解码
print("\n--- 3. 解码 ---")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    wav = root / "tone.wav"
    wav.write_bytes(make_wav_bytes(2.0))

    result = audio.decode_preview(wav, seconds=2.0)
    check("真音频能解码", result is not None)
    if result:
        samples, rate, channels = result
        check("采样率 44100", rate == 44100, str(rate))
        check("双声道", channels == 2, str(channels))
        got = len(samples) / rate / channels
        check("2 秒音频解出约 2 秒", abs(got - 2.0) < 0.05, f"{got:.2f} 秒")

    # 损坏文件
    bad = root / "bad.egg"
    bad.write_bytes(b"not audio at all" * 100)
    check("损坏文件返回 None 而不是抛异常", audio.decode_preview(bad) is None)

    # 空文件
    blank = root / "blank.egg"
    blank.write_bytes(b"")
    check("空文件返回 None", audio.decode_preview(blank) is None)

    # 不存在的文件
    check("不存在的文件返回 None",
          audio.decode_preview(root / "ghost.egg") is None)

# ---------------------------------------------------------------- 4. 限长
print("\n--- 4. 限长（交错立体声，差一倍是易错点）---")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    long_wav = root / "long.wav"
    long_wav.write_bytes(make_wav_bytes(6.0))

    for want in (1.0, 2.5, 4.0):
        result = audio.decode_preview(long_wav, seconds=want)
        check(f"请求 {want} 秒", result is not None)
        if result:
            samples, rate, channels = result
            got = len(samples) / rate / channels
            check(f"  → 实得 {got:.2f} 秒（误差 <0.05）", abs(got - want) < 0.05,
                  f"{got:.3f}")

    # 请求超过总长 → 给全长，不报错
    result = audio.decode_preview(long_wav, seconds=999.0)
    check("请求超过总长时返回全部", result is not None)
    if result:
        samples, rate, channels = result
        got = len(samples) / rate / channels
        check("  → 实得全长 6 秒", abs(got - 6.0) < 0.05, f"{got:.2f}")

# ---------------------------------------------------------------- 5. 生成器
print("\n--- 5. 流式生成器（模拟播放设备拉数据）---")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    src = root / "stream.wav"
    src.write_bytes(make_wav_bytes(3.0))

    data = src.read_bytes()
    stream = audio._miniaudio.stream_memory(
        data,
        output_format=audio._miniaudio.SampleFormat.SIGNED16,
        nchannels=2,
        sample_rate=44100,
    )
    first = next(stream, None)
    check("第一块非空", bool(first))

    ended: list[int] = []
    chained = audio._filled(
        audio._prepend(first, stream), on_end=lambda: ended.append(1))
    next(chained)  # 预热

    # `_filled` 是"设备驱动"的：必须一直 send() 直到它补满尾部后 return
    # （生成器 return → 下一次 send 抛 StopIteration）。
    # **放完时 on_end 只在 `finally` 里跑**，所以最后那次 send 抛
    # StopIteration 之后才计数。
    steps = 0
    while True:
        try:
            chunk = chained.send(1024)
        except StopIteration:
            break
        if chunk is None:
            break
        steps += 1
        if steps > 5000:
            break

    check("推完生成器后 on_end 恰好调一次", len(ended) == 1, f"调了 {len(ended)} 次")

# ---------------------------------------------------------------- 5b. _filled 必须供满
print("\n--- 5b. _filled：设备要多少就必须给多少（这是“一卡一卡”的根因）---")
# 背景：miniaudio 的 _data_callback 把生成器 yield 的内容**原样拷进输出缓冲**，
# 既不检查够不够也不补零；多余的缓冲是**未初始化内存**。
# 实测默认 buffersize_msec=200 时每次回调要 8820 帧，而 stream_memory()
# 一次只产 1024 帧 → 只有 11.6% 是有效数据 → 听感“一卡一卡”。
with tempfile.TemporaryDirectory() as tmp:
    root2 = Path(tmp)
    src = root2 / "short.wav"
    src.write_bytes(make_wav_bytes(0.05))     # 很短：故意让尾部不足

    # 5b.1 较短的数据 + 一次要 4096 帧 → 必须**补零供满 4096 帧**
    p = audio.stream_preview(src, seconds=0)
    feeder = audio._filled(p)
    next(feeder)                                # 预热
    out = feeder.send(4096)
    check("_filled 供满请求的帧数（不足补零）",
          len(out) == 4096 * 2, f"{len(out)} 元素 / 期望 {4096*2}")
    # 尾部补的必须是**零**（静音），不是残留数据
    tail = out[-(4096 * 2 - len(src.read_bytes())):]
    check("_filled 尾部补的是静音零值",
          all(v == 0 for v in out[-64:]), f"末尾 64 个元素：{list(out[-64:])[:8]}…")
    try:
        feeder.send(4096)
        raised = False
    except StopIteration:
        raised = True
    check("_filled 补满尾部后再要就 StopIteration", raised)

    # 5b.2 够长时每次 send(n) 都恰好 n 帧
    long2 = root2 / "long.wav"
    long2.write_bytes(make_wav_bytes(2.0))
    p2 = audio.stream_preview(long2, seconds=0)
    f2 = audio._filled(p2)
    next(f2)
    sizes = []
    for want in (8820, 4410, 1024, 2646, 100):
        chunk = f2.send(want)
        if chunk is None:
            sizes.append((want, None))
            break
        sizes.append((want, len(chunk) // 2))
        if len(chunk) // 2 != want:
            break
    check("_filled 每次 send(n) 都返回正好 n 帧",
          all(got == want for want, got in sizes), f"{sizes}")
    f2.close()

    # 5b.3 反复 send(0) 不能死循环也不能提前结束（设备偶尔会问 0）
    p3 = audio.stream_preview(long2, seconds=0)
    f3 = audio._filled(p3)
    next(f3)
    zeros_ok = True
    for _ in range(3):
        try:
            f3.send(0)
        except StopIteration:
            zeros_ok = False
            break
    check("_filled 收到 send(0) 不结束、不抛异常", zeros_ok)
    f3.close()

    # 5b.4 预热必须返回 None（miniaudio 要求生成器已启动）
    p4 = audio.stream_preview(long2, seconds=1.0)
    f4 = audio._filled(p4)
    warm = next(f4)
    check("_filled 预热返回 None", warm is None, f"{warm!r}")
    f4.close()

    # 5b.5 真音频 + 真设备默认请求量（8820 帧）：必须一次就供满
    real = audio._find_audio_file(Path(r"D:\Beatsaber\本地")
                                  / "1181f (Overwhelmed - fruhead)")
    if real is not None:
        p5 = audio.stream_preview(real, 1.0)
        f5 = audio._filled(p5)
        next(f5)
        got5 = f5.send(8820)
        check("真 .egg：一次 send(8820) 供满 8820 帧",
              got5 is not None and len(got5) // 2 == 8820,
              f"{0 if got5 is None else len(got5)//2} 帧")
        f5.close()
    else:
        check("真 .egg 可用（本机应该有）", False, "没找到测试文件")

    # 5b.6 play_file() 必须把 _BUFFERSIZE_MSEC 真的传给设备
    check("_BUFFERSIZE_MSEC 已从默认 200ms 降下来",
          audio._BUFFERSIZE_MSEC < 200, f"{audio._BUFFERSIZE_MSEC} ms")

# ---------------------------------------------------------------- 6. 播放器状态
print("\n--- 6. 播放器状态 ---")
with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp) / "song_dir"
    folder.mkdir()
    (folder / "song.egg").write_bytes(make_wav_bytes(4.0))

    audio.stop_all()
    check("初始未播放", audio.player().is_playing() is False)
    check("初始 playing_path 为 None", audio.player().playing_path is None)

    played = audio.player().play_song(folder)
    if played is None:
        check("play_song 返回文件（或无声卡时优雅失败）", True,
              "返回 None —— 本机可能没有可用音频设备")
    else:
        check("play_song 返回音频文件", played.name == "song.egg", played.name)
        check("开播后 is_playing 为真", audio.player().is_playing() is True)
        check("playing_path 指向该文件", audio.player().playing_path == played)

    audio.stop_all()
    check("stop_all 后 is_playing 为假", audio.player().is_playing() is False)
    check("stop_all 后 playing_path 为 None", audio.player().playing_path is None)

    # 连点不同歌不崩
    folder2 = Path(tmp) / "song_dir2"
    folder2.mkdir()
    (folder2 / "song.egg").write_bytes(make_wav_bytes(3.0))
    for _ in range(3):
        audio.player().play_song(folder)
        audio.player().play_song(folder2)
    check("连续切换歌曲不抛异常", True)
    audio.stop_all()

# ---------------------------------------------------------------- 7. 优雅失败
print("\n--- 7. 优雅失败（不能崩主流程）---")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    empty = root / "empty_song"
    empty.mkdir()
    check("目录里没音频 → play_song 返回 None",
          audio.player().play_song(empty) is None)

    broken = root / "broken_song"
    broken.mkdir()
    (broken / "song.egg").write_bytes(b"garbage" * 50)
    check("音频损坏 → play_song 返回 None",
          audio.player().play_song(broken) is None)

    check("不存在的目录 → 返回 None",
          audio.player().play_song(root / "ghost") is None)

    check("stop() 在没播放时调用不报错", audio.player().stop() is None)

# ---------------------------------------------------------------- 8. 单例
print("\n--- 8. 单例 ---")
check("player() 每次都返回同一个对象", audio.player() is audio.player())
check("stop_all() 不抛异常", audio.stop_all() is None)
check("PREVIEW_SECONDS 是正数且合理",
      isinstance(audio.PREVIEW_SECONDS, (int, float)) and 0 < audio.PREVIEW_SECONDS <= 120,
      str(audio.PREVIEW_SECONDS))

# ---------------------------------------------------------------- 9. 流式片段
# 这一节的由来：D1 第一版把"限长"做在解码**之后** —— 为了听 30 秒先把整曲
# 全解进内存再切片。实测 13.66 MB / 369 秒的文件峰值 66 MB、580 ms。
# 改成流式后 13.7 MB / 72 ms，且输出**逐样本一致**。下面几条守着这个性质。
print("\n--- 9. 流式片段（stream_preview）---")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    long_wav = root / "stream_src.wav"
    long_wav.write_bytes(make_wav_bytes(8.0))

    # 9.1 长度正确
    total = sum(len(c) for c in audio.stream_preview(long_wav, seconds=2.0))
    got = total / 44100 / 2
    check("stream_preview 限长 2 秒生效", abs(got - 2.0) < 0.05, f"{got:.3f} 秒")

    # 9.2 与 decode_preview（整曲解码后切片）**逐样本一致**
    #     这条是"改写有没有改变音频内容"的唯一硬证据。
    import array as _array

    ref = audio.decode_preview(long_wav, seconds=2.0)
    check("参考实现在", ref is not None)
    if ref:
        ref_samples, _, _ = ref
        got_samples = _array.array("h")
        for chunk in audio.stream_preview(long_wav, seconds=2.0):
            got_samples.extend(chunk)
        check("流式输出与整曲解码切片**样本数一致**",
              len(got_samples) == len(ref_samples),
              f"{len(got_samples)} vs {len(ref_samples)}")
        check("流式输出与整曲解码切片**逐样本一致**",
              len(got_samples) == len(ref_samples)
              and all(a == b for a, b in zip(got_samples, ref_samples)))

    # 9.3 start= 偏移
    off = _array.array("h")
    for chunk in audio.stream_preview(long_wav, seconds=1.0, start=3.0):
        off.extend(chunk)
    check("start=3.0 / 1.0 秒 → 长度对", abs(len(off) / 2 / 44100 - 1.0) < 0.05,
          f"{len(off)/2/44100:.3f} 秒")
    if ref is not None:
        whole = audio.decode_preview(long_wav, seconds=0)
        if whole:
            expect = whole[0][3 * 44100 * 2: 4 * 44100 * 2]
            check("start= 片段与整曲对应区间一致",
                  len(off) == len(expect) and all(a == b for a, b in zip(off, expect)))

    # 9.4 seconds=0 表示不限长
    n = sum(len(c) for c in audio.stream_preview(long_wav, seconds=0))
    check("seconds=0 → 不限长（返回整曲）", abs(n / 2 / 44100 - 8.0) < 0.1,
          f"{n/2/44100:.2f} 秒")

    # 9.5 边界：不存在 / 损坏 / 空文件都必须**不抛异常**且产出 0 样本
    check("不存在的文件产出 0 样本",
          list(audio.stream_preview(root / "ghost.egg")) == [])
    bad = root / "bad_stream.egg"
    bad.write_bytes(b"this is definitely not audio" * 50)
    check("损坏文件产出 0 样本且不抛异常",
          sum(len(c) for c in audio.stream_preview(bad)) == 0)
    blank = root / "blank_stream.egg"
    blank.write_bytes(b"")
    check("空文件产出 0 样本且不抛异常",
          sum(len(c) for c in audio.stream_preview(blank)) == 0)

    # 9.6 _find_audio_file / play_song 必须同时接受 str 和 Path
    #     曾被这个绊到：注解写了 Path，内部直接 `folder.is_dir()`，
    #     传字符串就 AttributeError: 'str' object has no attribute 'is_dir'
    song_dir = root / "accept_test"
    song_dir.mkdir()
    (song_dir / "song.egg").write_bytes(make_wav_bytes(2.0))
    check("_find_audio_file 接受 Path",
          audio._find_audio_file(song_dir) is not None)
    check("_find_audio_file 接受 str（原先会 AttributeError）",
          audio._find_audio_file(str(song_dir)) is not None)
    check("play_song 接受 str 路径",
          audio.player().play_song(str(song_dir)) is not None)
    audio.stop_all()

    # 9.7 _filled 丢弃时必须关掉 rest（否则解码器要等 GC 才释放）
    class _Watched:
        """转发给真生成器，只记录 close() 次数。"""

        def __init__(self, gen):
            self._gen = gen
            self.closed = 0

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._gen)

        def close(self):
            self.closed += 1
            return self._gen.close()

    real_preview = audio.stream_preview
    watched_holder: list[_Watched] = []

    def _spy(*a, **kw):
        w = _Watched(real_preview(*a, **kw))
        watched_holder.append(w)
        return w

    audio.stream_preview = _spy
    try:
        p = audio.stream_preview(long_wav, 3.0)
        next(p)
        filled = audio._filled(p)
        next(filled)
        filled.send(1024)
        filled.close()  # 模拟设备被 stop 后生成器被丢弃
        check("_filled 丢弃时 close 了 rest",
              watched_holder[-1].closed == 1,
              f"closed={watched_holder[-1].closed}")
    finally:
        audio.stream_preview = real_preview

    # 9.8 连续切换不积压（20 次全部关掉）
    audio.stream_preview = _spy
    try:
        watched_holder.clear()
        for _ in range(20):
            p = audio.stream_preview(long_wav, 3.0)
            next(p)
            ch = audio._filled(p)
            next(ch)
            ch.close()
        check("连续切 20 首 → 20 个生成器全部关闭",
              sum(w.closed for w in watched_holder) == 20,
              f"{sum(w.closed for w in watched_holder)}/20")
    finally:
        audio.stream_preview = real_preview

    # 9.9 on_end 恰好一次（放完 / 丢弃两条路径各一次）
    for label, discard in (("放完", False), ("丢弃", True)):
        ended2: list[int] = []
        p = real_preview(long_wav, 1.0)
        ch = audio._filled(p, on_end=lambda e=ended2: e.append(1))
        next(ch)
        if discard:
            ch.close()
        else:
            while True:
                try:
                    if ch.send(1024) is None:
                        break
                except StopIteration:
                    break
        check(f"_filled {label} 路径 on_end 恰好一次",
              len(ended2) == 1, f"{len(ended2)} 次")

# ---------------------------------------------------------------- 10. str 路径
print("\n--- 10. 传字符串路径不能崩（真实缺陷：83/85 首全中）---")
# `_find_audio_file()` 返回的可能是 str，调用方顺手把它交给解码函数很自然。
# 但三个入口原先都用 `source.name` / `source.read_bytes()` —— 传 str 就
# AttributeError，且被 except 兜住返回 None，**一声不响**。
# 实测：D:\Beatsaber\本地 的 85 首真歌里 83 首全解不出来，界面表现为"试听没反应"。
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    wav = root / "tone.wav"
    wav.write_bytes(make_wav_bytes(2.0))
    as_str = str(wav)

    result = audio.decode_preview(as_str, seconds=2.0)
    check("decode_preview 接受 str", result is not None)
    if result:
        samples, rate, channels = result
        got = len(samples) / rate / channels
        check("传 str 解出的时长与传 Path 一致", abs(got - 2.0) < 0.05, f"{got:.2f} 秒")

    check("probe_duration 接受 str", audio.probe_duration(as_str) is not None)

    chunks = list(audio.stream_preview(as_str, seconds=2.0))
    total = sum(len(c) for c in chunks)
    check("stream_preview 接受 str", total > 0, f"{len(chunks)} 块 / {total} 样本")

    # 与 Path 版本必须逐样本一致（否则"能播了"但内容变了）
    ref = b"".join(audio.stream_preview(wav, seconds=2.0))
    got_bytes = b"".join(bytes(c) for c in chunks)
    check("str 与 Path 产出逐字节一致", got_bytes == ref,
          f"{len(got_bytes)} vs {len(ref)}")

    # _find_audio_file 也要两种都收
    song = root / "Some Song"
    song.mkdir()
    (song / "song.egg").write_bytes(make_wav_bytes(1.0))
    check("_find_audio_file 接受 str", audio._find_audio_file(str(song)) is not None)
    check("_find_audio_file 接受 Path", audio._find_audio_file(song) is not None)

    # play_song 传 str 目录同样要能播
    player = audio.PreviewPlayer()
    played = player.play_song(str(song))
    check("play_song 接受 str 目录", played is not None, played)
    player.stop()

# ---------------------------------------------------------------- 11. 播放位置与量程
print("\n--- 11. 播放位置 / 起点偏移 / 时长档位 ---")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    folder = root / "Progress Song"
    folder.mkdir()
    (folder / "song.egg").write_bytes(make_wav_bytes(8.0))

    # 档位规范化：非法值一律回落到 30，合法档位原样保留
    check("档位 (30,60,120,0) 都在表里",
          audio.PREVIEW_LENGTH_CHOICES == (30, 60, 120, 0),
          str(audio.PREVIEW_LENGTH_CHOICES))
    for raw, want in ((-5, 30), (0, 0), (30, 30), (60, 60), (120, 120),
                      (55, 30), (999, 30), ("abc", 30), (None, 30), (30.7, 30)):
        got = audio.normalize_preview_seconds(raw)
        check(f"normalize({raw!r}) == {want}", got == want, f"得到 {got}")

    check("format(0) 说「整曲」", audio.format_preview_length(0) == "整曲",
          audio.format_preview_length(0))
    check("format(30) 说「30 秒」", audio.format_preview_length(30) == "30 秒",
          audio.format_preview_length(30))

    player = audio.PreviewPlayer()
    check("未播放时 position() 是 0", player.position() == 0.0)

    played = player.play_song(folder, seconds=5.0)
    check("play_song 成功", played is not None)

    # position() 必须真的会走：播一会儿再读，应当 >0 且 <= 量程
    import time as _time
    _time.sleep(0.35)
    pos = player.position()
    check("播放中 position() > 0", pos > 0.0, f"{pos:.3f}s")
    check("播放中 position() 不超过时长", pos <= 5.0, f"{pos:.3f}s")
    check("limit_seconds() = 5", player.limit_seconds() == 5.0,
          str(player.limit_seconds()))
    # `start_offset` 是 **property**（不是方法）—— 写 `()` 会得到
    # `TypeError: 'float' object is not callable`。而那个异常如果发生在
    # 音频回调线程里，主线程还会在解释器退出时等它 → 看起来像「卡死」。
    check("start_offset 是 property 不是方法",
          isinstance(type(player).start_offset, property))
    check("start_offset 初次为 0", player.start_offset == 0.0,
          str(player.start_offset))

    player.stop()
    frozen = player.position()
    check("stop() 后 position() 保留在停下的位置", frozen > 0.0, f"{frozen:.3f}s")
    # 停下之后不能再涨（否则进度条会一直往前跑）
    _time.sleep(0.25)
    check("stop() 后 position() 不再增长", player.position() == frozen,
          f"{frozen:.3f} -> {player.position():.3f}")

    # 从 2 秒处起播：position() 应当是 2 + 已播时长
    played = player.play_song(folder, seconds=3.0, start=2.0)
    check("带 start 起播成功", played is not None)
    check("start_offset = 2.0", player.start_offset == 2.0,
          str(player.start_offset))
    _time.sleep(0.3)
    pos = player.position()
    check("position() 从起点偏移算起（>=2.0）", pos >= 2.0, f"{pos:.3f}s")
    check("position() 不超过 起点+量程（<=5.0）", pos <= 5.0, f"{pos:.3f}s")
    player.stop()

    # 整曲档位：limit 为 0，表示"不限长"
    played = player.play_song(folder, seconds=0)
    check("seconds=0（整曲）能播", played is not None)
    check("seconds=0 时 limit_seconds() = 0", player.limit_seconds() == 0.0,
          str(player.limit_seconds()))
    player.stop()

    # 换歌要重置位置，不能把上一首的进度带过来
    other = root / "Another Song"
    other.mkdir()
    (other / "song.egg").write_bytes(make_wav_bytes(4.0))
    player.play_song(folder, seconds=5.0)
    _time.sleep(0.25)
    player.play_song(other, seconds=5.0)
    check("换歌后 position() 归零重算", player.position() < 0.3,
          f"{player.position():.3f}s")
    player.stop()

    check("playing_path 清空", player.playing_path is None)

# ------------------------------------------------ 12. stop() 不许阻塞界面线程
# 这一节的由来：`device.stop()` 内部要 join 音频回调线程，实测一次 100~600 ms
# （约为 buffersize_msec 的 2.5 倍）。而 `stop()` 是在**界面线程**上同步调的 ——
# 拖动进度条改起点、切歌、关窗口都会走到它，所以每次操作卡半秒多。
# 现在 `PreviewPlayer.stop()` 把关停丢进后台线程，本机实测 600ms -> 0.5ms。
print("\n-- 12. stop() 不许阻塞界面线程 --", flush=True)
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    folder = root / "Stop Song"
    folder.mkdir()
    (folder / "song.egg").write_bytes(make_wav_bytes(8.0))

    player = audio.PreviewPlayer()
    worst = 0.0
    for _ in range(5):
        player.play_song(folder, seconds=6.0)
        _time.sleep(0.25)                 # 让它真的响起来（闲置时 stop 本来就快）
        started = _time.perf_counter()
        player.stop()
        worst = max(worst, (_time.perf_counter() - started) * 1000)
    # 阈值给得比实测(0.5ms)宽，但远低于同步 stop 的 100ms 下限：
    # 退回同步实现时这里必然失败。
    check("真在播时 stop() 也不阻塞（5 次都 < 50ms）", worst < 50.0,
          f"最慢 {worst:.1f} ms")

    # 设备必须已经被摘走：否则下一次 play 会和正在关停的旧设备抢音频端点
    check("stop() 后 _device 已摘走", player._device is None)
    check("stop() 后 is_playing() 为 False", not player.is_playing())

    # 后台关停不能把解释器退出拖住 —— 线程必须是 daemon
    player.play_song(folder, seconds=6.0)
    _time.sleep(0.25)
    started = _time.perf_counter()
    player.stop()
    check("stop() 立即返回（< 20ms）",
          (_time.perf_counter() - started) * 1000 < 20.0)

# ------------------------------------------------ 13. 拖动进度条：量程与目标位置
# 这一节的由来（用户报「试听又不行了」的真正原因，都在界面层）：
#   ① 拖到 50% 后量程从 30 秒变成 45 秒，再拖变 52.5、56.25 —— 越拖越偏；
#   ② 目标位置忘了加上「量程起点」，拖到 50% 落到 22.5 秒而不是 30 秒。
# 两条都**不会报错**，只是行为悄悄变坏，所以必须逐项钉住。
#
# 这里复刻界面层的算法（`app_detail._on_seek_drag/_on_seek_release`），
# 用真实的 `PreviewPlayer` 状态，验证「拖 N 次 50%」的收敛性。
print("\n-- 13. 拖动进度条的换算（量程不漂 / 目标含起点）--", flush=True)
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    folder = root / "Seek Song"
    folder.mkdir()
    # 文件要**比连拖 3 次累计到的位置更长**：连拖 3 次 50% 会走到 15/30/45 秒，
    # 而每次都要往后放 30 秒，所以最后一段落在 45~75 秒。
    # 之前只用 30 秒的文件，第 2 次 play(30.0) 就已经越过了文件末尾 ——
    # `stream_preview` 正确地返回空、`play_song` 返回 None，
    # 于是状态停在上一段，看起来像"起点不前进"的假失败。
    (folder / "song.egg").write_bytes(make_wav_bytes(90.0))

    player = audio.PreviewPlayer()
    LENGTH = 30.0          # 界面层的预览档位（30 秒）
    SPAN = 0.0             # 「按下那一刻冻结」的量程


    def preview_span(player) -> float:
        """复刻 `app_detail._preview_span()`：有终点用终点，否则用起点兜底。"""
        end = player.end_offset()
        if end > 0:
            return max(0.1, end)
        return max(0.1, player.start_offset)

    # **必须等上一次播放关停干净**：`stop()` 把设备交给后台线程去关（100~600ms），
    # 而它只摘走 `_device` 引用、**不清 `_origin`**。所以紧接着再 play 时，
    # 若后台还没关完，新设备会和正在关停的旧设备抢音频端点，实测 play 会失败
    # （返回 None）而状态仍停留在上一段 → 起点看着"不前进"。
    # 真实界面里两次拖动之间隔着用户手速（几百毫秒），不会撞上；
    # 本测试跑得快，所以要显式等一下。
    def play_and_settle(seconds: float, start: float) -> None:
        played = player.play_song(folder, seconds=seconds, start=start)
        assert played is not None, f"play_song(seconds={seconds}, start={start}) 返回 None"
        _time.sleep(0.25)

    play_and_settle(LENGTH, 0.0)
    check("起播后 end_offset 就是量程（30 秒档 = 30.0）",
          player.end_offset() == 30.0, str(player.end_offset()))

    # ——— 拖 3 次「50%」：起点必须稳定前进 15 秒，量程恒为「起点 + 30」 ———
    origins = []
    spans = []
    for i in range(3):
        # --- 拖动（复刻 `_on_seek_drag`）---
        SPAN = preview_span(player)            # 只在按下时取一次
        ratio = 0.5
        begin_at_press = player.start_offset
        # --- 松手（复刻 `_on_seek_release`）---
        end = SPAN
        begin = player.start_offset
        target = begin + ratio * max(0.0, end - begin)
        play_and_settle(LENGTH, target)
        origins.append(player.start_offset)
        spans.append(player.end_offset())
        check(f"第{i + 1}次拖 50%：起点 {begin_at_press:.1f} -> {target:.1f}"
              f"（量程 {end:.1f}）", abs(player.start_offset - target) < 0.01,
              f"实际 {player.start_offset:.2f}")

    check("连拖 3 次 50% 起点是 15/30/45（线性前进，不漂）",
          all(abs(got - want) < 0.01 for got, want in zip(origins, (15.0, 30.0, 45.0))),
          str([round(o, 2) for o in origins]))
    check("连拖 3 次 50% 量程是 45/60/75（= 起点 + 30）",
          all(abs(got - want) < 0.01 for got, want in zip(spans, (45.0, 60.0, 75.0))),
          str([round(s, 2) for s in spans]))
    # 量程永远是「起点 + 时长」—— 这是界面的不变式，破了就会出现 `0:16 / 0:45` 那种漂
    check("每次量程 == 起点 + 30（不变式）",
          all(abs(sp - (og + LENGTH)) < 0.01 for og, sp in zip(origins, spans)),
          str([(round(o, 2), round(s, 2)) for o, s in zip(origins, spans)]))

    # 拖到 0% 就是回到片段开头，量程不该跟着缩（缩了就是又拿 limit 当终点了）
    play_and_settle(LENGTH, 0.0)
    player.play_song(folder, seconds=LENGTH, start=0.0 + 0.0 * 30.0)
    check("拖到 0% 时起点仍是 0、量程仍是 30",
          player.start_offset == 0.0 and player.end_offset() == 30.0,
          f"起点 {player.start_offset} 量程 {player.end_offset()}")

    # 拖到 100%：目标 = 量程终点，此时应当当作「放完了」（界面层回退到 _finish）
    _time.sleep(0.25)
    play_and_settle(LENGTH, 15.0)
    end = preview_span(player)                 # 45.0
    target = 15.0 + 1.0 * (end - 15.0)
    check("拖到 100% 目标落在量程终点（== 起播段的终点）",
          abs(target - end) < 0.01, f"{target:.2f} vs {end:.2f}")

    # 「整曲」档：没有终点，量程必须靠真实总长，不能退回 limit(0)
    player.play_song(folder, seconds=0, start=0.0)
    check("整曲档 end_offset() == 0（没有终点）", player.end_offset() == 0.0,
          str(player.end_offset()))
    check("整曲档 limit_seconds() == 0", player.limit_seconds() == 0.0,
          str(player.limit_seconds()))
    player.stop()
    check("拖动测试收尾后 playing_path 已清空", player.playing_path is None)

# Final PCM gain must be functional, not just a decorative slider.
import array as _gain_array
_gain_level = [0.5]
_gain_stream = audio._filled(iter([_gain_array.array("h", [1000, -1000, 2000, -2000])]),
                             volume=lambda: _gain_level[0])
next(_gain_stream)
_gain_out = _gain_stream.send(1)
check("音量 50% 确实缩放 PCM 并保持帧数", list(_gain_out) == [500, -500])
_gain_level[0] = 0.0
_gain_out = _gain_stream.send(1)
check("播放中音量降为 0 输出静音", list(_gain_out) == [0, 0])
_gain_stream.close()
_gain_player = audio.PreviewPlayer()
_gain_player.set_volume(-1)
check("音量下界夹到 0", _gain_player.volume() == 0.0)
_gain_player.set_volume(2)
check("音量上界夹到 1，避免额外放大", _gain_player.volume() == 1.0)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 76)
if FAIL:
    print(f"结果：{PASS}/{PASS + FAIL} 项通过，{FAIL} 项失败")
    for name in FAILURES:
        print(f"  - {name}")
    sys.exit(1)
print(f"结果：{PASS}/{PASS + FAIL} 项通过")
