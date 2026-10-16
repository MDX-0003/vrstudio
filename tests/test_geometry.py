"""Geometry: the closure invariant and the aspect -> P derivation.

The numbers in ``test_two_to_one_example`` / ``test_sixteen_nine_example`` are the
ones quoted in the design plan; if this module drifts, those tables become lies.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from vrstudio.redraw.geometry import (
    ClosureViolation,
    GeometryError,
    PadPlan,
    PadSpec,
    assert_closure,
    parse_aspect,
    plan_pad,
    region_slices,
    resolve_aspect,
    restore_height,
    round_even,
)

A21 = Fraction(21, 9)


def test_round_even_is_deterministic_and_even():
    assert round_even(Fraction(4480)) == 4480
    assert round_even(Fraction(4666.67)) == 4668          # 4667 -> odd -> up
    assert round_even(Fraction(2520)) == 2520
    assert round_even(Fraction(4665.5)) == 4666
    for value in (Fraction(3), Fraction(101), Fraction(2001.5)):
        assert round_even(value) % 2 == 0


def test_parse_aspect_is_exact():
    assert parse_aspect("21:9") == Fraction(21, 9)
    assert parse_aspect("2:1") == Fraction(2, 1)
    assert parse_aspect(Fraction(21, 9)) == A21
    # 21/9 is not representable in binary floating point; carrying it as float
    # can flip the round_even result between adjacent even integers.
    assert parse_aspect("21:9") * 9 == 21


def test_two_to_one_example():
    """3840x1920 @ 21:9 -> canvas 4480x1920, P=320, L=640 (plan section 4.5)."""
    plan = plan_pad(PadSpec((3840, 1920), A21))
    assert plan.canvas_size == (4480, 1920)
    assert plan.P == 320
    assert plan.L == 640
    assert plan.regions == {"G": (0, 640), "R": (640, 3840), "B": (3840, 4480)}
    assert plan.wrap_pair == (319, 320)
    assert plan.roll_delta == -320
    assert plan.source_preroll == 320
    assert plan.outer_boundary_margin_px == 640
    assert not plan.warnings


def test_sixteen_nine_example():
    """1920x1080 @ 21:9 -> canvas 2520x1080, P=300, L=600."""
    plan = plan_pad(PadSpec((1920, 1080), A21))
    assert plan.canvas_size == (2520, 1080)
    assert plan.P == 300
    assert plan.L == 600
    assert plan.regions["R"] == (600, 1920)
    assert plan.wrap_pair == (299, 300)


@pytest.mark.parametrize("size,expected_divisor", [((3840, 1920), 12), ((7680, 3840), 12)])
def test_two_to_one_gives_P_equals_W_over_12(size, expected_divisor):
    plan = plan_pad(PadSpec(size, A21))
    assert plan.P == size[0] // expected_divisor


def test_sixteen_nine_gives_P_equals_5W_over_32():
    plan = plan_pad(PadSpec((1920, 1080), A21))
    assert plan.P == 5 * 1920 // 32


def test_no_vertical_padding():
    """The target aspect is reached purely by widening: height never changes."""
    for size in ((3840, 1920), (1920, 1080), (4000, 2000)):
        plan = plan_pad(PadSpec(size, A21))
        assert plan.canvas_size[1] == size[1]


def test_pixel_budget_matches_the_plan_table():
    two_to_one = plan_pad(PadSpec((3840, 1920), A21))
    assert two_to_one.content_pixel_fraction == pytest.approx(5 / 7, abs=1e-9)   # 71.4%
    assert two_to_one.blend_fraction_of_output == pytest.approx(1 / 6, abs=1e-9)  # 16.7%

    sixteen_nine = plan_pad(PadSpec((1920, 1080), A21))
    assert sixteen_nine.content_pixel_fraction == pytest.approx(11 / 21, abs=1e-9)  # 52.4%
    assert sixteen_nine.blend_fraction_of_output == pytest.approx(5 / 16, abs=1e-9)  # 31.3%


def test_source_wider_than_target_is_an_error():
    # 3.2:1 is WIDER than 21:9 (2.333), so widening cannot reach the target
    # without cropping content -> refuse rather than silently crop
    with pytest.raises(GeometryError, match="narrower than the source"):
        plan_pad(PadSpec((3200, 1000), A21))


def test_a_narrow_source_is_fine():
    # 4:3 is narrower than 21:9, so it just gets more horizontal padding
    plan = plan_pad(PadSpec((1600, 1200), A21))
    assert plan.canvas_size == (2800, 1200)
    assert plan.P == 600


def test_grow_canvas_degenerates_loudly():
    plan = plan_pad(PadSpec((3200, 1000), A21), on_aspect_conflict="grow_canvas")
    assert plan.P == 0
    assert plan.L == 0
    assert any("NO redundancy" in w for w in plan.warnings)


def test_odd_source_is_trimmed_with_a_warning():
    plan = plan_pad(PadSpec((3841, 1921), A21))
    assert plan.requested_source_size == (3841, 1921)
    assert plan.source_size == (3840, 1920)
    assert any("trimmed" in w for w in plan.warnings)


def test_roll_delta_and_preroll_cancel():
    plan = plan_pad(PadSpec((3840, 1920), A21))
    assert plan.roll_delta + plan.source_preroll == 0


def test_preroll_can_be_disabled():
    assert plan_pad(PadSpec((3840, 1920), A21), pre_roll="none").source_preroll == 0
    assert plan_pad(PadSpec((3840, 1920), A21), pre_roll=7).source_preroll == 7


def test_assert_closure_accepts_a_good_plan():
    assert_closure(plan_pad(PadSpec((3840, 1920), A21)))


def test_assert_closure_rejects_tampered_regions():
    """The invariant has to be enforceable, not just documented."""
    good = plan_pad(PadSpec((3840, 1920), A21))
    import dataclasses

    bad = dataclasses.replace(good, regions={"G": (0, 640), "R": (641, 3840), "B": (3840, 4480)})
    with pytest.raises(ClosureViolation):
        assert_closure(bad)

    bad2 = dataclasses.replace(good, regions={"G": (0, 640), "R": (640, 3840), "B": (3839, 4480)})
    with pytest.raises(ClosureViolation):
        assert_closure(bad2)

    bad3 = dataclasses.replace(good, L=642)
    with pytest.raises(ClosureViolation):
        assert_closure(bad3)


def test_assert_closure_rejects_a_non_adjacent_wrap_pair():
    import dataclasses

    good = plan_pad(PadSpec((3840, 1920), A21))
    bad = dataclasses.replace(good, wrap_pair=(100, 101 + 1))
    with pytest.raises(ClosureViolation):
        assert_closure(bad)


def test_region_slices_are_contiguous():
    plan = plan_pad(PadSpec((3840, 1920), A21))
    slices = region_slices(plan)
    assert slices["G"] == slice(0, plan.L)
    assert slices["R"] == slice(plan.L, plan.W)
    assert slices["B"] == slice(plan.W, plan.Wc)


def test_restore_height_keeps_the_generated_aspect():
    # a 21:9 generation restored to the 4480-wide canvas is 1920 tall
    assert restore_height((2520, 1080), 4480) == 1920
    assert restore_height((4480, 1920), 4480) == 1920
    assert restore_height((1000, 429), 4480) % 2 == 0


def test_resolve_aspect_auto_skips_degenerate_buckets():
    # 2:1 exactly matches a bucket, but that bucket would give P == 0
    aspect = resolve_aspect("auto", (3840, 1920))
    assert aspect > Fraction(2, 1)
    assert plan_pad(PadSpec((3840, 1920), aspect)).P > 0


def test_resolve_aspect_passes_explicit_values_through():
    assert resolve_aspect("21:9", (3840, 1920)) == A21


def test_plan_warns_when_P_is_zero():
    plan = plan_pad(PadSpec((3840, 1920), Fraction(2, 1)))
    assert plan.P == 0
    assert any("P == 0" in w for w in plan.warnings)


def test_pad_plan_is_frozen():
    plan = plan_pad(PadSpec((3840, 1920), A21))
    assert isinstance(plan, PadPlan)
    with pytest.raises(Exception):
        plan.P = 1  # type: ignore[misc]
