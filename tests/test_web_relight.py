"""relight 流程的 web 装配、门控与槽位放行（决策 59–61）。

CLI 由 ``test_relight.py`` / ``test_relight_video.py`` 守护，case 模型由
``test_casemodel.py`` 守护；这里只测 web 这一段：不绑源建 case、槽位 op、
两条门链、只出 relight 的 op argv、以及「不碰 vrgen」的证明
（``/api/case`` 的 prompt 与 generation_params 是空表）。
"""

from __future__ import annotations

import numpy as np
import pytest
from starlette.testclient import TestClient

from tests.conftest import synthetic_erp
from vrstudio import case as case_mod
from vrstudio.media.imageio import write_png
from vrstudio.web import app as web_app

W, H = 320, 160


@pytest.fixture(autouse=True)
def _clear_preview_stores():
    web_app._stores.clear()
    yield
    web_app._stores.clear()


@pytest.fixture
def sources(tmp_path, monkeypatch):
    d = tmp_path / "sources"
    d.mkdir()
    monkeypatch.setattr(web_app, "sources_dir", lambda: d)
    return d


@pytest.fixture
def client(tmp_path, sources, monkeypatch):
    monkeypatch.setattr(web_app, "_vrgen_exe", lambda: "definitely-not-a-real-vrgen")
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "vrgen_runs")
    return TestClient(web_app.build_app(cases_root=tmp_path))


def _png(directory, name="pano.png", width=W, height=H):
    path = directory / name
    write_png(path, synthetic_erp(width, height))
    return path


def _video(path):
    """占位视频文件：relight 的 web 这一段只按扩展名分派，不解码。"""
    path.write_bytes(b"fake mp4")
    return path


# ── 建 case ────────────────────────────────────────────────────────────────


def test_create_relight_case_without_source(tmp_path, client):
    rsp = client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    assert rsp.status_code == 200, rsp.text
    case = case_mod.load("rl1", tmp_path)
    assert case.flow == "relight" and case.assets == []
    text = case_mod.case_toml("rl1", tmp_path).read_text(encoding="utf-8")
    assert "[source]" not in text


def test_create_relight_rejects_source_and_upload(client):
    rsp = client.post("/api/cases", json={"flow": "relight", "name": "rl1",
                                          "video": "a.mp4"})
    assert rsp.status_code == 400
    assert "不绑源文件" in rsp.json()["error"]
    rsp = client.post("/api/cases", json={"flow": "relight", "name": "rl1",
                                          "upload_id": "abc"})
    assert rsp.status_code == 400


def test_create_relight_requires_case_name(client):
    rsp = client.post("/api/cases", json={"flow": "relight"})
    assert rsp.status_code == 400
    assert "case 名" in rsp.json()["error"]


# ── 槽位 ──────────────────────────────────────────────────────────────────


def test_slot_op_sets_both_slots(tmp_path, client):
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    gt = _png(tmp_path, "gt.png")
    atmo = _png(tmp_path, "atmo.png")
    rsp = client.post("/api/case/rl1/assets",
                      json={"op": "slot", "role": "relight_gt", "path": str(gt)})
    assert rsp.status_code == 200, rsp.text
    rsp = client.post("/api/case/rl1/assets",
                      json={"op": "slot", "role": "relight_atmo", "path": str(atmo)})
    assert rsp.status_code == 200, rsp.text
    case = case_mod.load("rl1", tmp_path)
    gt_asset, atmo_asset = case.relight_slots()
    assert gt_asset is not None and atmo_asset is not None
    assert rsp.json()["relight"]["gt"]["asset"] == gt_asset.id


def test_slot_op_rejects_video_atmo(tmp_path, client):
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    video = _video(tmp_path / "atmo.mp4")
    rsp = client.post("/api/case/rl1/assets",
                      json={"op": "slot", "role": "relight_atmo", "path": str(video)})
    assert rsp.status_code == 400
    assert "relight_atmo" in rsp.json()["error"]


def test_slot_op_rejects_unknown_role(tmp_path, client):
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    gt = _png(tmp_path)
    rsp = client.post("/api/case/rl1/assets",
                      json={"op": "slot", "role": "motion_ref", "path": str(gt)})
    assert rsp.status_code == 400
    assert "relight_gt" in rsp.json()["error"]


