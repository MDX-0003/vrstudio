"""Image pad/stitch: the single-frame form of the fullredraw auxiliary.

The geometry is not re-derived here. ``plan_image`` wraps
:func:`vrstudio.redraw.geometry.plan_pad`; ``pad_image`` runs the same
``pre_roll`` + ``pad_h`` chain as ``vrstudio pad``; ``stitch_image`` runs the same
``restore_canvas`` -> ``circular_translate`` -> ``recompose`` -> ``finalize``
sequence as ``vrstudio recompose``. Only the I/O and the manifest differ, which is
why a still image does not need a second implementation of the seam arithmetic.

Two image-specific decisions:

* ``P == 0`` is an error, not a warning. The image flows exist to close a 360
  seam; a canvas with no redundancy cannot do that and should fail before spending
  a generation call.
* The stitched image is the *whole* 2:1 panorama. For a local redraw the caller
  composites only the selected region afterwards; keeping this module free of
  viewport concepts lets ``img2video`` reuse it for full keyframes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction

import numpy as np

from .blend import endpoint_lock_px
from .geometry import (PadPlan, PadSpec, parse_aspect, plan_pad, resolve_aspect,
                       restore_height)
from .manifest import ImageManifest, LocalInfo, from_image_plan
from .mask import make_mask
from .pipeline import TransformContext, apply_chain, build_chain
from .recompose_core import (RecomposeSpec, finalize, recompose, restore_canvas,
                             slice_regions)
from .registration import (RegistrationResult, circular_translate,
                           gate_registration)
from .verify_core import SeamMetrics, seam_metrics, verdict


class ImageFlowError(ValueError):
    """A pad/stitch input cannot satisfy the geometry or the gate.

    Named apart from ``media.imageio.ImageError``（读图失败），because ``extract``
    and ``merge`` need both in one ``except`` clause.
    """


@dataclass(frozen=True)
class ImagePad:
    padded: np.ndarray
    mask: np.ndarray
    manifest: ImageManifest


@dataclass(frozen=True)
class ImageStitch:
    image: np.ndarray
    registration: RegistrationResult
    gate: str                    # the canvas-premise gate: ok | warn | fail
    metrics: SeamMetrics
    verdict: str                 # the seam verdict: ok | warn | bad
    reasons: tuple[str, ...]
    aspect_drift_pct: float


def plan_image(
    size: tuple[int, int],
    *,
    target_aspect: str | Fraction = "21:9",
    even_dimensions: bool = True,
    on_aspect_conflict: str = "error",
    pre_roll: str | int = "auto",
) -> PadPlan:
    """Plan the 21:9 canvas for one ERP image (same arithmetic as ``vrstudio pad``)."""
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        raise ImageFlowError(f"bad image size {width}x{height}")
    if isinstance(target_aspect, Fraction):
        aspect = target_aspect
    elif str(target_aspect).strip().lower() == "auto":
        aspect = resolve_aspect("auto", (width, height))
    else:
        aspect = parse_aspect(target_aspect)
    return plan_pad(
        PadSpec((width, height), aspect, even_dimensions=even_dimensions),
        on_aspect_conflict=on_aspect_conflict,
        pre_roll=pre_roll,
    )


def pad_image(
    image: np.ndarray,
    plan: PadPlan,
    *,
    source_path,
    pipeline: tuple[str, ...] | list[str] = ("pre_roll", "pad_h"),
    mask: np.ndarray | None = None,
    mask_path: str | None = None,
    mask_strategy: str = "redundant_white",
    mask_feather_px: int = 0,
    mask_png_compression: int = 0,
    restore_interp: str = "lanczos4",
    local: LocalInfo | None = None,
    ratio_label: str = "",
    tool_version: str = "",
    write_hash: bool = True,
) -> ImagePad:
    """Apply the pad chain to one image and build its ``image_pad.json``.

    ``mask`` lets the caller supply a local-edit mask (``patchimg``); when omitted
    the standard band mask is built with ``mask_strategy`` (``img2video``).
    """
    if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        raise ImageFlowError(
            f"expected uint8 BGR (H, W, 3), got {image.dtype} {image.shape}")
    expected_w, expected_h = plan.source_size
    if image.shape[1] != expected_w or image.shape[0] != expected_h:
        # plan_pad trims odd dimensions for yuv420p; crop the same way it planned.
        if image.shape[1] < expected_w or image.shape[0] < expected_h:
            raise ImageFlowError(
                f"image {image.shape[1]}x{image.shape[0]} is smaller than the plan's "
                f"source {expected_w}x{expected_h}")
        image = image[:expected_h, :expected_w]

    chain = build_chain(list(pipeline))
    padded = apply_chain(image, chain, TransformContext(plan=plan))
    if padded.shape[:2] != (plan.H, plan.Wc):
        raise ImageFlowError(
            f"padded image is {padded.shape[1]}x{padded.shape[0]}, "
            f"plan says {plan.Wc}x{plan.H}")

    if mask is None:
        mask = make_mask(plan.H, plan.Wc, plan.L, strategy=mask_strategy,
                         feather_px=mask_feather_px)
    mask = np.asarray(mask)
    if mask.shape != (plan.H, plan.Wc):
        raise ImageFlowError(
            f"mask is {mask.shape[1]}x{mask.shape[0]}, canvas says {plan.Wc}x{plan.H}")
    if mask.dtype != np.uint8:
        mask = np.clip(np.rint(mask), 0, 255).astype(np.uint8)

    manifest = from_image_plan(
        plan,
        source_path=source_path,
        write_hash=write_hash,
        mask_path=mask_path,
        mask_strategy=mask_strategy,
        mask_feather_px=mask_feather_px,
        mask_png_compression=mask_png_compression,
        restore_interp=restore_interp,
        pipeline=list(pipeline),
        local=local,
        ratio_label=ratio_label,
        tool_version=tool_version,
    )
    return ImagePad(padded=padded, mask=mask, manifest=manifest)


def map_erp_mask_to_canvas(
    erp_mask: np.ndarray,
    plan: PadPlan,
    pipeline: tuple[str, ...] | list[str] = ("pre_roll", "pad_h"),
) -> np.ndarray:
    """Map an ERP-space mask through the same chain as the image.

    ``patchimg`` builds its local edit mask in ERP space; running it through
    ``pre_roll`` + ``pad_h`` guarantees the black region lands on exactly the
    same canvas pixels as the padded image, including the wrap and the roll.
    """
    if erp_mask.ndim != 2 or erp_mask.shape != (plan.H, plan.W):
        raise ImageFlowError(
            f"ERP mask is {erp_mask.shape}, plan source is {(plan.H, plan.W)}")
    chain = build_chain(list(pipeline))
    mapped = apply_chain(np.ascontiguousarray(erp_mask), chain,
                         TransformContext(plan=plan))
    if mapped.shape != (plan.H, plan.Wc):
        raise ImageFlowError(
            f"canvas mask is {mapped.shape}, canvas is {(plan.H, plan.Wc)}")
    return np.ascontiguousarray(mapped)


def stitch_image(
    generated: np.ndarray,
    manifest: ImageManifest,
    *,
    search_pct: float = 25.0,
    max_sample_rows: int = 256,
    min_band_correlation: float = 0.80,
    warn_quality: float = 1.15,
    restore_interp: str = "lanczos4",
    blend_weight: str = "smoothstep",
    endpoint_lock_pct: float = 2.0,
    repair: str = "off",
    repair_px: int = 0,
    interior_band: tuple[float, float] = (0.25, 0.75),
    max_wrap_percentile: float = 95.0,
    worst_interior_tol: float = 1.0,
) -> ImageStitch:
    """Turn one generated 21:9 canvas into a closed 2:1 panorama.

    Raises :class:`ImageFlowError` when the model reframed the canvas (band
    correlation below the threshold) or the manifest has no redundancy. A flat
    cost curve is only a warning: ``d*`` may be a coincidental match.
    """
    if generated.ndim != 3 or generated.shape[2] != 3:
        raise ImageFlowError(
            f"expected an (H, W, 3) generated image, got {generated.shape}")
    plan = manifest.to_pad_plan()
    if plan.P <= 0:
        raise ImageFlowError(
            "P == 0: the canvas has no horizontal redundancy, so the 360 seam "
            "cannot be closed; use a target aspect wider than the source (21:9)")

    canvas = restore_canvas(generated, plan.canvas_size, restore_interp)
    spec = RecomposeSpec(
        W=plan.W, Wc=plan.Wc, P=plan.P, L=plan.L, d_star=0,
        weight=blend_weight,
        endpoint_lock_px=endpoint_lock_px(plan.L, endpoint_lock_pct),
        repair=repair,
        repair_px=int(repair_px),
    )
    G, _, B = slice_regions(canvas, spec)
    search_px = int(round(plan.L * float(search_pct) / 100.0))
    reg = circular_translate(G, B, search_px, max_sample_rows)
    gate = gate_registration(reg, min_band_correlation, warn_quality)
    if gate == "fail":
        raise ImageFlowError(
            f"canvas premise failed: the two redundant bands correlate at "
            f"{reg.correlation:.3f} (< {min_band_correlation:.2f}); the model "
            f"likely cropped or reframed the canvas, so slicing would put the seam "
            f"in the wrong place")

    out = finalize(recompose(canvas, replace(spec, d_star=reg.d_star)))
    metrics = seam_metrics(out, interior_band)
    status, reasons = verdict(metrics, None, max_wrap_percentile,
                              worst_interior_tol)
    implied_h = restore_height((generated.shape[1], generated.shape[0]), plan.Wc)
    drift = 100.0 * (implied_h / plan.H - 1.0) if plan.H else 0.0
    return ImageStitch(
        image=out,
        registration=reg,
        gate=gate,
        metrics=metrics,
        verdict=status,
        reasons=tuple(reasons),
        aspect_drift_pct=drift,
    )
