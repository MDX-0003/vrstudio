"""ffmpeg discovery and encode sink.

Everything here is lazy: nothing runs until a merge actually needs to encode.

ffmpeg resolution order: the project's own ``bin/ffmpeg.exe`` first (ships
with the distribution package, so recipients never touch PATH), then whatever
is on PATH as a fallback. Pin the build used in bin/ (see docs/knowledge) —
the encode parameters are byte-compatible with the project's verified baseline
(see docs/knowledge/verification.md): rawvideo bgr24 stdin -> libx264
crf/preset -> yuv420p, audio carried over in a second ``-c copy`` remux pass
(remuxing a copied stream cannot change the frame count; encoding with the
audio input present can). Keyframes are pinned to ``KEYFRAME_INTERVAL_S``
instead of x264's 250-frame default so 8K deliverables stay seekable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

_FFMPEG: str | None = None
_RESOLVED = False

#: 关键帧间隔（秒）。源素材本身是 0.5 秒；x264 默认 250 帧，在 60fps 下等于
#: 4.17 秒，8K 成片 seek 一次要解码上百帧，播放器会卡死（对比页实测）。
#: 只影响关键帧位置，不改变解码后的像素，像素基线不受影响。
KEYFRAME_INTERVAL_S = 0.5


def gop_frames(fps) -> int:
    """关键帧间隔换算成帧数；fps 可以是 float 或 "30000/1001" 这类写法。"""
    try:
        value = float(Fraction(str(fps)))
    except (ValueError, ZeroDivisionError):
        return 250
    if value <= 0:
        return 250
    return max(1, int(round(value * KEYFRAME_INTERVAL_S)))


def bundled_ffmpeg() -> Path:
    """bin/ffmpeg.exe next to the project root (src/vrstudio/media/ffmpeg.py -> root)."""
    return Path(__file__).resolve().parents[3] / "bin" / "ffmpeg.exe"


def ffmpeg_path() -> str | None:
    """Locate an ffmpeg binary on first use; bin/ wins over PATH."""
    global _FFMPEG, _RESOLVED
    if not _RESOLVED:
        _RESOLVED = True
        bundled = bundled_ffmpeg()
        _FFMPEG = str(bundled) if bundled.is_file() else shutil.which("ffmpeg")
    return _FFMPEG


def has_ffmpeg() -> bool:
    p = ffmpeg_path()
    return bool(p)


def has_audio(path: str) -> bool:
    """True if `path` has an audio stream (probe via ffmpeg stderr)."""
    ff = ffmpeg_path()
    if not ff:
        return False
    probe = subprocess.run([ff, "-hide_banner", "-i", path],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    return "Audio:" in probe.stderr


class FfmpegSink:
    """Streams BGR frames into ffmpeg/libx265, optionally muxing source audio.

    Frames are uint16 (``bgr48le``); a uint8 caller is upscaled by 257 so the
    patch flows that still read through OpenCV keep working while their internals
    are migrated. The output profile follows the project standard: HEVC Main10
    ``yuv420p10le``.

    Two passes when audio is requested: encode video-only, then remux the audio
    in with ``-c:v copy`` — see module docstring for why.
    """

    def __init__(self, path, size, fps, crf=14, preset="slow",
                 audio_from=None, pix_fmt="yuv420p10le", encoder="libx265"):
        ff = ffmpeg_path()
        if not ff:
            raise RuntimeError("ffmpeg binary not found")
        self.path = path
        self.audio_from = audio_from
        self.tmp = (path + ".video-only.mp4") if audio_from else path
        gop = gop_frames(fps)
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "warning",
               "-f", "rawvideo", "-pix_fmt", "bgr48le",
               "-s", f"{size[0]}x{size[1]}", "-r", f"{fps}",
               "-i", "-",
               "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709",
               "-c:v", encoder, "-crf", str(crf), "-preset", preset,
               "-g", str(gop), "-keyint_min", str(gop),
               "-pix_fmt", pix_fmt,
               "-colorspace", "bt709", "-color_primaries", "bt709",
               "-color_trc", "bt709", "-color_range", "tv",
               "-x265-params",
               "colorprim=bt709:transfer=bt709:colormatrix=bt709:log-level=error",
               "-threads", "0", self.tmp]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stderr=subprocess.PIPE)

    def write(self, frame):
        if frame.dtype == np.uint8:
            frame = frame.astype(np.uint16) * 257
        self.proc.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self):
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode("utf-8", "replace")
        rc = self.proc.wait()
        if rc != 0:
            print(f"WARNING: ffmpeg exited with code {rc}: {err.strip()[:300]}")
        elif err.strip():
            print(f"ffmpeg notes: {err.strip()[:300]}")
        if not self.audio_from:
            return
        ff = ffmpeg_path()
        mux = [ff, "-y", "-hide_banner", "-loglevel", "error",
               "-i", self.tmp, "-i", self.audio_from,
               "-map", "0:v:0", "-map", "1:a:0?", "-c", "copy",
               "-movflags", "+faststart", self.path]
        r = subprocess.run(mux, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError("failed to mux audio:\n" + (r.stderr or "")[:500])
        os.remove(self.tmp)
