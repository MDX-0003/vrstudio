"""web 控制台单测（Starlette TestClient，不起真服务、不真跑队列任务）。

覆盖：页面路由、草稿 API（pick 三写路径 + nudge 回绕）、软向导门控判据的
递进、op 注册表的守卫。vrgen 不可用路径也在本测试环境下天然走到。
"""

from __future__ import annotations

import json
import os
from fractions import Fraction

import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio import config as config_mod
from vrstudio.redraw.geometry import PadSpec, plan_pad
from vrstudio.redraw.manifest import VideoSettings, from_plan
from vrstudio.web import app as web_app
from vrstudio.web import tasks as tasks_mod

VIEWPORT = {"yaw_deg": 10.0, "pitch_deg": -5.0, "fov_h_deg": 90.0,
            "width": 320, "height": 180}
INNER = {"x": 80, "y": 40, "width": 160, "height": 100}
FRAMES = {"start": 0, "end": 5}


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    # vrgen 不存在：_rounds 走"不可用"分支而不是 FileNotFoundError
    monkeypatch.setattr(web_app, "_vrgen_exe", lambda: "definitely-not-a-real-vrgen")
    return TestClient(web_app.build_app(cases_root=tmp_path))


@pytest.fixture
def patch_case(tmp_path) -> str:
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake erp bytes")
    case_mod.create("p1", "patch", src, root=tmp_path)
    case_mod.set_draft("p1", viewport=VIEWPORT, inner=INNER, frames=FRAMES,
                       root=tmp_path)
    return "p1"


def _mk_extract(case: str, tmp_path, version: str, with_result: bool = False):
    d = case_mod.case_dir(case, tmp_path) / "extracts" / version
    d.mkdir(parents=True)
    (d / "clip.json").write_text(json.dumps(
        {"segments": [{"id": "s0", "frame_start": 0, "frame_end": 5,
                       "viewport": VIEWPORT, "inner": INNER}]}, ensure_ascii=False),
        encoding="utf-8")
    if with_result:
        (d / "result.mp4").write_bytes(b"r")
    return d


@pytest.fixture
def fullredraw_case(tmp_path, monkeypatch) -> str:
    src = tmp_path / "ue.mp4"
    src.write_bytes(b"fake ue bytes")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    cdir = case_mod.case_dir("f1", tmp_path)
    plan = plan_pad(PadSpec((64, 32), Fraction(21, 9)))
    man = from_plan(
        plan, source_path=src, source_frames=6, source_fps="30/1",
        source_pix_fmt="yuv420p", has_audio=False, write_hash=False,
        padded_name="seedance_input.mp4", mask_path="seedance_input_mask.png",
        mask_strategy="redundant_white", mask_feather_px=0,
        mask_png_compression=0, restore_interp="lanczos4",
        video=VideoSettings("libx264", 18, "veryfast", "yuv420p", "copy", "copy"),
        tool_version="0.1.0",
    )
    man.save(cdir / "seedance_input_pad.json")
    (cdir / "seedance_input.mp4").write_bytes(b"padded")
    (cdir / "seedance_input_mask.png").write_bytes(b"mask")
    (cdir / "result.mp4").write_bytes(b"adopted")
    case_mod.set_adopt("f1", "round-1", root=tmp_path)
    runs = tmp_path / "vrgen_runs" / "f1" / "round-1"
    runs.mkdir(parents=True)
    (runs / "result.mp4").write_bytes(b"raw")
    (runs / "run.json").write_text(
        json.dumps({"result": str(runs / "result.mp4")}), encoding="utf-8")
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "vrgen_runs")
    outdir = cdir / "out"
    outdir.mkdir()
    (outdir / "round-1.mp4").write_bytes(b"out")
    (outdir / "round-1_recompose.json").write_text("{}", encoding="utf-8")
    return "f1"


@pytest.fixture
def gen_case(tmp_path) -> str:
    src = tmp_path / "simple.mp4"
    src.write_bytes(b"fake simple bytes")
    case_mod.create("g1", "gen", src, root=tmp_path)
    return "g1"


