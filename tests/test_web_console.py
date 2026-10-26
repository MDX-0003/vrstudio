"""web 控制台单测（Starlette TestClient，不起真服务、不真跑队列任务）。

覆盖：页面路由、草稿 API（pick 三写路径 + nudge 回绕）、软向导门控判据的
递进、op 注册表的守卫。vrgen 不可用路径也在本测试环境下天然走到。
"""

from __future__ import annotations

import json
import os
import time
import types
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio import config as config_mod
from vrstudio.cli import generate as gen_mod
from vrstudio import keyframe
from vrstudio.media.videoio import VideoInfo
from vrstudio.redraw.geometry import PadSpec, plan_pad
from vrstudio.redraw.manifest import VideoSettings, from_plan
from vrstudio.web import app as web_app
from vrstudio.web import tasks as tasks_mod

VIEWPORT = {"yaw_deg": 10.0, "pitch_deg": -5.0, "fov_h_deg": 90.0,
            "width": 320, "height": 180}
INNER = {"x": 80, "y": 40, "width": 160, "height": 100}
FRAMES = {"start": 0, "end": 5}


@pytest.fixture(autouse=True)
def _clear_preview_stores():
    """PreviewStore 是模块级缓存；每个用例用自己的 tmp 目录，不能串。"""
    web_app._stores.clear()
    yield
    web_app._stores.clear()


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


def test_index_redirects_to_next_by_default(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/next/"


def test_index_falls_back_to_legacy_by_config(client, monkeypatch):
    monkeypatch.setattr(web_app, "_default_frontend", lambda: "legacy")
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
    assert "生成设置" in r.text and "提交生成" in r.text
    assert 'id="submitBar"' in r.text and 'id="genMode"' in r.text
    assert 'id="sessProject"' in r.text
    assert 'id="resultStrip"' in r.text
    assert "Session 列表与选用" not in r.text and "logBox" not in r.text


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


def _upload(client, name: str, data: bytes, *, chunks: list[bytes] | None = None):
    r = client.post("/api/uploads", json={"name": name, "size": len(data)})
    assert r.status_code == 200, r.text
    upload_id = r.json()["upload_id"]
    offset = 0
    for part in chunks or [data]:
        r = client.put(f"/api/uploads/{upload_id}?offset={offset}", content=part)
        assert r.status_code == 200, r.text
        offset += len(part)
    r = client.post(f"/api/uploads/{upload_id}/complete")
    assert r.status_code == 200, r.text
    return upload_id, r.json()


def test_upload_chunks_complete_and_add_asset(client, gen_case, tmp_path):
    data = b"fake png bytes for upload"
    upload_id, done = _upload(client, "ref.png", data,
                              chunks=[data[:5], data[5:12], data[12:]])
    assert done["name"] == "ref.png" and done["size"] == len(data)
    assert len(done["sha256"]) == 64

    status = client.get(f"/api/uploads/{upload_id}").json()
    assert status["status"] == "complete" and status["received"] == len(data)

    r = client.post("/api/case/g1/assets",
                    json={"op": "add", "upload_ids": [upload_id],
                          "roles": ["subject_ref"]})
    assert r.status_code == 200, r.text
    assets = r.json()["assets"]
    assert [a["id"] for a in assets] == ["v1", "i1"]
    assert any((case_mod.case_dir("g1", tmp_path) / "assets").glob("i1_ref.png"))
    assert not (tmp_path / ".uploads" / "complete" / upload_id).exists()


def test_upload_rejects_discontinuous_offset_and_duplicate_file(client, gen_case, tmp_path):
    data = b"same bytes"
    r = client.post("/api/uploads", json={"name": "ref.png", "size": len(data)})
    upload_id = r.json()["upload_id"]
    r = client.put(f"/api/uploads/{upload_id}?offset=3", content=data)
    assert r.status_code == 409 and r.json()["received"] == 0
    r = client.put(f"/api/uploads/{upload_id}?offset=0", content=data)
    assert r.status_code == 200
    r = client.post(f"/api/uploads/{upload_id}/complete")
    assert r.status_code == 200

    r = client.post("/api/case/g1/assets",
                    json={"op": "add", "upload_ids": [upload_id],
                          "roles": ["subject_ref"]})
    assert r.status_code == 200, r.text

    second_id, _ = _upload(client, "ref-copy.png", data)
    r = client.post("/api/case/g1/assets",
                    json={"op": "add", "upload_ids": [second_id],
                          "roles": ["scene_ref"]})
    assert r.status_code == 409 and "已存在" in r.json()["error"]


def test_upload_rejects_bad_type_and_size(client):
    r = client.post("/api/uploads", json={"name": "notes.txt", "size": 4})
    assert r.status_code == 400 and "不支持" in r.json()["error"]
    r = client.post("/api/uploads", json={"name": "ref.png", "size": 0})
    assert r.status_code == 400
    r = client.post("/api/uploads", json={"name": "ref.png", "size": 3})
    assert r.status_code == 200
    upload_id = r.json()["upload_id"]
    r = client.put(f"/api/uploads/{upload_id}?offset=0", content=b"toolong")
    assert r.status_code == 400


def test_upload_create_cleans_expired_sessions(client, tmp_path):
    r = client.post("/api/uploads", json={"name": "old.png", "size": 4})
    old_id = r.json()["upload_id"]
    meta_path = tmp_path / ".uploads" / old_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_at"] = (
        datetime.now().astimezone() - timedelta(hours=25)
    ).isoformat(timespec="seconds")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    r = client.post("/api/uploads", json={"name": "new.png", "size": 4})
    assert r.status_code == 200
    assert not (tmp_path / ".uploads" / old_id).exists()
    assert (tmp_path / ".uploads" / r.json()["upload_id"]).is_dir()


# ── /file：按根寻址的原片直开 ─────────────────────────────────────────────


def test_media_extract_route_is_retired(client, patch_case):
    assert client.get(f"/media/extract/{patch_case}/v1/clip.mp4").status_code == 404


def _file_roots(tmp_path, monkeypatch):
    """三个白名单根都落在 tmp_path 下，避免依赖仓库内数据。"""
    sources = tmp_path / "sources"
    sources.mkdir()
    runs = tmp_path / "vrgen_runs"
    runs.mkdir()
    monkeypatch.setattr(web_app, "sources_dir", lambda: sources)
    monkeypatch.setattr(web_app, "_workdir", lambda: runs)
    return sources, runs


