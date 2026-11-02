"""Still pad/stitch: the single-frame form of the fullredraw auxiliary."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from vrstudio.media.imageio import read_image, write_png
from vrstudio.redraw.image import ImageFlowError, pad_image, plan_image, stitch_image

ASPECT = Fraction(21, 9)


def _plan(erp: np.ndarray):
    return plan_image((erp.shape[1], erp.shape[0]), target_aspect=ASPECT)


def test_plan_matches_the_video_two_to_one_case(erp):
    plan = _plan(erp)                       # 384x192 is 2:1
    assert plan.canvas_size == (448, 192)
    assert plan.P == 32 and plan.L == 64
    assert plan.wrap_pair == (31, 32)


def test_pad_image_geometry_and_manifest(erp):
    plan = _plan(erp)
    pad = pad_image(erp, plan, source_path="synthetic.png", write_hash=False)

    assert pad.padded.shape == (192, 448, 3)
    assert pad.mask.shape == (192, 448)
    assert pad.manifest.canvas.size == (448, 192)
    assert pad.manifest.canvas.P == 32
    assert pad.manifest.pad.pipeline == ["pre_roll", "pad_h"]
    assert pad.manifest.mask.strategy == "redundant_white"


def test_identity_pad_stitch_is_the_source(erp):
    """pre_roll + pad_h + stitch(d*=0) must return the source bit-exactly."""
    pad = pad_image(erp, _plan(erp), source_path="synthetic.png", write_hash=False)
    stitched = stitch_image(pad.padded, pad.manifest)

    assert stitched.gate == "ok"
    assert stitched.image.shape == erp.shape
    assert np.array_equal(stitched.image, erp)


def test_image_manifest_roundtrip(erp, tmp_path):
    pad = pad_image(erp, _plan(erp), source_path="synthetic.png", write_hash=False)
    path = pad.manifest.save(tmp_path / "image_pad.json")
    loaded = type(pad.manifest).load(path)

    assert loaded.canvas == pad.manifest.canvas
    assert loaded.pad == pad.manifest.pad
    assert loaded.mask == pad.manifest.mask
    assert loaded.source.size == (384, 192)
    assert loaded.to_pad_plan().canvas_size == (448, 192)


def test_stitch_rejects_a_reframed_canvas(erp):
    pad = pad_image(erp, _plan(erp), source_path="synthetic.png", write_hash=False)
    bad = pad.padded.copy()
    bad[:, _plan(erp).W:] = 0              # flatten the right redundant band

    with pytest.raises(ImageFlowError, match="premise"):
        stitch_image(bad, pad.manifest)


def test_local_mask_is_accepted(erp):
    plan = _plan(erp)
    mask = np.full((plan.H, plan.Wc), 255, np.uint8)
    mask[60:130, 200:300] = 0
    pad = pad_image(erp, plan, source_path="synthetic.png", mask=mask,
                    mask_strategy="local", write_hash=False)

    assert pad.mask.shape == mask.shape
    assert pad.manifest.mask.strategy == "local"
    assert pad.manifest.local is None


def test_imageio_handles_unicode_paths(tmp_path):
    image = np.random.default_rng(3).integers(0, 256, (16, 24, 3), dtype=np.uint8)
    path = write_png(tmp_path / "中文.png", image)
    assert np.array_equal(read_image(path), image)
