"""氛围图到 GT 的相似变换：估计、warp 与可信度判定。

这是重打光这套相似变换的唯一权威点（决策 48）：SIFT 特征 + RANSAC 相似变换、
匹配不足时退回相位相关只求平移、带水平环绕的 warp，以及「对齐是否可信」的判据。
``redraw/`` 与 ``patch/projection`` 不得实现第二套。

尺度约定：估计在 2K 工作尺度（宽 ``WORK_WIDTH``）上做；平移按「原宽 / 2K 宽」放大
回原尺寸，缩放与旋转按相似变换的定义不变。旋转保留、不钳制——实测 −0.14° 到
−0.33°，钳到 0 反而离真实变换更远。

可信度用一条判据：把氛围图按拟合出的变换 warp 过去以后，两张图在 2–6 像素尺度上的
结构重合度（各自减去 6 像素模糊、再各自减去 2 像素模糊，得到只剩粗结构的带通图像，
然后算归一化相关；0 表示无关，1 表示重合）低于 0.15 就判错位。不看内点数：内点数
只说明有多少对特征被解释了，不说明变换对不对，重复窗格能配上相邻的柱子。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

WORK_WIDTH = 2048
"""估计尺度：宽固定 2048，高按输入比例算（2:1 输入即 2048×1024）。"""

RANSAC_SEED = 20261029
"""固定 RANSAC 随机种子：同一组输入必须给出同一个变换。"""

MATCH_RATIO = 0.75
MIN_INLIERS = 12
RANSAC_REPROJ_PX = 3.0

BAND_SIGMA_FINE = 2.0
BAND_SIGMA_COARSE = 6.0

MIN_STRUCTURE_CORRELATION = 0.15
"""对齐后结构重合度下限；实测合格候选在 0.37–0.44，错位时约 0。"""


@dataclass(frozen=True)
class AlignResult:
    """一次对齐的结果，坐标落在 2K 工作尺度上。

    ``matrix`` 是 2K 尺度上把氛围图坐标映射到 GT 坐标的 2×3 相似变换；
    ``tx``/``ty`` 是同一尺度上的平移，`report` 另行给出放大到原尺寸的值。
    """

    matrix: np.ndarray
    scale: float
    angle_deg: float
    tx: float
    ty: float
    inliers: int
    keypoints_gt: int
    keypoints_atmo: int
    matches: int
    fallback: str | None
    correlation_before: float
    correlation_after: float

    @property
    def trusted(self) -> bool:
        return self.correlation_after >= MIN_STRUCTURE_CORRELATION


def work_size(width: int, height: int) -> tuple[int, int]:
    """2K 工作尺度的宽高，保持输入比例。"""
    return WORK_WIDTH, max(1, round(WORK_WIDTH * height / width))


def band_pass(gray: np.ndarray) -> np.ndarray:
    """只留 2–6 像素尺度的粗结构：整体明暗与细纹理都被减掉。"""
    fine = cv2.GaussianBlur(gray, (0, 0), BAND_SIGMA_FINE)
    coarse = cv2.GaussianBlur(gray, (0, 0), BAND_SIGMA_COARSE)
    return fine - coarse


def structure_correlation(gray_a: np.ndarray, gray_b: np.ndarray) -> float:
    """两张图在 2–6 像素尺度上的结构重合度（归一化相关）。"""
    a = band_pass(gray_a.astype(np.float32))
    b = band_pass(gray_b.astype(np.float32))
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt(float((a * a).sum()) * float((b * b).sum()) + 1e-9)
    return float((a * b).sum() / denom)


def gray8(bgr8: np.ndarray) -> np.ndarray:
    """uint8 灰度：SIFT 与相位相关都要求 CV_8U 或浮点，这里统一从 8-bit 起算。"""
    return cv2.cvtColor(bgr8, cv2.COLOR_BGR2GRAY)


def estimate_similarity(gt: np.ndarray, atmo: np.ndarray) -> AlignResult:
    """估计把氛围图变到 GT 坐标的相似变换（缩放、旋转、平移）。

    匹配数或内点少于 ``MIN_INLIERS`` 时退回相位相关只求平移，``fallback`` 记
    ``"phase_correlation"``。两条路都返回结果，是否可信由 ``AlignResult.trusted``
    判定——调用方负责在不可信时中断。
    """
    cv2.setRNGSeed(RANSAC_SEED)
    height, width = gt.shape[:2]
    work_w, work_h = work_size(width, height)
    gt_small = cv2.resize(gt, (work_w, work_h), interpolation=cv2.INTER_AREA)
    atmo_small = cv2.resize(atmo, (work_w, work_h), interpolation=cv2.INTER_AREA)
    gt_gray8 = gray8(gt_small)
    atmo_gray8 = gray8(atmo_small)
    gt_gray = gt_gray8.astype(np.float32)
    atmo_gray = atmo_gray8.astype(np.float32)
    correlation_before = structure_correlation(gt_gray, atmo_gray)

    sift = cv2.SIFT_create(6000)
    keys_gt, desc_gt = sift.detectAndCompute(gt_gray8, None)
    keys_atmo, desc_atmo = sift.detectAndCompute(atmo_gray8, None)
    good = []
    if desc_gt is not None and desc_atmo is not None and len(desc_gt) and len(desc_atmo):
        for pair in cv2.BFMatcher().knnMatch(desc_gt, desc_atmo, k=2):
            if len(pair) == 2 and pair[0].distance < MATCH_RATIO * pair[1].distance:
                good.append(pair[0])

    matrix = None
    inliers = 0
    if len(good) >= MIN_INLIERS:
        src = np.float32([keys_atmo[m.trainIdx].pt for m in good])
        dst = np.float32([keys_gt[m.queryIdx].pt for m in good])
        matrix, mask = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=RANSAC_REPROJ_PX,
            maxIters=5000, confidence=0.999,
        )
        inliers = int(mask.sum()) if mask is not None else 0

    fallback = None
    if matrix is None or inliers < MIN_INLIERS:
        (dx, dy), _ = cv2.phaseCorrelate(band_pass(gt_gray), band_pass(atmo_gray))
        matrix = np.float32([[1, 0, dx], [0, 1, dy]])
        fallback = "phase_correlation"

    warped = cv2.warpAffine(atmo_gray, matrix, (work_w, work_h),
                            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    return AlignResult(
        matrix=matrix,
        scale=float(np.sqrt(matrix[0, 0] ** 2 + matrix[0, 1] ** 2)),
        angle_deg=float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0]))),
        tx=float(matrix[0, 2]),
        ty=float(matrix[1, 2]),
        inliers=inliers,
        keypoints_gt=len(keys_gt or []),
        keypoints_atmo=len(keys_atmo or []),
        matches=len(good),
        fallback=fallback,
        correlation_before=correlation_before,
        correlation_after=structure_correlation(gt_gray, warped),
    )


def matrix_at_full_size(result: AlignResult, width: int) -> np.ndarray:
    """把 2K 尺度的平移放大到原尺寸；缩放与旋转不变。"""
    matrix = result.matrix.copy()
    factor = width / WORK_WIDTH
    matrix[0, 2] *= factor
    matrix[1, 2] *= factor
    return matrix


def warp_to_gt(atmo: np.ndarray, result: AlignResult) -> np.ndarray:
    """把氛围图 warp 到 GT 坐标，水平方向按 ERP 环绕取像素。"""
    height, width = atmo.shape[:2]
    return cv2.warpAffine(atmo, matrix_at_full_size(result, width), (width, height),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