def test_file_route_serves_each_whitelist_root(client, tmp_path, monkeypatch):
    sources, runs = _file_roots(tmp_path, monkeypatch)
    case_dir = tmp_path / "c1"
    case_dir.mkdir()
    (case_dir / "clip.mp4").write_bytes(b"0123456789")
    (case_dir / "clip_mask.png").write_bytes(b"\x89PNG")
    (sources / "src.mp4").write_bytes(b"source")
    round_dir = runs / "c1" / "r1"
    round_dir.mkdir(parents=True)
    (round_dir / "run.json").write_text("{}", encoding="utf-8")

    mp4 = client.get("/file", params={"root": "cases", "p": "c1/clip.mp4"})
    assert mp4.status_code == 200 and mp4.content == b"0123456789"
    assert mp4.headers["content-type"] == "video/mp4"
    assert mp4.headers["content-disposition"].startswith("inline")
    assert mp4.headers["x-content-type-options"] == "nosniff"

    png = client.get("/file", params={"root": "cases", "p": "c1/clip_mask.png"})
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"

    blob = client.get("/file", params={"root": "workdir", "p": "c1/r1/run.json"})
    assert blob.status_code == 200
    assert blob.headers["content-type"] == "application/json"

    src = client.get("/file", params={"root": "sources", "p": "src.mp4"})
    assert src.status_code == 200 and src.content == b"source"


