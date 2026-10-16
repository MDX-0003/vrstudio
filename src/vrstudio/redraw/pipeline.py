"""The pad-side pluggable point: a frame transform chain assembled from config.

Adding a step (burn a calibration marker, pre-correct colour, a future roll) means
registering one class here and naming it in ``pad.pipeline.transforms`` — no
caller changes. The chain is the reason ``pad`` has no hard-coded pixel loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

import numpy as np

from .geometry import PadPlan
from .padding import pad_horizontal
from .preroll import pre_roll


@dataclass(frozen=True)
class TransformContext:
    """Everything a transform may need. Deliberately just the plan."""

    plan: PadPlan


class FrameTransform(Protocol):
    name: ClassVar[str]

    def __call__(self, frame: np.ndarray, ctx: TransformContext) -> np.ndarray: ...


class PreRoll:
    """Cancel the output rotation the recompose step is guaranteed to introduce."""

    name: ClassVar[str] = "pre_roll"

    def __call__(self, frame: np.ndarray, ctx: TransformContext) -> np.ndarray:
        return pre_roll(frame, ctx.plan.source_preroll)


class PadH:
    """True circular horizontal padding to the canvas width."""

    name: ClassVar[str] = "pad_h"

    def __call__(self, frame: np.ndarray, ctx: TransformContext) -> np.ndarray:
        return pad_horizontal(frame, ctx.plan.P)


#: Registry. Names here are the only legal values in ``pad.pipeline.transforms``.
TRANSFORMS: dict[str, type] = {t.name: t for t in (PreRoll, PadH)}


def allowed_names() -> tuple[str, ...]:
    return tuple(TRANSFORMS)


def build_chain(names: list[str]) -> list[FrameTransform]:
    """Instantiate the chain in order, naming the offender on a typo."""
    chain: list[FrameTransform] = []
    for name in names:
        cls = TRANSFORMS.get(name)
        if cls is None:
            raise ValueError(
                f"unknown transform {name!r}; registered: {', '.join(allowed_names())}"
            )
        chain.append(cls())
    return chain


def apply_chain(frame: np.ndarray, chain: list[FrameTransform], ctx: TransformContext) -> np.ndarray:
    for transform in chain:
        frame = transform(frame, ctx)
    return frame
