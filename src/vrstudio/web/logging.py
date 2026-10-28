"""web 控制台的结构化日志。

服务日志与前端错误日志分成两个 JSONL 文件，按本地日期切换：

- ``logs/serve/service-YYYYMMDD.jsonl``：启动、HTTP 请求、预览耗时、几何变更、
  任务生命周期。
- ``logs/serve/client-YYYYMMDD.jsonl``：浏览器通过 ``/api/client-log`` 上报的
  脚本错误与媒体解码错误。

两个 logger 的传播关系在 :func:`setup_logging` 里固定，前端错误不会同时写进
服务文件。当前 ``serve`` 只有一个 uvicorn worker；未来开多 worker 时要换成
队列式处理器，或每个进程一个文件，不能让两个进程同时写同一个文件。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

from ..config import REPO_ROOT

ACCESS_LOGGER = "vrstudio.web.access"
PREVIEW_LOGGER = "vrstudio.web.preview"
TASK_LOGGER = "vrstudio.web.task"
GEOMETRY_LOGGER = "vrstudio.web.geometry"
CLIENT_LOGGER = "vrstudio.web.client"
PROXY_LOGGER = "vrstudio.web.proxy"

# /api/task 是前端每 2 秒一次的轮询；/static 与 favicon 是静态资源；客户端错误
# 请求只写 client 文件，不再重复进服务访问日志。
_SKIP_PATHS = {"/api/task", "/api/client-log", "/favicon.ico"}
_SKIP_PREFIXES = ("/static/",)
# 视频是 Range 流，查看/拖动会拆成很多请求；默认只在 DEBUG 记录。
_DEBUG_PREFIXES = ("/video/",)


class JsonFormatter(logging.Formatter):
    """一行一个 JSON 对象；业务字段放在 ``extra={"data": {...}}`` 里。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
        }
        data = getattr(record, "data", None)
        if isinstance(data, dict):
            payload.update({key: value for key, value in data.items()
                            if key not in ("ts", "level", "logger")})
        else:
            payload["message"] = record.getMessage()
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class DailyJsonlHandler(logging.Handler):
    """按本地日期写 ``<stem>-YYYYMMDD.jsonl``，换日时切换文件并清理旧文件。"""

    def __init__(self, directory: str | Path, stem: str, keep_days: int = 14):
        super().__init__()
        self.directory = Path(directory)
        self.stem = stem
        self.keep_days = max(1, int(keep_days))
        self._day: str | None = None
        self._fh = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = time.strftime("%Y%m%d")
            if day != self._day:
                self._close()
                self._open(day)
                self._cleanup(day)
            assert self._fh is not None
            self._fh.write(self.format(record) + "\n")
            self._fh.flush()
        except Exception:             # 日志失败不能影响业务
            self.handleError(record)

    def _open(self, day: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.directory / f"{self.stem}-{day}.jsonl",
                        "a", encoding="utf-8", newline="\n")
        self._day = day

    def _close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def _cleanup(self, day: str) -> None:
        cutoff = time.time() - self.keep_days * 86400
        current = f"{self.stem}-{day}.jsonl"
        for path in self.directory.glob(f"{self.stem}-*.jsonl"):
            if path.name == current:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    def close(self) -> None:
        self._close()
        super().close()


