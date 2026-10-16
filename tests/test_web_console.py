"""web 控制台单测（Starlette TestClient，不起真服务、不真跑队列任务）。

覆盖：页面路由、草稿 API（pick 三写路径 + nudge 回绕）、软向导门控判据的
递进、op 注册表的守卫。vrgen 不可用路径也在本测试环境下天然走到。
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio.web import app as web_app

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
        {"segments": [{"viewport": VIEWPORT, "inner": INNER}]}, ensure_ascii=False),
        encoding="utf-8")
    if with_result:
        (d / "result.mp4").write_bytes(b"r")
    return d


# ── 页面与列表 ────────────────────────────────────────────────────────────


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "vrstudio" in r.text


def test_wizard_page_renders_flow(client, patch_case):
    r = client.get("/case/p1")
    assert r.status_code == 200 and "patch" in r.text and "p1" in r.text


def test_wizard_page_404_for_unknown(client):
    assert client.get("/case/nope").status_code == 404


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
    assert r.status_code == 400 and "pad" in r.text


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


def test_gates_progress_with_extract_and_result(client, patch_case, tmp_path):
    _mk_extract("p1", tmp_path, "v1", with_result=True)
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    case_mod.set_adopt("p1", "manual-x", root=tmp_path)
    info = client.get("/api/case/p1").json()
    for step in ("pick", "extract", "generate"):
        assert info["gates"][step]["ok"] is True
    assert info["gates"]["merge"]["ready"] is True
    assert info["extracts"][0]["version"] == "v1" and info["extracts"][0]["has_result"]
