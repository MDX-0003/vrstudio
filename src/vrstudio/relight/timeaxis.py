"""时间区间 → 帧区间与分段：时间维度的唯一权威点（决策 91）。

``regions.py`` 只负责把一份 ``ranges`` 校验并合并成秒区间；本模块把秒换算成 GT 帧号，
再把各区域的帧区间切成「生效集合不变」的段。CLI 与报告都只读这里的结果，别处不重复
换算公式，也不在循环里逐帧推算归属。

边界规则（左闭右开）：

- 帧 i 的时刻是 ``round(i / fps × 1e6)`` 微秒，区间端点是 ``round(seconds × 1e6)``；
  帧生效当且仅当 ``start_us <= frame_us(i) < end_us``。
- 用整数微秒而不是浮点秒比较，避免前端保留 6 位小数后同一帧落在边界两侧。
- 段边界先估一个帧号，再用同一判定向两侧修正最多一帧。

``frames`` 未知（ffprobe 不报告帧数）时不做终点夹取；最后一段的 ``end`` 为 ``None``，
表示开放到片尾。没有启用区域时返回空表，调用方对所有帧走标量路径。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .regions import Region, TimeRange

MAX_SEGMENTS = 128
"""整条时间轴按生效集合切出的段数上限，含空隙；超过按参数错拒绝。"""

_MICRO = 1_000_000


class TimeAxisError(ValueError):
    """时间区间无法换算或分段不合约（CLI 层映射为参数错，退出码 2）。"""


@dataclass(frozen=True)
class FrameRange:
    """帧号区间，半开 ``[start, end)``。"""

    start: int
    end: int


@dataclass(frozen=True)
class Segment:
    """生效集合不变的帧区间；``end`` 为 ``None`` 表示开放到片尾。"""

    start: int
    end: int | None
    regions: frozenset[str]


def frame_microseconds(index: int, fps: float) -> int:
    """第 ``index`` 帧在时间轴上的时刻（微秒）。"""
    return int(round(index / fps * _MICRO))


def _microseconds(seconds: float) -> int:
    return int(round(seconds * _MICRO))


def _first_at_or_after(target_us: int, fps: float) -> int:
    """第一帧满足 ``frame_us(i) >= target_us``；先估后修正最多一帧。"""
    guess = max(0, int(target_us * fps // _MICRO))
    while frame_microseconds(guess, fps) < target_us:
        guess += 1
    while guess > 0 and frame_microseconds(guess - 1, fps) >= target_us:
        guess -= 1
    return guess


def frame_ranges(ranges: tuple[TimeRange, ...] | None, fps: float,
                 frames: int | None = None) -> list[FrameRange]:
    """把秒区间换算成帧区间并合并相接的段；``None`` 返回空表。"""
    if ranges is None:
        return []
    if fps <= 0:
        raise TimeAxisError(
            "GT 帧率未知，无法把时间区间换算成帧；去掉 ranges 或换 ffprobe "
            "能报告帧率的文件")
    out: list[FrameRange] = []
    for item in ranges:
        start = _first_at_or_after(_microseconds(item.start), fps)
        end = _first_at_or_after(_microseconds(item.end), fps)
        if frames is not None:
            if frames <= 0:
                return []
            start = min(start, frames)
            end = min(end, frames)
        if end > start:
            out.append(FrameRange(start, end))
    return _merge_frame_ranges(out)


def _merge_frame_ranges(items: list[FrameRange]) -> list[FrameRange]:
    if not items:
        return []
    ordered = sorted(items, key=lambda item: item.start)
    merged = [ordered[0]]
    for item in ordered[1:]:
        last = merged[-1]
        if item.start <= last.end:
            if item.end > last.end:
                merged[-1] = FrameRange(last.start, item.end)
        else:
            merged.append(item)
    return merged


def segments(region_list: list[Region], fps: float,
             frames: int | None = None) -> list[Segment]:
    """按生效集合切段；没有启用的区域时返回空表。"""
    enabled = [region for region in region_list if region.enabled]
    if not enabled:
        return []
    ranged = [region for region in enabled if region.ranges is not None]
    if ranged and fps <= 0:
        raise TimeAxisError(
            "GT 帧率未知，无法把时间区间换算成帧；去掉 ranges 或换 ffprobe "
            "能报告帧率的文件")
    whole = frozenset(region.id for region in enabled if region.ranges is None)
    per_region = {
        region.id: frame_ranges(region.ranges, fps, frames)
        for region in ranged
    }
    boundaries = sorted({
        point
        for ranges in per_region.values()
        for item in ranges
        for point in (item.start, item.end)
    })
    if not boundaries:
        end = frames if frames else None
        return [Segment(0, end, whole)] if whole else []

    out: list[Segment] = []
    if boundaries[0] > 0:
        out.append(Segment(0, boundaries[0], whole))
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        ids = set(whole)
        for region_id, ranges in per_region.items():
            if any(item.start <= start < item.end for item in ranges):
                ids.add(region_id)
        out.append(Segment(start, end, frozenset(ids)))
    tail_end = frames if frames else None
    if tail_end is None or boundaries[-1] < tail_end:
        if whole or tail_end is not None:
            out.append(Segment(boundaries[-1], tail_end, whole))

    merged: list[Segment] = []
    for item in out:
        if (merged and merged[-1].regions == item.regions
                and merged[-1].end == item.start):
            merged[-1] = Segment(merged[-1].start, item.end, item.regions)
        else:
            merged.append(item)
    if frames is not None:
        merged = [item for item in merged if item.start < frames]
    if len(merged) > MAX_SEGMENTS:
        raise TimeAxisError(
            f"时间区间把整段切成 {len(merged)} 段，超过上限 {MAX_SEGMENTS}；"
            "请减少区域或区间")
    return merged


def segment_at(segments: list[Segment], index: int) -> Segment | None:
    """二分查帧号所属的段；落在所有段之外（含空隙覆盖不到时）返回 ``None``。"""
    if not segments or index < segments[0].start:
        return None
    position = bisect_right(segments, index, key=lambda item: item.start) - 1
    if position < 0:
        return None
    found = segments[position]
    if found.end is not None and index >= found.end:
        return None
    return found
