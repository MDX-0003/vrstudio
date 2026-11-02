"""视频重打光的固定光照缓存：参考帧号与全精度相似变换。

缓存只存 ``AlignResult`` 和解析后的参考帧号，不存光照场。命中时跳过选帧扫描与
SIFT，只按参考帧 seek 一帧重算光照场。缓存是 ``derived/`` 下的可删派生数据：
缓存版本、视频/氛围图身份或参考帧参数任何一项不匹配就重算，读写失败都不影响出片。

视频与氛围图没换时才命中；氛围图一换，缓存键里的 ``atmo_sha256`` 就变了，会重新
从视频里选参考帧（决策 64）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .align import AlignResult

CACHE_VERSION = 1
"""缓存格式版本；改 ``AlignResult`` 的字段或对齐估计逻辑时递增。"""


@dataclass(frozen=True)
class CacheKey:
    """一组输入的完整身份：任何一项不同都不复用。"""

    gt_sha256: str | None
    gt_size: int
    gt_mtime_ns: int
    atmo_sha256: str | None
    atmo_size: int
    atmo_mtime_ns: int
    ref_frame: str | int          # "auto" 或显式帧号
    version: int = CACHE_VERSION

    def as_dict(self) -> dict:
        return {
            "gt_sha256": self.gt_sha256,
            "gt_size": self.gt_size,
            "gt_mtime_ns": self.gt_mtime_ns,
            "atmo_sha256": self.atmo_sha256,
            "atmo_size": self.atmo_size,
            "atmo_mtime_ns": self.atmo_mtime_ns,
            "ref_frame": self.ref_frame,
            "version": self.version,
        }


@dataclass(frozen=True)
class CachedFixed:
    """缓存里恢复出的固定光照：参考帧号、未对齐重合度与相似变换。"""

    ref_frame: int
    ref_matched: float
    alignment: AlignResult


def cache_path(case_dir: Path) -> Path:
    """缓存文件落点：``cases/<case>/derived/relight/fixed.json``。

    ``derived/`` 已经是文档化的可删缓存目录，不会被 ``out/*.mp4``、``out/*.png``
    或 ``*_pad.json`` 的扫描命中。
    """
    return case_dir / "derived" / "relight" / "fixed.json"


def _align_to_dict(result: AlignResult) -> dict:
    matrix = np.asarray(result.matrix)
    return {
        "matrix": matrix.reshape(-1).tolist(),
        "matrix_dtype": str(matrix.dtype),
        "scale": result.scale,
        "angle_deg": result.angle_deg,
        "tx": result.tx,
        "ty": result.ty,
        "inliers": result.inliers,
        "keypoints_gt": result.keypoints_gt,
        "keypoints_atmo": result.keypoints_atmo,
        "matches": result.matches,
        "fallback": result.fallback,
        "correlation_before": result.correlation_before,
        "correlation_after": result.correlation_after,
    }


def _align_from_dict(raw: dict) -> AlignResult:
    dtype = np.dtype(str(raw["matrix_dtype"]))
    matrix = np.asarray(raw["matrix"], dtype=dtype).reshape(2, 3)
    return AlignResult(
        matrix=matrix,
        scale=float(raw["scale"]),
        angle_deg=float(raw["angle_deg"]),
        tx=float(raw["tx"]),
        ty=float(raw["ty"]),
        inliers=int(raw["inliers"]),
        keypoints_gt=int(raw["keypoints_gt"]),
        keypoints_atmo=int(raw["keypoints_atmo"]),
        matches=int(raw["matches"]),
        fallback=raw["fallback"],
        correlation_before=float(raw["correlation_before"]),
        correlation_after=float(raw["correlation_after"]),
    )


def load(path: Path, key: CacheKey) -> CachedFixed | None:
    """读缓存；文件缺失、损坏、版本或键不匹配时返回 ``None``，由调用方重算。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if raw.get("key") != key.as_dict():
        return None
    try:
        cached = CachedFixed(
            ref_frame=int(raw["ref_frame"]),
            ref_matched=float(raw["ref_matched"]),
            alignment=_align_from_dict(raw["align"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if cached.ref_frame < 0:
        return None
    return cached


def save(path: Path, key: CacheKey, fixed: CachedFixed) -> bool:
    """原子写缓存；失败只返回 ``False``，调用方记一条警告即可，不影响出片。"""
    payload = {
        "key": key.as_dict(),
        "ref_frame": fixed.ref_frame,
        "ref_matched": fixed.ref_matched,
        "align": _align_to_dict(fixed.alignment),
    }
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
