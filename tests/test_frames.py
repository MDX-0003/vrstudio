"""Frame extraction helper: one frame, source resolution, no scaling."""

from __future__ import annotations

from pathlib import Path

import pytest

from vrstudio.media import frames
from vrstudio.media.videoio import VideoIOError


class _Result:
    returncode = 0
    stderr = ""


def test_extract_frame_uses_source_resolution(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    out = tmp_path / "frame.png"
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"png")
        return _Result()

    monkeypatch.setattr(frames, "ffmpeg", lambda config=None: "ffmpeg-fake")
    monkeypatch.setattr(frames.subprocess, "run", fake_run)
    result = frames.extract_frame(video, 1.25, out)
    assert result == out and out.is_file()
    args = calls[0]
    assert "-ss" in args and args[args.index("-ss") + 1] == "1.250"
    assert "-frames:v" in args and args[args.index("-frames:v") + 1] == "1"
    assert "-vf" not in args          # 不缩放


def test_extract_frame_rejects_negative_time(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    with pytest.raises(ValueError):
        frames.extract_frame(video, -0.1, tmp_path / "x.png")


def test_extract_frame_fails_when_ffmpeg_produces_nothing(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    class _Bad:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(frames, "ffmpeg", lambda config=None: "ffmpeg-fake")
    monkeypatch.setattr(frames.subprocess, "run", lambda args, **kw: _Bad())
    with pytest.raises(VideoIOError, match="no frame"):
        frames.extract_frame(video, 0.0, tmp_path / "x.png")
