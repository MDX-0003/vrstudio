"""裁回的强制出片：web 只把页面开关翻成 CLI 的 ``--force``，入口靠 ``error_kind``。"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from vrstudio import case as case_mod
from vrstudio.web import app as web_app
from vrstudio.web.tasks import Task, error_kind

#: recompose 几何闸门的报错原文（任务视图里的 error 就是这条的末行）。
GEOMETRY_ERROR = (
    "ERROR geometry premise failed: the two redundant bands no longer correspond, so the "
    "regions are probably being sliced in the wrong place (the AIGC tool likely cropped "
    "or reframed the canvas). Refusing to render a geometrically wrong panorama. Run "
    "`vrstudio calib`, re-check pad.target_aspect, or pass --force to inspect the result "
    "anyway."
)


@pytest.fixture(autouse=True)
def _clear_preview_stores():
    web_app._stores.clear()
    yield
    web_app._stores.clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 只用来初始化 _ctx["cases_root"]；这些用例不发任务、不起子进程。
    monkeypatch.setattr(web_app, "_vrgen_exe", lambda: "definitely-not-a-real-vrgen")
    return TestClient(web_app.build_app(cases_root=tmp_path))


def _fullredraw_case(tmp_path) -> None:
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    case_mod.create("f1", "fullredraw", src, root=tmp_path)
    cdir = case_mod.case_dir("f1", tmp_path)
    (cdir / "seedance_input_pad.json").write_text("{}", encoding="utf-8")
    (cdir / "result.mp4").write_bytes(b"fake result")


def test_recompose_op_passes_force(tmp_path, client):
    _fullredraw_case(tmp_path)
    argv = web_app._op_argv("f1", "recompose", {"force": True})
    assert "--force" in argv


def test_recompose_op_omits_force_by_default(tmp_path, client):
    _fullredraw_case(tmp_path)
    assert "--force" not in web_app._op_argv("f1", "recompose", {})


def test_recompose_op_keeps_follow_input_next_to_force(tmp_path, client):
    _fullredraw_case(tmp_path)
    argv = web_app._op_argv("f1", "recompose", {"output_mode": "input", "force": True})
    assert "--follow-input" in argv
    assert "--force" in argv


def test_error_kind_recognises_only_the_canvas_gate():
    assert error_kind(GEOMETRY_ERROR) == "geometry"
    assert error_kind("ERROR 打不开文件：no such file") is None
    assert error_kind("") is None
    assert error_kind(None) is None


def test_task_view_reports_the_error_kind():
    task = Task("recompose", "f1", [])
    task.error = GEOMETRY_ERROR
    assert task.view()["error_kind"] == "geometry"
    task.error = "ERROR 打不开文件：no such file"
    assert task.view()["error_kind"] is None
