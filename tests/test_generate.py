"""generate 接缝的单元测试（vrgen 子进程全部 monkeypatch，不起真进程）。

契约要点（docs/architecture.md §3）：输入包解析的 flow 特有知识、--set 纯透传、
adopt 后 [adopt] round 落 case、退出码透传。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vrstudio import case as case_mod
from vrstudio.cli import generate as gen
from vrstudio.redraw.geometry import PadSpec, plan_pad
from vrstudio.redraw.manifest import VideoSettings, from_plan

runner = CliRunner()


@pytest.fixture
def src_video(tmp_path) -> Path:
    p = tmp_path / "src.mp4"
    p.write_bytes(b"fake erp bytes")
    return p


def _fullredraw_case(tmp_path, src_video) -> case_mod.Case:
    case = case_mod.create("e2e", "fullredraw", src_video, root=tmp_path)
    cdir = case.dir(tmp_path)
    plan = plan_pad(PadSpec((256, 128), Fraction(21, 9)))
    man = from_plan(
        plan, source_path=src_video, source_frames=6, source_fps="30/1",
        source_pix_fmt="yuv420p", has_audio=False, write_hash=False,
        mask_path="seedance_input_mask.png", mask_strategy="redundant_white",
        mask_feather_px=0, mask_png_compression=0, restore_interp="lanczos4",
        video=VideoSettings("libx264", 18, "veryfast", "yuv420p", "copy", "copy"),
        tool_version="0.1.0", padded_name="seedance_input.mp4",
    )
    man.save(cdir / "seedance_input_pad.json")
    (cdir / "seedance_input.mp4").write_bytes(b"canvas")
    (cdir / "seedance_input_mask.png").write_bytes(b"mask")
    return case


# ── resolve_package ──────────────────────────────────────────────────────


def test_resolve_fullredraw_reads_manifest(tmp_path, src_video):
    case = _fullredraw_case(tmp_path, src_video)
    video, mask, template, canvas, source = gen.resolve_package(case, tmp_path)
    assert video.name == "seedance_input.mp4"
    assert mask is not None and mask.name == "seedance_input_mask.png"
    assert template == "360-closure" and canvas == (300, 128)
    assert source == (256, 128)


def test_resolve_rejects_ambiguous_manifest(tmp_path, src_video):
    case = _fullredraw_case(tmp_path, src_video)
    cdir = case.dir(tmp_path)
    (cdir / "other_pad.json").write_text("{}", encoding="utf-8")
    with pytest.raises(gen.GenError, match="指哪份"):
        gen.resolve_package(case, tmp_path)


def test_resolve_patch_reads_extract_pointer(tmp_path, src_video):
    case = case_mod.create("p1", "patch", src_video, root=tmp_path)
    with pytest.raises(gen.GenError, match="extract"):
        gen.resolve_package(case, tmp_path)          # 没抽取版本 → 明确报错
    case_mod.set_extract_current("p1", "20261016T000000", root=tmp_path)
    d = case.dir(tmp_path) / "extracts" / "20261016T000000"
    d.mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"clip")
    (d / "clip_mask.png").write_bytes(b"mask")
    from vrstudio.patch.sidecar import InnerRect, Segment, Sidecar, Viewport, save_sidecar
    save_sidecar(Sidecar(
        erp_width=4000, erp_height=2000, fps=30.0, version=1,
        segments=[Segment(id="s0", frame_start=0, frame_end=239,
                          viewport=Viewport(yaw_deg=0.0, pitch_deg=0.0, fov_h_deg=90.0,
                                            width=1470, height=630),
                          inner=InnerRect(x=400, y=200, width=600, height=500))]),
                d / "clip.json")
    video, mask, template, canvas, source = gen.resolve_package(case_mod.load("p1", tmp_path), tmp_path)
    assert video.name == "clip.mp4" and mask is not None
    assert template == "character-repaint"
    assert canvas == (1470, 630) and source == (1470, 630)   # 来自 clip.json 契约


# ── submit --dry-run ─────────────────────────────────────────────────────


def test_submit_dry_run_prints_argv_without_calling(tmp_path, src_video, monkeypatch):
    _fullredraw_case(tmp_path, src_video)
    called = []
    monkeypatch.setattr(gen, "_stream", lambda argv: called.append(argv) or 0)
    r = runner.invoke(gen.app, [
        "submit", "--case", "e2e", "--var", "theme=夜晚", "--dry-run",
        "--cases-dir", str(tmp_path),
    ])
    assert r.exit_code == 0, r.output
    argv_lines = [line.strip() for line in r.output.splitlines()]
    # 本机 vrstudio.toml 可把 vrgen 覆盖成绝对路径（generate.vrgen），
    # 所以可执行文件那行断言"包含 vrgen"而不是全等
    assert "vrgen" in argv_lines[1], f"argv 缺 vrgen 可执行文件：{r.output}"
    for token in ("submit", "--job", "e2e", "--input",
                  "--template", "360-closure", "--set", "theme=夜晚"):
        assert token in argv_lines, f"argv 缺 {token!r}：{r.output}"
    assert not called                     # dry-run 绝不真调


def test_submit_passes_exit_code_through(tmp_path, src_video, monkeypatch):
    _fullredraw_case(tmp_path, src_video)
    monkeypatch.setattr(gen, "_stream", lambda argv: 2)
    r = runner.invoke(gen.app, ["submit", "--case", "e2e", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 2               # vrgen 的 stub 退出码原样透传


def test_submit_dry_run_passes_generation_params(tmp_path, src_video, monkeypatch):
    _fullredraw_case(tmp_path, src_video)
    monkeypatch.setattr(gen, "_stream", lambda argv: 0)
    r = runner.invoke(gen.app, [
        "submit", "--case", "e2e", "--dry-run",
        "--resolution", "720p", "--scale", "half",
        "--cases-dir", str(tmp_path),
    ])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    assert "--resolution" in lines and "720p" in lines
    assert "--scale" in lines and "half" in lines


# ── adopt ────────────────────────────────────────────────────────────────


def test_adopt_writes_pointer_and_next_step(tmp_path, src_video, monkeypatch):
    _fullredraw_case(tmp_path, src_video)
    captured = {}

    cdir = case_mod.case_dir("e2e", tmp_path)

    def fake_capture(argv):
        captured["argv"] = argv
        (cdir / "result.mp4").write_bytes(b"result")
        return 0, f"{cdir / 'result.mp4'}   round=manual-ab12cd34   gate=ok"

    monkeypatch.setattr(gen, "_capture", fake_capture)
    r = runner.invoke(gen.app, ["adopt", "--case", "e2e", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    case = case_mod.load("e2e", tmp_path)
    assert case.adopt_round == "manual-ab12cd34"    # latest 被解析成具体 key 落盘
    assert "--round" in " ".join(captured["argv"]) and "--out" in " ".join(captured["argv"])
    assert "recompose" in r.output                  # 打印了下一步命令


def test_adopt_patch_lands_in_extract_dir(tmp_path, src_video, monkeypatch):
    case_mod.create("p1", "patch", src_video, root=tmp_path)
    case_mod.set_extract_current("p1", "20261016T000000", root=tmp_path)
    target = (case_mod.case_dir("p1", tmp_path) / "extracts"
              / "20261016T000000" / "result.mp4")

    def fake_capture(argv):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"r")
        return 0, f"{target}   round=manual-11112222   gate=ok"

    monkeypatch.setattr(gen, "_capture", fake_capture)
    r = runner.invoke(gen.app, ["adopt", "--case", "p1", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0 and target.is_file()
    assert "merge" in r.output                       # patch 的下一步是 merge
    assert case_mod.load("p1", tmp_path).adopt_round == "manual-11112222"


def test_adopt_vrgen_failure_propagates(tmp_path, src_video, monkeypatch):
    _fullredraw_case(tmp_path, src_video)
    monkeypatch.setattr(gen, "_capture", lambda argv: (1, "vrgen: 没有可用的轮次"))
    r = runner.invoke(gen.app, ["adopt", "--case", "e2e", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 1
    assert case_mod.load("e2e", tmp_path).adopt_round is None   # 失败不写指针
