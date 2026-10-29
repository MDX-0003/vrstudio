"""Local redraw: the ERP-space edit mask and the inner-only paste-back.

``patchimg`` generates on the *whole* padded 21:9 canvas, then keeps only the
selected region from the stitched result. Two properties matter:

* the mask sent to the model is built in ERP space and passed through the same
  ``pre_roll`` + ``pad_h`` chain as the image, so the black edit region lands on
  exactly the same pixels in the padded canvas;
* the paste-back writes only the inner rect's support and copies every other
  pixel from the source, so ``np.array_equal(out[~support], src[~support])`` is
  the acceptance test, not a tolerance.

The inner rect may cross the ERP seam. ``build_paste_map`` then falls back to a
full-width bounding box, and the support mask naturally consists of the two
halves on either side of the wrap.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..redraw.recompose_core import finalize
from .blend import multiband_blend
from .projection import build_paste_map
from .sidecar import InnerRect, Viewport


class LocalMaskError(ValueError):
    """The inner rect cannot be projected onto the ERP image."""


@dataclass(frozen=True)
class LocalMask:
    erp_mask: np.ndarray                 # (H, W) uint8: 255 keep, 0 edit
    support: np.ndarray                  # bbox-shaped bool: the inner rect's pixels
    bbox: tuple[int, int, int, int]      # x0, y0, x1, y1 in ERP columns/rows


@dataclass(frozen=True)
class LocalComposite:
    image: np.ndarray
    maxdiff_outside: int
    changed_inside: int
    support_pixels: int


def local_erp_mask(viewport: Viewport, inner: InnerRect,
                   erp_w: int, erp_h: int) -> LocalMask:
    """Project the viewport's inner rect onto ERP space.

    Returns the ERP mask (255 everywhere except the inner region), the
    bbox-shaped support and that bbox. Raises when the rect is empty, outside
    the viewport, or projects onto no visible ERP pixel.
    """
    if inner.width <= 0 or inner.height <= 0:
        raise LocalMaskError(f"inner rect is empty: {inner.width}x{inner.height}")
    if (inner.x < 0 or inner.y < 0
            or inner.x + inner.width > viewport.width
            or inner.y + inner.height > viewport.height):
        raise LocalMaskError(
            f"inner rect {inner.x},{inner.y},{inner.width},{inner.height} is outside "
            f"the {viewport.width}x{viewport.height} viewport")

    u, v, cover, x0, y0 = build_paste_map(
        viewport, viewport.width, viewport.height, erp_w, erp_h)
    inside = (cover
              & (u >= inner.x) & (u < inner.x + inner.width)
              & (v >= inner.y) & (v < inner.y + inner.height))
    if not inside.any():
        raise LocalMaskError("the inner rect does not project onto any visible ERP pixel")

    erp_mask = np.full((erp_h, erp_w), 255, np.uint8)
    x1, y1 = x0 + u.shape[1], y0 + v.shape[0]
    erp_mask[y0:y1, x0:x1][inside] = 0
    return LocalMask(erp_mask=erp_mask, support=inside, bbox=(x0, y0, x1, y1))


def composite_local(source_erp: np.ndarray, stitched_erp: np.ndarray,
                    local: LocalMask, *, feather_px: int = 16,
                    levels: int = 5) -> LocalComposite:
    """Blend the stitched result into the source inside the inner rect only.

    The feather is inward-only: the mask is clamped to the hard support, so the
    transition lives inside the edit region and every pixel outside it is copied
    from the source verbatim.
    """
    if source_erp.shape != stitched_erp.shape:
        raise LocalMaskError(
            f"source {source_erp.shape} and stitched {stitched_erp.shape} differ")
    x0, y0, x1, y1 = local.bbox
    support = local.support
    src = source_erp[y0:y1, x0:x1]
    gen = stitched_erp[y0:y1, x0:x1]

    if feather_px > 0:
        k = int(round(feather_px)) * 2 + 1
        mask = cv2.GaussianBlur(support.astype(np.float32), (k, k), 0)
        mask = np.clip(mask, 0.0, 1.0) * support
    else:
        mask = support.astype(np.float32)

    blended = finalize(multiband_blend(src, gen, mask, levels))
    out = source_erp.copy()
    view = out[y0:y1, x0:x1]
    view[support] = blended[support]

    diff = np.abs(out.astype(np.int16) - source_erp.astype(np.int16))
    outside = np.ones(source_erp.shape[:2], dtype=bool)
    outside[y0:y1, x0:x1] = ~support
    maxdiff_outside = int(diff[outside].max()) if outside.any() else 0
    changed_inside = int((diff[y0:y1, x0:x1][support] > 0).sum())
    return LocalComposite(
        image=out,
        maxdiff_outside=maxdiff_outside,
        changed_inside=changed_inside,
        support_pixels=int(support.sum()),
    )
