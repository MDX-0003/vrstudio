"""图片流程（patchimg / fullRedrawImg）的 web 装配、门控与选区放行。

CLI 的行为由 ``test_patchimg.py`` / ``test_fullredraw_img.py`` 守护；这里只测
web 这一段：建案例收图片源、op 参数透传、门链（不再落到 gen 的 else 分支）、
草稿段的单一判定、候选图路由与图片预览。
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from starlette.testclient import TestClient

from tests.conftest import synthetic_erp
from vrstudio import case as case_mod
from vrstudio.cli import generate as generate_cli
from vrstudio.media.imageio import write_png
from vrstudio.web import app as web_app
from vrstudio.web import pick_draft

VIEWPORT = {"yaw_deg": 10.0, "pitch_deg": -5.0, "fov_h_deg": 90.0,
            "width": 320, "height": 180}
INNER = {"x": 80, "y": 40, "width": 160, "height": 100}


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


def _png(directory, name="pano.png", width=320, height=160):
    path = directory / name
    write_png(path, synthetic_erp(width, height))
    return path


@pytest.fixture
def patchimg_case(tmp_path, client):
    src = _png(tmp_path)
    case_mod.create("i1", "patchimg", src, root=tmp_path)
    case_mod.set_draft("i1", viewport=VIEWPORT, inner=INNER, root=tmp_path)
    return "i1"


@pytest.fixture
def full_img_case(tmp_path, client):
    src = _png(tmp_path)
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    return "f1"


def _extract_version(tmp_path, name: str, marker: str, result: str | None = None):
    """造一个已抽取版本目录；``result`` 非空时同时放被采用的结果文件。"""
    root = case_mod.case_dir(name, tmp_path) / "extracts" / "v1"
    root.mkdir(parents=True)
    (root / marker).write_text("{}", encoding="utf-8")
    if result:
        (root / result).write_bytes(b"png")
    case_mod.set_extract_current(name, "v1", root=tmp_path)
    return root


def _gates(tmp_path, name: str) -> dict:
    case = case_mod.load(name, tmp_path)
    return web_app._gates(case, web_app._extracts(case))


# ── op 装配 ─────────────────────────────────────────────────────────────


def test_submit_argv_carries_image_params(patchimg_case):
    argv = web_app._op_argv(patchimg_case, "submit", {
        "num": 4, "resolution": "4K", "image_model_id": 44, "session_id": 2062569522605504})
    assert "--num" in argv and argv[argv.index("--num") + 1] == "4"
    assert "--resolution" in argv and argv[argv.index("--resolution") + 1] == "4K"
    assert "--image-model-id" in argv and argv[argv.index("--image-model-id") + 1] == "44"
    assert "--session-id" in argv and argv[argv.index("--session-id") + 1] == "2062569522605504"


def test_submit_argv_omits_absent_image_params(patchimg_case):
    argv = web_app._op_argv(patchimg_case, "submit", {})
    assert "--num" not in argv
    assert "--image-model-id" not in argv
    assert "--session-id" not in argv


def test_submit_argv_carries_resubmit_only_on_submit(patchimg_case):
    """再抽一版只挂真提交；预演不能被它带去开新轮。"""
    assert "--resubmit" in web_app._op_argv(patchimg_case, "submit", {"resubmit": True})
    assert "--resubmit" not in web_app._op_argv(patchimg_case, "submit", {})
    assert "--resubmit" not in web_app._op_argv(patchimg_case, "dry_run", {"resubmit": True})


def test_adopt_argv_carries_index(patchimg_case):
    argv = web_app._op_argv(patchimg_case, "adopt", {"round": "img-abc", "index": 3})
    assert argv[argv.index("--round") + 1] == "img-abc"
    assert argv[argv.index("--index") + 1] == "3"


def test_adopt_argv_without_index_leaves_it_out(patchimg_case):
    argv = web_app._op_argv(patchimg_case, "adopt", {"round": "img-abc"})
    assert "--index" not in argv


# ── 门控 ────────────────────────────────────────────────────────────────


def test_patchimg_gates_block_until_draft_and_extract(tmp_path, client):
    src = _png(tmp_path)
    case_mod.create("i1", "patchimg", src, root=tmp_path)
    gates = _gates(tmp_path, "i1")
    assert gates["pick"]["ok"] is False
    assert "viewport" in gates["pick"]["reason"]
    assert gates["extract"]["ok"] is False
    assert gates["generate"]["ready"] is False
    assert gates["merge"]["ready"] is False

    case_mod.set_draft("i1", viewport=VIEWPORT, inner=INNER, root=tmp_path)
    gates = _gates(tmp_path, "i1")
    assert gates["pick"]["ok"] is True
    assert gates["generate"]["ready"] is False

    _extract_version(tmp_path, "i1", "image.json")
    gates = _gates(tmp_path, "i1")
    assert gates["extract"]["ok"] is True
    assert gates["generate"]["ready"] is True
    assert gates["generate"]["ok"] is False
    assert gates["merge"]["ready"] is False


def test_patchimg_gates_open_after_adopt_and_merge(tmp_path, client):
    src = _png(tmp_path)
    case_mod.create("i1", "patchimg", src, root=tmp_path)
    case_mod.set_draft("i1", viewport=VIEWPORT, inner=INNER, root=tmp_path)
    version = _extract_version(tmp_path, "i1", "image.json", result="result.png")
    case_mod.set_adopt("i1", "img-1", root=tmp_path, index=2)
    gates = _gates(tmp_path, "i1")
    assert gates["generate"]["ok"] is True
    assert gates["merge"]["ready"] is True
    assert gates["merge"]["ok"] is False

    out = case_mod.case_dir("i1", tmp_path) / "out"
    out.mkdir()
    (out / "v1_image.png").write_bytes((version / "result.png").read_bytes())
    gates = _gates(tmp_path, "i1")
    assert gates["merge"]["ok"] is True
    assert gates["compare"]["ok"] is True


def test_fullredrawimg_has_no_pick_step_and_extract_is_ready(tmp_path, client):
    src = _png(tmp_path)
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    gates = _gates(tmp_path, "f1")
    assert "pick" not in gates
    assert gates["extract"]["ready"] is True
    assert gates["extract"]["ok"] is False
    _extract_version(tmp_path, "f1", "image_pad.json")
    gates = _gates(tmp_path, "f1")
    assert gates["extract"]["ok"] is True
    assert "image_pad.json" in gates["extract"]["evidence"]


def test_case_info_survives_a_report_with_infinity(tmp_path, client):
    """旧报告里的 Infinity 不能让 /api/case 变成 500（决策 68）。"""
    src = _png(tmp_path)
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    version = _extract_version(tmp_path, "f1", "image_pad.json")
    (version / "report.json").write_text(
        '{"case": "f1", "registration": {"quality": Infinity}, '
        '"gate": "ok", "verdict": "ok", "reasons": [], "aspect_drift_pct": 0.0}',
        encoding="utf-8")

    r = client.get("/api/case/f1")
    assert r.status_code == 200, r.text
    assert r.json()["image_report"]["registration"]["quality"] is None


def test_fullredrawimg_merge_ready_from_a_library_input(tmp_path, client):
    """拼接输入来自素材库时，没有 adopt 结果也可以拼接（决策 66）。"""
    src = _png(tmp_path)
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    _extract_version(tmp_path, "f1", "image_pad.json")
    gates = _gates(tmp_path, "f1")
    assert gates["merge"]["ready"] is False
    assert "21:9" in gates["merge"]["reason"]

    case_mod.add_assets("f1", [(src, "lighting_ref")], root=tmp_path)
    case_mod.set_merge_input("f1", "i1", root=tmp_path)
    gates = _gates(tmp_path, "f1")
    assert gates["merge"]["ready"] is True


def test_fullredrawimg_step_files_show_the_library_input(tmp_path, client):
    src = _png(tmp_path)
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    _extract_version(tmp_path, "f1", "image_pad.json")
    case_mod.add_assets("f1", [(src, "lighting_ref")], root=tmp_path)
    case_mod.set_merge_input("f1", "i1", root=tmp_path)

    files = web_app._step_files(case_mod.load("f1", tmp_path))
    names = [item["name"] for item in files["merge"]["inputs"]]
    assert names[0].startswith("i1_")
    assert "image_pad.json" in names


def test_fullredrawimg_step_files_show_the_follow_input_image(tmp_path, client):
    """跟随输入写出的 ``<版本>_image_<W>x<H>.png`` 要出现在拼接输出里。"""
    src = _png(tmp_path)
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    _extract_version(tmp_path, "f1", "image_pad.json", result="result.png")
    case_mod.set_adopt("f1", "img-1", root=tmp_path, index=0)
    out = case_mod.case_dir("f1", tmp_path) / "out"
    out.mkdir()
    (out / "v1_image_856x428.png").write_bytes(b"png")

    files = web_app._step_files(case_mod.load("f1", tmp_path))
    outputs = files["merge"]["outputs"]
    assert [item["name"] for item in outputs] == [
        "v1_image_856x428.png", "report.json"]
    assert outputs[0]["exists"] is True


def test_merge_input_op_sets_and_clears_the_pointer(tmp_path, client):
    src = _png(tmp_path)
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    case_mod.add_assets("f1", [(src, "lighting_ref")], root=tmp_path)

    r = client.post("/api/case/f1/assets",
                    json={"op": "merge-input", "asset_id": "i1"})
    assert r.status_code == 200, r.text
    assert case_mod.load("f1", tmp_path).merge_input == "i1"
    assert r.json()["merge_input"] == "i1"

    r = client.post("/api/case/f1/assets",
                    json={"op": "merge-input", "asset_id": ""})
    assert r.status_code == 200, r.text
    assert case_mod.load("f1", tmp_path).merge_input is None


def _upload_png(client, name: str, payload: bytes) -> str:
    session = client.post("/api/uploads", json={"name": name, "size": len(payload)}).json()
    upload_id = session["upload_id"]
    r = client.put(f"/api/uploads/{upload_id}", content=payload,
                   headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 200
    return upload_id


def test_merge_input_op_uploads_and_selects(tmp_path, client):
    src = _png(tmp_path)
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    payload = src.read_bytes()

    upload_id = _upload_png(client, "edited.png", payload)
    r = client.post("/api/case/f1/assets",
                    json={"op": "merge-input", "upload_ids": [upload_id]})
    assert r.status_code == 200, r.text
    case = case_mod.load("f1", tmp_path)
    assert case.merge_input is not None
    asset = case.asset(case.merge_input)
    assert (asset.role, asset.kind) == ("merge_input", "image")

    # 同一份文件再传一次：指向同一条，素材库不新增。
    again = _upload_png(client, "edited-copy.png", payload)
    r = client.post("/api/case/f1/assets",
                    json={"op": "merge-input", "upload_ids": [again]})
    assert r.status_code == 200, r.text
    assert len(case_mod.load("f1", tmp_path).assets) == 1


def test_merge_input_op_refuses_a_video_asset(tmp_path, client):
    src = _png(tmp_path)
    case_mod.create("f1", "fullRedrawImg", src, root=tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a video")
    case_mod.add_assets("f1", [(video, "edit_master")], root=tmp_path)

    r = client.post("/api/case/f1/assets",
                    json={"op": "merge-input", "asset_id": "v1"})
    assert r.status_code == 400 and "拼接输入必须是图片" in r.json()["error"]


# ── 建案例收图片源 ───────────────────────────────────────────────────────


def test_create_patchimg_from_sources_image(client, sources, tmp_path):
    _png(sources)
    r = client.post("/api/cases", json={"flow": "patchimg", "video": "pano.png"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "pano"
    case = case_mod.load("pano", tmp_path)
    assert case.flow == "patchimg"
    assert case.viewport and case.inner
    assert case.frames is None


def test_create_fullredrawimg_has_no_draft(client, sources, tmp_path):
    _png(sources)
    r = client.post("/api/cases",
                    json={"flow": "fullRedrawImg", "video": "pano.png", "name": "f9"})
    assert r.status_code == 200, r.text
    case = case_mod.load("f9", tmp_path)
    assert case.flow == "fullRedrawImg"
    assert case.viewport is None and case.inner is None


def test_create_rejects_a_non_2_to_1_image(client, sources):
    _png(sources, name="flat.png", width=300, height=160)
    r = client.post("/api/cases", json={"flow": "patchimg", "video": "flat.png"})
    assert r.status_code == 409
    assert "300x160" in r.json()["error"]


def test_create_video_flow_still_requires_a_video(client, sources):
    _png(sources)
    r = client.post("/api/cases", json={"flow": "patch", "video": "pano.png"})
    assert r.status_code == 409
    assert "视频" in r.json()["error"]


def test_sources_lists_images_separately(client, sources):
    _png(sources)
    (sources / "clip.mp4").write_bytes(b"x")
    payload = client.get("/api/sources").json()
    assert payload["images"] == ["pano.png"]
    assert payload["videos"] == ["clip.mp4"]
    assert "pano.png" in payload["files"]


# ── 草稿段单一判定 ───────────────────────────────────────────────────────


def test_draft_accepts_patchimg_and_rejects_frames(client, patchimg_case):
    r = client.post(f"/api/case/{patchimg_case}/pick-draft", json={"inner": INNER})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/case/{patchimg_case}/pick-draft",
                    json={"frames": {"start": 0, "end": 3}})
    assert r.status_code == 400
    assert "没有 frames" in r.json()["error"]


def test_draft_rejects_fullredrawimg_with_a_flow_message(client, full_img_case):
    r = client.post(f"/api/case/{full_img_case}/pick-draft",
                    json={"viewport": VIEWPORT})
    assert r.status_code == 400
    assert "没有选区草稿" in r.json()["error"]


def test_geometry_endpoints_reject_fullredrawimg(client, full_img_case):
    for path, body in (("viewport", {"fx": 0.5, "fy": 0.5}),
                       ("inner", {"fx": 0.1, "fy": 0.1, "fw": 0.2, "fh": 0.2}),
                       ("nudge", {"dyaw": 1, "dpitch": 0})):
        r = client.post(f"/api/case/{full_img_case}/{path}", json=body)
        assert r.status_code == 400, (path, r.text)
        assert "没有选区草稿" in r.json()["error"]


def test_viewport_size_and_fov_work_for_patchimg(client, patchimg_case):
    r = client.post(f"/api/case/{patchimg_case}/viewport-size",
                    json={"width": 3840, "height": 2160})
    assert r.status_code == 200, r.text
    case = case_mod.load(patchimg_case, web_app._ctx["cases_root"])
    draft = pick_draft.load_draft(case, web_app._ctx["cases_root"])
    assert draft.viewport["width"] == 3840
    assert draft.inner["width"] == int(INNER["width"] * 12)


# ── 候选图与预览 ─────────────────────────────────────────────────────────


def test_preview_store_handles_an_image_source(tmp_path, client, patchimg_case):
    store = web_app.get_store(patchimg_case)
    assert store is not None
    assert store.is_image is True
    assert store.frame_count == 1 and store.fps == 0.0
    assert store.viewport_png()[:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(ValueError, match="out of range"):
        store.viewport_frame_path(1)


def test_range_preset_load_restores_patchimg_geometry(client, patchimg_case, tmp_path):
    client.post(f"/api/case/{patchimg_case}/pick-draft",
                json={"inner": {**INNER, "x": 1}})
    r = client.post(f"/api/case/{patchimg_case}/range-presets/r1/load")
    assert r.status_code == 200, r.text
    case = case_mod.load(patchimg_case, tmp_path)
    draft = pick_draft.load_draft(case, tmp_path)
    assert draft.viewport == VIEWPORT
    assert draft.inner == INNER


def test_case_info_exposes_image_rounds_and_report(tmp_path, client, patchimg_case):
    version = _extract_version(tmp_path, patchimg_case, "image.json", result="result.png")
    case_mod.set_adopt(patchimg_case, "img-abc12345", root=tmp_path, index=2)
    (version / "report.json").write_text(json.dumps({
        "local": {"support_pixels": 100, "changed_inside": 90, "maxdiff_outside": 0},
    }), encoding="utf-8")
    runs = tmp_path / "vrgen_runs" / patchimg_case / "img-abc12345"
    runs.mkdir(parents=True)
    shot = runs / "2.png"
    write_png(shot, synthetic_erp(320, 160))
    (runs / "run.json").write_text(json.dumps({
        "stage": "done", "gate": {"verdict": "ok", "kind": "image"},
        "results": ["1.png", str(shot)],
    }), encoding="utf-8")

    payload = client.get(f"/api/case/{patchimg_case}").json()
    assert set(payload["gates"]) == {"pick", "extract", "generate", "merge", "compare"}
    assert payload["adopt_index"] == 2
    rounds = payload["image_rounds"]
    assert rounds and rounds[0]["round"] == "img-abc12345"
    assert rounds[0]["adopted"] is True
    assert rounds[0]["gate"] == "ok"
    assert [row["index"] for row in rounds[0]["results"]] == [1, 2]
    assert rounds[0]["results"][1]["exists"] is True
    assert rounds[0]["results"][1]["url"].endswith("/img-abc12345/2")
    assert payload["image_report"]["local"]["changed_inside"] == 90


def test_round_image_route_serves_a_candidate(tmp_path, client, patchimg_case):
    runs = tmp_path / "vrgen_runs" / patchimg_case / "img-abc12345"
    runs.mkdir(parents=True)
    shot = runs / "1.png"
    write_png(shot, synthetic_erp(320, 160))
    (runs / "run.json").write_text(
        json.dumps({"results": [str(shot)]}), encoding="utf-8")
    r = client.get(f"/img/round/{patchimg_case}/img-abc12345/1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    r = client.get(f"/img/round/{patchimg_case}/img-abc12345/2")
    assert r.status_code == 404


def test_proxy_endpoint_rejects_image_flows(client, patchimg_case):
    r = client.get(f"/api/case/{patchimg_case}/proxy")
    assert r.status_code == 400
    assert "没有预览代理" in r.json()["error"]


# ── CLI 值域 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("args", [["--num", "5"], ["--resolution", "8K"]])
def test_image_submit_rejects_out_of_domain_values(tmp_path, patchimg_case, args):
    from typer.testing import CliRunner
    result = CliRunner().invoke(generate_cli.app, [
        "submit", "--case", patchimg_case, "--cases-dir", str(tmp_path), *args])
    assert result.exit_code == 2
