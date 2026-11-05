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


def test_index_redirects_to_next(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/next/"


def test_legacy_case_url_redirects_to_next(client):
    r = client.get("/case/p1", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/next/case/p1"


def test_gen_case_info_has_assets_and_generate(client, gen_case):
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
                    json={"op": "role", "id": "i1", "role": "lighting_ref"})
    assert [a["role"] for a in r.json()["assets"]] == ["edit_master", "lighting_ref"]

    # 光照参考只对图片成立：视频走同一个接口也要按种类拦下。
    r = client.post("/api/case/g1/assets",
                    json={"op": "role", "id": "v1", "role": "lighting_ref"})
    assert r.status_code == 400 and "lighting_ref" in r.json()["error"]

    # 下拉词表由服务端下发：open / kinds / modes 一并给前端。
    roles = {item["name"]: item for item in client.get("/api/case/g1").json()["roles"]}
    assert roles["lighting_ref"]["kinds"] == ["image"]
    assert roles["lighting_ref"]["open"] is True
    assert "videoEdit" in roles["lighting_ref"]["modes"]
    assert roles["mask"]["reserved"] is True

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
    r = client.post("/api/uploads",
                    json={"name": "ref.png", "size": web_app.UPLOAD_MAX_BYTES + 1})
    assert r.status_code == 413 and "超过上限" in r.json()["error"]
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


def test_video_out_route_is_retired(client, patch_case):
    assert client.get(f"/video/out/{patch_case}/x.mp4").status_code == 404


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

    download = client.get("/file", params={"root": "cases", "p": "c1/clip_mask.png",
                                           "download": "1"})
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment")
    assert 'filename="clip_mask.png"' in download.headers["content-disposition"]


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


def test_keyframe_pick_and_refs_carry_open_url(
        client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    _add_image("p1", tmp_path, "first", "first_frame")
    cdir = case_mod.case_dir("p1", tmp_path)
    keyframe.save(cdir, keyframe.State(
        extract="v1", refs=[keyframe.Ref("i1", "subject_ref")],
        first=keyframe.Pick("img-a", 1, "i2")))
    info = client.get("/api/case/p1").json()
    first = info["keyframe"]["first"]
    assert first["asset"] == "i2"
    assert first["file"]["exists"] is True
    assert first["file"]["name"].endswith(".png")
    assert first["file"]["open_url"].startswith("/file?root=cases&p=")
    ref = info["keyframe"]["refs"][0]
    assert ref["file"]["open_url"].startswith("/file?root=cases&p=")
    assert info["keyframe"]["role_help"]["detail_ref"].startswith("细节参考")


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


def test_cases_list_does_not_decode_or_hash_assets(client, patch_case, tmp_path,
                                                  monkeypatch):
    """列表页只回答「下一步」，不能为了 26 个 case 解码几十张 4K 图、算全部哈希。

    首尾帧工作台的素材明细（宽高、存在性、sha256）属于 case 详情页；这里把它们
    钉成一旦调用就失败，锁住轻量路径。
    """
    _add_image(patch_case, tmp_path, "ref", "subject_ref")

    def boom(*args, **kwargs):
        raise AssertionError("case 列表不应解码素材或哈希文件")

    monkeypatch.setattr(web_app, "_media_meta", boom)
    monkeypatch.setattr(case_mod, "check_assets", boom)
    r = client.get("/api/cases")
    assert r.status_code == 200
    assert r.json()[0]["name"] == patch_case


def test_cases_list_sorts_by_last_activity_desc(client, patch_case, tmp_path,
                                                monkeypatch):
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "vrgen_runs")
    src = tmp_path / "src2.mp4"
    src.write_bytes(b"fake erp bytes")
    case_mod.create("p2", "patch", src, root=tmp_path)
    out = case_mod.case_dir("p2", tmp_path) / "out"
    out.mkdir()
    (out / "v1_frame.mp4").write_bytes(b"m")
    old = time.time() - 3600
    os.utime(case_mod.case_toml("p1", tmp_path), (old, old))
    os.utime(case_mod.case_toml("p2", tmp_path), (old, old))

    body = client.get("/api/cases").json()
    assert [item["name"] for item in body] == ["p2", "p1"]
    assert body[0]["updated_ts"] > body[1]["updated_ts"]
    # 活动口径取的是成片 mtime，不是 case.toml 的旧时间。
    assert body[0]["updated_ts"] > old + 1


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
    assert any(o["width"] == 3840 and o["height"] == 2160 for o in options)
    assert isinstance(info["viewport_footprint"], list)
    assert info["fov_range"] == [20.0, 120.0]
    assert "coverage" in info


def test_viewport_size_accepts_4k(client, patch_case, tmp_path):
    r = client.post("/api/case/p1/viewport-size",
                    json={"width": 3840, "height": 2160})
    assert r.status_code == 200, r.text
    data = r.json()
    assert (data["viewport"]["width"], data["viewport"]["height"]) == (3840, 2160)
    # 320×180 → 3840×2160 是 12 倍；inner 按比例缩放。
    inner = data["inner"]
    assert (inner["x"], inner["y"], inner["width"], inner["height"]) == (
        960, 480, 1920, 1200)


def test_fov_changes_angle_without_rescaling_inner(client, patch_case, tmp_path):
    before = client.get("/api/case/p1").json()
    r = client.post("/api/case/p1/fov", json={"value": 110})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["viewport"]["fov_h_deg"] == 110.0
    assert data["inner"] == INNER                     # fov 不缩放 inner
    assert data["previews"]["vp"] != before["previews"]["vp"]
    assert data["fov_range"] == [20.0, 120.0]
    assert case_mod.load("p1", tmp_path).viewport["fov_h_deg"] == 110.0


def test_fov_rejects_out_of_range_and_bad_values(client, patch_case):
    r = client.post("/api/case/p1/fov", json={"value": 150})
    assert r.status_code == 400
    assert "20" in r.json()["error"] and "120" in r.json()["error"]

    r = client.post("/api/case/p1/fov", json={"value": "wide"})
    assert r.status_code == 400
    assert "必须是数字" in r.json()["error"]

    r = client.post("/api/case/p1/fov", json={})
    assert r.status_code == 400
    assert "缺 value" in r.json()["error"]


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


def test_coverage_payload_reports_footprint_and_density():
    store = types.SimpleNamespace(
        case=types.SimpleNamespace(
            viewport={"width": 1920, "height": 1080, "fov_h_deg": 59.0,
                      "yaw_deg": 0.0, "pitch_deg": 0.0},
            inner={"x": 0, "y": 0, "width": 1728, "height": 972}),
        source_size=(8192, 4096))
    cov = web_app._coverage_payload(store)
    assert cov["fov_deg"] == 59.0
    assert 1340 <= cov["footprint"][0] <= 1346
    assert cov["density"]["1080p"] == 1.43
    assert cov["density"]["4K"] == 2.86
    assert cov["inner_deg"] == 53.1


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
                          "resolution": "720p", "scale": "auto",
                          "audio": "none"})
    assert r.status_code == 200 and r.json()["submitted"] == "submit"
    argv = submitted[0][2]
    assert "--resolution" in argv and "720p" in argv
    assert "--scale" in argv and "auto" in argv
    assert "--mode" in argv and "allMediaRef" in argv
    assert "--aspect-ratio" in argv and "16:9" in argv
    assert "--audio" in argv and "none" in argv


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


