"""End-to-end through real files, with ffmpeg.

Skipped when ``bin/ffmpeg.exe`` is absent (a fresh clone has no binaries; see
``bin/README.md``). Uses ``video.crf = 0`` + ``yuv444p`` so the only loss in the
file round trip is the RGB<->YUV colour transform, which is why the frame
comparison carries a small tolerance rather than demanding bit equality — the
bit-exact assertions live in ``test_roundtrip.py``, at the library boundary where
no colour conversion happens.
"""

from __future__ import annotations

import json
from fractions import Fraction

import numpy as np
import pytest

from vrstudio import config as config_mod
from vrstudio.media.binres import find_tool
from vrstudio.redraw.geometry import PadSpec, plan_pad
from vrstudio.redraw.padding import pad_horizontal
from vrstudio.redraw.preroll import pre_roll
from vrstudio.redraw.recompose_core import RecomposeSpec, finalize, recompose
from vrstudio.media.videoio import FrameReader, FrameWriter, probe, sample_frames

pytestmark = pytest.mark.skipif(
    find_tool("ffmpeg.exe") is None or find_tool("ffprobe.exe") is None,
    reason="external ffmpeg not populated in bin/ (see bin/README.md)",
)

ASPECT = Fraction(21, 9)
W, H, N = 256, 128, 6          # 2:1, small enough to be instant


def _closed_erp(n: int = N) -> np.ndarray:
    """(n, H, W, 3) uint8, periodic in x so the wrap is a real adjacency."""
    x = np.arange(W, dtype=np.float64)[None, :]
    y = np.arange(H, dtype=np.float64)[:, None]
    base = (120.0 + 60.0 * np.sin(2 * np.pi * 3 * x / W)
            + 30.0 * np.cos(2 * np.pi * 7 * x / W)
            + 25.0 * np.cos(2 * np.pi * y / H))
    frames = []
    for t in range(n):
        shifted = base + 8.0 * np.sin(2 * np.pi * t / n)
        img = np.clip(shifted, 0, 255).astype(np.uint8)
        frames.append(np.repeat(img[:, :, None], 3, axis=2))
    return np.stack(frames)


def _write(path, frames, conf) -> None:
    info_size = (frames.shape[2], frames.shape[1])
    with FrameWriter(path, info_size, "10/1", conf.video, config=conf) as writer:
        for frame in frames:
            writer.write(frame)


def test_video_roundtrip_through_files(tmp_path):
    conf = config_mod.load()
    conf.data["video"]["preset"] = "ultrafast"

    src_frames = _closed_erp()
    src = tmp_path / "src.mp4"
    _write(src, src_frames, conf)

    info = probe(src, conf)
    assert info.size == (W, H)
    assert info.frames in (0, N), f"expected {N} frames, ffprobe said {info.frames}"
    assert not info.has_audio

    plan = plan_pad(PadSpec((W, H), ASPECT))
    padded = tmp_path / "padded.mp4"
    ctx_frames = [pad_horizontal(pre_roll(f, plan.source_preroll), plan.P) for f in src_frames]
    _write(padded, np.stack(ctx_frames), conf)

    assert probe(padded, conf).size == plan.canvas_size

    spec = RecomposeSpec(W=plan.W, Wc=plan.Wc, P=plan.P, L=plan.L,
                         endpoint_lock_px=max(1, int(round(plan.L * 0.02))))
    final = tmp_path / "final.mp4"
    out_frames = []
    with FrameReader(probe(padded, conf), conf) as reader:
        for frame in reader:
            assert frame.shape[:2] == (plan.H, plan.Wc)
            out_frames.append(finalize(recompose(frame.astype(np.float64), spec)))
    _write(final, np.stack(out_frames), conf)

    back = probe(final, conf)
    assert back.size == (W, H)
    assert len(out_frames) == N

    # pre_roll cancelled the rotation, so the delivered frames are the source again.
    # Decode sequentially rather than via sample_frames(): that helper seeks with -ss,
    # which on a 6-frame clip can hand back the same keyframe twice and make this
    # comparison meaningless.
    got = []
    with FrameReader(back, conf) as reader:
        for frame in reader:
            got.append(frame)
    assert len(got) == N, f"expected {N} frames back, got {len(got)}"
    worst = max(int(np.abs(a.astype(int) - b.astype(int)).max())
                for a, b in zip(got, src_frames))
    # two 8-bit yuv444p round trips; measured at ~1 level each (see
    # docs/knowledge/verification.md). yuv444p10le would make it exact.
    assert worst <= 3, f"file round trip drifted by {worst} levels"


