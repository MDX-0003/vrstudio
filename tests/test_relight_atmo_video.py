"""视频氛围依据（决策 76）：时间映射、逐帧光场、分辨率/帧数不一致与非法组合。

这里的视频都很小（256×128、6 帧），依赖 ``bin/ffmpeg.exe``；缺二进制时跳过。
逐帧光场的行为用一个水平移动的亮斑验证：输出帧里被提亮的区域应跟着亮斑移动。
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from tests.test_relight import (
    VH,
    VN,
    VW,
    encoder_round_trip_error,
    read_video,
    requires_ffmpeg,
    run_cli,
    video_erp_without_edges,
    write_video,
)
from vrstudio.media.imageio import write_png
from vrstudio.relight.atmo import (
    MAPPING_PROGRESS,
    MAPPING_SEQUENTIAL,
    MAPPING_TIME,
    mapping_for,
    work_frame,
)


def test_mapping_prefers_frame_progress_then_time_then_sequential():
    progress = mapping_for(gt_frames=6, gt_fps=10.0,
                           atmo_frames=3, atmo_fps=5.0)
    assert progress.mode == MAPPING_PROGRESS
    assert [progress.index_for(i) for i in range(6)] == [0, 0, 1, 1, 2, 2]

    by_time = mapping_for(gt_frames=0, gt_fps=10.0,
                          atmo_frames=0, atmo_fps=5.0)
    assert by_time.mode == MAPPING_TIME
    assert by_time.index_for(2) == 1
    assert by_time.index_for(3) == 2

    sequential = mapping_for(gt_frames=0, gt_fps=0.0,
                             atmo_frames=0, atmo_fps=0.0)
    assert sequential.mode == MAPPING_SEQUENTIAL
    assert sequential.index_for(4) == 4


def test_work_frame_returns_a_copy_at_work_size():
    frame = video_erp_without_edges()
    small = work_frame(frame, (64, 32))
    assert small.shape == (32, 64, 3)
    assert not np.shares_memory(small, frame)

    same = work_frame(frame, (VW, VH))
    assert same.shape == frame.shape
    assert not np.shares_memory(same, frame)


def moving_light_frames(width: int = VW, height: int = VH,
                        count: int = VN) -> list[np.ndarray]:
    """同一底图，亮斑从左到右移动：用来验证逐帧光场是否跟着时间走。"""
    base = video_erp_without_edges()
    if (width, height) != (VW, VH):
        base = cv2.resize(base, (width, height), interpolation=cv2.INTER_AREA)
    yy, xx = np.mgrid[0:height, 0:width]
    sigma = max(4.0, width * 0.09)
    frames = []
    for index in range(count):
        cx = width * (0.15 + 0.7 * index / max(count - 1, 1))
        cy = height * 0.55
        blob = np.exp(-(((xx - cx) / sigma) ** 2
                        + ((yy - cy) / (sigma * 0.75)) ** 2) / 2.0)
        lit = base.astype(np.float32) + 95.0 * blob[..., None]
        frames.append(np.clip(lit, 0, 255).astype(np.uint8))
    return frames


def bright_centroid(produced: np.ndarray, source: np.ndarray) -> float:
    """被提亮的水平重心；返回归一化到 0..1 的位置。"""
    delta = (produced.astype(np.float32).mean(axis=2)
             - source.astype(np.float32).mean(axis=2))
    profile = np.clip(delta, 0.0, None).mean(axis=0)
    total = float(profile.sum())
    if total <= 1e-6:
        return 0.5
    return float((profile * np.arange(profile.size)).sum() / total / profile.size)


@requires_ffmpeg
def test_video_atmo_strength_zero_keeps_gt_pixels(tmp_path):
    frames = [video_erp_without_edges() for _ in range(VN)]
    decoded = read_video(write_video(tmp_path / "working.mp4", frames))
    src = write_video(tmp_path / "gt.mp4", decoded)
    noise = encoder_round_trip_error(tmp_path, decoded)
    atmo_frames = [np.clip(f.astype(np.float32) * 0.7, 0, 255).astype(np.uint8)
                   for f in moving_light_frames()]
    atmo = write_video(tmp_path / "atmo.mp4", atmo_frames)

    out = tmp_path / "out.mp4"
    report_path = tmp_path / "report.json"
    result = run_cli("--gt", str(src), "--atmo", str(atmo), "--out", str(out),
                     "--strength", "0", "--crf", "0", "--report", str(report_path),
                     "--set", "video.preset=ultrafast")
    assert result.exit_code == 0, result.output

    got = read_video(out)
    assert len(got) == VN
    for index, (produced, source) in enumerate(zip(got, read_video(src))):
        worst = int(np.abs(produced.astype(int) - source.astype(int)).max())
        assert worst <= noise, f"frame {index} drifted by {worst} > noise {noise}"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["atmo_kind"] == "video"
    assert report["fields_mode"] == "per_frame"
    assert report["atmo_frames"] == VN
    assert report["atmo_mapping"] == MAPPING_PROGRESS
    assert 0 <= report["atmo_ref_frame"] < VN
    assert report["atmo_match"]["frames"] == VN
    assert report["atmo_short"] is False


@requires_ffmpeg
def test_video_atmo_light_follows_the_moving_blob(tmp_path):
    frames = [video_erp_without_edges() for _ in range(VN)]
    src = write_video(tmp_path / "gt.mp4", frames)
    atmo = write_video(tmp_path / "atmo.mp4", moving_light_frames())
    out = tmp_path / "out.mp4"
    report_path = tmp_path / "report.json"
    result = run_cli("--gt", str(src), "--atmo", str(atmo), "--out", str(out),
                     "--strength", "1", "--local-shading", "0.35",
                     "--ref-frame", "0", "--crf", "0",
                     "--report", str(report_path),
                     "--set", "video.preset=ultrafast")
    assert result.exit_code == 0, result.output

    source = read_video(src)
    got = read_video(out)
    assert len(got) == VN
    centroids = [bright_centroid(a, b) for a, b in zip(got, source)]
    assert centroids[-1] > centroids[0] + 0.15, centroids
    # 亮斑单调右移；允许编码噪声造成一次很小的回退。
    increases = sum(1 for a, b in zip(centroids, centroids[1:]) if b > a)
    assert increases >= VN - 2, centroids

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["fields_mode"] == "per_frame"
    assert report["atmo_match"]["mean"] > 0.0


@requires_ffmpeg
def test_video_atmo_accepts_other_resolution_and_frame_count(tmp_path):
    frames = [video_erp_without_edges() for _ in range(VN)]
    src = write_video(tmp_path / "gt.mp4", frames)
    atmo = write_video(tmp_path / "atmo.mp4",
                       moving_light_frames(width=VW // 2, height=VH // 2, count=3))
    out = tmp_path / "samples"
    report_path = tmp_path / "sample.json"
    result = run_cli("--gt", str(src), "--atmo", str(atmo), "--out", str(out),
                     "--sample", "3", "--report", str(report_path),
                     "--set", "video.preset=ultrafast")
    assert result.exit_code == 0, result.output
    assert len(list(out.glob("sample_*.png"))) == 3

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["atmo_kind"] == "video"
    assert report["fields_mode"] == "per_frame"
    assert report["atmo_frames"] == 3
    assert report["atmo_mapping"] == MAPPING_PROGRESS
    assert report["sample"]["count"] == 3
    assert report["atmo_match"]["frames"] == 3


@requires_ffmpeg
def test_image_gt_rejects_video_atmo(tmp_path):
    gt = write_png(tmp_path / "gt.png", video_erp_without_edges())
    atmo = write_video(tmp_path / "atmo.mp4", moving_light_frames(count=3))
    out = tmp_path / "o.png"
    result = run_cli("--gt", str(gt), "--atmo", str(atmo), "--out", str(out))
    assert result.exit_code == 2, result.output
    assert "视频氛围只适用于视频底版" in result.output
    assert not out.exists()


@requires_ffmpeg
def test_video_atmo_rejects_non_2_to_1(tmp_path):
    frames = [video_erp_without_edges() for _ in range(VN)]
    src = write_video(tmp_path / "gt.mp4", frames)
    bad = write_video(tmp_path / "atmo.mp4",
                      [np.full((100, 256, 3), 120, np.uint8) for _ in range(3)])
    out = tmp_path / "out.mp4"
    result = run_cli("--gt", str(src), "--atmo", str(bad), "--out", str(out))
    assert result.exit_code == 2, result.output
    assert "氛围视频必须是 2:1" in result.output
    assert not out.exists()