# ── 页面与列表 ────────────────────────────────────────────────────────────


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "vrstudio" in r.text


def test_wizard_page_renders_flow(client, patch_case):
    r = client.get("/case/p1")
    assert r.status_code == 200 and "patch" in r.text and "p1" in r.text
    # 对比页的 360° 画布与统一播放控制（原生 controls 已移除）
    assert "cmpCanvas" in r.text and "cmpSeek" in r.text and "controls" not in r.text


def test_wizard_page_404_for_unknown(client):
    assert client.get("/case/nope").status_code == 404


def test_fullredraw_wizard_page_renders_partials(client, fullredraw_case):
    r = client.get("/case/f1")
    assert r.status_code == 200
    assert "扩充原视频两侧画面" in r.text and "裁回原尺寸" in r.text
    assert "生成参数" in r.text and "提交生成" in r.text


def test_gen_wizard_page_has_assets_and_generate(client, gen_case):
    r = client.get("/case/g1")
    assert r.status_code == 200
    assert 'data-flow="gen"' in r.text
    assert "添加视频或图片" in r.text and "素材" in r.text
    info = client.get("/api/case/g1").json()
    assert set(info["gates"]) == {"assets", "generate"}   # gen：素材 → 生成
    assert info["gates"]["assets"]["ok"] is True          # 创建时的源视频已复制为 v1
    assert info["gates"]["generate"]["ok"] is False
    assert "填好提示词" in info["gates"]["generate"]["reason"]
    sf = info["step_files"]
    assert set(sf) == {"assets", "generate"}
    assert sf["generate"]["groups"][0]["inputs"][0]["name"].startswith("v1_")


def test_gen_generate_gate_unlocks_after_adopt(client, gen_case, tmp_path):
    (case_mod.case_dir("g1", tmp_path) / "result.mp4").write_bytes(b"r")
    case_mod.set_adopt("g1", "manual-aaaa1111", root=tmp_path)
    info = client.get("/api/case/g1").json()
    assert info["gates"]["generate"]["ok"] is True
    assert "result.mp4" in info["gates"]["generate"]["evidence"]


def test_create_gen_without_video_requires_mode_and_name(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "_save_gen_mode", lambda name, mode: None)
    r = client.post("/api/cases", json={"flow": "gen", "name": "g2"})
    assert r.status_code == 400 and "模式" in r.json()["error"]

    r = client.post("/api/cases", json={"flow": "gen", "mode": "allMediaRef"})
    assert r.status_code == 400 and "case 名" in r.json()["error"]

    r = client.post("/api/cases", json={"flow": "gen", "name": "g2",
                                         "mode": "allMediaRef"})
    assert r.status_code == 200, r.text
    case = case_mod.load("g2", tmp_path)
    assert case.assets == [] and case.source_path is None


def test_create_gen_keeps_case_when_mode_save_fails(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "_save_gen_mode", lambda name, mode: "vrgen down")
    r = client.post("/api/cases", json={"flow": "gen", "name": "g3",
                                         "mode": "videoEdit"})
    assert r.status_code == 502 and r.json()["created"] is True
    assert case_mod.load("g3", tmp_path).flow == "gen"


def test_asset_api_add_role_reorder_remove(client, gen_case, tmp_path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"fake png")
    r = client.post("/api/case/g1/assets",
                    json={"op": "add", "paths": [str(image)],
                          "roles": ["subject_ref"]})
    assert r.status_code == 200, r.text
    assert [a["id"] for a in r.json()["assets"]] == ["v1", "i1"]

    r = client.post("/api/case/g1/assets",
                    json={"op": "role", "id": "i1", "role": "scene_ref"})
    assert [a["role"] for a in r.json()["assets"]] == ["edit_master", "scene_ref"]

    r = client.post("/api/case/g1/assets",
                    json={"op": "reorder", "ids": ["i1", "v1"]})
    assert [a["id"] for a in r.json()["assets"]] == ["i1", "v1"]

    r = client.post("/api/case/g1/assets", json={"op": "remove", "id": "i1"})
    assert [a["id"] for a in r.json()["assets"]] == ["v1"]
    assert not list((case_mod.case_dir("g1", tmp_path) / "assets").glob("i1_*"))


