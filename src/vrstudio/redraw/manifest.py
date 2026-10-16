"""Manifest: the pad -> recompose contract.

The two CLIs never share process state. Everything ``recompose`` is allowed to
assume comes from this file, which means the file has to be:

* **versioned** — ``schema`` is checked on load, so an old manifest fails loudly
  rather than being half-understood;
* **self-checking** — the source's sha256 (first 16 hex) plus frame count let
  ``recompose`` detect that it was handed a different clip's manifest;
* **geometry-bearing, not rectangle-bearing** — the regions are column ranges in
  canvas space plus the derived ``P``/``L``. There is no ``content_rect``: the
  recompose step is not a crop, it is a slice + registration + blend, and
  describing it with a rectangle is what made the previous design depend on the
  model being consistent across a full revolution.

``to_pad_plan()`` rebuilds a :class:`~vrstudio.redraw.geometry.PadPlan` so ``recompose``
can re-run ``assert_closure`` on what it was told, rather than trusting it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from .geometry import PadPlan, assert_closure

SCHEMA = 1
TOOL_NAME = "vrstudio"


def sha256_16(path: str | Path) -> str:
    """First 16 hex chars of the file's sha256 — enough to catch a mis-pairing."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SourceInfo:
    path: str
    size: tuple[int, int]                 # (W, H) as used (even-aligned)
    requested_size: tuple[int, int]
    aspect: float
    frames: int
    fps: str
    pix_fmt: str
    has_audio: bool
    sha256_16: str | None = None


@dataclass(frozen=True)
class CanvasInfo:
    target_aspect: tuple[int, int]
    target_aspect_value: float
    size: tuple[int, int]                 # (Wc, H)
    P: int
    L: int
    regions: dict[str, tuple[int, int]]
    wrap_pair: tuple[int, int]            # in SOURCE columns
    outer_boundary_margin_px: int


@dataclass(frozen=True)
class PadInfo:
    mode_h: str
    pre_roll: int
    pipeline: list[str]


@dataclass(frozen=True)
class MaskInfo:
    path: str | None
    strategy: str
    band_width: int
    feather_px: int
    png_compression: int


@dataclass(frozen=True)
class RestoreInfo:
    canvas_width: int
    height_from_aspect: bool
    interp: str


@dataclass(frozen=True)
class VideoSettings:
    encoder: str
    crf: float
    preset: str
    pix_fmt: str
    audio: str
    metadata: str


