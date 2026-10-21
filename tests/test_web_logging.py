"""web 日志：JSONL 文件、请求过滤、前端错误独立文件。"""

from __future__ import annotations

import json
import logging

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from vrstudio.web import app as web_app
from vrstudio.web.logging import (
    CLIENT_LOGGER,
    DailyJsonlHandler,
    JsonFormatter,
    RequestLogMiddleware,
    setup_logging,
)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _restore_logger(name: str, saved) -> None:
    logger = logging.getLogger(name)
    handlers, level, propagate, disabled = saved
    for handler in logger.handlers:
        if isinstance(handler, DailyJsonlHandler):
            handler.close()
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate
    logger.disabled = disabled


def _save_logger(name: str):
    logger = logging.getLogger(name)
    return (list(logger.handlers), logger.level, logger.propagate, logger.disabled)


def test_daily_handler_writes_jsonl(tmp_path):
    handler = DailyJsonlHandler(tmp_path, "service", keep_days=1)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.vrstudio.jsonl")
    saved = _save_logger(logger.name)
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("hello", extra={"data": {"event": "x", "n": 1}})
    finally:
        _restore_logger(logger.name, saved)

    files = list(tmp_path.glob("service-*.jsonl"))
    assert files
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["event"] == "x" and record["n"] == 1
    assert record["level"] == "INFO" and "ts" in record


def test_request_log_skips_polling():
    access = logging.getLogger("vrstudio.web.access")
    saved = _save_logger(access.name)
    cap = Capture()
    access.handlers[:] = [cap]
    access.setLevel(logging.DEBUG)
    access.propagate = False
    try:
        app = Starlette(routes=[
            Route("/ok", lambda request: PlainTextResponse("ok")),
            Route("/api/task", lambda request: PlainTextResponse("poll")),
        ])
        app.add_middleware(RequestLogMiddleware)
        client = TestClient(app)
        response = client.get("/ok")
        assert response.headers["x-request-id"]
        client.get("/api/task")
    finally:
        _restore_logger(access.name, saved)

    assert len(cap.records) == 1
    data = cap.records[0].data
    assert data["path"] == "/ok" and data["status"] == 200
    assert data["rid"]


def test_client_log_endpoint_writes_client_logger(tmp_path):
    service = logging.getLogger("vrstudio")
    client = logging.getLogger(CLIENT_LOGGER)
    saved_service = _save_logger(service.name)
    saved_client = _save_logger(client.name)
    service_cap, client_cap = Capture(), Capture()
    service.handlers[:] = [service_cap]
    service.setLevel(logging.INFO)
    service.propagate = False
    client.handlers[:] = [client_cap]
    client.setLevel(logging.INFO)
    client.propagate = False
    try:
        app = web_app.build_app(cases_root=tmp_path)
        response = TestClient(app).post("/api/client-log", json={
            "level": "warn", "message": "video decode failed",
            "case": "dacne", "context": "erpVid", "code": 4,
        })
        assert response.status_code == 204
    finally:
        _restore_logger(service.name, saved_service)
        _restore_logger(client.name, saved_client)

    assert not service_cap.records          # 客户端错误不混进服务日志
    assert len(client_cap.records) == 1
    data = client_cap.records[0].data
    assert data["event"] == "client.error"
    assert data["message"] == "video decode failed"
    assert data["case"] == "dacne" and data["code"] == 4
    assert data["cid"]


def test_setup_logging_attaches_separate_files(tmp_path):
    names = ["vrstudio", CLIENT_LOGGER, "uvicorn", "uvicorn.error", "uvicorn.access"]
    saved = {name: _save_logger(name) for name in names}
    try:
        directory = setup_logging(tmp_path, "INFO", keep_days=1)
        assert directory == tmp_path
        service = logging.getLogger("vrstudio").handlers[0]
        client = logging.getLogger(CLIENT_LOGGER).handlers[0]
        assert isinstance(service, DailyJsonlHandler) and service.stem == "service"
        assert isinstance(client, DailyJsonlHandler) and client.stem == "client"
        assert logging.getLogger(CLIENT_LOGGER).propagate is False
        assert logging.getLogger("uvicorn.access").disabled is True
    finally:
        for name in names:
            _restore_logger(name, saved[name])