def test_asset_api_rejects_bad_role(client, gen_case, tmp_path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"fake png")
    r = client.post("/api/case/g1/assets",
                    json={"op": "add", "paths": [str(image)], "roles": ["nope"]})
    assert r.status_code == 400


def test_modes_endpoint_returns_model_modes(client, monkeypatch):
    monkeypatch.setattr(web_app, "_vrgen_json",
                        lambda argv: {"modes": [{"value": "allMediaRef"}],
                                      "current": {"mode": "allMediaRef"}})
    r = client.get("/api/modes")
    assert r.status_code == 200
    assert r.json()["modes"][0]["value"] == "allMediaRef"


def test_cases_list_has_gates(client, patch_case):
    r = client.get("/api/cases")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["name"] == "p1" and body[0]["flow"] == "patch"
    assert body[0]["gates"]["pick"]["ok"] is True          # 草稿齐 → pick 达成
    assert body[0]["gates"]["extract"]["ok"] is False      # 还没抽


# ── 草稿 API ──────────────────────────────────────────────────────────────


def test_draft_write_and_case_info_roundtrip(client, patch_case, tmp_path):
    r = client.post("/api/case/p1/draft",
                    json={"frames": {"start": 2, "end": 4}})
    assert r.status_code == 200
    assert case_mod.load("p1", tmp_path).frames == {"start": 2, "end": 4}
    info = r.json()
    assert info["gates"]["pick"]["ok"] is True
    assert info["rounds"]["rounds"] == []                  # vrgen 缺席 → 空轮次
    assert "vrgen" in info["rounds"]["hint"]


def test_draft_rejects_fullredraw(client, tmp_path):
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    assert client.post("/api/case/f1/draft", json={"inner": INNER}).status_code == 400


def test_viewport_click_sets_yaw_pitch(client, patch_case, tmp_path):
    r = client.post("/api/case/p1/viewport", json={"fx": 0.75, "fy": 0.25})
    assert r.status_code == 200
    vp = case_mod.load("p1", tmp_path).viewport
    assert vp["yaw_deg"] == 90.0 and vp["pitch_deg"] == 45.0


def test_inner_drag_submits_fractions(client, patch_case, tmp_path):
    r = client.post("/api/case/p1/inner",
                    json={"fx": 0.25, "fy": 0.5, "fw": 0.5, "fh": 0.25})
    assert r.status_code == 200
    inner = case_mod.load("p1", tmp_path).inner
    assert (inner["x"], inner["y"]) == (80, 90)            # 分数 × 视口像素
    assert (inner["width"], inner["height"]) == (160, 45)


def test_nudge_wraps_yaw_and_clamps_pitch(client, patch_case, tmp_path):
    r = client.post("/api/case/p1/nudge", json={"dyaw": 180.0, "dpitch": 200.0})
    assert r.status_code == 200
    assert r.json()["nudged"] == {"yaw": -170.0, "pitch": 89.0}
    vp = case_mod.load("p1", tmp_path).viewport
    assert vp["yaw_deg"] == -170.0 and vp["pitch_deg"] == 89.0


def test_reset_draft_restores_from_version(client, patch_case, tmp_path):
    _mk_extract("p1", tmp_path, "v1")
    client.post("/api/case/p1/inner",
                json={"fx": 0.1, "fy": 0.1, "fw": 0.2, "fh": 0.2})
    r = client.post("/api/case/p1/reset-draft", json={"version": "v1"})
    assert r.status_code == 200
    assert case_mod.load("p1", tmp_path).inner == INNER     # 回到版本记录的框


# ── op 注册表与门控递进 ──────────────────────────────────────────────────


