"""合成：把光照场乘到 GT 上，以及高光软裁切。

公式（线性光，决策 48）：

```text
Ws       = (1 - strength) × I + strength × W
out_lin  = GT_lin × Ws × gY^strength × V^(local_shading × strength)
```

``strength=0`` 时 ``Ws`` 退化为单位矩阵、两个指数都是 0，输出与 GT 逐位一致；
这是验收基线，不是巧合。``local_shading`` 只作用在可见度项上：0 表示只用低频
光场，1 表示完全跟随 AI 画出的局部明暗。

视频版（决策 56）把光场与强度折成 ``LightCompose`` 一次，逐帧只调 ``apply``。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .fields import LightFields, linear_to_srgb8, srgb_to_linear


@dataclass(frozen=True)
class LightCompose:
    """固定强度下的光场，已放大到原尺寸：逐帧合成只做乘算。

    ``relight`` 每调一次都要用 LUT 把 2K 光场放大到原尺寸一次，再逐通道求幂；一段
    视频几帧到几百帧，同一份光场放大同样的次数没有意义。这里把三个场预先放大、
    把强度与局部明暗折进指数与矩阵，则 ``apply`` 与 ``relight`` 逐位一致。
    """

    gain: np.ndarray          # gY^strength，(H, W) float32
    visibility: np.ndarray    # V^(local_shading × strength)，(H, W) float32
    white_balance: np.ndarray # (1 - strength) I + strength W，3×3 float32

    def apply(self, gt8: np.ndarray) -> np.ndarray:
        """把光场乘到一帧上，返回同尺寸 8-bit BGR。"""
        gt_linear = srgb_to_linear(gt8).astype(np.float32)
        out_linear = (np.einsum("...k,ck->...c", gt_linear, self.white_balance)
                      * self.gain[..., None]
                      * self.visibility[..., None])
        return linear_to_srgb8(np.clip(out_linear, 0.0, 1.0))


def light_compose(fields: LightFields, size: tuple[int, int],
                  strength: float, local_shading: float) -> LightCompose:
    """把 2K 光场放大到 ``size`` 并折进强度参数，得到逐帧可复用的 ``LightCompose``。"""
    width, height = size
    gain = cv2.resize(fields.gain, (width, height), interpolation=cv2.INTER_LINEAR)
    visibility = cv2.resize(fields.visibility, (width, height),
                            interpolation=cv2.INTER_LINEAR)
    white_balance = ((1.0 - strength) * np.eye(3, dtype=np.float32)
                     + strength * fields.white_balance)
    return LightCompose(
        gain=np.power(gain, strength).astype(np.float32),
        visibility=np.power(visibility, local_shading * strength).astype(np.float32),
        white_balance=white_balance.astype(np.float32),
    )


def relight(gt8: np.ndarray, fields: LightFields,
            strength: float, local_shading: float) -> np.ndarray:
    """把 ``fields`` 乘到 GT 上，返回与 GT 同尺寸的 8-bit BGR 图。"""
    height, width = gt8.shape[:2]
    return light_compose(fields, (width, height), strength, local_shading).apply(gt8)


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
