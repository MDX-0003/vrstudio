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


def _write_sidecar(tmp_path, case, *, skipped: bool, with_file: bool,
                   max_w: int = 4096):
    directory = tmp_path / case.name / "derived" / "pick"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "proxy.json").write_text(json.dumps({
        "version": PROXY_VERSION, "max_w": max_w,
        "source_sha256": case.sha256,
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
    assert posted.json()["width"] == 1920       # 直连时宽度就是源宽度
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


def _write_file_proxy_sidecar(tmp_path, case, out, *, skipped=False,
                              max_w: int = 4096, width: int = 1920) -> None:
    fp = proxy_mod.file_proxy(case, out, tmp_path)
    fp.video.parent.mkdir(parents=True, exist_ok=True)
    fp.video.write_bytes(b"proxy")
    st = out.stat()
    fp.sidecar.write_text(json.dumps({
        "version": PROXY_VERSION, "max_w": max_w, "width": width,
        "source": str(out.resolve()),
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


# ── web.proxy_max_w：所有 4K+ 源共用的代理宽度上限（2026-10-26）────────


def _big_info(src):
    return VideoInfo(path=str(src), size=(8192, 4096), fps="60/1",
                     fps_float=60.0, frames=590, pix_fmt="yuv420p",
                     codec="h264", has_audio=False, duration_s=9.8)


def test_proxy_max_w_default_and_validation():
    from vrstudio import config as config_mod
    from vrstudio.config import ConfigError

    assert config_mod.load().get("web.proxy_max_w") == 4096
    with pytest.raises(ConfigError):
        config_mod.load(overrides=["web.proxy_max_w=100"])
    with pytest.raises(ConfigError):
        config_mod.load(overrides=["web.proxy_max_w=99999"])
    with pytest.raises(ConfigError):
        config_mod.load(overrides=['web.proxy_max_w="wide"'])


def test_proxy_target_and_sidecar_follow_configured_max_w(tmp_path, monkeypatch):
    """max_w 是目标宽度上限：8K 源在 1024 配置下转出 1024 宽的代理，
    侧车记录 max_w，配置再变时据此判定重建。"""
    case, src = _mk_case(tmp_path)
    monkeypatch.setattr(proxy_mod, "proxy_max_w", lambda: 1024)
    monkeypatch.setattr(proxy_mod, "probe", lambda path: _big_info(src))
    targets = []

    def fake_encode(source, info, target, tmp, log_path):
        targets.append(target)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(b"proxy")

    monkeypatch.setattr(proxy_mod, "_encode_proxy", fake_encode)

    assert ensure_proxy(case, tmp_path) == "building"
    proxy_mod._builds[case.name].thread.join(5)

    assert targets == [(1024, 512)]
    meta = json.loads((tmp_path / "p1" / "derived" / "pick" / "proxy.json")
                      .read_text(encoding="utf-8"))
    assert meta["max_w"] == 1024
    assert proxy_state(case, tmp_path) == "ready"
    assert proxy_mod.proxy_width(case, tmp_path) is not None


def test_config_change_invalidates_existing_proxy(tmp_path, monkeypatch):
    """4096 建的代理在 max_w=2048 下不再是 ready（按新宽度重建）。"""
    case, _ = _mk_case(tmp_path)
    _write_sidecar(tmp_path, case, skipped=False, with_file=True, max_w=4096)
    assert proxy_state(case, tmp_path) == "ready"

    monkeypatch.setattr(proxy_mod, "proxy_max_w", lambda: 2048)
    assert proxy_state(case, tmp_path) == "missing"

    # 配置改回去，同一个代理又算就绪——重建判定只看 max_w 是否匹配。
    monkeypatch.setattr(proxy_mod, "proxy_max_w", lambda: 4096)
    assert proxy_state(case, tmp_path) == "ready"


def test_browser_safe_follows_configured_max_w(tmp_path, monkeypatch):
    """3840 宽的浏览器可播源：默认上限直连（disabled）；上限调到 2048 后
    不再直连，必须转出 2048 的代理。"""
    case, src = _mk_case(tmp_path)
    info = VideoInfo(path=str(src), size=(3840, 1920), fps="30/1",
                     fps_float=30.0, frames=294, pix_fmt="yuv420p",
                     codec="h264", has_audio=False, duration_s=9.8)
    monkeypatch.setattr(proxy_mod, "probe", lambda path: info)

    assert ensure_proxy(case, tmp_path) == "disabled"      # 3840 <= 4096

    monkeypatch.setattr(proxy_mod, "proxy_max_w", lambda: 2048)
    targets = []

    def fake_encode(source, info, target, tmp, log_path):
        targets.append(target)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(b"proxy")

    monkeypatch.setattr(proxy_mod, "_encode_proxy", fake_encode)
    # 旧的 skipped 侧车 max_w=4096，在新配置下不再有效 → 重新走构建。
    assert ensure_proxy(case, tmp_path) == "building"
    proxy_mod._builds[case.name].thread.join(5)
    assert targets == [(2048, 1024)]
    assert proxy_state(case, tmp_path) == "ready"


def test_file_proxy_state_rejects_stale_max_w(tmp_path, monkeypatch):
    """对比页右路的成片代理同样按 max_w 判定重建。"""
    case, _ = _mk_case(tmp_path)
    out = _mk_out(tmp_path)
    _write_file_proxy_sidecar(tmp_path, case, out, max_w=4096)
    assert proxy_mod.file_proxy_state(case, out, tmp_path) == "ready"
    assert proxy_mod.file_proxy_width(case, out, tmp_path) == 1920

    monkeypatch.setattr(proxy_mod, "proxy_max_w", lambda: 2048)
    assert proxy_mod.file_proxy_state(case, out, tmp_path) == "missing"
