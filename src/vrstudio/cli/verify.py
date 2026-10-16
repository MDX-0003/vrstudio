"""``vrstudio verify`` — did the wrap actually close?

Compares a before/after pair on the two metrics that matter together: the wrap's
percentile in the frame's own adjacent-column distribution, and the frame's worst
adjacent-column pair. The second is not decoration — a pure circular shift moves a
broken wrap into the middle of the raster and collapses the first (measured:
12.36 -> 0.69 while the discontinuity stayed at 12.34), so a percentile-only check
can be passed by rolling.
"""

from __future__ import annotations

from pathlib import Path

import typer

from .. import config as config_mod
from ..progress import Log
from ..redraw.verify_core import metrics_over_frames, verdict
from ..media.videoio import probe, sample_frames



def _measure(path: Path, conf, samples: int, log: Log):
    info = probe(path, conf)
    frames = sample_frames(path, samples, conf)
    if not frames:
        log.error(f"could not decode any frame from {path}")
        raise typer.Exit(1)
    return info, metrics_over_frames(
        frames, interior_band=tuple(conf.get("verify.interior_band", (0.25, 0.75)))
    )


def main(
    before: Path | None = typer.Option(None, "--before", exists=True, dir_okay=False,
                                       help="pre-AIGC source (optional; enables the "
                                            "worst-interior comparison)"),
    after: Path = typer.Option(..., "--after", exists=True, dir_okay=False,
                               help="video to judge"),
    config: Path | None = typer.Option(None, "--config", help="TOML case config"),
    overrides: list[str] = typer.Option([], "--set", help="config override key=value"),
    samples: int = typer.Option(24, "--samples", help="frames sampled from each video"),
) -> None:
    """Measure the longitude wrap before/after and return a verdict."""
    log = Log()
    try:
        conf = config_mod.load(config, overrides)
    except config_mod.ConfigError as exc:
        log.error(str(exc))
        raise typer.Exit(2)

    info_after, m_after = _measure(after, conf, samples, log)
    m_before = None
    if before is not None:
        _, m_before = _measure(before, conf, samples, log)

    status, reasons = verdict(
        m_after, before=m_before,
        max_wrap_percentile=float(conf.get("verify.max_wrap_percentile", 95)),
        worst_interior_tol=float(conf.get("verify.worst_interior_tol", 1.0)),
    )

    def block(title: str, m, info) -> None:
        print(f"{title}   {info.size[0]}x{info.size[1]}  {info.frames or '?'} frames")
        print(f"    wrap                {m.wrap:10.4f}   percentile {m.wrap_percentile:5.1f}")
        print(f"    interior mean/med   {m.interior_mean:10.4f}   {m.interior_median:.4f}")
        print(f"    interior p95/p99    {m.interior_p95:10.4f}   {m.interior_p99:.4f}")
        print(f"    interior worst      {m.interior_max:10.4f}   wrap/worst {m.wrap_over_worst:.3f}")

    print()
    if m_before is not None:
        block("before", m_before, probe(before, conf))
        print()
    block("after ", m_after, info_after)
    print(f"\nverdict  {status.upper()}")
    for reason in reasons:
        print(f"  - {reason}")
    if m_before is not None and status != "bad":
        print(f"  worst interior pair {m_before.interior_max:.4f} -> "
              f"{m_after.interior_max:.4f} (no new discontinuity introduced)")

    raise typer.Exit(0 if status != "bad" else 1)