def test_params_save_passes_model_ids(client, fullredraw_case, monkeypatch):
    calls = []

    def fake(argv):
        calls.append(argv)
        return {"video_model_id": 22, "image_model_id": 25,
                "mode": "firstLastFrame", "resolution": "4K"}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    r = client.post("/api/case/f1/params",
                    json={"video_model_id": 22, "image_model_id": 25,
                          "mode": "firstLastFrame",
                          "resolution": "4K", "aspect_ratio": "16:9"})
    assert r.status_code == 200, r.text
    argv = calls[0]
    assert "--video-model-id" in argv and "22" in argv
    assert "--image-model-id" in argv and "25" in argv
    assert "--resolution" in argv and "4K" in argv
    assert "--mode" in argv and "firstLastFrame" in argv


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


def test_prompt_save_returns_expanded_overview(client, patch_case, monkeypatch):
    """save 只回草稿；再读一次 show，页面才能拿到 rendered 与 render_error。"""

    def fake_vrgen_json(argv):
        if argv[:2] == ["prompt", "show"]:
            return {"rendered": "展开后的正文", "render_error": None,
                    "vars": {"theme": "黄昏"}}
        return {"template": "character-repaint", "vars": {"theme": "黄昏"}}

    monkeypatch.setattr(web_app, "_vrgen_json", fake_vrgen_json)
    r = client.post("/api/case/p1/prompt", json={"set": "theme", "value": "黄昏"})
    assert r.status_code == 200
    assert r.json()["prompt"]["rendered"] == "展开后的正文"


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


def test_step_files_fullredraw_shows_the_follow_input_output(
        client, fullredraw_case, tmp_path):
    """跟随输入写出的 ``<轮次>_<W>x<H>.mp4`` 要出现在步骤输出里，
    而不是显示一个从未写过的案例分辨率路径。"""
    out = tmp_path / "f1" / "out"
    (out / "round-1_1260x630.mp4").write_bytes(b"small")
    (out / "round-1_1260x630_recompose.json").write_text("{}", encoding="utf-8")
    (out / "round-1.mp4").unlink()
    (out / "round-1_recompose.json").unlink()

    files = client.get("/api/case/f1").json()["step_files"]["recompose"]["outputs"]
    assert [f["name"] for f in files] == [
        "round-1_1260x630.mp4", "round-1_1260x630_recompose.json"]
    assert all(f["exists"] for f in files)
    assert files[0]["open_url"]


def test_step_files_fullredraw_ignores_other_rounds_and_stray_mp4(
        client, fullredraw_case, tmp_path):
    """共享前缀的其它轮次与没有报告的散件都不算这一轮的产物。"""
    out = tmp_path / "f1" / "out"
    (out / "round-10.mp4").write_bytes(b"other round")
    (out / "round-10_recompose.json").write_text("{}", encoding="utf-8")
    (out / "round-1_extra.mp4").write_bytes(b"stray")

    files = client.get("/api/case/f1").json()["step_files"]["recompose"]["outputs"]
    assert [f["name"] for f in files] == ["round-1.mp4", "round-1_recompose.json"]


def test_step_files_mtime_changes_when_the_step_reruns(client, fullredraw_case, tmp_path):
    cdir = case_mod.case_dir("f1", tmp_path)
    padded = cdir / "seedance_input.mp4"
    os.utime(padded, (1_000, 1_000))
    first = client.get("/api/case/f1").json()["step_files"]["pad"]["outputs"][0]["mtime"]
    os.utime(padded, (2_000, 2_000))
    second = client.get("/api/case/f1").json()["step_files"]["pad"]["outputs"][0]["mtime"]
    assert first == 1_000 and second == 2_000