def test_slot_op_picks_existing_candidate(tmp_path, client):
    """只改指针：库里已有两条氛围图候选时，可以来回切换而不重新上传。"""
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    first = _png(tmp_path, "a1.png")
    second = tmp_path / "a2.png"
    write_png(second, np.clip(synthetic_erp(W, H).astype(np.int16) - 20, 0, 255).astype(np.uint8))
    for path in (first, second):
        rsp = client.post("/api/case/rl1/assets",
                          json={"op": "slot", "role": "relight_atmo", "path": str(path)})
        assert rsp.status_code == 200, rsp.text
    case = case_mod.load("rl1", tmp_path)
    atmo_ids = [a.id for a in case.assets if a.role == case_mod.RELIGHT_ATMO]
    assert len(atmo_ids) == 2
    assert case.relight_slots()[1].id == atmo_ids[1]

    rsp = client.post("/api/case/rl1/assets",
                      json={"op": "slot", "role": "relight_atmo",
                            "asset_id": atmo_ids[0]})
    assert rsp.status_code == 200, rsp.text
    assert case_mod.load("rl1", tmp_path).relight_slots()[1].id == atmo_ids[0]


# ── 门控与 payload ────────────────────────────────────────────────────────


def test_gates_wait_for_both_slots(tmp_path, client):
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    info = client.get("/api/case/rl1").json()
    gates = info["gates"]
    assert set(gates) == {"assets", "relight", "compare"}
    assert gates["assets"]["ok"] is False
    assert "底版" in gates["assets"]["reason"]
    assert gates["relight"]["ready"] is False

    client.post("/api/case/rl1/assets",
                json={"op": "slot", "role": "relight_gt",
                      "path": str(_png(tmp_path, "gt.png"))})
    info = client.get("/api/case/rl1").json()
    assert info["gates"]["assets"]["ok"] is False
    assert "氛围图" in info["gates"]["assets"]["reason"]

    client.post("/api/case/rl1/assets",
                json={"op": "slot", "role": "relight_atmo",
                      "path": str(_png(tmp_path, "atmo.png"))})
    info = client.get("/api/case/rl1").json()
    assert info["gates"]["assets"]["ok"] is True
    assert info["gates"]["relight"]["ready"] is True
    assert info["gates"]["relight"]["ok"] is False      # 还没跑过
    assert info["gates"]["compare"]["ok"] is False


def test_case_payload_carries_slots_and_no_generation(tmp_path, client):
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    client.post("/api/case/rl1/assets",
                json={"op": "slot", "role": "relight_gt",
                      "path": str(_png(tmp_path, "gt.png"))})
    info = client.get("/api/case/rl1").json()
    assert info["relight"]["gt"] is not None
    assert info["relight"]["atmo"] is None
    assert info["relight"]["media"] == "image"
    assert info["relight"]["current"] is None
    # 不碰 vrgen：没有提示词与生成参数。
    assert info["prompt"] == {}
    assert info["generation_params"] == {}
    assert info["image_rounds"] == []
    assert info["extracts"] == []


def test_compare_gate_opens_after_output(tmp_path, client):
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    client.post("/api/case/rl1/assets",
                json={"op": "slot", "role": "relight_gt",
                      "path": str(_png(tmp_path, "gt.png"))})
    client.post("/api/case/rl1/assets",
                json={"op": "slot", "role": "relight_atmo",
                      "path": str(_png(tmp_path, "atmo.png"))})
    out = case_mod.case_dir("rl1", tmp_path) / "out"
    out.mkdir()
    write_png(out / "20261030-120000_relit.png", synthetic_erp(W, H))
    info = client.get("/api/case/rl1").json()
    assert info["gates"]["relight"]["ok"] is True
    assert info["gates"]["compare"]["ok"] is True
    assert info["relight"]["current"]["name"].endswith("_relit.png")
    assert info["outputs"] and info["outputs"][0]["name"].endswith("_relit.png")


def test_pinned_current_wins_over_newest(tmp_path, client):
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    client.post("/api/case/rl1/assets",
                json={"op": "slot", "role": "relight_gt",
                      "path": str(_png(tmp_path, "gt.png"))})
    client.post("/api/case/rl1/assets",
                json={"op": "slot", "role": "relight_atmo",
                      "path": str(_png(tmp_path, "atmo.png"))})
    out = case_mod.case_dir("rl1", tmp_path) / "out"
    out.mkdir()
    write_png(out / "old_relit.png", synthetic_erp(W, H))
    write_png(out / "new_relit.png", synthetic_erp(W, H))
    case_mod.set_relight("rl1", {"current": "out/old_relit.png"}, tmp_path)
    info = client.get("/api/case/rl1").json()
    assert info["relight"]["current"]["name"] == "old_relit.png"


