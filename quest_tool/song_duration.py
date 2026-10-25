"""Bounded audio-header duration reads; no decoding, playback, or Tk calls."""
from __future__ import annotations

import math
import struct
from pathlib import Path

HEAD_LIMIT = 65536
TAIL_LIMIT = 131072


def audio_filename(data):
    audio = data.get("audio", {}) if isinstance(data, dict) else {}
    value = (audio.get("songFilename") if isinstance(audio, dict) else None) or (data or {}).get("_songFilename") or (data or {}).get("songFilename")
    return value if isinstance(value, str) else ""


def duration_from_parts(head: bytes, tail: bytes) -> float:
    """Ogg Vorbis/Opus granules, FLAC STREAMINFO, or RIFF/WAVE data size."""
    duration = 0.0
    if head.startswith(b"OggS"):
        vorbis, opus = head.find(b"\x01vorbis"), head.find(b"OpusHead")
        rate, pre_skip = 0, 0
        if vorbis >= 0 and len(head) >= vorbis+16:
            rate = struct.unpack_from('<I', head, vorbis+12)[0]
        elif opus >= 0 and len(head) >= opus+12:
            rate, pre_skip = 48000, struct.unpack_from('<H', head, opus+10)[0]
        serial = head[14:18]
        granules = []
        offset = 0
        while True:
            offset = tail.find(b'OggS', offset)
            if offset < 0:break
            if offset+27 <= len(tail) and tail[offset+4] == 0 and tail[offset+14:offset+18] == serial:
                granule = struct.unpack_from('<Q', tail, offset+6)[0]
                if granule != 0xffffffffffffffff:granules.append(granule)
            offset += 4
        if rate and granules:duration = max(0, max(granules)-pre_skip)/rate
    elif head.startswith(b'fLaC') and len(head) >= 26 and head[4] & 0x7f == 0:
        packed = int.from_bytes(head[18:26], 'big')
        rate, samples = packed >> 44, packed & ((1 << 36)-1)
        if rate:duration = samples/rate
    elif head[:4] == b'RIFF' and head[8:12] == b'WAVE':
        offset, byte_rate = 12, 0
        while offset+8 <= len(head):
            tag, size = head[offset:offset+4], int.from_bytes(head[offset+4:offset+8], 'little')
            if tag == b'fmt ' and size >= 16 and offset+24 <= len(head):
                byte_rate = int.from_bytes(head[offset+16:offset+20], 'little')
            if tag == b'data' and byte_rate:
                duration = size/byte_rate;break
            offset += 8+size+(size % 2)
    return duration if math.isfinite(duration) and 0 < duration <= 86400 else 0.0


def local_song_duration(folder: Path, data) -> float:
    """Resolve inside the selected song folder, then read at most 192 KiB."""
    try:
        root = folder.resolve()
        name = audio_filename(data)
        paths = [root/name] if name else sorted(p for p in root.iterdir() if p.suffix.lower() in ('.ogg','.egg','.wav','.flac'))
        for path in paths:
            if not path.resolve().is_relative_to(root) or not path.is_file():continue
            with path.open('rb') as stream:
                head = stream.read(HEAD_LIMIT)
                stream.seek(max(0, path.stat().st_size-TAIL_LIMIT))
                tail = stream.read(TAIL_LIMIT)
            duration = duration_from_parts(head, tail)
            if duration:return duration
    except (OSError, ValueError, TypeError, struct.error):
        pass
    return 0.0
