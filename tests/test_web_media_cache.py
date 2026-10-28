"""素材元信息缓存与整包 payload 的一次性解码（2026-10-26 定稿卡顿排查）。

背景：一次 `_case_info` 里 `_asset_details` 被调三次，每次都把整批素材解码；
4K PNG 约 90ms/张，页面因此要 4 秒。这里钉住两条约束：
1. 同一份文件只解码一次，文件被替换（大小/mtime 变）后重新解码；
2. 一次 `_case_info` 只计算一次素材明细，`keyframe` 与 `gates` 共用同一份。
"""

from __future__ import annotations

import numpy as np
import pytest

import cv2

from vrstudio import case as case_mod
from vrstudio.web import app as web

VIEWPORT = {"yaw_deg": 10.0, "pitch_deg": -5.0, "fov_h_deg": 90.0,
            "width": 320, "height": 180}
INNER = {"x": 80, "y": 40, "width": 160, "height": 100}
FRAMES = {"start": 0, "end": 5}


def _write_png(path, width: int = 64, height: int = 36) -> None:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(buf.tobytes())


@pytest.fixture(autouse=True)
def _clear_media_cache():
    web._probe_media_meta.cache_clear()
    yield
    web._probe_media_meta.cache_clear()


def _counting_imread(monkeypatch):
    calls = {"n": 0}
    real = web._imread

    def counting(path, flags):
        calls["n"] += 1
        return real(path, flags)

    monkeypatch.setattr(web, "_imread", counting)
    return calls


def test_media_meta_decodes_a_file_once(tmp_path, monkeypatch):
    png = tmp_path / "sheet.png"
    _write_png(png)
    calls = _counting_imread(monkeypatch)

    first = web._media_meta(png, "image")
    second = web._media_meta(png, "image")

    assert first == {"width": 64, "height": 36, "duration": None, "fps": None}
    assert second == first
    assert calls["n"] == 1


def test_media_meta_returns_a_copy_and_revalidates_on_change(tmp_path, monkeypatch):
    png = tmp_path / "sheet.png"
    _write_png(png)
    calls = _counting_imread(monkeypatch)

    meta = web._media_meta(png, "image")
    meta["width"] = 9999
    assert web._media_meta(png, "image")["width"] == 64

    _write_png(png, 32, 18)
    refreshed = web._media_meta(png, "image")
    assert (refreshed["width"], refreshed["height"]) == (32, 18)
    assert calls["n"] == 2


def test_media_meta_missing_file_is_empty_and_uncached(tmp_path, monkeypatch):
    calls = _counting_imread(monkeypatch)
    missing = tmp_path / "nope.png"

    assert web._media_meta(missing, "image")["width"] is None
    assert calls["n"] == 0


def test_case_info_computes_asset_details_once(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake erp bytes")
    case_mod.create("p1", "patch", src, root=tmp_path)
    case_mod.set_draft("p1", viewport=VIEWPORT, inner=INNER, frames=FRAMES,
                       root=tmp_path)
    png = tmp_path / "sheet.png"
    _write_png(png)
    case_mod.add_assets("p1", [(png, "subject_ref")], tmp_path)

    monkeypatch.setattr(web, "_vrgen_exe", lambda: "definitely-not-a-real-vrgen")
    web.build_app(cases_root=tmp_path)

    calls = {"n": 0}
    real = web._asset_details

    def counting(case):
        calls["n"] += 1
        return real(case)

    monkeypatch.setattr(web, "_asset_details", counting)
    info = web._case_info("p1")

    assert info is not None
    assert calls["n"] == 1
    # 唯一一张 subject_ref 会自动绑定，keyframe 与 assets 看到的是同一份明细
    assert [ref["asset"] for ref in info["keyframe"]["refs"]] == ["i1"]
    assert [row["id"] for row in info["assets"]] == ["i1"]
    assert info["gates"]["keyframe"]["ready"] is False