def test_run_rejects_unknown_op(client, patch_case):
    r = client.post("/api/case/p1/run", json={"op": "wat"})
    assert r.status_code == 400 and "未知操作" in r.text


def test_run_recompose_needs_manifest(client, tmp_path):
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    r = client.post("/api/case/f1/run", json={"op": "recompose"})
    assert r.status_code == 400 and "扩充两侧画面" in r.text


def test_run_submits_to_queue(client, patch_case, monkeypatch):
    submitted = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda kind, case, argv: submitted.append((kind, case, argv)))
    r = client.post("/api/case/p1/run", json={"op": "extract"})
    assert r.status_code == 200 and r.json()["submitted"] == "extract"
    kind, case, argv = submitted[0]
    assert kind == "extract" and case == "p1"
    assert argv[1:5] == ["-m", "vrstudio", "extract", "name"]   # 走 CLI 同一路径
    assert "--case" in argv and "p1" in argv


def test_run_submit_passes_generation_params(client, patch_case, monkeypatch):
    submitted = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda kind, case, argv: submitted.append((kind, case, argv)))
    r = client.post("/api/case/p1/run",
                    json={"op": "submit", "mode": "allMediaRef",
                          "aspect_ratio": "16:9",
                          "resolution": "720p", "scale": "auto"})
    assert r.status_code == 200 and r.json()["submitted"] == "submit"
    argv = submitted[0][2]
    assert "--resolution" in argv and "720p" in argv
    assert "--scale" in argv and "auto" in argv
    assert "--mode" in argv and "allMediaRef" in argv
    assert "--aspect-ratio" in argv and "16:9" in argv


def test_gates_progress_with_extract_and_result(client, patch_case, tmp_path):
    _mk_extract("p1", tmp_path, "v1", with_result=True)
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    case_mod.set_adopt("p1", "manual-x", root=tmp_path)
    info = client.get("/api/case/p1").json()
    for step in ("pick", "extract", "generate"):
        assert info["gates"][step]["ok"] is True
    assert info["gates"]["merge"]["ready"] is True
    assert info["extracts"][0]["version"] == "v1" and info["extracts"][0]["has_result"]


# ── 提示词模板与变量（决策 18；vrgen 侧用假 JSON 通道替身）────────────────


def test_sessions_endpoint_returns_project_tree(client, monkeypatch):
    tree = {"fetched_at": "2026-10-19T06:30:00Z",
            "projects": [{"id": 11, "name": "项目甲", "episodes": [
                {"no": 1, "id": 111, "name": "第一集",
                 "sessions": [{"id": 1111, "name": "会话一"}]}]}]}
    calls = []

    def fake(argv):
        calls.append(argv)
        return tree if argv[:2] == ["sessions", "list"] else {"error": "unexpected"}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json()["projects"][0]["episodes"][0]["sessions"][0]["id"] == 1111
    assert calls == [["sessions", "list"]]


def test_sessions_endpoint_reports_vrgen_error(client, monkeypatch):
    monkeypatch.setattr(web_app, "_vrgen_json",
                        lambda argv: {"error": "mglc 未登录"})
    r = client.get("/api/sessions")
    assert r.status_code == 400 and "未登录" in r.text


def test_params_save_passes_project_session_fields(client, fullredraw_case,
                                                   monkeypatch):
    calls = []

    def fake(argv):
        calls.append(argv)
        return {"resolution": "1080p", "scale": "auto", "session_id": 1111,
                "project_id": 11, "episode": 1}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    r = client.post("/api/case/f1/params",
                    json={"resolution": "1080p", "scale": "auto",
                          "session_id": 1111, "project_id": 11, "episode": 1})
    assert r.status_code == 200, r.text
    argv = calls[0]
    for flag, value in (("--session-id", "1111"), ("--project-id", "11"),
                        ("--episode", "1")):
        assert flag in argv and value in argv


