"""Follow-input output scaling: geometry, both single-frame cores, web wiring.

The scaled geometry is derived by ``plan_pad`` itself, so these tests assert the
closure identity at the scaled size rather than only checking numbers.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest
from starlette.testclient import TestClient

from tests.conftest import synthetic_erp
from vrstudio import case as case_mod
from vrstudio.media.imageio import write_png
from vrstudio.redraw.blend import endpoint_lock_px
from vrstudio.redraw.geometry import PadSpec, assert_closure, follow_input_plan, plan_pad
from vrstudio.redraw.image import pad_image, stitch_image
from vrstudio.redraw.manifest import from_image_plan
from vrstudio.redraw.padding import pad_horizontal
from vrstudio.redraw.recompose_core import RecomposeSpec, recompose_u8
from vrstudio.web import app as web_app

ASPECT = Fraction(21, 9)


@pytest.fixture(autouse=True)
def _clear_preview_stores():
    web_app._stores.clear()
    yield
    web_app._stores.clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "_vrgen_exe", lambda: "definitely-not-a-real-vrgen")
    return TestClient(web_app.build_app(cases_root=tmp_path))


def _case_plan(width: int = 384, height: int = 192):
    return plan_pad(PadSpec((width, height), ASPECT))


# ── 几何 ────────────────────────────────────────────────────────────────


def test_scaled_plan_matches_the_input_density_and_keeps_2_to_1():
    plan = _case_plan(8192, 4096)
    scaled = follow_input_plan(plan, 1000)
    assert scaled is not None
    assert (scaled.W, scaled.H, scaled.Wc, scaled.L, scaled.P) == (856, 428, 1000, 144, 72)
    assert scaled.W == 2 * scaled.H
    assert_closure(scaled)


def test_input_at_or_above_the_source_is_not_followed():
    plan = _case_plan(8192, 4096)
    assert follow_input_plan(plan, plan.W) is None
    assert follow_input_plan(plan, plan.Wc) is None


def test_input_too_small_for_a_band_is_not_followed():
    plan = _case_plan(8192, 4096)
    assert follow_input_plan(plan, 20) is None


# ── 视频单帧核心 ────────────────────────────────────────────────────────


def test_scaled_recompose_is_a_rotation_at_the_scaled_geometry():
    case_plan = _case_plan()
    scaled = follow_input_plan(case_plan, 224)
    assert scaled is not None
    src = synthetic_erp(scaled.W, scaled.H, seed=11)
    canvas = pad_horizontal(src, scaled.P).astype(np.float64)
    spec = RecomposeSpec(
        W=scaled.W, Wc=scaled.Wc, P=scaled.P, L=scaled.L,
        endpoint_lock_px=max(1, endpoint_lock_px(scaled.L, 2.0)),
    )
    out = recompose_u8(canvas, spec)
    assert out.shape == (scaled.H, scaled.W, 3)
    assert np.array_equal(out, np.roll(src, scaled.roll_delta, axis=1))


# ── 图片单帧核心 ────────────────────────────────────────────────────────


def _case_pad(tmp_path):
    case_plan = _case_plan()
    case_src = synthetic_erp(384, 192, seed=3)
    return pad_image(case_src, case_plan, source_path="case.png", write_hash=False)


def test_scaled_stitch_is_a_rotation_at_the_scaled_geometry(tmp_path):
    case_pad = _case_pad(tmp_path)
    case_plan = case_pad.manifest.to_pad_plan()
    scaled = follow_input_plan(case_plan, 224)
    assert scaled is not None
    src = synthetic_erp(scaled.W, scaled.H, seed=7)
    canvas = pad_horizontal(src, scaled.P)

    stitched = stitch_image(canvas, case_pad.manifest, follow_input=True)

    assert stitched.output_mode == "input"
    assert stitched.output_size == (scaled.W, scaled.H)
    assert np.array_equal(stitched.image, np.roll(src, scaled.roll_delta, axis=1))


def test_stitch_without_follow_input_keeps_the_case_size(tmp_path):
    case_pad = _case_pad(tmp_path)
    stitched = stitch_image(case_pad.padded, case_pad.manifest)
    assert stitched.output_mode == "case"
    assert stitched.output_size == (384, 192)


# ── web ────────────────────────────────────────────────────────────────


def _image_case_with_input(tmp_path, input_width: int = 224):
    src = tmp_path / "pano.png"
    write_png(src, synthetic_erp(384, 192, seed=1))
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)

    version = case_mod.case_dir("f1", tmp_path) / "extracts" / "v1"
    version.mkdir(parents=True)
    from_image_plan(_case_plan(), source_path=src, write_hash=False).save(
        version / "image_pad.json")
    case_mod.set_extract_current("f1", "v1", root=tmp_path)

    canvas = tmp_path / "edited.png"
    write_png(canvas, synthetic_erp(input_width, round(input_width * 9 / 21), seed=2))
    case_mod.add_merge_input("f1", canvas, root=tmp_path)
    return case_mod.load("f1", tmp_path)


def test_case_info_exposes_both_output_sizes(tmp_path, client):
    _image_case_with_input(tmp_path)
    info = client.get("/api/case/f1").json()
    options = {item["mode"]: (item["width"], item["height"])
               for item in info["output_options"]}
    assert options == {"case": (384, 192), "input": (192, 96)}


def test_merge_op_passes_follow_input(tmp_path, client):
    _image_case_with_input(tmp_path)
    argv = web_app._op_argv("f1", "merge", {"output_mode": "input"})
    assert "--follow-input" in argv


def test_recompose_op_passes_follow_input(tmp_path, client):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    cdir = case_mod.case_dir("f1", tmp_path)
    (cdir / "seedance_input_pad.json").write_text("{}", encoding="utf-8")
    (cdir / "result.mp4").write_bytes(b"fake result")

    argv = web_app._op_argv("f1", "recompose", {"output_mode": "input"})
    assert "--follow-input" in argv


def test_output_files_recognise_suffixed_video_outputs(tmp_path, client):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    cdir = case_mod.case_dir("f1", tmp_path)
    out = cdir / "out"
    out.mkdir()
    for name in ("round", "round_856x428"):
        (out / f"{name}.mp4").write_bytes(b"video")
        (out / f"{name}_recompose.json").write_text("{}", encoding="utf-8")

    names = [path.name for path, _, _ in web_app._output_files(
        case_mod.load("f1", tmp_path))]
    assert names == ["round.mp4", "round_856x428.mp4"]


def test_output_files_recognise_suffixed_image_outputs(tmp_path, client):
    src = tmp_path / "pano.png"
    write_png(src, synthetic_erp(384, 192, seed=1))
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    out = case_mod.case_dir("f1", tmp_path) / "out"
    out.mkdir()
    (out / "v1_image.png").write_bytes(b"png")
    (out / "v1_image_192x96.png").write_bytes(b"png")

    names = [path.name for path, _, _ in web_app._output_files(
        case_mod.load("f1", tmp_path))]
    assert names == ["v1_image.png", "v1_image_192x96.png"]
