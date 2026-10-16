"""Recompose report: the audit trail.

Three things can make a recomposed panorama wrong, and they are indistinguishable
from the output alone:

1. the tool did not preserve the canvas framing, so the regions were sliced in the
   wrong place (``registration.mae_min`` vs the interior baseline);
2. the registration found a coincidental match (``registration.quality``);
3. the model rendered the two redundant bands inconsistently, so the blend carries
   a ghost (also ``mae_min``).

Writing these down is what lets a later reader tell them apart without re-running
the AIGC tool — which is the only step that cannot be repeated cheaply.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1


@dataclass(frozen=True)
class RegistrationReport:
    method: str
    search_px: int
    d_star: int
    d_star_mode: str
    frames_used: int
    mae_min: float                 # mean-removed structural residual at d* (levels)
    mae_second: float
    quality: float                 # mae_second / mae_min; ~1 means a flat, untrustworthy curve
    correlation: float             # Pearson r between the aligned bands -- the gate's input
    interior_baseline: float       # the canvas's own adjacent-column mean, for context
    verdict: str                   # ok | warn | fail


@dataclass(frozen=True)
class BlendReport:
    weight: str
    endpoint_lock_px: int


@dataclass(frozen=True)
class RepairReport:
    enabled: bool
    T: int


@dataclass(frozen=True)
class RecomposeReport:
    manifest_path: str
    input_path: str
    input_size: tuple[int, int]
    input_frames: int
    input_fps: str
    restored_canvas: tuple[int, int]
    restore_interp: str
    registration: RegistrationReport
    blend: BlendReport
    repair: RepairReport
    aspect_drift_pct: float = 0.0   # tool's returned aspect vs the canvas, in implied height
    verify: dict = field(default_factory=dict)
    output_path: str = ""
    output_frames: int = 0
    warnings: tuple[str, ...] = ()
    schema: int = SCHEMA
    created_utc: str = field(default_factory=lambda: datetime.now(timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ"))

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def load(path: str | Path) -> "RecomposeReport":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if int(raw.get("schema", 0)) != SCHEMA:
            raise ValueError(f"unsupported report schema {raw.get('schema')!r}")
        return RecomposeReport(
            manifest_path=raw["manifest_path"],
            input_path=raw["input_path"],
            input_size=tuple(raw["input_size"]),
            input_frames=int(raw["input_frames"]),
            input_fps=str(raw["input_fps"]),
            restored_canvas=tuple(raw["restored_canvas"]),
            restore_interp=str(raw["restore_interp"]),
            registration=RegistrationReport(**raw["registration"]),
            blend=BlendReport(**raw["blend"]),
            repair=RepairReport(**raw["repair"]),
            aspect_drift_pct=float(raw.get("aspect_drift_pct", 0.0)),
            verify=dict(raw.get("verify", {})),
            output_path=raw.get("output_path", ""),
            output_frames=int(raw.get("output_frames", 0)),
            warnings=tuple(raw.get("warnings", ())),
        )
