"""Reframe detection: can the tool's horizontal crop be recovered from data?

The band-correlation gate is blind to a uniform reframe (the bands are copies and
transform together), so this compares against the *padded input* instead — the one
reference a reframe cannot drag along. These tests pin both the recovery and, just
as importantly, the fact that a style change does not defeat it.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from vrstudio.redraw.framefit import column_profile, fit_horizontal, mean_profile


def _structured_canvas(Wc: int = 960, H: int = 240, seed: int = 3,
                       phase: float = 0.0) -> np.ndarray:
    """Column profile with distinctive, uniquely-placed structure."""
    rng = np.random.default_rng(seed)
    x = np.arange(Wc, dtype=np.float64)
    col = (120.0
           + 40.0 * np.sin(2 * np.pi * 5 * x / Wc + phase)
           + 30.0 * np.sin(2 * np.pi * 17 * x / Wc + 1.1 + phase)
           + 25.0 * np.sin(2 * np.pi * 41 * x / Wc + 2.3 + phase)
           + 20.0 * np.exp(-((x - 0.27 * Wc) ** 2) / (2 * (0.02 * Wc) ** 2))
           + 25.0 * np.exp(-((x - 0.81 * Wc) ** 2) / (2 * (0.015 * Wc) ** 2)))
    frame = np.tile(col[None, :], (H, 1))
    frame = frame + rng.normal(0, 1.0, frame.shape)
    return np.repeat(np.clip(frame, 0, 255).astype(np.uint8)[:, :, None], 3, axis=2)


def test_identity_is_recovered_as_identity():
    canvas = _structured_canvas()
    fit = fit_horizontal(mean_profile([canvas]), mean_profile([canvas]))
    assert fit.is_identity
    assert abs(fit.scale - 1.0) < 0.01
    assert abs(fit.crop_per_side_px) < 5
    assert "identity" in fit.note


@pytest.mark.parametrize("crop_cols", [20, 48, 90])
def test_a_centred_crop_is_recovered(crop_cols):
    """crop -> rescale back to canvas width, exactly what a reframing tool does.

    This is the plan's measured behaviour on the reference tool: output covering a
    centred 94.7% of the source longitude, i.e. 2.65% cropped per side.
    """
    canvas = _structured_canvas()
    Wc = canvas.shape[1]
    rescaled = cv2.resize(canvas[:, crop_cols : Wc - crop_cols], (Wc, canvas.shape[0]),
                          interpolation=cv2.INTER_LINEAR)

    fit = fit_horizontal(mean_profile([rescaled]), mean_profile([canvas]))
    assert fit.scale < 1.0, fit
    assert fit.coverage == pytest.approx((Wc - 2 * crop_cols) / Wc, abs=0.02), fit
    assert fit.crop_left_px == pytest.approx(crop_cols, abs=6), fit
    assert fit.crop_right_px == pytest.approx(crop_cols, abs=6), fit
    assert "cropping" in fit.note


def test_an_asymmetric_crop_is_recovered():
    canvas = _structured_canvas()
    Wc = canvas.shape[1]
    left, right = 30, 74
    rescaled = cv2.resize(canvas[:, left : Wc - right], (Wc, canvas.shape[0]),
                          interpolation=cv2.INTER_LINEAR)
    fit = fit_horizontal(mean_profile([rescaled]), mean_profile([canvas]))
    assert fit.crop_left_px == pytest.approx(left, abs=8), fit
    assert fit.crop_right_px == pytest.approx(right, abs=8), fit


def test_a_style_change_does_not_defeat_the_fit():
    """The profile records where structure is, not what colour it is.

    A generative repaint changes brightness and contrast wholesale (the reference
    clip went daylight-blue to night-storm). A fit based on column means would be
    destroyed by that; one based on structure energy is not.
    """
    canvas = _structured_canvas()
    Wc = canvas.shape[1]
    crop_cols = 48
    rescaled = cv2.resize(canvas[:, crop_cols : Wc - crop_cols], (Wc, canvas.shape[0]),
                          interpolation=cv2.INTER_LINEAR)

    # restyle: crush brightness, flip contrast, drain colour, add noise
    styled = (255.0 - rescaled.astype(np.float64) * 0.35) * np.array([0.35, 0.8, 1.0])
    styled = styled + np.random.default_rng(0).normal(0, 4.0, styled.shape)
    styled = np.clip(styled, 0, 255).astype(np.uint8)

    plain = fit_horizontal(mean_profile([rescaled]), mean_profile([canvas]))
    restyled = fit_horizontal(mean_profile([styled]), mean_profile([canvas]))
    assert restyled.crop_per_side_px == pytest.approx(plain.crop_per_side_px, abs=8), restyled


def test_unrelated_content_is_reported_as_a_bad_fit():
    a = _structured_canvas(seed=1)
    b = _structured_canvas(seed=2, phase=1.9)
    fit = fit_horizontal(mean_profile([b]), mean_profile([a]))
    assert fit.correlation < 0.7, fit
    assert fit.correlation > fit.baseline_correlation or fit.correlation < 0.7


def test_profile_is_structure_not_level():
    """A contrast change scales the profile; the normalised shape survives.

    The 8-bit rounding of the scaling leaves a few percent of shape error, which is
    why the fit is a coarse diagnostic (columns, not sub-pixel) rather than a
    measurement.
    """
    canvas = _structured_canvas()
    dimmed = np.clip(canvas.astype(np.float64) * 0.4, 0, 255).astype(np.uint8)
    p0, p1 = column_profile(canvas), column_profile(dimmed)
    assert np.abs(p0 / p0.max() - p1 / p1.max()).max() < 0.08


def test_mean_profile_requires_frames():
    with pytest.raises(ValueError, match="no frames"):
        mean_profile([])
