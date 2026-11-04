"""generate 接缝的单元测试（vrgen 子进程全部 monkeypatch，不起真进程）。

契约要点（docs/architecture.md §3）：输入包解析的 flow 特有知识、--set 纯透传、
adopt 后 [adopt] round 落 case、退出码透传。
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vrstudio import case as case_mod
from vrstudio.cli import generate as gen
from vrstudio.media.videoio import VideoInfo
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
    case_mod.set_submit_selected("e2e", ["pad"], root=tmp_path)
    return case


# ── candidates / assemble（三流程统一清单）──────────────────────────────


def test_candidates_list_pad_before_file_assets(tmp_path, src_video):
    _fullredraw_case(tmp_path, src_video)
    extra = tmp_path / "extra.png"
    extra.write_bytes(b"x")
    case_mod.add_assets("e2e", [(extra, "subject_ref")], root=tmp_path)
    cands = gen.candidates(case_mod.load("e2e", tmp_path), tmp_path)
    assert [c.id for c in cands] == ["pad", "i1"]
    assert cands[0].source == "flow" and cands[0].role_fixed
    assert cands[1].source == "asset"


def test_assemble_fullredraw_drops_mask_without_a_preceding_image(tmp_path, src_video):
    _fullredraw_case(tmp_path, src_video)
    sub = gen.assemble(case_mod.load("e2e", tmp_path), tmp_path)
    assert [c.id for c in sub.refs] == ["pad"]
    assert sub.mask is None
    assert sub.template == "360-closure"
    assert sub.canvas_size == (300, 128) and sub.source_size == (256, 128)
    payload = sub.payload()
    assert [a["role"] for a in payload["assets"]] == ["edit_master"]


def test_assemble_fullredraw_keeps_mask_after_a_scene_ref(tmp_path, src_video):
    _fullredraw_case(tmp_path, src_video)
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"scene")
    case_mod.add_assets("e2e", [(scene, "scene_ref")], root=tmp_path)
    case_mod.set_submit_selected("e2e", ["pad", "i1"], root=tmp_path)
    sub = gen.assemble(case_mod.load("e2e", tmp_path), tmp_path)
    assert [c.id for c in sub.refs] == ["pad", "i1"]
    assert sub.mask is not None and sub.mask.name == "seedance_input_mask.png"
    payload = sub.payload()
    assert [a["role"] for a in payload["assets"]] == ["edit_master", "scene_ref", "mask"]
    assert payload["assets"][2]["id"] == "pad_mask"


def test_assemble_without_selection_is_an_error(tmp_path, src_video):
    _fullredraw_case(tmp_path, src_video)
    case_mod.set_submit_selected("e2e", [], root=tmp_path)
    with pytest.raises(gen.GenError, match="没有勾选"):
        gen.assemble(case_mod.load("e2e", tmp_path), tmp_path)


def test_assemble_rejects_ambiguous_manifest(tmp_path, src_video):
    case = _fullredraw_case(tmp_path, src_video)
    cdir = case.dir(tmp_path)
    (cdir / "other_pad.json").write_text("{}", encoding="utf-8")
    with pytest.raises(gen.GenError, match="指哪份"):
        gen.assemble(case_mod.load("e2e", tmp_path), tmp_path)


def test_assemble_patch_reads_extract_pointer(tmp_path, src_video):
    case = case_mod.create("p1", "patch", src_video, root=tmp_path)
    case_mod.set_submit_selected("p1", ["clip"], root=tmp_path)
    with pytest.raises(gen.GenError, match="extract"):
        gen.assemble(case_mod.load("p1", tmp_path), tmp_path)   # 没抽取版本 → 明确报错
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
    sub = gen.assemble(case_mod.load("p1", tmp_path), tmp_path)
    assert sub.refs[0].id == "clip"
    assert sub.mask is not None and sub.mask.name == "clip_mask.png"
    assert sub.template == "character-repaint"
    assert sub.canvas_size == (1470, 630) and sub.source_size == (1470, 630)


# ── gen：素材清单 → vrgen --refs ───────────────────────────────────────


def _gen_info() -> VideoInfo:
    return VideoInfo(path="src.mp4", size=(1920, 1052), fps="30/1",
                     fps_float=30.0, frames=367, pix_fmt="yuv420p",
                     codec="h264", has_audio=True, duration_s=12.23)


def test_assemble_gen_uses_master_asset_and_probes_size(tmp_path, src_video, monkeypatch):
    case_mod.create("g1", "gen", src_video, root=tmp_path)
    case_mod.set_submit_selected("g1", ["v1"], root=tmp_path)
    monkeypatch.setattr(gen, "probe", lambda path: _gen_info())
    sub = gen.assemble(case_mod.load("g1", tmp_path), tmp_path)
    assert sub.refs[0].path == case_mod.load("g1", tmp_path).assets[0].resolved(
        case_mod.load("g1", tmp_path), tmp_path)
    assert sub.mask is None
    assert sub.template == "gen-multi"
    assert sub.canvas_size == sub.source_size == (1920, 1052)


def test_assemble_gen_requires_a_selection(tmp_path):
    case_mod.create("g1", "gen", root=tmp_path)
    with pytest.raises(gen.GenError, match="没有勾选"):
        gen.assemble(case_mod.load("g1", tmp_path), tmp_path)


def test_submit_dry_run_gen_writes_refs_and_omits_input(tmp_path, src_video, monkeypatch):
    case_mod.create("g1", "gen", src_video, root=tmp_path)
    case_mod.set_submit_selected("g1", ["v1"], root=tmp_path)
    monkeypatch.setattr(gen, "probe", lambda path: _gen_info())
    r = runner.invoke(gen.app, ["submit", "--case", "g1", "--dry-run",
                                "--resolution", "1080p", "--scale", "auto",
                                "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    assert "--template" in lines and "gen-multi" in lines
    # 未显式给比例时不再补 auto：让 generation.toml 的 16:9/21:9 生效
    assert "--aspect-ratio" not in lines
    assert "1920x1052" in lines
    assert "--input" not in lines and "--refs" in lines
    refs_path = Path(lines[lines.index("--refs") + 1])
    data = json.loads(refs_path.read_text(encoding="utf-8"))
    assert data["assets"][0]["id"] == "v1"
    assert data["assets"][0]["role"] == "edit_master"
    assert data["assets"][0]["path"].endswith(".mp4")


def test_submit_dry_run_gen_passes_explicit_aspect(tmp_path, src_video, monkeypatch):
    case_mod.create("g1", "gen", src_video, root=tmp_path)
    case_mod.set_submit_selected("g1", ["v1"], root=tmp_path)
    monkeypatch.setattr(gen, "probe", lambda path: _gen_info())
    r = runner.invoke(gen.app, ["submit", "--case", "g1", "--dry-run",
                                "--aspect-ratio", "16:9",
                                "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    assert "--aspect-ratio" in lines and "16:9" in lines


def _image_assets(tmp_path):
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    return first, last


def test_assemble_gen_image_master_omits_video(tmp_path, monkeypatch):
    first, last = _image_assets(tmp_path)
    case_mod.create("g2", "gen", root=tmp_path)
    case_mod.add_assets("g2", [(first, "first_frame"), (last, "last_frame")],
                        root=tmp_path)
    case_mod.set_submit_selected("g2", ["i1", "i2"], root=tmp_path)
    monkeypatch.setattr(gen, "_image_size", lambda path: (1920, 1080))
    sub = gen.assemble(case_mod.load("g2", tmp_path), tmp_path, mode="firstLastFrame")
    assert [c.role for c in sub.refs] == ["first_frame", "last_frame"]
    assert sub.mask is None
    assert sub.template == "gen-multi"
    assert sub.canvas_size == sub.source_size == (1920, 1080)


def test_assemble_uses_submit_role_override(tmp_path, monkeypatch):
    first, last = _image_assets(tmp_path)
    case_mod.create("g5", "gen", root=tmp_path)
    case_mod.add_assets("g5", [(first, "subject_ref"), (last, "last_frame")],
                        root=tmp_path)
    case_mod.set_submit_selected(
        "g5", ["i1", "i2"], root=tmp_path,
        roles={"i1": "subject_ref", "i2": "last_frame"})
    monkeypatch.setattr(gen, "_image_size", lambda path: (1920, 1080))
    sub = gen.assemble(case_mod.load("g5", tmp_path), tmp_path, mode="allMediaRef")
    assert [c.role for c in sub.refs] == ["subject_ref", "last_frame"]
    assert [a["role"] for a in sub.payload()["assets"]] == ["subject_ref", "last_frame"]


def test_submit_gen_image_master_uses_refs(tmp_path, monkeypatch):
    first, last = _image_assets(tmp_path)
    case_mod.create("g2", "gen", root=tmp_path)
    case_mod.add_assets("g2", [(first, "first_frame"), (last, "last_frame")],
                        root=tmp_path)
    case_mod.set_submit_selected("g2", ["i1", "i2"], root=tmp_path)
    monkeypatch.setattr(gen, "_image_size", lambda path: (1920, 1080))
    r = runner.invoke(gen.app, ["submit", "--case", "g2", "--mode", "firstLastFrame",
                                "--dry-run", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    assert "--refs" in lines and "--input" not in lines
    assert "gen-multi" in lines
    refs_path = Path(lines[lines.index("--refs") + 1])
    data = json.loads(refs_path.read_text(encoding="utf-8"))
    assert [a["role"] for a in data["assets"]] == ["first_frame", "last_frame"]


def test_submit_refs_filtered_by_selected(tmp_path, src_video, monkeypatch):
    case_mod.create("g3", "gen", src_video, root=tmp_path)
    first, _last = _image_assets(tmp_path)
    case_mod.add_assets("g3", [(first, "subject_ref")], root=tmp_path)
    case_mod.set_submit_selected("g3", ["v1"], root=tmp_path)
    monkeypatch.setattr(gen, "probe", lambda path: _gen_info())
    r = runner.invoke(gen.app, ["submit", "--case", "g3", "--dry-run",
                                "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    refs_path = Path(lines[lines.index("--refs") + 1])
    data = json.loads(refs_path.read_text(encoding="utf-8"))
    assert [a["id"] for a in data["assets"]] == ["v1"]


def test_firstlast_requires_exactly_one_pair(tmp_path, monkeypatch):
    first, last = _image_assets(tmp_path)
    third = tmp_path / "third.png"
    third.write_bytes(b"third")
    case_mod.create("g4", "gen", root=tmp_path)
    case_mod.add_assets("g4", [(first, "first_frame")], root=tmp_path)
    case_mod.set_submit_selected("g4", ["i1"], root=tmp_path)
    monkeypatch.setattr(gen, "_image_size", lambda path: (1920, 1080))
    r = runner.invoke(gen.app, ["submit", "--case", "g4", "--mode", "firstLastFrame",
                                "--dry-run", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 2, r.output
    assert "首帧" in r.output and "尾帧" in r.output

    case_mod.add_assets("g4", [(last, "last_frame")], root=tmp_path)
    case_mod.set_submit_selected("g4", ["i1", "i2"], root=tmp_path)
    r = runner.invoke(gen.app, ["submit", "--case", "g4", "--mode", "firstLastFrame",
                                "--dry-run", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output

    # 多勾的素材由装配器忽略（契约：firstLastFrame 只取首尾两张）。
    case_mod.add_assets("g4", [(third, "subject_ref")], root=tmp_path)
    case_mod.set_submit_selected("g4", ["i1", "i2", "i3"], root=tmp_path)
    sub = gen.assemble(case_mod.load("g4", tmp_path), tmp_path, mode="firstLastFrame")
    assert [c.role for c in sub.refs] == ["first_frame", "last_frame"]


def _patch_16_9_case(tmp_path, src_video, *, size=(1920, 1080)):
    case_mod.create("p1", "patch", src_video, root=tmp_path)
    case_mod.set_extract_current("p1", "20261016T000000", root=tmp_path)
    d = case_mod.case_dir("p1", tmp_path) / "extracts" / "20261016T000000"
    d.mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"clip")
    (d / "clip_mask.png").write_bytes(b"mask")
    from vrstudio.patch.sidecar import InnerRect, Segment, Sidecar, Viewport, save_sidecar
    save_sidecar(Sidecar(
        erp_width=4000, erp_height=2000, fps=30.0, version=1,
        segments=[Segment(id="s0", frame_start=0, frame_end=239,
                          viewport=Viewport(yaw_deg=0.0, pitch_deg=0.0, fov_h_deg=90.0,
                                            width=size[0], height=size[1]),
                          inner=InnerRect(x=400, y=200, width=600, height=500))]),
        d / "clip.json")
    return case_mod.load("p1", tmp_path)


def test_patch_firstlast_branch_builds_refs(tmp_path, src_video, monkeypatch):
    _patch_16_9_case(tmp_path, src_video)
    first, last = _image_assets(tmp_path)
    case_mod.add_assets("p1", [(first, "first_frame"), (last, "last_frame")],
                        root=tmp_path)
    case_mod.set_submit_selected("p1", ["i1", "i2"], root=tmp_path)
    r = runner.invoke(gen.app, ["submit", "--case", "p1", "--mode", "firstLastFrame",
                                "--dry-run", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    assert "--refs" in lines and "--input" not in lines
    assert "gen-multi" in lines
    assert "1920x1080" in lines
    refs_path = Path(lines[lines.index("--refs") + 1])
    data = json.loads(refs_path.read_text(encoding="utf-8"))
    # firstLastFrame 不附带 mask，也不提交流程视频。
    assert [a["role"] for a in data["assets"]] == ["first_frame", "last_frame"]


def test_clip_duration_uses_frozen_frame_range(tmp_path, src_video):
    case = _patch_16_9_case(tmp_path, src_video)
    # clip.json: 0..239 @30fps = 240 帧 = 8.0 秒
    assert gen.clip_duration_s(case, tmp_path) == 8


def test_submit_firstlast_defaults_to_clip_duration(tmp_path, src_video):
    _patch_16_9_case(tmp_path, src_video)
    first, last = _image_assets(tmp_path)
    case_mod.add_assets("p1", [(first, "first_frame"), (last, "last_frame")],
                        root=tmp_path)
    case_mod.set_submit_selected("p1", ["i1", "i2"], root=tmp_path)
    r = runner.invoke(gen.app, ["submit", "--case", "p1", "--mode", "firstLastFrame",
                                "--dry-run", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    assert lines[lines.index("--duration") + 1] == "8"


def test_submit_firstlast_explicit_duration_wins(tmp_path, src_video):
    _patch_16_9_case(tmp_path, src_video)
    first, last = _image_assets(tmp_path)
    case_mod.add_assets("p1", [(first, "first_frame"), (last, "last_frame")],
                        root=tmp_path)
    case_mod.set_submit_selected("p1", ["i1", "i2"], root=tmp_path)
    r = runner.invoke(gen.app, ["submit", "--case", "p1", "--mode", "firstLastFrame",
                                "--duration", "10", "--dry-run",
                                "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    assert lines[lines.index("--duration") + 1] == "10"


def test_patch_firstlast_requires_16_9_viewport(tmp_path, src_video):
    _patch_16_9_case(tmp_path, src_video, size=(1920, 1052))
    first, last = _image_assets(tmp_path)
    case_mod.add_assets("p1", [(first, "first_frame"), (last, "last_frame")],
                        root=tmp_path)
    case_mod.set_submit_selected("p1", ["i1", "i2"], root=tmp_path)
    with pytest.raises(gen.GenError, match="16:9"):
        gen.assemble(case_mod.load("p1", tmp_path), tmp_path, mode="firstLastFrame")


def test_template_follows_flow_and_mode():
    assert gen.template_for("patch", "firstLastFrame") == "gen-multi"
    assert gen.template_for("patch", "allMediaRef") == "gen-multi"
    assert gen.template_for("patch", "videoEdit") == "character-repaint"
    assert gen.template_for("gen", "firstLastFrame") == "gen-multi"
    assert gen.template_for("fullredraw", "videoEdit") == "360-closure"


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
    for token in ("submit", "--job", "e2e", "--refs",
                  "--template", "360-closure", "--set", "theme=夜晚"):
        assert token in argv_lines, f"argv 缺 {token!r}：{r.output}"
    assert not called                     # dry-run 绝不真调


def test_submit_check_calls_vrgen_dry_run(tmp_path, src_video, monkeypatch):
    _fullredraw_case(tmp_path, src_video)
    called = []
    monkeypatch.setattr(gen, "_stream", lambda argv: called.append(argv) or 0)
    r = runner.invoke(gen.app, ["submit", "--case", "e2e", "--check",
                                "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert called and called[0][-1] == "--dry-run"


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
        "--resolution", "720p", "--scale", "half", "--audio", "none",
        "--cases-dir", str(tmp_path),
    ])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    assert "--resolution" in lines and "720p" in lines
    assert "--scale" in lines and "half" in lines
    assert "--audio" in lines and "none" in lines


def test_submit_dry_run_passes_project_session_id(tmp_path, src_video, monkeypatch):
    _fullredraw_case(tmp_path, src_video)
    monkeypatch.setattr(gen, "_stream", lambda argv: 0)
    r = runner.invoke(gen.app, [
        "submit", "--case", "e2e", "--dry-run",
        "--session-id", "2060150528006080",
        "--cases-dir", str(tmp_path),
    ])
    assert r.exit_code == 0, r.output
    lines = [line.strip() for line in r.output.splitlines()]
    assert "--session-id" in lines and "2060150528006080" in lines


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


def test_adopt_gen_lands_result_in_case(tmp_path, src_video, monkeypatch):
    case_mod.create("g1", "gen", src_video, root=tmp_path)
    target = case_mod.case_dir("g1", tmp_path) / "result.mp4"

    def fake_capture(argv):
        target.write_bytes(b"r")
        return 0, f"{target}   round=manual-33334444   gate=ok"

    monkeypatch.setattr(gen, "_capture", fake_capture)
    r = runner.invoke(gen.app, ["adopt", "--case", "g1", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert case_mod.load("g1", tmp_path).adopt_round == "manual-33334444"
    assert target.is_file()
    assert "result.mp4" in r.output and "recompose" not in r.output


def test_adopt_vrgen_failure_propagates(tmp_path, src_video, monkeypatch):
    _fullredraw_case(tmp_path, src_video)
    monkeypatch.setattr(gen, "_capture", lambda argv: (1, "vrgen: 没有可用的轮次"))
    r = runner.invoke(gen.app, ["adopt", "--case", "e2e", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 1
    assert case_mod.load("e2e", tmp_path).adopt_round is None   # 失败不写指针
