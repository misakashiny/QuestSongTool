"""音频解码与试听播放（Beat Saber 的 `.egg` 其实是 OGG Vorbis）。

## 为什么需要这个模块

Beat Saber 的歌曲音频文件叫 `song.egg`，**内容就是 OGG Vorbis**，
但扩展名不是 `.ogg`。因此：

- Windows 自带的 MCI（`winmm.dll`）不认它 —— 实测打不开；
- `miniaudio` 的**路径 API 也不认** —— `get_file_info(路径)` / `decode_file(路径)`
  会**按扩展名猜格式**，`.egg` 不在它的表里，直接抛
  `DecodeError: unsupported file format`（5 个真文件 100% 复现）。

把同一个文件复制成 `.ogg` 后立刻成功（44100Hz / 2ch / 161.3s），
**所以问题在扩展名，不在文件内容**。

结论：**必须绕开路径 API，读成字节走 `miniaudio.decode(bytes)`**。
那条路已验证可用（161 秒整曲解码约 293 ms）。

## 依赖是软性的

`miniaudio` 只在本模块被真正调用时才 import。**没装也不影响程序启动**，
只是试听功能会被禁用（`available()` 返回 False），界面给出明确提示。
这样打包/部署时不会因为少一个可选库就整程序起不来。

## 线程约定

`decode_preview()` 会阻塞（整曲约 0.3 秒），**必须在工作线程里调**。
`PreviewPlayer` 的 `start()`/`stop()` 是轻量的，但 `stop()` 内部会 join
播放线程，为稳妥起见也一并放工作线程或不阻塞的路径上调用。
"""

from __future__ import annotations

import array
import io
import threading
import time
from pathlib import Path

from .logging_setup import get_logger

logger = get_logger("audio")

#: 试听片段长度（秒）—— **只是默认值**，用户可以改（设置里"试听时长"）。
#:
#: 为什么默认 30 而不放整曲：`stream_preview()` 是流式的，放整曲也不费内存，
#: 但试听的目的就是"这是哪首歌"，30 秒足够；而**列表里连续切歌**时
#: 每首都从头放很久会让人不得不一首首手动停。
#:
#: 用户可以选 30 / 60 / 120 / 0（0 = 整曲），见 `PREVIEW_LENGTH_CHOICES`。
PREVIEW_SECONDS = 30.0

#: 设置里"试听时长"的可选档位（秒）。0 表示放完整首。
PREVIEW_LENGTH_CHOICES: tuple[int, ...] = (30, 60, 120, 0)

#: 校验：把任意值收敛到合法档位，非法值回到 30。
def normalize_preview_seconds(value) -> int:
    """把设置里的"试听时长"收敛成合法档位（秒）。

    **必须是 4 个档位之一**（30/60/120/0），其余一律回到 30。
    挡的是手改 settings.json 写进 `-1` / `1e9` / `"abc"` 这类值
    —— 负数会让 `stream_preview` 的 `seconds > 0` 判断走成"不限长"
    （语义反了），超大值又会让人以为程序卡住。
    """
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return int(PREVIEW_SECONDS)
    return seconds if seconds in PREVIEW_LENGTH_CHOICES else int(PREVIEW_SECONDS)


def format_preview_length(seconds: int) -> str:
    """把秒数写成人看的样子：`0` → `整曲`，其余 → `30 秒`。"""
    return "整曲" if seconds <= 0 else f"{seconds} 秒"

#: 解码目标格式。固定成 44100/16bit/双声道，省去播放时重采样。
_TARGET_RATE = 44100
_TARGET_CHANNELS = 2

#: 播放设备的缓冲长度（毫秒）。
#:
#: 两个数字都实测过（Windows / WASAPI 共享模式，真 `song.egg`）：
#:
#: * 它**决定设备每次回调要多少帧**：20ms→882 / 30ms→1323 / 50ms→2205 /
#:   100ms→4410 / **默认 200ms→8820**；
#: * 它**决定 `stop()` 要等多久**（≈ 2.5 × 该值）：200ms→约 600ms、
#:   50ms→约 105ms。
#:
#: 而 `stop()` 是**在界面线程同步调的**（切歌、停止、关窗口），
#: 所以默认值会让每次停止都卡半秒多。取 60ms 兼顾两者。
_BUFFERSIZE_MSEC = 60


#: 每个样本（`array('h')` 的一个元素）占的字节数 —— **只用于补零**。
_BYTES_PER_SAMPLE = 2

