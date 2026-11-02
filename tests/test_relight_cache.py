"""重打光固定光照缓存（决策 64）：只存参考帧号与全精度相似变换。"""

from __future__ import annotations

import numpy as np

from vrstudio.relight.align import AlignResult
from vrstudio.relight.cache import (
    CACHE_VERSION,
    CacheKey,
    CachedFixed,
    cache_path,
    load,
    save,
)


def _align(dtype=np.float64) -> AlignResult:
    return AlignResult(
        matrix=np.array([[0.99, -0.01, 3.0], [0.01, 0.99, -2.0]], dtype=dtype),
        scale=0.99,
        angle_deg=0.5,
        tx=3.0,
        ty=-2.0,
        inliers=20,
        keypoints_gt=100,
        keypoints_atmo=120,
        matches=30,
        fallback=None,
        correlation_before=0.1,
        correlation_after=0.7,
    )


def _key(**overrides) -> CacheKey:
    values = {
        "gt_sha256": "gt-hash",
        "gt_size": 100,
        "gt_mtime_ns": 200,
        "atmo_sha256": "atmo-hash",
        "atmo_size": 300,
        "atmo_mtime_ns": 400,
        "ref_frame": "auto",
    }
    values.update(overrides)
    return CacheKey(**values)


def test_cache_path_is_a_derived_artifact(tmp_path):
    assert cache_path(tmp_path) == tmp_path / "derived" / "relight" / "fixed.json"


def test_roundtrip_preserves_the_full_precision_matrix(tmp_path):
    path = cache_path(tmp_path)
    alignment = _align()
    key = _key()
    assert save(path, key, CachedFixed(7, 0.1234, alignment))

    got = load(path, key)
    assert got is not None
    assert got.ref_frame == 7
    assert got.ref_matched == 0.1234
    assert got.alignment.matrix.dtype == alignment.matrix.dtype
    assert np.array_equal(got.alignment.matrix, alignment.matrix)
    assert got.alignment.scale == alignment.scale
    assert got.alignment.angle_deg == alignment.angle_deg
    assert got.alignment.inliers == alignment.inliers
    assert got.alignment.fallback is None
    assert got.alignment.correlation_after == alignment.correlation_after


def test_float32_matrix_keeps_its_dtype(tmp_path):
    path = cache_path(tmp_path)
    alignment = _align(np.float32)
    key = _key()
    assert save(path, key, CachedFixed(0, 0.5, alignment))

    got = load(path, key)
    assert got is not None
    assert got.alignment.matrix.dtype == np.float32


def test_any_changed_input_misses(tmp_path):
    path = cache_path(tmp_path)
    key = _key()
    assert save(path, key, CachedFixed(7, 0.1, _align()))

    assert load(path, _key(atmo_sha256="another")) is None
    assert load(path, _key(gt_size=101)) is None
    assert load(path, _key(ref_frame=7)) is None
    assert load(path, _key(version=CACHE_VERSION - 1)) is None


def test_corrupt_or_missing_file_misses(tmp_path):
    path = cache_path(tmp_path)
    key = _key()
    assert load(path, key) is None

    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    assert load(path, key) is None


def test_save_failure_returns_false_without_raising(tmp_path):
    blocker = tmp_path / "derived"
    blocker.write_text("not a directory", encoding="utf-8")
    assert not save(cache_path(tmp_path), _key(), CachedFixed(0, 0.0, _align()))
