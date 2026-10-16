"""Shared fixtures.

The synthetic ERP is built from functions that are exactly periodic in x, so the
frame really is a closed panorama: column W-1 and column 0 are neighbours. That
matters — a fixture with an arbitrary wrap would make the round-trip assertions
meaningless, because the pipeline is supposed to preserve the wrap, not create it.
"""

from __future__ import annotations

import numpy as np
import pytest


def synthetic_erp(width: int = 384, height: int = 192, seed: int = 0) -> np.ndarray:
    """A closed ERP frame: every horizontal term has period ``width``."""
    rng = np.random.default_rng(seed)
    x = np.arange(width, dtype=np.float64)[None, :]
    y = np.arange(height, dtype=np.float64)[:, None]

    base = (
        128.0
        + 55.0 * np.sin(2 * np.pi * 3 * x / width)
        + 35.0 * np.cos(2 * np.pi * 7 * x / width)
        + 25.0 * np.sin(2 * np.pi * 13 * x / width + 0.7)
        + 18.0 * np.sin(2 * np.pi * (5 * x + 0.35 * y) / width)
        + 20.0 * np.cos(2 * np.pi * y / height)
    )
    base = base + rng.normal(0.0, 1.5, base.shape)
    frame = np.clip(base, 0, 255).astype(np.uint8)
    return np.repeat(frame[:, :, None], 3, axis=2)


@pytest.fixture
def erp() -> np.ndarray:
    return synthetic_erp()


@pytest.fixture
def small_erp() -> np.ndarray:
    return synthetic_erp(width=160, height=80, seed=7)
