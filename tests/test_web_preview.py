"""pick 预览图与 inner 解耦：红框只由页面 #innerBox 绘制。

服务端一旦把 inner 画进 PNG，同一画面上就会出现两个框；而且拖一次框要
重解一次 8K 源帧。这里钉住两件事：inner 不改变预览图字节，也不改变预览
标识（因此不会触发浏览器重新请求）。
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pytest

from vrstudio import case as case_mod
from vrstudio.web import render as render_mod
from vrstudio.web.render import PreviewStore

VIEWPORT = {"yaw_deg": 0.0, "pitch_deg": 0.0, "fov_h_deg": 90.0,
            "width": 320, "height": 180}
INNER = {"x": 80, "y": 40, "width": 160, "height": 100}


@pytest.fixture
def patch_case(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake erp bytes")
    case_mod.create("p1", "patch", src, root=tmp_path)
    case_mod.set_draft("p1", viewport=VIEWPORT, inner=INNER,
                       frames={"start": 0, "end": 0}, root=tmp_path)
    return "p1", tmp_path


def test_inner_does_not_change_preview_bytes_or_key(patch_case, erp, monkeypatch):
    name, root = patch_case
    # 预览只需要一帧 ERP；绕开真实视频解码，测试只关心几何与叠加。
    monkeypatch.setattr(render_mod, "_read_frame",
                        lambda video, index: erp.copy())
    store = PreviewStore(name, root)
    before = store.viewport_png(None)
    before_key = store.key

    store.set_inner(10, 10, 100, 50)

    assert store.key == before_key
    store._vp_bytes = None          # 强制重算，别让记忆化掩盖内嵌红框
    after = store.viewport_png(None)
    assert after == before
    image = cv2.imdecode(np.frombuffer(after, np.uint8), cv2.IMREAD_COLOR)
    # 合成 ERP 是灰度（B=G=R）；纯红只可能来自旧的内嵌框（BGR 0,0,255）。
    pure_red = (image[..., 0] == 0) & (image[..., 1] == 0) & (image[..., 2] == 255)
    assert not pure_red.any()


def test_preview_logs_timing(patch_case, erp, monkeypatch):
    name, root = patch_case
    monkeypatch.setattr(render_mod, "_read_frame",
                        lambda video, index: erp.copy())
    logger = logging.getLogger("vrstudio.web.preview")
    saved_handlers, saved_level = list(logger.handlers), logger.level
    saved_prop = logger.propagate
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger.handlers[:] = [Capture()]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        PreviewStore(name, root).viewport_png(None)
    finally:
        logger.handlers[:] = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_prop

    renders = [r.data for r in records if r.data.get("event") == "preview.render"]
    assert len(renders) == 1
    data = renders[0]
    assert data["case"] == name
    for key in ("decode_ms", "reproject_ms", "png_ms", "total_ms"):
        assert key in data and data[key] >= 0