def test_file_route_supports_range(client, tmp_path, monkeypatch):
    _file_roots(tmp_path, monkeypatch)
    case_dir = tmp_path / "c1"
    case_dir.mkdir()
    (case_dir / "clip.mp4").write_bytes(b"0123456789")
    r = client.get("/file", params={"root": "cases", "p": "c1/clip.mp4"},
                   headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.content == b"0123"
    assert r.headers["content-range"] == "bytes 0-3/10"


@pytest.mark.parametrize("query", [
    {"root": "cases", "p": "../outside.txt"},
    {"root": "cases", "p": "c1"},
    {"root": "cases", "p": "nope.mp4"},
    {"root": "cases", "p": ""},
    {"root": "evil", "p": "c1/clip.mp4"},
    {"root": "", "p": "c1/clip.mp4"},
])
def test_file_route_rejects_bad_targets(client, tmp_path, monkeypatch, query):
    _file_roots(tmp_path, monkeypatch)
    case_dir = tmp_path / "c1"
    case_dir.mkdir()
    (case_dir / "clip.mp4").write_bytes(b"x")
    (tmp_path.parent / "outside.txt").write_bytes(b"secret")
    assert client.get("/file", params=query).status_code == 404


def test_file_route_rejects_absolute_and_encoded_traversal(
        client, tmp_path, monkeypatch):
    _file_roots(tmp_path, monkeypatch)
    case_dir = tmp_path / "c1"
    case_dir.mkdir()
    (case_dir / "clip.mp4").write_bytes(b"x")
    (tmp_path.parent / "outside.txt").write_bytes(b"secret")

    absolute = str((case_dir / "clip.mp4").resolve())
    assert client.get("/file", params={"root": "cases", "p": absolute}).status_code == 404
    assert client.get("/file?root=cases&p=c1%2F..%2F..%2Foutside.txt").status_code == 404
    assert client.get("/file?root=cases&p=%2E%2E%2Foutside.txt").status_code == 404


def test_file_route_rejects_symlink_escape(client, tmp_path, monkeypatch):
    _file_roots(tmp_path, monkeypatch)
    case_dir = tmp_path / "c1"
    case_dir.mkdir()
    outside = tmp_path.parent / "outside_link.txt"
    outside.write_bytes(b"secret")
    link = case_dir / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("本机不允许创建符号链接")
    assert client.get("/file", params={"root": "cases", "p": "c1/link.txt"}).status_code == 404


# ── file_entry：payload 侧的 open_url ────────────────────────────────────


def test_step_file_entries_open_url_does_not_depend_on_repo_rel(
        client, patch_case, tmp_path, monkeypatch):
    """临时 cases 根在仓库外：rel 为 None，open_url 仍按 cases 根生成。"""
    _file_roots(tmp_path, monkeypatch)
    d = _mk_extract("p1", tmp_path, "v1")
    (d / "clip.mp4").write_bytes(b"clip")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    info = client.get("/api/case/p1").json()
    clip = next(row for row in info["step_files"]["extract"]["outputs"]
                if row["name"] == "clip.mp4")
    assert clip["exists"] is True
    assert clip["rel"] is None
    assert clip["open_url"].startswith("/file?root=cases&p=")
    assert client.get(clip["open_url"]).content == b"clip"

    missing = next(row for row in info["step_files"]["extract"]["outputs"]
                   if row["name"] == "first_frame_raw.png")
    assert missing["exists"] is False
    assert "open_url" not in missing


def test_output_entry_uses_open_url_instead_of_legacy_url(
        client, patch_case, tmp_path, monkeypatch):
    _file_roots(tmp_path, monkeypatch)
    out = tmp_path / "p1" / "out"
    out.mkdir(parents=True)
    (out / "v1_frame.mp4").write_bytes(b"out")
    monkeypatch.setattr(web_app, "_video_frames", lambda path: 6)
    entry = client.get("/api/case/p1").json()["outputs"][0]
    assert entry["file"] == "v1_frame.mp4"
    assert "url" not in entry
    assert entry["open_url"].startswith("/file?root=cases&p=")
    assert entry["proxy_url"].startswith("/video/outproxy/")


def test_rounds_include_result_and_run_json_file_entries(client, tmp_path, monkeypatch):
    runs = tmp_path / "vrgen_runs"
    round_dir = runs / "x" / "r1"
    round_dir.mkdir(parents=True)
    (round_dir / "result.mp4").write_bytes(b"r")
    (round_dir / "run.json").write_text(
        json.dumps({"result": str(round_dir / "result.mp4")}), encoding="utf-8")
    (runs / "x" / "r2").mkdir()
    monkeypatch.setattr(web_app, "_workdir", lambda: runs)
    monkeypatch.setattr(web_app.subprocess, "run", lambda *a, **k:
                        web_app.subprocess.CompletedProcess(
                            a[0], 0, stdout=(
                                "  * r1 mglc checked gate=ok result=yes 2026-10-26T01:00:00Z\n"
                                "  * r2 mglc failed gate=bad result=no 2026-10-26T02:00:00Z\n"),
                            stderr=""))
    files = {row["round"]: row["files"] for row in web_app._rounds("x")["rounds"]}
    assert files["r1"]["result"]["exists"] is True
    assert files["r1"]["result"]["open_url"].startswith("/file?root=workdir&p=")
    assert files["r1"]["run_json"]["exists"] is True
    assert files["r2"]["result"]["exists"] is False
    assert "open_url" not in files["r2"]["result"]
    assert files["r2"]["run_json"]["exists"] is False


def test_keyframe_pick_and_sheet_candidates_carry_open_url(
        client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    _add_image("p1", tmp_path, "first", "first_frame")
    cdir = case_mod.case_dir("p1", tmp_path)
    keyframe.save(cdir, keyframe.State(
        extract="v1", sheet="i1", first=keyframe.Pick("img-a", 1, "i2")))
    info = client.get("/api/case/p1").json()
    first = info["keyframe"]["first"]
    assert first["asset"] == "i2"
    assert first["file"]["exists"] is True
    assert first["file"]["name"].endswith(".png")
    assert first["file"]["open_url"].startswith("/file?root=cases&p=")
    sheet = info["keyframe"]["sheet_candidates"][0]
    assert sheet["open_url"].startswith("/file?root=cases&p=")


def test_external_source_has_path_but_no_open_url(client, tmp_path):
    src = tmp_path.parent / "external_source.mp4"
    src.write_bytes(b"external")
    case_mod.create("f9", "fullredraw", src, root=tmp_path)
    info = client.get("/api/case/f9").json()
    assert info["source_file"]["exists"] is True
    assert info["source_file"]["rel"] is None
    assert "open_url" not in info["source_file"]


def test_case_meta_revision_and_conflict(client, patch_case, tmp_path):
    info = client.get(f"/api/case/{patch_case}").json()
    assert info["meta"]["revision"] == 0
    assert info["meta"]["owner_name"] == "访客"
    assert (case_mod.case_dir(patch_case, tmp_path) / "case-meta.json").is_file()

    body = {"frames": {"start": 0, "end": 4}, "revision": 0}
    r = client.post(f"/api/case/{patch_case}/draft", json=body)
    assert r.status_code == 200, r.text
    assert r.headers["x-case-revision"] == "1"

    r = client.post(f"/api/case/{patch_case}/draft", json=body)
    assert r.status_code == 409
    payload = r.json()
    assert payload["code"] == "revision_conflict"
    assert payload["current_revision"] == 1

    r = client.post(f"/api/case/{patch_case}/draft",
                    json={**body, "revision": 1})
    assert r.status_code == 200
    assert r.headers["x-case-revision"] == "2"

    # 老前端不传 revision：允许写入，仍然递增。
    r = client.post(f"/api/case/{patch_case}/draft",
                    json={"frames": {"start": 0, "end": 5}})
    assert r.status_code == 200
    assert r.headers["x-case-revision"] == "3"


def test_case_lock_acquire_conflict_and_release(client, patch_case):
    client.cookies.set("vrstudio_uid", "u1")
    client.cookies.set("vrstudio_name", "alice")
    r = client.post(f"/api/case/{patch_case}/lock")
    assert r.status_code == 200 and r.json()["mine"] is True

    client.cookies.set("vrstudio_uid", "u2")
    client.cookies.set("vrstudio_name", "bob")
    r = client.post(f"/api/case/{patch_case}/lock")
    assert r.status_code == 409 and r.json()["lock"]["owner_name"] == "alice"
    r = client.delete(f"/api/case/{patch_case}/lock")
    assert r.json()["released"] is False

    client.cookies.set("vrstudio_uid", "u1")
    client.cookies.set("vrstudio_name", "alice")
    r = client.post(f"/api/case/{patch_case}/lock")
    assert r.status_code == 200
    r = client.delete(f"/api/case/{patch_case}/lock")
    assert r.json()["released"] is True
    assert client.get(f"/api/case/{patch_case}/lock").json()["lock"] is None


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
    assert body[0]["updated_at"]
    assert body[0]["outputs"] == 0
    assert body[0]["owner"] is None
    assert body[0]["task_status"] is None


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
    payload = r.json()
    assert payload["viewport"]["yaw_deg"] == 90.0
    assert "previews" in payload and "assets" not in payload


def test_inner_drag_submits_fractions(client, patch_case, tmp_path):
    r = client.post("/api/case/p1/inner",
                    json={"fx": 0.25, "fy": 0.5, "fw": 0.5, "fh": 0.25})
    assert r.status_code == 200
    inner = case_mod.load("p1", tmp_path).inner
    assert (inner["x"], inner["y"]) == (80, 90)            # 分数 × 视口像素
    assert (inner["width"], inner["height"]) == (160, 45)
    payload = r.json()
    assert payload["inner"] == inner
    assert "previews" in payload and "assets" not in payload


def test_inner_keeps_preview_store_and_url(client, patch_case, tmp_path):
    """拖 inner 只写草稿：预览图 URL 不变（不触发浏览器重载），store 不重建。"""
    before_info = client.get("/api/case/p1").json()
    before_url = before_info["previews"]["vp"]
    store = web_app.get_store("p1")

    r = client.post("/api/case/p1/inner",
                    json={"fx": 0.25, "fy": 0.5, "fw": 0.5, "fh": 0.25})
    assert r.status_code == 200

    assert web_app.get_store("p1") is store
    after_info = client.get("/api/case/p1").json()
    assert after_info["previews"]["vp"] == before_url
    inner = case_mod.load("p1", tmp_path).inner
    assert (inner["x"], inner["y"], inner["width"], inner["height"]) == (80, 90, 160, 45)


def test_nudge_wraps_yaw_and_clamps_pitch(client, patch_case, tmp_path):
    r = client.post("/api/case/p1/nudge", json={"dyaw": 180.0, "dpitch": 200.0})
    assert r.status_code == 200
    payload = r.json()
    assert payload["nudged"] == {"yaw": -170.0, "pitch": 89.0}
    assert payload["viewport"]["yaw_deg"] == -170.0
    assert "previews" in payload and "assets" not in payload
    vp = case_mod.load("p1", tmp_path).viewport
    assert vp["yaw_deg"] == -170.0 and vp["pitch_deg"] == 89.0


def test_viewport_size_scales_inner_and_switches_preview(client, patch_case, tmp_path):
    before = client.get("/api/case/p1").json()
    r = client.post("/api/case/p1/viewport-size",
                    json={"width": 1920, "height": 1080})
    assert r.status_code == 200, r.text
    data = r.json()
    assert (data["viewport"]["width"], data["viewport"]["height"]) == (1920, 1080)
    # 320×180 → 1920×1080 是 6 倍；inner 按比例缩放，取景内容不变。
    inner = data["inner"]
    assert (inner["x"], inner["y"], inner["width"], inner["height"]) == (
        480, 240, 960, 600)
    assert data["previews"]["vp"] != before["previews"]["vp"]


def test_viewport_size_rejects_unknown_option(client, patch_case):
    r = client.post("/api/case/p1/viewport-size",
                    json={"width": 640, "height": 360})
    assert r.status_code == 400
    assert "不支持的视口尺寸" in r.json()["error"]


def test_case_info_exposes_viewport_options(client, patch_case):
    info = client.get("/api/case/p1").json()
    options = info["viewport_options"]
    assert any(o["width"] == 1920 and o["height"] == 1080 for o in options)
    assert isinstance(info["viewport_footprint"], list)


def test_footprint_payload_normalizes_to_fractions():
    store = types.SimpleNamespace(
        case=types.SimpleNamespace(viewport={
            "width": 320, "height": 180, "fov_h_deg": 90.0,
            "yaw_deg": 0.0, "pitch_deg": 0.0}),
        source_size=(360, 180))
    rects = web_app._footprint_payload(store)
    assert len(rects) == 1
    rect = rects[0]
    assert 0.0 <= rect["x"] < 1.0 and 0.0 <= rect["y"] < 1.0
    assert 0.0 < rect["w"] <= 1.0 and 0.0 < rect["h"] <= 1.0
    assert rect["x"] + rect["w"] <= 1.0 and rect["y"] + rect["h"] <= 1.0


def test_reset_draft_restores_from_version(client, patch_case, tmp_path):
    _mk_extract("p1", tmp_path, "v1")
    client.post("/api/case/p1/inner",
                json={"fx": 0.1, "fy": 0.1, "fw": 0.2, "fh": 0.2})
    r = client.post("/api/case/p1/reset-draft", json={"version": "v1"})
    assert r.status_code == 200
    assert case_mod.load("p1", tmp_path).inner == INNER     # 回到版本记录的框
    payload = r.json()
    assert payload["inner"] == INNER
    assert "previews" in payload and "assets" not in payload


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
                        lambda kind, case, argv, **kwargs:
                        submitted.append((kind, case, argv)))
    r = client.post("/api/case/p1/run", json={"op": "extract"})
    assert r.status_code == 200 and r.json()["submitted"] == "extract"
    kind, case, argv = submitted[0]
    assert kind == "extract" and case == "p1"
    assert argv[1:5] == ["-m", "vrstudio", "extract", "name"]   # 走 CLI 同一路径
    assert "--case" in argv and "p1" in argv


def test_run_submit_passes_generation_params(client, patch_case, monkeypatch):
    submitted = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda kind, case, argv, **kwargs:
                        submitted.append((kind, case, argv)))
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
    # F5（2026-10-25 验收发现）：pad 有 ready 语义——源在就可以开始第一次扩充，
    # 不能让「扩充画面」按钮在新 case 上永远禁用（死锁）。
    assert gates["pad"]["ready"] is True
    assert gates["generate"]["ready"] is False


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
    prompt_calls = [c for c in calls if c[:2] == ["prompt", "save"]]
    assert prompt_calls[0][:3] == ["prompt", "save", "--job"]
    assert "--set" in prompt_calls[0] and "theme=黄昏" in prompt_calls[0]

    assert client.post("/api/case/p1/prompt",
                       json={"unset": "theme"}).status_code == 200
    prompt_calls = [c for c in calls if c[:2] == ["prompt", "save"]]
    assert "--unset" in prompt_calls[1] and "theme" in prompt_calls[1]

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


