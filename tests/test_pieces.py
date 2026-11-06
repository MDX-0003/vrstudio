"""Pre-roll, mask, blend, registration — the small pure pieces."""

from __future__ import annotations

import numpy as np
import pytest

from vrstudio.redraw.blend import blend_block, endpoint_lock_px, weights
from vrstudio.redraw.mask import STRATEGIES, make_mask
from vrstudio.redraw.preroll import pre_roll
from vrstudio.redraw.registration import RegistrationResult, circular_translate, to_gray


# ---------------------------------------------------------------- pre-roll
def test_pre_roll_shifts_right_and_is_lossless():
    frame = np.arange(64, dtype=np.uint8)[None, :, None]      # (H=1, W=64, C=1)
    rolled = pre_roll(frame, 5)
    assert rolled[0, 5, 0] == 0
    assert sorted(rolled.ravel().tolist()) == sorted(frame.ravel().tolist())


def test_pre_roll_zero_is_a_no_op():
    frame = np.zeros((2, 8, 3), np.uint8)
    assert pre_roll(frame, 0) is frame


def test_pre_roll_round_trips():
    rng = np.random.default_rng(2)
    frame = rng.integers(0, 255, (5, 40, 3), dtype=np.uint8)
    assert np.array_equal(pre_roll(pre_roll(frame, 13), -13), frame)


# ------------------------------------------------------------------- mask
def test_mask_geometry_and_binary():
    H, Wc, band = 8, 448, 64
    mask = make_mask(H, Wc, band, "redundant_white")
    assert mask.shape == (H, Wc)
    assert set(np.unique(mask).tolist()) == {0, 255}
    assert np.all(mask[:, :band] == 255)
    assert np.all(mask[:, Wc - band :] == 255)
    assert np.all(mask[:, band : Wc - band] == 0)


def test_mask_bands_are_byte_identical():
    mask = make_mask(4, 448, 64, "redundant_white")
    assert np.array_equal(mask[:, :64], mask[:, 448 - 64 :])


def test_mask_polarities_and_three_band():
    mask = make_mask(4, 448, 64, "content_white")
    assert np.all(mask[:, :64] == 0)
    assert np.all(mask[:, 64:384] == 255)

    tri = make_mask(4, 448, 64, "tri_band", seam_px=8)
    assert set(np.unique(tri).tolist()) == {0, 128, 255}
    assert np.all(tri[:, 64:72] == 128)
    assert np.all(tri[:, 376:384] == 128)


def test_mask_rejects_bad_input():
    with pytest.raises(ValueError, match="unknown mask strategy"):
        make_mask(4, 448, 64, "nope")
    with pytest.raises(ValueError, match="does not fit"):
        make_mask(4, 100, 60)


def test_mask_feather_leaves_the_polarity_contract():
    """feather_px > 0 is an experiment; the contract wants a strictly binary mask."""
    assert not np.array_equal(make_mask(64, 448, 64, "redundant_white", feather_px=6),
                              make_mask(64, 448, 64, "redundant_white", feather_px=0))


def test_all_strategies_are_reachable():
    for name in STRATEGIES:
        assert make_mask(4, 448, 64, name).shape == (4, 448)


# ------------------------------------------------------------------ blend
def test_weight_endpoints_and_monotonicity():
    for kind in ("smoothstep", "linear", "cosine"):
        w = weights(100, kind)
        assert w[0] < 0.02 and w[-1] > 0.98
        assert np.all(np.diff(w) >= 0)


def test_weights_reject_unknown_kind():
    with pytest.raises(ValueError, match="unknown blend weight"):
        weights(16, "cubic")


def test_blend_block_endpoints_are_pure():
    rng = np.random.default_rng(3)
    G = rng.integers(0, 255, (4, 32, 3)).astype(np.float64)
    B = rng.integers(0, 255, (4, 32, 3)).astype(np.float64)
    block = blend_block(G, B, d_star=0, endpoint_lock_px=0)
    assert np.allclose(block[:, 0], B[:, 0], atol=2.0)
    assert np.allclose(block[:, -1], G[:, -1], atol=2.0)