def _write_pad_upload(tmp_path, size: str, *, mtime: float | None = None,
                      sidecar: bool = True) -> Path:
    d = tmp_path / "f1" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    video = d / f"upload_pad_{size}.mp4"
    video.write_bytes(b"upload")
    (d / f"upload_pad_mask_{size}.png").write_bytes(b"upload-mask")
    if sidecar:
        width, height = (int(part) for part in size.split("x"))
        (d / f"upload_pad_{size}.json").write_text(json.dumps({
            "canvas": [4480, 1920], "source": [3840, 1920],
            "upload": {"path": str(video), "width": width, "height": height,
                       "pixels": width * height},
            "mask": {"path": str(d / f"upload_pad_mask_{size}.png"),
                     "width": width, "height": height},
            "scaled": True, "scale": "auto", "scale_percent": 98.2,
            "limit": {"max_pixel": 8_295_044, "max_bytes": 209_715_200},
            "video_model_id": 37, "mode": "videoEdit",
            "created_utc": "2026-11-05T06:00:00Z",
        }, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        for p in d.glob(f"upload_pad*{size}*"):
            os.utime(p, (mtime, mtime))
    return video


def test_pad_upload_is_listed_as_a_second_file_group(client, fullredraw_case, tmp_path):
    _write_pad_upload(tmp_path, "4398x1884")
    info = client.get("/api/case/f1").json()
    uploads = info["step_files"]["pad"]["uploads"]
    assert [f["name"] for f in uploads] == [
        "upload_pad_4398x1884.mp4",
        "upload_pad_mask_4398x1884.png",
        "upload_pad_4398x1884.json"]
    assert all(f["exists"] and f["open_url"] for f in uploads)
    summary = info["pad_upload"]
    assert summary["upload"]["width"] == 4398
    assert summary["upload"]["pixels"] == 4398 * 1884
    assert summary["limit"]["max_pixel"] == 8_295_044
    assert summary["video_model_id"] == 37
    assert summary["mode"] == "videoEdit"
    assert summary["scale"] == "auto"


def test_pad_upload_shows_only_the_newest_set(client, fullredraw_case, tmp_path):
    _write_pad_upload(tmp_path, "4398x1884", mtime=1_000)
    _write_pad_upload(tmp_path, "4400x1884", mtime=2_000)
    uploads = client.get("/api/case/f1").json()["step_files"]["pad"]["uploads"]
    assert [f["name"] for f in uploads] == [
        "upload_pad_4400x1884.mp4",
        "upload_pad_mask_4400x1884.png",
        "upload_pad_4400x1884.json"]


def test_pad_upload_without_sidecar_still_lists_the_files(client, fullredraw_case, tmp_path):
    _write_pad_upload(tmp_path, "4398x1884", sidecar=False)
    info = client.get("/api/case/f1").json()
    assert info["pad_upload"] is None
    names = [f["name"] for f in info["step_files"]["pad"]["uploads"]]
    assert names == ["upload_pad_4398x1884.mp4",
                     "upload_pad_mask_4398x1884.png",
                     "upload_pad_4398x1884.json"]
    assert info["step_files"]["pad"]["uploads"][2]["exists"] is False


def test_pad_op_queues_the_upload_export(client, fullredraw_case, monkeypatch):
    submitted = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda kind, case, argv, **kwargs:
                        submitted.append((kind, case, argv, kwargs.get("follow_up"))))
    r = client.post("/api/case/f1/run", json={"op": "pad"})
    assert r.status_code == 200
    kind, case, _argv, follow = submitted[0]
    assert kind == "pad" and case == "f1"
    assert follow and follow[0][0] == "export_upload"
    assert follow[0][1] == "f1"
    assert "export-upload" in follow[0][2]


def test_pad_op_has_no_upload_follow_up_for_patch(client, patch_case, monkeypatch):
    submitted = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda kind, case, argv, **kwargs:
                        submitted.append((kind, case, argv, kwargs.get("follow_up"))))
    r = client.post("/api/case/p1/run", json={"op": "pad"})
    assert r.status_code == 200
    assert submitted[0][3] is None


def test_run_export_upload_queues_the_case_cli(client, fullredraw_case, monkeypatch):
    submitted = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda kind, case, argv, **kwargs:
                        submitted.append((kind, case, argv)))
    r = client.post("/api/case/f1/run", json={"op": "export_upload"})
    assert r.status_code == 200 and r.json()["submitted"] == "export_upload"
    kind, case, argv = submitted[0]
    assert kind == "export_upload" and case == "f1"
    assert "export-upload" in argv and "--case" in argv and "f1" in argv


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


def test_outputs_fullredraw_requires_the_recompose_report(client, fullredraw_case, tmp_path):
    """out/ 里的散件不是成片：只有 recompose 写下报告的 mp4 才进列表。"""
    out = tmp_path / "f1" / "out"
    (out / "stray.mp4").write_bytes(b"stray")
    (out / "older.mp4").write_bytes(b"older")
    (out / "older_recompose.json").write_text("{}", encoding="utf-8")
    os.utime(out / "round-1.mp4", (2_000, 2_000))
    os.utime(out / "older.mp4", (1_000, 1_000))
    os.utime(out / "stray.mp4", (3_000, 3_000))

    outs = client.get("/api/case/f1").json()["outputs"]
    assert [o["file"] for o in outs] == ["older.mp4", "round-1.mp4"]


def test_outputs_are_sorted_by_mtime_not_filename(client, patch_case, tmp_path):
    """版本名是时间戳以外的写法时，仍按 mtime 升序，最后一项才是最新成片。"""
    _mk_extract("p1", tmp_path, "v1")
    _mk_extract("p1", tmp_path, "v2")
    case_mod.set_extract_current("p1", "v2", root=tmp_path)
    out = tmp_path / "p1" / "out"
    out.mkdir()
    (out / "v1_space.mp4").write_bytes(b"old")
    (out / "v2_space.mp4").write_bytes(b"new")
    os.utime(out / "v1_space.mp4", (2_000, 2_000))
    os.utime(out / "v2_space.mp4", (1_000, 1_000))

    outs = client.get("/api/case/p1").json()["outputs"]
    assert [o["file"] for o in outs] == ["v2_space.mp4", "v1_space.mp4"]


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


