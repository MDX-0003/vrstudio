"""M4 patch 流程接线：统一 case 模型上的 pick 草稿 / extract name / merge --case。

纯解析与守卫路径不起 ffmpeg（CI 可跑）；文件级 e2e 在文件底部，
与 test_videoio_smoke 同款 skipif。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from vrstudio import case as case_mod
from vrstudio.cli import extract as extract_cli
from vrstudio.cli import merge as merge_cli

runner = CliRunner()

VIEWPORT = {"yaw_deg": 10.0, "pitch_deg": -5.0, "fov_h_deg": 90.0,
            "width": 320, "height": 180}
INNER = {"x": 80, "y": 40, "width": 160, "height": 100}
FRAMES = {"start": 0, "end": 5}


@pytest.fixture
def patch_case(tmp_path) -> tuple[case_mod.Case, Path]:
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake erp bytes")
    case = case_mod.create("p1", "patch", src, root=tmp_path)
    return case, tmp_path


# ── set_draft / draft_ready ──────────────────────────────────────────────


def test_set_draft_partial_then_complete(patch_case):
    case, root = patch_case
    case_mod.set_draft("p1", viewport=VIEWPORT, root=root)
    case = case_mod.load("p1", root)
    assert case.viewport == VIEWPORT and case.inner is None
    assert case_mod.draft_ready(case) == "inner"          # 缺哪段报哪段
    case_mod.set_draft("p1", inner=INNER, frames=FRAMES, root=root)
    case = case_mod.load("p1", root)
    assert case_mod.draft_ready(case) is None
    # extract 指针与草稿共存（save 重写不丢别的段）
    case_mod.set_extract_current("p1", "v1", root=root)
    assert case_mod.load("p1", root).viewport == VIEWPORT


def test_set_draft_rejects_fullredraw(tmp_path):
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    with pytest.raises(case_mod.CaseError, match="fullredraw"):
        case_mod.set_draft("f1", viewport=VIEWPORT, root=tmp_path)


# ── extract name：守卫路径（无 ffmpeg）───────────────────────────────────


def test_extract_name_rejects_incomplete_draft(patch_case):
    case, root = patch_case
    r = runner.invoke(extract_cli.app,
                      ["name", "--case", "p1", "--cases-dir", str(root)])
    assert r.exit_code == 2 and "viewport" in r.output


def test_extract_name_rejects_fullredraw(tmp_path):
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    r = runner.invoke(extract_cli.app,
                      ["name", "--case", "f1", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 2 and "fullredraw" in r.output


# ── merge resolve_case_inputs：守卫路径（无 ffmpeg）──────────────────────


def test_merge_resolve_full_chain(patch_case):
    case, root = patch_case
    with pytest.raises(Exception, match="extract"):
        merge_cli.resolve_case_inputs("p1", root)        # 没抽取版本
    case_mod.set_extract_current("p1", "20261016T000000", root=root)
    with pytest.raises(Exception, match="adopt"):
        merge_cli.resolve_case_inputs("p1", root)        # 没采用的结果
    d = case_mod.case_dir("p1", root) / "extracts" / "20261016T000000"
    d.mkdir(parents=True)
    (d / "result.mp4").write_bytes(b"r")
    (d / "clip.json").write_bytes(b"{}")
    got = merge_cli.resolve_case_inputs("p1", root)
    assert got["ai"] == d / "result.mp4"
    assert got["sidecar"] == d / "clip.json"
    assert got["input"] == Path(case.source_path)
    assert got["output"].parent.name == "out"
    assert got["output"].stem == "20261016T000000"       # out/<extract 版本>.mp4


def test_merge_resolve_rejects_fullredraw(tmp_path):
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    with pytest.raises(Exception, match="fullredraw"):
        merge_cli.resolve_case_inputs("f1", tmp_path)


def test_merge_case_and_explicit_args_are_exclusive(patch_case):
    _, root = patch_case
    r = runner.invoke(merge_cli.app, [
        "--case", "p1", "--input", "x.mp4",
        "--cases-dir", str(root)])
    assert r.exit_code != 0 and "x.mp4" not in r.output   # 互斥：拒绝执行


# ── 文件级 e2e（需要本地 ffmpeg，与 test_videoio_smoke 同款 skipif）──────

try:
    from vrstudio.media.binres import find_tool
    _HAS_FFMPEG = (find_tool("ffmpeg.exe") is not None
                   and find_tool("ffprobe.exe") is not None)
except Exception:                                            # pragma: no cover
    _HAS_FFMPEG = False


@pytest.mark.skipif(not _HAS_FFMPEG, reason="external ffmpeg not populated in bin/")
def test_patch_extract_and_merge_by_case(tmp_path):
    """草稿 → extract name → 假返回件落 extracts → merge --case → out/ 成片。"""
    import shutil as _shutil

    from vrstudio import config as config_mod
    from vrstudio.media.videoio import probe
    from tests.test_videoio_smoke import W, H, _closed_erp, _write

    conf = config_mod.load()
    src = tmp_path / "src.mp4"
    _write(src, _closed_erp(), conf)                       # 6 帧 256x128 闭环源

    case_mod.create("e2e", "patch", src, root=tmp_path)
    vp = dict(VIEWPORT, width=160, height=80)              # 视口比源小
    inner = dict(INNER, x=40, y=20, width=80, height=40)
    case_mod.set_draft("e2e", viewport=vp, inner=inner, frames=FRAMES, root=tmp_path)

    r = runner.invoke(extract_cli.app,
                      ["name", "--case", "e2e", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    case = case_mod.load("e2e", tmp_path)
    assert case.extract_current, "extract 应写 [extract] current"
    d = case_mod.case_dir("e2e", tmp_path) / "extracts" / case.extract_current
    for f in ("clip.mp4", "clip.json", "clip_mask.png"):
        assert (d / f).is_file(), f
    assert (case_mod.case_dir("e2e", tmp_path) / "derived" / "clip.json").is_file()

    # 假装外部工具返回了同几何的件（恒等：直接拷 clip.mp4 当 result）
    _shutil.copyfile(d / "clip.mp4", d / "result.mp4")

    r = runner.invoke(merge_cli.app, [
        "--case", "e2e", "-q", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    out = case_mod.case_dir("e2e", tmp_path) / "out" / f"{case.extract_current}.mp4"
    assert out.is_file()
    info = probe(out, conf)
    assert info.size == (W, H) and info.frames in (0, 6)   # 源尺寸/帧数不变