def test_sample_report_is_exposed_for_the_latest_sample(tmp_path, client):
    """前端靠 sample_report 显示这次小样的时间戳与分段耗时。"""
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    sample = case_mod.case_dir("rl1", tmp_path) / "out" / "samples" / "20261102-120000"
    sample.mkdir(parents=True)
    write_png(sample / "sample_000000.png", synthetic_erp(W, H))
    report = sample.with_suffix(".json")
    report.write_text('{"kind": "sample", "seconds": 1.0}', encoding="utf-8")

    info = client.get("/api/case/rl1").json()
    payload = info["relight"]
    assert payload["sample_report"]["name"] == "20261102-120000.json"
    assert payload["sample_report"]["exists"] is True
    assert payload["sample_report"]["open_url"]
    assert len(payload["samples"]) == 1


# ── op 装配 ──────────────────────────────────────────────────────────────


def _ready_case(tmp_path, gt_kind: str) -> str:
    case_mod.create("rl", "relight", root=tmp_path)
    gt = _png(tmp_path, "gt.png") if gt_kind == "image" else _video(tmp_path / "gt.mp4")
    case_mod.add_relight_candidate("rl", case_mod.RELIGHT_GT, gt, tmp_path)
    case_mod.add_relight_candidate("rl", case_mod.RELIGHT_ATMO,
                              _png(tmp_path, "atmo.png"), tmp_path)
    return "rl"


def test_op_argv_uses_case_and_shared_params(tmp_path, client):
    name = _ready_case(tmp_path, "image")
    argv = web_app._op_argv(name, "relight", {"scene": "day", "strength": 0.5,
                                              "highlight_clip": True})
    assert "relight" in argv and "--case" in argv and name in argv
    # 槽位解析、产物命名与 current 回写都在 CLI：web 不自己拼 --gt/--atmo/--out。
    for flag in ("--gt", "--atmo", "--out"):
        assert flag not in argv
    assert "--sample" not in argv
    assert "--ref-frame" not in argv
    assert "--scene" in argv and "day" in argv
    assert "--highlight-clip" in argv
    assert argv[-1] == "--quiet"


def test_op_argv_video_gt_passes_ref_frame_and_crf(tmp_path, client):
    name = _ready_case(tmp_path, "video")
    argv = web_app._op_argv(name, "relight", {"ref_frame": 12, "crf": 0})
    assert "--ref-frame" in argv and "12" in argv
    assert "--crf" in argv and "0" in argv


def test_op_argv_sample_passes_ref_frame_but_not_crf(tmp_path, client):
    name = _ready_case(tmp_path, "video")
    argv = web_app._op_argv(name, "relight", {"sample": 5, "ref_frame": 12, "crf": 0})
    assert "--sample" in argv and "5" in argv
    # 小样也要能钉参考帧；编码质量只对整段有意义。
    assert "--ref-frame" in argv and "12" in argv
    assert "--crf" not in argv


def test_relight_draft_endpoint_persists_and_returns_no_prompt(tmp_path, client):
    """草稿端点：写进 [relight] 并返回面板数据。

    这条曾经 500：端点回头构造提示词，而 relight 没有模板（``template_for`` KeyError）。
    """
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    rsp = client.post("/api/case/rl1/relight",
                      json={"scene": "day", "strength": 0.8, "ref_frame": 3})
    assert rsp.status_code == 200, rsp.text
    body = rsp.json()
    assert body["prompt"] == {}
    assert body["generation_params"] == {}
    assert body["relight"]["draft"] == {"scene": "day", "strength": 0.8, "ref_frame": 3}
    assert case_mod.load("rl1", tmp_path).relight["scene"] == "day"


def test_relight_draft_endpoint_rejects_unknown_key(tmp_path, client):
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    rsp = client.post("/api/case/rl1/relight", json={"strengh": 0.5})
    assert rsp.status_code == 400
    assert "不认识的键" in rsp.json()["error"]


# ── 选区类端点不放行 ─────────────────────────────────────────────────────


def test_viewport_endpoint_rejects_relight(tmp_path, client):
    client.post("/api/cases", json={"flow": "relight", "name": "rl1"})
    rsp = client.post("/api/case/rl1/viewport", json={"fx": 0.5, "fy": 0.5})
    assert rsp.status_code == 400
    assert "选区草稿" in rsp.json()["error"]
