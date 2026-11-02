"""fullRedrawImg: 2:1 ERP -> 21:9 canvas + atmosphere refs -> stitch back to 2:1."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
from typer.testing import CliRunner

from tests.conftest import synthetic_erp
from vrstudio import case as case_mod
from vrstudio.case import CaseError
from vrstudio.cli import case as case_cli
from vrstudio.cli import extract as extract_cli
from vrstudio.cli import generate as generate_cli
from vrstudio.cli import merge as merge_cli
from vrstudio.media.imageio import read_image, write_png

runner = CliRunner()


def _source(tmp_path, name: str = "src.png", width: int = 384, height: int = 192):
    path = tmp_path / name
    write_png(path, synthetic_erp(width, height))
    return path


def _extract(tmp_path):
    return runner.invoke(extract_cli.app,
                         ["name", "--case", "p", "--cases-dir", str(tmp_path)])


def _prepared_case(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "fullRedrawImg", src, root=tmp_path)
    result = _extract(tmp_path)
    assert result.exit_code == 0, result.output
    return src


def _version_dir(tmp_path):
    case = case_mod.load("p", tmp_path)
    return case_mod.case_dir("p", tmp_path) / "extracts" / case.extract_current


def test_create_rejects_non_2_to_1(tmp_path):
    path = tmp_path / "flat.png"
    write_png(path, np.zeros((100, 160, 3), np.uint8))
    with pytest.raises(CaseError, match="2:1"):
        case_mod.create("p", "fullRedrawImg", path, root=tmp_path)


def test_create_rejects_a_video_source(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a video")
    with pytest.raises(CaseError, match="图片"):
        case_mod.create("p", "fullRedrawImg", video, root=tmp_path)


def test_case_roundtrip_has_no_draft(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "fullRedrawImg", src, root=tmp_path)
    case_mod.set_extract_current("p", "v1", root=tmp_path)
    case_mod.set_adopt("p", "img-x", root=tmp_path, index=2)

    case = case_mod.load("p", tmp_path)
    assert case.flow == "fullRedrawImg"
    assert case.viewport is None and case.inner is None and case.frames is None
    assert case.extract_current == "v1"
    assert case.adopt_index == 2

    raw = (case_mod.case_dir("p", tmp_path) / "case.toml").read_text(encoding="utf-8")
    assert "[extract]" in raw and "[viewport]" not in raw


def test_rejects_a_viewport_section(tmp_path):
    src = _source(tmp_path)
    case_mod.create("p", "fullRedrawImg", src, root=tmp_path)
    toml = case_mod.case_dir("p", tmp_path) / "case.toml"
    toml.write_text(toml.read_text(encoding="utf-8")
                    + "\n[viewport]\nyaw_deg = 0.0\n", encoding="utf-8")
    with pytest.raises(CaseError, match="viewport"):
        case_mod.load("p", tmp_path)


def test_case_new_seeds_no_draft(tmp_path):
    src = _source(tmp_path)
    result = runner.invoke(case_cli.app, [
        "new", "p", "--flow", "fullRedrawImg", "--source", str(src),
        "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    case = case_mod.load("p", tmp_path)
    assert case.viewport is None and case.extract_current is None


def test_extract_writes_canvas_and_mask(tmp_path):
    _prepared_case(tmp_path)
    version = _version_dir(tmp_path)

    for name in ("padded.png", "padded_mask.png", "image_pad.json"):
        assert (version / name).is_file(), name
    assert not (version / "regions.json").exists()
    assert read_image(version / "padded.png").shape[:2] == (192, 448)

    manifest = json.loads((version / "image_pad.json").read_text(encoding="utf-8"))
    assert manifest["canvas"]["size"] == [448, 192]
    mask = read_image(version / "padded_mask.png")[:, :, 0]
    assert mask.max() == 255 and mask.min() == 0


def _attach_atmosphere(tmp_path, src):
    case_mod.add_assets("p", [(src, "lighting_ref")], root=tmp_path)
    case_mod.set_submit_selected("p", ["i1"], root=tmp_path)


def test_submit_dry_run_lists_base_mask_and_atmosphere(tmp_path):
    src = _prepared_case(tmp_path)
    _attach_atmosphere(tmp_path, src)

    result = runner.invoke(generate_cli.app, [
        "submit", "--case", "p", "--dry-run", "--num", "4",
        "--resolution", "4K", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "fullRedrawImg" in result.output
    assert "padded.png" in result.output
    assert "padded_mask.png" in result.output
    assert "lighting_ref" in result.output
    assert result.output.count("--image\n") == 3
    assert "detail_ref" not in result.output
    assert "--canvas\n  448x192" in result.output
    assert "--source\n  384x192" in result.output
    assert "21:9" in result.output


def test_submit_rejects_more_than_the_ref_cap(tmp_path):
    src = _prepared_case(tmp_path)
    _attach_atmosphere(tmp_path, src)

    result = runner.invoke(generate_cli.app, [
        "submit", "--case", "p", "--dry-run", "--cases-dir", str(tmp_path),
        "--set", "generate.image_max_refs=2"])
    assert result.exit_code != 0
    assert "2 张上限" in result.output


def test_merge_returns_the_source_bit_exactly(tmp_path):
    src = _prepared_case(tmp_path)
    version = _version_dir(tmp_path)
    shutil.copyfile(version / "padded.png", version / "result.png")

    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    case = case_mod.load("p", tmp_path)
    out = (case_mod.case_dir("p", tmp_path) / "out"
           / f"{case.extract_current}_image.png")
    assert out.is_file()
    assert np.array_equal(read_image(out), read_image(src))

    report = json.loads((version / "report.json").read_text(encoding="utf-8"))
    assert report["gate"] == "ok"
    assert report["source"] == [384, 192]


def test_merge_requires_an_adopted_result(tmp_path):
    _prepared_case(tmp_path)
    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "adopt" in result.output


# ── 拼接输入可以指向素材库（决策 66）────────────────────────────────────


def _add_merge_candidate(tmp_path, path) -> str:
    """把一张图片加进素材库并作为拼接输入，返回素材 id。"""
    case_mod.add_assets("p", [(path, "lighting_ref")], root=tmp_path)
    case_id = case_mod.load("p", tmp_path).assets[-1].id
    case_mod.set_merge_input("p", case_id, root=tmp_path)
    return case_id


def test_merge_input_roundtrip_and_clear(tmp_path):
    _prepared_case(tmp_path)
    case_id = _add_merge_candidate(tmp_path, _source(tmp_path, "edited.png"))

    case = case_mod.load("p", tmp_path)
    assert case.merge_input == case_id
    assert case.merge_input_file(tmp_path).name == f"{case_id}_edited.png"
    raw = case_mod.case_toml("p", tmp_path).read_text(encoding="utf-8")
    assert "[merge]" in raw and f'input = "{case_id}"' in raw

    case_mod.set_merge_input("p", None, root=tmp_path)
    assert case_mod.load("p", tmp_path).merge_input is None
    assert "[merge]" not in case_mod.case_toml("p", tmp_path).read_text(encoding="utf-8")


def test_merge_input_falls_back_to_the_adopted_result(tmp_path):
    _prepared_case(tmp_path)
    version = _version_dir(tmp_path)
    result = version / "result.png"
    assert case_mod.load("p", tmp_path).merge_input_file(tmp_path) is None
    result.write_bytes(b"png")
    assert case_mod.load("p", tmp_path).merge_input_file(tmp_path) == result


def test_merge_stitches_from_a_library_asset(tmp_path):
    """没有 adopt 结果，只靠 ``[merge] input`` 也能出片。"""
    src = _prepared_case(tmp_path)
    version = _version_dir(tmp_path)
    case_id = _add_merge_candidate(tmp_path, version / "padded.png")

    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    case = case_mod.load("p", tmp_path)
    out = (case_mod.case_dir("p", tmp_path) / "out"
           / f"{case.extract_current}_image.png")
    assert np.array_equal(read_image(out), read_image(src))
    report = json.loads((version / "report.json").read_text(encoding="utf-8"))
    assert report["input"]["asset"] == case_id
    assert report["input"]["sha256"]


def test_merge_rejects_a_two_to_one_library_input(tmp_path):
    _prepared_case(tmp_path)
    _add_merge_candidate(tmp_path, _source(tmp_path, "erp.png"))

    result = runner.invoke(merge_cli.app, [
        "--case", "p", "-q", "--cases-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "21:9" in result.output


def test_merge_input_rejects_a_video_asset(tmp_path):
    _prepared_case(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a video")
    case_mod.add_assets("p", [(video, "edit_master")], root=tmp_path)
    with pytest.raises(CaseError, match="拼接输入必须是图片"):
        case_mod.set_merge_input("p", "v1", root=tmp_path)


def test_merge_input_rejects_a_dangling_pointer(tmp_path):
    _prepared_case(tmp_path)
    toml = case_mod.case_toml("p", tmp_path)
    toml.write_text(toml.read_text(encoding="utf-8")
                    + '\n[merge]\ninput = "i7"\n', encoding="utf-8")
    with pytest.raises(CaseError, match="不存在的素材"):
        case_mod.load("p", tmp_path)


def test_merge_input_only_belongs_to_fullredrawimg(tmp_path):
    case_mod.create("g", "gen", root=tmp_path)
    toml = case_mod.case_toml("g", tmp_path)
    toml.write_text(toml.read_text(encoding="utf-8")
                    + '\n[merge]\ninput = "i1"\n', encoding="utf-8")
    with pytest.raises(CaseError, match="只属于"):
        case_mod.load("g", tmp_path)


def test_add_merge_input_uses_the_dedicated_role(tmp_path):
    _prepared_case(tmp_path)
    path = _source(tmp_path, "edited.png")
    case, case_id = case_mod.add_merge_input("p", path, root=tmp_path)
    assert case.merge_input == case_id
    assert case.asset(case_id).role == case_mod.MERGE_INPUT

    # 同一份文件再入库：指向同一条，不新增副本。
    again, again_id = case_mod.add_merge_input("p", path, root=tmp_path)
    assert again_id == case_id
    assert len(again.assets) == 1


def test_add_merge_input_rejects_a_video(tmp_path):
    _prepared_case(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a video")
    with pytest.raises(CaseError, match="不是图片"):
        case_mod.add_merge_input("p", video, root=tmp_path)


def test_removing_the_merge_input_asset_clears_the_pointer(tmp_path):
    _prepared_case(tmp_path)
    case_id = _add_merge_candidate(tmp_path, _source(tmp_path, "edited.png"))
    case_mod.remove_asset("p", case_id, root=tmp_path)
    assert case_mod.load("p", tmp_path).merge_input is None
