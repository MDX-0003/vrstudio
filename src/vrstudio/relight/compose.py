"""合成：把光照场乘到 GT 上，以及高光软裁切与色度差低通。

公式（线性光，决策 48；暗部置信权重见决策 84）：

```text
w        = G_low / (G_low + SHADOW_PIVOT)
Ws       = (1 - strength) × I + strength × W
out_lin  = GT_lin × Ws × gY^(strength × w) × V^(local_shading × strength × w)
```

``strength=0`` 时两个指数都是 0、``Ws`` 退化为单位矩阵，输出与 GT 逐位一致；
这是验收基线，不是巧合。``local_shading`` 只作用在可见度项上：0 表示只用低频
光场，1 表示完全跟随 AI 画出的局部明暗。

``w`` 让比值只在 GT 有信号的亮度区间生效：夜戏里七成像素低于 1% 线性亮度，
那里的 ``gain`` 是两个近黑量相除，直接乘上去会把量化噪声与编码块放大成可见的灰雾。

``chroma_lowpass`` 在 8-bit 输出之后单独处理色度：只把 ``relit - GT`` 的色度差磨平，
亮度重打光保持不动（决策 85）。它按行带分线程，结果与整帧计算逐位相同。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .bands import run_bands
from .fields import LightFields, linear_to_srgb8_inplace, shadow_weight, srgb_to_linear

CHROMA_SIGMA = 2.0
"""色度差的低通 σ（输出分辨率的像素）。由 4:2:0 的色度采样率决定，不是创作参数。"""

CHROMA_BAND_MARGIN = 8
"""行带外扩行数：必须大于 σ=2 对应高斯核的半径（6），否则带边界会与整帧不同。"""


@dataclass(frozen=True)
class LightCompose:
    """固定强度下的光场，已放大到原尺寸：逐帧合成只做乘算。

    ``relight`` 每调一次都要用 LUT 把 2K 光场放大到原尺寸一次，再逐通道求幂；一段
    视频几帧到几百帧，同一份光场放大同样的次数没有意义。这里把三个场预先放大、
    把强度与局部明暗折进指数与矩阵，则 ``apply`` 与 ``relight`` 逐位一致。

    ``strength_map`` 是区域覆盖（决策 88）的逐像素施加量，已放大到原尺寸；为 ``None``
    时走标量白平衡矩阵，与引入区域之前逐位一致。
    """

    gain: np.ndarray          # gY^(strength × w)，(H, W) float32
    visibility: np.ndarray    # V^(local_shading × strength × w)，(H, W) float32
    white_balance: np.ndarray # (1 - strength) I + strength W，3×3 float32
    strength_map: np.ndarray | None = None  # (H, W) float32；有区域时逐像素混合 I 与 W

    def apply(self, gt8: np.ndarray, *, pool=None, workers: int = 1) -> np.ndarray:
        """把光场乘到一帧上，返回同尺寸 8-bit BGR。

        ``pool`` 给出时按行带并行；每个行带只申请自己那一段的 float32 临时数组，
        输出数组在这里预分配、由各线程写互不重叠的行区间。切带不改变运算顺序，
        结果与整帧计算逐位相同。
        """
        out = np.empty(gt8.shape, dtype=gt8.dtype)

        def band(y0: int, y1: int) -> None:
            gt_linear = srgb_to_linear(gt8[y0:y1])
            if self.strength_map is None:
                out_linear = np.einsum("...k,ck->...c", gt_linear, self.white_balance)
            else:
                # 逐像素白平衡：out = GT + s × (GT × W − GT)，等价于 (1−s)I + sW。
                wb = np.einsum("...k,ck->...c", gt_linear, self.white_balance)
                strength = self.strength_map[y0:y1, :, None]
                out_linear = gt_linear + strength * (wb - gt_linear)
            np.multiply(out_linear, self.gain[y0:y1, :, None], out=out_linear)
            np.multiply(out_linear, self.visibility[y0:y1, :, None], out=out_linear)
            out[y0:y1] = linear_to_srgb8_inplace(out_linear)

        run_bands(gt8.shape[0], band, pool, workers)
        return out


def _power_bands(field: np.ndarray, exponent, pool, workers: int) -> None:
    """就地求幂；``exponent`` 是标量或与 ``field`` 同尺寸的数组。

    分带执行与整帧执行逐元素相同：行带只切片，不改变任何一个元素的指数值。
    """
    scalar = np.ndim(exponent) == 0
    value = np.float32(exponent) if scalar else exponent

    def band(y0: int, y1: int) -> None:
        exp = value if scalar else value[y0:y1]
        np.power(field[y0:y1], exp, out=field[y0:y1])

    run_bands(field.shape[0], band, pool, workers)


def light_compose(fields: LightFields, size: tuple[int, int],
                  strength: float | np.ndarray,
                  local_shading: float | np.ndarray, *,
                  pool=None, workers: int = 1) -> LightCompose:
    """把 2K 光场折进强度参数并放大到 ``size``，得到逐帧可复用的 ``LightCompose``。

    指数带暗部置信权重 ``w``（决策 84），所以先在 2K 上求幂、再放大；放大后的场与
    原实现的顺序不同，但仍然是同一份光场。

    ``strength`` / ``local_shading`` 可以是标量，也可以是 2K 工作尺度上的逐像素场
    （决策 88 的区域覆盖）。标量路径的表达式与运算顺序保持不变，逐位一致；场路径
    只在有区域时出现，白平衡改成逐像素混合 ``I`` 与 ``W``。
    """
    weight = shadow_weight(fields.gt_light)
    gain = fields.gain.copy()
    visibility = fields.visibility.copy()
    width, height = size

    strength_array = None if np.ndim(strength) == 0 else np.asarray(strength, np.float32)
    shading_array = None if np.ndim(local_shading) == 0 else np.asarray(
        local_shading, np.float32)
    if strength_array is not None and strength_array.shape != weight.shape:
        raise ValueError(
            f"strength 场 {strength_array.shape} 与光场 {weight.shape} 不一致")
    if shading_array is not None and shading_array.shape != weight.shape:
        raise ValueError(
            f"local_shading 场 {shading_array.shape} 与光场 {weight.shape} 不一致")

    if strength_array is None and shading_array is None:
        # 标量路径：逐位保持引入区域之前的写法。
        _power_bands(gain, np.float32(strength) * weight, pool, workers)
        _power_bands(visibility, np.float32(local_shading * strength) * weight,
                     pool, workers)
    else:
        s_exp = strength_array if strength_array is not None else np.float32(strength)
        l_exp = shading_array if shading_array is not None else np.float32(local_shading)
        _power_bands(gain, s_exp * weight, pool, workers)
        _power_bands(visibility, (l_exp * s_exp) * weight, pool, workers)

    gain = cv2.resize(gain, (width, height), interpolation=cv2.INTER_LINEAR)
    visibility = cv2.resize(visibility, (width, height),
                            interpolation=cv2.INTER_LINEAR)

    if strength_array is None:
        white_balance = ((1.0 - strength) * np.eye(3, dtype=np.float32)
                         + strength * fields.white_balance)
        strength_map = None
    elif strength_array.min() == strength_array.max():
        # 常数场（例如整幅一个值）退回标量路径，省掉每帧一次 3×3 乘。
        value = float(strength_array.min())
        white_balance = ((1.0 - value) * np.eye(3, dtype=np.float32)
                         + value * fields.white_balance)
        strength_map = None
    else:
        white_balance = fields.white_balance
        strength_map = cv2.resize(strength_array, (width, height),
                                  interpolation=cv2.INTER_LINEAR)

    return LightCompose(
        gain=gain,
        visibility=visibility,
        white_balance=np.asarray(white_balance, dtype=np.float32),
        strength_map=strength_map,
    )


def relight(gt8: np.ndarray, fields: LightFields,
            strength: float, local_shading: float) -> np.ndarray:
    """把 ``fields`` 乘到 GT 上，返回与 GT 同尺寸的 8-bit BGR 图。"""
    height, width = gt8.shape[:2]
    return light_compose(fields, (width, height), strength, local_shading).apply(gt8)


def soft_clip(bgr8: np.ndarray, knee=0.72, max_out=0.98, *,
              pool=None, workers: int = 1) -> np.ndarray:
    """高光软裁切：亮度过 ``knee`` 后按指数压向 ``max_out``，色相保持不变。

    ``knee`` / ``max_out`` 可以是标量，也可以是与 ``bgr8`` 同尺寸的逐像素场（决策 88
    的区域裁切）；场里取 1.0 的像素永远不会过线，即不裁切。标量路径的表达式与运算
    顺序保持不变，逐位一致。

    只有过曝像素需要浮点运算；其余像素在旧实现里经过 ``/255 * 1 * 255`` 的往返，对
    0–255 每个值都精确回到原值，所以这里直接复制。亮度按通道计算，不物化整张
    float32 BGR，省掉 8K 上约 400 MB 的临时数组。

    分带执行时每个行带只看自己的像素，输出写进预分配的数组；结果与整帧相同。
    """
    out = np.empty(bgr8.shape, dtype=bgr8.dtype)
    scalar = np.ndim(knee) == 0 and np.ndim(max_out) == 0

    def band(y0: int, y1: int) -> None:
        sub = bgr8[y0:y1]
        y = (0.0722 * (sub[..., 0].astype(np.float32) / 255.0)
             + 0.7152 * (sub[..., 1].astype(np.float32) / 255.0)
             + 0.2126 * (sub[..., 2].astype(np.float32) / 255.0))
        out[y0:y1] = sub
        if scalar:
            over = y > knee
            if not over.any():
                return
            span = max(max_out - knee, 1e-3)
            y_over = y[over]
            ratio = (max_out - span * np.exp(-(y_over - knee) / span)) / np.maximum(y_over, 1e-6)
        else:
            k = knee[y0:y1] if np.ndim(knee) else knee
            m = max_out[y0:y1] if np.ndim(max_out) else max_out
            over = y > k
            if not over.any():
                return
            span = np.maximum(m - k, 1e-3)
            y_over = y[over]
            k_over = np.broadcast_to(k, y.shape)[over]
            max_over = np.broadcast_to(m, y.shape)[over]
            span_over = span[over] if np.ndim(span) else np.float32(span)
            ratio = ((max_over - span_over * np.exp(-(y_over - k_over) / span_over))
                     / np.maximum(y_over, 1e-6))
        target = out[y0:y1]
        target[over] = np.clip(
            (sub[over].astype(np.float32) / 255.0) * ratio[:, None] * 255.0,
            0, 255).astype(np.uint8)

    run_bands(bgr8.shape[0], band, pool, workers)
    return out


def chroma_lowpass(bgr8: np.ndarray, gt8: np.ndarray, *,
                   sigma: float = CHROMA_SIGMA,
                   pool=None, workers: int = 1) -> np.ndarray:
    """保留重打光的亮度，只把"额外加上去的色度对比"磨平（决策 85）。

    ``relit - GT`` 分解成亮度分量（BGR 权重 0.114/0.587/0.299 的投影）与色度向量，
    对色度向量做高斯模糊再加回。亮度分量逐位不动，所以重打光的明暗结果不变；
    改变的是色度差的高频部分——4:2:0 的色度只有半分辨率，抬亮的色度边界在成片里
    表现为彩色台阶，这一步把它们收回到 GT 的量级。

    按行带执行，带外扩 ``CHROMA_BAND_MARGIN`` 行，保证带内结果与整帧计算逐位相同。
    """
    height = bgr8.shape[0]
    out = np.empty(bgr8.shape, dtype=bgr8.dtype)

    def band(y0: int, y1: int) -> None:
        e0 = max(0, y0 - CHROMA_BAND_MARGIN)
        e1 = min(height, y1 + CHROMA_BAND_MARGIN)
        diff = bgr8[e0:e1].astype(np.float32)
        diff -= gt8[e0:e1].astype(np.float32)
        y = (0.114 * diff[..., 0] + 0.587 * diff[..., 1]
             + 0.299 * diff[..., 2])
        diff -= y[..., None]                       # diff 变成色度向量
        smooth = cv2.GaussianBlur(diff, (0, 0), sigma)
        smooth -= diff
        relit = bgr8[e0:e1].astype(np.float32)
        relit += smooth
        np.clip(relit, 0, 255, out=relit)
        start = y0 - e0
        out[y0:y1] = relit[start:start + (y1 - y0)].astype(np.uint8)

    run_bands(height, band, pool, workers)
    return out


def clipped_fraction(bgr8: np.ndarray, threshold: int = 250) -> float:
    """任一通道达到 ``threshold`` 的像素比例——过曝的量度。"""
    return float((bgr8.max(axis=2) >= threshold).mean())
