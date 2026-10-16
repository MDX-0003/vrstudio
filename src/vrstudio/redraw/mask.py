"""Mask strategies.

The mask is a *geometric record* first and a model hint second. It is not part of
the closure chain: ``recompose`` derives every region from the manifest, never
from the mask, so a mask the model ignored or scribbled over cannot make the
geometry wrong.

``redundant_white`` (the default) marks the two ``L = 2P``-wide redundant bands
white and the main content black. Note the bands are ``2P`` wide, not ``P``:
each covers the outward circular copy *plus* the adjacent ``P`` columns of real
content, which is what lets the model see the complete circular neighbourhood.

Polarity is a genuine unknown — the target tool may read white as "keep" or as
"free to generate" — so the alternative polarities are one config line away and
should be settled by a single-frame A/B, not by argument.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

MaskStrategy = Literal["redundant_white", "content_white", "tri_band"]
STRATEGIES: tuple[str, ...] = ("redundant_white", "content_white", "tri_band")


def make_mask(
    height: int,
    canvas_width: int,
    band: int,
    strategy: MaskStrategy = "redundant_white",
    feather_px: int = 0,
    seam_px: int | None = None,
) -> np.ndarray:
    """Build the ``uint8`` mask for a canvas of ``canvas_width`` columns.

    ``band`` is ``L = 2P``. The main content occupies columns ``[L, canvas_width - L)``,
    whose width is ``W - 2P`` by construction.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown mask strategy {strategy!r}; want one of {STRATEGIES}")
    if band < 0 or 2 * band > canvas_width:
        raise ValueError(f"band {band} does not fit a canvas of {canvas_width}")

    main = canvas_width - band
    mask = np.zeros((height, canvas_width), np.uint8)

    if strategy == "content_white":
        mask[:, band:main] = 255
    elif strategy == "redundant_white":
        mask[:, :band] = 255
        mask[:, main:] = 255
    else:  # tri_band
        s = seam_px if seam_px is not None else max(1, band // 4)
        mask[:, :band] = 255
        mask[:, main:] = 255
        mask[:, band : band + s] = 128
        mask[:, main - s : main] = 128

    if feather_px > 0:
        # only for experiments: the contract wants a strictly binary mask
        mask = cv2.GaussianBlur(mask, (0, 0), feather_px / 2.0)
    return mask
