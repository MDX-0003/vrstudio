"""Seam metrics and the acceptance verdict.

Two numbers, deliberately kept together:

``wrap_percentile``
    where the wrap discontinuity sits in the frame's own distribution of
    adjacent-column differences. Scale-free, so it works on 4000-wide and
    7680-wide footage alike (measured interior medians differ by ~10x between
    them while both are "normal").

``interior_max`` / ``wrap_over_worst``
    the frame's worst adjacent-column pair anywhere. **Required**, because the
    percentile alone can be gamed: rolling is a pure circular shift, so it moves
    a broken wrap into the middle of the raster and the wrap metric collapses
    (measured: 12.36 -> 0.69) while the discontinuity itself is unchanged
    (12.34, now in the frame's interior). A verdict that never looks at the worst
    interior pair will pass a rolled-but-still-broken video.

Only single column pairs are used, never a band. A band would have to pair
column ``W-1-k`` with column ``k``, which for k > 0 are almost a full revolution
apart — the wrap number would then be dominated by unrelated content and would no
longer be comparable with the interior distribution the percentile is taken over.
Per-frame noise is handled by averaging over frames instead (``SeamAccumulator``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .registration import to_gray


@dataclass(frozen=True)
class SeamMetrics:
    width: int
    wrap: float                 # mean |col 0 - col W-1| — the wrap adjacency
    wrap_percentile: float      # percentile of `wrap` among all interior column pairs
    interior_mean: float
    interior_median: float
    interior_p95: float
    interior_p99: float
    interior_max: float         # the frame's worst adjacent-column pair
    baseline_mean: float        # middle-band mean, used as the gate's scale
    wrap_over_worst: float

    def as_dict(self) -> dict:
        return {
            "width": self.width,
            "wrap": round(self.wrap, 4),
            "wrap_percentile": round(self.wrap_percentile, 1),
            "interior_mean": round(self.interior_mean, 4),
            "interior_median": round(self.interior_median, 4),
            "interior_p95": round(self.interior_p95, 4),
            "interior_p99": round(self.interior_p99, 4),
            "interior_max": round(self.interior_max, 4),
            "baseline_mean": round(self.baseline_mean, 4),
            "wrap_over_worst": round(self.wrap_over_worst, 4),
        }


def _assemble(width: int, adj: np.ndarray, wrap: float,
              interior_band: tuple[float, float]) -> SeamMetrics:
    lo = max(0, int(width * interior_band[0]))
    hi = max(lo + 1, min(width - 1, int(width * interior_band[1])))
    window = adj[lo:hi]
    worst = float(adj.max())
    return SeamMetrics(
        width=width,
        wrap=wrap,
        wrap_percentile=float(100.0 * (adj < wrap).mean()),
        interior_mean=float(adj.mean()),
        interior_median=float(np.median(adj)),
        interior_p95=float(np.percentile(adj, 95)),
        interior_p99=float(np.percentile(adj, 99)),
        interior_max=worst,
        baseline_mean=float(window.mean()) if window.size else float(adj.mean()),
        wrap_over_worst=(wrap / worst) if worst > 0 else float("inf"),
    )


def seam_metrics(
    frame: np.ndarray,
    interior_band: tuple[float, float] = (0.25, 0.75),
) -> SeamMetrics:
    """Measure the wrap against the frame's own interior column statistics."""
    g = to_gray(frame)
    if g.ndim != 2 or g.shape[1] < 4:
        raise ValueError(f"need a 2D frame of width >= 4, got {g.shape}")
    width = g.shape[1]
    adj = np.abs(np.diff(g, axis=1)).mean(axis=0)              # (W-1,)
    wrap = float(np.abs(g[:, 0] - g[:, width - 1]).mean())
    return _assemble(width, adj, wrap, interior_band)


def verdict(
    after: SeamMetrics,
    before: SeamMetrics | None = None,
    max_wrap_percentile: float = 95.0,
    worst_interior_tol: float = 1.0,
) -> tuple[str, list[str]]:
    """``("ok" | "warn" | "bad", reasons)``.

    Two independent break indicators, and they catch different things:

    * ``wrap > worst interior pair`` is scale-free and says "the wrap *is* the
      worst discontinuity in this frame" — it needs no reference video, and it
      catches a seam that is present but not an outlier in a noisy frame.
    * the worst-interior comparison against ``before`` catches a seam that was
      *relocated*: a circular shift makes ``wrap_over_worst`` look excellent while
      the discontinuity reappears in the raster interior.

    Either one failing is a hard failure; the percentile alone is only a warning,
    because a seam that is genuinely in place but rougher than the rest of the
    frame is a judgement call rather than a broken panorama.
    """
    reasons: list[str] = []
    hard = False

    if after.wrap_percentile > max_wrap_percentile:
        reasons.append(
            f"wrap sits at percentile {after.wrap_percentile:.1f} "
            f"(limit {max_wrap_percentile:.0f}); wrap={after.wrap:.2f}"
        )

    if after.wrap_over_worst > 1.0:
        hard = True
        reasons.append(
            f"wrap {after.wrap:.2f} is worse than the frame's own worst interior pair "
            f"{after.interior_max:.2f} (x{after.wrap_over_worst:.2f}) -- the seam is the "
            f"worst discontinuity in the frame"
        )

    if before is not None:
        limit = before.interior_max * (1.0 + worst_interior_tol)
        if after.interior_max > limit:
            hard = True
            reasons.append(
                f"worst interior column pair {after.interior_max:.2f} exceeds the "
                f"pre-existing worst {before.interior_max:.2f} "
                f"(tolerance {worst_interior_tol:.2f}) -- a new discontinuity was introduced"
            )

    if not reasons:
        return "ok", []
    return ("bad" if hard else "warn"), reasons


class SeamAccumulator:
    """Streaming form of :func:`seam_metrics`, for a pass that must not buffer.

    ``recompose`` already has every output frame in hand, so it accumulates the
    per-column difference and its running maximum rather than writing the clip out
    and reading it back. The percentile therefore refers to the time-averaged
    column profile, which is the right reference for a defect that is either there
    for the whole clip or not at all.
    """

    def __init__(self, width: int):
        if width < 4:
            raise ValueError(f"need width >= 4, got {width}")
        self.width = int(width)
        self.frames = 0
        self._sum_adj = np.zeros(self.width - 1, np.float64)
        self._max_adj = np.zeros(self.width - 1, np.float64)
        self._sum_wrap = 0.0

    def add(self, frame: np.ndarray) -> None:
        g = to_gray(frame)
        if g.shape[1] != self.width:
            raise ValueError(f"frame width {g.shape[1]} != {self.width}")
        adj = np.abs(np.diff(g, axis=1)).mean(axis=0)
        self._sum_adj += adj
        np.maximum(self._max_adj, adj, out=self._max_adj)
        self._sum_wrap += float(np.abs(g[:, 0] - g[:, self.width - 1]).mean())
        self.frames += 1

    def result(self, interior_band: tuple[float, float] = (0.25, 0.75)) -> SeamMetrics:
        if not self.frames:
            raise ValueError("no frames were accumulated")
        return _assemble(self.width, self._sum_adj / self.frames,
                         self._sum_wrap / self.frames, interior_band)


def metrics_over_frames(
    frames,
    interior_band: tuple[float, float] = (0.25, 0.75),
) -> SeamMetrics:
    """Aggregate :func:`seam_metrics` over a list of frames."""
    frames = list(frames)
    if not frames:
        raise ValueError("no frames to measure")
    acc = SeamAccumulator(frames[0].shape[1])
    for frame in frames:
        acc.add(frame)
    return acc.result(interior_band)
