"""Orientation pre-roll.

The identity-model round trip of this pipeline is ``out == roll(source, -P)``
(asserted in ``tests/test_roundtrip.py``). Left alone, the delivered panorama is
therefore the source rotated by ``P`` columns — 30 degrees for a 2:1 source at
21:9. If the upstream render has a defined front, that silently moves it.

Rolling by ``+P`` before padding cancels it exactly. It has to happen here and
not in ``recompose``: rolling the *output* would move the wrap pair off the
adjacent canvas columns the closure proof relies on (measured: rolling relocates
a seam without reducing it).
"""

from __future__ import annotations

import numpy as np


def pre_roll(frame: np.ndarray, amount: int) -> np.ndarray:
    """Circularly shift the frame by ``amount`` columns (positive shifts right).

    Integer columns only, so the operation is lossless — no resampling.
    """
    if amount == 0:
        return frame
    return np.ascontiguousarray(np.roll(frame, amount, axis=1))
