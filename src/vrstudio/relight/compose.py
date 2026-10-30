"""合成：把光照场乘到 GT 上，以及高光软裁切。

公式（线性光，决策 48）：

```text
Ws       = (1 - strength) × I + strength × W
out_lin  = GT_lin × Ws × gY^strength × V^(local_shading × strength)
```

``strength=0`` 时 ``Ws`` 退化为单位矩阵、两个指数都是 0，输出与 GT 逐位一致；
这是验收基线，不是巧合。``local_shading`` 只作用在可见度项上：0 表示只用低频
光场，1 表示完全跟随 AI 画出的局部明暗。
"""

from __future__ import annotations

import cv2
import numpy as np

from .fields import LightFields, linear_to_srgb8, srgb_to_linear


def relight(gt8: np.ndarray, fields: LightFields,
            strength: float, local_shading: float) -> np.ndarray:
    """把 ``fields`` 乘到 GT 上，返回与 GT 同尺寸的 8-bit BGR 图。"""
    height, width = gt8.shape[:2]
    gain = cv2.resize(fields.gain, (width, height), interpolation=cv2.INTER_LINEAR)
    visibility = cv2.resize(fields.visibility, (width, height),
                            interpolation=cv2.INTER_LINEAR)
    gt_linear = srgb_to_linear(gt8).astype(np.float32)
    white_balance = ((1.0 - strength) * np.eye(3, dtype=np.float32)
                     + strength * fields.white_balance)
    out_linear = (np.einsum("...k,ck->...c", gt_linear, white_balance)
                  * (gain[..., None] ** strength)
                  * (visibility[..., None] ** (local_shading * strength)))
    return linear_to_srgb8(np.clip(out_linear, 0.0, 1.0))


def soft_clip(bgr8: np.ndarray, knee: float = 0.72, max_out: float = 0.98) -> np.ndarray:
    """高光软裁切：亮度过 ``knee`` 后按指数压向 ``max_out``，色相保持不变。"""
    x = np.clip(bgr8.astype(np.float32), 0, 255) / 255.0
    y = 0.0722 * x[..., 0] + 0.7152 * x[..., 1] + 0.2126 * x[..., 2]
    over = y > knee
    y_clipped = y.copy()
    span = max(max_out - knee, 1e-3)
    y_clipped[over] = max_out - span * np.exp(-(y[over] - knee) / span)
    scaled = x * (y_clipped / np.maximum(y, 1e-6))[..., None] * 255.0
    return np.clip(scaled, 0, 255).astype(np.uint8)


def clipped_fraction(bgr8: np.ndarray, threshold: int = 250) -> float:
    """任一通道达到 ``threshold`` 的像素比例——过曝的量度。"""
    return float((bgr8.max(axis=2) >= threshold).mean())
