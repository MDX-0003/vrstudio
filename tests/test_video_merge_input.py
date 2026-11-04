"""视频流（fullredraw）的拼接输入：指针、种类校验与 web 装配。

图片流程的同一组行为由 ``test_fullredraw_img.py`` / ``test_web_image_flows.py``
守护；这里只测视频流扩展出来的部分，两条流程共用 ``[merge] input`` 与
``merge_input`` 角色（决策 78）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio.case import CaseError
from vrstudio.media.imageio import write_png
from vrstudio.web import app as web_app


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


@pytest.fixture
def video_case(tmp_path, client) -> str:
    src = tmp_path / "pano.mp4"
    src.write_bytes(b"fake erp video")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    return "f1"


def _video(tmp_path, name: str = "edited.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(b"fake canvas video")
    return path


def _add_video_asset(tmp_path, name: str = "f1") -> str:
    case_mod.add_assets(name, [(_video(tmp_path), "edit_master")], root=tmp_path)
    return case_mod.load(name, tmp_path).assets[-1].id


# ── case 模型 ───────────────────────────────────────────────────────────


def test_merge_input_roundtrip_and_clear(tmp_path, video_case):
    case_id = _add_video_asset(tmp_path)
    case_mod.set_merge_input(video_case, case_id, root=tmp_path)

    case = case_mod.load(video_case, tmp_path)
    assert case.merge_input == case_id
    assert case.merge_input_file(tmp_path).name == f"{case_id}_edited.mp4"
    text = case_mod.case_toml(video_case, tmp_path).read_text(encoding="utf-8")
    assert "[merge]" in text and f'input = "{case_id}"' in text

    case_mod.set_merge_input(video_case, None, root=tmp_path)
    assert case_mod.load(video_case, tmp_path).merge_input is None
    assert "[merge]" not in case_mod.case_toml(video_case, tmp_path).read_text(
        encoding="utf-8")


def test_merge_input_falls_back_to_the_adopted_video(tmp_path, video_case):
    cdir = case_mod.case_dir(video_case, tmp_path)
    assert case_mod.load(video_case, tmp_path).merge_input_file(tmp_path) is None
    (cdir / "result.mp4").write_bytes(b"adopted")
    assert case_mod.load(video_case, tmp_path).merge_input_file(tmp_path) == (
        cdir / "result.mp4")


def test_merge_input_rejects_an_image_asset(tmp_path, video_case):
    image = tmp_path / "canvas.png"
    write_png(image, np.zeros((8, 18, 3), dtype=np.uint8))
    case_mod.add_assets(video_case, [(image, "lighting_ref")], root=tmp_path)
    with pytest.raises(CaseError, match="拼接输入必须是视频"):
        case_mod.set_merge_input(video_case, "i1", root=tmp_path)


def test_merge_input_rejects_a_dangling_pointer(tmp_path, video_case):
    toml = case_mod.case_toml(video_case, tmp_path)
    toml.write_text(toml.read_text(encoding="utf-8")
                    + '\n[merge]\ninput = "v7"\n', encoding="utf-8")
    with pytest.raises(CaseError, match="不存在的素材"):
        case_mod.load(video_case, tmp_path)


def test_merge_input_only_belongs_to_merge_flows(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    case_mod.create("g", "gen", src, root=tmp_path)
    toml = case_mod.case_toml("g", tmp_path)
    toml.write_text(toml.read_text(encoding="utf-8")
                    + '\n[merge]\ninput = "v1"\n', encoding="utf-8")
    with pytest.raises(CaseError, match="只属于"):
        case_mod.load("g", tmp_path)


def test_add_merge_input_dedupes_and_uses_the_role(tmp_path, video_case):
    path = _video(tmp_path)
    case, case_id = case_mod.add_merge_input(video_case, path, root=tmp_path)
    assert case.merge_input == case_id
    assert (case.asset(case_id).role, case.asset(case_id).kind) == (
        case_mod.MERGE_INPUT, "video")

    again, again_id = case_mod.add_merge_input(video_case, path, root=tmp_path)
    assert again_id == case_id
    assert len(again.assets) == 1


def test_add_merge_input_rejects_an_image(tmp_path, video_case):
    image = tmp_path / "canvas.png"
    write_png(image, np.zeros((8, 18, 3), dtype=np.uint8))
    with pytest.raises(CaseError, match="不是视频"):
        case_mod.add_merge_input(video_case, image, root=tmp_path)


def test_removing_the_merge_input_asset_clears_the_pointer(tmp_path, video_case):
    case_id = _add_video_asset(tmp_path)
    case_mod.set_merge_input(video_case, case_id, root=tmp_path)
    case_mod.remove_asset(video_case, case_id, root=tmp_path)
    assert case_mod.load(video_case, tmp_path).merge_input is None


# ── web 装配 ────────────────────────────────────────────────────────────


def _manifest(tmp_path, name: str = "f1") -> None:
    cdir = case_mod.case_dir(name, tmp_path)
    (cdir / "seedance_input_pad.json").write_text("{}", encoding="utf-8")


def test_recompose_uses_the_merge_input(tmp_path, video_case):
    _manifest(tmp_path)
    case_id = _add_video_asset(tmp_path)
    case_mod.set_merge_input(video_case, case_id, root=tmp_path)

    argv = web_app._op_argv(video_case, "recompose", {})
    assert argv[argv.index("--in") + 1] == str(
        case_mod.load(video_case, tmp_path).merge_input_file(tmp_path))


def test_recompose_falls_back_to_the_adopted_video(tmp_path, video_case):
    _manifest(tmp_path)
    cdir = case_mod.case_dir(video_case, tmp_path)
    (cdir / "result.mp4").write_bytes(b"adopted")

    argv = web_app._op_argv(video_case, "recompose", {})
    assert argv[argv.index("--in") + 1] == str(cdir / "result.mp4")


def test_recompose_ready_from_a_library_input(tmp_path, video_case):
    _manifest(tmp_path)
    case = case_mod.load(video_case, tmp_path)
    assert web_app._gates(case, [])["recompose"]["ready"] is False

    case_id = _add_video_asset(tmp_path)
    case_mod.set_merge_input(video_case, case_id, root=tmp_path)
    gates = web_app._gates(case_mod.load(video_case, tmp_path), [])
    assert gates["recompose"]["ready"] is True


def test_step_files_show_the_library_input(tmp_path, video_case):
    _manifest(tmp_path)
    case_id = _add_video_asset(tmp_path)
    case_mod.set_merge_input(video_case, case_id, root=tmp_path)

    files = web_app._step_files(case_mod.load(video_case, tmp_path))
    names = [item["name"] for item in files["recompose"]["inputs"]]
    assert names[0].startswith(f"{case_id}_")
    assert "seedance_input_pad.json" in names


def _upload(client, name: str, payload: bytes) -> str:
    session = client.post("/api/uploads", json={"name": name, "size": len(payload)}).json()
    upload_id = session["upload_id"]
    r = client.put(f"/api/uploads/{upload_id}", content=payload,
                   headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 200
    return upload_id


def test_merge_input_op_uploads_and_selects_a_video(tmp_path, video_case, client):
    payload = b"fake canvas video"
    upload_id = _upload(client, "edited.mp4", payload)

    r = client.post("/api/case/f1/assets",
                    json={"op": "merge-input", "upload_ids": [upload_id]})
    assert r.status_code == 200, r.text
    case = case_mod.load(video_case, tmp_path)
    assert case.merge_input is not None
    assert (case.asset(case.merge_input).role, case.asset(case.merge_input).kind) == (
        "merge_input", "video")


def test_merge_input_op_refuses_an_image_asset(tmp_path, video_case, client):
    image = tmp_path / "canvas.png"
    write_png(image, np.zeros((8, 18, 3), dtype=np.uint8))
    case_mod.add_assets(video_case, [(image, "lighting_ref")], root=tmp_path)

    r = client.post("/api/case/f1/assets",
                    json={"op": "merge-input", "asset_id": "i1"})
    assert r.status_code == 400 and "拼接输入必须是视频" in r.json()["error"]
