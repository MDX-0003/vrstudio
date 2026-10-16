"""``vrstudio calib`` — does the AIGC tool preserve the canvas framing?

The whole scheme rests on one empirical assumption: the tool returns the canvas
reframed only in resolution, not in framing. If it crops or rescales the canvas,
the regions are sliced in the wrong place and the closure guarantee is void.

This command answers that question from the generated video alone (plus the
manifest), so it needs no extra round trip:

* **aspect drift** — the generated aspect vs the canvas aspect. A tool that
  letterboxes or crops to its own bucket shows up here immediately.
* **band correspondence** — the same measurement ``recompose`` gates on. When the
  framing is preserved the two redundant bands still hold the same content and
  ``mae_over_baseline`` stays small; when the framing shifted, the bands no longer
  correspond and it jumps, at every candidate ``d*``.

It is a *diagnostic*, not a calibration of a crop ratio: it tells you whether to
trust the current ``target_aspect``, and by how much. Choosing the tool's native
bucket is the cheapest way to make this number small.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import typer

from .. import config as config_mod
from ..redraw.framefit import fit_horizontal, mean_profile
from ..redraw.geometry import restore_height
from ..redraw.manifest import Manifest
from ..progress import Log
from ..redraw.recompose_core import RecomposeSpec, restore_canvas, slice_regions
from ..redraw.registration import circular_translate, to_gray
from ..media.videoio import probe, sample_frames



def main(
    src: Path = typer.Option(..., "--in", exists=True, dir_okay=False,
                             help="video returned by the AIGC tool"),
    manifest: Path = typer.Option(..., "--manifest", exists=True, dir_okay=False,
                                  help="manifest written by `vrstudio pad`"),
    config: Path | None = typer.Option(None, "--config", help="TOML case config"),
    overrides: list[str] = typer.Option([], "--set", help="config override key=value"),
    samples: int = typer.Option(12, "--samples", help="frames sampled for the band check"),
    padded: Path | None = typer.Option(None, "--padded", exists=True, dir_okay=False,
                                       help="the padded video handed to the tool; enables the "
                                            "reframe fit (default: the manifest's sibling)"),
) -> None:
    """Check whether the AIGC tool preserved the padded canvas framing."""
    log = Log()
    try:
        conf = config_mod.load(config, overrides)
        man = Manifest.load(manifest)
        plan = man.to_pad_plan()
        info = probe(src, conf)
    except Exception as exc:                                  # noqa: BLE001
        log.error(str(exc))
        raise typer.Exit(2)

    gen_w, gen_h = info.size
    Wc, L, W = plan.Wc, plan.L, plan.W
    gen_aspect = gen_w / gen_h
    canvas_aspect = Wc / plan.H
    drift_pct = 100.0 * (gen_aspect / canvas_aspect - 1.0)

    print("canvas framing")
    print(f"    padded canvas   {Wc}x{plan.H}   aspect {canvas_aspect:.4f}")
    print(f"    tool returned   {gen_w}x{gen_h}   aspect {gen_aspect:.4f}"
          f"   ({drift_pct:+.2f}% vs canvas)")
    if abs(drift_pct) > float(conf.get("recompose.aspect_drift_warn_pct", 2.0)):
        log.warn(
            f"the tool returned aspect {gen_aspect:.4f} vs the canvas {canvas_aspect:.4f} "
            f"({drift_pct:+.1f}%). recompose still delivers exactly W x H because closure is "
            f"horizontal, and a plain rescale is undone by the restore -- but if the tool "
            f"letterboxed or reframed instead, the regions will be sliced off-target. The "
            f"reframe fit below is what decides which of those happened."
        )

    if L <= 0:
        log.error("manifest has P == 0 — no redundancy to calibrate against")
        raise typer.Exit(2)

    # same canvas geometry recompose uses: the canvas is known, so the tool's aspect
    # error must not be allowed to move the region boundaries
    canvas_size = plan.canvas_size
    spec = RecomposeSpec(W=W, Wc=Wc, P=plan.P, L=L)
    search_px = int(round(L * float(conf.get("recompose.search_pct", 25.0)) / 100.0))
    frames = sample_frames(src, samples, conf)
    if not frames:
        log.error("no frames decoded")
        raise typer.Exit(1)

    ds, maes, quals, corrs, base = [], [], [], [], []
    for frame in frames:
        canvas = restore_canvas(frame, canvas_size, str(conf.get("recompose.restore_interp",
                                                                 "lanczos4")))
        G, R, B = slice_regions(canvas, spec)
        res = circular_translate(G, B, search_px, int(conf.get("recompose.max_sample_rows", 256)))
        ds.append(res.d_star)
        maes.append(res.mae_min)
        quals.append(res.quality)
        corrs.append(res.correlation)
        base.append(float(np.abs(np.diff(to_gray(R), axis=1)).mean()))

    mae_min = float(np.median(maes))
    quality = float(np.median(quals))
    correlation = float(np.median(corrs))
    baseline = float(np.mean(base))
    min_corr = float(conf.get("recompose.min_band_correlation", 0.80))
    warn_quality = float(conf.get("recompose.warn_quality", 1.15))

    print("\nredundant-band correspondence")
    print(f"    d* (columns)          {int(np.median(ds)):+d}"
          f"   median of {len(ds)} sampled frames")
    print(f"    band correlation      {correlation:.3f}   (gate aborts below {min_corr:.2f})")
    print(f"      same content scores ~0.95+; unrelated content ~0. A smooth brightness")
    print(f"      gradient across the canvas does NOT lower this -- it only shifts the mean.")
    print(f"    structure residual    {mae_min:.3f}   (mean-removed, levels)")
    print(f"    canvas baseline       {baseline:.3f}   (adjacent-column mean of the main content)")
    quality_text = ("exact match (every other shift is worse)" if not np.isfinite(quality)
                    else f"{quality:.3f}" + ("   (flat curve, match not decisive)"
                                             if quality < warn_quality else ""))
    print(f"    cost-curve quality    {quality_text}")

    if correlation < min_corr:
        verdict, note = "FAIL", (
            "the two bands are not the same scene, so the tool reframed the canvas and the "
            "region boundaries are wrong. recompose would produce a geometrically wrong "
            "panorama, and refuses to run."
        )
    elif quality < warn_quality:
        verdict, note = "WARN", "the match is not decisive; d* may be coincidental."
    else:
        verdict, note = "OK", "the framing looks preserved; recompose can be trusted."

    print(f"\nverdict  {verdict}\n    {note}")

    _reframe_section(man, manifest, padded, info, canvas_size, conf, samples, log)
    raise typer.Exit(1 if verdict == "FAIL" else 0)


def _reframe_section(man, manifest: Path, padded: Path | None, info, canvas_size,
                     conf, samples: int, log) -> None:
    """Recover the tool's horizontal crop by comparing against the padded input.

    The band check above is a weak detector of a reframe: a crop deletes the *outer*
    part of each redundant band, so the two bands lose different content and their
    correlation drops — but only in proportion to how much was cut, and a small crop
    stays above the gate. This measures the crop directly instead, against the one
    reference a reframe cannot drag along: the padded input itself.
    """
    candidate = padded
    if candidate is None and man.padded:
        sibling = Path(manifest).parent / man.padded
        candidate = sibling if sibling.is_file() else None
    if candidate is None or not Path(candidate).is_file():
        print("\nreframe check   unavailable -- pass --padded PADDED.mp4 to measure the "
              "tool's horizontal crop against the padded input")
        return

    pad_frames = sample_frames(candidate, samples, conf)
    gen_frames = sample_frames(info.path, samples, conf)
    if not pad_frames or not gen_frames:
        log.warn("reframe check: could not decode frames")
        return

    interp = str(conf.get("recompose.restore_interp", "lanczos4"))
    gen_canvas = [restore_canvas(f, canvas_size, interp) for f in gen_frames]
    P = man.canvas.P
    # Both sides are already in canvas coordinates: the padded video *is* the canvas,
    # and the generated frame was restored into it. No pre-roll correction is needed
    # or wanted -- applying one would shift the reference profile by P columns.
    pad_canvas = list(pad_frames)

    fit = fit_horizontal(mean_profile(gen_canvas), mean_profile(pad_canvas))
    print("\nreframe check (vs the padded input)")
    print(f"    scale                 {fit.scale:.4f}   kept fraction of the canvas")
    print(f"    crop left / right     {fit.crop_left_px:.0f} / {fit.crop_right_px:.0f}"
          f"   canvas columns (= source columns)")
    print(f"    fit correlation       {fit.correlation:.3f}"
          f"   (no-reframe model: {fit.baseline_correlation:.3f})")
    print(f"    peak significance     {fit.peak_z:.1f} sigma"
          f"   ({'confident' if fit.is_confident else 'NOT confident'})")
    print(f"    {fit.note}")

    if not fit.is_confident:
        log.warn(f"reframe fit found no clear peak ({fit.peak_z:.1f} sigma); treat the "
                 f"crop numbers as indicative only")
    if fit.scale < 1.0 and fit.crop_per_side_px >= P:
        log.warn(
            f"the tool cropped {fit.crop_per_side_px:.0f} columns per side but the "
            f"redundancy is only P = {P}: the padding is fully consumed and the tool "
            f"may be cutting into real content. Raise pad.target_aspect."
        )
