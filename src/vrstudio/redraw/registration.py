"""Blue/green registration: the one alignment this pipeline performs.

``G`` and ``B`` are two renderings of the *same* content (the circular band
straddling the source wrap). In the padded input they are pixel-identical; after
the AIGC tool they drift horizontally. This module recovers the integer circular
shift that best re-aligns them.

Three deliberate constraints:

* **Integer pixel, circular.** An integer circular shift is lossless — no
  resampling, no interpolation, no softening of the very detail the seam sits
  on. Sub-pixel alignment would require a resample and is not worth it.
* **No warping, no optical flow.** Optical flow answers "where did this content
  move"; when the two sides hold *different content* the question is ill-posed
  and the optimizer tears thin structure. Measured on the reference clip, a
  maximally free per-row warp reached 4.05 where an adjacent pair reached ~1.0,
  i.e. there is a residual no displacement field can remove.
* **Direction is horizontal only.** No mirror, no flip, no scale. ``G`` and ``B``
  are the same content in the same orientation; anything else is a modelling
  error, and admitting it would make ``d*`` uninterpretable.

Cost: the search is ``2S+1`` MAE evaluations per frame. A vertical reduction
(``max_sample_rows``) bounds that; the winner is then re-scored at full
resolution. ``quality`` comes from the reduced curve (a shape measure), while
``mae_min`` comes from the full-resolution re-score so it is comparable with the
interior baseline measured on the same canvas.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class RegistrationResult:
    d_star: int          # the circular shift applied to G, in canvas columns
    mae_min: float       # mean-removed MAE at d_star, full resolution
    mae_second: float    # best MAE outside the exclusion window (reduced curve)
    quality: float       # mae_second / mae_min; near 1 means a flat, untrustworthy cost curve
    correlation: float   # Pearson r between the aligned bands -- the structural gate


def to_gray(frame: np.ndarray) -> np.ndarray:
    """Luma as float32. Frames are BGR (ffmpeg bgr24), matching the rest of vrstudio."""
    if frame.ndim == 2:
        return frame.astype(np.float32)
    if frame.shape[2] == 4:
        frame = frame[:, :, :3]
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _mean_removed(a: np.ndarray) -> np.ndarray:
    """Remove the band's own mean before comparing.

    The two redundant bands sit at opposite ends of the canvas, so any smooth
    brightness ramp across the canvas (exactly what a VAE tile boundary leaves
    behind) offsets them by a constant. That offset is a photometric fact the blend
    is designed to absorb, not evidence that the framing moved — comparing raw
    levels would make the gate fire on every gradient.
    """
    a = a.astype(np.float32)
    return a - a.mean()


def _reduce_rows(a: np.ndarray, target: int) -> np.ndarray:
    """Shrink vertically to at most ``target`` rows, leaving the width untouched.

    Width must not be resampled: ``d*`` is a column-precision quantity, and any
    horizontal interpolation would smear the cost curve's minimum.
    """
    h = a.shape[0]
    if target <= 0 or h <= target:
        return a
    return cv2.resize(a, (a.shape[1], int(target)), interpolation=cv2.INTER_AREA)


def _mae_curve(G: np.ndarray, B: np.ndarray, search_px: int) -> tuple[np.ndarray, np.ndarray]:
    ds = np.arange(-search_px, search_px + 1)
    curve = np.empty(ds.shape, np.float64)
    for i, d in enumerate(ds):
        curve[i] = np.abs(np.roll(G, int(d), axis=1) - B).mean()
    return ds, curve


def circular_translate(
    G: np.ndarray,
    B: np.ndarray,
    search_px: int,
    max_sample_rows: int = 256,
    exclude_px: int | None = None,
) -> RegistrationResult:
    """Best integer circular shift ``d`` aligning ``G`` onto ``B``.

    ``correlation`` is the gate's input, not ``mae_min``: correlation is scale-free,
    so it answers "is this the same scene?" independently of how much brighter or
    darker the tool made one end of the canvas. Framing preserved gives r ~ 0.95+;
    unrelated content gives r ~ 0.
    """
    if G.shape != B.shape:
        raise ValueError(f"G {G.shape} and B {B.shape} must match")
    if G.shape[1] == 0:
        return RegistrationResult(0, 0.0, 0.0, float("inf"), 1.0)

    g, b = _mean_removed(to_gray(G)), _mean_removed(to_gray(B))
    S = max(0, int(search_px))

    ds, curve = _mae_curve(_reduce_rows(g, max_sample_rows),
                           _reduce_rows(b, max_sample_rows), S)
    k = int(np.argmin(curve))
    d0 = int(ds[k])
    mae_min_reduced = float(curve[k])

    ex = exclude_px if exclude_px is not None else max(2, S // 50)
    far = np.abs(ds - d0) > ex
    mae_second = float(curve[far].min()) if far.any() else float("nan")
    # quality = how much worse the runner-up is. A perfect match (mae_min == 0)
    # with a worse runner-up is maximally decisive; a zero everywhere means every
    # shift ties and the curve says nothing about d.
    if mae_min_reduced > 0:
        quality = mae_second / mae_min_reduced
    else:
        quality = float("inf") if (np.isfinite(mae_second) and mae_second > 0) else 1.0

    # full-resolution re-score over a small window around the coarse winner
    win = [d for d in range(d0 - 2, d0 + 3) if -S <= d <= S]
    exact = [(float(np.abs(np.roll(g, d, axis=1) - b).mean()), d) for d in win]
    mae_min, d_star = min(exact)

    aligned = np.roll(g, d_star, axis=1)
    if aligned.std() < 1e-6 or b.std() < 1e-6:
        # a flat band carries no structure: agreement is meaningless, not perfect
        correlation = 1.0 if (aligned.std() < 1e-6 and b.std() < 1e-6) else 0.0
    else:
        correlation = float(np.corrcoef(aligned.ravel(), b.ravel())[0, 1])

    return RegistrationResult(d_star, mae_min, mae_second, quality, correlation)