def test_params_save_passes_mode_and_aspect(client, fullredraw_case, monkeypatch):
    calls = []

    def fake(argv):
        calls.append(argv)
        return {"mode": "allMediaRef", "aspect_ratio": "16:9"}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    r = client.post("/api/case/f1/params",
                    json={"resolution": "1080p", "scale": "auto",
                          "mode": "allMediaRef", "aspect_ratio": "16:9"})
    assert r.status_code == 200, r.text
    argv = calls[0]
    assert "--mode" in argv and "allMediaRef" in argv
    assert "--aspect-ratio" in argv and "16:9" in argv


def test_case_info_prompt_survives_missing_vrgen(client, patch_case):
    info = client.get("/api/case/p1").json()
    assert "error" in info["prompt"]          # vrgen 缺席 → 提示文案，不是 500


def test_case_info_generation_params_survive_missing_vrgen(client, patch_case):
    info = client.get("/api/case/p1").json()
    assert "error" in info["generation_params"]


def test_params_save_calls_vrgen_and_returns_full_info(client, fullredraw_case,
                                                       monkeypatch):
    calls = []
    shown = {
        "output": [{"value": "720p", "width": 1470, "height": 630}],
        "input": [{"value": "auto", "width": 4400, "height": 1884,
                   "ok": True, "reason": ""}],
        "current": {"resolution": "720p", "scale": "auto"},
        "fixed": {"model_id": 37, "mode": "videoEdit", "aspect_ratio": "21:9",
                  "duration_s": 8, "num": 1},
        "saved": True,
    }

    def fake(argv):
        calls.append(argv)
        if argv[:2] == ["params", "save"]:
            return {"resolution": argv[argv.index("--resolution") + 1],
                    "scale": argv[argv.index("--scale") + 1]}
        if argv[:2] == ["params", "show"]:
            return shown
        return {"template": None, "available": [], "template_text": None,
                "placeholders": [], "geometry_keys": [], "vars": {}}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    r = client.post("/api/case/f1/params", json={"resolution": "720p", "scale": "auto"})
    assert r.status_code == 200, r.text
    assert any(c[:2] == ["params", "save"] for c in calls)
    assert r.json()["generation_params"] == shown
    assert r.json()["generation_params"]["current"]["resolution"] == "720p"


def test_case_info_exposes_generation_params(client, fullredraw_case, monkeypatch):
    shown = {
        "output": [{"value": "480p", "width": 992, "height": 432},
                   {"value": "720p", "width": 1470, "height": 630},
                   {"value": "1080p", "width": 2206, "height": 946}],
        "input": [{"value": "auto", "width": 4400, "height": 1884,
                   "ok": True, "reason": ""},
                  {"value": "full", "width": 4668, "height": 2000,
                   "ok": False, "reason": "over cap"},
                  {"value": "half", "width": 2334, "height": 1000,
                   "ok": True, "reason": ""}],
        "current": {"resolution": "720p", "scale": "auto"},
        "fixed": {"model_id": 37, "mode": "videoEdit", "aspect_ratio": "21:9",
                  "duration_s": 8, "num": 1},
        "saved": True,
    }
    monkeypatch.setattr(web_app, "_vrgen_json", lambda argv: shown if argv[:2] ==
                        ["params", "show"] else {"template": None})
    info = client.get("/api/case/f1").json()
    assert info["generation_params"] == shown


def test_case_info_ok_for_fresh_fullredraw(client, tmp_path):
    # 回归：pad 步骤的 evidence 曾在没 manifest 时无条件取 [0]，全新
    # fullredraw case 一打开向导就 500（2026-10-16 浏览器验收发现）
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    r = client.get("/api/case/f1")
    assert r.status_code == 200
    gates = r.json()["gates"]
    assert gates["pad"]["ok"] is False and "扩充" in gates["pad"]["reason"]