def test_endpoint_lock_pins_both_ends():
    rng = np.random.default_rng(4)
    G = rng.integers(0, 255, (4, 32, 3)).astype(np.float64)
    B = rng.integers(0, 255, (4, 32, 3)).astype(np.float64)
    block = blend_block(G, B, d_star=7, endpoint_lock_px=3)
    assert np.array_equal(block[:, :3], B[:, :3])
    assert np.array_equal(block[:, -3:], G[:, -3:])       # UNSHIFTED green


def test_blend_block_handles_2d_and_rejects_mismatch():
    G = np.zeros((4, 16), np.float64)
    B = np.ones((4, 16), np.float64)
    assert blend_block(G, B).shape == (4, 16)
    with pytest.raises(ValueError, match="must match"):
        blend_block(G, np.ones((4, 17)))


def test_endpoint_lock_never_lets_the_two_locks_collide():
    rng = np.random.default_rng(5)
    G = rng.integers(0, 255, (2, 10, 3)).astype(np.float64)
    B = rng.integers(0, 255, (2, 10, 3)).astype(np.float64)
    block = blend_block(G, B, endpoint_lock_px=999)       # absurd: clamped to L//2
    assert np.array_equal(block[:, :5], B[:, :5])
    assert np.array_equal(block[:, 5:], G[:, 5:])


def test_shift_ramp_is_a_no_op_without_a_shift():
    rng = np.random.default_rng(7)
    G = rng.integers(0, 255, (4, 32, 3)).astype(np.float64)
    B = rng.integers(0, 255, (4, 32, 3)).astype(np.float64)
    assert np.array_equal(blend_block(G, B, d_star=0, endpoint_lock_px=2, ramp_px=8),
                          blend_block(G, B, d_star=0, endpoint_lock_px=2))
    # and a zero ramp must keep the old hard-shift behaviour bit for bit
    assert np.array_equal(blend_block(G, B, d_star=7, endpoint_lock_px=3, ramp_px=0),
                          blend_block(G, B, d_star=7, endpoint_lock_px=3))


def test_shift_ramp_aligns_the_interior_and_pins_the_ends():
    """With B = roll(G, 5) the band must show one copy, not two offset by 5 px."""
    L = 64
    x = np.arange(L)[None, :]
    G = np.broadcast_to((x * 4 % 256).astype(np.float64)[..., None], (3, L, 1)).copy()
    G = np.repeat(G, 3, axis=2)
    B = np.roll(G, 5, axis=1)
    block = blend_block(G, B, d_star=5, endpoint_lock_px=0, ramp_px=16)
    # ends: left is B, right is the unshifted G (closure pair stays adjacent)
    assert np.allclose(block[:, 0], B[:, 0], atol=1.0)
    assert np.allclose(block[:, -1], G[:, -1], atol=1.0)
    # interior (before the ramp): the shifted G, i.e. B's content -- a 50/50 mix
    # of G and B here would sit exactly between the two ramps and double the edge
    mid = L // 2
    assert np.allclose(block[:, mid], np.roll(G, 5, axis=1)[:, mid], atol=6.0)
    plain = blend_block(G, B, d_star=0, endpoint_lock_px=0)
    assert abs(block[:, mid] - np.roll(G, 5, axis=1)[:, mid]).mean() < \
        abs(plain[:, mid] - np.roll(G, 5, axis=1)[:, mid]).mean()


def test_endpoint_lock_px_from_percentage():
    assert endpoint_lock_px(600, 2.0) == 12
    assert endpoint_lock_px(640, 0.0) == 0


# ----------------------------------------------------------- registration
def test_registration_recovers_a_known_shift():
    rng = np.random.default_rng(6)
    G = rng.integers(0, 255, (32, 64, 3)).astype(np.uint8)
    for shift in (-7, -1, 0, 3, 11):
        B = np.roll(G, shift, axis=1)
        res = circular_translate(G, B, search_px=20)
        assert isinstance(res, RegistrationResult)
        assert res.d_star == -shift or res.d_star == shift, (
            f"shift {shift} recovered as {res.d_star}"
        )
        assert res.mae_min < 1e-6


