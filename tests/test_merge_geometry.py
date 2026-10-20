"""Merge geometry: uniform scale for equal aspects, loud refusal otherwise."""

from __future__ import annotations

import numpy as np
import pytest

from vrstudio.patch.composite import align_ai_clip, normalize_ai_frame


def _frame(width: int, height: int) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[height // 2, width // 2] = (0, 0, 255)
    return frame


def test_identity_keeps_every_pixel():
    frame = _frame(64, 36)
    out = normalize_ai_frame(frame, (64, 36))
    assert out is frame
    assert int(np.abs(out.astype(np.int16) - frame.astype(np.int16)).max()) == 0


def test_same_aspect_scales_uniformly_without_crop():
    frame = _frame(64, 36)
    out = normalize_ai_frame(frame, (128, 72))
    assert out.shape[:2] == (72, 128)
    # 中心标记仍在中心；同比例缩放没有裁切
    assert tuple(out[36, 64]) == (0, 0, 255)


def test_aspect_mismatch_is_refused_by_default():
    frame = _frame(64, 48)                 # 4:3
    with pytest.raises(ValueError, match="allow-crop"):
        normalize_ai_frame(frame, (64, 36))  # 16:9


def test_allow_crop_centre_crops_after_cover_scale():
    frame = np.arange(64 * 48 * 3, dtype=np.uint8).reshape(48, 64, 3)
    out = normalize_ai_frame(frame, (64, 36), allow_crop=True)
    assert out.shape[:2] == (36, 64)
    # 4:3 -> 16:9 是上下各裁 6 行（cover 后仍是 64 宽，裁高度）
    assert np.array_equal(out, frame[6:42])


def test_align_ai_clip_uses_the_same_rule():
    frames = [_frame(64, 48) for _ in range(2)]
    with pytest.raises(ValueError, match="allow-crop"):
        align_ai_clip(frames, (64, 36), 30.0, 30.0, 2)
    out = align_ai_clip(frames, (64, 36), 30.0, 30.0, 2, allow_crop=True)
    assert out.shape == (2, 36, 64, 3)
