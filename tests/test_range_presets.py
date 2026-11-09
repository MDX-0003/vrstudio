"""选区 preset：case.toml 列表、迁移、CLI --preset/--new 与合并输入。"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from vrstudio import case as case_mod
from vrstudio.cli import assemble as assemble_cli
from vrstudio.cli import case as case_cli
from vrstudio.cli import extract as extract_cli
from vrstudio.cli import merge as merge_cli

runner = CliRunner()

VIEWPORT = {"yaw_deg": 10.0, "pitch_deg": -5.0, "fov_h_deg": 90.0,
            "width": 320, "height": 180}
INNER = {"x": 80, "y": 40, "width": 160, "height": 100}
FRAMES = {"start": 0, "end": 5}
VIEWPORT_B = {"yaw_deg": -20.0, "pitch_deg": 3.0, "fov_h_deg": 70.0,
              "width": 320, "height": 180}
INNER_B = {"x": 20, "y": 20, "width": 120, "height": 80}
FRAMES_B = {"start": 2, "end": 5}


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake erp bytes")
    return src


def _patch(tmp_path: Path) -> case_mod.Case:
    return case_mod.create("p1", "patch", _source(tmp_path), root=tmp_path)


def _add(case_name: str, tmp_path: Path, *, viewport=VIEWPORT, inner=INNER,
         frames=FRAMES) -> case_mod.Case:
    return case_mod.add_range_preset(
        case_name, viewport=viewport, inner=inner, frames=frames, root=tmp_path)


def test_add_save_activate_delete_presets(tmp_path):
    _patch(tmp_path)
    case = _add("p1", tmp_path)
    assert case.active_range == "r1"
    assert len(case.range_presets) == 1
    assert case.range_presets[0].viewport == VIEWPORT

    case = _add("p1", tmp_path, viewport=VIEWPORT_B, inner=INNER_B, frames=FRAMES_B)
    assert case.active_range == "r2"
    assert [p.id for p in case.range_presets] == ["r1", "r2"]

    case = case_mod.save_range_preset(
        "p1", "r1", viewport=VIEWPORT_B, inner=INNER_B, frames=FRAMES,
        root=tmp_path)
    assert case.active_range == "r1"
    assert case.viewport == VIEWPORT_B
    assert case.range_presets[0].updated_at

    case = case_mod.set_active_range("p1", "r2", root=tmp_path)
    assert case.active_range == "r2" and case.viewport == VIEWPORT_B
    case = case_mod.delete_range_preset("p1", "r2", root=tmp_path)
    assert case.active_range == "r1" and [p.id for p in case.range_presets] == ["r1"]
    case = case_mod.delete_range_preset("p1", "r1", root=tmp_path)
    assert case.active_range is None and case.range_presets == []
    assert case.viewport is None and case.inner is None and case.frames is None


def test_patchimg_presets_have_no_frames(tmp_path):
    src = tmp_path / "pano.png"
    cv2.imwrite(str(src), np.zeros((4, 8, 3), dtype=np.uint8))
    case_mod.create("img", "patchimg", src, root=tmp_path)
    case = case_mod.add_range_preset(
        "img", viewport=VIEWPORT, inner=INNER, root=tmp_path)
    assert case.range_presets[0].frames is None
    with pytest.raises(case_mod.CaseError, match="frames"):
        case_mod.add_range_preset(
            "img", viewport=VIEWPORT, inner=INNER, frames=FRAMES, root=tmp_path)


def test_legacy_top_level_migrates_on_save(tmp_path):
    src = _source(tmp_path)
    digest = case_mod.file_sha256(src)
    path = case_mod.case_toml("legacy", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        "schema = 1\n"
        'flow = "patch"\n\n'
        "[source]\n"
        f"path = {json.dumps(str(src))}\n"
        f"sha256 = {json.dumps(digest)}\n\n"
        "[viewport]\n"
        "yaw_deg = 1.0\n"
        "pitch_deg = 2.0\n"
        "fov_h_deg = 80.0\n"
        "width = 1920\n"
        "height = 1080\n\n"
        "[inner]\n"
        "x = 10\n"
        "y = 20\n"
        "width = 300\n"
        "height = 400\n\n"
        "[frames]\n"
        "start = 1\n"
        "end = 9\n",
        encoding="utf-8",
    )
    case = case_mod.load("legacy", tmp_path)
    assert case.legacy_draft is True
    assert case.active_range == "r1"
    assert case.range_presets[0].viewport["yaw_deg"] == 1.0
    case_mod.save(case, tmp_path)

    text = path.read_text(encoding="utf-8")
    assert "[[range_preset]]" in text and '[pick]\nactive = "r1"' in text
    assert "\n[viewport]\n" not in text and "\n[inner]\n" not in text
    assert list(path.parent.glob("case.toml.bak-*"))
    back = case_mod.load("legacy", tmp_path)
    assert back.viewport == {"yaw_deg": 1.0, "pitch_deg": 2.0, "fov_h_deg": 80.0,
                             "width": 1920, "height": 1080}
    assert back.frames == {"start": 1, "end": 9}


def test_mixed_legacy_and_presets_is_rejected(tmp_path):
    _patch(tmp_path)
    _add("p1", tmp_path)
    path = case_mod.case_toml("p1", tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\n[viewport]\nyaw_deg = 1.0\n",
                    encoding="utf-8")
    with pytest.raises(case_mod.CaseError, match="不能同时"):
        case_mod.load("p1", tmp_path)


def test_active_missing_or_dangling_is_rejected(tmp_path):
    _patch(tmp_path)
    case = _add("p1", tmp_path)
    # 用 save 直接写非法 active 会被检查拒绝。
    case.active_range = "r9"
    with pytest.raises(case_mod.CaseError, match="r9"):
        case_mod.save(case, tmp_path)


def test_extract_cli_lists_presets_when_missing(tmp_path):
    _patch(tmp_path)
    _add("p1", tmp_path)
    r = runner.invoke(extract_cli.app,
                      ["name", "--case", "p1", "--cases-dir", str(tmp_path),
                       "--preset", "r9"])
    assert r.exit_code == 2 and "r1" in r.output


def test_case_draft_new_appends_preset(tmp_path):
    _patch(tmp_path)
    _add("p1", tmp_path)
    payload = tmp_path / "draft.json"
    payload.write_text(json.dumps({"viewport": VIEWPORT_B, "inner": INNER_B,
                                   "frames": FRAMES_B}), encoding="utf-8")
    r = runner.invoke(case_cli.app,
                      ["draft", "--case", "p1", "--file", str(payload),
                       "--new", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    case = case_mod.load("p1", tmp_path)
    assert [p.id for p in case.range_presets] == ["r1", "r2"]
    assert case.active_range == "r2" and case.viewport == VIEWPORT_B


def test_patch_merge_input_uses_asset_and_falls_back(tmp_path):
    _patch(tmp_path)
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    d = case_mod.case_dir("p1", tmp_path) / "extracts" / "v1"
    d.mkdir(parents=True)
    (d / "clip.json").write_bytes(b"{}")
    (d / "result.mp4").write_bytes(b"r")
    video = tmp_path / "external.mp4"
    video.write_bytes(b"external video")

    case, asset_id = case_mod.add_merge_input("p1", video, root=tmp_path)
    assert case.merge_input == asset_id
    assert case.merge_input_file(tmp_path) == case.asset(asset_id).resolved(case, tmp_path)

    case = case_mod.set_merge_input("p1", None, root=tmp_path)
    assert case.merge_input is None
    assert case.merge_input_file(tmp_path) == d / "result.mp4"


def test_merge_and_assemble_resolve_explicit_version(tmp_path):
    _patch(tmp_path)
    case_mod.set_extract_current("p1", "v2", root=tmp_path)
    d = case_mod.case_dir("p1", tmp_path) / "extracts" / "v1"
    d.mkdir(parents=True)
    (d / "clip.json").write_bytes(b"{}")
    (d / "result.mp4").write_bytes(b"r")
    out = case_mod.case_dir("p1", tmp_path) / "out"
    out.mkdir()

    got = merge_cli.resolve_case_inputs("p1", tmp_path, "v1")
    assert got["ai"] == d / "result.mp4"
    assert got["sidecar"] == d / "clip.json"
    assert got["output"] == out / "v1_space.mp4"

    got = assemble_cli.resolve_case_inputs("p1", tmp_path, "v1")
    assert got["space"] == out / "v1_space.mp4"
    assert got["output"] == out / "v1_frame.mp4"
