"""True circular horizontal padding — the only horizontal mode this tool has.

The rest of the pipeline depends on the identity ``canvas[:, j] == source[(j-P) mod W]``
(see ``geometry`` module docstring). That identity is what makes ``G`` and ``B``
two renderings of the *same* content, which is what makes the blend's endpoints
adjacent canvas columns, which is what closes the wrap.

``mirror`` / ``replicate`` / ``constant`` padding each break that identity: they
fill the band with content that is not the panorama's continuation, so the two
bands no longer correspond and every downstream boundary condition is void. They
are deliberately not implemented rather than implemented-and-discouraged.
"""

from __future__ import annotations

import numpy as np


def canvas_column_index(width: int, pad: int) -> np.ndarray:
    """Column gather indices for ``canvas[:, j] == source[(j - pad) mod width]``.

    Handles ``pad > width`` (more than one revolution of redundancy) because the
    modulo is applied to the index, not to the output length.
    """
    j = np.arange(width + 2 * pad, dtype=np.int64)
    return (j - pad) % width


def pad_horizontal(frame: np.ndarray, pad: int) -> np.ndarray:
    """Wrap ``pad`` columns of each end onto the opposite side.

    ``pad == 0`` returns the frame unchanged (no copy) — the degenerate
    ``P == 0`` case that ``geometry.plan_pad`` warns about.
    """
    if pad <= 0:
        return frame
    idx = canvas_column_index(frame.shape[1], pad)
    return np.ascontiguousarray(np.take(frame, idx, axis=1))