def test_submit_roles_endpoint(client, patch_case, tmp_path):
    _add_image("p1", tmp_path, "a", "subject_ref")
    _add_image("p1", tmp_path, "b", "last_frame")
    r = client.put("/api/case/p1/submit", json={
        "selected": ["i1", "i2"],
        "roles": {"i1": "subject_ref", "i2": "last_frame"}})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["submit_roles"] == {"i1": "subject_ref", "i2": "last_frame"}
    assert info["submit_entries"] == [
        {"asset": "i1", "role": "subject_ref"},
        {"asset": "i2", "role": "last_frame"},
    ]
    assert info["role_help"]["subject_ref"].startswith("主体参考")
    # 只给 selected 时保留仍在选中的旧覆盖。
    r = client.put("/api/case/p1/submit", json={"selected": ["i2"]})
    assert r.status_code == 200, r.text
    assert r.json()["submit_roles"] == {"i2": "last_frame"}
    assert client.get("/api/case/p1/submit").json()["roles"] == {"i2": "last_frame"}


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
        "step": "first", "image_model_id": 44, "resolution": "2K"})
    assert r.status_code == 200, r.text
    op, name, argv = captured[0]
    assert op == "keyframe" and name == "p1"
    assert "image" in argv and "submit" in argv
    assert "--ratio" in argv and "16:9" in argv
    assert argv[argv.index("--image-model-id") + 1] == "44"
    assert argv[argv.index("--template") + 1] == "keyframe-first"
    assert argv[argv.index("--vars-file") + 1] == "keyframe-vars.toml"
    assert argv[argv.index("--step") + 1] == "first"
    assert argv[argv.index("--session-id") + 1] == "123"
    assert argv.count("--image") == 2
    assert argv.count("--image-role") == 2
    assert argv.count("--image-note") == 2
    # 图片步只认原始帧 + 绑定的三视图，不读 [submit] selected。
    case_mod.set_submit_selected("p1", [], root=tmp_path)
    r = client.post("/api/case/p1/keyframe/run", json={"step": "first"})
    assert r.status_code == 200, r.text


def test_keyframe_run_includes_detail_refs_and_notes(
        client, patch_case, tmp_path, monkeypatch):
    d = _mk_extract("p1", tmp_path, "v1")
    (d / "first_frame_raw.png").write_bytes(b"first")
    (d / "last_frame_raw.png").write_bytes(b"last")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    _add_image("p1", tmp_path, "sheet", "subject_ref")
    _add_image("p1", tmp_path, "detail", "subject_ref")
    r = client.put("/api/case/p1/keyframe/refs", json={"refs": [
        {"asset": "i1", "role": "subject_ref", "note": ""},
        {"asset": "i2", "role": "detail_ref", "note": "头冠纹样"},
    ]})
    assert r.status_code == 200, r.text
    captured = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda op, name, argv, **kwargs:
                        captured.append((op, name, argv)))
    monkeypatch.setattr(web_app, "_current_session", lambda name: 123)
    r = client.post("/api/case/p1/keyframe/run", json={"step": "first"})
    assert r.status_code == 200, r.text
    argv = captured[0][2]
    roles = [argv[index + 1] for index, item in enumerate(argv)
             if item == "--image-role"]
    notes = [argv[index + 1] for index, item in enumerate(argv)
             if item == "--image-note"]
    assert roles == ["first_frame_raw", "subject_ref", "detail_ref"]
    assert notes == ["", "", "头冠纹样"]
    assert argv.count("--image") == 3


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


def test_keyframe_refs_endpoint(client, patch_case, tmp_path):
    _add_image("p1", tmp_path, "a", "subject_ref")
    _add_image("p1", tmp_path, "b", "subject_ref")
    r = client.put("/api/case/p1/keyframe/refs", json={"refs": [
        {"asset": "i1", "role": "subject_ref", "note": ""},
        {"asset": "i2", "role": "detail_ref", "note": "袖口纹样"},
    ]})
    assert r.status_code == 200, r.text
    info = r.json()
    assert [(ref["asset"], ref["role"], ref["note"])
            for ref in info["keyframe"]["refs"]] == [
        ("i1", "subject_ref", ""), ("i2", "detail_ref", "袖口纹样")]
    assert info["submit_selected"] is None
    assert client.get("/api/case/p1/keyframe/refs").json()["refs"][1]["note"] == "袖口纹样"

    r = client.put("/api/case/p1/keyframe/refs", json={"refs": [
        {"asset": "i1", "role": "subject_ref"},
        {"asset": "i2", "role": "subject_ref"},
    ]})
    assert r.status_code == 400