def test_cli_pad_and_recompose_and_verify(tmp_path):
    """Drive the installed entry points, not just the library."""
    import os
    import subprocess
    import sys

    root = config_mod.REPO_ROOT
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    env["VRSTUDIO_BIN_DIR"] = str(root / "bin")

    conf = config_mod.load()
    src_frames = _closed_erp()
    src = tmp_path / "src.mp4"
    _write(src, src_frames, conf)

    def run(*args):
        r = subprocess.run([sys.executable, "-m", "vrstudio", *args], cwd=root, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r

    padded = tmp_path / "padded.mp4"
    r = run("pad", "--in", str(src), "--out", str(padded), "--quiet",
            "--set", "video.preset=ultrafast")
    assert r.returncode == 0, r.stdout + r.stderr

    # derived names come from the config templates, so read them from there
    conf = config_mod.load()
    manifest = tmp_path / str(conf.get("manifest.path_template")).format(stem=padded.stem)
    mask = tmp_path / str(conf.get("mask.path_template")).format(stem=padded.stem)
    assert manifest.is_file(), f"pad did not write the manifest ({manifest.name})"
    assert mask.is_file(), f"pad did not write the mask ({mask.name})"
    canvas = json.loads(manifest.read_text(encoding="utf-8"))["canvas"]
    expect = plan_pad(PadSpec((W, H), ASPECT))
    assert canvas["P"] == expect.P and canvas["L"] == expect.L, canvas
    assert canvas["size"] == list(expect.canvas_size), canvas
    assert canvas["wrap_pair"] == list(expect.wrap_pair), canvas

    # the AIGC tool stands in as the identity: feed the padded video straight back
    final = tmp_path / "final.mp4"
    r = run("recompose", "--in", str(padded), "--out", str(final),
            "--manifest", str(manifest), "--quiet",
            "--set", "video.preset=ultrafast")
    assert r.returncode == 0, r.stdout + r.stderr
    assert final.is_file()
    report = final.with_name(final.stem + "_recompose.json")
    assert report.is_file(), "recompose wrote no report"
    assert json.loads(report.read_text(encoding="utf-8"))["registration"]["verdict"] == "ok"

    r = run("verify", "--before", str(src), "--after", str(final), "--samples", "4")
    assert "verdict  OK" in r.stdout, r.stdout + r.stderr

    r = run("plan", "--in", str(src))
    assert "pad plan" in r.stdout
    assert "no warnings" in r.stdout


def test_cli_reports_a_missing_manifest(tmp_path):
    import os
    import subprocess
    import sys

    root = config_mod.REPO_ROOT
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    env["VRSTUDIO_BIN_DIR"] = str(root / "bin")

    conf = config_mod.load()
    src = tmp_path / "src.mp4"
    _write(src, _closed_erp(), conf)

    r = subprocess.run([sys.executable, "-m", "vrstudio", "recompose", "--in", str(src),
                        "--out", str(tmp_path / "o.mp4"), "--manifest", str(tmp_path / "nope.json")],
                       cwd=root, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode != 0
    assert "nope.json" in (r.stdout + r.stderr)


@pytest.mark.parametrize("return_size", [(W, H * 3 // 2), (128, 64), (W, H)])
def test_output_geometry_does_not_follow_the_returned_aspect(tmp_path, return_size):
    """The deliverable is always exactly W x H, whatever resolution the tool returns.

    The canvas geometry is known (no vertical padding), so deriving the restored
    height from the tool's *returned* aspect lets its aspect error leak into the
    deliverable: a 21:9 return at a slightly-off resolution would come back a couple
    of rows taller than the source, i.e. the panorama's own geometry would depend on
    what resolution the tool happened to choose. Closure only depends on the columns,
    so pinning the height to the source costs nothing.
    """
    import os
    import subprocess
    import sys

    from vrstudio.media.binres import ffmpeg

    root = config_mod.REPO_ROOT
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    env["VRSTUDIO_BIN_DIR"] = str(root / "bin")

    conf = config_mod.load()
    src = tmp_path / "src.mp4"
    _write(src, _closed_erp(), conf)

    def run(*args):
        return subprocess.run([sys.executable, "-m", "vrstudio", *args], cwd=root, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")

    padded = tmp_path / "padded.mp4"
    assert run("pad", "--in", str(src), "--out", str(padded), "--quiet",
               "--set", "video.preset=ultrafast").returncode == 0
    manifest = tmp_path / "padded_pad.json"

    gw, gh = return_size
    gen = tmp_path / "gen.mp4"
    r = subprocess.run([ffmpeg(conf), "-y", "-v", "error", "-i", str(padded),
                        "-vf", f"scale={gw}:{gh}", "-c:v", "libx264", "-crf", "12",
                        "-preset", "ultrafast", "-pix_fmt", "yuv444p", "-an", str(gen)],
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[:300]

    final = tmp_path / "final.mp4"
    res = run("recompose", "--in", str(gen), "--out", str(final),
              "--manifest", str(manifest), "--quiet", "--set", "video.preset=ultrafast")
    assert res.returncode == 0, res.stdout + res.stderr

    got = probe(final, conf)
    assert got.size == (W, H), f"return {gw}x{gh} produced a {got.size} deliverable"
