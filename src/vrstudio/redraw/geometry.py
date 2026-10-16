"""Padding geometry: the closed-loop arithmetic that keeps the ERP wrap closed.

Why this module is pure, and why it is the one thing that must not drift:

The pipeline's correctness is a *geometric invariant*, not a quality target. The
value handed to the AIGC tool is the source panorama plus ``P`` columns of true
circular redundancy on each side; the value taken back is a window of that
canvas re-wrapped so that the output's wrap pair lands on an **adjacent** column
pair of the model's own raster. If this arithmetic is off by one column the
result is still a plausible-looking panorama with a broken seam — precisely the
failure this tool exists to prevent. So the arithmetic lives here, has no I/O,
and is asserted (``assert_closure``) before a single pixel is touched.

Coordinate systems
------------------
``source`` space
    columns ``[0, W)`` — the ERP frame the pipeline starts from.
``canvas`` space
    columns ``[0, Wc)`` — source plus ``P`` columns of circular redundancy each
    side, related to source space by the padding identity::

        canvas[:, j] == source[(j - P) mod W]                       (*)

(*) is why ``mode_h`` can only be ``"wrap"``: every downstream boundary
condition follows from it — in particular that ``G`` and ``B`` are two
renderings of the same content, so the blend's two endpoints are adjacent
canvas columns and the output's wrap closes without relying on the model being
consistent over a full revolution of raster distance.

Output orientation
------------------
The identity-model round trip is ``out == roll(source, -P)``: the output is the
source rotated by ``P`` columns. ``source_preroll`` cancels that so the
delivered panorama keeps the source's own front. It has to be applied *before*
padding — rolling after ``recompose`` would move the wrap pair again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

#: Standard aspect buckets offered by ``target_aspect = "auto"``, ascending.
STANDARD_ASPECTS: tuple[Fraction, ...] = (
    Fraction(1, 1),
    Fraction(5, 4),
    Fraction(4, 3),
    Fraction(3, 2),
    Fraction(16, 9),
    Fraction(2, 1),
    Fraction(21, 9),
    Fraction(3, 1),
    Fraction(4, 1),
)

ConflictPolicy = Literal["error", "grow_canvas"]
PreRollPolicy = Literal["auto", "none"]


class GeometryError(ValueError):
    """The requested geometry cannot be satisfied without cropping content."""


class ClosureViolation(AssertionError):
    """The wrap pair does not land on an adjacent column pair of the canvas."""


@dataclass(frozen=True)
class PadSpec:
    """What the caller asks for. ``P`` is deliberately absent: it is derived."""

    source_size: tuple[int, int]          # (W, H)
    target_aspect: Fraction
    even_dimensions: bool = True


@dataclass(frozen=True)
class PadPlan:
    """Everything both CLIs need to agree on, derived from a :class:`PadSpec`."""

    requested_source_size: tuple[int, int]
    source_size: tuple[int, int]           # even-aligned size actually used
    target_aspect: Fraction
    canvas_size: tuple[int, int]           # (Wc, H)
    P: int                                 # per-side circular redundancy
    L: int                                 # mask band width == 2P
    regions: dict[str, tuple[int, int]]    # {"G","R","B"} -> half-open column ranges
    wrap_pair: tuple[int, int]             # in SOURCE columns, adjacent pair
    outer_boundary_margin_px: int          # wrap pair -> canvas raster boundary == L
    roll_delta: int                        # identity round trip rolls by this
    source_preroll: int                    # applied before padding to cancel it
    blend_fraction_of_output: float
    content_pixel_fraction: float
    warnings: tuple[str, ...] = field(default=())

    @property
    def W(self) -> int:
        return self.source_size[0]

    @property
    def H(self) -> int:
        return self.source_size[1]

    @property
    def Wc(self) -> int:
        return self.canvas_size[0]


def round_even(value: Fraction | int | float) -> int:
    """Nearest integer, then pushed up to even.

    yuv420p needs even dimensions. Rounding *up* after the nearest-integer step
    guarantees the canvas is never narrower than the target aspect requires, and
    makes the result independent of binary floating point (callers pass a
    ``Fraction``).
    """
    if not isinstance(value, Fraction):
        value = Fraction(value).limit_denominator(10**9)
    n = int((value + Fraction(1, 2)).__floor__())     # round half up
    return n if n % 2 == 0 else n + 1


def parse_aspect(text: str | Fraction) -> Fraction:
    """``"21:9"`` / ``"2.3333"`` / ``Fraction`` -> exact ``Fraction``.

    Exact rationals matter: ``21/9`` is not representable in binary floating
    point, so an aspect carried as ``float`` can flip the ``round_even`` result
    between two adjacent even integers.
    """
    if isinstance(text, Fraction):
        return text
    s = str(text).strip()
    if ":" in s:
        a, _, b = s.partition(":")
        return Fraction(int(a), int(b))
    return Fraction(s).limit_denominator(10**6)


def resolve_aspect(text: str, source_size: tuple[int, int]) -> Fraction:
    """Resolve ``"auto"`` to the next standard bucket that yields ``P > 0``."""
    if str(text).strip().lower() != "auto":
        return parse_aspect(text)
    W, H = source_size
    want = Fraction(W, H)
    for bucket in STANDARD_ASPECTS:
        if bucket >= want and plan_pad(PadSpec(source_size, bucket)).P > 0:
            return bucket
    raise GeometryError(
        f"no standard aspect bucket is wider than the source {W}x{H} "
        f"(aspect {float(want):.4f}); set target_aspect explicitly"
    )


def plan_pad(
    spec: PadSpec,
    on_aspect_conflict: ConflictPolicy = "error",
    pre_roll: PreRollPolicy | int = "auto",
) -> PadPlan:
    """Derive the full canvas geometry. See the module docstring for the invariant.

    ``P`` is a function of ``target_aspect`` alone:

    * 2:1 source, 21:9 target  -> ``P = W/12``  (8.33% per side)
    * 16:9 source, 21:9 target -> ``P = 5W/32`` (15.63% per side)

    There is deliberately no vertical padding: the target aspect is reached
    purely by widening, so the canvas keeps the source's height and every row is
    real content.
    """
    warnings: list[str] = []
    W_req, H_req = (int(spec.source_size[0]), int(spec.source_size[1]))
    if W_req <= 0 or H_req <= 0:
        raise GeometryError(f"bad source size {W_req}x{H_req}")

    W, H = W_req, H_req
    if spec.even_dimensions:
        W -= W % 2
        H -= H % 2
        if (W, H) != (W_req, H_req):
            warnings.append(
                f"source trimmed to even dimensions for yuv420p: "
                f"{W_req}x{H_req} -> {W}x{H}"
            )

    Wc = round_even(H * spec.target_aspect)
    if Wc < W:
        if on_aspect_conflict == "grow_canvas":
            Wc = W
            warnings.append(
                "target aspect is narrower than the source; canvas grown to the "
                "source width, so P == 0 and there is NO redundancy "
                "(the wrap closure guarantee does not hold)"
            )
        else:
            need = Fraction(W, H)
            raise GeometryError(
                f"target aspect {spec.target_aspect} ({float(spec.target_aspect):.4f}) "
                f"is narrower than the source {W}x{H} (aspect {float(need):.4f}); "
                f"widening is the only way to pad without cropping content. "
                f"Use target_aspect >= {need} or on_aspect_conflict='grow_canvas'."
            )

    if (Wc - W) % 2:
        raise GeometryError(f"canvas {Wc} minus source {W} is odd; cannot pad symmetrically")

    P = (Wc - W) // 2
    L = 2 * P
    if P == 0:
        warnings.append(
            "P == 0: no circular redundancy, this tool degenerates to a plain copy "
            "and the wrap closure guarantee does not hold"
        )

    regions = {
        "G": (0, L),            # left redundant band
        "R": (L, W),            # main content, width W - 2P
        "B": (W, Wc),           # right redundant band
    }

    # Identity round trip: out == roll(source, -P).
    roll_delta = -P
    if pre_roll == "auto":
        preroll = P
    elif pre_roll == "none":
        preroll = 0
    else:
        preroll = int(pre_roll)

    plan = PadPlan(
        requested_source_size=(W_req, H_req),
        source_size=(W, H),
        target_aspect=spec.target_aspect,
        canvas_size=(Wc, H),
        P=P,
        L=L,
        regions=regions,
        wrap_pair=((P - 1) % W, P % W) if W else (0, 0),
        outer_boundary_margin_px=L,
        roll_delta=roll_delta,
        source_preroll=preroll,
        blend_fraction_of_output=(L / W) if W else 0.0,
        content_pixel_fraction=((W - L) / Wc) if Wc else 0.0,
        warnings=tuple(warnings),
    )
    assert_closure(plan)
    return plan


def assert_closure(plan: PadPlan) -> None:
    """Assert the invariant that makes the output's wrap close.

    Two things are checked, and both are load-bearing:

    1. the three regions tile the canvas exactly (so the slices are the ones the
       blend math assumes);
    2. the two canvas columns that feed the output's wrap are adjacent — the
       output's first column comes from canvas column ``L`` (``R[0]``) and its
       last column from canvas column ``L - 1`` (the blend's right end, which the
       endpoint lock pins to unshifted ``G``).
    """
    G, R, B = plan.regions["G"], plan.regions["R"], plan.regions["B"]
    W, Wc, L, P = plan.W, plan.Wc, plan.L, plan.P

    if plan.L != 2 * plan.P:
        raise ClosureViolation(f"L={plan.L} must be 2P={2 * plan.P}")
    if G[0] != 0 or G[1] != R[0] or R[1] != B[0] or B[1] != Wc:
        raise ClosureViolation(f"regions do not tile the canvas: G={G} R={R} B={B} Wc={Wc}")
    if R[1] - R[0] != W - 2 * P:
        raise ClosureViolation(f"R width {R[1] - R[0]} != W-2P = {W - 2 * P}")
    for name in ("G", "B"):
        if plan.regions[name][1] - plan.regions[name][0] != L:
            raise ClosureViolation(f"{name} width != L = {L}")

    # out[0] <- canvas col L ; out[-1] <- canvas col L-1
    if R[0] != L:
        raise ClosureViolation(f"R must start at canvas column L={L}, got {R[0]}")

    # the wrap pair must be adjacent on the source circle
    a, b = plan.wrap_pair
    if (b - a) % W != 1:
        raise ClosureViolation(f"wrap pair {plan.wrap_pair} is not adjacent modulo W={W}")


def restore_height(generated_size: tuple[int, int], canvas_width: int) -> int:
    """Height of the restored canvas: keep the generated aspect, force canvas width.

    The AIGC tool is free to change resolution; only its *aspect* is assumed.
    Restoring to canvas width makes the region boundaries land on integer
    columns, which is the whole reason this step exists.
    """
    gw, gh = generated_size
    if gw <= 0 or gh <= 0:
        raise GeometryError(f"bad generated size {gw}x{gh}")
    return round_even(Fraction(gh * canvas_width, gw))


def region_slices(plan: PadPlan) -> dict[str, slice]:
    return {name: slice(a, b) for name, (a, b) in plan.regions.items()}
