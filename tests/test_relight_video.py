"""``relight`` 的错误路径（决策 56）：退出码 2 与 1，以及「失败不出片」。

正常路径与像素基线在 ``tests/test_relight.py`` 末尾；这里只放不需要产出视频的用例，
所以除音频探测外不依赖 ffmpeg。画面生成沿用同一份合成素材。
"""

from __future__ import annotations

import json
from datetime import datetime

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from tests.test_relight import (
    VH,
    VN,
    VW,
    read_video,
    video_erp_without_edges,
    write_video,
)
from vrstudio.cli import app
from vrstudio.cli.relight import resolve_reference
from vrstudio.media.binres import find_tool
from vrstudio.media.imageio import write_png
from vrstudio.progress import Log
from vrstudio.relight.align import warp_to_gt
from vrstudio.relight.cache import CachedFixed
from vrstudio.relight.fields import estimate_fields

runner = CliRunner()


def run_video_cli(*args: str):
    return runner.invoke(app, ["relight", *args])


@pytest.fixture
def sources(tmp_path):
    frames = [video_erp_without_edges() for _ in range(VN)]
    video = write_video(tmp_path / "gt.mp4", frames)
    atmo = write_png(tmp_path / "atmo.png", frames[0])
    return tmp_path, video, atmo, frames


def test_rejects_non_2_to_1_video(tmp_path):
    """GT 视频不是 2:1：退出 2，指名比例，不出片。"""
    frames = [np.full((96, 256, 3), 120, np.uint8)] * 3
    video = write_video(tmp_path / "gt.mp4", frames)
    atmo = write_png(tmp_path / "atmo.png", np.full((96, 256, 3), 100, np.uint8))
    out = tmp_path / "out.mp4"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo), "--out", str(out))
    assert result.exit_code == 2
    assert "2:1" in result.output
    assert not out.exists()


def test_rejects_size_mismatch(sources):
    tmp_path, video, _, frames = sources
    atmo = write_png(tmp_path / "small.png",
                     cv2.resize(frames[0], (VW // 2, VH // 2), interpolation=cv2.INTER_AREA))
    out = tmp_path / "out.mp4"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo), "--out", str(out))
    assert result.exit_code == 2
    assert "尺寸不一致" in result.output
    assert not out.exists()


@pytest.mark.parametrize("extra", [
    ("--strength", "1.5"),
    ("--local-shading", "-0.1"),
    ("--scene", "dusk"),
    ("--highlight-clip", "--clip-knee", "0.9", "--clip-max", "0.8"),
    ("--ref-frame", "-1"),
    ("--ref-frame", "99"),
    ("--crf", "60"),
])
def test_rejects_bad_parameters(sources, extra):
    tmp_path, video, atmo, _ = sources
    out = tmp_path / "out.mp4"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo), "--out", str(out), *extra)
    assert result.exit_code == 2, result.output
    assert not out.exists()


def test_alignment_failure_exits_1_without_output(tmp_path):
    """氛围图与画面无关：对齐不可信，退出 1，不出片。"""
    frames = [video_erp_without_edges() for _ in range(VN)]
    video = write_video(tmp_path / "gt.mp4", frames)
    rng = np.random.default_rng(5)
    unrelated = rng.integers(0, 256, (VH, VW, 3), dtype=np.uint8)
    atmo = write_png(tmp_path / "atmo.png", unrelated)
    out = tmp_path / "out.mp4"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo), "--out", str(out),
                           "--ref-frame", "0")
    assert result.exit_code == 1, result.output
    assert not out.exists()


def test_black_reference_frame_is_rejected(tmp_path):
    """参考帧是黑场：对齐不可信，退出 1 且不出片。

    黑场不再在估对齐之前拦（未对齐的带通相关在有几像素平移时也会接近 0，那条判据
    会误杀正常素材）；判据回到对齐之后的重合度，黑场的相关必然是 0。
    """
    frames = [video_erp_without_edges() for _ in range(VN)]
    frames[0] = np.zeros_like(frames[0])
    video = write_video(tmp_path / "gt.mp4", frames)
    atmo = write_png(tmp_path / "atmo.png", video_erp_without_edges())
    out = tmp_path / "out.mp4"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo), "--out", str(out),
                           "--ref-frame", "0")
    assert result.exit_code == 1, result.output
    assert "对齐不可信" in result.output
    assert not out.exists()


def test_auto_scan_skips_black_frames(tmp_path):
    """自动选帧跳过黑场：前几帧全黑时仍能选到有结构的一帧并出片。"""
    frames = [np.zeros((VH, VW, 3), np.uint8) for _ in range(3)]
    frames += [video_erp_without_edges() for _ in range(VN)]
    video = write_video(tmp_path / "gt.mp4", frames)
    atmo = write_png(tmp_path / "atmo.png", video_erp_without_edges())
    report = tmp_path / "report.json"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo),
                           "--out", str(tmp_path / "out.mp4"),
                           "--report", str(report), "--set", "video.preset=ultrafast")
    assert result.exit_code == 0, result.output
    assert json.loads(report.read_text(encoding="utf-8"))["ref_frame"] >= 3


