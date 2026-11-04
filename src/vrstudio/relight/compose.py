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

from .bands import run_bands
from .fields import LightFields, linear_to_srgb8_inplace, srgb_to_linear


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

    def apply(self, gt8: np.ndarray, *, pool=None, workers: int = 1) -> np.ndarray:
        """把光场乘到一帧上，返回同尺寸 8-bit BGR。

        ``pool`` 给出时按行带并行；每个行带只申请自己那一段的 float32 临时数组，
        输出数组在这里预分配、由各线程写互不重叠的行区间。切带不改变运算顺序，
        结果与整帧计算逐位相同。
        """
        out = np.empty(gt8.shape, dtype=gt8.dtype)

        def band(y0: int, y1: int) -> None:
            gt_linear = srgb_to_linear(gt8[y0:y1])
            out_linear = np.einsum("...k,ck->...c", gt_linear, self.white_balance)
            np.multiply(out_linear, self.gain[y0:y1, :, None], out=out_linear)
            np.multiply(out_linear, self.visibility[y0:y1, :, None], out=out_linear)
            out[y0:y1] = linear_to_srgb8_inplace(out_linear)

        run_bands(gt8.shape[0], band, pool, workers)
        return out


def _power_bands(field: np.ndarray, exponent: float, pool, workers: int) -> None:
    """就地求幂；分带执行与整帧执行逐元素相同。"""
    value = np.float32(exponent)

    def band(y0: int, y1: int) -> None:
        np.power(field[y0:y1], value, out=field[y0:y1])

    run_bands(field.shape[0], band, pool, workers)


def light_compose(fields: LightFields, size: tuple[int, int],
                  strength: float, local_shading: float, *,
                  pool=None, workers: int = 1) -> LightCompose:
    """把 2K 光场放大到 ``size`` 并折进强度参数，得到逐帧可复用的 ``LightCompose``。"""
    width, height = size
    gain = cv2.resize(fields.gain, (width, height), interpolation=cv2.INTER_LINEAR)
    visibility = cv2.resize(fields.visibility, (width, height),
                            interpolation=cv2.INTER_LINEAR)
    _power_bands(gain, strength, pool, workers)
    _power_bands(visibility, local_shading * strength, pool, workers)
    white_balance = ((1.0 - strength) * np.eye(3, dtype=np.float32)
                     + strength * fields.white_balance)
    return LightCompose(
        gain=gain,
        visibility=visibility,
        white_balance=white_balance.astype(np.float32),
    )


def relight(gt8: np.ndarray, fields: LightFields,
            strength: float, local_shading: float) -> np.ndarray:
    """把 ``fields`` 乘到 GT 上，返回与 GT 同尺寸的 8-bit BGR 图。"""
    height, width = gt8.shape[:2]
    return light_compose(fields, (width, height), strength, local_shading).apply(gt8)


def soft_clip(bgr8: np.ndarray, knee: float = 0.72, max_out: float = 0.98, *,
              pool=None, workers: int = 1) -> np.ndarray:
    """高光软裁切：亮度过 ``knee`` 后按指数压向 ``max_out``，色相保持不变。

    只有过曝像素需要浮点运算；其余像素在旧实现里经过 ``/255 * 1 * 255`` 的
    往返，对 0–255 每个值都精确回到原值，所以这里直接复制。亮度按通道计算，
    不物化整张 float32 BGR，省掉 8K 上约 400 MB 的临时数组。

    分带执行时每个行带只看自己的像素，输出写进预分配的数组；结果与整帧相同。
    """
    out = np.empty(bgr8.shape, dtype=bgr8.dtype)

    def band(y0: int, y1: int) -> None:
        sub = bgr8[y0:y1]
        y = (0.0722 * (sub[..., 0].astype(np.float32) / 255.0)
             + 0.7152 * (sub[..., 1].astype(np.float32) / 255.0)
             + 0.2126 * (sub[..., 2].astype(np.float32) / 255.0))
        out[y0:y1] = sub
        over = y > knee
        if not over.any():
            return
        span = max(max_out - knee, 1e-3)
        y_over = y[over]
        ratio = (max_out - span * np.exp(-(y_over - knee) / span)) / np.maximum(y_over, 1e-6)
        target = out[y0:y1]
        target[over] = np.clip(
            (sub[over].astype(np.float32) / 255.0) * ratio[:, None] * 255.0,
            0, 255).astype(np.uint8)

    run_bands(bgr8.shape[0], band, pool, workers)
    return out


def clipped_fraction(bgr8: np.ndarray, threshold: int = 250) -> float:
    """任一通道达到 ``threshold`` 的像素比例——过曝的量度。"""
    return float((bgr8.max(axis=2) >= threshold).mean())