def test_keyframe_prompt_uses_vars_file(client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    vars_path = tmp_path / "runs" / "p1" / "keyframe-vars.toml"
    vars_path.parent.mkdir(parents=True)
    vars_path.write_text("template = 'keyframe-first'\n[vars]\n", encoding="utf-8")
    calls = []

    def fake(argv):
        calls.append(argv)
        return {"template": "keyframe-first",
                "vars": {"figure": "x", "pose_first": "y", "pose_last": "z"},
                "rendered": "图1 = 首帧原始图。",
                "placeholders": ["image_roles", "pose_first", "figure", "preserve"],
                "computed_keys": ["image_roles"], "labels": {}, "help": {}}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    r = client.get("/api/case/p1/keyframe/prompt?step=first")
    assert r.status_code == 200, r.text
    assert r.json()["vars"] == {"figure": "x", "pose_first": "y"}
    argv = calls[0]
    assert argv[0] == "prompt"          # _vrgen_json 自己补可执行文件，不能再传一遍
    assert argv[argv.index("--vars-file") + 1] == "keyframe-vars.toml"
    assert argv[argv.index("--template") + 1] == "keyframe-first"
    assert argv.count("--image-role") == 2
    assert argv.count("--image-note") == 2

    r = client.put("/api/case/p1/keyframe/prompt",
                   json={"step": "first", "vars": {"figure": "新外观"}})
    assert r.status_code == 200, r.text
    assert "--set" in calls[-1]
    assert "figure=新外观" in calls[-1]
    assert r.json()["vars"] == {"figure": "x", "pose_first": "y"}


def test_keyframe_prompt_backfills_new_template_slots(client, patch_case, tmp_path,
                                                      monkeypatch):
    """模板新增槽位后，旧草稿按 placeholders 差集补默认值，不需要手工迁移。"""
    _keyframe_ready(tmp_path, monkeypatch)
    vars_path = tmp_path / "runs" / "p1" / "keyframe-vars.toml"
    vars_path.parent.mkdir(parents=True)
    vars_path.write_text("template = 'keyframe-first'\n[vars]\nfigure = 'x'\n",
                         encoding="utf-8")
    state = {"figure": "x"}
    calls = []

    def fake(argv):
        calls.append(argv)
        if argv[1] == "save":
            for index, item in enumerate(argv):
                if item == "--set":
                    key, _, value = argv[index + 1].partition("=")
                    state[key] = value
            return {"template": "keyframe-first", "vars": dict(state)}
        return {"template": "keyframe-first",
                "vars": dict(state),
                "placeholders": ["image_roles", "target", "source_first", "figure",
                                 "pose_first", "preserve", "cleanup"],
                "computed_keys": ["image_roles"],
                "rendered": "图1 = 首帧原始图。",
                "labels": {}, "help": {}}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    r = client.get("/api/case/p1/keyframe/prompt?step=first")
    assert r.status_code == 200, r.text
    save = next(call for call in calls if call[1] == "save")
    joined = " ".join(save)
    assert "target=人物" in joined
    assert "cleanup=" in joined
    assert "preserve=" in joined
    body = r.json()
    assert set(body["vars"]) == {"target", "source_first", "figure",
                                 "pose_first", "preserve", "cleanup"}
    assert body["vars"]["target"] == "人物"


def test_keyframe_prompt_saves_new_slots(client, patch_case, tmp_path, monkeypatch):
    """槽位随模板演进，保存接口不能再用四个键的白名单拦住新槽位。"""
    _keyframe_ready(tmp_path, monkeypatch)
    calls = []

    def fake(argv):
        calls.append(argv)
        return {"template": "keyframe-first",
                "vars": {"target": "三位舞女", "cleanup": "", "subject_last": ""},
                "rendered": "以图1为底图做局部编辑。",
                "placeholders": ["image_roles", "target", "cleanup", "subject_last"],
                "computed_keys": ["image_roles"], "labels": {}, "help": {}}

    monkeypatch.setattr(web_app, "_vrgen_json", fake)
    r = client.put("/api/case/p1/keyframe/prompt",
                   json={"step": "first",
                         "vars": {"target": "三位舞女", "cleanup": "",
                                  "source_last": "", "subject_last": ""}})
    assert r.status_code == 200, r.text
    save = calls[-1]
    assert "target=三位舞女" in save
    assert "source_last=" in save
    assert "subject_last=" in save
    assert r.json()["vars"]["target"] == "三位舞女"


def test_keyframe_pick_first_does_not_submit_a_batch(
        client, patch_case, tmp_path, monkeypatch):
    """定稿只写台账；消耗额度的批次必须由用户点按钮提交（2026-10-26 决策）。"""
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
    captured = []
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda op, name, argv, **kwargs:
                        captured.append((op, name, argv)))

    r = client.post("/api/case/p1/keyframe/pick",
                    json={"round": "img-abc12345", "index": 1, "role": "first_frame"})
    assert r.status_code == 200, r.text
    info = r.json()
    assert captured == []
    assert "keyframe_queued" not in info
    assert "不会自动生成" in info["keyframe_hint"]
    state = keyframe.load(case_mod.case_dir("p1", tmp_path))
    assert state.first is not None and state.first.round == "img-abc12345"
    assert state.first.asset == info["assets"][-1]["id"]