def test_missing_inputs_are_runtime_failures(sources):
    tmp_path, video, atmo, _ = sources
    out = tmp_path / "out.mp4"
    missing_video = run_video_cli("--gt", str(tmp_path / "nope.mp4"), "--atmo", str(atmo),
                                  "--out", str(out))
    assert missing_video.exit_code == 1
    missing_atmo = run_video_cli("--gt", str(video), "--atmo", str(tmp_path / "nope.png"),
                                 "--out", str(out))
    assert missing_atmo.exit_code == 1
    assert not out.exists()


def test_bad_config_override_is_a_parameter_error(sources):
    tmp_path, video, atmo, _ = sources
    out = tmp_path / "out.mp4"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo), "--out", str(out),
                           "--set", "video.nope=1")
    assert result.exit_code == 2, result.output
    assert "配置错误" in result.output
    assert not out.exists()


def test_dump_light_is_rejected_for_video(sources):
    """--dump-light 是单帧概念：视频 GT 下按参数错拦下，不静默忽略。"""
    tmp_path, video, atmo, _ = sources
    out = tmp_path / "out.mp4"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo), "--out", str(out),
                           "--dump-light")
    assert result.exit_code == 2, result.output
    assert "--dump-light" in result.output
    assert not out.exists()


def test_sample_rejects_non_positive_count(sources):
    tmp_path, video, atmo, _ = sources
    out = tmp_path / "samples"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo), "--out", str(out),
                           "--sample", "0")
    assert result.exit_code == 2, result.output
    assert "--sample" in result.output


@pytest.mark.skipif(find_tool("ffmpeg.exe") is None or find_tool("ffprobe.exe") is None,
                    reason="external ffmpeg not populated in bin/ (see bin/README.md)")
def test_sample_writes_frames_without_encoding(sources):
    """--sample N：写 N 张 PNG 到 --out 目录，不产出视频文件。"""
    tmp_path, video, atmo, _ = sources
    out = tmp_path / "samples"
    report_path = tmp_path / "sample.json"
    result = run_video_cli("--gt", str(video), "--atmo", str(atmo), "--out", str(out),
                           "--sample", "3", "--report", str(report_path))
    assert result.exit_code == 0, result.output
    written = sorted(out.glob("sample_*.png"))
    assert len(written) == 3, result.output
    assert not any(out.glob("*.mp4"))
    assert not (tmp_path / "samples.mp4").exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["sample"]["count"] == 3
    assert len(report["sample"]["frames"]) == 3
    assert report["kind"] == "sample"
    assert report["timing"]["total"] == report["seconds"]
    assert report["timing"]["sample_decode"] > 0
    assert report["timing"]["sample_compose"] > 0
    assert "render_total" not in report["timing"]
    assert report["cache"] == {"enabled": False, "hit": False, "load": 0.0}


def test_retired_command_name_is_gone(sources):
    """relight-video 退役，不留别名：调用它必须是「未知命令」，不能静默走新路径。"""
    tmp_path, video, atmo, _ = sources
    result = runner.invoke(app, ["relight-video", "--gt", str(video),
                                 "--atmo", str(atmo), "--out", str(tmp_path / "x.mp4")])
    assert result.exit_code != 0
    assert "relight-video" in result.output


# ── --case：以 case 为单位跑（槽位解析、产物命名、current 回写）────────────


def _relight_case(tmp_path, video, atmo, name="rl"):
    from vrstudio import case as case_mod
    case_mod.create(name, "relight", root=tmp_path)
    case_mod.add_relight_candidate(name, case_mod.RELIGHT_GT, video, tmp_path)
    case_mod.add_relight_candidate(name, case_mod.RELIGHT_ATMO, atmo, tmp_path)
    return name


def test_case_flag_requires_both_slots(sources):
    """槽位没齐：退出 2 并指名缺哪一项，不出产物。"""
    from vrstudio import case as case_mod
    tmp_path, video, atmo, _ = sources
    case_mod.create("rl", "relight", root=tmp_path)
    result = run_video_cli("--case", "rl", "--cases-dir", str(tmp_path))
    assert result.exit_code == 2, result.output
    assert "还缺底版" in result.output

    case_mod.add_relight_candidate("rl", case_mod.RELIGHT_GT, video, tmp_path)
    result = run_video_cli("--case", "rl", "--cases-dir", str(tmp_path))
    assert result.exit_code == 2, result.output
    assert "还缺氛围依据" in result.output


