"""重打光（决策 48）：相似变换恢复、strength=0 恒等、对齐闸门、CLI 契约。

CLI 三条错误路径分别对应退出码：比例/尺寸/参数越界是 2，读图失败与对齐不可信是 1。

视频路径（决策 56，2026-10-30 起由同一条 ``relight`` 按 GT 媒体类型派发，决策 60）
在本文件末尾：拍平光场的逐位一致性、真实文件端到端（需要 ``bin/ffmpeg.exe``，
缺了跳过）。它的视频错误路径在 ``tests/test_relight_video.py``。决策 64 起不再做
逐帧同位检查。

8K 冒烟用真实素材（``sources/``、``cases/``，都不进 git），缺文件时跳过；标 ``slow``，
本地用 ``uv run pytest -m slow`` 显式跑。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from vrstudio import config as config_mod
from vrstudio.cli import app
from vrstudio.media.binres import find_tool
from vrstudio.media.imageio import write_png
from vrstudio.media.videoio import u8_reader, FrameWriter, probe
from vrstudio.relight.align import (
    MIN_STRUCTURE_CORRELATION,
    estimate_similarity,
    gray8,
    matrix_at_full_size,
    warp_to_gt,
)
from vrstudio.relight.compose import (
    CHROMA_SIGMA,
    LightCompose,
    chroma_lowpass,
    clipped_fraction,
    light_compose,
    relight,
    soft_clip,
)
from vrstudio.relight.fields import (
    SHADOW_PIVOT,
    LightFields,
    estimate_fields,
    linear_to_srgb8,
    shadow_weight,
    srgb_to_linear,
)
from vrstudio.relight.bands import executor, workers_for

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


def _reference_soft_clip(bgr8, knee=0.72, max_out=0.98):
    x = np.clip(bgr8.astype(np.float32), 0, 255) / 255.0
    y = 0.0722 * x[..., 0] + 0.7152 * x[..., 1] + 0.2126 * x[..., 2]
    over = y > knee
    y_clipped = y.copy()
    span = max(max_out - knee, 1e-3)
    y_clipped[over] = max_out - span * np.exp(-(y[over] - knee) / span)
    scaled = x * (y_clipped / np.maximum(y, 1e-6))[..., None] * 255.0
    return np.clip(scaled, 0, 255).astype(np.uint8)


def test_soft_clip_matches_the_reference_including_overexposure():
    rng = np.random.default_rng(7)
    image = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
    image[:8, :8] = 255
    image[20:24, 30:40] = 230
    assert np.array_equal(soft_clip(image), _reference_soft_clip(image))


def _reference_apply(gt8, gain, visibility, white_balance):
    gt_linear = srgb_to_linear(gt8).astype(np.float32)
    out_linear = (np.einsum("...k,ck->...c", gt_linear, white_balance)
                  * gain[..., None] * visibility[..., None])
    return linear_to_srgb8(np.clip(out_linear, 0.0, 1.0))


def test_light_compose_matches_the_reference_formula():
    rng = np.random.default_rng(5)
    gt = rng.integers(0, 256, (64, 96, 3), dtype=np.uint8)
    gain = rng.uniform(0.4, 1.6, (64, 96)).astype(np.float32)
    visibility = rng.uniform(0.5, 1.5, (64, 96)).astype(np.float32)
    white_balance = np.array(
        [[1.02, 0.01, 0.0], [0.0, 0.99, 0.02], [0.01, 0.0, 1.01]], np.float32)

    got = LightCompose(gain=gain, visibility=visibility,
                       white_balance=white_balance).apply(gt)
    want = _reference_apply(gt, gain, visibility, white_balance)
    assert int(np.abs(got.astype(np.int16) - want).max()) == 0


# ── 暗部置信权重（决策 84）：比值只在 GT 有信号处生效 ─────────────────────


def test_shadow_weight_is_a_smooth_zero_to_one_gate():
    zero = np.zeros((1, 1, 3), np.float32)
    at_pivot = np.full((1, 1, 3), SHADOW_PIVOT, np.float32)
    bright = np.full((1, 1, 3), 1.0, np.float32)
    assert float(shadow_weight(zero)[0, 0]) == 0.0
    assert float(shadow_weight(at_pivot)[0, 0]) == pytest.approx(0.5)
    assert float(shadow_weight(bright)[0, 0]) > 0.98

    ramp = np.linspace(0.0, 1.0, 64, dtype=np.float32).reshape(1, 64, 1)
    ramp = np.repeat(ramp, 3, axis=2)
    weight = shadow_weight(ramp)
    assert weight.min() >= 0.0 and weight.max() <= 1.0
    assert np.all(np.diff(weight[0]) >= 0)


def test_light_compose_leaves_black_gt_untouched_under_a_huge_gain():
    """近黑处 gain 可以是 149 倍；权重为 0 时乘算场必须完全不进入。"""
    fields = LightFields(
        gain=np.full((8, 16), 149.0, np.float32),
        visibility=np.full((8, 16), 2.0, np.float32),
        light=np.zeros((8, 16, 3), np.float32),
        gt_light=np.zeros((8, 16, 3), np.float32),
        white_balance=np.eye(3, dtype=np.float32),
    )
    gt = np.zeros((8, 16, 3), np.uint8)
    out = light_compose(fields, (16, 8), 1.0, 1.0).apply(gt)
    assert np.array_equal(out, gt)


def test_strength_stays_monotone_under_the_shadow_weight():
    """strength 越大离 full relight 越远/越近都不会来回摆；0 仍然是 GT。"""
    gt = textured_erp(128, 64, seed=51)
    atmo = np.clip(gt.astype(np.float32) * 0.5 + 60, 0, 255).astype(np.uint8)
    fields = estimate_fields(gt, atmo)
    means = [light_compose(fields, (128, 64), s, 0.35).apply(gt).mean()
             for s in (0.0, 0.3, 0.6, 0.9, 1.0)]
    assert means[0] == pytest.approx(gt.mean())
    diffs = np.diff(means)
    assert np.all(diffs >= -1e-9) or np.all(diffs <= 1e-9), means


# ── 色度差低通（决策 85）：亮度不动，色度台阶收敛 ─────────────────────────


def _chroma_edge_energy(image: np.ndarray, box) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    patch = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2YCrCb).astype(np.float32)
    rms = lambda plane: float((cv2.Laplacian(plane, cv2.CV_32F) ** 2).mean()) ** 0.5
    return rms(patch[..., 0]), rms(patch[..., 2])


def test_chroma_lowpass_is_identity_when_nothing_changed():
    rng = np.random.default_rng(61)
    image = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
    assert np.array_equal(chroma_lowpass(image, image), image)


def test_chroma_lowpass_keeps_luma_and_softens_chroma_edges():
    # 均匀 GT：色度边缘只来自重打光那一块，指标才量得到低通本身。
    gt = np.full((64, 96, 3), 120, np.uint8)
    relit = np.full((64, 96, 3), 128, np.uint8)
    relit[16:40, 24:60] = np.array([90, 90, 170], np.uint8)      # 硬色度边界
    out = chroma_lowpass(relit, gt)
    assert out.min() > 0 and out.max() < 255      # 没有撞到 0/255，亮度比较才有意义

    def luma(image: np.ndarray) -> np.ndarray:
        return (0.114 * image[..., 0].astype(np.float32)
                + 0.587 * image[..., 1].astype(np.float32)
                + 0.299 * image[..., 2].astype(np.float32))

    assert np.abs(luma(out) - luma(relit)).max() <= 1.0
    before = _chroma_edge_energy(relit, (16, 8, 72, 48))
    after = _chroma_edge_energy(out, (16, 8, 72, 48))
    assert after[1] < before[1], (before, after)


def test_banded_chroma_lowpass_is_bit_identical():
    rng = np.random.default_rng(63)
    gt = rng.integers(30, 190, (97, 128, 3), dtype=np.uint8)
    relit = np.clip(gt.astype(np.int16) + 15, 0, 255).astype(np.uint8)
    relit[30:60, 40:90] = np.array([50, 50, 170], np.uint8)

    want = chroma_lowpass(relit, gt)
    for workers in (2, 3, 7):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            got = chroma_lowpass(relit, gt, pool=pool, workers=workers)
        assert np.array_equal(got, want), f"workers={workers}"


def test_chroma_sigma_is_a_fixed_constant():
    """σ 由 4:2:0 的色度采样率决定，不开参数。"""
    assert CHROMA_SIGMA == 2.0


# ── 行带并行：切带不改变任何像素 ───────────────────────────────────────────
def test_banded_apply_is_bit_identical():
    rng = np.random.default_rng(21)
    gt = rng.integers(0, 256, (97, 128, 3), dtype=np.uint8)
    gain = rng.uniform(0.4, 1.6, (97, 128)).astype(np.float32)
    visibility = rng.uniform(0.5, 1.5, (97, 128)).astype(np.float32)
    white_balance = np.array(
        [[0.98, 0.01, 0.0], [0.0, 1.01, 0.02], [0.03, 0.0, 0.99]], np.float32)
    light = LightCompose(gain=gain, visibility=visibility,
                         white_balance=white_balance)

    want = light.apply(gt)
    for workers in (2, 3, 7):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            got = light.apply(gt, pool=pool, workers=workers)
        assert np.array_equal(got, want), f"workers={workers}"


def test_banded_soft_clip_is_bit_identical():
    rng = np.random.default_rng(22)
    image = rng.integers(0, 256, (65, 96, 3), dtype=np.uint8)
    image[:8, :8] = 255
    image[20:24, 30:40] = 230

    want = soft_clip(image)
    for workers in (2, 3, 5):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            got = soft_clip(image, pool=pool, workers=workers)
        assert np.array_equal(got, want), f"workers={workers}"


def test_banded_light_compose_is_bit_identical():
    gt = textured_erp(256, 128, seed=41)
    atmo = np.clip(gt.astype(np.float32) * 0.8, 0, 255).astype(np.uint8)
    fields = estimate_fields(gt, atmo)

    want = light_compose(fields, (256, 128), 0.8, 0.4)
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = light_compose(fields, (256, 128), 0.8, 0.4, pool=pool, workers=4)
    assert np.array_equal(got.gain, want.gain)
    assert np.array_equal(got.visibility, want.visibility)
    assert np.array_equal(got.white_balance, want.white_balance)
    assert np.array_equal(got.apply(gt), want.apply(gt))


def test_threaded_fields_match_serial():
    gt = textured_erp(256, 128, seed=31)
    atmo = np.clip(gt.astype(np.float32) * 0.75, 0, 255).astype(np.uint8)

    want = estimate_fields(gt, atmo)
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = estimate_fields(gt, atmo, pool=pool)
    for name in ("gain", "visibility", "light", "gt_light", "white_balance"):
        assert np.array_equal(getattr(got, name), getattr(want, name)), name


def test_worker_count_leaves_cores_for_codecs():
    """线程数按机器推导：小机器串行、上限 16、串行时不建线程池。"""
    assert workers_for(2) == 1
    assert workers_for(5) == 1
    assert workers_for(20) == 16
    assert workers_for(64) == 16
    assert executor(1) is None


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
    assert set(report) == {"gt", "atmo", "size", "params", "align", "clip", "light",
                           "chroma", "regions", "seconds"}
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


@pytest.mark.parametrize("extra", [
    ("--ref-frame", "0"),
    ("--sample", "3"),
    ("--crf", "0"),
])
def test_cli_rejects_video_only_flags_for_image_gt(tmp_path, extra):
    """视频专属参数配图片 GT 时按参数错拦下，指名哪个参数只适用于视频。"""
    gt = write_image(tmp_path, "gt.png", textured_erp(64, 32, seed=1))
    result = run_cli("--gt", str(gt), "--atmo", str(gt), "--out", str(tmp_path / "o.png"),
                     *extra)
    assert result.exit_code == 2, result.output
    assert extra[0] in result.output
    assert not (tmp_path / "o.png").exists()


def test_cli_rejects_unsupported_gt_suffix(tmp_path):
    """GT 扩展名既不是视频也不是图片：参数错，并列岀支持的类型。"""
    bogus = tmp_path / "gt.xyz"
    bogus.write_bytes(b"not a media file")
    result = run_cli("--gt", str(bogus), "--atmo", str(bogus),
                     "--out", str(tmp_path / "o.png"))
    assert result.exit_code == 2, result.output
    assert "不支持的素材类型" in result.output


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


# ── 视频版（决策 56）：光场复用、跟踪、真实文件端到端 ─────────────────────

VW, VH, VN = 256, 128, 6


def video_erp_without_edges(shift: float = 0.0) -> np.ndarray:
    """地平线以下的画面，平移任何距离都不会绕到顶部。

    最细的横向周期是 256/4 = 64 像素，远大于相位相关的分辨率；位移小于半个周期
    时带通相关不会反相，单帧位移与多帧累积都能被测准。
    """
    x = np.arange(VW, dtype=np.float32)[None, :]
    y = np.arange(VH, dtype=np.float32)[:, None]
    base = (110.0 + 45.0 * np.sin(2 * np.pi * 2 * x / VW)
            + 30.0 * np.cos(2 * np.pi * 3 * x / VW + 0.4)
            + 25.0 * np.sin(2 * np.pi * 4 * x / VW) * np.exp(-((y - 0.6 * VH) / 25.0) ** 2)
            + 12.0 * np.sin(2 * np.pi * 5 * x / VW + 0.9)
            + 18.0 * np.sin(2 * np.pi * 7 * y / VH + 0.3 * y / VH))
    if shift:
        base = np.roll(base, int(round(shift)), axis=1)
    frame = np.clip(base, 0, 255).astype(np.uint8)
    return np.repeat(frame[:, :, None], 3, axis=2)


def video_erp() -> np.ndarray:
    """``conftest.synthetic_erp`` 的画面，用来与图像版逐位对拍（它的顶部会绕到底部）。"""
    from tests.conftest import synthetic_erp
    return synthetic_erp(VW, VH, seed=3)


def ffmpeg_video_conf() -> config_mod.Config:
    conf = config_mod.load()
    conf.data["video"]["preset"] = "ultrafast"
    conf.data["video"]["crf"] = 0
    return conf


def write_video(path: Path, frames: list[np.ndarray]) -> Path:
    with FrameWriter(path, (frames[0].shape[1], frames[0].shape[0]), "10/1",
                     ffmpeg_video_conf().video, config=ffmpeg_video_conf()) as writer:
        for frame in frames:
            writer.write(frame)
    return path


def read_video(path: Path) -> list[np.ndarray]:
    conf = ffmpeg_video_conf()
    with u8_reader(probe(path, conf), conf) as reader:
        return [frame.copy() for frame in reader]


requires_ffmpeg = pytest.mark.skipif(
    find_tool("ffmpeg.exe") is None or find_tool("ffprobe.exe") is None,
    reason="external ffmpeg not populated in bin/ (see bin/README.md)",
)


def test_light_compose_matches_relight():
    """逐帧复用的光场与图像版的 ``relight`` 逐位一致，任何强度下。"""
    gt = textured_erp(256, 128, seed=17)
    atmo = np.clip(gt.astype(np.float32) * 0.6 + 40, 0, 255).astype(np.uint8)
    fields = estimate_fields(gt, atmo)
    for strength, shading in ((1.0, 0.35), (0.8, 0.2), (0.0, 1.0)):
        once = relight(gt, fields, strength, shading)
        reused = light_compose(fields, (256, 128), strength, shading).apply(gt)
        assert np.array_equal(once, reused)


def color_video_frames() -> list[np.ndarray]:
    """``VN`` 帧彩色画面：逐位恒等式要求 sRGB→线性→sRGB 在 8-bit 上无损往返。

    灰阶输入（R=G=B）会撞上 Rec.709 权重和不为 1 的问题：线性光下亮度运算让
    ``round(255/254 × 255)`` 这类往返差一级到三级，那是引擎的已知量级，不是打光错。
    """
    frame = textured_erp(VW, VH, seed=3).copy()
    frame[:, :, 0] = np.clip(frame[:, :, 0].astype(np.int16) - 12, 0, 255)
    frame[:, :, 2] = np.clip(frame[:, :, 2].astype(np.int16) + 9, 0, 255)
    return [frame.copy() for _ in range(VN)]


def encoder_round_trip_error(tmp_path: Path, frames: list[np.ndarray]) -> int:
    """同一组解码帧再编码、解码一次的最大偏差：文件层的固有不确定度。

    实测这张图上为 4 级（均值约 2.4），来自 YUV 转换与 4:2:0 子采样在第二次编码时的
    重新量化；crf 0 也消不掉。视频层的像素断言都以它为界。
    """
    first = read_video(write_video(tmp_path / "noise-1.mp4", frames))
    second = read_video(write_video(tmp_path / "noise-2.mp4", first))
    return max(int(np.abs(a.astype(int) - b.astype(int)).max())
               for a, b in zip(first, second))


@requires_ffmpeg
def test_video_strength_zero_keeps_gt_pixels(tmp_path):
    """--strength 0：输出的解码帧与 GT 解码帧的差不超过编码器的固有往返误差。

    引擎在进程内的逐位恒等由 ``test_strength_zero_is_bit_exact_on_full_path`` 与
    ``test_light_compose_matches_relight`` 负责；这里量的是「整条视频链路没有额外改动
    像素」，所以以文件层的往返噪声为界，而不是零。
    """
    frames = color_video_frames()
    decoded = read_video(write_video(tmp_path / "working.mp4", frames))
    src = write_video(tmp_path / "gt.mp4", decoded)
    noise = encoder_round_trip_error(tmp_path, decoded)
    atmo = write_png(tmp_path / "atmo.png",
                     np.clip(decoded[0].astype(np.float32) * 0.7, 0, 255).astype(np.uint8))
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
        assert worst <= noise, f"frame {index} drifted by {worst} > encoding noise {noise}"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(report) == {
        "gt", "atmo", "size", "frames", "fps", "kind", "started_at",
        "finished_at", "cache", "timing", "ref_frame", "ref_matched",
        "params", "align", "clip", "light", "chroma", "regions", "seconds",
        "atmo_kind", "fields_mode"}
    assert report["atmo_kind"] == "image" and report["fields_mode"] == "fixed"
    assert report["size"] == [VW, VH] and report["frames"] == VN
    assert report["kind"] == "full"
    assert datetime.fromisoformat(report["finished_at"]) >= datetime.fromisoformat(
        report["started_at"])
    assert report["timing"]["total"] == report["seconds"]
    assert report["timing"]["render_total"] > 0
    assert report["cache"] == {"enabled": False, "hit": False, "load": 0.0}
    assert report["params"]["strength"] == 0.0
    assert report["clip"] is None
    assert set(report["align"]) == {
        "enabled", "fallback", "trusted", "keypoints_gt", "keypoints_atmo", "matches",
        "inliers", "scale", "angle_deg", "tx_2k", "ty_2k", "tx", "ty",
        "correlation_before", "correlation_after"}


@requires_ffmpeg
def test_video_reference_frame_and_lighting(tmp_path):
    """默认自动选的参考帧能与氛围图对齐，输出确实被重新打光。"""
    frames = [video_erp_without_edges() for _ in range(VN)]
    src = write_video(tmp_path / "gt.mp4", frames)
    darker = np.clip(frames[0].astype(np.float32) * 0.45, 0, 255).astype(np.uint8)
    atmo = write_png(tmp_path / "atmo.png", darker)
    out = tmp_path / "out.mp4"
    report_path = tmp_path / "report.json"
    result = run_cli("--gt", str(src), "--atmo", str(atmo), "--out", str(out),
                           "--strength", "1", "--report", str(report_path),
                           "--set", "video.preset=ultrafast")
    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["align"]["trusted"] is True
    assert 0 <= report["ref_frame"] < VN
    got = read_video(out)
    assert len(got) == VN
    # 氛围图压暗了画面，打光后均值应当低于 GT 的均值，且不是全黑
    assert got[0].mean() < read_video(src)[0].mean() - 10
    assert got[0].mean() > 5


@requires_ffmpeg
def test_video_ref_frame_explicit(tmp_path):
    frames = [video_erp_without_edges() for _ in range(VN)]
    src = write_video(tmp_path / "gt.mp4", frames)
    atmo = write_png(tmp_path / "atmo.png", frames[0])
    report_path = tmp_path / "report.json"
    result = run_cli("--gt", str(src), "--atmo", str(atmo),
                           "--out", str(tmp_path / "out.mp4"), "--strength", "0",
                           "--ref-frame", "3", "--report", str(report_path),
                           "--set", "video.preset=ultrafast")
    assert result.exit_code == 0, result.output
    assert json.loads(report_path.read_text(encoding="utf-8"))["ref_frame"] == 3


@requires_ffmpeg
def test_video_audio_is_copied(tmp_path):
    from vrstudio.media.binres import ffmpeg

    frames = [video_erp_without_edges() for _ in range(VN)]
    silent = write_video(tmp_path / "silent.mp4", frames)
    src = tmp_path / "gt.mp4"
    import subprocess
    r = subprocess.run([ffmpeg(config_mod.load()), "-y", "-v", "error", "-i", str(silent),
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-shortest",
                        "-c:v", "copy", "-c:a", "aac", str(src)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[:300]
    assert probe(src).has_audio

    atmo = write_png(tmp_path / "atmo.png", frames[0])
    out = tmp_path / "out.mp4"
    result = run_cli("--gt", str(src), "--atmo", str(atmo), "--out", str(out),
                           "--strength", "0", "--set", "video.preset=ultrafast")
    assert result.exit_code == 0, result.output
    after = probe(out)
    assert after.has_audio, "audio stream was not carried over"
    assert after.frames == VN and after.size == (VW, VH)