def test_keyframe_resolution_follows_first_asset_width(monkeypatch):
    monkeypatch.setattr(web_app, "_media_meta", lambda path, kind: {"width": 1024})
    assert web_app._keyframe_resolution_for(Path("a.png")) == "1K"
    monkeypatch.setattr(web_app, "_media_meta", lambda path, kind: {"width": 2048})
    assert web_app._keyframe_resolution_for(Path("a.png")) == "2K"
    monkeypatch.setattr(web_app, "_media_meta", lambda path, kind: {"width": 3840})
    assert web_app._keyframe_resolution_for(Path("a.png")) == "4K"


def test_task_view_includes_billing_attribution():
    task = tasks_mod.Task("submit", "x", ["echo"], billing={
        "project_id": 12, "episode_id": 3, "session_id": 456,
    })
    view = task.view()
    assert view["project_id"] == 12
    assert view["episode_id"] == 3
    assert view["session_id"] == 456


def test_task_log_path_uses_tasks_day_directory(tmp_path):
    path = tasks_mod._task_log_path("abc123", "extract", "p1")
    assert path.parent.name == time.strftime("%Y%m%d")
    assert path.parent.parent.name == "tasks"
    assert path.name == "abc123_extract_p1.log"


def test_cli_log_appends_to_web_task_log(tmp_path, monkeypatch):
    from vrstudio.progress import open_log_file

    target = tmp_path / "task.log"
    monkeypatch.setenv("VRSTUDIO_CLI_LOG_PATH", str(target))
    fh, path = open_log_file("extract_p1")
    fh.write("cli line\n")
    fh.close()
    assert path == target
    assert target.read_text(encoding="utf-8") == "cli line\n"


def test_task_detail_and_log_endpoints(client, tmp_path):
    task = tasks_mod.Task("submit", "p1", ["echo"])
    task.out_path = tmp_path / "task.log"
    task.out_path.write_text("line1\nline2\nline3", encoding="utf-8")
    task.state = "done"
    tasks_mod.QUEUE.history.append(task)
    try:
        r = client.get(f"/api/tasks/{task.id}")
        assert r.status_code == 200 and r.json()["kind"] == "submit"
        r = client.get(f"/api/tasks/{task.id}/log?tail=2")
        assert r.status_code == 200 and r.text == "line2\nline3"
        assert client.get("/api/tasks/nope/log").status_code == 404
    finally:
        tasks_mod.QUEUE.history.remove(task)


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
    names = [f["name"] for f in sf["extract"]["outputs"]]
    assert names[:3] == ["clip.mp4", "clip_mask.png", "clip.json"]
    assert names[3:] == ["first_frame_raw.png", "last_frame_raw.png"]
    # 还没 adopt：生成组只有输入，不猜 vrgen 轮次目录
    gen = sf["generate"]["groups"][0]
    assert [f["name"] for f in gen["inputs"]] == ["clip.mp4", "clip_mask.png", "clip.json"]
    assert gen["outputs"] == []
    assert sf["merge"]["outputs"] == []       # 还没合并：不猜产物名