def test_registration_is_decisive_for_a_unique_match():
    rng = np.random.default_rng(7)
    G = rng.integers(0, 255, (32, 64, 3)).astype(np.uint8)
    B = np.roll(G, 4, axis=1)
    res = circular_translate(G, B, search_px=20)
    assert res.quality > 1.15


def test_registration_quality_drops_on_a_flat_cost_curve():
    """Identical uniform bands: every shift ties, so the curve says nothing about d."""
    G = np.full((16, 32, 3), 128, np.uint8)
    B = np.full((16, 32, 3), 128, np.uint8)
    res = circular_translate(G, B, search_px=10)
    assert res.quality == pytest.approx(1.0, abs=0.05)


def test_registration_quality_is_infinite_on_a_perfect_unique_match():
    rng = np.random.default_rng(8)
    G = rng.integers(0, 255, (16, 32, 3)).astype(np.uint8)
    res = circular_translate(G, G.copy(), search_px=10)
    assert res.mae_min == pytest.approx(0.0)
    assert res.quality == float("inf")


def test_registration_handles_an_empty_band():
    res = circular_translate(np.zeros((4, 0, 3)), np.zeros((4, 0, 3)), search_px=5)
    assert res.d_star == 0


def test_registration_rejects_shape_mismatch():
    with pytest.raises(ValueError, match="must match"):
        circular_translate(np.zeros((4, 8, 3), np.uint8), np.zeros((4, 9, 3), np.uint8), 3)


def test_to_gray_accepts_2d_and_3d():
    assert to_gray(np.zeros((4, 8), np.uint8)).shape == (4, 8)
    assert to_gray(np.zeros((4, 8, 3), np.uint8)).shape == (4, 8)
    assert to_gray(np.zeros((4, 8, 4), np.uint8)).shape == (4, 8)


# ------------------------------------------------- the canvas-assumption gate
def _canvas_with_bands(W=1280, L=214, canvas_w=1494, seed=5):
    """A canvas whose two redundant bands hold the SAME content, as the real one does."""
    band = np.tile(np.linspace(-60, 60, L) + 40 * np.sin(np.arange(L) / 9.0), (48, 1))
    band = band + np.random.default_rng(seed).normal(0, 6.0, band.shape)
    canvas = np.zeros((48, canvas_w), np.float32)
    canvas[:, :L] = band
    canvas[:, W : W + L] = band
    canvas[:, L:W] = 20 * np.sin(np.arange(W - L) / 30.0)
    return canvas, W, L, canvas_w


def test_a_canvas_wide_gradient_does_not_break_band_correlation():
    """The failure mode this gate used to have.

    A smooth brightness ramp across the canvas (what a VAE tile boundary leaves
    behind) offsets the two bands by a constant AND tilts each band slightly. Both
    are photometric, not geometric — the gate must not read them as "the framing
    moved", or it would refuse every gradient-bearing repaint.
    """
    canvas, W, L, canvas_w = _canvas_with_bands()
    graded = canvas + np.linspace(-20.0, 20.0, canvas_w)[None, :]

    flat = circular_translate(graded[:, :L], graded[:, W : W + L], search_px=16)
    assert flat.correlation > 0.9, flat
    # mean removal is what makes it immune: the raw residual is dominated by the tilt
    raw = float(np.abs(graded[:, :L] - graded[:, W : W + L]).mean())
    assert raw > 1.0
    assert flat.mae_min < raw


def test_a_reframed_canvas_breaks_band_correlation():
    """Different content in the two bands is exactly what a crop looks like."""
    canvas, W, L, canvas_w = _canvas_with_bands()
    rng = np.random.default_rng(99)
    canvas[:, W : W + L] = rng.normal(0, 40, (48, L))        # unrelated content

    res = circular_translate(canvas[:, :L], canvas[:, W : W + L], search_px=16)
    assert res.correlation < 0.3, res


def test_registration_recovers_the_drift_a_generative_tool_injects():
    canvas, W, L, canvas_w = _canvas_with_bands()
    shifted = canvas.copy()
    shifted[:, :L] = np.roll(shifted[:, :L], 5, axis=1)      # differential band drift
    res = circular_translate(shifted[:, :L], shifted[:, W : W + L], search_px=16)
    assert res.correlation > 0.9
    assert res.d_star in (-5, 5), res