def test_keyframe_handoff_sets_mode_and_selection(client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    _add_image("p1", tmp_path, "first", "first_frame")
    _add_image("p1", tmp_path, "last", "last_frame")
    keyframe.save(case_mod.case_dir("p1", tmp_path), keyframe.State(
        extract="v1", refs=[keyframe.Ref("i1", "subject_ref")],
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
    keyframe.save(cdir, keyframe.State(
        refs=[keyframe.Ref("i9", "subject_ref")]))
    info = client.get("/api/case/p1").json()
    assert info["keyframe"]["refs"][0]["asset"] == "i9"
    assert info["keyframe"]["refs"][0]["exists"] is False
    # 指针指向的素材角色已经变了：按未定稿处理。
    keyframe.save(cdir, keyframe.State(
        refs=[keyframe.Ref("i1", "subject_ref")],
        first=keyframe.Pick("img-a", 1, "i1")))
    info = client.get("/api/case/p1").json()
    assert info["keyframe"]["first"] is None


def test_keyframe_stale_only_blocks_what_depends_on_old_picks(client, patch_case,
                                                              tmp_path, monkeypatch):
    """抽取版本更新后：提示词能看能改、首帧候选能生成；尾帧与交接才要重定稿。

    首帧生成只用到当前抽取的原始帧与三视图，拿旧定稿去挡它等于让用户没法
    重新定稿（旧定稿作废，新候选又生不出来）。
    """
    d = _mk_extract("p1", tmp_path, "v1")
    (d / "first_frame_raw.png").write_bytes(b"first")
    (d / "last_frame_raw.png").write_bytes(b"last")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    _add_image("p1", tmp_path, "sheet", "subject_ref")
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "runs")
    vars_path = tmp_path / "runs" / "p1" / "keyframe-vars.toml"
    vars_path.parent.mkdir(parents=True)
    vars_path.write_text("template = 'keyframe-first'\n[vars]\n", encoding="utf-8")
    # 定稿挂在旧版本上，case 已重新抽取
    keyframe.save(case_mod.case_dir("p1", tmp_path), keyframe.State(
        extract="v0", refs=[keyframe.Ref("i1", "subject_ref")]))
    case_mod.set_extract_current("p1", "v2", root=tmp_path)

    monkeypatch.setattr(web_app, "_vrgen_json", lambda argv: {
        "template": "keyframe-first", "vars": {},
        "rendered": "图1 = 首帧原始图。",
        "placeholders": ["image_roles", "target", "figure"],
        "computed_keys": ["image_roles"], "labels": {}, "help": {}})

    r = client.get("/api/case/p1/keyframe/prompt?step=first")
    assert r.status_code == 200, r.text
    assert r.json()["rendered"] == "图1 = 首帧原始图。"

    calls = []
    monkeypatch.setattr(web_app, "_vrgen_json",
                        lambda argv: calls.append(argv) or {"vars": {}, "rendered": "x"})
    r = client.put("/api/case/p1/keyframe/prompt",
                   json={"step": "first", "vars": {"figure": "新外观"}})
    assert r.status_code == 200, r.text
    assert "figure=新外观" in calls[-1]

    # 首帧候选照常能生成：入参是当前抽取的原始首帧 + 三视图
    submitted = []
    monkeypatch.setattr(web_app, "_current_session", lambda name: 2062569522605504)
    monkeypatch.setattr(web_app, "_keyframe_ratio", lambda case: "16:9")
    monkeypatch.setattr(web_app.QUEUE, "submit",
                        lambda op, name, argv, **kw: submitted.append((op, argv)))
    r = client.post("/api/case/p1/keyframe/run", json={"step": "first"})
    assert r.status_code == 200, r.text
    argv = submitted[0][1]
    assert argv[argv.index("--step") + 1] == "first"
    assert "first_frame_raw" in " ".join(argv)

    # 尾帧要的是当前抽取上定稿的首帧，旧定稿不能用
    r = client.post("/api/case/p1/keyframe/run", json={"step": "last"})
    assert r.status_code == 400
    assert "先生成并挑选首帧定稿" in r.json()["error"]

    # 交接用的也是两帧定稿，继续按过期挡住
    r = client.post("/api/case/p1/keyframe/handoff")
    assert r.status_code == 400
    assert "重新定稿" in r.json()["error"]


def test_keyframe_last_pick_compares_with_the_current_first_pick(
        client, patch_case, tmp_path, monkeypatch):
    """尾帧宽高比对的参照物是「当前定稿的首帧」，不是 assets 里最早的那张首帧。

    dance_move 的实际情况：新首帧是 2K（2048x1152），更早的首帧是 4K；按最早
    那张比会把同尺寸的新尾帧判成不一致，用户就永远配不出这一对。
    """
    _keyframe_ready(tmp_path, monkeypatch)
    _add_image("p1", tmp_path, "old_first", "first_frame")     # i2：更早的 4K 首帧
    _add_image("p1", tmp_path, "new_first", "first_frame")     # i3：当前定稿的 2K 首帧
    cdir = case_mod.case_dir("p1", tmp_path)
    keyframe.save(cdir, keyframe.State(
        extract="v1", refs=[keyframe.Ref("i1", "subject_ref")],
        first=keyframe.Pick("img-new", 1, "i3")))

    sizes = {"old_first": (3840, 2160), "new_first": (2048, 1152),
             "1": (2048, 1152), "2": (3840, 2160)}

    def meta(path, kind):
        """按文件内容认图：临时出的候选文件里写的是候选下标。"""
        raw = Path(path).read_bytes()[:32].decode("utf-8", "replace").strip()
        key = raw if raw in sizes else Path(path).stem.split("_")[0]
        width, height = sizes.get(key, (2048, 1152))
        return {"width": width, "height": height}

    def fake_capture(argv):
        Path(argv[argv.index("--out") + 1]).write_text(
            argv[argv.index("--index") + 1], encoding="utf-8")
        return 0, "ok"

    monkeypatch.setattr(web_app, "_media_meta", meta)
    monkeypatch.setattr(web_app, "_capture", fake_capture)
    run_dir = tmp_path / "runs" / "p1" / "img-last"
    run_dir.mkdir(parents=True)
    for index in (1, 2):
        (run_dir / f"result_{index}.png").write_bytes(b"c")
    (run_dir / "run.json").write_text(json.dumps({
        "key": "last-round", "results": [str(run_dir / "result_1.png"),
                                        str(run_dir / "result_2.png")]}),
        encoding="utf-8")

    # 与当前首帧同为 2K：放行
    r = client.post("/api/case/p1/keyframe/pick",
                    json={"round": "img-last", "index": 1, "role": "last_frame"})
    assert r.status_code == 200, r.text
    assert keyframe.load(cdir).last is not None

    # 4K 候选与当前 2K 首帧不一致：拒绝，并指明比的是哪张首帧
    r = client.post("/api/case/p1/keyframe/pick",
                    json={"round": "img-last", "index": 2, "role": "last_frame"})
    assert r.status_code == 400
    assert "当前首帧 i3" in r.json()["error"]


def test_keyframe_last_pick_rejected_when_the_pair_is_stale(
        client, patch_case, tmp_path, monkeypatch):
    """过期状态下只挑尾帧会拼出一对跨版本的图，入口直接拒绝。"""
    _keyframe_ready(tmp_path, monkeypatch)
    _add_image("p1", tmp_path, "sheet2", "first_frame")
    cdir = case_mod.case_dir("p1", tmp_path)
    keyframe.save(cdir, keyframe.State(
        extract="v0", refs=[keyframe.Ref("i1", "subject_ref")],
        first=keyframe.Pick("img-a", 1, "i2")))
    case_mod.set_extract_current("p1", "v2", root=tmp_path)
    run_dir = tmp_path / "runs" / "p1" / "img-last"
    run_dir.mkdir(parents=True)
    (run_dir / "result_1.png").write_bytes(b"c")
    (run_dir / "run.json").write_text(json.dumps({
        "key": "k", "results": [str(run_dir / "result_1.png")]}), encoding="utf-8")
    monkeypatch.setattr(web_app, "_media_meta",
                        lambda path, kind: {"width": 2048, "height": 1152})
    monkeypatch.setattr(web_app, "_capture", lambda argv: (0, "ok"))

    r = client.post("/api/case/p1/keyframe/pick",
                    json={"round": "img-last", "index": 1, "role": "last_frame"})
    assert r.status_code == 400
    assert "先生成并挑选首帧定稿" in r.json()["error"]
    assert keyframe.load(cdir).last is None


def test_keyframe_use_raw_frames_for_both_steps(client, patch_case, tmp_path, monkeypatch):
    """「直接用原始帧」：不生成、不花额度，两帧都定了就能交接。"""
    _keyframe_ready(tmp_path, monkeypatch)
    cdir = case_mod.case_dir("p1", tmp_path)

    r = client.post("/api/case/p1/keyframe/use-raw", json={"step": "first"})
    assert r.status_code == 200, r.text
    state = keyframe.load(cdir)
    assert state.extract == "v1"
    assert state.first is not None and state.last is None
    assets = case_mod.load("p1", tmp_path).assets
    assert assets[-1].role == "first_frame"
    assert state.first.asset == assets[-1].id
    info = r.json()
    assert info["keyframe"]["first"]["source"] == "raw"

    r = client.post("/api/case/p1/keyframe/use-raw", json={"step": "last"})
    assert r.status_code == 200, r.text
    state = keyframe.load(cdir)
    assert state.last is not None
    assets = case_mod.load("p1", tmp_path).assets
    assert assets[-1].role == "last_frame"

    modes = []
    monkeypatch.setattr(web_app, "_save_gen_mode",
                        lambda name, mode: modes.append(mode) or None)
    r = client.post("/api/case/p1/keyframe/handoff")
    assert r.status_code == 200, r.text
    assert modes == ["firstLastFrame"]
    assert r.json()["submit_selected"] == [state.first.asset, state.last.asset]


def test_keyframe_use_raw_needs_the_current_extract_frames(client, patch_case,
                                                           tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    (case_mod.case_dir("p1", tmp_path) / "extracts" / "v1"
     / "first_frame_raw.png").unlink()
    r = client.post("/api/case/p1/keyframe/use-raw", json={"step": "first"})
    assert r.status_code == 400
    assert "原始帧" in r.json()["error"]


def test_keyframe_use_raw_last_is_guarded_but_first_reanchors(
        client, patch_case, tmp_path, monkeypatch):
    """尾帧要跟当前版本的首帧配对；首帧本身就是版本锚，过期时正好用它重锚。"""
    _keyframe_ready(tmp_path, monkeypatch)
    cdir = case_mod.case_dir("p1", tmp_path)
    keyframe.save(cdir, keyframe.State(extract="v0",
                                       refs=[keyframe.Ref("i1", "subject_ref")]))
    # 新抽取也有自己的两张原始帧
    d = _mk_extract("p1", tmp_path, "v2")
    (d / "first_frame_raw.png").write_bytes(b"first2")
    (d / "last_frame_raw.png").write_bytes(b"last2")
    case_mod.set_extract_current("p1", "v2", root=tmp_path)

    r = client.post("/api/case/p1/keyframe/use-raw", json={"step": "last"})
    assert r.status_code == 400
    assert "先把首帧定稿到当前版本" in r.json()["error"]

    r = client.post("/api/case/p1/keyframe/use-raw", json={"step": "first"})
    assert r.status_code == 200, r.text
    assert keyframe.load(cdir).extract == "v2"


def test_keyframe_use_raw_rejects_other_flows(client, tmp_path, monkeypatch):
    case_mod.create("g7", "gen", root=tmp_path)
    _add_image("g7", tmp_path, "first", "first_frame")
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "runs")
    r = client.post("/api/case/g7/keyframe/use-raw", json={"step": "first"})
    assert r.status_code == 400
    assert "只接 patch" in r.json()["error"]


def test_duration_gate_names_the_mode_limit(client, patch_case, tmp_path):
    """clip 时长超出模式区间时，提交前的理由要说清是哪一项超了。

    平台只会回「素材 clip 不合约」；16.2 秒的 clip 对上 allMediaRef 的 15 秒
    上限就是典型场景（dance_move 2026-10-30）。
    """
    cdir = case_mod.case_dir("p1", tmp_path)
    d = cdir / "extracts" / "v1"
    d.mkdir(parents=True)
    (d / "clip.json").write_text(json.dumps({
        "erp_width": 1920, "erp_height": 960, "fps": 30.0,
        "segments": [{"id": "s0", "frame_start": 0, "frame_end": 479,
                      "viewport": VIEWPORT, "inner": INNER}]}), encoding="utf-8")
    case_mod.set_extract_current("p1", "v1", root=tmp_path)
    _add_image("p1", tmp_path, "clip_ref", "subject_ref")
    case_mod.set_submit_selected("p1", ["i1"], root=tmp_path)
    case = case_mod.load("p1", tmp_path)

    def run_gate(seconds_note: dict) -> dict:
        info = {"gates": {"generate": {"ok": False, "ready": True}},
                "generation_params": {"modes": [{"value": "allMediaRef",
                                                 "duration": {"min": 4, "max": 15}}],
                                      **seconds_note}}
        web_app._apply_mode_gate(info, case, "allMediaRef")
        return info["gates"]["generate"]

    gate = run_gate({})
    assert gate["ready"] is False
    assert "16 秒" in gate["reason"] and "4–15 秒" in gate["reason"]

    # 缩到 15 秒以内（450 帧）就不再挡
    (d / "clip.json").write_text(json.dumps({
        "erp_width": 1920, "erp_height": 960, "fps": 30.0,
        "segments": [{"id": "s0", "frame_start": 0, "frame_end": 449,
                      "viewport": VIEWPORT, "inner": INNER}]}), encoding="utf-8")
    assert run_gate({})["ready"] is True


def test_keyframe_gate_states(client, patch_case, tmp_path, monkeypatch):
    _keyframe_ready(tmp_path, monkeypatch)
    info = client.get("/api/case/p1").json()
    gate = info["gates"]["keyframe"]
    assert gate["ok"] is False and "首帧" in gate["reason"]

    _add_image("p1", tmp_path, "first", "first_frame")
    _add_image("p1", tmp_path, "last", "last_frame")
    cdir = case_mod.case_dir("p1", tmp_path)
    keyframe.save(cdir, keyframe.State(
        extract="v1", refs=[keyframe.Ref("i1", "subject_ref")],
        first=keyframe.Pick("img-a", 1, "i2"),
        last=keyframe.Pick("img-b", 1, "i3")))
    info = client.get("/api/case/p1").json()
    assert info["gates"]["keyframe"]["ok"] is True

    keyframe.save(cdir, keyframe.State(
        extract="v0", refs=[keyframe.Ref("i1", "subject_ref")],
        first=keyframe.Pick("img-a", 1, "i2"),
        last=keyframe.Pick("img-b", 1, "i3")))
    info = client.get("/api/case/p1").json()
    gate = info["gates"]["keyframe"]
    assert gate["ok"] is False and "抽取版本" in gate["reason"]


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


def test_identity_accepts_non_ascii_name(client):
    """中文昵称要能存：cookie 值只能是 ASCII，写入按 UTF-8 百分号编码。

    未编码时 Starlette 在 set_cookie 里抛 UnicodeEncodeError，整个请求变 500。
    """
    saved = client.post("/api/me", json={"display_name": "张三"})
    assert saved.status_code == 200
    assert saved.json()["display_name"] == "张三"
    assert client.get("/api/me").json()["display_name"] == "张三"


def test_request_path_does_not_reload_config(client, patch_case, monkeypatch):
    """配置在 build_app 时定格；请求路径再读盘就会让代码与配置在运行中错开。"""
    monkeypatch.setattr(config_mod, "load",
                        lambda *args, **kwargs: pytest.fail("请求路径不应重新加载配置"))
    response = client.get(f"/api/case/{patch_case}")
    assert response.status_code == 200


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


def test_task_history_survives_restart(tmp_path, monkeypatch):
    """任务历史落盘：serve 重启后队列重建仍能列出已结束任务与日志尾巴。"""
    monkeypatch.setenv("VRSTUDIO_LOGS_DIR", str(tmp_path / "logs"))
    task = tasks_mod.Task("submit", "p1", ["echo"])
    task.state = "done"
    task.created_at = "2026-11-02T09:49:45+08:00"
    task.started_at = "2026-11-02T09:49:46+08:00"
    task.finished_at = "2026-11-02T09:50:59+08:00"
    task.out_path = tasks_mod._task_log_path(task.id, task.kind, task.case)
    task.out_path.write_text("gate ok 1 candidate(s)\n", encoding="utf-8")

    tasks_mod.TaskQueue().record(task)
    restarted = tasks_mod.TaskQueue()
    restarted.restore_history()
    rows = restarted.list()
    assert [row["id"] for row in rows] == [task.id]
    assert rows[0]["status"] == "done"
    assert rows[0]["tail"] == ["gate ok 1 candidate(s)"]

    task.out_path.unlink()
    gone = tasks_mod.TaskQueue()
    gone.restore_history()
    assert gone.list() == []


def test_reused_marker_reads_the_submit_log(tmp_path):
    """vrgen 的 reused 行是「没花额度」的唯一机器可读信号。"""
    path = tmp_path / "submit.log"
    path.write_text(
        "WARN this round already exists: gen-multi-6d0da795 (stage checked) -- reusing it\n"
        "job         dance_walk_01\n"
        "round       E:\\runs\\dance_walk_01\\gen-multi-6d0da795\n"
        "reused      gen-multi-6d0da795  reason=key  task=2067798413457408  "
        "(本次未提交，未花费)\n"
        "result      E:\\runs\\dance_walk_01\\gen-multi-6d0da795\\result.mp4  (9.7 MB)\n",
        encoding="utf-8")
    assert tasks_mod._reused_marker(path) == {
        "round": "gen-multi-6d0da795", "reason": "key", "task_id": 2067798413457408}


def test_reused_marker_reads_resumed_without_a_task_id(tmp_path):
    path = tmp_path / "submit.log"
    path.write_text(
        "reused      img-1234abcd  reason=resumed  task=-  (本次未提交，未花费)\n",
        encoding="utf-8")
    assert tasks_mod._reused_marker(path) == {
        "round": "img-1234abcd", "reason": "resumed", "task_id": None}


def test_reused_marker_absent_for_a_real_submit(tmp_path):
    path = tmp_path / "submit.log"
    path.write_text("round       gen-multi-6d0da795\nresult      ok\n", encoding="utf-8")
    assert tasks_mod._reused_marker(path) is None


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
