"""视频氛围依据的时间映射与工作尺度取样。

氛围是视频时，GT 与氛围可能分辨率、帧率、帧数都不同（平台常把 30fps 重采样成
24fps）。这里只做两件事：

- ``AtmoMapping``：把 GT 帧号映射到氛围视频帧号。两段都有帧数时按归一化进度
  （首尾对齐），否则按时间，再否则退化成逐帧一一对应。
- ``work_frame``：把一帧降到 2K 工作尺度，供 ``estimate_similarity`` 与
  ``estimate_fields`` 使用。所有光场仍在 2K 上估计，不在 8K 上逐帧 warp。

这是 GT ↔ 氛围帧号的唯一权威点；web 与前端不得实现第二套。相似变换本身仍只住在
``relight/align.py``。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .align import work_size

MAPPING_PROGRESS = "normalized_progress"
MAPPING_TIME = "time"
MAPPING_SEQUENTIAL = "sequential"

_WORK_INTERP = cv2.INTER_AREA


@dataclass(frozen=True)
class AtmoMapping:
    """GT 帧号 → 氛围视频帧号的映射；``mode`` 进报告，便于复查。"""

    gt_frames: int
    atmo_frames: int
    gt_fps: float
    atmo_fps: float
    mode: str

    def index_for(self, gt_index: int) -> int:
        """第 ``gt_index`` 帧（0 起）对应的氛围帧号。"""
        if self.mode == MAPPING_PROGRESS:
            if self.gt_frames <= 1 or self.atmo_frames <= 1:
                return 0
            scaled = round(gt_index * (self.atmo_frames - 1) / (self.gt_frames - 1))
            return int(min(max(scaled, 0), self.atmo_frames - 1))
        if self.mode == MAPPING_TIME:
            if self.gt_fps <= 0 or self.atmo_fps <= 0:
                return max(0, gt_index)
            scaled = round((gt_index / self.gt_fps) * self.atmo_fps)
            if self.atmo_frames > 0:
                scaled = min(scaled, self.atmo_frames - 1)
            return int(max(scaled, 0))
        return max(0, gt_index)

def mapping_for(gt_frames: int, gt_fps: float,
                atmo_frames: int, atmo_fps: float) -> AtmoMapping:
    """按可得信息选映射方式：帧数优先，其次时间，最后逐帧。"""
    if gt_frames > 0 and atmo_frames > 0:
        mode = MAPPING_PROGRESS
    elif gt_fps > 0 and atmo_fps > 0:
        mode = MAPPING_TIME
    else:
        mode = MAPPING_SEQUENTIAL
    return AtmoMapping(gt_frames=gt_frames, atmo_frames=atmo_frames,
                       gt_fps=gt_fps, atmo_fps=atmo_fps, mode=mode)


def work_frame(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """把一帧降到 2K 工作尺度；尺寸已相同也返回副本，调用方可以安全留存。

    ``cv2.resize`` 在尺寸不变时返回同一个数组对象，而 ``FrameReader`` 的缓冲区分帧
    独立，留存原对象本身没有危险；这里仍复制一份，避免调用方把「当前帧」误当成
    「上一帧」的别名。
    """
    work_w, work_h = size
    if frame.shape[1] == work_w and frame.shape[0] == work_h:
        return np.array(frame, copy=True)
    return cv2.resize(frame, (work_w, work_h), interpolation=_WORK_INTERP)


def work_size_for(width: int, height: int) -> tuple[int, int]:
    """GT 的 2K 工作尺度；与 ``align.work_size`` 是同一份规则。"""
    return work_size(width, height)
