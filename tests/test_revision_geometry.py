"""几何端点不参与 case revision。

几何写的是 derived/pick/draft.toml，高频且最后写入者胜出：无论有没有
X-Case-Revision，都直接放行，不推进 case-meta revision，也不因旧头 409。
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio.web import app as web_app
from vrstudio.web import pick_draft
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


def _draft_inner(tmp_path) -> dict:
    case = case_mod.load("p1", tmp_path)
    return pick_draft.load_draft(case, tmp_path).inner


def test_geometry_without_header_passes_and_skips_touch(client, tmp_path):
    r = client.post("/api/case/p1/viewport", json={"fx": 0.75, "fy": 0.25})
    assert r.status_code == 200
    assert "x-case-revision" not in r.headers
    assert _revision(tmp_path) == 0


def test_geometry_with_header_is_ignored(client, tmp_path):
    r = client.post("/api/case/p1/inner",
                    json={"fx": 0.25, "fy": 0.5, "fw": 0.5, "fh": 0.25},
                    headers={"X-Case-Revision": "0"})
    assert r.status_code == 200
    assert "x-case-revision" not in r.headers
    assert _revision(tmp_path) == 0


def test_geometry_stale_header_is_ignored(client, tmp_path):
    r = client.post("/api/case/p1/inner",
                    json={"fx": 0.25, "fy": 0.5, "fw": 0.5, "fh": 0.25},
                    headers={"X-Case-Revision": "5"})
    assert r.status_code == 200
    assert _revision(tmp_path) == 0
    inner = _draft_inner(tmp_path)
    assert (inner["x"], inner["y"]) == (80, 90)
    assert (inner["width"], inner["height"]) == (160, 45)


def test_geometry_bad_header_is_ignored(client):
    r = client.post("/api/case/p1/nudge", json={"dyaw": 1.0, "dpitch": 0.0},
                    headers={"X-Case-Revision": "abc"})
    assert r.status_code == 200
    assert "x-case-revision" not in r.headers
