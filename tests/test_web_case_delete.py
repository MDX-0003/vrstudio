"""case 删除：清理 cases/ 与 vrgen_runs/，占用与任务闸门各一条。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio.web import app as web_app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(web_app, "_vrgen_exe", lambda: "definitely-not-a-real-vrgen")
    monkeypatch.setattr(web_app, "_workdir", lambda: tmp_path / "vrgen_runs")
    return TestClient(web_app.build_app(cases_root=tmp_path))


def _make_case(tmp_path, name: str = "d1") -> None:
    case_mod.create(name, "relight", root=tmp_path)
    run = tmp_path / "vrgen_runs" / name / "img-aaaa1111"
    run.mkdir(parents=True)
    (run / "run.json").write_text("{}", encoding="utf-8")


def test_delete_removes_case_and_runs(client, tmp_path):
    _make_case(tmp_path)
    r = client.delete("/api/case/d1")
    assert r.status_code == 200
    assert r.json()["deleted"] == "d1"
    assert not (tmp_path / "d1").exists()
    assert not (tmp_path / "vrgen_runs" / "d1").exists()


def test_delete_missing_case_is_404(client):
    assert client.delete("/api/case/nope").status_code == 404


def test_delete_refuses_case_locked_by_other_user(client, tmp_path):
    _make_case(tmp_path)
    client.cookies.set("vrstudio_uid", "me")
    (tmp_path / "d1" / ".web-lock.json").write_text(json.dumps({
        "owner_id": "someone-else",
        "owner_name": "别人",
        "acquired_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "heartbeat_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }), encoding="utf-8")

    r = client.delete("/api/case/d1")
    assert r.status_code == 409
    assert "别人" in r.json()["error"]
    assert (tmp_path / "d1").is_dir()


def test_delete_refuses_case_with_active_task(client, tmp_path, monkeypatch):
    _make_case(tmp_path)
    monkeypatch.setattr(web_app.QUEUE, "active_for", lambda case: [object()])

    r = client.delete("/api/case/d1")
    assert r.status_code == 409
    assert "任务" in r.json()["error"]
    assert (tmp_path / "d1").is_dir()


def test_delete_falls_back_to_trash_when_rmtree_fails(client, tmp_path,
                                                      monkeypatch):
    """Windows 句柄占用：第一次 rmtree 失败，改名后重试必须仍然删干净。"""
    _make_case(tmp_path)
    real = shutil.rmtree
    calls = {"n": 0}

    def flaky(path, *args, **kwargs):
        if calls["n"] == 0:
            calls["n"] += 1
            raise PermissionError("in use")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(case_mod.shutil, "rmtree", flaky)

    r = client.delete("/api/case/d1")
    assert r.status_code == 200
    assert not (tmp_path / "d1").exists()
    trash = tmp_path / ".trash"
    assert not trash.exists() or not any(trash.iterdir())
