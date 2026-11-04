"""``vrstudio recompose`` — turn the repainted canvas back into a closed panorama.

Two passes, and the reason is the closure guarantee rather than throughput:

* pass 1 measures the blue/green drift per frame and the canvas's own adjacent-column
  baseline, then fixes **one** ``d*`` for the whole clip. A per-frame ``d*`` would
  make the blend block breathe, and the content next to the seam is temporally
  stable to ~0.99 correlation, so adapting buys nothing and costs flicker.
* pass 2 renders with that constant, slicing ``G``/``R``/``B`` and blending.

Pass 1 also feeds the gate. If the tool did not preserve the canvas framing the
regions are sliced in the wrong place, the two redundant bands stop corresponding,
and the output would be a plausible-looking panorama with the seam in the wrong
spot — so that condition aborts instead of rendering.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import typer

from .. import config as config_mod
from ..redraw.blend import endpoint_lock_px as lock_px_from_pct
from ..redraw.geometry import restore_height
from ..redraw.manifest import Manifest
from ..progress import Log, RateMeter
from ..redraw.recompose_core import (ComposeBuffers, RecomposeSpec, recompose_u8,
                                     restore_canvas, slice_regions)
from ..redraw.registration import (RegistrationResult, circular_translate,
                                   gate_registration, to_gray)
from ..redraw.report import (BlendReport, RecomposeReport, RegistrationReport, RepairReport)
from ..redraw.verify_core import SeamAccumulator, verdict
from ..media.videoio import FrameReader, FrameWriter, probe



def _sample_indices(total: int, count: int) -> list[int]:
    """Evenly spaced frame indices to analyse in the gate pass.

    ``d*`` is one constant for the whole clip and the gate answers a clip-level
    question, so registering every frame buys no accuracy. Sampling spreads the
    checks over the clip instead of taking only the head. ``count <= 0`` or a
    clip no longer than ``count`` returns every frame.
    """
    if total <= 0 or count <= 0 or total <= count:
        return list(range(max(total, 0)))
    return sorted({int(round(v)) for v in np.linspace(0, total - 1, count)})


def _summarise_registration(ds: list[int], maes: list[float], quals: list[float],
                            corrs: list[float], baseline: list[float],
                            mode: str) -> RegistrationReport:
    ds_arr = np.asarray(ds, dtype=np.int64)
    if mode == "clip_mode":
        values, counts = np.unique(ds_arr, return_counts=True)
        best = counts.max()
        chosen = int(values[counts == best][np.argmin(np.abs(values[counts == best]))])
    else:
        chosen = int(np.round(np.median(ds_arr)))
    mae_min = float(np.median(maes))
    quality = float(np.median(quals))
    return RegistrationReport(
        method="circular_translate",
        search_px=0, d_star=chosen, d_star_mode=mode, frames_used=len(ds),
        mae_min=mae_min, mae_second=mae_min * quality, quality=quality,
        correlation=float(np.median(corrs)),
        interior_baseline=float(np.mean(baseline)) if baseline else 0.0,
        verdict="ok",
    )


def _gate(reg: RegistrationReport, conf) -> str:
    """Decide whether the canvas assumption still holds.

    The rule lives in :func:`vrstudio.redraw.registration.gate_registration` so the
    still stitch step uses exactly the same threshold semantics.
    """
    return gate_registration(
        reg,
        float(conf.get("recompose.min_band_correlation", 0.60)),
        float(conf.get("recompose.warn_quality", 1.15)),
    )


def main(
    src: Path = typer.Option(..., "--in", exists=True, dir_okay=False,
                             help="video returned by the AIGC tool"),
    out: Path = typer.Option(..., "--out", help="final closed panorama, source width"),
    manifest: Path = typer.Option(..., "--manifest", exists=True, dir_okay=False,
                                  help="manifest written by `vrstudio pad`"),
    config: Path | None = typer.Option(None, "--config", help="TOML case config"),
    overrides: list[str] = typer.Option([], "--set",
                                        help="config override key=value, repeatable"),
    d_star: int | None = typer.Option(None, "--d-star",
                                      help="force the circular shift (columns); skips the "
                                           "automatic choice but still runs the gate pass"),
    force: bool = typer.Option(False, "--force",
                               help="render even if the geometry-premise gate fails "
                                    "(diagnosis only — the output will be geometrically wrong)"),
    report_path: Path | None = typer.Option(None, "--report",
                                            help="report path (default: alongside --out)"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="run the gate pass and print it, render nothing"),
    quiet: bool = typer.Option(False, "--quiet", help="suppress progress chatter"),
) -> None:
    """Slice the repainted canvas, register, blend, and write the closed panorama."""
    log = Log(quiet=quiet)
    try:
        conf = config_mod.load(config, overrides)
    except config_mod.ConfigError as exc:
        log.error(str(exc))
        raise typer.Exit(2)

    try:
        man = Manifest.load(manifest)
        plan = man.to_pad_plan()             # re-asserts the closure invariant
    except Exception as exc:                                  # noqa: BLE001
        log.error(f"manifest {manifest}: {exc}")
        raise typer.Exit(2)

    try:
        info = probe(src, conf)
    except Exception as exc:                                  # noqa: BLE001
        log.error(str(exc))
        raise typer.Exit(2)

    try:
        man.check_source_hash(man.source.path) if Path(man.source.path).is_file() else None
    except ValueError:                                        # source not next to us: not fatal
        pass

    # The canvas geometry is KNOWN -- (Wc, H), no vertical padding -- so restore to it
    # rather than deriving a height from the tool's returned aspect. Deriving it would
    # let the tool's aspect error leak into the deliverable: a 21:9 return at 2520x1080
    # implies H'=2002 and the output would come back 4000x2002 instead of 4000x2000,
    # i.e. the panorama's own geometry would depend on what resolution the tool chose.
    # Restoring to the known canvas keeps the output exactly W x H (the source's ERP
    # aspect) for any return size; a wrong returned aspect then shows up as a content
    # stretch plus the drift warning below, not as a wrong deliverable.
    canvas_size = plan.canvas_size
    canvas_h = plan.H
    implied_h = restore_height(info.size, plan.Wc)
    aspect_drift = 100.0 * (implied_h / plan.H - 1.0) if plan.H else 0.0
    if abs(aspect_drift) > float(conf.get("recompose.aspect_drift_warn_pct", 2.0)):
        log.warn(
            f"the tool's aspect differs from the canvas: {info.size[0]}x{info.size[1]} "
            f"(aspect {info.size[0] / info.size[1]:.4f}) vs canvas "
            f"{plan.canvas_size[0]}x{plan.canvas_size[1]} "
            f"(aspect {plan.canvas_size[0] / plan.canvas_size[1]:.4f}), "
            f"{aspect_drift:+.1f}% implied height. The output is still exactly "
            f"{plan.W}x{plan.H} (closure is horizontal), but the content is stretched; "
            f"run `vrstudio calib` to see whether the framing itself moved."
        )
    interp = str(conf.get("recompose.restore_interp", "lanczos4"))
    weight = str(conf.get("recompose.blend_weight", "smoothstep"))
    lock_px = lock_px_from_pct(plan.L, float(conf.get("recompose.endpoint_lock_pct", 0.0)))
    repair = str(conf.get("recompose.boundary_repair", "off"))
    repair_px = (max(8, int(round(plan.L * float(conf.get("recompose.boundary_repair_pct", 15.0)) / 100)))
                 if repair == "feather" else 0)
    spec = RecomposeSpec(W=plan.W, Wc=plan.Wc, P=plan.P, L=plan.L,
                         weight=weight, endpoint_lock_px=lock_px,
                         repair=repair, repair_px=repair_px)

    log.info("recompose plan")
    for line in [
        f"canvas restore {info.size[0]}x{info.size[1]} -> {canvas_size[0]}x{canvas_size[1]}"
        f"   ({interp}, aspect {info.size[0] / info.size[1]:.4f})",
        f"output         {plan.W}x{canvas_h}   regions G/R/B = "
        f"{plan.L} / {spec.R_width} / {plan.L}",
        f"wrap pair      canvas columns {plan.L - 1}, {plan.L}  (adjacent by construction)",
        f"blend          {weight}, endpoint lock {lock_px} columns"
        + (f", boundary repair {repair} T={repair_px}" if repair != "off" else ""),
    ]:
        print(f"    {line}")

    registration = str(conf.get("recompose.registration", "circular_translate"))
    mode = str(conf.get("recompose.d_star_mode", "clip_median"))
    search_px = int(round(plan.L * float(conf.get("recompose.search_pct", 25.0)) / 100.0))
    max_rows = int(conf.get("recompose.max_sample_rows", 256))

    reg = RegistrationReport(method=registration, search_px=search_px,
                             d_star=0, d_star_mode=mode, frames_used=0,
                             mae_min=float("nan"), mae_second=float("nan"),
                             quality=float("inf"), correlation=1.0,
                             interior_baseline=0.0, verdict="ok")
    per_frame_d: list[int] = []

    if registration != "none" and plan.L > 0:
        ds: list[int] = []
        maes: list[float] = []
        quals: list[float] = []
        corrs: list[float] = []
        base: list[float] = []
        meter = RateMeter(info.frames or None, log)
        gate_max = int(conf.get("recompose.gate_max_frames", 48))
        gate_total = info.frames or int(round(info.duration_s * info.fps_float))
        sampled = _sample_indices(gate_total, gate_max) if gate_total > 0 else []
        sample_set = set(sampled) if len(sampled) < gate_total else None
        log.step("gate pass: registration + canvas baseline")
        with FrameReader(info, conf) as reader:
            for index, frame in enumerate(reader):
                meter.tick()
                if sample_set is not None and index not in sample_set:
                    continue
                canvas = restore_canvas(frame, canvas_size, interp)
                G, R, B = slice_regions(canvas, spec)
                res = circular_translate(G, B, search_px, max_rows)
                ds.append(res.d_star)
                maes.append(res.mae_min)
                quals.append(res.quality)
                corrs.append(res.correlation)
                base.append(float(np.abs(np.diff(to_gray(R), axis=1)).mean()))
        if sample_set is not None:
            log.info(f"    gate analysed {len(ds)} of {gate_total} frames "
                     f"(recompose.gate_max_frames = {gate_max})")
        if ds:
            reg = _summarise_registration(ds, maes, quals, corrs, base, mode)
            reg = replace(reg, search_px=search_px)
            reg = replace(reg, verdict=_gate(reg, conf))
            log.info(f"    d* = {reg.d_star} ({mode}), band correlation "
                     f"{reg.correlation:.3f}, cost-curve quality {reg.quality:.2f}, "
                     f"structure residual {reg.mae_min:.3f} (canvas baseline "
                     f"{reg.interior_baseline:.3f})  -> {reg.verdict}")

    if d_star is not None:
        log.warn(f"--d-star {d_star} overrides the automatic choice ({reg.d_star})")
        reg = replace(reg, d_star=int(d_star))

    if reg.verdict == "fail" and not force:
        log.error(
            "geometry premise failed: the two redundant bands no longer correspond, so the "
            "regions are probably being sliced in the wrong place (the AIGC tool likely "
            "cropped or reframed the canvas). Refusing to render a geometrically wrong "
            "panorama. Run `vrstudio calib`, re-check pad.target_aspect, or pass --force to "
            "inspect the result anyway."
        )
        _write_report(report_path, out, man, manifest, info, canvas_size, interp, reg,
                      spec, repair_px, {}, 0, aspect_drift)
        raise typer.Exit(1)
    if reg.verdict == "warn":
        log.warn("registration is not decisive (flat cost curve); d* may be a coincidental match")

    if dry_run:
        raise typer.Exit(0)

    # ---- render -------------------------------------------------------
    spec_render = replace(spec, d_star=reg.d_star)
    alpha = float(conf.get("recompose.per_frame_ema_alpha", 0.05))
    ema = float(reg.d_star)
    acc = SeamAccumulator(plan.W)
    buffers = ComposeBuffers()
    meter = RateMeter(info.frames or None, log)
    log.step("render pass")
    try:
        with FrameWriter(out, (plan.W, canvas_h), info.fps, conf.video,
                         audio_from=src, config=conf) as writer:
            with FrameReader(info, conf) as reader:
                for frame in reader:
                    canvas = restore_canvas(frame, canvas_size, interp)
                    if mode == "per_frame" and registration != "none" and plan.L > 0:
                        G, _, B = slice_regions(canvas, spec)
                        d_now = circular_translate(G, B, search_px, max_rows).d_star
                        ema = alpha * d_now + (1.0 - alpha) * ema
                        d_use = int(round(ema))
                        per_frame_d.append(d_use)
                        u8 = recompose_u8(canvas, replace(spec_render, d_star=d_use),
                                          buffers)
                    else:
                        u8 = recompose_u8(canvas, spec_render, buffers)
                    acc.add(u8)
                    writer.write(u8)
                    meter.tick()
            written = writer.frames_written
    except Exception as exc:                                  # noqa: BLE001
        log.error(f"recompose failed: {exc}")
        raise typer.Exit(1)

    log.step(f"{out}  ({meter.summary()})")
    if info.frames and written != info.frames:
        log.warn(f"frame count changed: input {info.frames}, output {written}")

    metrics = acc.result(tuple(conf.get("verify.interior_band", (0.25, 0.75))))
    status, reasons = verdict(metrics, before=None,
                              max_wrap_percentile=float(conf.get("verify.max_wrap_percentile", 95)),
                              worst_interior_tol=float(conf.get("verify.worst_interior_tol", 1.0)))
    log.info(f"    wrap {metrics.wrap:.3f} at percentile {metrics.wrap_percentile:.1f}; "
             f"worst interior pair {metrics.interior_max:.3f} -> {status}")
    for reason in reasons:
        (log.warn if status == "warn" else log.error)(reason)

    _write_report(report_path, out, man, manifest, info, canvas_size, interp, reg,
                  spec, repair_px, metrics.as_dict(), written, aspect_drift)

    print()
    print(f"final    {out}   {plan.W}x{canvas_h}   {written} frames")
    if reg.verdict != "fail":
        print(f"wrap     {metrics.wrap:.3f} (percentile {metrics.wrap_percentile:.1f}); "
              f"interior worst {metrics.interior_max:.3f}")
    if plan.warnings:
        for warning in plan.warnings:
            print(f"WARN     {warning}")
    print(f"report   {report_path or out.with_name(out.stem + '_recompose.json')}")


def _write_report(report_path, out: Path, man: Manifest, manifest: Path, info,
                  canvas_size, interp, reg, spec, repair_px, verify: dict,
                  written: int, aspect_drift_pct: float = 0.0) -> None:
    path = report_path or out.with_name(out.stem + "_recompose.json")
    RecomposeReport(
        manifest_path=Path(manifest).name,
        input_path=Path(info.path).name,
        input_size=info.size,
        input_frames=info.frames,
        input_fps=info.fps,
        restored_canvas=canvas_size,
        restore_interp=interp,
        registration=reg,
        blend=BlendReport(weight=spec.weight, endpoint_lock_px=spec.endpoint_lock_px),
        repair=RepairReport(enabled=spec.repair == "feather", T=repair_px),
        aspect_drift_pct=aspect_drift_pct,
        verify=verify,
        output_path=str(out),
        output_frames=written,
        warnings=man.warnings,
    ).save(path)