def test_case_flag_rejects_explicit_paths(sources):
    """--case 与 --gt/--atmo/--out 混用：参数错，不猜用户想用哪一份。"""
    tmp_path, video, atmo, _ = sources
    _relight_case(tmp_path, video, atmo)
    result = run_video_cli("--case", "rl", "--cases-dir", str(tmp_path),
                           "--gt", str(video))
    assert result.exit_code == 2, result.output
    assert "只能选一种" in result.output


def test_case_flag_rejects_other_flows(tmp_path, sources):
    """非 relight 的 case 直接拒绝。"""
    from vrstudio import case as case_mod
    _tmp, video, _atmo, _frames = sources
    case_mod.create("ship", "patch", video, root=tmp_path)
    result = run_video_cli("--case", "ship", "--cases-dir", str(tmp_path))
    assert result.exit_code == 2, result.output
    assert "不是 relight" in result.output


@pytest.mark.skipif(find_tool("ffmpeg.exe") is None or find_tool("ffprobe.exe") is None,
                    reason="external ffmpeg not populated in bin/ (see bin/README.md)")
def test_case_flag_sample_writes_into_case_out(sources):
    """--case + --sample：产物写在 case 的 out/samples/ 下，且不改 current。"""
    from vrstudio import case as case_mod
    tmp_path, video, atmo, _ = sources
    _relight_case(tmp_path, video, atmo)
    result = run_video_cli("--case", "rl", "--cases-dir", str(tmp_path), "--sample", "2")
    assert result.exit_code == 0, result.output
    samples = list((case_mod.case_dir("rl", tmp_path) / "out" / "samples").glob("*/*.png"))
    assert len(samples) == 2, result.output
    # 小样不改 current：指针仍是两个槽位，没有成片指针。
    assert "current" not in (case_mod.load("rl", tmp_path).relight or {})


# ── 固定光照缓存与单遍解码（决策 64）──────────────────────────────────────


def _open_frames(frames, calls):
    """假的 ``open_frames``：记录每次打开时的 index，按 index 返回对应帧。"""
    def opener(index=None):
        calls.append(index)

        def generate():
            if index is None:
                for frame in frames:
                    yield frame
            elif 0 <= index < len(frames):
                yield frames[index]

        return generate()
    return opener


def _shifted_frames():
    return [video_erp_without_edges(shift=index * 8) for index in range(VN)]


def test_reference_scan_keeps_the_best_frame_in_one_pass():
    """自动选帧只开一次视频，光照场直接用扫描留下的原分辨率最佳帧。"""
    frames = _shifted_frames()
    atmo = frames[3]
    calls = []
    resolved = resolve_reference(
        _open_frames(frames, calls), atmo, VW, None, Log(quiet=True))
    fixed = resolved.fixed
    assert not resolved.from_cache
    assert calls == [None]
    assert fixed.ref_frame == 3
    expected = estimate_fields(frames[fixed.ref_frame],
                               warp_to_gt(atmo, fixed.alignment))
    assert np.array_equal(fixed.fields.gain, expected.gain)
    assert np.array_equal(fixed.fields.visibility, expected.visibility)
    assert np.array_equal(fixed.fields.white_balance, expected.white_balance)


def test_explicit_reference_frame_reads_only_that_frame():
    """显式参考帧按帧号 seek，不再从片头顺序解码。"""
    frames = _shifted_frames()
    calls = []
    resolved = resolve_reference(
        _open_frames(frames, calls), frames[3], VW, 3, Log(quiet=True))
    assert not resolved.from_cache
    assert calls == [3]
    assert resolved.fixed.ref_frame == 3


def test_cached_reference_skips_the_scan_and_sift(monkeypatch):
    """缓存命中时不再选帧、不再做 SIFT，只取一帧重算光照场。"""
    import vrstudio.cli.relight as relight_cli

    frames = _shifted_frames()
    atmo = frames[3]
    cold = resolve_reference(
        _open_frames(frames, []), atmo, VW, None, Log(quiet=True)).fixed
    cached = CachedFixed(cold.ref_frame, cold.ref_matched, cold.alignment)

    def boom(*_args, **_kwargs):
        raise AssertionError("estimate_similarity ran despite a fresh cache")

    monkeypatch.setattr(relight_cli, "estimate_similarity", boom)
    calls = []
    resolved = resolve_reference(
        _open_frames(frames, calls), atmo, VW, None, Log(quiet=True),
        cached=cached)
    warm = resolved.fixed
    assert resolved.from_cache
    assert calls == [cold.ref_frame]
    assert np.array_equal(warm.fields.gain, cold.fields.gain)
    assert np.array_equal(warm.fields.visibility, cold.fields.visibility)
    assert np.array_equal(warm.fields.white_balance, cold.fields.white_balance)