#: 一帧的元素数 = 声道数。
#:
#: **实测过的单位坑，写在这里免得再踩**：`stream_memory()` 产出的是
#: `array.array('h')`（**元素序列，不是字节串**，实测 `typecode='h'`、`len()==2048`）。
#: 所以 `len(chunk)` 是**元素数**，而**一帧 = `_TARGET_CHANNELS` 个元素**，
#: 两者只差一个声道倍数 —— 与"每样本 2 字节"**无关**。
#:
#: v17.3 我曾经把它当成"字节串、一帧 4 字节"去改，于是整个片段被算短一半
#: （`// 4` 而不是 `// 2`）。**原有代码本来就是对的。**
#: 判据：整曲 14229124 个元素 ÷ 2 = 7114562 帧，与权威
#: `ma.decode().num_frames` 完全一致；测试里也是按 `total / 44100 / 2` 反推秒数。
_ELEMENTS_PER_FRAME = _TARGET_CHANNELS

_import_error: str | None = None
_miniaudio = None


def _load():
    """惰性导入 miniaudio，失败时记住原因。"""
    global _miniaudio, _import_error
    if _miniaudio is not None or _import_error is not None:
        return _miniaudio
    try:
        import miniaudio  # noqa: PLC0415  (必须惰性导入：这是可选依赖)
    except Exception as exc:  # pragma: no cover - 取决于环境
        _import_error = str(exc)
        logger.info("未安装 miniaudio，试听功能不可用：%s", exc)
        return None
    _miniaudio = miniaudio
    return _miniaudio


def available() -> bool:
    """试听是否可用（miniaudio 是否装上了）。"""
    return _load() is not None


def unavailable_reason() -> str:
    """不可用时的原因，给界面显示用。"""
    _load()
    return _import_error or ""


