"""Region slicing, the blue/green blend, and the optional boundary repairs.

This module is the whole geometric payload of the recompose step, in float, with
no I/O. It is also where the closure invariant is *realised*:

    out[0]  = R[0]      -> canvas column L   (= 2P)
    out[-1] = block[-1] -> canvas column L-1 (= 2P-1, via the endpoint lock)
    and canvas columns 2P-1 and 2P are adjacent

So the output's wrap pair is an adjacent column pair *of the model's own
output*. That is what lets the seam close without the model being consistent
over a full revolution of raster distance.

Float discipline: the blend is computed in float64 and rounded exactly once, in
``finalize``. Rounding mid-chain would quantise the smoothstep into visible steps
and would break the bit-exactness ``tests/test_roundtrip.py`` asserts.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .blend import blend_block, blend_into

RepairKind = str  # "off" | "feather"


class ComposeBuffers:
    """Scratch arrays for one streaming pass.

    The video loop processes thousands of frames at 4K/8K, and allocating the
    float64 temporaries per frame costs more in page faults than the arithmetic
    they serve (measured: 8K rounding 160 ms fresh vs 28 ms reused). One instance
    is created per pass and passed explicitly; nothing is cached at module level,
    so two concurrent passes cannot share state.
    """

    def __init__(self) -> None:
        self._arrays: dict[str, np.ndarray] = {}

    def take(self, name: str, shape: tuple[int, ...], dtype) -> np.ndarray:
        """Return a buffer for ``name``, allocating it on first use or shape change."""
        current = self._arrays.get(name)
        if current is None or current.shape != shape or current.dtype != dtype:
            current = np.empty(shape, dtype)
            self._arrays[name] = current
        return current


@dataclass(frozen=True)
class RecomposeSpec:
    """Geometry + parameters for one recompose, all in canvas columns."""

    W: int                 # source width == output width
    Wc: int                # canvas width
    P: int                 # per-side redundancy
    L: int                 # band width == 2P
    d_star: int = 0
    weight: str = "smoothstep"
    endpoint_lock_px: int = 0
    repair: str = "off"
    repair_px: int = 0

    @property
    def R_width(self) -> int:
        return self.W - self.L


def slice_regions(canvas: np.ndarray, spec: RecomposeSpec):
    """``G`` (left band), ``R`` (main content), ``B`` (right band)."""
    if canvas.shape[1] != spec.Wc:
        raise ValueError(f"canvas has {canvas.shape[1]} columns, manifest says {spec.Wc}")
    G = canvas[:, 0 : spec.L]
    R = canvas[:, spec.L : spec.W]
    B = canvas[:, spec.W : spec.Wc]
    if R.shape[1] != spec.R_width or B.shape[1] != spec.L:
        raise ValueError("region widths disagree with the spec")
    return G, R, B


def _resample_x(arr: np.ndarray, new_w: int) -> np.ndarray:
    h = arr.shape[0]
    out = cv2.resize(arr.astype(np.float32), (int(new_w), h), interpolation=cv2.INTER_LINEAR)
    return out.astype(np.float64)


def _ramp(n: int) -> np.ndarray:
    return (np.arange(n, dtype=np.float64) + 0.5) / max(n, 1)


def _repair(out: np.ndarray, spec: RecomposeSpec) -> np.ndarray:
    """Optional red/blue and green/red feathering, for when the tool is not locally smooth.

    Both repairs resample the two ``T``-wide ends to ``2T`` and cross-fade, so no
    single pixel column carries the whole transition. Off by default: when the
    tool behaves, the red/blue and green/red junctions are already adjacent
    canvas columns and feathering them only adds ghosting.
    """
    T = int(spec.repair_px)
    W, L, Rw = spec.W, spec.L, spec.R_width
    if T <= 0 or 2 * T > min(Rw, L):
        return out

    ramp = _ramp(2 * T)
    if out.ndim == 3:
        ramp = ramp[None, :, None]
    else:
        ramp = ramp[None, :]

    # red -> blue: R's tail against the block's head
    cross = _resample_x(out[:, Rw - T : Rw], 2 * T) * (1 - ramp) + \
            _resample_x(out[:, Rw : Rw + T], 2 * T) * ramp
    out = out.copy()
    out[:, Rw - T : Rw] = cross[:, :T]
    out[:, Rw : Rw + T] = cross[:, T:]

    # green -> red wraparound: block's tail against R's head
    cross = _resample_x(out[:, W - T : W], 2 * T) * (1 - ramp) + \
            _resample_x(out[:, :T], 2 * T) * ramp
    out[:, W - T : W] = cross[:, :T]
    out[:, :T] = cross[:, T:]
    return out


def recompose(canvas: np.ndarray, spec: RecomposeSpec) -> np.ndarray:
    """Canvas -> final frame of width ``W``, in float64."""
    G, R, B = slice_regions(canvas, spec)
    block = blend_block(G, B, spec.d_star, spec.weight, spec.endpoint_lock_px)
    out = np.concatenate([R.astype(np.float64), block], axis=1)
    if out.shape[1] != spec.W:
        raise ValueError(f"recomposed width {out.shape[1]} != W {spec.W}")
    if spec.repair == "feather":
        out = _repair(out, spec)
    return out


def finalize(out: np.ndarray) -> np.ndarray:
    """The single quantisation step: float64 -> uint8."""
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def recompose_u8(
    canvas: np.ndarray,
    spec: RecomposeSpec,
    buffers: ComposeBuffers | None = None,
) -> np.ndarray:
    """``finalize(recompose(canvas, spec))`` without materialising ``R`` in float64.

    ``R`` is copied straight from the canvas and only the blend band is rounded,
    which is what makes the video pass cheap: the region is already uint8 and its
    float64 round trip is an identity. The blend itself keeps the float64
    expression from :func:`blend_block`, so the output is bit-identical to the
    float path. ``buffers`` is reused across frames; without it a fresh set is
    allocated, which is correct but pays the allocation cost.
    """
    if spec.repair == "feather":
        # Repair reads both ends of the assembled frame in float64. It is off by
        # default and rare; keeping one implementation is worth more than the
        # allocation saved here.
        return finalize(recompose(canvas, spec))

    G, R, B = slice_regions(canvas, spec)
    buf = buffers if buffers is not None else ComposeBuffers()
    band = buf.take("band", B.shape, np.float64)
    temp = buf.take("temp", B.shape, np.float64)
    shifted = buf.take("shifted", B.shape, G.dtype) if spec.d_star else None
    blend_into(G, B, spec.d_star, spec.weight, spec.endpoint_lock_px,
               band, temp, shifted)

    np.rint(band, out=band)
    np.clip(band, 0, 255, out=band)
    band_u8 = buf.take("band_u8", B.shape, np.uint8)
    band_u8[...] = band

    out_shape = (B.shape[0], spec.W) + B.shape[2:]
    out = buf.take("frame_u8", out_shape, np.uint8)
    out[:, : spec.R_width] = R
    out[:, spec.R_width :] = band_u8
    if out.shape[1] != spec.W:
        raise ValueError(f"recomposed width {out.shape[1]} != W {spec.W}")
    return out


#: Named interpolations for the canvas restore, so config carries a word not an int.
INTERPOLATIONS: dict[str, int] = {
    "nearest": cv2.INTER_NEAREST,
    "linear": cv2.INTER_LINEAR,
    "cubic": cv2.INTER_CUBIC,
    "area": cv2.INTER_AREA,
    "lanczos4": cv2.INTER_LANCZOS4,
}


def restore_canvas(frame: np.ndarray, canvas_size: tuple[int, int],
                   interp: str = "lanczos4") -> np.ndarray:
    """Map a generated frame into canvas coordinates: force the width, keep the aspect.

    The AIGC tool is free to return any resolution; only its aspect is trusted.
    Restoring to the canvas width is what makes the region boundaries land on
    integer columns — the sole reason this step exists. It changes no logical
    proportion, so it neither adds nor removes content.
    """
    if interp not in INTERPOLATIONS:
        raise ValueError(f"unknown interpolation {interp!r}; want one of {tuple(INTERPOLATIONS)}")
    Wc, Hc = int(canvas_size[0]), int(canvas_size[1])
    if frame.shape[1] == Wc and frame.shape[0] == Hc:
        return frame
    return cv2.resize(frame, (Wc, Hc), interpolation=INTERPOLATIONS[interp])