def _shifted_sources(tmp_path):
    """带位移的 6 帧视频：自动选帧必须落在第 3 帧，seek 偏一帧会改变光照场。"""
    frames = _shifted_frames()
    video = write_video(tmp_path / "gt_shift.mp4", frames)
    atmo = write_png(tmp_path / "atmo_shift.png", frames[3])
    return tmp_path, video, atmo, frames


@pytest.mark.skipif(find_tool("ffmpeg.exe") is None or find_tool("ffprobe.exe") is None,
                    reason="external ffmpeg not populated in bin/ (see bin/README.md)")
def test_case_sample_reuses_the_fixed_cache(tmp_path, monkeypatch):
    """同一 case 的第二次小样命中缓存，且两次小样逐位一致。"""
    _tmp, video, atmo, _frames = _shifted_sources(tmp_path)
    _relight_case(tmp_path, video, atmo)
    stamps = iter([
        datetime(2026, 11, 2, 12, 0, 0).astimezone(),
        datetime(2026, 11, 2, 12, 0, 1).astimezone(),
    ])
    monkeypatch.setattr("vrstudio.cli.relight._now", lambda: next(stamps))

    first = run_video_cli("--case", "rl", "--cases-dir", str(tmp_path),
                          "--sample", "6")
    assert first.exit_code == 0, first.output
    assert "cache miss" in first.output
    second = run_video_cli("--case", "rl", "--cases-dir", str(tmp_path),
                           "--sample", "6")
    assert second.exit_code == 0, second.output
    assert "cache hit" in second.output
    assert (tmp_path / "rl" / "derived" / "relight" / "fixed.json").is_file()

    first_dir = tmp_path / "rl" / "out" / "samples" / "20261102-120000"
    second_dir = tmp_path / "rl" / "out" / "samples" / "20261102-120001"
    first_files = sorted(first_dir.glob("sample_*.png"))
    second_files = sorted(second_dir.glob("sample_*.png"))
    assert len(first_files) == len(second_files) == 6
    for a, b in zip(first_files, second_files):
        assert np.array_equal(cv2.imread(str(a)), cv2.imread(str(b)))

    first_report = json.loads(first_dir.with_suffix(".json").read_text(encoding="utf-8"))
    second_report = json.loads(second_dir.with_suffix(".json").read_text(encoding="utf-8"))
    assert first_report["kind"] == "sample"
    assert first_report["cache"]["enabled"] is True
    assert first_report["cache"]["hit"] is False
    assert second_report["cache"]["hit"] is True
    assert second_report["timing"]["scan"] == 0.0
    assert second_report["timing"]["align"] == 0.0
    assert second_report["timing"]["read"] > 0
    assert second_report["timing"]["total"] == second_report["seconds"]
    assert datetime.fromisoformat(second_report["started_at"]) == (
        datetime(2026, 11, 2, 12, 0, 1).astimezone())


@pytest.mark.skipif(find_tool("ffmpeg.exe") is None or find_tool("ffprobe.exe") is None,
                    reason="external ffmpeg not populated in bin/ (see bin/README.md)")
def test_case_full_run_reuses_the_fixed_cache_and_keeps_pixels(tmp_path, monkeypatch):
    """整段第二次命中缓存，输出帧与冷跑逐位一致。"""
    _tmp, video, atmo, _frames = _shifted_sources(tmp_path)
    _relight_case(tmp_path, video, atmo)
    stamps = iter([
        datetime(2026, 11, 2, 12, 0, 0).astimezone(),
        datetime(2026, 11, 2, 12, 0, 1).astimezone(),
    ])
    monkeypatch.setattr("vrstudio.cli.relight._now", lambda: next(stamps))

    args = ("--case", "rl", "--cases-dir", str(tmp_path),
            "--crf", "0", "--set", "video.preset=ultrafast")
    first = run_video_cli(*args)
    assert first.exit_code == 0, first.output
    second = run_video_cli(*args)
    assert second.exit_code == 0, second.output
    assert "cache hit" in second.output

    first_video = tmp_path / "rl" / "out" / "20261102-120000_relit.mp4"
    second_video = tmp_path / "rl" / "out" / "20261102-120001_relit.mp4"
    assert first_video.is_file() and second_video.is_file()
    for a, b in zip(read_video(first_video), read_video(second_video)):
        assert np.array_equal(a, b)

    first_report = json.loads(first_video.with_suffix(".json").read_text(encoding="utf-8"))
    second_report = json.loads(second_video.with_suffix(".json").read_text(encoding="utf-8"))
    assert first_report["kind"] == "full"
    assert first_report["cache"]["hit"] is False
    assert second_report["cache"]["hit"] is True
    assert second_report["timing"]["render_total"] > 0
    assert second_report["timing"]["total"] == second_report["seconds"]
