"""Estimate the AIGC tool's horizontal reframing, using the padded input as reference.

Why this module exists alongside the band-correlation gate:

The gate compares the two redundant bands against each other. It is a *weak*
detector of a reframe — a crop deletes the outer part of each band, so the two bands
lose different content and their correlation drops, but only in proportion to how
much was cut, and a modest crop stays above the threshold. It cannot say *how much*
was cropped, which is the number that decides whether the padding is still doing its
job (the plan's R1, its highest risk).

This module measures the crop directly, against the one reference a reframe cannot
drag along — the padded input, which is on disk at calibration time::

    padded_canvas_col = origin + scale * generated_canvas_col

The restore step has already forced the generated frame to canvas width, so ``scale``
is the fraction of the canvas the tool kept (``< 1`` means it cropped) and ``origin``
is the padded column shown at generated column 0. This is the shape the reference
tool was measured to have: output covering a centred 94.7% of the source longitude.

Only 1-D column profiles are compared — per-column structure energy, not colour — so
a heavy style change does not defeat the fit. The profile says *where the structure
is*; it does not care what colour the model repainted it.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .registration import to_gray


@dataclass(frozen=True)
class ReframeFit:
    """``padded_canvas_col = origin + scale * generated_canvas_col``.

    ``scale`` is the fraction of the canvas the tool kept, so ``scale < 1`` means it
    cropped and magnified. This matches how the reference tool behaves: the plan
    measured its output covering a *centred 94.7%* of the source longitude, i.e.
    scale 0.947 and ``origin`` at the left crop.
    """

    scale: float                     # kept fraction of the canvas (< 1 => cropped)
    origin: float                    # padded-canvas column shown at generated column 0
    correlation: float               # fit quality at (scale, origin)
    baseline_correlation: float      # fit quality of the "no reframe" model (1, 0)
    peak_z: float                    # how far the best score stands above the grid's spread
    crop_left_px: float
    crop_right_px: float
    note: str

    @property
    def crop_per_side_px(self) -> float:
        return (self.crop_left_px + self.crop_right_px) / 2.0

    @property
    def coverage(self) -> float:
        """Fraction of the padded canvas that survived -- the plan's 'span'."""
        return self.scale

    @property
    def is_identity(self) -> bool:
        return abs(self.scale - 1.0) < 0.005 and abs(self.origin) < 3.0

    def as_dict(self) -> dict:
        return {
            "scale": round(self.scale, 4),
            "origin": round(self.origin, 2),
            "coverage": round(self.coverage, 4),
            "crop_left_px": round(self.crop_left_px, 1),
            "crop_right_px": round(self.crop_right_px, 1),
            "crop_per_side_px": round(self.crop_per_side_px, 1),
            "correlation": round(self.correlation, 4),
            "baseline_correlation": round(self.baseline_correlation, 4),
            "peak_z": round(self.peak_z, 2),
            "note": self.note,
        }

    @property
    def is_confident(self) -> bool:
        """Whether the search found a distinguishable peak.

        Raw correlation is a conservative confidence measure here: a tool that drifts
        the two redundant bands *during* the repaint shifts part of the profile and
        depresses the correlation even when the scale is recovered correctly. What
        matters is whether the best (scale, origin) stands out from the rest of the
        score surface.
        """
        return self.peak_z >= 4.0


def column_profile(frame: np.ndarray) -> np.ndarray:
    """Per-column structure energy: mean |horizontal gradient| down each column.

    Deliberately not the column mean brightness: a repaint changes brightness
    wholesale (day to night) while leaving the *placement* of edges alone.
    """
    g = to_gray(frame)
    if g.shape[1] < 2:
        raise ValueError(f"need width >= 2, got {g.shape}")
    return np.abs(np.diff(g, axis=1)).mean(axis=0)