class RequestLogMiddleware:
    """记录 HTTP 请求的请求 id、状态、耗时和字节数。

    纯 ASGI 实现，不缓冲响应体；``/api/task`` 轮询、静态资源和客户端错误入口
    直接跳过，视频流默认只记到 DEBUG。
    """

    def __init__(self, app):
        self.app = app
        self.log = logging.getLogger(ACCESS_LOGGER)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in _SKIP_PATHS or path.startswith(_SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        rid = uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["rid"] = rid
        query = scope.get("query_string", b"").decode("utf-8", "replace")
        target = path + (("?" + query) if query else "")
        started = time.perf_counter()
        status = 0
        size = 0

        async def send_wrapper(message):
            nonlocal status, size
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append(
                    (b"x-request-id", rid.encode("ascii")))
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # 带方法与路径记一条：这是控制台上唯一的一条异常记录，uvicorn 那条
            # 同内容记录被 AsgiExceptionDuplicateFilter 丢掉。
            self.log.exception(
                f"request.error {scope.get('method', '')} {target[:300]}",
                extra={"data": {
                    "event": "request.error", "rid": rid,
                    "method": scope.get("method", ""), "path": target[:1000],
                }})
            raise

        ms = (time.perf_counter() - started) * 1000
        slow = ms >= 500
        if status >= 500:
            level = logging.ERROR
        elif status >= 400 or slow:
            level = logging.WARNING
        else:
            level = logging.INFO
        if path.startswith(_DEBUG_PREFIXES):
            level = logging.DEBUG
        user = scope.get("state", {}).get("user")
        case = ""
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "case":
            case = parts[2]
        self.log.log(level, "request", extra={"data": {
            "event": "request", "rid": rid, "request_id": rid,
            "method": scope.get("method", ""),
            "path": target[:1000], "status": status,
            "ms": round(ms, 1), "duration_ms": round(ms, 1),
            "bytes": size, "slow": slow,
            "user_id": getattr(user, "id", None), "case": case,
            "error": None if status < 400 else f"HTTP {status}",
        }})


def _replace_handlers(logger: logging.Logger, handlers: list[logging.Handler],
                      level: int, propagate: bool) -> None:
    for old in logger.handlers:
        if isinstance(old, DailyJsonlHandler):
            old.close()
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


_ASGI_DUPLICATE = "Exception in ASGI application"


class AsgiExceptionDuplicateFilter(logging.Filter):
    """丢掉 uvicorn 记的同一条 ASGI 异常：请求中间件已经带 rid 与路径记过一遍。

    只按这一句固定文案过滤，uvicorn 的其他异常（启动失败、lifespan 报错）照常
    保留；万一 uvicorn 换了文案，最坏结果是重复的那条回来，不会吞掉真实错误。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn 这条消息带尾随换行（h11_impl 里的 "Exception in ASGI application\n"）
        return record.getMessage().strip() != _ASGI_DUPLICATE


def setup_logging(log_dir: str | Path = "logs/serve",
                  level: str = "INFO", keep_days: int = 14) -> Path:
    """配置服务与前端错误两套 JSONL 日志，返回日志目录。

    文件默认写在仓库 ``logs/serve/``；``uvicorn.access`` 关闭，访问行统一由
    :class:`RequestLogMiddleware` 产出，避免和 uvicorn 自带格式重复。
    """
    directory = Path(log_dir)
    if not directory.is_absolute():
        directory = REPO_ROOT / directory

    service = DailyJsonlHandler(directory, "service", keep_days)
    service.setFormatter(JsonFormatter())
    client = DailyJsonlHandler(directory, "client", keep_days)
    client.setFormatter(JsonFormatter())

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    console.setLevel(logging.WARNING)

    numeric = getattr(logging, str(level).upper(), None)
    if not isinstance(numeric, int):
        numeric = logging.INFO

    service_root = logging.getLogger("vrstudio")
    _replace_handlers(service_root, [service, console], numeric, propagate=False)

    client_logger = logging.getLogger(CLIENT_LOGGER)
    _replace_handlers(client_logger, [client, console], numeric, propagate=False)

    uvicorn_logger = logging.getLogger("uvicorn")
    _replace_handlers(uvicorn_logger, [service, console], numeric, propagate=False)
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.handlers[:] = []
    uvicorn_error.filters[:] = [AsgiExceptionDuplicateFilter()]
    uvicorn_error.propagate = True
    uvicorn_error.setLevel(numeric)
    logging.getLogger("uvicorn.access").disabled = True
    return directory
