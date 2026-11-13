"""光照场：引导滤波低频、亮度增益、可见度与全局白点矩阵。

全部在线性光下计算，且在 2K 工作尺度上算（与 ``align`` 同一个 ``WORK_WIDTH``），
再交给 ``compose`` 放大到原尺寸。这是重打光 v2 引擎的「表示」部分，只读不写、
不含 I/O——输入是两张 16-bit BGR 图（每通道 0..65535，决策 99 WP4），输出是一组
float32 场。

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

FULL_SCALE = 65535.0
"""16-bit 每通道满量程。量化、裁剪与 LUT 长度都以它为准（旧实现是 255）。"""

GUIDED_RADIUS_2K = 12
GUIDED_EPS = 300.0
"""引导滤波的正则项，域是线性光 0..1（与位深无关，所以 16-bit 不改值）。

它远大于典型方差（实测约 1e-3），``a = cov / (var + eps)`` 因此接近 0，滤波实际
退化成盒式平滑（实测与纯 boxFilter 的最大差 0.05，输出标准差 0.0084 对比原图
0.29）。这是 WP4 之前一直成立的行为，改了画面就会变，所以本次只记录不动它。"""

SHADOW_PIVOT = 0.01
"""暗部置信权重的半效点：线性亮度，约等于 sRGB 28/255。

``gain = low(atmo)/low(GT)`` 在两个小量相除时没有物理意义：8-bit 夜戏里 71% 的像素
低于 0.01，实测这一段 gain 的 p95 是 12–13、最大值 149。低于这个量级时 GT 只剩
量化与编码噪声，乘算不该进入；0.01 是 8-bit 素材还能承载反射率的下界。

这是线性光的归一化值（0..1），与位深无关：16-bit 下同一个 0.01 对应 7196/65535，
换算不进任何常量。位深迁移没有改动它，行为随之一字不变。
"""

_SRGB_GRID = np.arange(65536, dtype=np.float64) / FULL_SCALE
"""正向表（sRGB → 线性）的自变量：0..1 上的 65536 个等距点，一个码值一格。

65535 = 257 × 255，所以旧 8-bit 表在 v 处的自变量 v/255 与这里的 257v/65535 是
同一个实数，两次除法都是同一次正确舍入——256 项表里的每个 float32 值都原样出现在
65536 项表的 257v 位置上。这就是「8-bit 输入下 srgb_to_linear 与旧实现逐位一致」
的来源，也是像素等价验收的锚点。
"""

LUT_SRGB_TO_LINEAR = np.where(
    _SRGB_GRID <= 0.04045,
    _SRGB_GRID / 12.92,
    ((_SRGB_GRID + 0.055) / 1.055) ** 2.4,
).astype(np.float32)

LINEAR_LUT_SIZE = 1 << 20
"""反向表（线性 → sRGB）的网格数：比 16-bit 输出细 16 倍。

