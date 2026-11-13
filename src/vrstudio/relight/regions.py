"""区域参数覆盖：球面多边形 → 2K 逐像素参数场。

这是「区域」的唯一权威点：``[lon, lat]`` 与 ERP 像素的换算、叠加顺序与羽化宽度。
它不做相机投影——屏幕 ↔ 球面的换算是前端 ``utils/erpMath.ts`` 的事。

约定（决策 88）：

- 顶点是 ``[lon, lat]``，单位度。经度允许超出 ±180 且不 wrap：跨接缝的多边形写成
  ``179`` 与 ``181`` 两个点，光栅化时按 ±360 平移补画。
- ``pole`` 为 ``north`` / ``south`` 时表示含该极点的盖帽：``verts`` 是绕该极点一圈的
  闭环，光栅化时把首个顶点按 ±360 复制成接缝短边，再沿纬度 ±90 补极点边。
  盖帽只表示极点所在的一侧，不表示环带、洞或极点的补集。
- 参数是稀疏表：``None`` 表示沿用默认值，该通道不参与叠加。
- ``ranges`` 为 ``None`` 表示整段生效；非空是秒区间列表，解析时排序并合并重叠或相接的
  区间（决策 91）。帧号换算不在本模块，见 ``relight.timeaxis``。
- 叠加按数组顺序，后一个盖在前一个上面；羽化只在该区域边界与下方值之间过渡。
- 没有区域或全部 ``enabled = false`` 时返回 ``None``，调用方走原来的标量路径，
  成片与改动前逐位一致。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

FORMAT_VERSION = 1
MAX_REGIONS = 16
MAX_VERTICES = 64
MAX_RANGES_RAW = 64
MAX_RANGES_PER_REGION = 8
LAT_LIMIT = 89.5
MAX_ABS_LON = 720.0
"""经度绝对值上限：允许绕一两圈，但拒绝明显的单位错误（例如把像素当度数）。"""

_REGION_KEYS = ("id", "name", "color", "enabled", "verts", "pole",
                "feather_deg", "params", "ranges")
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
class TimeRange:
    """一个生效区间，单位秒，左闭右开。"""

    start: float
    end: float


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    color: str
    enabled: bool
    verts: tuple[tuple[float, float], ...]
    pole: str | None
    feather_deg: float
    params: RegionParams
    ranges: tuple[TimeRange, ...] | None


@dataclass(frozen=True)
class RegionMaps:
    """2K 工作尺度上的逐像素参数场。

    ``clip_knee`` / ``clip_max`` 的 1.0 表示该像素不做软裁切（``y > 1`` 永不成立）。

    四个场都是无量纲的 0..1 参数，与帧的位深无关（决策 99 WP4 只改像素刻度：
    0..255 → 0..65535）；掩码自己的 0/255 光栅只用于求覆盖比例。因此这一层在
    16-bit 迁移里没有数值需要换。
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
    """读区域文件并校验；文件不存在或 JSON 坏掉都按参数错报出真实原因。

    运行路径打开几何校验：手改文件或旧数据即使绕过界面检查，也不会静默画出错误掩码。
    """
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegionError(f"区域文件读不到：{target}（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise RegionError(f"区域文件不是合法 JSON：{target}（{exc}）") from exc
    try:
        return parse_regions(data, validate_geometry=True)
    except RegionError as exc:
        raise RegionError(f"{target}: {exc}") from exc


def parse_regions(data, *, validate_geometry: bool = False) -> list[Region]:
    """把区域文件的内容校验成 ``Region`` 列表。

    结构校验总是执行；``validate_geometry`` 追加「单个闭合多边形能否表示」的检查。
    CLI 运行路径打开它，Web 读接口保持关闭：旧区域要先能显示出来，页面才能标 ⚠ 并删除。
    """
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
        region = _parse_region(raw, index, validate_geometry=validate_geometry)
        if region.id in seen:
            raise RegionError(f"区域 id 重复：{region.id!r}")
        seen.add(region.id)
        out.append(region)
    return out


def _parse_region(raw, index: int, *, validate_geometry: bool = False) -> Region:
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

    pole = raw.get("pole")
    if pole is not None and pole not in ("north", "south"):
        raise RegionError(f"{where}（{rid}）的 pole 必须是 north、south 或 null")

    if validate_geometry:
        _check_shape(points, pole, where, rid)

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

    ranges = _parse_ranges(raw.get("ranges"), f"{where}（{rid}）的 ranges")

    return Region(
        id=rid, name=name, color=color, enabled=enabled,
        verts=tuple(points), pole=pole, feather_deg=float(feather),
        params=RegionParams(strength=strength, local_shading=shading, clip=clip),
        ranges=ranges,
    )


def _parse_ranges(value, where: str) -> tuple[TimeRange, ...] | None:
    """``ranges``：``null`` 整段，非空数组按并集规范化；空数组拒绝。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise RegionError(f"{where} 必须是数组或 null")
    if not value:
        raise RegionError(
            f"{where} 不能是空数组：整段请写 null，不生效请用 enabled = false")
    if len(value) > MAX_RANGES_RAW:
        raise RegionError(f"{where} 最多 {MAX_RANGES_RAW} 条区间，收到 {len(value)}")
    items: list[TimeRange] = []
    for index, raw in enumerate(value):
        item_where = f"{where} 第 {index + 1} 条"
        if not isinstance(raw, dict):
            raise RegionError(f"{item_where} 必须是对象")
        unknown = sorted(set(raw) - {"start", "end"})
        if unknown:
            raise RegionError(f"{item_where} 不认识的键 {unknown}；可用：start、end")
        start = _seconds_value(raw.get("start"), f"{item_where} 的 start")
        end = _seconds_value(raw.get("end"), f"{item_where} 的 end")
        if end <= start:
            raise RegionError(
                f"{item_where} 的 end 必须大于 start，收到 {start}..{end}")
        items.append(TimeRange(start, end))
    merged = _merge_ranges(items)
    if len(merged) > MAX_RANGES_PER_REGION:
        raise RegionError(
            f"{where} 并集后最多 {MAX_RANGES_PER_REGION} 段，收到 {len(merged)}")
    return tuple(merged)


def _seconds_value(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegionError(f"{where} 必须是非负秒数")
    value = float(value)
    if not math.isfinite(value):
        raise RegionError(f"{where} 必须是有限数，收到 {value}")
    if value < 0:
        raise RegionError(f"{where} 不能为负，收到 {value}")
    return value


def _merge_ranges(items: list[TimeRange]) -> list[TimeRange]:
    """排序并合并重叠或相接的区间；相接算合并，因为结果没有任何不同。"""
    if not items:
        return []
    ordered = sorted(items, key=lambda item: item.start)
    merged = [ordered[0]]
    for item in ordered[1:]:
        last = merged[-1]
        if item.start <= last.end + 1e-9:
            if item.end > last.end:
                merged[-1] = TimeRange(last.start, item.end)
        else:
            merged.append(item)
    return merged


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


def region_coverage(region: Region, width: int, height: int) -> float:
    """区域在给定网格上的空间占比；掩码为空时为 0。"""
    alpha = _alpha(region, width, height)
    return 0.0 if alpha is None else float((alpha > 0.5).mean())


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


def _check_shape(points: list[tuple[float, float]], pole: str | None,
                 where: str, rid: str) -> None:
    """拒绝单个 ERP 闭合多边形装不下的形状。

    普通区域：扭转量必须为 0、跨度不超过 360°、边不自交。
    盖帽（``pole`` 非空）：边界必须绕该极点恰好一圈、边不自交。
    """
    span = _longitude_span(points)
    winding = _winding(points)
    if pole is None:
        if winding != 0:
            raise RegionError(
                f"{where}（{rid}）的边界绕极点转了 {abs(winding)} 圈"
                f"（经度跨度 {span:.0f}°），单个闭合多边形表示不了；请拆成两块")
        if span > 360.0:
            raise RegionError(
                f"{where}（{rid}）的经度跨度 {span:.0f}° 超过一整圈，"
                "单个闭合多边形在切平面上会自重叠；请沿经线拆成两块")
        if _self_intersects(_unwrap(points)):
            raise RegionError(f"{where}（{rid}）的边与边交叉")
        return
    if abs(winding) != 1:
        raise RegionError(
            f"{where}（{rid}）的 pole 区域边界必须绕该极点恰好一圈，"
            f"当前扭转量 {winding}（经度跨度 {span:.0f}°）")
    if span > 360.0:
        raise RegionError(
            f"{where}（{rid}）的 pole 区域经度跨度 {span:.0f}° 超过一整圈，"
            "切平面闭合后会自重叠；请沿经线拆成两块")
    if _self_intersects(_pole_boundary(points, pole), closed=False):
        raise RegionError(f"{where}（{rid}）的边与边交叉")


def _pole_boundary(points: list[tuple[float, float]],
                   pole: str) -> list[tuple[float, float]]:
    """盖帽的真实边界：展开后的闭环，末尾补上接缝短边的复制点。"""
    lifted = _unwrap(points)
    first = lifted[0]
    last = lifted[-1]
    sign = 1.0 if last[0] >= first[0] else -1.0
    return [*lifted, (first[0] + 360.0 * sign, first[1])]


def _pole_polygon(points: list[tuple[float, float]],
                  pole: str) -> list[tuple[float, float]]:
    """盖帽在 ERP 平面上的闭合多边形：真实边界 + 两条经线边 + 极点边。"""
    boundary = _pole_boundary(points, pole)
    pole_lat = 90.0 if pole == "north" else -90.0
    return [
        *boundary,
        (boundary[-1][0], pole_lat),
        (boundary[0][0], pole_lat),
    ]


def _self_intersects(polygon: list[tuple[float, float]], *,
                     closed: bool = True) -> bool:
    """边与边是否交叉；相邻边不算。``closed=False`` 时不检查末边回到首点。"""
    count = len(polygon)
    if count < (4 if closed else 3):
        return False
    edge_count = count if closed else count - 1
    for index in range(edge_count):
        start, end = polygon[index], polygon[(index + 1) % count]
        if start == end:
            continue
        for other in range(index + 1, edge_count):
            if other == index + 1 or (closed and index == 0
                                      and other == edge_count - 1):
                continue
            a, b = polygon[other], polygon[(other + 1) % count]
            if a == b:
                continue
            if _segments_intersect(start, end, a, b):
                return True
    return False


def _orientation(a: tuple[float, float], b: tuple[float, float],
                 c: tuple[float, float]) -> int:
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(value) < 1e-9:
        return 0
    return 1 if value > 0 else -1


def _on_segment(a: tuple[float, float], b: tuple[float, float],
                c: tuple[float, float]) -> bool:
    return (min(a[0], c[0]) <= b[0] <= max(a[0], c[0])
            and min(a[1], c[1]) <= b[1] <= max(a[1], c[1]))


def _segments_intersect(a: tuple[float, float], b: tuple[float, float],
                        c: tuple[float, float], d: tuple[float, float]) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and _on_segment(a, c, b))
            or (o2 == 0 and _on_segment(a, d, b))
            or (o3 == 0 and _on_segment(c, a, d))
            or (o4 == 0 and _on_segment(c, b, d)))


def _longitude_span(points: list[tuple[float, float]]) -> float:
    """``_unwrap`` 之后的经度跨度；超过 360° 的形状在 ERP 平面上会自重叠。"""
    lons = [lon for lon, _ in _unwrap(points)]
    return max(lons) - min(lons) if lons else 0.0


def _winding(points: list[tuple[float, float]]) -> int:
    """边界绕极点的圈数：逐边取最短经度差（闭合边也算）求和后除以 360。"""
    unwrapped = _unwrap(points)
    count = len(unwrapped)
    if count < 3:
        return 0
    total = 0.0
    for index, (lon, _) in enumerate(unwrapped):
        next_lon = unwrapped[(index + 1) % count][0]
        delta = next_lon - lon
        if index == count - 1:
            while delta > 180.0:
                delta -= 360.0
            while delta < -180.0:
                delta += 360.0
        total += delta
    return int(round(total / 360.0))


def _alpha(region: Region, width: int, height: int) -> np.ndarray | None:
    """区域掩码的羽化权重，2K 尺度 float32；多边形为空时返回 ``None``。"""
    mask = _rasterize(region.verts, width, height, region.pole)
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
               width: int, height: int, pole: str | None = None) -> np.ndarray:
    """把区域画到 ERP 网格；跨接缝时按 ±width 平移补画。

    盖帽先合成 ERP 平面上的闭合多边形：真实边界 + 接缝短边 + 极点边。
    """
    polygon = _pole_polygon(verts, pole) if pole else _unwrap(verts)
    xs = np.array([(lon + 180.0) / 360.0 * width for lon, _ in polygon], dtype=np.float64)
    ys = np.array([(90.0 - lat) / 180.0 * height for _, lat in polygon], dtype=np.float64)
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
