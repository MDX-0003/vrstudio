"""Padding: the identity that everything downstream depends on."""

from __future__ import annotations

import numpy as np
import pytest

from vrstudio.redraw.padding import canvas_column_index, pad_horizontal


@pytest.mark.parametrize("width,pad", [(384, 32), (384, 384), (384, 500)])
def test_padding_identity_holds(width, pad):
    """canvas[:, j] == src[(j - pad) mod W] — including pad >= W (multi-revolution)."""
    frame = np.arange(width, dtype=np.uint8)[None, :]
    canvas = pad_horizontal(frame, pad)
    assert canvas.shape[1] == width + 2 * pad
    idx = canvas_column_index(width, pad)
    assert np.array_equal(canvas, frame[:, idx])
    for j in range(0, canvas.shape[1], 7):
        assert canvas[0, j] == frame[0, (j - pad) % width]


def test_zero_pad_is_a_no_op():
    frame = np.zeros((4, 10, 3), np.uint8)
    assert pad_horizontal(frame, 0) is frame


def test_edges_are_continuous():
    """Both internal seams of the canvas are natural adjacencies of the source."""
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (8, 384, 3), dtype=np.uint8)
    pad = 32
    canvas = pad_horizontal(frame, pad)
    W = 384
    left_seam = np.abs(canvas[:, pad].astype(int) - canvas[:, pad - 1].astype(int)).mean()
    right_seam = np.abs(canvas[:, pad + W].astype(int) - canvas[:, pad + W - 1].astype(int)).mean()
    interior = np.abs(np.diff(frame.astype(int), axis=1)).mean()
    assert left_seam == pytest.approx(interior, rel=0.6, abs=6)
    assert right_seam == pytest.approx(interior, rel=0.6, abs=6)


def test_the_two_bands_are_pixel_identical():
    """G and B must be two renderings of the SAME content — the closure proof needs it."""
    rng = np.random.default_rng(1)
    frame = rng.integers(0, 255, (8, 384, 3), dtype=np.uint8)
    pad = 32
    canvas = pad_horizontal(frame, pad)
    L = 2 * pad
    G = canvas[:, :L]
    B = canvas[:, 384 : 384 + L]
    assert G.shape == B.shape
    assert np.array_equal(G, B)


def test_band_content_straddles_the_wrap():
    """Each band is [last P of source] + [first P of source]."""
    width = 200
    frame = np.arange(width, dtype=np.uint8)[None, :, None]      # (1, width, 1)
    pad = 16
    canvas = pad_horizontal(frame, pad)
    band = canvas[0, : 2 * pad, 0]
    assert np.array_equal(band[:pad], np.arange(width - pad, width, dtype=np.uint8))
    assert np.array_equal(band[pad:], np.arange(0, pad, dtype=np.uint8))
