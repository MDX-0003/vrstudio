"""区域参数覆盖：球面多边形 → 2K 逐像素参数场。

这是「区域」的唯一权威点：``[lon, lat]`` 与 ERP 像素的换算、叠加顺序与羽化宽度。
它不做相机投影——屏幕 ↔ 球面的换算是前端 ``utils/erpMath.ts`` 的事。

约定（决策 88）：

- 顶点是 ``[lon, lat]``，单位度。经度允许超出 ±180 且不 wrap：跨接缝的多边形写成
  ``179`` 与 ``181`` 两个点，光栅化时按 ±360 平移补画。
- 参数是稀疏表：``None`` 表示沿用默认值，该通道不参与叠加。
- 叠加按数组顺序，后一个盖在前一个上面；羽化只在该区域边界与下方值之间过渡。
- 没有区域或全部 ``enabled = false`` 时返回 ``None``，调用方走原来的标量路径，
  成片与改动前逐位一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

FORMAT_VERSION = 1
MAX_REGIONS = 16
MAX_VERTICES = 64
LAT_LIMIT = 89.5
MAX_ABS_LON = 720.0
"""经度绝对值上限：允许绕一两圈，但拒绝明显的单位错误（例如把像素当度数）。"""

_REGION_KEYS = ("id", "name", "color", "enabled", "verts", "feather_deg", "params", "ranges")
_PARAM_KEYS = ("strength", "local_shading", "clip")


class RegionError(ValueError):
    """区域文件不合约（CLI 层映射为参数错，退出码 2）。"""


@dataclass(frozen=True)
class RegionParams:
    """一个区域覆盖哪些参数；``None`` 表示沿用默认值。"""

    strength: float | None = None
    local_shading: float | None = None
    clip: tuple[float, float] | None = None   # (knee, max)


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    color: str
    enabled: bool
    verts: tuple[tuple[float, float], ...]
    feather_deg: float
    params: RegionParams


@dataclass(frozen=True)
class RegionMaps:
    """2K 工作尺度上的逐像素参数场。

    ``clip_knee`` / ``clip_max`` 的 1.0 表示该像素不做软裁切（``y > 1`` 永不成立）。
    """

    strength: np.ndarray
    local_shading: np.ndarray
    clip_knee: np.ndarray
    clip_max: np.ndarray
    coverage: dict[str, float]

    @property
    def has_clip(self) -> bool:
        return bool((self.clip_knee < 1.0).any())

    @property
    def strength_is_constant(self) -> bool:
        return bool(self.strength.min() == self.strength.max())


def load_regions(path: str | Path) -> list[Region]:
    """读区域文件并校验；文件不存在或 JSON 坏掉都按参数错报出真实原因。"""
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegionError(f"区域文件读不到：{target}（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise RegionError(f"区域文件不是合法 JSON：{target}（{exc}）") from exc
    try:
        return parse_regions(data)
    except RegionError as exc:
        raise RegionError(f"{target}: {exc}") from exc


def parse_regions(data) -> list[Region]:
    """把区域文件的内容校验成 ``Region`` 列表。"""
    if not isinstance(data, dict):
        raise RegionError("区域文件必须是对象")
    unknown = sorted(set(data) - {"version", "regions"})
    if unknown:
        raise RegionError(f"不认识的顶层键 {unknown}；可用：version、regions")
    version = data.get("version")
    if version != FORMAT_VERSION:
        raise RegionError(f"version 必须是 {FORMAT_VERSION}，收到 {version!r}")
    raw_regions = data.get("regions")
    if not isinstance(raw_regions, list):
        raise RegionError("regions 必须是数组")
    if len(raw_regions) > MAX_REGIONS:
        raise RegionError(f"区域最多 {MAX_REGIONS} 个，收到 {len(raw_regions)}")
    out: list[Region] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_regions):
        region = _parse_region(raw, index)
        if region.id in seen:
            raise RegionError(f"区域 id 重复：{region.id!r}")
        seen.add(region.id)
        out.append(region)
    return out


def _parse_region(raw, index: int) -> Region:
    where = f"第 {index + 1} 个区域"
    if not isinstance(raw, dict):
        raise RegionError(f"{where} 必须是对象")
    unknown = sorted(set(raw) - set(_REGION_KEYS))
    if unknown:
        raise RegionError(f"{where} 不认识的键 {unknown}；可用：{'、'.join(_REGION_KEYS)}")

    rid = raw.get("id")
    if not isinstance(rid, str) or not rid:
        raise RegionError(f"{where} 的 id 必须是非空字符串")
    name = raw.get("name", rid)
    if not isinstance(name, str):
        raise RegionError(f"{where}（{rid}）的 name 必须是字符串")
    color = raw.get("color", "#f59e0b")
    if not isinstance(color, str):
        raise RegionError(f"{where}（{rid}）的 color 必须是字符串")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RegionError(f"{where}（{rid}）的 enabled 必须是布尔值")

    verts = raw.get("verts")
    if not isinstance(verts, list):
        raise RegionError(f"{where}（{rid}）的 verts 必须是数组")
    if not 3 <= len(verts) <= MAX_VERTICES:
        raise RegionError(
            f"{where}（{rid}）的顶点数必须在 3..{MAX_VERTICES} 之间，收到 {len(verts)}")
    points: list[tuple[float, float]] = []
    for point in verts:
        if (not isinstance(point, list) or len(point) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           for v in point)):
            raise RegionError(f"{where}（{rid}）的顶点必须是 [lon, lat] 两个数字")
        lon, lat = float(point[0]), float(point[1])
        if abs(lon) > MAX_ABS_LON:
            raise RegionError(f"{where}（{rid}）的经度 {lon} 超出 ±{MAX_ABS_LON:.0f}")
        if abs(lat) > LAT_LIMIT:
            raise RegionError(f"{where}（{rid}）的纬度 {lat} 超出 ±{LAT_LIMIT}")
        points.append((lon, lat))

    feather = raw.get("feather_deg", 0.0)
    if isinstance(feather, bool) or not isinstance(feather, (int, float)) or feather < 0:
        raise RegionError(f"{where}（{rid}）的 feather_deg 必须是不小于 0 的数字")

    params_raw = raw.get("params") or {}
    if not isinstance(params_raw, dict):
        raise RegionError(f"{where}（{rid}）的 params 必须是对象")
    unknown = sorted(set(params_raw) - set(_PARAM_KEYS))
    if unknown:
        raise RegionError(
            f"{where}（{rid}）的 params 不认识的键 {unknown}；可用：{'、'.join(_PARAM_KEYS)}")
    strength = _unit_value(params_raw.get("strength"), f"{where}（{rid}）的 strength")
    shading = _unit_value(params_raw.get("local_shading"),
                          f"{where}（{rid}）的 local_shading")
    clip = params_raw.get("clip")
    if clip is not None:
        if not isinstance(clip, dict):
            raise RegionError(f"{where}（{rid}）的 clip 必须是对象")
        unknown = sorted(set(clip) - {"knee", "max"})
        if unknown:
            raise RegionError(
                f"{where}（{rid}）的 clip 不认识的键 {unknown}；可用：knee、max")
        knee = _unit_value(clip.get("knee"), f"{where}（{rid}）的 clip.knee")
        max_out = _unit_value(clip.get("max"), f"{where}（{rid}）的 clip.max")
        if knee is None or max_out is None:
            raise RegionError(f"{where}（{rid}）的 clip 必须同时给 knee 与 max")
        if not 0.0 < knee < max_out <= 1.0:
            raise RegionError(
                f"{where}（{rid}）的 soft clip 须满足 0 < knee < max <= 1，"
                f"收到 knee={knee}，max={max_out}")
        clip = (knee, max_out)

    ranges = raw.get("ranges")
    if ranges is not None:
        raise RegionError(
            f"{where}（{rid}）的 ranges 当前版本必须为 null：时间区间在第二阶段")

    return Region(
        id=rid, name=name, color=color, enabled=enabled,
        verts=tuple(points), feather_deg=float(feather),
        params=RegionParams(strength=strength, local_shading=shading, clip=clip),
    )


def _unit_value(value, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegionError(f"{where} 必须是 0..1 的数字或 null")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise RegionError(f"{where} 必须在 0..1 之间，收到 {value}")
    return value


def build_maps(regions: list[Region], work: tuple[int, int], *,
               strength: float, local_shading: float,
               clip: tuple[float, float] | None) -> RegionMaps | None:
    """把区域叠加成 2K 参数场；没有可用区域时返回 ``None``。"""
    active = [region for region in regions if region.enabled]
    if not active:
        return None
    width, height = work
    out_strength = np.full((height, width), np.float32(strength), dtype=np.float32)
    out_shading = np.full((height, width), np.float32(local_shading), dtype=np.float32)
    if clip is None:
        out_knee = np.ones((height, width), dtype=np.float32)
        out_max = np.ones((height, width), dtype=np.float32)
    else:
        out_knee = np.full((height, width), np.float32(clip[0]), dtype=np.float32)
        out_max = np.full((height, width), np.float32(clip[1]), dtype=np.float32)

    coverage: dict[str, float] = {}
    for region in active:
        alpha = _alpha(region, width, height)
        if alpha is None:
            coverage[region.id] = 0.0
            continue
        coverage[region.id] = float((alpha > 0.5).mean())
        if region.params.strength is not None:
            _blend(out_strength, alpha, region.params.strength)
        if region.params.local_shading is not None:
            _blend(out_shading, alpha, region.params.local_shading)
        if region.params.clip is not None:
            _blend(out_knee, alpha, region.params.clip[0])
            _blend(out_max, alpha, region.params.clip[1])

    return RegionMaps(
        strength=out_strength, local_shading=out_shading,
        clip_knee=out_knee, clip_max=out_max, coverage=coverage,
    )


def upscale_clip(maps: RegionMaps,
                 size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray] | None:
    """把 2K 的裁切场放大到输出尺寸；没有区域裁切时返回 ``None``。

    一次运行只调用一次：逐帧放大两张某尺寸的场没有意义，也会把耗时摊到每一帧。
    """
    if not maps.has_clip:
        return None
    width, height = size
    return (
        cv2.resize(maps.clip_knee, (width, height), interpolation=cv2.INTER_LINEAR),
        cv2.resize(maps.clip_max, (width, height), interpolation=cv2.INTER_LINEAR),
    )


def _blend(field: np.ndarray, alpha: np.ndarray, value: float) -> None:
    """``field = field + (value - field) × alpha``，就地写回。"""
    delta = field - np.float32(value)
    delta *= alpha
    field -= delta


def _alpha(region: Region, width: int, height: int) -> np.ndarray | None:
    """区域掩码的羽化权重，2K 尺度 float32；多边形为空时返回 ``None``。"""
    mask = _rasterize(region.verts, width, height)
    if not mask.any():
        return None
    feather_px = region.feather_deg * width / 360.0
    if feather_px <= 0.5:
        return mask.astype(np.float32) / np.float32(255.0)
    inside = (mask > 0).astype(np.uint8)
    dist_in = cv2.distanceTransform(inside, cv2.DIST_L2, 5)
    dist_out = cv2.distanceTransform(1 - inside, cv2.DIST_L2, 5)
    signed = dist_in - dist_out
    return np.clip(0.5 + signed / feather_px, 0.0, 1.0).astype(np.float32)


def _rasterize(verts: tuple[tuple[float, float], ...],
               width: int, height: int) -> np.ndarray:
    """把球面多边形画到 ERP 网格；跨接缝时按 ±width 平移补画。"""
    unwrapped = _unwrap(verts)
    xs = np.array([(lon + 180.0) / 360.0 * width for lon, _ in unwrapped], dtype=np.float64)
    ys = np.array([(90.0 - lat) / 180.0 * height for _, lat in unwrapped], dtype=np.float64)
    points = np.stack([xs, ys], axis=1).round().astype(np.int32)
    mask = np.zeros((height, width), dtype=np.uint8)
    for shift in (-width, 0, width):
        shifted = points.copy()
        shifted[:, 0] += shift
        cv2.fillPoly(mask, [shifted], 255)
    return mask


def _unwrap(verts: tuple[tuple[float, float], ...]) -> list[tuple[float, float]]:
    """把相邻经度差压到 ±180 内，跨接缝的多边形因此变成连续的一串。"""
    out: list[tuple[float, float]] = []
    previous: float | None = None
    for lon, lat in verts:
        if previous is not None:
            while lon - previous > 180.0:
                lon -= 360.0
            while lon - previous < -180.0:
                lon += 360.0
        out.append((lon, lat))
        previous = lon
    return out
