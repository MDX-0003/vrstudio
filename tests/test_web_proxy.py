"""预览代理：状态侧车、源片选择、浏览器可播源跳过生成、API 路由。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio.media.videoio import VideoInfo
from vrstudio.web import app as web_app
from vrstudio.web import proxy as proxy_mod
from vrstudio.web.proxy import (
    PROXY_VERSION,
    ensure_proxy,
    proxy_disabled,
    proxy_path,
    proxy_state,
)
from vrstudio.web.render import PreviewStore

VIEWPORT = {"yaw_deg": 0.0, "pitch_deg": 0.0, "fov_h_deg": 90.0,
            "width": 320, "height": 180}
INNER = {"x": 80, "y": 40, "width": 160, "height": 100}


@pytest.fixture(autouse=True)
def _clear_proxy_builds():
    proxy_mod._builds.clear()
    yield
    proxy_mod._builds.clear()


def _mk_case(tmp_path, name="p1"):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake erp bytes")
    case_mod.create(name, "patch", src, root=tmp_path)
    case_mod.set_draft(name, viewport=VIEWPORT, inner=INNER,
                       frames={"start": 0, "end": 0}, root=tmp_path)
    return case_mod.load(name, tmp_path), src


def _write_sidecar(tmp_path, case, *, skipped: bool, with_file: bool):
    directory = tmp_path / case.name / "derived" / "pick"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "proxy.json").write_text(json.dumps({
        "version": PROXY_VERSION, "source_sha256": case.sha256,
        "skipped": skipped,
    }), encoding="utf-8")
    if with_file:
        (directory / "proxy.mp4").write_bytes(b"proxy")


def _safe_info(src):
    return VideoInfo(path=str(src), size=(1920, 960), fps="60/1",
                     fps_float=60.0, frames=10, pix_fmt="yuv420p",
                     codec="h264", has_audio=False, duration_s=10.0)


def test_proxy_state_lifecycle(tmp_path):
    case, _ = _mk_case(tmp_path)
    assert proxy_state(case, tmp_path) == "missing"

    _write_sidecar(tmp_path, case, skipped=True, with_file=False)
    assert proxy_disabled(case, tmp_path)
    assert proxy_state(case, tmp_path) == "disabled"

    _write_sidecar(tmp_path, case, skipped=False, with_file=False)
    assert proxy_state(case, tmp_path) == "missing"       # 侧车在但文件不在

    _write_sidecar(tmp_path, case, skipped=False, with_file=True)
    assert proxy_state(case, tmp_path) == "ready"
    assert proxy_path(case, tmp_path).is_file()


def test_preview_store_switches_to_proxy(tmp_path):
    case, src = _mk_case(tmp_path)
    plain = PreviewStore(case.name, tmp_path)
    assert plain.source_kind == "source"
    assert plain._video() == str(src)
    plain_key = plain.key

    _write_sidecar(tmp_path, case, skipped=False, with_file=True)
    proxied = PreviewStore(case.name, tmp_path)
    assert proxied.source_kind == "proxy"
    assert proxied._video().endswith("proxy.mp4")
    assert proxied.key != plain_key


def test_ensure_proxy_skips_browser_safe_source(tmp_path, monkeypatch):
    case, src = _mk_case(tmp_path)
    monkeypatch.setattr(proxy_mod, "probe", lambda path: _safe_info(src))

    assert ensure_proxy(case, tmp_path) == "disabled"
    assert proxy_disabled(case, tmp_path)
    assert proxy_state(case, tmp_path) == "disabled"
    assert not proxy_path(case, tmp_path).exists()


def test_proxy_api_and_serving(tmp_path, monkeypatch):
    case, src = _mk_case(tmp_path)
    monkeypatch.setattr(proxy_mod, "probe", lambda path: _safe_info(src))
    web_app._stores.clear()
    client = TestClient(web_app.build_app(cases_root=tmp_path))

    assert client.get("/api/case/p1/proxy").json()["state"] == "missing"
    posted = client.post("/api/case/p1/proxy")
    assert posted.status_code == 200
    assert posted.json()["state"] == "disabled"
    assert client.get("/video/proxy/p1").status_code == 404

    _write_sidecar(tmp_path, case, skipped=False, with_file=True)
    assert client.get("/api/case/p1/proxy").json()["state"] == "ready"
    served = client.get("/video/proxy/p1")
    assert served.status_code == 200
    assert served.headers["content-type"] == "video/mp4"


# ── 任意成片的 4K 预览代理（对比页右路）────────────────────────────────


def _mk_out(tmp_path, name="v1_space.mp4", data=b"out") -> Path:
    out = tmp_path / "p1" / "out" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def _write_file_proxy_sidecar(tmp_path, case, out, *, skipped=False) -> None:
    fp = proxy_mod.file_proxy(case, out, tmp_path)
    fp.video.parent.mkdir(parents=True, exist_ok=True)
    fp.video.write_bytes(b"proxy")
    st = out.stat()
    fp.sidecar.write_text(json.dumps({
        "version": PROXY_VERSION, "source": str(out.resolve()),
        "size": st.st_size, "mtime_ns": st.st_mtime_ns, "skipped": skipped,
    }), encoding="utf-8")


def test_file_proxy_state_tracks_the_source_file(tmp_path):
    case, _ = _mk_case(tmp_path)
    out = _mk_out(tmp_path)
    assert proxy_mod.file_proxy_state(case, out, tmp_path) == "missing"

    _write_file_proxy_sidecar(tmp_path, case, out)
    assert proxy_mod.file_proxy_state(case, out, tmp_path) == "ready"

    # 合并覆盖同名成片 → 字节数与 mtime 变了，旧代理不再算就绪。
    out.write_bytes(b"new output bytes")
    assert proxy_mod.file_proxy_state(case, out, tmp_path) == "missing"


def test_file_proxy_skips_browser_safe_file(tmp_path, monkeypatch):
    case, _ = _mk_case(tmp_path)
    out = _mk_out(tmp_path)
    monkeypatch.setattr(proxy_mod, "probe", lambda path: _safe_info(out))

    assert proxy_mod.ensure_file_proxy(case, out, tmp_path) == "disabled"
    assert proxy_mod.file_proxy_state(case, out, tmp_path) == "disabled"
    assert not proxy_mod.file_proxy(case, out, tmp_path).video.exists()


def test_out_proxy_endpoint_serves_only_a_ready_proxy(tmp_path):
    case, _ = _mk_case(tmp_path)
    out = _mk_out(tmp_path)
    web_app._stores.clear()
    client = TestClient(web_app.build_app(cases_root=tmp_path))

    assert client.get("/video/outproxy/p1/v1_space.mp4").status_code == 404
    assert client.get("/api/case/p1/proxy",
                      params={"file": "v1_space.mp4"}).json()["state"] == "missing"

    _write_file_proxy_sidecar(tmp_path, case, out)
    assert client.get("/video/outproxy/p1/v1_space.mp4").status_code == 200
    state = client.get("/api/case/p1/proxy", params={"file": "v1_space.mp4"}).json()
    assert state["state"] == "ready"
    assert state["url"] == "/video/outproxy/p1/v1_space.mp4"

    assert client.post("/api/case/p1/proxy",
                       json={"file": "nope.mp4"}).status_code == 404
    assert client.post("/api/case/p1/proxy",
                       json={"file": "../case.toml"}).status_code == 400


def test_source_proxy_api_is_open_to_fullredraw(tmp_path):
    src = tmp_path / "f.mp4"
    src.write_bytes(b"fake")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    client = TestClient(web_app.build_app(cases_root=tmp_path))
    r = client.get("/api/case/f1/proxy")
    assert r.status_code == 200
    assert r.json()["state"] in ("missing", "building", "failed")
