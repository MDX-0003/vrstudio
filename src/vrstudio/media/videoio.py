"""ffmpeg streaming: probe, frame reader, frame writer.

Everything is streamed frame by frame. A 600-frame 8K pass would be ~62 GB if it
were resident, so the algorithms only ever see one frame (pure functions in, pure
functions out), and this module owns the pipes.

Two encoding details that are part of the pixel baseline:

* frames move as ``bgr24`` rawvideo (OpenCV's native channel order, so no
  conversion anywhere);
* audio is muxed in a **second pass** with ``-c:v copy``. Encoding with the audio
  input present can change the frame count, and a copied stream cannot.

The only output profile is H.264 8-bit ``yuv420p`` (``video.pix_fmt``): the
Seedance return is already 4:2:0, and Chrome's H.264 decoder rejects 4:4:4, so
storing the deliverable as 4:4:4 used to buy a huge file that the console could
not play. Pixel-baseline tests that need an exact round trip pin
``video.crf = 0`` explicitly instead of relying on a lossless default.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from .binres import ffmpeg, ffprobe

FRAME_PIX_FMT = "bgr24"
_BYTES_PER_PIXEL = 3
#: 管道分块读取的块大小。Windows 下实测 8K 帧整帧一次读 298 ms/帧，64 KB 分块
#: 62 ms/帧；更大的块（4 MB）反而退化，所以固定在这里，不暴露成配置。
_READ_CHUNK = 1 << 16


@dataclass(frozen=True)
class VideoInfo:
    path: str
    size: tuple[int, int]          # (W, H)
    fps: str                       # ffprobe's exact rational, e.g. "30000/1001"
    fps_float: float
    frames: int                    # 0 when the container does not report it
    pix_fmt: str
    codec: str
    has_audio: bool
    duration_s: float

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]


class VideoIOError(RuntimeError):
    pass


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def probe(path: str | Path, config=None) -> VideoInfo:
    """Metadata via ffprobe. ``frames == 0`` means "not reported", not "empty"."""
    path = str(path)
    if not Path(path).is_file():
        raise VideoIOError(f"no such file: {path}")
    r = _run([ffprobe(config), "-v", "error", "-select_streams", "v:0",
              "-show_entries",
              "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,pix_fmt,codec_name",
              "-show_entries", "format=duration", "-of", "json", path])
    if r.returncode != 0:
        raise VideoIOError(f"ffprobe failed on {path}:\n{(r.stderr or '').strip()[:400]}")
    try:
        payload = json.loads(r.stdout or "{}")
        stream = payload["streams"][0]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise VideoIOError(f"ffprobe returned no video stream for {path}") from exc

    fps = str(stream.get("r_frame_rate") or stream.get("avg_frame_rate") or "0/0")
    try:
        num, _, den = fps.partition("/")
        fps_float = float(num) / float(den) if float(den or 0) else 0.0
    except (ValueError, ZeroDivisionError):
        fps_float = 0.0

    frames_raw = stream.get("nb_frames", "0")
    try:
        frames = int(frames_raw)
    except (TypeError, ValueError):
        frames = 0

    try:
        duration = float(payload.get("format", {}).get("duration", 0.0) or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    audio = _run([ffprobe(config), "-v", "error", "-select_streams", "a",
                  "-show_entries", "stream=index", "-of", "csv=p=0", path])
    has_audio = bool((audio.stdout or "").strip())

    return VideoInfo(
        path=path,
        size=(int(stream["width"]), int(stream["height"])),
        fps=fps,
        fps_float=fps_float,
        frames=frames,
        pix_fmt=str(stream.get("pix_fmt", "")),
        codec=str(stream.get("codec_name", "")),
        has_audio=has_audio,
        duration_s=duration,
    )


class FrameReader:
    """Iterate decoded frames as ``(H, W, 3)`` uint8 BGR.

    Stopping early is *not* an error: a caller that wants one frame out of a long
    clip closes the pipe, ffmpeg sees EPIPE and exits nonzero, and treating that as
    a decode failure would make "grab the middle frame" impossible. Only a nonzero
    exit *after* the stream was consumed to the end is reported.

    Each frame gets its own buffer. Callers keep frames (reference-frame scans,
    samples, ``list(reader)``), so a reused buffer would let frame N+1 overwrite
    frame N; ownership passes to the caller and the buffer goes away with the last
    reference. The generator drops its own reference right after yielding.
    """

    def __init__(self, info: VideoInfo, config=None, seek: float | None = None,
                 count: int | None = None):
        self.info = info
        self.width, self.height = info.size
        args = [ffmpeg(config), "-v", "error", "-nostdin"]
        if seek is not None:
            args += ["-ss", f"{seek:.6f}"]
        args += ["-i", info.path]
        if count is not None:
            args += ["-frames:v", str(int(count))]
        args += ["-f", "rawvideo", "-pix_fmt", FRAME_PIX_FMT, "-"]
        self._args = args
        self._proc: subprocess.Popen | None = None
        self._exhausted = False
        self._frame_bytes = self.width * self.height * _BYTES_PER_PIXEL

    def __enter__(self) -> "FrameReader":
        self._proc = subprocess.Popen(self._args, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
        return self

    def __iter__(self) -> Iterator[np.ndarray]:
        assert self._proc is not None and self._proc.stdout is not None
        fd = self._proc.stdout.fileno()
        while True:
            frame = np.empty(self._frame_bytes, np.uint8)
            view = memoryview(frame)
            got = 0
            while got < self._frame_bytes:
                chunk = os.read(fd, min(_READ_CHUNK, self._frame_bytes - got))
                if not chunk:
                    break
                view[got:got + len(chunk)] = chunk
                got += len(chunk)
            view.release()
            if got < self._frame_bytes:
                self._exhausted = True
                break
            yield frame.reshape(self.height, self.width, _BYTES_PER_PIXEL)
            # 生成器在 yield 处挂起时会持有这一帧；先解引用，下一帧申请之前释放。
            frame = None

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._proc is None:
            return
        proc, self._proc = self._proc, None
        if proc.stdout:
            proc.stdout.close()
        if not self._exhausted and proc.poll() is None:
            proc.terminate()
        err = b""
        if proc.stderr:
            err = proc.stderr.read()
            proc.stderr.close()
        rc = proc.wait()
        if exc_type is not None or not self._exhausted:
            return                      # consumer stopped early: not a decode failure
        if rc != 0:
            raise VideoIOError(
                f"ffmpeg decode failed on {self.info.path} (exit {rc}):\n"
                f"{err.decode('utf-8', 'replace').strip()[:400]}"
            )


class FrameWriter:
    """Encode frames to the project's baseline settings, optionally muxing audio."""

    def __init__(self, path: str | Path, size: tuple[int, int], fps: str,
                 video: dict, audio_from: str | Path | None = None, config=None):
        self.path = Path(path)
        self.final = self.path
        self.tmp = (self.path.with_suffix(".video-only" + self.path.suffix)
                    if audio_from else self.path)
        self.audio_from = str(audio_from) if audio_from else None
        self.video_cfg = dict(video)
        self.config = config
        self.width, self.height = size
        self.fps = fps
        self._proc: subprocess.Popen | None = None
        self.frames_written = 0

    def __enter__(self) -> "FrameWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.width % 2 or self.height % 2:
            raise VideoIOError(
                f"yuv420p needs even dimensions, got {self.width}x{self.height}; "
                f"trim or rescale the canvas before encoding"
            )
        args = [ffmpeg(self.config), "-y", "-v", "error", "-nostdin",
                "-f", "rawvideo", "-pix_fmt", FRAME_PIX_FMT,
                "-s", f"{self.width}x{self.height}", "-r", self.fps, "-i", "-",
                "-an", "-c:v", str(self.video_cfg["encoder"]),
                "-crf", str(self.video_cfg["crf"]),
                "-preset", str(self.video_cfg["preset"]),
                "-pix_fmt", str(self.video_cfg["pix_fmt"]),
                "-threads", "0", str(self.tmp)]
        self._proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        return self

    def write(self, frame: np.ndarray) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        if frame.shape[0] != self.height or frame.shape[1] != self.width:
            raise VideoIOError(
                f"frame {frame.shape[1]}x{frame.shape[0]} != declared "
                f"{self.width}x{self.height}"
            )
        # memoryview 直接写管道：8K 帧省掉 tobytes() 的 100 MB 复制。
        self._proc.stdin.write(np.ascontiguousarray(frame).data.cast("B"))
        self.frames_written += 1

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._proc is None:
            return
        if self._proc.stdin:
            self._proc.stdin.close()
        err = b""
        if self._proc.stderr:
            err = self._proc.stderr.read()
            self._proc.stderr.close()
        rc = self._proc.wait()
        self._proc = None
        if exc_type is not None:
            return
        if rc != 0:
            raise VideoIOError(
                f"ffmpeg encode failed (exit {rc}):\n{err.decode('utf-8', 'replace').strip()[:400]}"
            )
        if not self.audio_from or str(self.video_cfg.get("audio", "copy")) == "none":
            return
        mux = [ffmpeg(self.config), "-y", "-v", "error",
               "-i", str(self.tmp), "-i", self.audio_from,
               "-map", "0:v:0", "-map", "1:a:0?", "-c", "copy"]
        if str(self.video_cfg.get("metadata", "copy")) == "copy":
            mux += ["-map_metadata", "1"]
        mux += ["-movflags", "+faststart", str(self.final)]
        r = _run(mux)
        if r.returncode != 0:
            raise VideoIOError(f"audio mux failed:\n{(r.stderr or '').strip()[:400]}")
        try:
            self.tmp.unlink()
        except OSError:
            pass


def sample_frames(path: str | Path, count: int, config=None) -> list[np.ndarray]:
    """Up to ``count`` frames spread evenly across the clip.

    Seeks with ``-ss`` per sample instead of decoding everything: verify runs on
    full-length 8K deliverables and has no business decoding 600 frames to say
    something about the seam.
    """
    info = probe(path, config)
    if count <= 0:
        return []
    duration = info.duration_s
    if duration <= 0 and info.frames and info.fps_float > 0:
        duration = info.frames / info.fps_float
    if duration <= 0:
        times = [0.0]
    else:
        times = [duration * (i + 0.5) / count for i in range(count)]

    frames: list[np.ndarray] = []
    for t in times:
        with FrameReader(info, config, seek=t, count=1) as reader:
            frames.extend(iter(reader))
    return frames