def test_step_files_patch_prefers_frame_then_space(client, patch_case, tmp_path):
    _mk_extract("p1", tmp_path, "v1", with_result=True)
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    out = tmp_path / "p1" / "out"
    out.mkdir()
    (out / "v1_space.mp4").write_bytes(b"space")
    sf = client.get("/api/case/p1").json()["step_files"]
    assert [f["name"] for f in sf["merge"]["outputs"]] == ["v1_space.mp4"]
    (out / "v1_frame.mp4").write_bytes(b"frame")
    sf = client.get("/api/case/p1").json()["step_files"]
    assert [f["name"] for f in sf["merge"]["outputs"]] == ["v1_frame.mp4"]


def test_outputs_fallback_and_partial_flag(client, patch_case, tmp_path, monkeypatch):
    _mk_extract("p1", tmp_path, "v1")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    out = tmp_path / "p1" / "out"
    out.mkdir()
    (out / "v1_space.mp4").write_bytes(b"space")
    # 源片 6 帧、区间 0..5 → 整片抽取：_space 就是整片，不标“仅中间段”。
    monkeypatch.setattr(web_app, "_video_frames", lambda path: 6)
    outs = client.get("/api/case/p1").json()["outputs"]
    assert [o["file"] for o in outs] == ["v1_space.mp4"]
    assert outs[0]["partial"] is False

    # 区间 0..2 → 非整片且只有 _space：标注“仅中间段”。
    d = tmp_path / "p1" / "extracts" / "v1"
    blob = json.loads((d / "clip.json").read_text(encoding="utf-8"))
    blob["segments"][0]["frame_end"] = 2
    (d / "clip.json").write_text(json.dumps(blob), encoding="utf-8")
    outs = client.get("/api/case/p1").json()["outputs"]
    assert outs[0]["partial"] is True

    # _frame 出现后优先使用，且不再是“仅中间段”。
    (out / "v1_frame.mp4").write_bytes(b"frame")
    outs = client.get("/api/case/p1").json()["outputs"]
    assert [o["file"] for o in outs] == ["v1_frame.mp4"]
    assert outs[0]["partial"] is False


def test_old_out_names_are_not_in_the_fallback_chain(client, patch_case, tmp_path):
    _mk_extract("p1", tmp_path, "v1")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    out = tmp_path / "p1" / "out"
    out.mkdir()
    (out / "v1.mp4").write_bytes(b"old")
    assert client.get("/api/case/p1").json()["outputs"] == []


def test_merge_follow_up_skips_whole_extract(client, patch_case, tmp_path, monkeypatch):
    _mk_extract("p1", tmp_path, "v1")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    monkeypatch.setattr(web_app, "_video_frames", lambda path: 6)   # 0..5 = 整片
    assert web_app._assemble_followup("p1") == []

    d = tmp_path / "p1" / "extracts" / "v1"
    blob = json.loads((d / "clip.json").read_text(encoding="utf-8"))
    blob["segments"][0]["frame_end"] = 2
    (d / "clip.json").write_text(json.dumps(blob), encoding="utf-8")
    follow = web_app._assemble_followup("p1")
    assert [item[0] for item in follow] == ["assemble"]
    assert "assemble" in follow[0][2] and "--case" in follow[0][2]


def test_file_info_uses_repo_relative_path():
    p = config_mod.REPO_ROOT / "cases" / "_rel_test" / "x.mp4"
    info = web_app._file_info(p)
    assert info["exists"] is False
    assert info["rel"] == os.path.join("cases", "_rel_test", "x.mp4")
    assert info["path"].endswith(os.path.join("cases", "_rel_test", "x.mp4"))


# ── WP3c：素材侧边栏、派生条目、抽帧、关键帧工作台 ─────────────────────


def _add_image(case: str, tmp_path, name: str, role: str) -> Path:
    image = tmp_path / f"{name}.png"
    image.write_bytes(name.encode())
    case_mod.add_assets(case, [(image, role)], root=tmp_path)
    return image


def test_asset_api_allows_patch(client, patch_case, tmp_path):
    _add_image("p1", tmp_path, "ref", "subject_ref")
    info = client.get("/api/case/p1").json()
    assert [a["role"] for a in info["assets"]] == ["subject_ref"]
    assert info["derived"][0]["label"] == "源视频"


def test_derived_endpoint_lists_source_and_geometry(client, patch_case, tmp_path):
    d = _mk_extract("p1", tmp_path, "v1")
    (d / "clip.mp4").write_bytes(b"c")
    (d / "clip_mask.png").write_bytes(b"m")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    rows = client.get("/api/case/p1/derived").json()["derived"]
    labels = {row["label"] for row in rows}
    assert {"源视频", "几何契约"} <= labels
    assert "当前 mask" not in labels and "当前 clip" not in labels
    assert all(row["editable"] is False for row in rows)
    # clip 是可勾选候选，mask 不出现在面板里。
    info = client.get("/api/case/p1").json()
    assert [a["id"] for a in info["assets"]] == ["clip"]
    assert info["assets"][0]["source"] == "flow"


def test_submit_set_roundtrip(client, gen_case, tmp_path):
    _add_image("g1", tmp_path, "first", "first_frame")
    _add_image("g1", tmp_path, "last", "last_frame")
    r = client.put("/api/case/g1/submit", json={"selected": ["i1", "i2"]})
    assert r.status_code == 200, r.text
    assert r.json()["submit_selected"] == ["i1", "i2"]
    assert client.get("/api/case/g1/submit").json()["selected"] == ["i1", "i2"]


def test_generation_params_image_case_omits_input(client, tmp_path, monkeypatch):
    case_mod.create("g2", "gen", root=tmp_path)
    _add_image("g2", tmp_path, "first", "first_frame")
    _add_image("g2", tmp_path, "last", "last_frame")
    monkeypatch.setattr(gen_mod, "_image_size", lambda path: (1920, 1080))
    calls = []

    def fake(argv):
        calls.append(argv)
        return {"modes": [], "current": {"mode": "firstLastFrame"}}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    payload = web_app._generation_params("g2")
    assert payload["current"]["mode"] == "firstLastFrame"
    assert len(calls) == 1 and "--input" not in calls[0]


def test_prompt_preview_image_case_no_crash(client, tmp_path, monkeypatch):
    case_mod.create("g2", "gen", root=tmp_path)
    _add_image("g2", tmp_path, "first", "first_frame")
    _add_image("g2", tmp_path, "last", "last_frame")
    monkeypatch.setattr(gen_mod, "_image_size", lambda path: (1920, 1080))
    monkeypatch.setattr(web_app, "_vrgen_json", lambda argv: {"error": "vrgen down"})
    r = client.get("/api/case/g2")
    assert r.status_code == 200
    assert "error" in r.json()["prompt"]


