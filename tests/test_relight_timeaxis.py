"""时间区间 → 帧区间与分段（决策 91）：边界、空隙、整段与上限。"""

from __future__ import annotations

import pytest

from vrstudio.relight.regions import Region, RegionParams, TimeRange
from vrstudio.relight.timeaxis import (
    MAX_SEGMENTS,
    FrameRange,
    Segment,
    TimeAxisError,
    frame_ranges,
    segment_at,
    segments,
)


def region(rid: str, ranges=None, enabled: bool = True) -> Region:
    return Region(
        id=rid, name=rid, color="#ffffff", enabled=enabled,
        verts=((-1.0, -1.0), (1.0, -1.0), (0.0, 1.0)), pole=None,
        feather_deg=0.0, params=RegionParams(), ranges=ranges,
    )


def ranges(*pairs: tuple[float, float]) -> tuple[TimeRange, ...]:
    return tuple(TimeRange(pair[0], pair[1]) for pair in pairs)


def test_frame_ranges_are_left_closed_and_right_open():
    assert frame_ranges(ranges((0.2, 0.5)), 10.0) == [FrameRange(2, 5)]
    assert frame_ranges(ranges((0.2, 0.3)), 10.0) == [FrameRange(2, 3)]


def test_frame_ranges_match_rounded_microseconds_at_the_boundary():
    # 1/3 秒在 30 fps 下正好是第 10 帧；2/3 秒正好是第 20 帧，按左闭右开被排除。
    assert frame_ranges(ranges((1 / 3, 2 / 3)), 30.0) == [FrameRange(10, 20)]


def test_frame_ranges_handle_2997_fps():
    assert frame_ranges(ranges((1.0, 2.0)), 30000 / 1001) == [FrameRange(30, 60)]


def test_frame_ranges_clamp_to_the_clip_and_drop_empty_ranges():
    assert frame_ranges(ranges((0.0, 10.0)), 10.0, frames=5) == [FrameRange(0, 5)]
    assert frame_ranges(ranges((5.0, 6.0)), 10.0, frames=5) == []
    assert frame_ranges(ranges((0.0, 0.5), (2.0, 3.0)), 10.0, frames=7) == [
        FrameRange(0, 5)]


def test_frame_ranges_merge_touching_frames():
    assert frame_ranges(ranges((0.0, 0.1), (0.1, 0.2)), 10.0) == [FrameRange(0, 2)]


def test_frame_ranges_need_a_known_fps():
    with pytest.raises(TimeAxisError, match="帧率未知"):
        frame_ranges(ranges((0.0, 1.0)), 0.0)


def test_segments_follow_the_active_set_and_keep_gaps():
    result = segments(
        [region("r1", ranges((1.0, 2.0))), region("r2", ranges((1.5, 2.5)))],
        10.0, frames=40)
    assert result == [
        Segment(0, 10, frozenset()),
        Segment(10, 15, frozenset({"r1"})),
        Segment(15, 20, frozenset({"r1", "r2"})),
        Segment(20, 25, frozenset({"r2"})),
        Segment(25, 40, frozenset()),
    ]


def test_whole_clip_region_appears_in_every_segment():
    result = segments(
        [region("whole"), region("part", ranges((1.0, 2.0)))],
        10.0, frames=40)
    assert all("whole" in item.regions for item in result)
    assert [item.regions for item in result if "part" in item.regions] == [
        frozenset({"whole", "part"})]


def test_disabled_region_does_not_create_segments():
    result = segments(
        [region("off", ranges((1.0, 2.0)), enabled=False), region("whole")],
        10.0, frames=30)
    assert result == [Segment(0, 30, frozenset({"whole"}))]


def test_no_enabled_region_returns_no_segments():
    assert segments([], 30.0, frames=10) == []
    assert segments([region("off", enabled=False)], 30.0, frames=10) == []


def test_segments_without_ranges_are_one_whole_clip_segment():
    assert segments([region("a")], 30.0, frames=10) == [
        Segment(0, 10, frozenset({"a"}))]


def test_segments_without_frame_count_leave_the_tail_open():
    assert segments([region("a")], 30.0) == [
        Segment(0, None, frozenset({"a"}))]


def test_segments_reject_unknown_fps_when_a_range_is_active():
    with pytest.raises(TimeAxisError, match="帧率未知"):
        segments([region("a", ranges((0.0, 1.0)))], 0.0, frames=30)


def test_segments_reject_more_than_the_cap():
    # 9 个区域、每个 8 段互不相交的区间：144 个边界切出 143 段，超过上限。
    many = [
        region(f"r{index}", ranges(*[(index * 100 + slot * 10,
                                      index * 100 + slot * 10 + 1)
                                    for slot in range(8)]))
        for index in range(9)
    ]
    with pytest.raises(TimeAxisError, match=f"上限 {MAX_SEGMENTS}"):
        segments(many, 10.0, frames=8800)


def test_segment_at_reads_gaps_open_tails_and_outside():
    result = segments(
        [region("r1", ranges((1.0, 2.0))), region("r2", ranges((1.5, 2.5)))],
        10.0, frames=40)
    assert segment_at(result, 5) == Segment(0, 10, frozenset())
    assert segment_at(result, 12).regions == frozenset({"r1"})
    assert segment_at(result, 16).regions == frozenset({"r1", "r2"})
    assert segment_at(result, 30).regions == frozenset()
    assert segment_at(result, 40) is None

    open_tail = segments([region("a")], 30.0)
    assert segment_at(open_tail, 10**9) == Segment(0, None, frozenset({"a"}))