def _find_audio_file(folder: Path | str) -> Path | None:
    """在歌曲目录里找音频文件。

    Beat Saber 的音频扩展名不固定：常见 `song.egg`，也有用歌名命名的
    （`Daddy.egg`），还有少数用 `.ogg` / `.wav`。所以按**扩展名**找，
    而不是只认 `song.egg`。

    **收 `Path` 也收 `str`**：调用方传进来的路径可能来自
    `Location.root / folder`（Path）也可能来自别处的字符串拼接，
    早先只写 `Path` 注解、内部直接 `folder.is_dir()`，传字符串就
    `AttributeError: 'str' object has no attribute 'is_dir'`。
    这里显式转一次，别让类型注解当运行时保证。
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    wanted = (".egg", ".ogg", ".wav", ".mp3", ".flac")
    # 先看固定名，命中率最高
    for name in ("song.egg", "song.ogg"):
        cand = folder / name
        if cand.is_file():
            return cand
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and entry.suffix.lower() in wanted:
            return entry
    return None


def probe_duration(source: Path | str | bytes) -> float | None:
    """优先读取头尾中的时长；不支持的格式才用旧单曲解码回退。"""
    from .song_duration import duration_from_parts, local_song_duration, HEAD_LIMIT, TAIL_LIMIT
    if isinstance(source, bytes):
        quick = duration_from_parts(source[:HEAD_LIMIT], source[-TAIL_LIMIT:])
    else:
        path = Path(source)
        quick = local_song_duration(path.parent, {"_songFilename": path.name})
    if quick > 0:
        return quick
    ma = _load()
    if ma is None:
        return None
    try:
        if isinstance(source, bytes):
            data = source
        else:
            data = Path(source).read_bytes()
        decoded = ma.decode(
            data,
            output_format=ma.SampleFormat.SIGNED16,
            nchannels=_TARGET_CHANNELS,
            sample_rate=_TARGET_RATE,
        )
        return decoded.num_frames / float(_TARGET_RATE)
    except Exception as exc:
        logger.debug("探测时长失败：%s", exc)
        return None


def decode_preview(source: Path | str, seconds: float = PREVIEW_SECONDS):
    """解码前 `seconds` 秒，返回 `(samples, sample_rate, channels)`。

    `samples` 是 `array('h')`（16 位有符号整数，交错立体声），可以直接
    交给 `miniaudio.PlaybackDevice`。

    失败（文件不存在 / 不是 OGG / 解码错）返回 `None`。

    ## 只用于**探测/一次性取片段**，播放走 `stream_preview()`

    这个函数会把片段**一次性装进 `array.array`**，所以别在列表里循环调。
    真要播放请用 `stream_preview()`（边解边喂，峰值内存接近 0）。
    """
    ma = _load()
    if ma is None:
        return None
    # **必须显式转 Path**：`_find_audio_file()` 已经允许传 str 了，
    # 调用方顺手把它的返回值（可能是 str）再传进来很自然；
    # 而这里用 `source.name` 记日志 → 传 str 就 AttributeError。
    # 更糟的是它被下面的 except 兜住，返回 None 且**一声不响** ——
    # 表现为"试听没反应"，实测 83/85 首真歌全中。
    source = Path(source)
    try:
        data = source.read_bytes()
        decoded = ma.decode(
            data,
            output_format=ma.SampleFormat.SIGNED16,
            nchannels=_TARGET_CHANNELS,
            sample_rate=_TARGET_RATE,
        )
    except Exception as exc:
        logger.info("解码失败 %s：%s", source.name, exc)
        return None

    samples = decoded.samples
    # **注意：samples 是交错立体声**，一帧 = 声道数个样本。
    # 按样本数截断会把时长算错一倍（实测：161 秒的歌切成了 15 秒
    # 而不是 30 秒 —— 因为 30 秒需要 30*44100*2 个样本，不是 30*44100）。
    frames_wanted = int(seconds * _TARGET_RATE) if seconds > 0 else None
    if frames_wanted is not None and frames_wanted < decoded.num_frames:
        samples = samples[: frames_wanted * _TARGET_CHANNELS]
    return samples, _TARGET_RATE, _TARGET_CHANNELS


def _seekable_memory(ma, data):
    """Public miniaudio stream source: seek encoded bytes without keeping a file open."""
    class MemorySource(ma.StreamableSource):
        def __init__(self):self.buffer=io.BytesIO(data)
        def read(self, num_bytes):return self.buffer.read(num_bytes)
        def seek(self, offset, origin):
            try:self.buffer.seek(offset,origin.value);return True
            except (OSError,ValueError):return False
        def close(self):
            self.buffer.close();self.ffi_handle=ma.ffi.NULL
    return MemorySource()


def stream_preview(
    source: Path | str,
    seconds: float = PREVIEW_SECONDS,
    *,
    start: float = 0.0,
):
    """**边解边喂**的生成器，最多产出 `seconds` 秒（默认 30 秒）。

    产出的是 `array('h')` 小块（16 位交错立体声，44100/2ch），
    可以直接交给 `miniaudio.PlaybackDevice.start()`。

    ## 为什么必须用它，而不是"先解好再播"

    早先的实现是 `ma.decode(整曲) → 切前 30 秒`：**为了听 30 秒把整曲全解了**。
    实测（13.66 MB / 369 秒的 Rap God，`tracemalloc` 峰值）：

    | 做法 | 耗时 | 峰值内存 |
    |---|---|---|
    | 整曲解码再切片（旧） | 580 ms | **66.0 MB** |
    | `decode_preview()`（同样整曲解，保留的旧接口） | 584 ms | **84.7 MB** |
    | `stream_preview()` 解到 30 秒就停（本函数，播放路径） | 72 ms | **13.7 MB** |

    即 **8× 快**；峰值内存从源文件的 4.8 倍降到 **1.0 倍**
    （13.7 MB 就是 `read_bytes()` 那一份缓冲本身，解码增量几乎为零）。
    在 216 首里连续切歌时，旧做法每首都留 66 MB 的瞬时峰值。

    ## 三个实现要点

    ① **收字节而不是路径**：`stream_file(路径)` 会按扩展名猜格式，
       `.egg` 不在 miniaudio 的表里 → `DecodeError: unsupported file format`。
       所以先 `read_bytes()`（这步是源文件本身大小，约 4~13 MB，无法避免），
       再喂 `stream_memory(字节)`。

    ② **够了就 `return`**：生成器 return 会把 `GeneratorExit` 送进
       `stream_memory`，它的 `finally` 负责把底层解码器放掉。
       不要用 `break` 然后不管 —— 那样解码器要等 GC 才释放。

    ③ **按帧数切，不能按样本数**：一帧 = 声道数个样本，
       按样本数切会把片段长度算错一倍（详见 `decode_preview()` 的注释）。
    """
    ma = _load()
    if ma is None:
        return
    source = Path(source)  # 同 decode_preview：允许调用方传 str
    try:
        data = source.read_bytes()
    except OSError as exc:
        logger.info("读音频文件失败 %s：%s", source.name, exc)
        return

    stream = None;seek_source = None
    try:
        options=dict(output_format=ma.SampleFormat.SIGNED16,nchannels=_TARGET_CHANNELS,sample_rate=_TARGET_RATE)
        if start > 0:
            # Decoding and discarding every preceding OGG frame made a mid-song
            # resume take tens of seconds. Seek in the decoder instead. A memory
            # source also lets PreviewClient remove its temporary file on Windows.
            seek_source=_seekable_memory(ma,data)
            stream=ma.stream_any(seek_source,seek_frame=max(0,int(start*_TARGET_RATE)),**options)
        else:stream=ma.stream_memory(data,**options)
    except Exception as exc:
        if seek_source is not None:seek_source.close()
        logger.info("创建解码流失败 %s：%s", source.name, exc)
        return

    want_frames = int(seconds * _TARGET_RATE) if seconds > 0 else None
    produced = 0
    try:
        for chunk in stream:
            if not chunk:
                continue
            # **chunk 是 `array('h')` 元素序列**，一帧 = `_TARGET_CHANNELS` 个元素。
            # 别再"顺手改成按字节算" —— v17.3 试过，把整段算短了一半。
            frames = len(chunk) // _TARGET_CHANNELS
            if want_frames is not None and produced + frames > want_frames:
                cut = want_frames - produced
                if cut <= 0:
                    break
                chunk = chunk[: cut * _TARGET_CHANNELS]
                frames = cut
            produced += frames
            yield chunk
            if want_frames is not None and produced >= want_frames:
                break
    except Exception as exc:
        # 损坏的 OGG / 不支持的编码：已经产出的部分照常放，别再往下解。
        logger.info("解码中断 %s：%s", source.name, exc)
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception as exc:  # pragma: no cover
                logger.debug("关闭解码流出错：%s", exc)
        if seek_source is not None:seek_source.close()


def _close_device(device) -> None:
    """关掉播放设备。**调用方必须已经离开音频回调线程。**

    `device.stop()` 内部要 join 音频回调线程 —— 在回调线程里调它就是
    自锁死（实测挂住 2 分钟不返回，只能 kill）。这就是本函数
    **不能**从 `_filled(on_end=...)` 里调的原因（那个回调就跑在回调线程上）。

    > **曾经错在这里**：本函数的 docstring 一度写着「在独立线程里关掉播放设备」，
    > 但函数体一直是**同步**调用 `device.stop()`。文档描述的是意图，代码做的是
    > 另一回事 —— 而文档反而替这个不一致打了掩护。真正的约定记在这里：
    > 关闭是同步的，所以**别在界面线程上调**（实测一次约 100~600 ms，
    > 取决于 `buffersize_msec`）。`PreviewPlayer.stop()` 已经改成后台线程关。
    """
    try:
        device.stop()
        device.close()
    except Exception as exc:  # pragma: no cover - 关闭失败不影响主流程
        logger.debug("关闭播放设备时出错：%s", exc)


def _prepend(first, rest):
    """把已经取出的一块接回生成器开头。

    `play_file()` 会先 `next(preview)` 一次以便**在 worker 线程上**暴露解码错误，
    但那一块不能丢，所以要还回去。与 `_filled()` 配合使用。
    """
    if first:
        yield first
    for chunk in rest:
        yield chunk


def _filled(rest, on_end=None, volume=None):
    """把 `rest` 的小块攒成"**设备要多少给多少**"。

    ## 为什么必须攒（这是"一卡一卡"的真正根因）

    `miniaudio` 的 `_data_callback`（`miniaudio.py` 的 `PlaybackDevice`）是这样写的：

    ```python
    samples = self.callback_generator.send(framecount)
    samples_bytes = _bytes_from_generator_samples(samples)
    if samples_bytes:
        ffi.memmove(output, samples_bytes, len(samples_bytes))
    ```

    它把生成器**这一次 yield 的内容原样拷进输出缓冲的开头**，然后**不管了**：
    既不检查够不够，也不把剩下的补零。输出缓冲剩下的部分是
    **未初始化内存**。所以只要你供的帧数少于 `framecount`，
    每次回调就有 `(1 - 供给/需求)` 比例的缓冲是垃圾 → 听感就是"一卡一卡"。

    ## 实测数字（真 `song.egg`，Windows / WASAPI 共享模式）

    | `buffersize_msec` | 设备每次回调要的帧数 |
    |---|---|
    | 20 | 882 |
    | 30 | 1323 |
    | 50 | 2205 |
    | 100 | 4410 |
    | **200（miniaudio 默认）** | **8820** |

    而 `stream_memory()` 一次只产 **1024 帧**（2048 个 `array('h')` 元素）。
    也就是说默认配置下每次回调只有 **11.6%** 是有效数据，**88.4% 是垃圾**。
    这既解释了"有声音但一卡一卡"，也解释了为什么换成真音频文件才暴露
    （原来的单元测试用 8 秒的合成 WAV，从不真正开声卡）。

    ## 协议

    设备是**生成器驱动**的：它调 `send(framecount)` 问你要多少个**帧**。
    所以本生成器第一次 `next()` 只完成预热（返回 `None`），
    之后每次 `send(n)` 都必须吐出**正好 `n` 帧**的数据。

    尾部不足时**补零供满**（静音比残留垃圾好），补满这一块之后就 `return`，
    让设备自然停下。

    `on_end()` 在 `finally` 里调，用于让播放器知道已经不响了
    （否则 `is_playing()` 会一直停在 True）。
    """
    pending = array.array("h")
    exhausted = False
    tail_sent = False

    def _take(want_frames: int):
        """攒够 `want_frames` 帧；不足则补零供满。返回 (数据, 是否已到尾部)。"""
        nonlocal exhausted
        need = want_frames * _TARGET_CHANNELS      # 帧 -> 元素
        while len(pending) < need and not exhausted:
            try:
                chunk = next(rest)
            except StopIteration:
                exhausted = True
                break
            if chunk:
                pending.extend(chunk)
        if len(pending) >= need:
            out = pending[:need]
            del pending[:need]
            return out, False
        # 尾部不足：补零供满，别让最后一次回调留下未初始化内存。
        pad = need - len(pending)
        out = pending[:] + array.array("h", bytes(pad * _BYTES_PER_SAMPLE))
        del pending[:]
        return out, True

    try:
        # **协议必须一次 send 换一次数据。**
        #
        # 设备（miniaudio 的 `_data_callback`）是这么用的：
        #     chunk = gen.send(framecount)      # 要 framecount 帧
        #     memmove(output, chunk)            # 原样拷走
        # 也就是说**每次 send() 的返回值就是那一次要写进缓冲的数据**。
        #
        # 所以生成器从第一次 send() 起，每一次恢复都必须**直接产出数据**。
        # **不能让"收请求"和"给数据"各占一个 yield** —— 那样每两次 send
        # 就有一次返回 None，而 `None` 让 miniaudio 整个缓冲都不写入，
        # 于是听感还是"一卡一卡"（实测就是这个症状，见 _filled 的调试记录）。
        #
        # 正确写法：第一次 yield 只用来预热（返回 None），
        # 之后**在同一个 yield 上既交付本块数据、又接收下一个请求**
        # （`want_frames = yield out` —— send() 的返回值赋给 want_frames，
        # 而 send() 拿到的就是 out）。
        want_frames = yield None
        while True:
            if not want_frames:
                want_frames = yield None      # 设备偶尔问 0，保持挂起等真请求
                continue
            out, tail_sent = _take(want_frames)
            gain = volume() if volume is not None else 1.0
            if gain < 1.0:
                out = array.array("h", (int(sample * gain) for sample in out))
            if tail_sent:
                yield out
                return
            want_frames = yield out
    finally:
        try:
            rest.close()
        except Exception as exc:  # pragma: no cover - 关闭失败不影响主流程
            logger.debug("关闭解码生成器出错：%s", exc)
        if on_end is not None:
            on_end()


class PreviewPlayer:
    """一次只放一首的试听播放器。

    ## 为什么用生成器而不是"先解好再播"

    `miniaudio` 的 `PlaybackDevice.start()` **只接受生成器**
    （`PlaybackCallbackGeneratorType`），传 `array.array` 会得到
    `AttributeError: 'array.array' object has no attribute 'send'`
    —— 而且是 **cffi 回调里的异常**，`try/except` 根本抓不到，
    表现为"程序看起来在播、其实一点声音都没有"。这是本模块踩过的第二个坑。

    正确做法是把 `stream_preview()` 的生成器交给设备：它内部用
    `stream_memory(data)` **按设备回调的节奏边解边喂**。好处有三：
    ① 起步快（不用先把整曲解完）；
    ② 内存小（只缓冲一小段）；
    ③ 停止干净（设备停了就不再拉数据）。

    而且 `stream_memory` 收的是**字节**，天然绕开了 `.egg` 扩展名问题。

    > **曾经错在这里**：早先版本"设备那侧确实接了生成器"，但生成器前面
    > 却是 `ma.decode(整曲)` —— 设备拿到的是**已经全解到内存**的整曲切片。
    > 看起来完全符合上面这段描述，实际每次切歌要 580 ms / 66 MB。
    > 现在整条链路都是流式的（`stream_preview()`）。

    ## 线程约定

    `play_song()` / `play_file()` 会 `read_bytes()` 整个文件（最长 13.7 MB，
    实测约 4~13 MB）并同步解出第一块，约 0.1~0.4 秒，**建议在工作线程里调**。
    `stop()` 只关设备，很快，任何线程都能调。
    """

    def __init__(self) -> None:
        self._operation_lock = threading.Lock()
        self._request_id = 0
        self._volume = 1.0
        self._device = None
        self._playing_path: Path | None = None
        self._started_at = 0.0
        self._elapsed_before = 0.0
        #: 这次播放的起点（秒）。`position()` 是 `_origin + 已放时长`，
        #: 所以从 45 秒起播时进度条显示 45.x，而不是从 0 重新数。
        self._origin = 0.0
        self._limit_seconds = 0.0
        self._lock = threading.Lock()

    def set_volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, float(value)))

    def volume(self) -> float:
        return self._volume

    @property
    def playing_path(self) -> Path | None:
        """正在播放的文件；没在放则 None。"""
        return self._playing_path

    def is_playing(self) -> bool:
        return self._playing_path is not None

    @property
    def start_offset(self) -> float:
        """这次播放是从第几秒起的（第一次播放是 0.0）。"""
        with self._lock:
            return self._origin

    def position(self) -> float:
        """当前播放位置（秒，永远非负）—— **是歌曲里的绝对位置**。

        ## 为什么要把起点加回去

        从第 45 秒起播时，`_started_at` 只是"这一刻"，
        `time.monotonic() - _started_at` 得到的是**相对起点的偏移**（0.5 秒），
        而进度条要的是**歌曲里的位置**（45.5 秒）。所以基准点是
        `_origin`（这次播放的起点），不是 0。
        曾经漏了这一步：拖到 45 秒重新起播后进度条立刻跳回 0。

        ## 暂停时用记下来的累加值
        `PlaybackDevice` 一 `stop()` 计时基准就没了，所以暂停瞬间要把位置
        存进 `_elapsed_before`，恢复时把表重新对到那个位置。
        """
        with self._lock:
            if self._playing_path is None:
                return max(0.0, self._elapsed_before)
            if self._started_at <= 0.0:
                return max(0.0, self._origin)
            position = self._origin + (time.monotonic() - self._started_at)
        return max(0.0, position)

    def limit_seconds(self) -> float:
        """当前这次试听的**有效上限**（秒）；0 表示不设限（放整曲）。

        ## 这是"片段长度"还是"绝对终点"？（曾经搞混，导致进度条越拖越长）

        返回的是**这一段的长度**（`seconds` 参数原样），**不是**歌曲里的绝对终点。
        从第 15 秒起播、时长 30 秒时，这里返回 **30**，
        本次播放覆盖的是歌曲的 **15s ~ 45s**。

        想要绝对终点用 `end_offset()`。v17.3 的进度条把本方法当成了绝对终点
        （`span = start_offset + limit_seconds`），于是每拖一次量程就涨一截：
        30 → 45 → 52.5 → 56.25，显示成 `0:16 / 0:45`、越拖越偏。
        """
        with self._lock:
            return self._limit_seconds

    def end_offset(self) -> float:
        """本次播放的**绝对终点**（秒）—— 也就是进度条的量程。

        ## 为什么是"起点 + 长度"

        量程必须跟着**本次播放**走：从第 15 秒起播 30 秒，进度条就该是
        `15s → 45s`（否则前 30 秒只能爬到 18%，看着像卡住了）。

        - 有时长限制：`start_offset + limit_seconds`（15s 起播 30 秒 → 45.0）
        - 「整曲」（`limit_seconds() == 0`）：没有终点，返回 0.0，
          由调用方按歌曲总长决定量程。

        ## 它不是"歌曲结束位置"

        本方法**只知道本次播放这一段**，不知道歌曲总长
        （总长要读完整个文件才能得到，代价大且只有"整曲"档才需要）。
        所以进度条到 45s 就是 100%，而不是歌曲的 100%。
        想按整曲看比例，要用 `_preview_duration()`。

        **别用 `limit_seconds()` 当终点** —— 那是长度（30），不是位置（45）。
        """
        with self._lock:
            if self._limit_seconds > 0:
                return self._origin + self._limit_seconds
            return 0.0

    def play_song(
        self,
        song_folder: Path,
        *,
        seconds: float = PREVIEW_SECONDS,
        start: float = 0.0,
        request_id: int | None = None,
    ) -> Path | None:
        """播放一首歌的试听片段。

        返回实际播放的音频文件路径；找不到音频/解码失败/没装依赖都返回 None。
        `seconds=0` 表示放完整首，`start` 指定从第几秒开始（见 `play_file`）。
        """
        audio_file = _find_audio_file(song_folder)
        if audio_file is None:
            logger.info("目录里没有音频文件：%s", song_folder)
            return None
        return self.play_file(audio_file, seconds=seconds, start=start, request_id=request_id)

    def play_file(
        self,
        audio_file: Path,
        *,
        seconds: float = PREVIEW_SECONDS,
        start: float = 0.0,
        request_id: int | None = None,
    ) -> Path | None:
        """播放指定音频文件的一段。返回实际播放的文件路径，失败返回 None。

        * `seconds` —— 这一段最多放多久；**0 表示不设限（放整曲）**。
          用户可以按 `start` 从中间起播（拖动进度条就是这么实现的）。
        * `start` —— 从第几秒开始（负值按 0 处理）。
        """
        if request_id is None:
            self.stop()
            request_id = self.request_id
        with self._operation_lock:
            if request_id != self.request_id:
                return None
            return self._play_file(audio_file, seconds, start, request_id)

    @property
    def request_id(self) -> int:
        with self._lock:
            return self._request_id

    def _play_file(self, audio_file, seconds, start, request_id):
        ma = _load()
        if ma is None:
            return None
        start = max(0.0, float(start))
        seconds = max(0.0, float(seconds))
        # 用 stream_preview()：解够就停，**不会把整曲解进内存**。
        # 旧实现是 `ma.decode(整曲)` 再切片，实测峰值 66.0 MB / 580 ms
        # （13.66 MB 的 Rap God），现在 13.7 MB（= 源文件缓冲本身）/ 72 ms。
        # 详见 stream_preview()。seconds=0 时它内部是"不限长"，正好等于放整曲。
        preview = stream_preview(audio_file, seconds, start=start)
        # 先取一块：**在 worker 线程上**暴露解码错误，
        # 否则第一帧的错误会被推到音频回调线程里（那里没法处理）。
        try:
            head = next(preview, None)
        except Exception as exc:
            logger.info("解码失败 %s：%s", audio_file.name, exc)
            preview.close()
            return None
        if not head:
            logger.info("音频没有可播放数据：%s", audio_file.name)
            preview.close()
            return None

        device = None
        try:
            device = ma.PlaybackDevice(
                output_format=ma.SampleFormat.SIGNED16,
                nchannels=_TARGET_CHANNELS,
                sample_rate=_TARGET_RATE,
                # **别用默认的 200ms**：实测 `stop()` 的耗时 ≈ 2.5 × buffersize
                # （200ms → 约 600ms，50ms → 约 105ms），因为 WASAPI 要把
                # 共享缓冲排空。而 `stop()` 是在界面线程同步调的（见 stop()），
                # 于是"切歌/停止"会有半秒多的卡顿感。
                # 60ms 既明显缩短这个等待，又留够解码余量
                # （实测单次回调给的帧数从 8820 降到 2646）。
                buffersize_msec=_BUFFERSIZE_MSEC,
            )
            # 把刚取出的第一块接回去，用生成器保持"边解边喂"。
            def _on_end(_path=audio_file) -> None:
                """试听放完了 —— 只清状态，**绝对不要在这里关设备**。

                ## 三个坑叠在一起，前两个都表现为"声音不对/卡住"

                **坑一：生成器的 `finally` 不一定在放完时执行。**
                设备回调把生成器喂空后就不再调它了（实测：放完 1.5 秒
                片段后 `is_playing` 一直停在 True）；
                它只在解释器关闭、生成器被 GC 时才补跑。

                **坑二：关设备的线程可能起不来。**
                上面那种"补跑"发生在解释器退出期间，此时
                `threading.Thread(...).start()` 会抛
                `PythonFinalizationError: can't create new thread at
                interpreter shutdown`。

                **坑三（最危险）：不能在音频回调里直接 `device.stop()`。**
                本函数就跑在回调线程里，而 `stop()` 内部要 join 回调线程
                → **自锁死**（实测挂住 2 分钟不返回，只能 kill）。

                **结论：这里只做"改状态"这一件绝对安全的事**，
                把设备关停交给调用方（`play_file()` 开播前会 `stop()`、
                `stop_all()`、或下一次 `play_file()`）。
                关掉的设备由 GC 回收，miniaudio 的 `__del__` 会兜底。
                """
                with self._lock:
                    if self._device is device and self._playing_path == _path:
                        self._playing_path = None

            # 用 _filled() 把小块攒成"设备要多少给多少"，
            # 否则每次回调只有 11.6% 是有效数据 → 听感是"一卡一卡"。
            # 详见 _filled() 的说明。
            chained = _filled(_prepend(head, preview), on_end=_on_end, volume=self.volume)
            # **必须先 next() 一次"预热"**：PlaybackDevice.start() 内部会
            # 直接 send()，而刚创建的生成器只能接收 None ——
            # 不预热就得到 `TypeError: can't send non-None value to a
            # just-started generator`（同样是 cffi 回调里的异常，
            # 表现症状还是"看起来在播、其实没声音"）。
            # miniaudio 的文档原话：The generator should already be started
            # before passing it in.
            next(chained)
            with self._lock:
                stale = request_id != self._request_id
                if not stale:
                    self._device = device
                    self._playing_path = audio_file
            if stale:
                chained.close()
                _close_device(device)
                return None
            device.start(chained)
        except Exception as exc:
            # 没声卡 / 设备被独占 / 驱动异常 / 解码错都会走到这里。
            # **绝不能让它冒泡**：试听是加分项，不该把主流程弄崩。
            logger.info("打开播放设备失败（%s）：%s", audio_file.name, exc)
            with self._lock:
                if self._device is device:
                    self._device = None
                    self._playing_path = None
            if device is not None:
                _close_device(device)
            preview.close()
            return None

        with self._lock:
            if request_id != self._request_id:
                return None
            # 进度条靠这个：记下"从哪一刻开始算"，播放位置查询
            # 就是 `time.monotonic() - _started_at`，不必在回调里累加。
            self._started_at = time.monotonic()
            self._elapsed_before = start
            self._origin = start
            self._limit_seconds = seconds
        logger.info("试听：%s（%s 起，%s）",
                    audio_file.name,
                    f"{start:.1f}s",
                    "整曲" if seconds <= 0 else f"{seconds:.0f}s")
        return audio_file

    def stop(self) -> None:
        """停掉当前播放（没有在放则什么都不做）。**立即返回**，不阻塞。

        ## 为什么关设备要放到后台线程

        `device.stop()` 内部要 join 音频回调线程，实测一次 **100~600 ms**
        （取决于 `buffersize_msec`：200ms → 约 600ms，实测 `stop` 耗时
        约为它的 2.5 倍，因为 WASAPI 要把共享缓冲排空）。

        `stop()` 是在**界面线程**上同步调的 —— 拖动进度条改起点、切歌、
        关窗口都会走到这里。留在界面线程上就是每次操作卡半秒。

        ## 为什么不能干脆不同步关

        设备对象不能同一时刻既在播又被关，所以 `_device` 先**原子地摘走**
        （在锁里），后面的关停动作只持有那个局部引用，跟播放器状态无关。
        这样即使关停还在后台跑，新的 `play_file()` 也不会用到旧设备。

        ## 线程约定

        **不能从音频回调线程调**（那里调就是自锁死，见 `_close_device`）。
        `_on_end` 回调因此只改状态，不碰设备。
        """
        with self._lock:
            self._request_id += 1
            device, self._device = self._device, None
            # 暂停语义：把"当前在哪一秒"存下来，`position()` 在停掉之后
            # 仍然能报出一个有意义的位置（拖动进度条前先取位置用得上）。
            if self._playing_path is not None and self._started_at > 0.0:
                self._elapsed_before = max(
                    0.0, self._origin + (time.monotonic() - self._started_at)
                )
            self._playing_path = None
            self._started_at = 0.0
        if device is None:
            return
        # 串行关闭设备，并等待原生资源释放后才允许解释器退出。
        threading.Thread(
            target=self._close_serial, args=(device,), daemon=False, name="preview-close"
        ).start()

    def _close_serial(self, device):
        with self._operation_lock:
            _close_device(device)


#: 全局单例：整个程序只留一个播放器，避免多首同时响。
_player: PreviewPlayer | None = None
_player_lock = threading.Lock()


def player() -> PreviewPlayer:
    """取全局播放器（惰性创建）。"""
    global _player
    if _player is None:
        with _player_lock:
            if _player is None:
                _player = PreviewPlayer()
    return _player


def stop_all() -> None:
    """停掉所有试听（切歌 / 关窗口 / 退出程序时调）。

    **退出前一定要调**，否则音频设备会留到 GC 才关，
    而 GC 可能发生在解释器关闭期间（那时起线程会失败）。
    """
    if _player is not None:
        try:
            _player.stop()
        except Exception as exc:  # pragma: no cover - 退出路径不能抛
            logger.debug("stop_all 出错：%s", exc)