def test_prompt_save_passes_through_to_vrgen(client, patch_case, monkeypatch):
    calls = []

    def fake_vrgen_json(argv):
        calls.append(argv)
        return {"template": "character-repaint", "vars": {"theme": "黄昏"}}

    monkeypatch.setattr(web_app, "_vrgen_json", fake_vrgen_json)
    r = client.post("/api/case/p1/prompt", json={"set": "theme", "value": "黄昏"})
    assert r.status_code == 200
    info = r.json()
    assert info["prompt"]["vars"]["theme"] == "黄昏"      # 回显进完整 info
    assert calls[0][:3] == ["prompt", "save", "--job"]
    assert "--set" in calls[0] and "theme=黄昏" in calls[0]

    assert client.post("/api/case/p1/prompt",
                       json={"unset": "theme"}).status_code == 200
    assert "--unset" in calls[1] and "theme" in calls[1]

    r = client.post("/api/case/p1/prompt", json={})
    assert r.status_code == 400 and "没有要改" in r.text


def test_prompt_save_propagates_vrgen_error(client, patch_case, monkeypatch):
    monkeypatch.setattr(web_app, "_vrgen_json",
                        lambda argv: {"error": "变量 ['theme'] 被模板正文引用，不能删除"})
    r = client.post("/api/case/p1/prompt", json={"unset": "theme"})
    assert r.status_code == 400 and "不能删除" in r.text


# ── 任务队列状态（tasks.py：提交时间 + 最近 10 条）────────────────────────


def _done_task(kind: str, case: str, state: str = "done") -> tasks_mod.Task:
    t = tasks_mod.Task(kind, case, ["echo"])
    t.state = state
    t.returncode = 0 if state == "done" else 1
    return t


def test_task_status_carries_submit_time_and_ten_history():
    q = tasks_mod.TaskQueue()
    q.history = [_done_task("merge", f"c{i:02d}") for i in range(12)]
    s = q.status()
    assert len(s["last"]) == 10                          # 窗口从 3 条扩到 10 条
    assert s["last"][-1]["case"] == "c11"                # 末尾是最新的（前端再反转展示）
    assert all(t["submitted"] for t in s["last"])

    t = tasks_mod.Task("extract", "cx", ["echo"])
    q.pending = [t]
    assert q.status()["queued"][0]["submitted"] == t.submitted


