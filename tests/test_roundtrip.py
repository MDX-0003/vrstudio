"""The keystone: ``recompose(pad(src))`` must be a pure rotation of ``src``.

Everything else in this project is a convenience. This invariant is why the wrap
closes without the AIGC model being consistent over a full revolution: the
output's wrap pair is an *adjacent* column pair of the model's own canvas, so the
seam is a local-smoothness question rather than a long-range one.

Assertions are written in the uint8 domain. The blend's weighted sum
``(1-w)*X + w*X`` is not bit-exact in float64 (the weights are inexact), but the
pipeline rounds exactly once, in ``finalize``, so the delivered bytes are.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from vrstudio.redraw.blend import endpoint_lock_px
from vrstudio.redraw.geometry import PadSpec, plan_pad
from vrstudio.redraw.padding import pad_horizontal
from vrstudio.redraw.preroll import pre_roll
from vrstudio.redraw.recompose_core import RecomposeSpec, finalize, recompose, slice_regions
from vrstudio.redraw.verify_core import seam_metrics

#: 21:9 — the pipeline's target. Must be >= the 2:1 fixture's aspect.
ASPECT = Fraction(21, 9)


def plan_for(frame: np.ndarray):
    H, W = frame.shape[:2]
    return plan_pad(PadSpec((W, H), ASPECT))


def spec_for(plan, **kw) -> RecomposeSpec:
    base = dict(W=plan.W, Wc=plan.Wc, P=plan.P, L=plan.L,
                endpoint_lock_px=max(1, endpoint_lock_px(plan.L, 2.0)))
    base.update(kw)
    return RecomposeSpec(**base)


def test_fixture_plan_matches_the_documented_two_to_one_case(erp):
    plan = plan_for(erp)                     # 384x192 is 2:1
    assert plan.canvas_size == (448, 192)
    assert plan.P == 32 and plan.L == 64
    assert plan.wrap_pair == (31, 32)


def test_identity_roundtrip_is_a_rotation(erp):
    plan = plan_for(erp)
    canvas = pad_horizontal(erp, plan.P)
    out = finalize(recompose(canvas.astype(np.float64), spec_for(plan)))
    assert out.shape == erp.shape
    assert np.array_equal(out, np.roll(erp, plan.roll_delta, axis=1))


def test_preroll_cancels_the_rotation(erp):
    """With pre_roll applied before padding the output is the source, unchanged."""
    plan = plan_for(erp)
    assert plan.source_preroll == plan.P
    canvas = pad_horizontal(pre_roll(erp, plan.source_preroll), plan.P)
    out = finalize(recompose(canvas.astype(np.float64), spec_for(plan)))
    assert np.array_equal(out, erp)


def test_only_finalize_quantises(erp):
    plan = plan_for(erp)
    canvas = pad_horizontal(erp, plan.P).astype(np.float64)
    out = recompose(canvas, spec_for(plan))
    assert out.dtype == np.float64
    W, L = plan.W, plan.L
    assert np.array_equal(out[:, : W - L], canvas[:, L:W])
    # the endpoint lock pins the last column to unshifted G, i.e. canvas column L-1
    assert np.array_equal(out[:, -1], canvas[:, L - 1])


def test_wrap_pair_is_an_adjacent_canvas_column_pair(erp):
    """out[0] <- canvas[L] and out[-1] <- canvas[L-1]; those canvas columns are neighbours."""
    plan = plan_for(erp)
    Wc = plan.Wc
    # a canvas whose value IS its column index, so provenance is readable
    canvas = np.repeat(np.arange(Wc, dtype=np.float64)[None, :, None], 4, axis=0)
    canvas = np.repeat(canvas, 3, axis=2)                      # (4, Wc, 3)
    out = recompose(canvas, spec_for(plan, endpoint_lock_px=4))
    assert out[0, 0, 0] == plan.L
    assert out[0, -1, 0] == plan.L - 1
    assert int(out[0, 0, 0]) - int(out[0, -1, 0]) == 1


def test_endpoint_lock_is_what_pins_the_last_column(erp):
    """Without the lock a non-zero d* walks the wrap off the adjacent pair."""
    plan = plan_for(erp)
    canvas = pad_horizontal(erp, plan.P).astype(np.float64)
    locked = recompose(canvas, spec_for(plan, d_star=5, endpoint_lock_px=8))
    unlocked = recompose(canvas, spec_for(plan, d_star=5, endpoint_lock_px=0))
    assert np.array_equal(locked[:, -1], canvas[:, plan.L - 1])
    assert not np.array_equal(unlocked[:, -1], canvas[:, plan.L - 1])


def test_smooth_exposure_gradient_does_not_break_the_wrap(erp):
    """Closure is structural, not a property of the content.

    A global brightness gradient is what a VAE-tile boundary leaves behind, and
    what a content-rectangle crop cannot survive. Here the wrap pair is two
    neighbouring canvas columns, so a gradient contributes only its per-column step.
    """
    plan = plan_for(erp)
    canvas = pad_horizontal(erp, plan.P).astype(np.float64)
    ramp = np.linspace(0.0, 60.0, canvas.shape[1])
    graded = np.clip(canvas + ramp[None, :, None], 0, 255)

    out = finalize(recompose(graded, spec_for(plan)))
    wrap_after = seam_metrics(out).wrap

    # the same canvas cut as a plain rectangle, i.e. the previous design's behaviour
    rect_crop = np.clip(graded[:, plan.L : plan.W], 0, 255).astype(np.uint8)
    wrap_rect = seam_metrics(rect_crop).wrap

    assert wrap_after < 0.25 * wrap_rect, (
        f"recompose wrap {wrap_after:.3f} should sit far below the rectangle crop's "
        f"{wrap_rect:.3f}"
    )


def test_output_geometry(erp):
    plan = plan_for(erp)
    canvas = pad_horizontal(erp, plan.P).astype(np.float64)
    out = recompose(canvas, spec_for(plan))
    assert out.shape[1] == plan.W
    assert out.shape[0] == canvas.shape[0]


def test_boundary_repair_keeps_the_shape(erp):
    plan = plan_for(erp)
    canvas = pad_horizontal(erp, plan.P).astype(np.float64)
    out = recompose(canvas, spec_for(plan, repair="feather", repair_px=16))
    assert out.shape == (canvas.shape[0], plan.W, canvas.shape[2])


def test_slice_regions_rejects_a_wrong_canvas(erp):
    plan = plan_for(erp)
    with pytest.raises(ValueError, match="columns"):
        slice_regions(np.zeros((4, plan.Wc + 2, 3), np.uint8), spec_for(plan))
