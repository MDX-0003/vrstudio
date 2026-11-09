"""新建 case 的 upload_id 分支：上传会话直接作为源视频。

用真实分片端点构造 complete 会话；sources_dir 在测试里指向 tmp_path，
避免污染仓库 sources/。同名冲突的语义见决策 95 与
``docs/plan/source-upload-reuse-plan.md``。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio.web import app as web_app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(web_app, "_vrgen_exe", lambda: "definitely-not-a-real-vrgen")
    monkeypatch.setattr(web_app, "sources_dir", lambda: tmp_path / "sources")
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "vrgen_runs")
    return TestClient(web_app.build_app(cases_root=tmp_path))


def _write_video(path: Path, frames: int = 4, fps: float = 30.0) -> Path:
    """写一段 cv2 能读出帧数与宽高的真视频（内容随帧数变化，便于比较字节）。"""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 32))
    assert writer.isOpened(), f"cannot open VideoWriter for {path}"
    for index in range(frames):
        writer.write(np.full((32, 64, 3), (index * 40) % 256, dtype=np.uint8))
    writer.release()
    return path


def _upload(client: TestClient, name: str, payload: bytes) -> str:
    """走真实分片端点完成一次上传，返回 file_id。"""
    session = client.post("/api/uploads", json={"name": name, "size": len(payload)}).json()
    upload_id = session["upload_id"]
    r = client.put(f"/api/uploads/{upload_id}", content=payload,
                   headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    done = client.post(f"/api/uploads/{upload_id}/complete")
    assert done.status_code == 200, done.text
    return upload_id


def _create(client: TestClient, **payload):
    return client.post("/api/cases", json={"flow": "patch", "name": "p1", **payload})


def _sources(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "sources").glob("*") if p.is_file())


def test_create_case_from_upload_lands_in_sources(client, tmp_path):
    """假字节过不了 cv2 探测（400），但文件已入库、会话已清理。"""
    upload_id = _upload(client, "clip.mp4", b"not-a-real-video")
    r = _create(client, upload_id=upload_id)
    assert r.status_code == 400
    assert "打不开这个视频" in r.json()["error"]
    assert (tmp_path / "sources" / "clip.mp4").is_file()
    complete = tmp_path / ".uploads" / "complete"
    assert not complete.exists() or not list(complete.glob("*"))


def test_create_case_reuses_same_content(client, tmp_path):
    """同名同内容：不落第二份，指向已有文件，会话被清理。"""
    payload = _write_video(tmp_path / "clip.mp4").read_bytes()
    first = _create(client, upload_id=_upload(client, "clip.mp4", payload))
    assert first.status_code == 200, first.text
    assert first.json()["source"] == {
        "name": "clip.mp4", "reused": False, "renamed_from": None}

    second = _create(client, name="p2", upload_id=_upload(client, "clip.mp4", payload))
    assert second.status_code == 200, second.text
    assert second.json()["source"] == {
        "name": "clip.mp4", "reused": True, "renamed_from": None}
    assert _sources(tmp_path) == ["clip.mp4"]
    complete = tmp_path / ".uploads" / "complete"
    assert not complete.exists() or not list(complete.glob("*"))


def test_create_case_renames_same_name_different_content(client, tmp_path):
    """同名不同内容：旧文件字节不动，新文件落成 <stem> (1)<ext>。"""
    first_bytes = _write_video(tmp_path / "clip.mp4", frames=4).read_bytes()
    second_bytes = _write_video(tmp_path / "clip.mp4", frames=8).read_bytes()
    assert first_bytes != second_bytes

    assert _create(client, upload_id=_upload(client, "clip.mp4", first_bytes)).status_code == 200
    second = _create(client, name="p2", upload_id=_upload(client, "clip.mp4", second_bytes))
    assert second.status_code == 200, second.text
    assert second.json()["source"] == {
        "name": "clip (1).mp4", "reused": False, "renamed_from": "clip.mp4"}
    assert (tmp_path / "sources" / "clip.mp4").read_bytes() == first_bytes
    assert (tmp_path / "sources" / "clip (1).mp4").read_bytes() == second_bytes


def test_create_case_rejects_incomplete_upload(client):
    session = client.post("/api/uploads", json={"name": "clip.mp4", "size": 100}).json()
    r = _create(client, upload_id=session["upload_id"])
    assert r.status_code == 400
    assert "还没有完成" in r.json()["error"]


def test_create_case_rejects_non_video_ext(client):
    upload_id = _upload(client, "pic.png", b"png-bytes")
    r = _create(client, upload_id=upload_id)
    assert r.status_code == 400
    assert "源视频必须" in r.json()["error"]


def test_create_case_rejects_upload_and_video_together(client):
    upload_id = _upload(client, "clip.mp4", b"bytes")
    r = _create(client, upload_id=upload_id, video="clip.mp4")
    assert r.status_code == 400
    assert "二选一" in r.json()["error"]


# ── 删除 case 时回收无人引用的源（决策 95）────────────────────────────


def _patch_case_with_source(client, tmp_path, name: str = "p1") -> Path:
    payload = _write_video(tmp_path / "clip.mp4").read_bytes()
    r = client.post("/api/cases", json={"flow": "patch", "name": name,
                                        "upload_id": _upload(client, "clip.mp4", payload)})
    assert r.status_code == 200, r.text
    return tmp_path / "sources" / "clip.mp4"


def test_delete_case_reclaims_unreferenced_source(client, tmp_path):
    source = _patch_case_with_source(client, tmp_path)
    r = client.delete("/api/case/p1")
    assert r.status_code == 200, r.text
    assert r.json()["source_reclaimed"]
    assert not source.exists()
    assert [p.name for p in (tmp_path / "sources" / ".trash").iterdir()]


def test_delete_case_keeps_shared_source(client, tmp_path):
    source = _patch_case_with_source(client, tmp_path)
    case_mod.create("p2", "patch", source, root=tmp_path)
    r = client.delete("/api/case/p1")
    assert r.status_code == 200, r.text
    assert r.json()["source_reclaimed"] is None
    assert source.is_file()


def test_delete_case_leaves_sources_outside_the_shared_dir_alone(client, tmp_path):
    outside = _write_video(tmp_path / "outside.mp4")
    case_mod.create("p1", "patch", outside, root=tmp_path)
    r = client.delete("/api/case/p1")
    assert r.status_code == 200, r.text
    assert r.json()["source_reclaimed"] is None
    assert outside.is_file()
