"""区域参数覆盖（决策 88）：解析、光栅化、叠加、合成与 CLI 契约。

这些测试不依赖 ffmpeg：区域几何与逐像素参数场都在内存里验证；视频路径的
``--sample-at`` 只测参数校验，真实出帧见 ``test_relight_video.py`` 的端到端用例。
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from vrstudio import config as config_mod
from vrstudio.cli import app
from vrstudio.media.binres import find_tool
from vrstudio.media.imageio import write_png
from vrstudio.media.videoio import FrameReader, FrameWriter, probe
from vrstudio.relight.align import work_size
from vrstudio.relight.compose import light_compose, soft_clip
from vrstudio.relight.fields import estimate_fields
from vrstudio.relight.regions import (
    MAX_REGIONS,
    RegionError,
    build_maps,
    load_regions,
    parse_regions,
    upscale_clip,
)

runner = CliRunner()


def textured_erp(width: int = 480, height: int | None = None, seed: int = 3) -> np.ndarray:
    height = height or width // 2
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 256, (height, width), dtype=np.uint8)
    blobs = cv2.GaussianBlur(noise, (0, 0), 2.0)
    blobs = cv2.normalize(blobs, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    x = np.arange(width, dtype=np.float32)[None, :]
    y = np.arange(height, dtype=np.float32)[:, None]
    stripes = (20 * np.sin(2 * np.pi * 9 * x / width)
               + 12 * np.cos(2 * np.pi * 3 * y / height)).astype(np.float32)
    gray = np.clip(blobs.astype(np.float32) + stripes, 0, 255).astype(np.uint8)
    return np.repeat(gray[:, :, None], 3, axis=2)


def region_doc(verts, *, strengths=None, shading=None, clip=None,
               feather=0.0, enabled=True, name="r1") -> dict:
    params: dict = {}
    if strengths is not None:
        params["strength"] = strengths
    if shading is not None:
        params["local_shading"] = shading
    if clip is not None:
        params["clip"] = {"knee": clip[0], "max": clip[1]}
    return {
        "version": 1,
        "regions": [{
            "id": name, "name": name, "color": "#f59e0b", "enabled": enabled,
            "verts": [list(point) for point in verts],
            "feather_deg": feather, "params": params, "ranges": None,
        }],
    }


# ── 解析与校验 ─────────────────────────────────────────────────────────────


def test_parse_accepts_a_minimal_document():
    regions = parse_regions(region_doc([[-30, -20], [30, -20], [0, 20]],
                                       strengths=0.4, shading=0.35,
                                       clip=(0.7, 0.95)))
    assert len(regions) == 1
    region = regions[0]
    assert region.params.strength == 0.4
    assert region.params.local_shading == 0.35
    assert region.params.clip == (0.7, 0.95)
    assert region.enabled and region.feather_deg == 0.0


def test_parse_rejects_unknown_keys_and_bad_version():
    doc = region_doc([[-30, -20], [30, -20], [0, 20]])
    doc["extra"] = 1
    with pytest.raises(RegionError, match="不认识的顶层键"):
        parse_regions(doc)
    doc = region_doc([[-30, -20], [30, -20], [0, 20]])
    doc["version"] = 2
    with pytest.raises(RegionError, match="version"):
        parse_regions(doc)
    doc = region_doc([[-30, -20], [30, -20], [0, 20]])
    doc["regions"][0]["params"]["temperature"] = 0.2
    with pytest.raises(RegionError, match="params 不认识的键"):
        parse_regions(doc)


def test_parse_rejects_ranges_clip_and_geometry_limits():
    doc = region_doc([[-30, -20], [30, -20], [0, 20]])
    doc["regions"][0]["ranges"] = [{"start": 1.0, "end": 2.0}]
    with pytest.raises(RegionError, match="ranges"):
        parse_regions(doc)

    doc = region_doc([[-30, -20], [30, -20], [0, 20]], clip=(0.9, 0.5))
    with pytest.raises(RegionError, match="knee"):
        parse_regions(doc)

    doc = region_doc([[-30, -20], [30, -20]])
    with pytest.raises(RegionError, match="顶点数"):
        parse_regions(doc)

    doc = region_doc([[-30, -89.9], [30, -20], [0, 20]])
    with pytest.raises(RegionError, match="纬度"):
        parse_regions(doc)

    doc = region_doc([[-30, -20], [30, -20], [0, 20]])
    doc["regions"] = doc["regions"] * (MAX_REGIONS + 1)
    with pytest.raises(RegionError, match="最多"):
        parse_regions(doc)


def test_load_reports_the_file_on_bad_json(tmp_path):
    path = tmp_path / "regions.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RegionError, match="合法 JSON"):
        load_regions(path)
    with pytest.raises(RegionError, match="读不到"):
        load_regions(tmp_path / "missing.json")


# ── 光栅化与参数场 ─────────────────────────────────────────────────────────


def test_rasterize_rectangle_covers_the_expected_pixels():
    # work 360x180：1 度 = 1 像素，便于按经纬度直接断言。
    regions = parse_regions(region_doc(
        [[-30, -20], [30, -20], [30, 20], [-30, 20]], strengths=0.25))
    maps = build_maps(regions, (360, 180), strength=1.0, local_shading=0.35,
                      clip=None)
    assert maps is not None
    field = maps.strength
    assert field[90, 180] == pytest.approx(0.25)
    assert field[90, 10] == pytest.approx(1.0)
    # 经度 -30..30 → 列 150..210，纬度 -20..20 → 行 70..110；
    # fillPoly 的边界像素闭区间，允许一格误差。
    assert 0.036 < maps.coverage["r1"] < 0.040


def test_rasterize_wraps_across_the_seam():
    regions = parse_regions(region_doc(
        [[170, -20], [190, -20], [190, 20], [170, 20]], strengths=0.0))
    maps = build_maps(regions, (360, 180), strength=1.0, local_shading=0.35,
                      clip=None)
    assert maps is not None
    assert maps.strength[90, 5] == pytest.approx(0.0)      # 190° 落到左边
    assert maps.strength[90, 355] == pytest.approx(0.0)    # 170° 仍在右边
    assert maps.strength[90, 180] == pytest.approx(1.0)    # 中间没被碰到


def test_feather_ramps_between_zero_and_one():
    regions = parse_regions(region_doc(
        [[-30, -20], [30, -20], [30, 20], [-30, 20]],
        strengths=0.0, feather=10.0))
    maps = build_maps(regions, (360, 180), strength=1.0, local_shading=0.35,
                      clip=None)
    assert maps is not None
    center = maps.strength[90, 180]
    edge = maps.strength[90, 150]          # 边界上
    outside = maps.strength[90, 140]
    assert center == pytest.approx(0.0, abs=1e-6)
    assert 0.1 < edge < 0.9
    assert outside == pytest.approx(1.0)


def test_build_maps_is_none_without_enabled_regions():
    assert build_maps([], (360, 180), strength=1.0, local_shading=0.35,
                      clip=None) is None
    regions = parse_regions(region_doc(
        [[-30, -20], [30, -20], [0, 20]], strengths=0.2, enabled=False))
    assert build_maps(regions, (360, 180), strength=1.0, local_shading=0.35,
                      clip=None) is None


def test_maps_only_override_declared_channels():
    regions = parse_regions(region_doc(
        [[-30, -20], [30, -20], [30, 20], [-30, 20]],
        strengths=0.2, shading=None))
    maps = build_maps(regions, (360, 180), strength=1.0, local_shading=0.35,
                      clip=None)
    assert maps is not None
    assert maps.strength[90, 180] == pytest.approx(0.2)
    assert maps.local_shading[90, 180] == pytest.approx(0.35)


def test_later_region_wins_on_overlap():
    doc = region_doc([[-30, -20], [30, -20], [30, 20], [-30, 20]], strengths=0.2)
    doc["regions"].append({
        "id": "r2", "name": "r2", "color": "#38bdf8", "enabled": True,
        "verts": [[-10, -10], [10, -10], [10, 10], [-10, 10]],
        "feather_deg": 0.0, "params": {"strength": 0.8}, "ranges": None,
    })
    maps = build_maps(parse_regions(doc), (360, 180), strength=1.0,
                      local_shading=0.35, clip=None)
    assert maps is not None
    assert maps.strength[90, 180] == pytest.approx(0.8)   # 小区域在上
    assert maps.strength[90, 160] == pytest.approx(0.2)   # 只被大区域覆盖
    assert maps.strength[90, 100] == pytest.approx(1.0)


# ── 合成：区域强度 0 与原色温 ──────────────────────────────────────────────


def _fields_for(width: int = 480) -> tuple[np.ndarray, object]:
    gt = textured_erp(width, width // 2, seed=21)
    atmo = np.clip(gt.astype(np.float32) * 0.6, 0, 255).astype(np.uint8)
    return gt, estimate_fields(gt, atmo)


def test_region_zero_strength_matches_gt_inside_and_plain_outside():
    gt, fields = _fields_for(480)
    size = (gt.shape[1], gt.shape[0])
    plain = light_compose(fields, size, 1.0, 0.35).apply(gt)
    regions = parse_regions(region_doc(
        [[-40, -30], [40, -30], [40, 30], [-40, 30]], strengths=0.0))
    maps = build_maps(regions, work_size(*size), strength=1.0,
                      local_shading=0.35, clip=None)
    assert maps is not None
    out = light_compose(fields, size, maps.strength, maps.local_shading).apply(gt)

    center = (size[1] // 2, size[0] // 2)
    assert np.array_equal(out[center], gt[center])
    corner = (4, 4)
    assert int(np.abs(out[corner].astype(int) - plain[corner].astype(int)).max()) <= 1


def test_regional_clip_only_touches_its_pixels():
    bright = np.full((180, 360, 3), 250, dtype=np.uint8)
    regions = parse_regions(region_doc(
        [[-30, -20], [30, -20], [30, 20], [-30, 20]], clip=(0.5, 0.9)))
    maps = build_maps(regions, (360, 180), strength=1.0, local_shading=0.35,
                      clip=None)
    assert maps is not None
    clip_full = upscale_clip(maps, (360, 180))
    assert clip_full is not None
    out = soft_clip(bright, clip_full[0], clip_full[1])
    assert out[90, 180, 0] < 245           # 区域中心被压暗
    assert np.array_equal(out[90, 10], bright[90, 10])   # 区域外原样


def test_scalar_soft_clip_is_unchanged_by_the_map_path():
    rng = np.random.default_rng(5)
    frame = rng.integers(0, 256, (64, 96, 3), dtype=np.uint8)
    knee = np.full((64, 96), 0.72, dtype=np.float32)
    max_out = np.full((64, 96), 0.98, dtype=np.float32)
    scalar = soft_clip(frame, 0.72, 0.98)
    mapped = soft_clip(frame, knee, max_out)
    assert np.array_equal(scalar, mapped)


# ── CLI 契约 ───────────────────────────────────────────────────────────────


def _write_case(tmp_path: Path) -> tuple[Path, Path]:
    gt = textured_erp(256, 128, seed=9)
    atmo = np.clip(gt.astype(np.float32) * 0.7, 0, 255).astype(np.uint8)
    gt_path = write_png(tmp_path / "gt.png", gt)
    atmo_path = write_png(tmp_path / "atmo.png", atmo)
    return gt_path, atmo_path


def test_cli_rejects_a_bad_regions_file(tmp_path):
    gt_path, atmo_path = _write_case(tmp_path)
    bad = tmp_path / "regions.json"
    bad.write_text('{"version": 1, "regions": [{"id": "r1"}]}', encoding="utf-8")
    result = runner.invoke(app, [
        "relight", "--gt", str(gt_path), "--atmo", str(atmo_path),
        "--out", str(tmp_path / "out.png"), "--regions", str(bad),
    ])
    assert result.exit_code == 2
    assert "区域" in result.output


def test_cli_rejects_sample_at_on_image_gt(tmp_path):
    gt_path, atmo_path = _write_case(tmp_path)
    result = runner.invoke(app, [
        "relight", "--gt", str(gt_path), "--atmo", str(atmo_path),
        "--out", str(tmp_path / "out.png"), "--sample-at", "1,2",
    ])
    assert result.exit_code == 2
    assert "--sample-at" in result.output


def test_cli_sample_at_and_sample_are_exclusive(tmp_path):
    result = runner.invoke(app, [
        "relight", "--gt", str(tmp_path / "a.mp4"),
        "--atmo", str(tmp_path / "a.png"),
        "--out", str(tmp_path / "samples"),
        "--sample", "3", "--sample-at", "1,2",
    ])
    assert result.exit_code == 2
    assert "只能选一种" in result.output


def test_cli_applies_regions_and_reports_them(tmp_path):
    gt_path, atmo_path = _write_case(tmp_path)
    gt = cv2.imread(str(gt_path), cv2.IMREAD_COLOR)
    regions_path = tmp_path / "regions.json"
    regions_path.write_text(json.dumps(region_doc(
        [[-60, -15], [-20, -15], [-20, 15], [-60, 15]], strengths=0.0)), encoding="utf-8")

    out_regions = tmp_path / "regions.png"
    report_path = tmp_path / "regions_report.json"
    result = runner.invoke(app, [
        "relight", "--gt", str(gt_path), "--atmo", str(atmo_path),
        "--out", str(out_regions), "--regions", str(regions_path),
        "--report", str(report_path),
    ])
    assert result.exit_code == 0, result.output
    produced = cv2.imread(str(out_regions), cv2.IMREAD_COLOR)

    out_plain = tmp_path / "plain.png"
    result = runner.invoke(app, [
        "relight", "--gt", str(gt_path), "--atmo", str(atmo_path),
        "--out", str(out_plain),
    ])
    assert result.exit_code == 0, result.output
    plain = cv2.imread(str(out_plain), cv2.IMREAD_COLOR)

    # 区域中心（lon -40、lat 0 → x≈85，y=64）与 GT 逐像素一致。
    assert np.array_equal(produced[64, 85], gt[64, 85])
    # 区域外与无区域运行最多差一个 LSB（逐像素白平衡的混合写法）。
    assert int(np.abs(produced[8, 8].astype(int) - plain[8, 8].astype(int)).max()) <= 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["regions"]["count"] == 1
    assert report["regions"]["enabled"] == 1
    assert report["regions"]["regions"][0]["id"] == "r1"
    assert report["regions"]["regions"][0]["coverage"] > 0


def test_cli_report_has_null_regions_without_a_file(tmp_path):
    gt_path, atmo_path = _write_case(tmp_path)
    report_path = tmp_path / "report.json"
    result = runner.invoke(app, [
        "relight", "--gt", str(gt_path), "--atmo", str(atmo_path),
        "--out", str(tmp_path / "out.png"), "--report", str(report_path),
    ])
    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["regions"] is None


# ── 视频：--sample-at 与区域接线 ──────────────────────────────────────────


requires_ffmpeg = pytest.mark.skipif(
    find_tool("ffmpeg.exe") is None or find_tool("ffprobe.exe") is None,
    reason="external ffmpeg not populated in bin/ (see bin/README.md)",
)


def _video_conf() -> config_mod.Config:
    conf = config_mod.load()
    conf.data["video"]["preset"] = "ultrafast"
    conf.data["video"]["crf"] = 0
    return conf


def _write_video(path: Path, frames: list[np.ndarray]) -> Path:
    conf = _video_conf()
    with FrameWriter(path, (frames[0].shape[1], frames[0].shape[0]), "10/1",
                     conf.video, config=conf) as writer:
        for frame in frames:
            writer.write(frame)
    return path


@requires_ffmpeg
def test_video_sample_at_with_regions(tmp_path):
    frame = textured_erp(256, 128, seed=31)
    frames = [frame.copy() for _ in range(6)]
    gt_path = _write_video(tmp_path / "gt.mp4", frames)
    atmo_path = write_png(tmp_path / "atmo.png",
                          np.clip(frame.astype(np.float32) * 0.7, 0, 255).astype(np.uint8))
    regions_path = tmp_path / "regions.json"
    regions_path.write_text(json.dumps(region_doc(
        [[-60, -15], [-20, -15], [-20, 15], [-60, 15]], strengths=0.0)),
        encoding="utf-8")
    out_dir = tmp_path / "samples"
    report_path = tmp_path / "report.json"

    result = runner.invoke(app, [
        "relight", "--gt", str(gt_path), "--atmo", str(atmo_path),
        "--out", str(out_dir), "--sample-at", "0,3",
        "--regions", str(regions_path), "--report", str(report_path),
        "--set", "video.preset=ultrafast",
    ])
    assert result.exit_code == 0, result.output
    written = sorted(path.name for path in out_dir.glob("*.png"))
    assert written == ["sample_000000.png", "sample_000003.png"]

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["kind"] == "sample"
    assert report["sample"]["frames"] == [0, 3]
    assert report["regions"]["count"] == 1

    produced = cv2.imread(str(out_dir / "sample_000000.png"), cv2.IMREAD_COLOR)
    conf = _video_conf()
    with FrameReader(probe(gt_path, conf), conf) as reader:
        decoded = next(iter(reader)).copy()
    # 区域中心（lon −40 → x≈100，lat 0 → y=64），离开边界足够远。
    assert np.array_equal(produced[64, 100], decoded[64, 100])
