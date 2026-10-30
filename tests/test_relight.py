"""重打光（决策 48）：相似变换恢复、strength=0 恒等、对齐闸门、CLI 契约。

CLI 三条错误路径分别对应退出码：比例/尺寸/参数越界是 2，读图失败与对齐不可信是 1。

8K 冒烟用真实素材（``sources/``、``cases/``，都不进 git），缺文件时跳过；标 ``slow``，
本地用 ``uv run pytest -m slow`` 显式跑。
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
from vrstudio.media.imageio import write_png
from vrstudio.relight.align import (
    MIN_STRUCTURE_CORRELATION,
    estimate_similarity,
    matrix_at_full_size,
    warp_to_gt,
)
from vrstudio.relight.compose import clipped_fraction, relight, soft_clip
from vrstudio.relight.fields import estimate_fields

runner = CliRunner()


def textured_erp(width: int = 2048, height: int | None = None, seed: int = 3) -> np.ndarray:
    """带大量可匹配结构的 2:1 图：随机噪声低通成斑块，再叠一点周期性纹理。"""
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


def similarity(scale: float, angle_deg: float, tx: float, ty: float) -> np.ndarray:
    theta = np.radians(angle_deg)
    return np.array([
        [scale * np.cos(theta), -scale * np.sin(theta), tx],
        [scale * np.sin(theta), scale * np.cos(theta), ty],
    ], dtype=np.float64)


def write_image(tmp_path: Path, name: str, image: np.ndarray) -> Path:
    return write_png(tmp_path / name, image)


def run_cli(*args: str):
    return runner.invoke(app, ["relight", *args])


# ── 几何：已知变换必须被恢复 ───────────────────────────────────────────────


def test_synthetic_similarity_recovered():
    gt = textured_erp(2048, 1024, seed=11)
    truth = similarity(1.02, -0.2, 5.2, -3.4)
    atmo = cv2.warpAffine(gt, cv2.invertAffineTransform(truth), (2048, 1024),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    result = estimate_similarity(gt, atmo)
    assert result.fallback is None
    assert abs(result.scale - 1.02) <= 0.005
    assert abs(result.angle_deg - (-0.2)) <= 0.2
    assert abs(result.tx - 5.2) <= 1.0
    assert abs(result.ty - (-3.4)) <= 1.0
    assert result.trusted and result.correlation_after > 0.3


def test_estimate_is_deterministic():
    gt = textured_erp(1024, 512, seed=5)
    atmo = cv2.warpAffine(gt, np.float32([[1, 0, 6.5], [0, 1, -4.0]]), (1024, 512),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    first = estimate_similarity(gt, atmo)
    second = estimate_similarity(gt, atmo)
    assert np.array_equal(first.matrix, second.matrix)
    assert first.inliers == second.inliers
    assert first.correlation_after == second.correlation_after


def test_full_size_matrix_only_scales_translation():
    gt = textured_erp(1024, 512, seed=2)
    result = estimate_similarity(gt, gt)
    matrix = matrix_at_full_size(result, 8192)
    assert matrix[0, 0] == pytest.approx(result.matrix[0, 0])
    assert matrix[0, 1] == pytest.approx(result.matrix[0, 1])
    assert matrix[0, 2] == pytest.approx(result.matrix[0, 2] * 4.0)


def test_unrelated_images_fail_the_gate():
    gt = textured_erp(1024, 512, seed=21)
    atmo = textured_erp(1024, 512, seed=22)
    result = estimate_similarity(gt, atmo)
    assert result.correlation_after < MIN_STRUCTURE_CORRELATION
    assert not result.trusted


# ── 像素：strength=0 必须与 GT 逐位一致 ───────────────────────────────────


def test_strength_zero_is_bit_exact_on_full_path():
    gt = textured_erp(256, 128, seed=7)
    atmo = np.clip(gt.astype(np.float32) * 0.55 + 18, 0, 255).astype(np.uint8)
    aligned = warp_to_gt(atmo, estimate_similarity(gt, atmo))
    fields = estimate_fields(gt, aligned)
    out = relight(gt, fields, 0.0, 1.0)
    assert out.dtype == np.uint8 and out.shape == gt.shape
    assert np.array_equal(out, gt)


@pytest.mark.parametrize("local_shading", [0.0, 0.35, 1.0])
def test_strength_zero_ignores_local_shading(local_shading):
    gt = textured_erp(128, 64, seed=8)
    atmo = np.clip(255 - gt, 0, 255).astype(np.uint8)
    fields = estimate_fields(gt, atmo)
    assert np.array_equal(relight(gt, fields, 0.0, local_shading), gt)


def test_strength_zero_round_trips_extremes():
    gt = np.zeros((64, 128, 3), np.uint8)
    gt[:, 0:16] = 255
    gt[:, 16:32] = 1
    gt[:, 32:48] = 254
    fields = estimate_fields(gt, gt)
    assert np.array_equal(relight(gt, fields, 0.0, 0.35), gt)


def test_soft_clip_is_monotone_and_bounded():
    ramp = np.arange(256, dtype=np.uint8).reshape(1, -1)
    image = np.repeat(np.repeat(ramp, 8, axis=0)[:, :, None], 3, axis=2)
    clipped = soft_clip(image, knee=0.72, max_out=0.98)
    assert clipped.max() <= 250
    luma = clipped[:, :, 0].astype(int).mean(axis=0)
    assert np.all(np.diff(luma) >= 0)

    midtones = np.full((32, 32, 3), 120, np.uint8)
    assert clipped_fraction(midtones) == 0.0
    assert np.array_equal(soft_clip(midtones), midtones)
    assert clipped_fraction(clipped) == 0.0


# ── CLI：退出码、输出、报告 ────────────────────────────────────────────────


def test_cli_strength_zero_matches_gt(tmp_path):
    gt = textured_erp(256, 128, seed=9)
    atmo = np.clip(gt.astype(np.float32) * 0.7, 0, 255).astype(np.uint8)
    gt_path = write_image(tmp_path, "gt.png", gt)
    atmo_path = write_image(tmp_path, "atmo.png", atmo)
    out_path = tmp_path / "out.png"
    report_path = tmp_path / "report.json"
    result = run_cli("--gt", str(gt_path), "--atmo", str(atmo_path),
                     "--out", str(out_path), "--strength", "0",
                     "--report", str(report_path))
    assert result.exit_code == 0, result.output
    produced = cv2.imread(str(out_path), cv2.IMREAD_COLOR)
    assert np.array_equal(produced, gt)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(report) == {"gt", "atmo", "size", "params", "align", "clip", "light", "seconds"}
    assert set(report["params"]) == {"scene", "strength", "local_shading",
                                     "highlight_clip", "clip_knee", "clip_max"}
    assert set(report["align"]) == {
        "enabled", "fallback", "trusted", "keypoints_gt", "keypoints_atmo", "matches",
        "inliers", "scale", "angle_deg", "tx_2k", "ty_2k", "tx", "ty",
        "correlation_before", "correlation_after"}
    assert report["params"]["strength"] == 0.0
    assert report["clip"] is None and report["light"] is None
    assert report["size"] == [256, 128]


def test_cli_scene_presets(tmp_path):
    gt = textured_erp(128, 64, seed=12)
    gt_path = write_image(tmp_path, "gt.png", gt)
    for scene, strength, shading in (("night", 1.0, 0.35), ("day", 0.8, 0.2)):
        report_path = tmp_path / f"{scene}.json"
        result = run_cli("--gt", str(gt_path), "--atmo", str(gt_path),
                         "--out", str(tmp_path / f"{scene}.png"),
                         "--scene", scene, "--report", str(report_path))
        assert result.exit_code == 0, result.output
        params = json.loads(report_path.read_text(encoding="utf-8"))["params"]
        assert (params["strength"], params["local_shading"]) == (strength, shading)


def test_cli_dumps_four_diagnostics(tmp_path):
    gt = textured_erp(128, 64, seed=13)
    gt_path = write_image(tmp_path, "gt.png", gt)
    out_path = tmp_path / "out.png"
    result = run_cli("--gt", str(gt_path), "--atmo", str(gt_path),
                     "--out", str(out_path), "--dump-light")
    assert result.exit_code == 0, result.output
    for suffix in ("_light.png", "_gain.png", "_visibility.png", "_light_cmp.png"):
        assert (tmp_path / f"out{suffix}").is_file()


def test_cli_rejects_bad_aspect(tmp_path):
    gt = write_image(tmp_path, "gt.png", textured_erp(300, 100, seed=1))
    atmo = write_image(tmp_path, "atmo.png", textured_erp(300, 100, seed=2))
    result = run_cli("--gt", str(gt), "--atmo", str(atmo), "--out", str(tmp_path / "o.png"))
    assert result.exit_code == 2
    assert "2:1" in result.output
    assert not (tmp_path / "o.png").exists()


def test_cli_rejects_size_mismatch(tmp_path):
    gt = write_image(tmp_path, "gt.png", textured_erp(200, 100, seed=1))
    atmo = write_image(tmp_path, "atmo.png", textured_erp(128, 64, seed=2))
    result = run_cli("--gt", str(gt), "--atmo", str(atmo), "--out", str(tmp_path / "o.png"))
    assert result.exit_code == 2
    assert "尺寸不一致" in result.output


@pytest.mark.parametrize("extra", [
    ("--strength", "1.5"),
    ("--strength", "-0.2"),
    ("--local-shading", "2"),
    ("--scene", "dusk"),
    ("--highlight-clip", "--clip-knee", "0.9", "--clip-max", "0.8"),
])
def test_cli_rejects_bad_parameters(tmp_path, extra):
    gt = write_image(tmp_path, "gt.png", textured_erp(64, 32, seed=1))
    result = run_cli("--gt", str(gt), "--atmo", str(gt), "--out", str(tmp_path / "o.png"),
                     *extra)
    assert result.exit_code == 2
    assert not (tmp_path / "o.png").exists()


def test_cli_missing_input_is_runtime_failure(tmp_path):
    gt = write_image(tmp_path, "gt.png", textured_erp(64, 32, seed=1))
    result = run_cli("--gt", str(gt), "--atmo", str(tmp_path / "nope.png"),
                     "--out", str(tmp_path / "o.png"))
    assert result.exit_code == 1


def test_cli_alignment_failure_exits_1_without_output(tmp_path):
    gt = write_image(tmp_path, "gt.png", textured_erp(512, 256, seed=31))
    atmo = write_image(tmp_path, "atmo.png", textured_erp(512, 256, seed=32))
    out_path = tmp_path / "o.png"
    result = run_cli("--gt", str(gt), "--atmo", str(atmo), "--out", str(out_path))
    assert result.exit_code == 1
    assert "对齐不可信" in result.output
    assert not out_path.exists()


def test_cli_writes_utf8_report_to_missing_directory(tmp_path):
    gt = write_image(tmp_path, "gt.png", textured_erp(64, 32, seed=14))
    report_path = tmp_path / "深层" / "report.json"
    result = run_cli("--gt", str(gt), "--atmo", str(gt),
                     "--out", str(tmp_path / "out.png"), "--report", str(report_path))
    assert result.exit_code == 0, result.output
    assert json.loads(report_path.read_text(encoding="utf-8"))["params"]["scene"] == "night"


# ── 8K 真实素材冒烟（slow）────────────────────────────────────────────────


@pytest.mark.slow
def test_8k_real_pair_end_to_end(tmp_path):
    root = config_mod.REPO_ROOT
    gt_path = root / "sources" / "360erp_test.png"
    atmo_path = root / "cases" / "frdi_lo1" / "out" / "img-2a7da464_cand1.png"
    if not (gt_path.is_file() and atmo_path.is_file()):
        pytest.skip("缺少 8K 素材（sources/360erp_test.png 或 cases/frdi_lo1 的候选）")

    out_path = tmp_path / "relit.png"
    report_path = tmp_path / "relit.json"
    result = run_cli("--gt", str(gt_path), "--atmo", str(atmo_path), "--out", str(out_path),
                     "--report", str(report_path))
    assert result.exit_code == 0, result.output
    gt = cv2.imread(str(gt_path), cv2.IMREAD_COLOR)
    produced = cv2.imread(str(out_path), cv2.IMREAD_COLOR)
    assert produced.shape == gt.shape == (4096, 8192, 3)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["align"]["trusted"] is True
    assert report["align"]["inliers"] >= 12
    assert report["seconds"] <= 30.0