def mean_profile(frames, highpass: bool = True) -> np.ndarray:
    """Average the column profiles of several frames into one 1-D signature.

    ``highpass`` subtracts a moving average, which matters specifically for *scale*
    estimation: a slow sinusoidal component rescaled looks a lot like a phase-shifted
    version of itself, so low frequencies smear the peak in the (scale, origin) score
    surface. Removing them leaves the sharp, uniquely-placed features that pin the
    scale down.
    """
    frames = list(frames)
    if not frames:
        raise ValueError("no frames to profile")
    acc = None
    for frame in frames:
        p = column_profile(frame)
        acc = p if acc is None else acc + p
    profile = acc / len(frames)

    if highpass and profile.shape[0] >= 8:
        w = max(5, profile.shape[0] // 24)
        kernel = np.ones(w, np.float64) / w
        smooth = np.convolve(profile, kernel, mode="same")
        profile = profile - smooth

    profile = profile - profile.mean()
    norm = np.linalg.norm(profile)
    return profile / norm if norm > 1e-9 else profile


def _score(gen: np.ndarray, pad: np.ndarray, scale: float, origin: float) -> float:
    """Correlation between the generated profile and the padded profile resampled.

    ``pad_index = origin + scale * gen_index`` — a column at position j in the
    generated canvas is the padded canvas at ``origin + scale * j``.
    """
    j = np.arange(gen.shape[0], dtype=np.float64)
    idx = origin + scale * j
    if idx[0] < 0 or idx[-1] > pad.shape[0] - 1:
        return -np.inf
    sampled = np.interp(idx, np.arange(pad.shape[0], dtype=np.float64), pad)
    if sampled.std() < 1e-9 or gen.std() < 1e-9:
        return -np.inf
    return float(np.corrcoef(gen, sampled)[0, 1])


def fit_horizontal(
    generated_profile: np.ndarray,
    padded_profile: np.ndarray,
    scale_range: tuple[float, float] = (0.75, 1.35),
    coarse: int = 121,
) -> ReframeFit:
    """Search (scale, origin) by coarse grid then local refine on the correlation."""
    Wc = padded_profile.shape[0]
    scales = np.linspace(scale_range[0], scale_range[1], coarse)
    origins = np.arange(-0.30 * Wc, 0.30 * Wc + 1e-9, max(1.0, Wc / 400.0))

    best = (-np.inf, 1.0, 0.0)
    coarse_scores: list[float] = []
    for scale in scales:
        for origin in origins:
            score = _score(generated_profile, padded_profile, scale, origin)
            coarse_scores.append(score)
            if score > best[0]:
                best = (score, float(scale), float(origin))

    finite = np.asarray([s for s in coarse_scores if np.isfinite(s)], np.float64)
    peak_z = float((best[0] - finite.mean()) / finite.std()) if finite.size > 2 and finite.std() > 1e-9 else 0.0

    # refine: the coarse origin grid is the limiting factor
    score, scale, origin = best
    for _ in range(3):
        step_s = (scales[1] - scales[0]) / 4.0
        step_o = max(0.25, (origins[1] - origins[0]) / 4.0 if len(origins) > 1 else 0.25)
        for ds in np.linspace(-step_s, step_s, 9):
            for do in np.linspace(-step_o, step_o, 9):
                s2, o2 = scale + ds, origin + do
                if not (scale_range[0] <= s2 <= scale_range[1]):
                    continue
                sc = _score(generated_profile, padded_profile, s2, o2)
                if sc > score:
                    score, scale, origin = sc, float(s2), float(o2)

    baseline = _score(generated_profile, padded_profile, 1.0, 0.0)
    crop_left = origin
    crop_right = Wc - (origin + scale * Wc)
    if abs(scale - 1.0) < 0.005 and abs(origin) < 3.0:
        note = "identity: the tool preserved the canvas framing"
    elif scale < 1.0:
        note = (f"the tool kept {scale:.1%} of the canvas, cropping about "
                f"{crop_left:.0f} columns on the left and {crop_right:.0f} on the right "
                f"(canvas columns are source columns)")
    else:
        note = (f"the tool kept {scale:.1%} of the canvas, i.e. it saw more than the "
                f"padded content (scale {scale:.4f}) -- the padding is not the limit")

    return ReframeFit(scale=scale, origin=origin, correlation=float(score),
                      baseline_correlation=float(baseline), peak_z=peak_z,
                      crop_left_px=float(crop_left), crop_right_px=float(crop_right),
                      note=note)
