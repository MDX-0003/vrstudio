"""pick 页工作草稿：``derived/pick/draft.toml``。

它不是契约，CLI 不直接读；高频几何只写这里，``case.toml`` 只存显式保存的
``[[range_preset]]``。草稿带 ``source_sha256``，源片换了就回退 active preset。
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import case as case_mod

log = logging.getLogger(__name__)


@dataclass
class PickDraft:
    source_sha256: str
    updated_at: str
    viewport: dict
    inner: dict
    frames: dict | None = None

    def payload(self) -> dict:
        return {
            "viewport": dict(self.viewport),
            "inner": dict(self.inner),
            "frames": dict(self.frames) if self.frames is not None else None,
            "updated_at": self.updated_at,
        }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def draft_path(case: case_mod.Case, root: str | Path | None = None) -> Path:
    return case.dir(root) / "derived" / "pick" / "draft.toml"


def _s(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _kv(key: str, value) -> str:
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{key} = {value}"
    if isinstance(value, float):
        return f"{key} = {value!r}"
    return f"{key} = {_s(str(value))}"


def _write_section(lines: list[str], name: str, value: dict | None) -> None:
    if value is None:
        return
    lines += ["", f"[{name}]"]
    lines += [_kv(key, item) for key, item in value.items()]


def save_draft(case: case_mod.Case, root: str | Path | None, *,
               viewport: dict, inner: dict, frames: dict | None = None,
               updated_at: str | None = None) -> PickDraft:
    """写工作草稿；临时文件替换，避免读到半截 TOML。"""
    draft = PickDraft(
        source_sha256=case.sha256 or "",
        updated_at=updated_at or _now_iso(),
        viewport=dict(viewport), inner=dict(inner),
        frames=dict(frames) if frames is not None else None,
    )
    lines = [f"source_sha256 = {_s(draft.source_sha256)}",
             f"updated_at = {_s(draft.updated_at)}"]
    _write_section(lines, "viewport", draft.viewport)
    _write_section(lines, "inner", draft.inner)
    if draft.frames is not None:
        _write_section(lines, "frames", draft.frames)
    path = draft_path(case, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return draft


def load_draft(case: case_mod.Case, root: str | Path | None = None) -> PickDraft | None:
    """读草稿；文件缺失、损坏或源哈希不匹配时回退 active preset。"""
    path = draft_path(case, root)
    if not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("pick draft 读取失败，回退 active preset：%s（%s）", path, exc)
        return None
    source = raw.get("source_sha256")
    if case.sha256 and source and source != case.sha256:
        log.warning("pick draft 的源哈希与 case 不一致，忽略：%s", path)
        return None
    viewport = raw.get("viewport")
    inner = raw.get("inner")
    frames = raw.get("frames")
    if not isinstance(viewport, dict) or not isinstance(inner, dict):
        log.warning("pick draft 缺 viewport/inner，回退 active preset：%s", path)
        return None
    if frames is not None and not isinstance(frames, dict):
        log.warning("pick draft 的 frames 不是表，回退 active preset：%s", path)
        return None
    updated = raw.get("updated_at")
    return PickDraft(
        source_sha256=str(source or ""),
        updated_at=str(updated or ""),
        viewport=dict(viewport), inner=dict(inner),
        frames=dict(frames) if frames is not None else None,
    )


def draft_from_preset(case: case_mod.Case, preset_id: str | None = None) -> PickDraft | None:
    """把 active（或指定）preset 复制成一份草稿对象；没有 preset 时 None。"""
    if not case.range_presets:
        return None
    try:
        preset = case.range_preset(preset_id)
    except case_mod.CaseError:
        return None
    return PickDraft(
        source_sha256=case.sha256 or "",
        updated_at=preset.updated_at,
        viewport=dict(preset.viewport), inner=dict(preset.inner),
        frames=dict(preset.frames) if preset.frames is not None else None,
    )


def _norm_viewport(value: dict) -> tuple:
    return (
        round(float(value.get("yaw_deg", 0.0)), 2),
        round(float(value.get("pitch_deg", 0.0)), 2),
        round(float(value.get("fov_h_deg", 0.0)), 1),
        int(value.get("width", 0)),
        int(value.get("height", 0)),
    )


def _norm_inner(value: dict) -> tuple:
    return tuple(int(value.get(key, 0)) for key in ("x", "y", "width", "height"))


def _norm_frames(value: dict | None) -> tuple | None:
    if value is None:
        return None
    return (int(value.get("start", 0)), int(value.get("end", 0)))


def same_geometry(preset: case_mod.RangePreset, draft: PickDraft) -> bool:
    return (
        _norm_viewport(preset.viewport) == _norm_viewport(draft.viewport)
        and _norm_inner(preset.inner) == _norm_inner(draft.inner)
        and _norm_frames(preset.frames) == _norm_frames(draft.frames)
    )


def draft_dirty(case: case_mod.Case, draft: PickDraft | None) -> bool:
    if draft is None:
        return False
    if not case.range_presets:
        return True
    try:
        active = case.range_preset()
    except case_mod.CaseError:
        return True
    return not same_geometry(active, draft)