def test_rounds_forces_utf8_env(client, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return web_app.subprocess.CompletedProcess(
            argv, 0, stdout="job x: 还没有 Session（记录根 E:\\runs\\x）\n", stderr="")

    monkeypatch.setattr(web_app.subprocess, "run", fake_run)
    payload = web_app._rounds("x")
    assert seen["env"]["PYTHONUTF8"] == "1"
    assert "还没有 Session" in payload["hint"]


def test_task_tail_decodes_legacy_local_codepage_log(tmp_path):
    import locale
    text = "job x: 还没有 Session（记录根 E:\\runs\\x）"
    log = tmp_path / "web_status_x.log"
    log.write_bytes(text.encode(locale.getpreferredencoding(False)))
    task = tasks_mod.Task("submit", "x", ["echo"])
    task.out_path = log
    task.state = "done"
    assert text in "\n".join(task.tail())


def test_failed_task_tail_keeps_the_error_line(tmp_path):
    """超长提示词会把 ERROR 行挤出尾部窗口；失败任务的 tail 必须补回原因。"""
    log = tmp_path / "web_submit_x.log"
    log.write_text("x" * 9000 + "\n" + "\n".join(
        ["ERROR mglc code 400403: 生产栗额度不足",
         "calls       x/mglc.log"] + ["prompt tail"] * 10),
        encoding="utf-8")
    t = tasks_mod.Task("submit", "x", ["echo"])
    t.out_path = log
    t.state = "failed"
    tail = t.tail(5)
    assert tail[-1] == "ERROR mglc code 400403: 生产栗额度不足"

    t.state = "running"
    assert "ERROR mglc code" not in " ".join(t.tail(1))


# ── step_files：两套 flow 的文件规范必须分流 ─────────────────────────────


def test_step_files_are_flow_specific(client, patch_case, fullredraw_case, gen_case):
    patch = client.get("/api/case/p1").json()["step_files"]
    assert set(patch) == {"extract", "generate", "merge"}
    assert "pad" not in patch and "recompose" not in patch

    full = client.get("/api/case/f1").json()["step_files"]
    assert set(full) == {"pad", "generate", "recompose"}
    assert "extract" not in full and "merge" not in full

    gen = client.get("/api/case/g1").json()["step_files"]
    assert set(gen) == {"assets", "generate"}


def test_step_files_fullredraw_reports_names_and_times(client, fullredraw_case, tmp_path):
    sf = client.get("/api/case/f1").json()["step_files"]
    pad = sf["pad"]
    assert pad["inputs"][0]["name"] == "ue.mp4"
    assert [f["name"] for f in pad["outputs"]] == [
        "seedance_input.mp4", "seedance_input_mask.png", "seedance_input_pad.json"]
    assert all(f["exists"] for f in pad["outputs"])
    assert all(isinstance(f["mtime"], (int, float)) for f in pad["outputs"])
    assert all(f["size"] > 0 for f in pad["outputs"])

    gen = sf["generate"]
    assert [g["label"] for g in gen["groups"]] == ["生成", "采用"]
    assert [f["name"] for f in gen["groups"][0]["outputs"]] == ["result.mp4", "run.json"]
    assert gen["groups"][0]["outputs"][0]["exists"] is True     # vrgen 轮次目录里的原始件
    assert gen["groups"][1]["inputs"][0]["exists"] is True
    assert gen["groups"][1]["outputs"][0]["path"] == str(
        case_mod.case_dir("f1", tmp_path) / "result.mp4")        # adopt 拷进 case 的副本

    assert [f["name"] for f in sf["recompose"]["outputs"]] == [
        "round-1.mp4", "round-1_recompose.json"]


def test_step_files_mtime_changes_when_the_step_reruns(client, fullredraw_case, tmp_path):
    cdir = case_mod.case_dir("f1", tmp_path)
    padded = cdir / "seedance_input.mp4"
    os.utime(padded, (1_000, 1_000))
    first = client.get("/api/case/f1").json()["step_files"]["pad"]["outputs"][0]["mtime"]
    os.utime(padded, (2_000, 2_000))
    second = client.get("/api/case/f1").json()["step_files"]["pad"]["outputs"][0]["mtime"]
    assert first == 1_000 and second == 2_000


def test_step_files_marks_a_missing_file(client, fullredraw_case, tmp_path):
    (case_mod.case_dir("f1", tmp_path) / "seedance_input_mask.png").unlink()
    mask = client.get("/api/case/f1").json()["step_files"]["pad"]["outputs"][1]
    assert mask["exists"] is False and mask["mtime"] is None and mask["size"] is None


def test_step_files_patch_uses_extract_version(client, patch_case, tmp_path):
    d = _mk_extract("p1", tmp_path, "v1", with_result=True)
    (d / "clip.mp4").write_bytes(b"c")
    (d / "clip_mask.png").write_bytes(b"m")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    sf = client.get("/api/case/p1").json()["step_files"]
    assert [f["name"] for f in sf["extract"]["outputs"]] == [
        "clip.mp4", "clip_mask.png", "clip.json"]
    # 还没 adopt：生成组只有输入，不猜 vrgen 轮次目录
    gen = sf["generate"]["groups"][0]
    assert [f["name"] for f in gen["inputs"]] == ["clip.mp4", "clip_mask.png", "clip.json"]
    assert gen["outputs"] == []
    assert [f["name"] for f in sf["merge"]["outputs"]] == ["v1.mp4"]


def test_file_info_uses_repo_relative_path():
    p = config_mod.REPO_ROOT / "cases" / "_rel_test" / "x.mp4"
    info = web_app._file_info(p)
    assert info["exists"] is False
    assert info["rel"] == os.path.join("cases", "_rel_test", "x.mp4")
    assert info["path"].endswith(os.path.join("cases", "_rel_test", "x.mp4"))
