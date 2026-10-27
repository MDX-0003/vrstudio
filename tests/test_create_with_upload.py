"""新建 case 的 upload_id 分支：上传会话直接作为源视频。

用真实分片端点构造 complete 会话，覆盖主链路与四类错误。
sources_dir 在测试里指向 tmp_path，避免污染仓库 sources/。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vrstudio.web import app as web_app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(web_app, "_vrgen_exe", lambda: "definitely-not-a-real-vrgen")
    monkeypatch.setattr(web_app, "sources_dir", lambda: tmp_path / "sources")
    return TestClient(web_app.build_app(cases_root=tmp_path))


def _upload_video(client: TestClient, tmp_path: Path, name: str, payload: bytes) -> str:
    """走真实分片端点完成一次上传，返回 file_id。"""
    video = tmp_path / name
    video.write_bytes(payload)
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


def test_create_case_from_upload_lands_in_sources(client, tmp_path):
    """假字节过不了 cv2 探测（400），但文件已入库、会话已清理。"""
    upload_id = _upload_video(client, tmp_path, "clip.mp4", b"not-a-real-video")
    r = _create(client, upload_id=upload_id)
    assert r.status_code == 400
    assert "打不开这个视频" in r.json()["error"]
    assert (tmp_path / "sources" / "clip.mp4").is_file()
    complete = tmp_path / ".uploads" / "complete"
    assert not complete.exists() or not list(complete.glob("*"))


def test_create_case_rejects_incomplete_upload(client):
    session = client.post("/api/uploads", json={"name": "clip.mp4", "size": 100}).json()
    r = _create(client, upload_id=session["upload_id"])
    assert r.status_code == 400
    assert "还没有完成" in r.json()["error"]


def test_create_case_rejects_duplicate_source_name(client, tmp_path):
    (tmp_path / "sources").mkdir(exist_ok=True)
    (tmp_path / "sources" / "clip.mp4").write_bytes(b"existing")
    upload_id = _upload_video(client, tmp_path, "clip.mp4", b"new-bytes")
    r = _create(client, upload_id=upload_id)
    assert r.status_code == 409
    assert "同名" in r.json()["error"]


def test_create_case_rejects_non_video_ext(client, tmp_path):
    upload_id = _upload_video(client, tmp_path, "pic.png", b"png-bytes")
    r = _create(client, upload_id=upload_id)
    assert r.status_code == 400
    assert "源视频必须" in r.json()["error"]


def test_create_case_rejects_upload_and_video_together(client, tmp_path):
    upload_id = _upload_video(client, tmp_path, "clip.mp4", b"bytes")
    r = _create(client, upload_id=upload_id, video="clip.mp4")
    assert r.status_code == 400
    assert "二选一" in r.json()["error"]