@dataclass(frozen=True)
class Manifest:
    source: SourceInfo
    canvas: CanvasInfo
    pad: PadInfo
    mask: MaskInfo
    restore: RestoreInfo
    video: VideoSettings
    padded: str | None = None      # the padded video's file name, so calib can find it
    schema: int = SCHEMA
    created_utc: str = field(default_factory=_now)
    tool_version: str = ""
    warnings: tuple[str, ...] = ()

    # ---- serialisation -------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["tool"] = {"name": TOOL_NAME, "version": self.tool_version}
        d.pop("tool_version")
        # tuples -> lists for JSON; asdict already did this for nested tuples
        return d

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def load(path: str | Path) -> "Manifest":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        schema = int(raw.get("schema", 0))
        if schema != SCHEMA:
            raise ValueError(
                f"manifest schema {schema} is not supported (this build reads {SCHEMA}); "
                f"re-run `vrstudio pad` with the current version"
            )
        try:
            return Manifest(
                schema=schema,
                created_utc=raw["created_utc"],
                tool_version=str(raw.get("tool", {}).get("version", "")),
                warnings=tuple(raw.get("warnings", ())),
                padded=raw.get("padded"),
                source=SourceInfo(
                    path=raw["source"]["path"],
                    size=tuple(raw["source"]["size"]),
                    requested_size=tuple(raw["source"].get("requested_size", raw["source"]["size"])),
                    aspect=float(raw["source"]["aspect"]),
                    frames=int(raw["source"]["frames"]),
                    fps=str(raw["source"]["fps"]),
                    pix_fmt=str(raw["source"].get("pix_fmt", "")),
                    has_audio=bool(raw["source"].get("has_audio", False)),
                    sha256_16=raw["source"].get("sha256_16"),
                ),
                canvas=CanvasInfo(
                    target_aspect=tuple(raw["canvas"]["target_aspect"]),
                    target_aspect_value=float(raw["canvas"]["target_aspect_value"]),
                    size=tuple(raw["canvas"]["size"]),
                    P=int(raw["canvas"]["P"]),
                    L=int(raw["canvas"]["L"]),
                    regions={k: tuple(v) for k, v in raw["canvas"]["regions"].items()},
                    wrap_pair=tuple(raw["canvas"]["wrap_pair"]),
                    outer_boundary_margin_px=int(raw["canvas"]["outer_boundary_margin_px"]),
                ),
                pad=PadInfo(
                    mode_h=str(raw["pad"]["mode_h"]),
                    pre_roll=int(raw["pad"]["pre_roll"]),
                    pipeline=list(raw["pad"]["pipeline"]),
                ),
                mask=MaskInfo(
                    path=raw["mask"].get("path"),
                    strategy=str(raw["mask"]["strategy"]),
                    band_width=int(raw["mask"]["band_width"]),
                    feather_px=int(raw["mask"]["feather_px"]),
                    png_compression=int(raw["mask"]["png_compression"]),
                ),
                restore=RestoreInfo(
                    canvas_width=int(raw["restore"]["canvas_width"]),
                    height_from_aspect=bool(raw["restore"]["height_from_aspect"]),
                    interp=str(raw["restore"]["interp"]),
                ),
                video=VideoSettings(**raw["video"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"manifest is missing or malformed: {exc}") from exc

    # ---- derived -------------------------------------------------------
    def to_pad_plan(self) -> PadPlan:
        """Rebuild the plan so the closure invariant can be re-asserted on load."""
        Wc, H = self.canvas.size
        W, _ = self.source.size
        plan = PadPlan(
            requested_source_size=self.source.requested_size,
            source_size=self.source.size,
            target_aspect=Fraction(*self.canvas.target_aspect),
            canvas_size=(Wc, H),
            P=self.canvas.P,
            L=self.canvas.L,
            regions={k: tuple(v) for k, v in self.canvas.regions.items()},
            wrap_pair=self.canvas.wrap_pair,
            outer_boundary_margin_px=self.canvas.outer_boundary_margin_px,
            roll_delta=-self.canvas.P,
            source_preroll=self.pad.pre_roll,
            blend_fraction_of_output=self.canvas.L / W if W else 0.0,
            content_pixel_fraction=(W - self.canvas.L) / Wc if Wc else 0.0,
            warnings=self.warnings,
        )
        assert_closure(plan)
        return plan

    def check_source_hash(self, path: str | Path) -> None:
        """Raise if ``path`` is not the clip this manifest was written for."""
        if not self.source.sha256_16:
            return
        actual = sha256_16(path)
        if actual != self.source.sha256_16:
            raise ValueError(
                f"source hash mismatch: manifest expects {self.source.sha256_16}, "
                f"{path} is {actual}. This manifest belongs to a different clip."
            )


def from_plan(
    plan: PadPlan,
    *,
    source_path: str | Path,
    padded_name: str | None = None,
    source_frames: int,
    source_fps: str,
    source_pix_fmt: str,
    has_audio: bool,
    write_hash: bool = True,
    mask_path: str | None,
    mask_strategy: str,
    mask_feather_px: int,
    mask_png_compression: int,
    restore_interp: str,
    video: VideoSettings,
    tool_version: str,
) -> Manifest:
    W, H = plan.source_size
    return Manifest(
        source=SourceInfo(
            path=Path(source_path).name,
            size=(W, H),
            requested_size=plan.requested_source_size,
            aspect=W / H if H else 0.0,
            frames=source_frames,
            fps=source_fps,
            pix_fmt=source_pix_fmt,
            has_audio=has_audio,
            sha256_16=sha256_16(source_path) if write_hash else None,
        ),
        canvas=CanvasInfo(
            target_aspect=(plan.target_aspect.numerator, plan.target_aspect.denominator),
            target_aspect_value=float(plan.target_aspect),
            size=plan.canvas_size,
            P=plan.P,
            L=plan.L,
            regions=plan.regions,
            wrap_pair=plan.wrap_pair,
            outer_boundary_margin_px=plan.outer_boundary_margin_px,
        ),
        pad=PadInfo(mode_h="wrap", pre_roll=plan.source_preroll, pipeline=[]),
        mask=MaskInfo(
            path=mask_path,
            strategy=mask_strategy,
            band_width=plan.L,
            feather_px=mask_feather_px,
            png_compression=mask_png_compression,
        ),
        restore=RestoreInfo(
            canvas_width=plan.Wc,
            height_from_aspect=True,
            interp=restore_interp,
        ),
        video=video,
        padded=padded_name,
        tool_version=tool_version,
        warnings=plan.warnings,
    )