def test_generation_params_exposes_clip_auto_duration(client, patch_case, tmp_path, monkeypatch):
    _mk_extract("p1", tmp_path, "v1")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    _add_image("p1", tmp_path, "first", "first_frame")
    _add_image("p1", tmp_path, "last", "last_frame")
    case_mod.set_submit_selected("p1", ["i1", "i2"], root=tmp_path)
    monkeypatch.setattr(web_app, "clip_duration_s", lambda case, root: 10)
    payload = {"current": {"mode": "firstLastFrame"},
               "output": [], "input": [],
               "fixed": {"duration_s": 4, "model_id": 37, "num": 1}}
    monkeypatch.setattr(web_app, "_vrgen_json", lambda argv: payload)
    data = web_app._generation_params("p1")
    assert data["auto_duration_s"] == 10
    assert data["fixed"]["duration_s"] == 10


def test_generate_gate_mode_aware(client, tmp_path, monkeypatch):
    case_mod.create("g5", "gen", root=tmp_path)
    _add_image("g5", tmp_path, "first", "first_frame")
    monkeypatch.setattr(web_app, "_generation_params",
                        lambda name: {"current": {"mode": "firstLastFrame"}})
    monkeypatch.setattr(gen_mod, "_image_size", lambda path: (1920, 1080))
    monkeypatch.setattr(web_app, "_vrgen_json", lambda argv: {"error": "vrgen down"})
    info = client.get("/api/case/g5").json()
    assert info["gates"]["generate"]["ready"] is False
    assert "勾选" in info["gates"]["generate"]["reason"]

    # 勾了首帧还缺尾帧：门禁给出具体缺什么。
    case_mod.set_submit_selected("g5", ["i1"], root=tmp_path)
    info = client.get("/api/case/g5").json()
    assert "尾帧" in info["gates"]["generate"]["reason"]


def test_raw_frames_come_from_extract_and_the_endpoint_is_retired(client, patch_case, tmp_path):
    d = _mk_extract("p1", tmp_path, "v1")
    (d / "clip.mp4").write_bytes(b"clip")
    (d / "first_frame_raw.png").write_bytes(b"first")
    (d / "last_frame_raw.png").write_bytes(b"last")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    info = client.get("/api/case/p1").json()
    assert [row["id"] for row in info["raw_frames"]] == ["first_raw", "last_raw"]
    assert all(row["exists"] for row in info["raw_frames"])
    # 原始帧只进工作台，不是 ``[[assets]]`` 候选。
    assert info["assets"] == [] or all(a["id"] != "first_raw" for a in info["assets"])
    r = client.post("/api/case/p1/extract-frames", json={})
    assert r.status_code == 410


def test_patch_extract_files_list_raw_frames(client, patch_case, tmp_path):
    d = _mk_extract("p1", tmp_path, "v1")
    (d / "first_frame_raw.png").write_bytes(b"first")
    (d / "last_frame_raw.png").write_bytes(b"last")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    info = client.get("/api/case/p1").json()
    extract = info["step_files"]["extract"]
    names = [row["name"] for row in extract["outputs"]]
    assert names == ["clip.mp4", "clip_mask.png", "clip.json",
                     "first_frame_raw.png", "last_frame_raw.png"]
    assert "groups" not in extract          # 一次抽取的产物，不额外分组


def test_keyframe_run_queues_image_argv(client, patch_case, tmp_path, monkeypatch):
    d = _mk_extract("p1", tmp_path, "v1")
    (d / "first_frame_raw.png").write_bytes(b"first")
    (d / "last_frame_raw.png").write_bytes(b"last")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    _add_image("p1", tmp_path, "sheet", "subject_ref")
    captured = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda op, name, argv, **kwargs:
                        captured.append((op, name, argv)))
    monkeypatch.setattr(web_app, "_current_session", lambda name: 123)
    r = client.post("/api/case/p1/keyframe/run", json={
        "step": "first", "model_id": 44, "resolution": "2K"})
    assert r.status_code == 200, r.text
    op, name, argv = captured[0]
    assert op == "keyframe" and name == "p1"
    assert "image" in argv and "submit" in argv
    assert "--ratio" in argv and "16:9" in argv
    assert argv[argv.index("--template") + 1] == "keyframe-first"
    assert argv[argv.index("--vars-file") + 1] == "keyframe-vars.toml"
    assert argv[argv.index("--step") + 1] == "first"
    assert argv[argv.index("--session-id") + 1] == "123"
    assert argv.count("--image") == 2
    assert argv.count("--image-role") == 2
    # 图片步只认原始帧 + 绑定的三视图，不读 [submit] selected。
    case_mod.set_submit_selected("p1", [], root=tmp_path)
    r = client.post("/api/case/p1/keyframe/run", json={"step": "first"})
    assert r.status_code == 200, r.text


def test_keyframe_run_requires_project_session(client, patch_case, tmp_path, monkeypatch):
    d = _mk_extract("p1", tmp_path, "v1")
    (d / "first_frame_raw.png").write_bytes(b"first")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    _add_image("p1", tmp_path, "sheet", "subject_ref")
    monkeypatch.setattr(web_app, "_current_session", lambda name: 0)
    r = client.post("/api/case/p1/keyframe/run", json={"step": "first"})
    assert r.status_code == 400
    assert "项目生成会话" in r.json()["error"]


def test_keyframe_rounds_lists_candidates(client, patch_case, tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "runs")
    run_dir = tmp_path / "runs" / "p1" / "img-abc12345"
    run_dir.mkdir(parents=True)
    a = run_dir / "result_1.png"
    b = run_dir / "result_2.png"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    (run_dir / "run.json").write_text(json.dumps({
        "key": "abcdef123456", "stage": "checked",
        "params": {"kind": "image", "step": "first", "num": 4},
        "results": [str(a), str(b)], "gate": {"verdict": "ok"}}), encoding="utf-8")
    data = client.get("/api/case/p1/keyframe/rounds").json()
    assert data["rounds"][0]["round"] == "img-abc12345"
    assert data["rounds"][0]["step"] == "first"
    assert data["rounds"][0]["num"] == 4
    assert [row["index"] for row in data["rounds"][0]["results"]] == [1, 2]
    first = data["rounds"][0]["results"][0]
    assert first["name"] == "result_1.png"
    assert first["open_url"].startswith("/file?root=workdir&p=")


