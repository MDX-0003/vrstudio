"""时间拼接（vrstudio assemble）：前缀 + 空间合并段 + 后缀 → 完整成片。

用真实 ffmpeg 写小素材；没有 bin/ffmpeg.exe 时整文件跳过（CI 只跑不依赖外部
二进制的单测）。像素比较用 crf 0，只剩 RGB<->YUV 的一次转换。
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from typer.testing import CliRunner

from vrstudio import config as config_mod
from vrstudio.cli import assemble as asm
from vrstudio.media.binres import find_tool
from vrstudio.media.videoio import FrameReader, probe
from vrstudio.patch.sidecar import (
    InnerRect,
    Segment,
    Sidecar,
    Viewport,
    save_sidecar,
)
from tests.test_videoio_smoke import H, W, _closed_erp, _write

pytestmark = pytest.mark.skipif(
    find_tool("ffmpeg.exe") is None or find_tool("ffprobe.exe") is None,
    reason="external ffmpeg not populated in bin/ (see bin/README.md)",
)

runner = CliRunner()


def _conf():
    conf = config_mod.load()
    conf.data["video"]["preset"] = "ultrafast"
    conf.data["video"]["crf"] = 0
    return conf


def _sidecar(path, start: int, end: int, fps: float = 10.0):
    save_sidecar(Sidecar(
        erp_width=W, erp_height=H, fps=fps, version=1,
        segments=[Segment(id="s0", frame_start=start, frame_end=end,
                          viewport=Viewport(yaw_deg=0.0, pitch_deg=0.0, fov_h_deg=90.0,
                                            width=W, height=H),
                          inner=InnerRect(x=0, y=0, width=W // 2, height=H // 2))]),
        path)


def _read(path) -> list[np.ndarray]:
    info = probe(path, _conf())
    with FrameReader(info, _conf()) as reader:
        return list(reader)


def _run(src, space, sidecar, output):
    return runner.invoke(asm.app, [
        "--input", str(src), "--space", str(space), "--sidecar", str(sidecar),
        "--output", str(output), "--crf", "0", "--preset", "ultrafast", "-q"])


def _keyframes(path) -> list[int]:
    import subprocess

    r = subprocess.run(
        [find_tool("ffprobe.exe"), "-v", "error", "-select_streams", "v:0",
         "-show_frames", "-show_entries", "frame=key_frame", "-of", "csv=p=0",
         str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    return [i for i, line in enumerate(r.stdout.splitlines()) if line.startswith("1")]


def test_assemble_stitches_prefix_segment_suffix(tmp_path):
    conf = _conf()
    src = tmp_path / "src.mp4"
    _write(src, _closed_erp(), conf)                 # 6 帧 @10fps
    seg_frames = np.full((3, H, W, 3), 7, dtype=np.uint8)
    space = tmp_path / "space.mp4"
    _write(space, seg_frames, conf)
    sidecar = tmp_path / "clip.json"
    _sidecar(sidecar, 2, 4)
    out = tmp_path / "out_frame.mp4"

    r = _run(src, space, sidecar, out)
    assert r.exit_code == 0, r.output
    assert probe(out, conf).frames in (0, 6)

    got = _read(out)
    src_back = _read(src)
    seg_back = _read(space)
    assert len(got) == 6
    # 三段都经过一次 8-bit yuv420p 重编码（crf 0）：像素内容不变，与源文件的
    # 解码帧相比允许一次文件往返的差值（~5 级），位置与顺序必须严格一致。
    for i in range(2):
        assert np.abs(got[i].astype(int) - src_back[i].astype(int)).max() <= 5
    for i in range(3):
        assert np.abs(got[2 + i].astype(int) - seg_back[i].astype(int)).max() <= 5
    assert np.abs(got[5].astype(int) - src_back[5].astype(int)).max() <= 5

    report = json.loads((tmp_path / "out_frame.assemble.json").read_text(encoding="utf-8"))
    assert report["frames_prefix"] == 2 and report["frames_segment"] == 3
    assert report["frames_suffix"] == 1 and report["frames_written"] == 6
    # 10fps × 0.5s = 每 5 帧一个关键帧：seek 不再需要解整段。
    assert _keyframes(out) == [0, 5]


def test_whole_segment_is_skipped(tmp_path):
    conf = _conf()
    src = tmp_path / "src.mp4"
    _write(src, _closed_erp(), conf)
    space = tmp_path / "space.mp4"
    _write(space, _closed_erp(), conf)
    sidecar = tmp_path / "clip.json"
    _sidecar(sidecar, 0, 5)
    out = tmp_path / "out_frame.mp4"

    r = _run(src, space, sidecar, out)
    assert r.exit_code == 0, r.output
    assert not out.exists()
    assert "跳过时间拼接" in r.output


def test_segment_frame_count_must_match_the_contract(tmp_path):
    conf = _conf()
    src = tmp_path / "src.mp4"
    _write(src, _closed_erp(), conf)
    space = tmp_path / "space.mp4"
    _write(space, _closed_erp(2), conf)              # 少一帧
    sidecar = tmp_path / "clip.json"
    _sidecar(sidecar, 2, 4)
    r = _run(src, space, sidecar, tmp_path / "o.mp4")
    assert r.exit_code != 0
    assert "契约要求" in r.output


def test_segment_beyond_source_is_rejected(tmp_path):
    conf = _conf()
    src = tmp_path / "src.mp4"
    _write(src, _closed_erp(), conf)
    space = tmp_path / "space.mp4"
    _write(space, _closed_erp(), conf)
    sidecar = tmp_path / "clip.json"
    _sidecar(sidecar, 5, 99)
    r = _run(src, space, sidecar, tmp_path / "o.mp4")
    assert r.exit_code != 0
    assert "超出源片帧数" in r.output
