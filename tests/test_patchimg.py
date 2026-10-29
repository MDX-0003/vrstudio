"""patchimg: 2:1 ERP image -> padded canvas -> local mask -> still_pad.json."""

from __future__ import annotations

import json
import shutil

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
from vrstudio.media.imageio import read_image, write_png
from vrstudio.patch.sidecar import InnerRect, Viewport
from vrstudio.patch.still import LocalMaskError, composite_local, local_erp_mask
from vrstudio.redraw.manifest import StillManifest

runner = CliRunner()


def _source(tmp_path, name: str = "src.png", width: int = 384, height: int = 192):
    path = tmp_path / name
    write_png(path, synthetic_erp(width, height))
    return path


def _prepared_case(tmp_path):
    """create + draft + extract; returns the source path."""
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    case_mod.set_draft("p", root=tmp_path, **case_mod.patchimg_default_draft(384))
    result = runner.invoke(extract_cli.app, [
        "name", "--case", "p", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return src


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


def test_patchimg_extract_writes_the_still_artifacts(tmp_path):
    src = _source(tmp_path)
    result = runner.invoke(case_cli.app, [
        "new", "p", "--flow", "patchimg", "--source", str(src),
        "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    result = runner.invoke(extract_cli.app, [
        "name", "--case", "p", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    case = case_mod.load("p", tmp_path)
    assert case.extract_current
    version = case_mod.case_dir("p", tmp_path) / "extracts" / case.extract_current
    for name in ("padded.png", "padded_mask.png", "still_pad.json"):
        assert (version / name).is_file(), name
    assert (case_mod.case_dir("p", tmp_path) / "derived" / "still_pad.json").is_file()

    manifest = StillManifest.load(version / "still_pad.json")
    assert manifest.local is not None
    assert manifest.local.viewport["width"] == 384
    assert manifest.local.inner["width"] == 192
    assert manifest.mask.strategy == "local"
    assert manifest.canvas.size == (448, 192)


def test_local_mask_and_composite_keep_pixels_outside_inner(erp):
    viewport = Viewport(yaw_deg=0.0, pitch_deg=0.0, fov_h_deg=59.0,
                        width=384, height=216)
    inner = InnerRect(x=96, y=54, width=96, height=54)
    local = local_erp_mask(viewport, inner, 384, 192)
    assert local.support.any()

    stitched = np.zeros_like(erp)
    result = composite_local(erp, stitched, local, feather_px=16)

    x0, y0, x1, y1 = local.bbox
    support = np.zeros(erp.shape[:2], dtype=bool)
    support[y0:y1, x0:x1] = local.support
    assert result.maxdiff_outside == 0
    assert np.array_equal(result.image[~support], erp[~support])
    assert result.changed_inside > 0
    assert result.changed_inside <= result.support_pixels


def test_local_mask_rejects_an_inner_outside_the_viewport():
    viewport = Viewport(yaw_deg=0.0, pitch_deg=0.0, fov_h_deg=59.0,
                        width=384, height=216)
    with pytest.raises(LocalMaskError, match="outside"):
        local_erp_mask(viewport, InnerRect(x=400, y=0, width=10, height=10),
                       384, 192)


def test_patchimg_extract_rejects_a_region_on_the_redundant_band(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "patchimg", src, root=tmp_path)
    case_mod.set_draft("p", root=tmp_path,
                       viewport={"yaw_deg": 160.0, "pitch_deg": -5.0,
                                 "fov_h_deg": 70.0, "width": 1920, "height": 1080},
                       inner={"x": 480, "y": 270, "width": 960, "height": 540})

    result = runner.invoke(extract_cli.app, [
        "name", "--case", "p", "--cases-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "冗余带" in result.output


def test_patchimg_submit_dry_run_lists_the_image_argv(tmp_path):
    _prepared_case(tmp_path)
    result = runner.invoke(generate_cli.app, [
        "submit", "--case", "p", "--dry-run", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "image" in result.output and "submit" in result.output
    assert "padded.png" in result.output
    assert "edit_master" in result.output
    assert "--image-role" in result.output
    assert "mask" in result.output
    assert "21:9" in result.output


def test_patchimg_submit_downscales_a_large_padded_canvas(tmp_path):
    _prepared_case(tmp_path)
    result = runner.invoke(generate_cli.app, [
        "submit", "--case", "p", "--dry-run",
        "--set", "generate.image_upload_max_side=200",
        "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "upload.png" in result.output
    assert "upload_mask.png" in result.output

    case = case_mod.load("p", tmp_path)
    version = case_mod.case_dir("p", tmp_path) / "extracts" / case.extract_current
    upload = read_image(version / "upload.png")
    assert max(upload.shape[:2]) == 200
    mask = read_image(version / "upload_mask.png")
    assert mask.shape[:2] == upload.shape[:2]


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
    version = case_mod.case_dir("p", tmp_path) / "extracts" / case.extract_current
    shutil.copyfile(version / "padded.png", version / "result.png")

    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    out = (case_mod.case_dir("p", tmp_path) / "out"
           / f"{case.extract_current}_still.png")
    assert out.is_file()
    assert np.array_equal(read_image(out), read_image(src))


def test_patchimg_merge_requires_an_adopted_result(tmp_path):
    _prepared_case(tmp_path)
    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "adopt" in result.output
