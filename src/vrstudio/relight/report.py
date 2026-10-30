"""报告 JSON 与诊断图（返回数组，不写文件）。

报告字段固定，不随运行变化：``gt``/``atmo``/``size``/``params``/``align``/``clip``/
``light``/``seconds``；``clip`` 与 ``light`` 未启用时为 ``null``，而不是缺键。报告只
用于人工核对与回归，不是跨仓库契约（决策 48）。

诊断图与六联对比全部用 opencv 生成：``media`` 之外不引入 Pillow，标签是英文
（Hershey 字体没有中文字形）。
"""

from __future__ import annotations

import cv2
import numpy as np

from .align import AlignResult, matrix_at_full_size
from .fields import LightFields, linear_to_srgb8

PANEL_SIZE = (1024, 512)
LABEL_HEIGHT = 28


def align_report(result: AlignResult, width: int) -> dict:
    """对齐一节：内点、缩放、旋转、2K 与原尺寸平移、对齐前后的结构重合度。"""
    matrix = matrix_at_full_size(result, width)
    return {
        "enabled": True,
        "fallback": result.fallback,
        "trusted": result.trusted,
        "keypoints_gt": result.keypoints_gt,
        "keypoints_atmo": result.keypoints_atmo,
        "matches": result.matches,
        "inliers": result.inliers,
        "scale": round(result.scale, 5),
        "angle_deg": round(result.angle_deg, 4),
        "tx_2k": round(result.tx, 2),
        "ty_2k": round(result.ty, 2),
        "tx": round(float(matrix[0, 2]), 1),
        "ty": round(float(matrix[1, 2]), 1),
        "correlation_before": round(result.correlation_before, 4),
        "correlation_after": round(result.correlation_after, 4),
    }


def light_report(fields: LightFields) -> dict:
    """光照一节：增益与可见度的均值、分位，以及白点矩阵。"""
    return {
        "gain_mean": round(float(fields.gain.mean()), 3),
        "gain_p05": round(float(np.percentile(fields.gain, 5)), 3),
        "gain_p95": round(float(np.percentile(fields.gain, 95)), 3),
        "visibility_mean": round(float(fields.visibility.mean()), 3),
        "visibility_p05": round(float(np.percentile(fields.visibility, 5)), 3),
        "white_balance": np.round(fields.white_balance, 4).tolist(),
    }


def build_report(*, gt: str, atmo: str, size: tuple[int, int], params: dict,
                 align: dict | None, clip: dict | None, light: dict | None,
                 seconds: float) -> dict:
    return {
        "gt": gt,
        "atmo": atmo,
        "size": [int(size[0]), int(size[1])],
        "params": params,
        "align": align,
        "clip": clip,
        "light": light,
        "seconds": round(seconds, 1),
    }


def light_image(fields: LightFields, size: tuple[int, int]) -> np.ndarray:
    """以 GT 为引导的 AI 低频，按 sRGB 显示。"""
    width, height = size
    small = linear_to_srgb8(np.clip(fields.light, 0.0, 1.0))
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def _scaled_field(field: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """单通道场按 128 = 1.0 显示成灰度图。"""
    width, height = size
    small = np.clip(field * 127.5, 0, 255).astype(np.uint8)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def gain_image(fields: LightFields, size: tuple[int, int]) -> np.ndarray:
    return _scaled_field(fields.gain, size)


def visibility_image(fields: LightFields, size: tuple[int, int]) -> np.ndarray:
    return _scaled_field(fields.visibility, size)


def comparison_image(*panels: tuple[str, np.ndarray]) -> np.ndarray:
    """把若干 (标签, 图) 排成每行三格、格子上方留标签带的对比图。"""
    columns = 3
    rows = max(1, (len(panels) + columns - 1) // columns)
    cell_w, cell_h = PANEL_SIZE
    canvas = np.zeros((rows * (cell_h + LABEL_HEIGHT), columns * cell_w, 3), np.uint8)
    for index, (label, image) in enumerate(panels):
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        row, column = divmod(index, columns)
        top = row * (cell_h + LABEL_HEIGHT)
        left = column * cell_w
        canvas[top + LABEL_HEIGHT:top + LABEL_HEIGHT + cell_h,
               left:left + cell_w] = cv2.resize(image, PANEL_SIZE,
                                               interpolation=cv2.INTER_AREA)
        cv2.putText(canvas, label, (left + 8, top + 20), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas
