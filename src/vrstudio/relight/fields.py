"""光照场：引导滤波低频、亮度增益、可见度与全局白点矩阵。

全部在线性光下计算，且在 2K 工作尺度上算（与 ``align`` 同一个 ``WORK_WIDTH``），
再交给 ``compose`` 放大到原尺寸。这是重打光 v2 引擎的「表示」部分，只读不写、
不含 I/O——输入是两张 8-bit BGR 图，输出是一组 float32 场。

三项各挡一类已实测的失败（决策 48）：

- ``gain`` 用 GT 亮度引导的联合引导滤波，而不是高斯低频：烛光在 σ=48 低频里
  是亮的，比值看不到遮挡，会把人脸整体点亮。
- ``visibility`` 让模型画出的阴影能压暗 GT。
- ``white_balance`` 只做全局 von Kries，不做逐通道比值：逐通道比值在日光下把
  GT 木材的高饱和红一起放大。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .align import work_size
from .bands import run_tasks

GUIDED_RADIUS_2K = 12
GUIDED_EPS = 300.0

SHADOW_PIVOT = 0.01
"""暗部置信权重的半效点：线性亮度，约等于 sRGB 28/255。

``gain = low(atmo)/low(GT)`` 在两个小量相除时没有物理意义：8-bit 夜戏里 71% 的像素
低于 0.01，实测这一段 gain 的 p95 是 12–13、最大值 149。低于这个量级时 GT 只剩
量化与编码噪声，乘算不该进入；0.01 是 8-bit 素材还能承载反射率的下界。
"""

_IDX = np.arange(256, dtype=np.float64) / 255.0
LUT_SRGB_TO_LINEAR = np.where(
    _IDX <= 0.04045,
    _IDX / 12.92,
    ((_IDX + 0.055) / 1.055) ** 2.4,
).astype(np.float32)

_STEPS = np.arange(65536, dtype=np.float64) / 65535.0
LUT_LINEAR_TO_SRGB8 = np.clip(np.round(
    np.where(
        _STEPS <= 0.0031308,
        _STEPS * 12.92,
        1.055 * np.power(_STEPS, 1 / 2.4) - 0.055,
    ) * 255.0,
), 0, 255).astype(np.uint8)

RGB_TO_LMS = np.array([
    [0.31399022, 0.63951294, 0.04649755],
    [0.15537241, 0.75789446, 0.08670142],
    [0.01775239, 0.10944209, 0.87256922],
], dtype=np.float32)
LMS_TO_RGB = np.linalg.inv(RGB_TO_LMS).astype(np.float32)


@dataclass(frozen=True)
class LightFields:
    """2K 尺度上的照明表示。"""

    gain: np.ndarray          # 亮度增益场 gY，(h, w) float32；1 表示不变
    visibility: np.ndarray    # 可见度 V，(h, w) float32，clamp 到 [0, 2]
    light: np.ndarray         # AI 低频 La，(h, w, 3) float32，线性光
    gt_light: np.ndarray      # GT 低频 Lg，(h, w, 3) float32，线性光
    white_balance: np.ndarray # 全局白点矩阵 W，3×3 float32，保亮度


def srgb_to_linear(bgr8: np.ndarray) -> np.ndarray:
    # For the uint8 input the old clip+astype pair was an identity copy; skip it
    # so a contiguous frame goes straight into the LUT.
    if bgr8.dtype == np.uint8:
        src = bgr8 if bgr8.flags.c_contiguous else np.ascontiguousarray(bgr8)
        return cv2.LUT(src, LUT_SRGB_TO_LINEAR)
    return cv2.LUT(np.clip(bgr8, 0, 255).astype(np.uint8), LUT_SRGB_TO_LINEAR)


def linear_to_srgb8(linear: np.ndarray) -> np.ndarray:
    return LUT_LINEAR_TO_SRGB8[np.clip(linear * 65535.0, 0, 65535).astype(np.uint16)]


def linear_to_srgb8_inplace(linear: np.ndarray) -> np.ndarray:
    """``linear_to_srgb8`` for an array the caller owns, reusing its memory.

    Same arithmetic and same LUT: multiply by 65535, clip, cast to uint16, index.
    The separate ``clip(..., 0, 1)`` is unnecessary because the 65535 clip covers
    both ends. ``linear`` must be writable and disposable.
    """
    np.multiply(linear, 65535.0, out=linear)
    np.clip(linear, 0, 65535, out=linear)
    return LUT_LINEAR_TO_SRGB8[linear.astype(np.uint16)]


def luma(linear_bgr: np.ndarray) -> np.ndarray:
    """线性光亮度（Rec.709 权重，BGR 顺序）。"""
    return (0.2126 * linear_bgr[..., 2]
            + 0.7152 * linear_bgr[..., 1]
            + 0.0722 * linear_bgr[..., 0])


def shadow_weight(gt_light: np.ndarray) -> np.ndarray:
    """乘算场的暗部置信权重，与 ``gt_light`` 同尺寸的 (h, w) float32，取值 0..1。

    ``w = G / (G + SHADOW_PIVOT)``：GT 低频亮度在 0.01（线性）以上时接近 1，越黑越小，
    全黑处为 0。它乘在 ``gain`` 与 ``visibility`` 的指数上，让比值只在 GT 有信号的
    位置生效；``strength = 0`` 时指数仍为 0，逐位恒等不受影响。
    """
    low = luma(gt_light)
    np.maximum(low, 0.0, out=low)
    return low / (low + SHADOW_PIVOT)


def guided(guide: np.ndarray, src: np.ndarray,
           radius: int = GUIDED_RADIUS_2K, eps: float = GUIDED_EPS) -> np.ndarray:
    """以 ``guide`` 为引导对 ``src`` 做联合引导滤波，水平方向按 ERP 环绕。"""
    height, width = guide.shape
    padded_guide = np.pad(guide, ((radius, radius), (radius, radius)), mode="reflect")
    padded_guide[radius:radius + height, :radius] = guide[:, -radius:]
    padded_guide[radius:radius + height, -radius:] = guide[:, :radius]
    padded_src = np.pad(src, ((radius, radius), (radius, radius)), mode="reflect")
    padded_src[radius:radius + height, :radius] = src[:, -radius:]
    padded_src[radius:radius + height, -radius:] = src[:, :radius]

    kernel = 2 * radius + 1

    def box(array: np.ndarray) -> np.ndarray:
        return cv2.boxFilter(array, -1, (kernel, kernel), borderType=cv2.BORDER_REFLECT)

    mean_guide = box(padded_guide)
    mean_src = box(padded_src)
    covariance = box(padded_guide * padded_src) - mean_guide * mean_src
    variance = box(padded_guide * padded_guide) - mean_guide * mean_guide
    a = covariance / (variance + eps)
    b = mean_src - a * mean_guide
    return (box(a) * padded_guide + box(b))[radius:radius + height, radius:radius + width]


def white_balance_matrix(light: np.ndarray, gt_light: np.ndarray) -> np.ndarray:
    """LMS 空间的 von Kries 白点，归一化为保亮度。"""
    lms_light = RGB_TO_LMS @ np.maximum(light.reshape(-1, 3).mean(axis=0), 1e-5)
    lms_gt = RGB_TO_LMS @ np.maximum(gt_light.reshape(-1, 3).mean(axis=0), 1e-5)
    gains = np.diag(np.clip(lms_light / np.maximum(lms_gt, 1e-6), 0.2, 5.0))
    rgb = LMS_TO_RGB @ gains @ RGB_TO_LMS
    y = float(0.2126 * rgb[2, 2] + 0.7152 * rgb[1, 1] + 0.0722 * rgb[0, 0])
    return (rgb / max(y, 1e-6)).astype(np.float32)


def estimate_fields(gt8: np.ndarray, atmo8: np.ndarray, *,
                    pool=None) -> LightFields:
    """在 2K 上估计照明表示；输入是同尺寸 8-bit BGR 图（氛围图已对齐）。

    ``pool`` 给出时 6 次引导滤波并行（3 个 GT 通道 + 3 个氛围通道）。每次调用
    仍然完整计算自己的引导统计量，不共享中间量；并行只改变执行顺序的等待方式，
    不改变任何一次滤波的输入与输出。
    """
    height, width = gt8.shape[:2]
    work_w, work_h = work_size(width, height)
    gt_lin = srgb_to_linear(cv2.resize(gt8, (work_w, work_h),
                                      interpolation=cv2.INTER_AREA)).astype(np.float32)
    atmo_lin = srgb_to_linear(cv2.resize(atmo8, (work_w, work_h),
                                        interpolation=cv2.INTER_AREA)).astype(np.float32)

    guide = luma(gt_lin)
    channels = [gt_lin[..., c] for c in range(3)] + [atmo_lin[..., c] for c in range(3)]
    smoothed = run_tasks(6, lambda i: guided(guide, channels[i]), pool)
    gt_light = np.stack(smoothed[:3], axis=-1)
    light = np.stack(smoothed[3:], axis=-1)
    light_y = luma(light)

    gain = (light_y + 1e-4) / np.maximum(luma(gt_light) + 1e-4, 1e-6)
    visibility = np.clip(luma(atmo_lin) / np.maximum(light_y, 1e-4), 0.0, 2.0)
    return LightFields(
        gain=gain.astype(np.float32),
        visibility=visibility.astype(np.float32),
        light=light,
        gt_light=gt_light,
        white_balance=white_balance_matrix(light, gt_light),
    )
