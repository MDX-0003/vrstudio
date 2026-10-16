"""``vrstudio pad`` — circular redundancy + mask + manifest, ready for the AIGC tool.

This is the only step that touches the source. It writes three things that must
travel together: the padded video and its mask go to the AIGC tool, the manifest
stays with the operator and is what ``recompose`` later reads. Losing the manifest
costs a whole AIGC round trip, which is why the geometry summary prints even
under ``--quiet``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import typer

from .. import __version__, config as config_mod
from ..redraw.geometry import GeometryError, PadSpec, PadPlan, plan_pad, resolve_aspect
from ..redraw.manifest import VideoSettings, from_plan
from ..redraw.mask import make_mask
from ..redraw.pipeline import TransformContext, apply_chain, build_chain
from ..progress import Log, RateMeter
from ..media.videoio import FrameReader, FrameWriter, probe



def derived_path(base: Path, template: str) -> Path:
    """``{stem}`` is the *output* stem, so --out names the whole artifact set."""
    return base.with_name(template.format(stem=base.stem))


def build_plan(info, conf) -> PadPlan:
    """Derive the plan from config + probed source. Shared with ``vrstudio plan``."""
    aspect = resolve_aspect(str(conf.get("pad.target_aspect")), info.size)
    return plan_pad(
        PadSpec(source_size=info.size, target_aspect=aspect,
                even_dimensions=bool(conf.get("pad.even_dimensions", True))),
        on_aspect_conflict=str(conf.get("pad.on_aspect_conflict", "error")),
        pre_roll=conf.get("pad.pre_roll", "auto"),
    )


def summary_lines(plan: PadPlan, info, out: Path, mask: Path | None, manifest: Path) -> list[str]:
    at = plan.target_aspect
    return [
        f"source        {plan.requested_source_size[0]}x{plan.requested_source_size[1]}"
        f"   {info.frames or '?'} frames @ {info.fps}   {info.pix_fmt}",
        f"canvas        {plan.canvas_size[0]}x{plan.canvas_size[1]}"
        f"   target aspect {at.numerator}:{at.denominator} = {float(at):.4f}"
        f"   (no vertical padding)",
        f"redundancy    P = {plan.P}/side   L = {plan.L} mask band"
        f"   ({100 * plan.P / plan.W:.2f}% of W)",
        f"regions       G={plan.regions['G']}  R={plan.regions['R']}  B={plan.regions['B']}",
        f"wrap pair     source columns {plan.wrap_pair}, "
        f"{plan.outer_boundary_margin_px} columns clear of the canvas raster boundary",
        f"orientation   pre_roll {plan.source_preroll:+d}"
        f"   (without it the identity round trip rotates by {plan.roll_delta:+d})",
        f"pixel budget  main content {100 * plan.content_pixel_fraction:.1f}% of canvas, "
        f"blend block {100 * plan.blend_fraction_of_output:.1f}% of output",
        f"artifacts     {out.name}"
        + (f"  +  {mask.name}" if mask else "")
        + f"  +  {manifest.name}",
    ]


def main(
    src: Path = typer.Option(..., "--in", exists=True, dir_okay=False,
                             help="source ERP video (the UE render)"),
    out: Path = typer.Option(..., "--out", help="padded video to hand to the AIGC tool"),
    config: Path | None = typer.Option(None, "--config", help="TOML case config"),
    overrides: list[str] = typer.Option([], "--set",
                                        help="config override key=value, repeatable"),
    dry_run: bool = typer.Option(False, "--dry-run", help="print the geometry and exit"),
    emit_manifest: Path | None = typer.Option(None, "--emit-manifest",
                                              help="manifest path (default: alongside --out)"),
    emit_mask: Path | None = typer.Option(None, "--emit-mask",
                                          help="mask PNG path (default: alongside --out)"),
    no_mask: bool = typer.Option(False, "--no-mask", help="skip the mask PNG"),
    quiet: bool = typer.Option(False, "--quiet", help="suppress progress chatter"),
) -> None:
    """Circularly pad a 360 ERP source to the target aspect; emit the mask + manifest."""
    log = Log(quiet=quiet)

    # config -- layered defaults; an unknown key aborts here rather than silently
    # doing nothing. From here on `conf` is read via .get("a.b.c", default).
    try:
        conf = config_mod.load(config, overrides)
    except config_mod.ConfigError as exc:
        log.error(str(exc))
        raise typer.Exit(2)

    # probe -- source dimensions/fps/frame count from ffprobe; no pixel decoded yet.
    try:
        info = probe(src, conf)
    except Exception as exc:                                  # noqa: BLE001
        log.error(str(exc))
        raise typer.Exit(2)

    # plan -- derive P/L/canvas from target_aspect alone, then assert closure.
    # This is the last stage that touches no pixels, and the one to get right.
    try:
        plan = build_plan(info, conf)
    except GeometryError as exc:
        log.error(str(exc))
        raise typer.Exit(2)

    # unpack -- read every setting the run needs, before any pixel is touched.
    transforms = list(conf.get("pad.pipeline.transforms"))
    mask_enabled = bool(conf.get("mask.enabled", True)) and not no_mask
    mask_strategy = str(conf.get("mask.strategy", "redundant_white"))
    mask_feather = int(conf.get("mask.feather_px", 0))
    mask_png_level = int(conf.get("mask.png_compression", 0))
    restore_interp = str(conf.get("recompose.restore_interp", "lanczos4"))

    # artifact paths -- all derived from --out, so --out names the whole set.
    manifest_path = emit_manifest or derived_path(out, str(conf.get("manifest.path_template")))
    mask_path = None
    if mask_enabled:
        mask_path = emit_mask or derived_path(out, str(conf.get("mask.path_template")))

    # the geometry prints even under --quiet: losing the manifest costs a whole
    # AIGC round trip, so these numbers must reach the console.
    print("pad plan")
    for line in summary_lines(plan, info, out, mask_path, manifest_path):
        print(f"    {line}")
    for warning in plan.warnings:
        log.warn(warning)

    # --dry-run stops here; everything below touches pixels.
    if dry_run:
        raise typer.Exit(0)

    if plan.P == 0:
        log.warn("P == 0 — no redundancy; the output's wrap will NOT be guaranteed to close")

    # chain -- the pad-side pluggable point: config names the steps, this order
    # is what builds them. Both steps are lossless integer-column operations.
    chain = build_chain(transforms)
    ctx = TransformContext(plan=plan)
    meter = RateMeter(info.frames or None, log)
    written = 0
    # the only pixel loop -- one frame resident at a time (8K x 600 frames ~ 62 GB
    # if held, which is why every stage above had to stay pure and plan-only).
    try:
        with FrameWriter(out, plan.canvas_size, info.fps, conf.video,
                         audio_from=src, config=conf) as writer:
            with FrameReader(info, conf) as reader:
                for frame in reader:
                    writer.write(apply_chain(frame, chain, ctx))
                    written += 1
                    meter.tick()
    except Exception as exc:                                  # noqa: BLE001
        log.error(f"pad failed: {exc}")
        raise typer.Exit(1)
    log.step(f"{out}  ({meter.summary()})")
    if info.frames and written != info.frames:
        log.warn(f"frame count changed: source {info.frames}, padded {written}")

    # mask -- a geometric record for the AIGC tool, not part of the closure chain:
    # recompose derives its regions from the manifest, never from this PNG.
    if mask_path is not None:
        mask = make_mask(plan.H, plan.Wc, plan.L, strategy=mask_strategy, feather_px=mask_feather)
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(mask_path), mask,
                           [cv2.IMWRITE_PNG_COMPRESSION, mask_png_level]):
            log.error(f"could not write mask {mask_path}")
            raise typer.Exit(1)
        log.step(f"{mask_path}  ({plan.Wc}x{plan.H}, band L={plan.L})")

    # manifest -- the pad/recompose contract; recompose later reads this, not the
    # video's own metadata. Losing it costs a whole AIGC round trip.
    manifest = from_plan(
        plan,
        source_path=src,
        source_frames=info.frames,
        source_fps=info.fps,
        source_pix_fmt=info.pix_fmt,
        has_audio=info.has_audio,
        padded_name=out.name,
        write_hash=bool(conf.get("manifest.write_hash", True)),
        mask_path=mask_path.name if mask_path else None,
        mask_strategy=mask_strategy,
        mask_feather_px=mask_feather,
        mask_png_compression=mask_png_level,
        restore_interp=restore_interp,
        video=VideoSettings(**dict(conf.video)),
        tool_version=__version__,
    )
    manifest.save(manifest_path)
    log.step(f"{manifest_path}")

    # handoff -- the JSON above is machine-readable; this is the human prompt hint.
    print(json.dumps({
        "canvas": list(plan.canvas_size), "P": plan.P, "L": plan.L,
        "regions": {k: list(v) for k, v in plan.regions.items()},
        "wrap_pair": list(plan.wrap_pair),
        "artifacts": {"video": str(out), "mask": str(mask_path) if mask_path else None,
                      "manifest": str(manifest_path)},
        "warnings": list(plan.warnings),
    }, indent=2, ensure_ascii=False))

    stream = sys.stdout if quiet else sys.stderr
    print(f"\nHand {out.name}"
          + (f" + {mask_path.name}" if mask_path else "")
          + " to the AIGC tool. Keep the manifest.\n"
          "Prompt hint: the two white bands are the same circular boundary's redundant\n"
          "content and must keep matching objects, texture, lighting and motion.",
          file=stream)