def test_keyframe_pick_rejects_mismatched_pair(client, tmp_path, monkeypatch):
    case_mod.create("g6", "gen", root=tmp_path)
    _add_image("g6", tmp_path, "first", "first_frame")
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "runs")
    run_dir = tmp_path / "runs" / "g6" / "img-abc12345"
    run_dir.mkdir(parents=True)
    candidate = run_dir / "result_1.png"
    candidate.write_bytes(b"c")
    (run_dir / "run.json").write_text(json.dumps({
        "key": "abcdef123456", "results": [str(candidate)]}),
        encoding="utf-8")

    def fake_capture(argv):
        Path(argv[argv.index("--out") + 1]).write_bytes(b"picked")
        return 0, "ok"

    monkeypatch.setattr(web_app, "_capture", fake_capture)

    def fake_meta(path, kind):
        width, height = ((1280, 720) if Path(path).name.startswith("keyframe-")
                         else (1920, 1080))
        return {"width": width, "height": height, "duration": None, "fps": None}

    monkeypatch.setattr(web_app, "_media_meta", fake_meta)
    r = client.post("/api/case/g6/keyframe/pick",
                    json={"round": "img-abc12345", "index": 1, "role": "last_frame"})
    assert r.status_code == 400
    assert "宽高不一致" in r.json()["error"]


def _keyframe_ready(tmp_path, monkeypatch) -> str:
    """patch case + 抽取 + 两张原始帧 + 一张三视图；返回 case 名。"""
    d = _mk_extract("p1", tmp_path, "v1")
    (d / "first_frame_raw.png").write_bytes(b"first")
    (d / "last_frame_raw.png").write_bytes(b"last")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    _add_image("p1", tmp_path, "sheet", "subject_ref")
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "runs")
    return "p1"


def test_keyframe_sheet_switch_does_not_touch_submit(client, patch_case, tmp_path):
    _add_image("p1", tmp_path, "a", "subject_ref")
    _add_image("p1", tmp_path, "b", "subject_ref")
    case_mod.set_submit_selected("p1", ["i2"], root=tmp_path)
    r = client.put("/api/case/p1/keyframe/sheet", json={"asset": "i1"})
    assert r.status_code == 200, r.text
    assert r.json()["keyframe"]["sheet"] == "i1"
    assert r.json()["submit_selected"] == ["i2"]


