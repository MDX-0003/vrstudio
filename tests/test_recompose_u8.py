"""Byte-exactness of the streaming compose path, and the gate sampler.

The video pass stopped materialising ``R`` in float64 and stopped allocating its
temporaries per frame. Both are performance changes only: ``recompose_u8`` must
produce exactly what ``finalize(recompose(...))`` produced before. These tests
pin that, including buffer reuse across frames and the rare repair fallback.
"""

from __future__ import annotations

import numpy as np

from vrstudio.cli.recompose import _choose_shift, _sample_indices
from vrstudio.redraw.recompose_core import (ComposeBuffers, RecomposeSpec, finalize,
                                            recompose, recompose_u8)
from vrstudio.redraw.report import RegistrationReport


def _reg(**overrides) -> RegistrationReport:
    base = dict(method="circular_translate", search_px=78, d_star=10,
                d_star_mode="clip_median", frames_used=48, mae_min=9.0,
                mae_second=9.7, quality=1.07, correlation=0.927,
                correlation_zero=0.869, interior_baseline=1.9, verdict="warn")
    base.update(overrides)
    return RegistrationReport(**base)


def test_choose_shift_keeps_a_flat_curve_shift_when_the_correlation_agrees():
    # measured on the 110601 v3 return: 0.869 unshifted -> 0.927 at 10 px
    assert _choose_shift(_reg(), 314) == 10
    # no correlation gain: the flat MAE curve wins, blend at 0
    assert _choose_shift(_reg(correlation_zero=0.92), 314) == 0
    # a shift that only hit the search bound is not a measurement
    assert _choose_shift(_reg(d_star=78), 314) == 0
    # a decisive MAE curve keeps its own choice regardless of the correlation
    assert _choose_shift(_reg(verdict="ok", correlation_zero=0.9), 314) == 10
    assert _choose_shift(_reg(d_star=0), 314) == 0


def test_choose_shift_rejects_a_shift_too_wide_for_the_band():
    # 65 px in a 240 px band: absorbing it is a 27% squeeze, itself the defect
    assert _choose_shift(_reg(d_star=65, search_px=120, correlation=0.756,
                              correlation_zero=0.321), 240) == 0
    assert _choose_shift(_reg(d_star=59, search_px=120, correlation=0.80,
                              correlation_zero=0.40), 240) == 59


def _spec(**overrides) -> RecomposeSpec:
    base = dict(W=40, Wc=48, P=4, L=8, endpoint_lock_px=2)
    base.update(overrides)
    return RecomposeSpec(**base)


def _canvas(shape: tuple[int, int, int] = (7, 48, 3), seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, shape, dtype=np.uint8)


def test_recompose_u8_matches_the_float_path():
    canvas = _canvas()
    for d_star in (0, 3, -3):
        for lock in (0, 2):
            for ramp in (0, 3):
                spec = _spec(d_star=d_star, endpoint_lock_px=lock, shift_ramp_px=ramp)
                want = finalize(recompose(canvas, spec))
                got = recompose_u8(canvas, spec)
                assert got.dtype == np.uint8
                assert got.shape == want.shape
                assert np.array_equal(got, want)


def test_buffers_are_reused_without_leaking_between_frames():
    buffers = ComposeBuffers()
    spec = _spec(d_star=2)
    first, second = _canvas(seed=1), _canvas(seed=2)
    assert np.array_equal(recompose_u8(first, spec, buffers),
                          finalize(recompose(first, spec)))
    assert np.array_equal(recompose_u8(second, spec, buffers),
                          finalize(recompose(second, spec)))

    # a different geometry must replace the buffers, not write out of bounds
    other = _spec(W=24, Wc=32, L=8, d_star=1)
    small = _canvas(shape=(5, 32, 3), seed=3)
    assert np.array_equal(recompose_u8(small, other, buffers),
                          finalize(recompose(small, other)))


def test_repair_falls_back_to_the_float_path():
    canvas = _canvas()
    spec = _spec(repair="feather", repair_px=2)
    assert np.array_equal(recompose_u8(canvas, spec),
                          finalize(recompose(canvas, spec)))


def test_sample_indices_are_even_and_unique():
    indices = _sample_indices(570, 48)
    assert len(indices) == 48
    assert indices == sorted(set(indices))
    assert indices[0] == 0 and indices[-1] == 569
    gaps = np.diff(indices)
    assert gaps.max() - gaps.min() <= 1


def test_sample_indices_keep_short_clips_whole():
    assert _sample_indices(10, 48) == list(range(10))
    assert _sample_indices(48, 48) == list(range(48))
    assert _sample_indices(0, 48) == []
