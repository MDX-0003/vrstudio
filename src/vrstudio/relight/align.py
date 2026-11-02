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

视频版（决策 56）复用同一套：参考帧在 2K 上先过 ``reference_score``，够结构才解码
原分辨率进 ``estimate_similarity``；变换只估一次，其余帧用 ``warp_with_matrix``
原样复用，不重估。
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

MAX_REFERENCE_SCAN = 90
"""视频选参考帧时最多挑几张试；固定机位下相关曲线很平，90 张够覆盖整段。"""

MIN_FRAME_HOLD = 0.94
"""其余帧与参考帧按同一坐标比的结构重合度下限；低于它就判「画面在动」。

合成素材实测：同坐标 1.0、平移 4 像素 0.99、16 像素 0.887、40 像素 0.798。下限取
0.94，约等于画面整体位移 10 像素以上就判在动。"""

MIN_REFERENCE_STRUCTURE = 12.0
"""选参考帧时，2K 灰度缩略图的标准差下限：黑场与空镜几乎没有结构，不能被选中。

未对齐的带通相关在场景对得上但有几像素平移时也会接近 0，所以不能用它排除黑场；
用画面自身的结构量排除。"""


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


def reference_small(frame: np.ndarray, width: int) -> np.ndarray:
    """按 ``width`` 缩到 2K 工作尺度（等比），返回副本。

    ``cv2.resize`` 在尺寸不变时直接返回同一个数组对象（返回 FrameReader 复用的缓冲区），
    调用方留着它当下一次比较的基准，所以这里必须拷贝。
    """
    height = frame.shape[0]
    work_w = min(width, WORK_WIDTH)
    return cv2.resize(frame, (work_w, max(1, round(work_w * height / width))),
                      interpolation=cv2.INTER_AREA).copy()


def frame_hold(reference_gray: np.ndarray, frame_gray: np.ndarray) -> float:
    """参考帧与另一帧直接按像素比的结构重合度：机位没动时接近 1。

    用固定变换打光要求整段画面在参考帧的坐标里不动，所以判据就是「这一帧和参考帧
    按同一坐标比得上吗」，不需要先估出位移。带通去掉了整体明暗，光照变化不影响它；
    实测同一坐标的静止帧 0.97 以上，画面整体平移后明显下降。
    """
    return structure_correlation(reference_gray.astype(np.float32),
                                 frame_gray.astype(np.float32))


def reference_score(frame: np.ndarray, width: int,
                    atmo_gray: np.ndarray | None = None) -> float:
    """这一帧有多适合当参考帧。

    给了 ``atmo_gray``（2K 氛围图灰度）时钟：与氛围图的结构重合度，按带通相关算，
    用来排除黑场、淡入与空镜——它们和氛围图的相关接近 0，SIFT 也几乎提不出特征。
    不给时钟：缩小后灰度的标准差，也就是画面本身有多少结构可供 SIFT 匹配。
    """
    small = gray8(reference_small(frame, width))
    return structure_score(small, atmo_gray)


def structure_score(gray_small: np.ndarray, atmo_gray: np.ndarray | None = None) -> float:
    """``reference_score`` 的已缩放版本：输入是已缩放的灰度，扫描整段时只缩一次。"""
    gray = gray_small.astype(np.float32)
    if atmo_gray is not None:
        return structure_correlation(gray, atmo_gray)
    return float(gray.std())


def search_matrix(candidate: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """相位相关（带通后）求两帧之间的纯平移，坐标在 2K 工作尺度上。

    只用于小幅位移：带通图像的能量集中在低频，纯平移超过半个通带周期（几十像素）
    时，相位相关的峰值会塌成噪声。视频版判断机位是否移动不用它，用 ``frame_hold``。
    """
    (dx, dy), _ = cv2.phaseCorrelate(band_pass(reference.astype(np.float32)),
                                     band_pass(candidate.astype(np.float32)))
    return np.float32([[1, 0, dx], [0, 1, dy]])


def track_search_matrix(previous: np.ndarray, current: np.ndarray) -> np.ndarray:
    """``previous`` → ``current`` 的 2K 平移，含相位相关的 ±half 模糊解开。

    相位相关只给出模周期内的量，真实位移超过半幅时被卷回一个错误的小值；相邻帧的
    位移远小于半幅，所以把两帧交换顺序再估一次，两个值差半个周期时才按约定取
    ``[-half, half)`` 里的那个。两帧里画面如果整体移动了半个 360°，两种取法都说得通，
    约定本身就是那时的答案。
    """
    forward = search_matrix(current, previous)
    backward = search_matrix(previous, current)
    width = previous.shape[1]
    dx = float(forward[0, 2])
    if abs((dx + float(backward[0, 2])) % width) > 1e-6:
        dx -= round(dx / width) * width
    return np.float32([[1, 0, dx], [0, 1, float(forward[1, 2])]])


def estimate_similarity(gt: np.ndarray, atmo: np.ndarray) -> AlignResult:
    """估计把氛围图变到 GT 坐标的相似变换（缩放、旋转、平移）。

    匹配数或内点少于 ``MIN_INLIERS`` 时退回相位相关只求平移，``fallback`` 记
    ``"phase_correlation"``。两条路都返回结果，是否可信由 ``AlignResult.trusted``
    判定——调用方负责在不可信时中断。
    """
    cv2.setRNGSeed(RANSAC_SEED)
    height, width = gt.shape[:2]
    work_w, work_h = work_size(width, height)
    gt_small = cv2.resize(gt, (work_w, work_h),
                          interpolation=cv2.INTER_AREA)
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
    return warp_with_matrix(atmo, matrix_at_full_size(result, atmo.shape[1]),
                            (atmo.shape[1], atmo.shape[0]))


def warp_with_matrix(atmo: np.ndarray, matrix: np.ndarray,
                     size: tuple[int, int]) -> np.ndarray:
    """按一张已定好的原尺寸变换 warp 到 ``size``，水平环绕。

    ``relight-video`` 每帧复用同一个变换就只调这一个函数：变换的构造仍只住本模块。
    """
    return cv2.warpAffine(atmo, matrix, size,
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
