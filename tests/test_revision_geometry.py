"""几何端点的落定帧 revision 校验。

拖动帧（不带 X-Case-Revision）照旧放行且不推进 revision；
落定帧带头：一致则落盘并推进，不一致 409 不写盘。
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio.web import app as web_app
from vrstudio.web.case_meta import FileCaseMetaStore

VIEWPORT = {"yaw_deg": 10.0, "pitch_deg": -5.0, "fov_h_deg": 90.0,
            "width": 320, "height": 180}
INNER = {"x": 80, "y": 40, "width": 160, "height": 100}
FRAMES = {"start": 0, "end": 5}


@pytest.fixture
def client(tmp_path) -> TestClient:
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake erp bytes")
    case_mod.create("p1", "patch", src, root=tmp_path)
    case_mod.set_draft("p1", viewport=VIEWPORT, inner=INNER, frames=FRAMES,
                       root=tmp_path)
    return TestClient(web_app.build_app(cases_root=tmp_path))


def _revision(tmp_path) -> int:
    return FileCaseMetaStore(tmp_path).read("p1").revision


def test_geometry_without_header_passes_and_skips_touch(client, tmp_path):
    r = client.post("/api/case/p1/viewport", json={"fx": 0.75, "fy": 0.25})
    assert r.status_code == 200
    assert "x-case-revision" not in r.headers
    assert _revision(tmp_path) == 0  # 拖动帧不推进 revision


def test_geometry_settle_with_fresh_header_passes_and_touches(client, tmp_path):
    r = client.post("/api/case/p1/inner",
                    json={"fx": 0.25, "fy": 0.5, "fw": 0.5, "fh": 0.25},
                    headers={"X-Case-Revision": "0"})
    assert r.status_code == 200
    assert r.headers["x-case-revision"] == "1"
    assert _revision(tmp_path) == 1


def test_geometry_settle_with_stale_header_conflicts(client, tmp_path):
    stale = client.post("/api/case/p1/inner",
                        json={"fx": 0.25, "fy": 0.5, "fw": 0.5, "fh": 0.25},
                        headers={"X-Case-Revision": "5"})
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"
    inner = case_mod.load("p1", tmp_path).inner
    assert (inner["x"], inner["y"]) == (80, 40)  # 冲突不写盘


def test_geometry_bad_header_is_400(client):
    r = client.post("/api/case/p1/nudge", json={"dyaw": 1.0, "dpitch": 0.0},
                    headers={"X-Case-Revision": "abc"})
    assert r.status_code == 400
