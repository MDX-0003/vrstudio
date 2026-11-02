"""patchimg: 2:1 ERP image -> 16:9 viewport crop -> inner mask -> image.json."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from tests.conftest import synthetic_erp
from vrstudio import case as case_mod
from vrstudio.case import CaseError
from vrstudio.cli import case as case_cli
from vrstudio.cli import extract as extract_cli
from vrstudio.cli import generate as generate_cli
from vrstudio.cli import merge as merge_cli
from vrstudio.media.imageio import read_image, write_gray_png, write_png
from vrstudio.patch.composite import SegmentMaps
from vrstudio.patch.sidecar import InnerRect, Viewport, load_image

runner = CliRunner()


def _source(tmp_path, name: str = "src.png", width: int = 384, height: int = 192):
    path = tmp_path / name
    write_png(path, synthetic_erp(width, height))
    return path


def _extract(tmp_path):
    result = runner.invoke(extract_cli.app, [
        "name", "--case", "p", "--cases-dir", str(tmp_path)])
    return result


def _prepared_case(tmp_path):
    """create + draft + extract; returns the source path."""
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    case_mod.set_draft("p", root=tmp_path, **case_mod.patchimg_default_draft(384))
    result = _extract(tmp_path)
    assert result.exit_code == 0, result.output
    return src


def _version_dir(tmp_path):
    case = case_mod.load("p", tmp_path)
    return case_mod.case_dir("p", tmp_path) / "extracts" / case.extract_current


def _adopted_result(version, inner, colour=(0, 0, 255)):
    """模型的返回：视口图原样，inner 区域涂成一种颜色。"""
    clip = read_image(version / "clip.png")
    x, y, w, h = inner["x"], inner["y"], inner["width"], inner["height"]
    clip[y:y + h, x:x + w] = colour
    write_png(version / "result.png", clip)


def _support(source, viewport, inner, feather=16):
    """inner 在 ERP 上覆盖的像素，展开成整幅 bool。"""
    maps = SegmentMaps.from_geometry(viewport, inner, source.shape[1],
                                     source.shape[0], feather_px=feather)
    full = np.zeros(source.shape[:2], dtype=bool)
    x0, y0, x1, y1 = maps.inner_bbox
    full[y0:y1, x0:x1] = maps.inner_support
    return full


def test_create_patchimg_rejects_non_2_to_1(tmp_path):
    path = tmp_path / "flat.png"
    write_png(path, np.zeros((100, 160, 3), np.uint8))       # 1.6:1
    with pytest.raises(CaseError, match="2:1"):
        case_mod.create("p", "patchimg", path, root=tmp_path)


def test_create_patchimg_rejects_a_video_source(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a video")
    with pytest.raises(CaseError, match="图片"):
        case_mod.create("p", "patchimg", video, root=tmp_path)


def test_patchimg_case_roundtrip_has_no_frames(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    case_mod.set_draft("p", root=tmp_path, **case_mod.patchimg_default_draft(384))
    case_mod.set_extract_current("p", "v1", root=tmp_path)
    case_mod.set_adopt("p", "img-x", root=tmp_path, index=2)

    case = case_mod.load("p", tmp_path)
    assert case.flow == "patchimg"
    assert case.viewport["width"] == 384
    assert case.inner["width"] == 192
    assert case.frames is None
    assert case.extract_current == "v1"
    assert case.adopt_index == 2

    raw = (case_mod.case_dir("p", tmp_path) / "case.toml").read_text(encoding="utf-8")
    assert "[frames]" not in raw
    assert "index = 2" in raw


def test_patchimg_draft_ready_ignores_frames(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    assert case_mod.draft_ready(case_mod.load("p", tmp_path)) == "viewport"

    case_mod.set_draft("p", root=tmp_path, **case_mod.patchimg_default_draft(384))
    assert case_mod.draft_ready(case_mod.load("p", tmp_path)) is None


def test_patchimg_set_draft_rejects_frames(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    with pytest.raises(CaseError, match="frames"):
        case_mod.set_draft("p", frames={"start": 0, "end": 0}, root=tmp_path)


def test_case_new_patchimg_seeds_a_draft(tmp_path):
    src = _source(tmp_path)
    result = runner.invoke(case_cli.app, [
        "new", "p", "--flow", "patchimg", "--source", str(src),
        "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    case = case_mod.load("p", tmp_path)
    assert case.viewport["width"] == 384
    assert case.inner["width"] == 192
    assert case.frames is None


def test_case_draft_cli_writes_viewport_and_inner(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({
        "viewport": {"yaw_deg": 10.0, "pitch_deg": 2.0, "fov_h_deg": 80.0,
                     "width": 320, "height": 180},
        "inner": {"x": 80, "y": 45, "width": 160, "height": 90},
    }), encoding="utf-8")

    result = runner.invoke(case_cli.app, [
        "draft", "--case", "p", "--file", str(draft),
        "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    case = case_mod.load("p", tmp_path)
    assert case.viewport["yaw_deg"] == 10.0
    assert case.inner["width"] == 160


def test_patchimg_extract_writes_the_viewport_artifacts(tmp_path):
    _prepared_case(tmp_path)
    version = _version_dir(tmp_path)

    for name in ("clip.png", "clip_mask.png", "image.json"):
        assert (version / name).is_file(), name
    assert not (version / "padded.png").exists()
    assert not (version / "image_pad.json").exists()
    derived = case_mod.case_dir("p", tmp_path) / "derived"
    for name in ("clip.png", "clip_mask.png", "image.json"):
        assert (derived / name).is_file(), name

    geo = load_image(version / "image.json")
    assert (geo.erp_width, geo.erp_height) == (384, 192)
    assert (geo.viewport.width, geo.viewport.height) == (384, 216)
    assert (geo.inner.x, geo.inner.y, geo.inner.width, geo.inner.height) == \
        (96, 54, 192, 108)

    clip = read_image(version / "clip.png")
    assert clip.shape[:2] == (geo.viewport.height, geo.viewport.width)


def test_patchimg_extract_mask_is_black_inside_inner(tmp_path):
    _prepared_case(tmp_path)
    case = case_mod.load("p", tmp_path)
    mask = read_image(_version_dir(tmp_path) / "clip_mask.png")[:, :, 0]
    inner = case.inner

    assert mask.dtype == np.uint8
    assert mask[inner["y"]:inner["y"] + inner["height"],
                inner["x"]:inner["x"] + inner["width"]].max() == 0
    outside = mask.copy()
    outside[inner["y"]:inner["y"] + inner["height"],
            inner["x"]:inner["x"] + inner["width"]] = 0
    assert outside.max() == 255


def test_patchimg_extract_rejects_a_non_16_9_viewport(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    case_mod.set_draft("p", root=tmp_path,
                       viewport={"yaw_deg": 0.0, "pitch_deg": 0.0,
                                 "fov_h_deg": 59.0, "width": 320, "height": 200},
                       inner={"x": 80, "y": 50, "width": 160, "height": 100})

    result = _extract(tmp_path)
    assert result.exit_code == 2
    assert "16:9" in result.output


def test_patchimg_extract_rejects_an_inner_outside_the_viewport(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    case_mod.set_draft("p", root=tmp_path,
                       viewport={"yaw_deg": 0.0, "pitch_deg": 0.0,
                                 "fov_h_deg": 59.0, "width": 384, "height": 216},
                       inner={"x": 300, "y": 0, "width": 200, "height": 100})

    result = _extract(tmp_path)
    assert result.exit_code == 2
    assert "越出" in result.output


def test_patchimg_submit_dry_run_lists_the_image_argv(tmp_path):
    _prepared_case(tmp_path)
    result = runner.invoke(generate_cli.app, [
        "submit", "--case", "p", "--dry-run", "--num", "4",
        "--resolution", "4K", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "image" in result.output and "submit" in result.output
    assert "clip.png" in result.output
    assert "clip_mask.png" in result.output
    assert "edit_master" in result.output
    assert "--image-role" in result.output
    assert "mask" in result.output
    assert "16:9" in result.output
    assert "upload.png" not in result.output


def test_upload_inputs_bounds_the_long_side(tmp_path):
    """img2video 的关键帧提交（WP3）复用这个上限；patchimg 不再经过它。"""
    base = tmp_path / "padded.png"
    mask = tmp_path / "padded_mask.png"
    write_png(base, np.full((360, 640, 3), 128, np.uint8))
    write_gray_png(mask, np.full((360, 640), 255, np.uint8))

    upload, upload_mask = generate_cli._upload_inputs(base, mask, 200)
    assert upload.name == "upload.png"
    assert max(read_image(upload).shape[:2]) == 200
    assert read_image(upload_mask).shape[:2] == read_image(upload).shape[:2]


def test_patchimg_adopt_requires_an_explicit_index(tmp_path):
    _prepared_case(tmp_path)
    result = runner.invoke(generate_cli.app, [
        "adopt", "--case", "p", "--cases-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "--index" in result.output


def test_patchimg_adopt_records_the_index(tmp_path, monkeypatch):
    _prepared_case(tmp_path)
    monkeypatch.setattr(generate_cli, "_capture",
                        lambda argv: (0, "round=img-1a2b3c"))
    result = runner.invoke(generate_cli.app, [
        "adopt", "--case", "p", "--round", "img-1a2b3c", "--index", "2",
        "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    case = case_mod.load("p", tmp_path)
    assert case.adopt_round == "img-1a2b3c"
    assert case.adopt_index == 2


def test_patchimg_merge_writes_the_erp_png_bit_identically(tmp_path):
    src = _prepared_case(tmp_path)
    case = case_mod.load("p", tmp_path)
    _adopted_result(_version_dir(tmp_path), case.inner)

    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    out = (case_mod.case_dir("p", tmp_path) / "out"
           / f"{case.extract_current}_image.png")
    assert out.is_file()
    merged = read_image(out)
    source = read_image(src)
    support = _support(source, Viewport(**case.viewport), InnerRect(**case.inner))

    assert np.array_equal(merged[~support], source[~support])
    assert np.any(merged[support] != source[support])


def test_patchimg_merge_requires_an_adopted_result(tmp_path):
    _prepared_case(tmp_path)
    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "adopt" in result.output


def test_patchimg_merge_rejects_an_aspect_mismatch(tmp_path):
    _prepared_case(tmp_path)
    case = case_mod.load("p", tmp_path)
    version = _version_dir(tmp_path)
    clip = read_image(version / "clip.png")
    write_png(version / "result.png",
              cv2.resize(clip, (200, 100), interpolation=cv2.INTER_AREA))

    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "--allow-crop" in result.output

    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--allow-crop", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_patchimg_merge_pastes_a_viewport_across_the_erp_seam(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    case_mod.set_draft("p", root=tmp_path,
                       viewport={"yaw_deg": 180.0, "pitch_deg": 0.0,
                                 "fov_h_deg": 70.0, "width": 384, "height": 216},
                       inner={"x": 96, "y": 54, "width": 192, "height": 108})
    result = _extract(tmp_path)
    assert result.exit_code == 0, result.output

    case = case_mod.load("p", tmp_path)
    _adopted_result(_version_dir(tmp_path), case.inner)
    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    merged = read_image(case_mod.case_dir("p", tmp_path) / "out"
                        / f"{case.extract_current}_image.png")
    source = read_image(src)
    support = _support(source, Viewport(**case.viewport), InnerRect(**case.inner))

    # 跨接缝视口的 inner 落在 ERP 的两端，中间一段不受影响。
    assert support[:, :5].any()
    assert support[:, -5:].any()
    assert not support[:, 150:250].any()
    # 选区外逐位不变；远离视口的 ERP 中缝那一列也原样保留。
    assert np.array_equal(merged[~support], source[~support])
    assert np.array_equal(merged[:, 192], source[:, 192])
    assert np.any(merged[support] != source[support])

    # 视口中缝两列来自 ERP 的两端：环绕采样拼好了，就没有跳变。
    clip = read_image(_version_dir(tmp_path) / "clip.png").astype(np.int16)
    jump = np.abs(np.diff(clip, axis=1)).mean(axis=(0, 2))
    assert jump[191] < 5 * np.median(jump)