def test_keyframe_prompt_uses_vars_file(client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    vars_path = tmp_path / "runs" / "p1" / "keyframe-vars.toml"
    vars_path.parent.mkdir(parents=True)
    vars_path.write_text("template = 'keyframe-first'\n[vars]\n", encoding="utf-8")
    calls = []

    def fake(argv):
        calls.append(argv)
        return {"template": "keyframe-first",
                "vars": {"figure": "x", "pose_first": "y"},
                "rendered": "图1 = 首帧原始图。",
                "placeholders": ["image_roles", "pose_first", "figure", "preserve"],
                "computed_keys": ["image_roles"], "labels": {}, "help": {}}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    r = client.get("/api/case/p1/keyframe/prompt?step=first")
    assert r.status_code == 200, r.text
    argv = calls[0]
    assert argv[0] == "prompt"          # _vrgen_json 自己补可执行文件，不能再传一遍
    assert argv[argv.index("--vars-file") + 1] == "keyframe-vars.toml"
    assert argv[argv.index("--template") + 1] == "keyframe-first"
    assert argv.count("--image-role") == 2

    r = client.put("/api/case/p1/keyframe/prompt",
                   json={"step": "first", "vars": {"figure": "新外观"}})
    assert r.status_code == 200, r.text
    assert "--set" in calls[-1]
    assert "figure=新外观" in calls[-1]


def test_keyframe_pick_first_queues_last(client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    run_dir = tmp_path / "runs" / "p1" / "img-abc12345"
    run_dir.mkdir(parents=True)
    candidate = run_dir / "result_1.png"
    candidate.write_bytes(b"c")
    (run_dir / "run.json").write_text(json.dumps({
        "key": "abcdef123456", "results": [str(candidate)]}), encoding="utf-8")

    def fake_capture(argv):
        Path(argv[argv.index("--out") + 1]).write_bytes(b"picked")
        return 0, "ok"

    monkeypatch.setattr(web_app, "_capture", fake_capture)
    monkeypatch.setattr(web_app, "_media_meta",
                        lambda path, kind: {"width": 1920, "height": 1080})
    monkeypatch.setattr(web_app, "_current_session", lambda name: 123)
    monkeypatch.setattr(web_app, "_keyframe_prompt",
                        lambda name, step: {"vars": {"figure": "x", "pose_last": "y"}})
    captured = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda op, name, argv, **kwargs:
                        captured.append((op, name, argv)))

    r = client.post("/api/case/p1/keyframe/pick",
                    json={"round": "img-abc12345", "index": 1, "role": "first_frame"})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["keyframe_queued"] is True
    state = keyframe.load(case_mod.case_dir("p1", tmp_path))
    assert state.first is not None and state.first.round == "img-abc12345"
    assert state.first.asset == info["assets"][-1]["id"]
    op, name, argv = captured[0]
    assert op == "keyframe" and name == "p1"
    assert argv[argv.index("--step") + 1] == "last"
    images = [argv[i + 1] for i, item in enumerate(argv) if item == "--image"]
    assert len(images) == 3 and Path(images[2]).name.startswith("i")


def test_keyframe_handoff_sets_mode_and_selection(client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    _add_image("p1", tmp_path, "first", "first_frame")
    _add_image("p1", tmp_path, "last", "last_frame")
    keyframe.save(case_mod.case_dir("p1", tmp_path), keyframe.State(
        extract="v1", sheet="i1",
        first=keyframe.Pick("img-a", 1, "i2"),
        last=keyframe.Pick("img-b", 1, "i3")))
    modes = []
    monkeypatch.setattr(web_app, "_save_gen_mode",
                        lambda name, mode: modes.append(mode) or None)
    r = client.post("/api/case/p1/keyframe/handoff")
    assert r.status_code == 200, r.text
    assert modes == ["firstLastFrame"]
    assert r.json()["submit_selected"] == ["i2", "i3"]


def test_keyframe_pointer_must_match_a_live_role(client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    _add_image("p1", tmp_path, "second_sheet", "subject_ref")
    cdir = case_mod.case_dir("p1", tmp_path)
    # 指针指向不存在的素材：不自动改绑另一张三视图。
    keyframe.save(cdir, keyframe.State(sheet="i9"))
    info = client.get("/api/case/p1").json()
    assert info["keyframe"]["sheet"] is None
    assert len(info["keyframe"]["sheet_candidates"]) == 2
    # 指针指向的素材角色已经变了：按未定稿处理。
    keyframe.save(cdir, keyframe.State(
        sheet="i1", first=keyframe.Pick("img-a", 1, "i1")))
    info = client.get("/api/case/p1").json()
    assert info["keyframe"]["first"] is None


def test_keyframe_gate_states(client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    info = client.get("/api/case/p1").json()
    gate = info["gates"]["keyframe"]
    assert gate["ok"] is False and "首帧" in gate["reason"]

    _add_image("p1", tmp_path, "first", "first_frame")
    _add_image("p1", tmp_path, "last", "last_frame")
    cdir = case_mod.case_dir("p1", tmp_path)
    keyframe.save(cdir, keyframe.State(
        extract="v1", sheet="i1",
        first=keyframe.Pick("img-a", 1, "i2"),
        last=keyframe.Pick("img-b", 1, "i3")))
    info = client.get("/api/case/p1").json()
    assert info["gates"]["keyframe"]["ok"] is True

    keyframe.save(cdir, keyframe.State(
        extract="v0", sheet="i1",
        first=keyframe.Pick("img-a", 1, "i2"),
        last=keyframe.Pick("img-b", 1, "i3")))
    info = client.get("/api/case/p1").json()
    gate = info["gates"]["keyframe"]
    assert gate["ok"] is False and "抽取版本" in gate["reason"]


def test_gen_page_has_sidebar_and_no_keyframe_step(client, gen_case):
    text = client.get("/case/g1").text
    assert "素材库与本次视频提交" in text
    assert "时长" in text and "genDuration" in text
    assert "最近成片" in text and "扣费额度" in text
    assert text.count('id="genMode"') == 1        # 控件只在底栏出现
    assert text.count('id="sessProject"') == 1
    assert "pnl-assets" not in text       # 素材步骤已并入生成页侧边栏
    # gen 的首尾帧入口待“上传两张原始帧”的入口，本轮只接 patch。
    assert 'id="pnl-keyframe"' not in text


def test_fullredraw_page_has_no_keyframe_step(client, fullredraw_case):
    text = client.get("/case/f1").text
    assert "素材库与本次视频提交" in text
    assert 'id="pnl-keyframe"' not in text


def test_patch_page_has_keyframe_step(client, patch_case):
    text = client.get("/case/p1").text
    assert 'id="pnl-keyframe"' in text
    assert "首尾帧重绘" in text
    assert "从当前 clip 抽首尾原始帧" not in text
    assert "两帧底图" in text


def test_patch_page_has_footprint_and_size_control(client, patch_case):
    text = client.get("/case/p1").text
    assert 'id="vpFoots"' in text and 'class="vpFoot"' in text
    assert 'id="vpSize"' in text


def test_next_frontend_reports_missing_build(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "_NEXT_DIR", tmp_path / "missing-next")
    client = TestClient(web_app.build_app(cases_root=tmp_path))
    response = client.get("/next/")
    assert response.status_code == 503
    assert "尚未构建" in response.text


def test_next_frontend_serves_spa_and_assets(tmp_path, monkeypatch):
    next_dir = tmp_path / "next"
    (next_dir / "assets").mkdir(parents=True)
    (next_dir / "index.html").write_text("<html>next</html>", encoding="utf-8")
    (next_dir / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(web_app, "_NEXT_DIR", next_dir)
    client = TestClient(web_app.build_app(cases_root=tmp_path))
    assert client.get("/next/").text == "<html>next</html>"
    assert client.get("/next/case/dacne/generate").text == "<html>next</html>"
    assert client.get("/next/assets/app.js").text == "console.log(1)"


def test_identity_roundtrip(client):
    first = client.get("/api/me")
    assert first.status_code == 200
    body = first.json()
    assert body["display_name"] == "访客"
    assert body["authenticated"] is False
    uid = client.cookies.get("vrstudio_uid")
    assert uid

    saved = client.post("/api/me", json={"display_name": "MDX"})
    assert saved.status_code == 200
    assert saved.json()["display_name"] == "MDX"
    assert saved.json()["id"] == uid

    again = client.get("/api/me")
    assert again.json()["display_name"] == "MDX"


def test_identity_rejects_empty_and_long_names(client):
    assert client.post("/api/me", json={"display_name": "  "}).status_code == 400
    assert client.post("/api/me", json={"display_name": "x" * 41}).status_code == 400


def test_task_view_and_queue_filter():
    from vrstudio.web.identity import User

    user = User("u1", "MDX")
    other = User("u2", "LQ")
    current = tasks_mod.Task("extract", "p1", ["echo"], owner=user)
    current.state = "running"
    pending = tasks_mod.Task("merge", "p2", ["echo"], owner=other)
    done = tasks_mod.Task("adopt", "p3", ["echo"], owner=user)
    done.state = "done"

    queue = tasks_mod.TaskQueue()
    queue.current = current
    queue.pending = [pending]
    queue.history = [done]

    mine = queue.list(user, "mine")
    assert [row["id"] for row in mine] == [current.id, done.id]
    assert mine[0]["queue_position"] == 1
    assert mine[1]["queue_position"] is None
    assert mine[0]["owner_name"] == "MDX"

    all_rows = queue.list(user, "all")
    assert [row["kind"] for row in all_rows] == ["extract", "merge", "adopt"]
    assert all_rows[1]["queue_position"] == 2


def test_tasks_api_returns_owner_and_positions(client, monkeypatch):
    from vrstudio.web.identity import User

    user = User("u1", "MDX")
    queue = tasks_mod.TaskQueue()
    queue.current = tasks_mod.Task("extract", "p1", ["echo"], owner=user)
    queue.current.state = "running"
    queue.pending = [tasks_mod.Task("merge", "p2", ["echo"], owner=user)]
    queue.history = [tasks_mod.Task("adopt", "p3", ["echo"], owner=user)]
    queue.history[0].state = "done"
    monkeypatch.setattr(web_app, "QUEUE", queue)

    response = client.get("/api/tasks?scope=all")
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert [task["kind"] for task in tasks] == ["extract", "merge", "adopt"]
    assert tasks[0]["queue_position"] == 1
    assert tasks[1]["queue_position"] == 2
    assert tasks[2]["queue_position"] is None
    assert tasks[0]["owner_name"] == "MDX"
    assert client.get("/api/tasks?scope=bad").status_code == 400

    filtered = client.get("/api/tasks?scope=all&case=p2")
    assert filtered.status_code == 200
    assert [task["kind"] for task in filtered.json()["tasks"]] == ["merge"]
