"""case 负责人归属与「归属到我的名下」。

覆盖：set_owner 落盘、revision 冲突不写盘、touch 的显示名自我刷新、
端点级归属与 409。身份走 cookie（与 identity.py 的免登录设计一致）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio.web import app as web_app
from vrstudio.web.case_meta import CaseMeta, FileCaseMetaStore

UID_A, NAME_A = "uid-aaa", "alice"
UID_B, NAME_B = "uid-bbb", "bob"


@pytest.fixture
def client(tmp_path) -> TestClient:
    case_mod.create("p1", "patch", _fake_src(tmp_path), root=tmp_path)
    return TestClient(web_app.build_app(cases_root=tmp_path))


def _fake_src(tmp_path: Path) -> Path:
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake erp bytes")
    return src


def _as(client: TestClient, uid: str, name: str) -> None:
    client.cookies.set("vrstudio_uid", uid)
    client.cookies.set("vrstudio_name", name)


def _meta(tmp_path: Path) -> CaseMeta:
    return FileCaseMetaStore(tmp_path).read("p1")


def test_set_owner_assigns_and_keeps_created_at(tmp_path):
    store = FileCaseMetaStore(tmp_path)

    class U:
        id = UID_A
        display_name = NAME_A

    store.set_owner("p1", U())
    meta = store.read("p1")
    assert meta.owner_id == UID_A and meta.owner_name == NAME_A
    assert meta.created_at and meta.updated_by_id == UID_A
    # revision 的推进是 revision 中间件的职责，store 层不动它。
    assert meta.revision == 0


def test_touch_refreshes_owner_name_when_owner_is_self(tmp_path):
    store = FileCaseMetaStore(tmp_path)

    class A:
        id = UID_A
        display_name = NAME_A

    class ARenamed:
        id = UID_A
        display_name = "甲改名了"

    store.set_owner("p1", A())
    store.touch("p1", ARenamed())
    meta = store.read("p1")
    assert meta.owner_id == UID_A
    assert meta.owner_name == "甲改名了"


def test_touch_keeps_owner_when_other_user_writes(tmp_path):
    store = FileCaseMetaStore(tmp_path)

    class A:
        id = UID_A
        display_name = NAME_A

    class B:
        id = UID_B
        display_name = NAME_B

    store.set_owner("p1", A())
    store.touch("p1", B())
    meta = store.read("p1")
    assert meta.owner_id == UID_A and meta.owner_name == NAME_A
    assert meta.updated_by_id == UID_B


def test_owner_endpoint_assigns_to_current_user(client, tmp_path):
    _as(client, UID_B, NAME_B)
    r = client.post("/api/case/p1/owner", json={})
    assert r.status_code == 200
    meta = r.json()["meta"]
    assert meta["owner_id"] == UID_B and meta["owner_name"] == NAME_B
    # 响应体是 touch 前的快照；推进结果看响应头与落盘。
    assert meta["revision"] == 0
    assert r.headers["x-case-revision"] == "1"
    assert _meta(tmp_path).revision == 1


def test_owner_endpoint_conflict_returns_current(client, tmp_path):
    _as(client, UID_A, NAME_A)
    first = client.post("/api/case/p1/owner", json={"revision": 0})
    assert first.status_code == 200

    _as(client, UID_B, NAME_B)
    stale = client.post("/api/case/p1/owner", json={"revision": 0})
    assert stale.status_code == 409
    body = stale.json()
    assert body["code"] == "revision_conflict"
    assert body["current_revision"] == 1
    # 冲突时归属不变。
    assert _meta(tmp_path).owner_id == UID_A


def test_owner_endpoint_without_revision_still_writes(client, tmp_path):
    """老前端不传 revision：照旧写入，只是没有冲突保护（与保存端点同约定）。"""
    _as(client, UID_A, NAME_A)
    client.get("/api/case/p1")  # ensure 建档
    r = client.post("/api/case/p1/owner", json={"revision": 0})
    assert r.status_code == 200
    assert _meta(tmp_path).owner_id == UID_A


def test_owner_endpoint_missing_case(client):
    _as(client, UID_A, NAME_A)
    r = client.post("/api/case/nope/owner", json={})
    assert r.status_code == 404
    assert json.loads(json.dumps(r.json()))["error"] == "no such case"