65536 格的线性网格对 16-bit 输出来说不够细：sRGB 曲线在暗部斜率是 12.92，一个
16-bit 输出级只对应 0.077 个 65536 格，暗部十几个码值会塌到同一格上——``strength=0``
就不会再逐位还原 GT（实测 18817 个码值出错，最暗处差十几级）。换成 2^20 格后整表
往返 0 个错值，任意线性值的量化误差 ≤1 级；2 MB 常驻、建表约 12 ms。
"""

_LINEAR_GRID = (np.arange(LINEAR_LUT_SIZE + 1, dtype=np.float32)
                / np.float32(LINEAR_LUT_SIZE))
LUT_LINEAR_TO_SRGB16 = np.clip(np.round(
    np.where(
        _LINEAR_GRID <= 0.0031308,
        _LINEAR_GRID * np.float32(12.92),
        np.float32(1.055) * np.power(_LINEAR_GRID, np.float32(1 / 2.4))
        - np.float32(0.055),
    ) * np.float32(FULL_SCALE),
), 0, FULL_SCALE).astype(np.uint16)


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


def srgb_to_linear(bgr: np.ndarray) -> np.ndarray:
    """16-bit BGR（0..65535）→ 线性光 float32（0..1），按 65536 项 LUT 查表。

    只接受 uint16：8-bit 素材（PNG 底版、测试里的合成图）必须在入口用
    ``media.videoio.to_u16`` 显式升位。那条路径上每个像素都是 257 × v，索引正好
    命中旧 8-bit 表在 v 处的同一个 float32 值，见 ``_SRGB_GRID``。
    """
    if bgr.dtype != np.uint16:
        raise ValueError(
            f"expected uint16 BGR (lift 8-bit sources with media.videoio.to_u16), "
            f"got {bgr.dtype}")
    src = bgr if bgr.flags.c_contiguous else np.ascontiguousarray(bgr)
    return cv2.LUT(src, LUT_SRGB_TO_LINEAR)


def linear_to_srgb16(linear: np.ndarray) -> np.ndarray:
    """线性光 → 16-bit BGR（0..65535），量化用四舍五入（见 ``LINEAR_LUT_SIZE``）。"""
    scaled = np.clip(linear * np.float32(LINEAR_LUT_SIZE),
                     0, LINEAR_LUT_SIZE)
    return LUT_LINEAR_TO_SRGB16[np.rint(scaled).astype(np.uint32)]


def linear_to_srgb16_inplace(linear: np.ndarray) -> np.ndarray:
    """``linear_to_srgb16`` for an array the caller owns, reusing its memory.

    Same arithmetic and same LUT: multiply by ``LINEAR_LUT_SIZE``, clip, round,
    cast to uint32, index. ``np.rint`` (nearest) rather than truncation matters:
    the old 8-bit path truncated, which biased the output half a level dark, and
    rounding is what makes ``strength = 0`` land back on the input code exactly.
    The index array is uint32 (2^20 grid), so a row band costs twice the index
    memory of a 16-bit scheme; the float32 band itself is still a single copy.
    ``linear`` must be writable and disposable.
    """
    np.multiply(linear, np.float32(LINEAR_LUT_SIZE), out=linear)
    np.clip(linear, 0, LINEAR_LUT_SIZE, out=linear)
    np.rint(linear, out=linear)
    return LUT_LINEAR_TO_SRGB16[linear.astype(np.uint32)]


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


def estimate_fields(gt_bgr: np.ndarray, atmo_bgr: np.ndarray, *,
                    pool=None) -> LightFields:
    """在 2K 上估计照明表示；输入是同尺寸 16-bit BGR 图（氛围图已对齐）。

    缩放在 16-bit 上做，这是拿到 10-bit 细节的前提，也带来一处与 8-bit 实现的已知
    差别：``cv2.resize`` 的 INTER_AREA 在 16U 上的取整与 8U 不同（8K → 2K 实测最多
    差 128/65535，即半级 8-bit，出现在 53% 的 2K 像素上）。光场把这点差低通掉，
    gain 的相对差约 5e-5、visibility 约 5e-4；输出上表现为每个通道最多 1 级 8-bit
    的偏移。实测 8K 抽帧与改动前 PSNR 52.3–52.9 dB，光场统计与对齐结果一致。

    ``pool`` 给出时 6 次引导滤波并行（3 个 GT 通道 + 3 个氛围通道）。每次调用
    仍然完整计算自己的引导统计量，不共享中间量；并行只改变执行顺序的等待方式，
    不改变任何一次滤波的输入与输出。
    """
    height, width = gt_bgr.shape[:2]
    work_w, work_h = work_size(width, height)
    gt_lin = srgb_to_linear(cv2.resize(gt_bgr, (work_w, work_h),
                                      interpolation=cv2.INTER_AREA)).astype(np.float32)
    atmo_lin = srgb_to_linear(cv2.resize(atmo_bgr, (work_w, work_h),
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
