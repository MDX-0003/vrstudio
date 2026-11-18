"""``vrstudio serve`` —— 启动 web 控制台（M4）。

web 层本体在 ``vrstudio.web``（app/tasks/render）；这里只做参数接线。
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

#: 默认值集中在这里：``_start_console`` 直接调用 ``serve`` 时必须把每个参数都传齐，
#: 否则形参拿到的是 ``typer.Option`` 对象而不是值。
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8760
DEFAULT_LOG_DIR = Path("logs/serve")
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_KEEP_DAYS = 14


def _port_busy(host: str, port: int) -> bool:
    """端口是否已被别的进程监听。

    Windows 允许在 ``0.0.0.0`` 已被占用时再绑 ``127.0.0.1`` 的同一端口，只靠 bind 探测
    会漏掉“另一个实例正在跑”的情况，所以优先用 psutil 看监听表，拿不到时再退回 bind。
    """
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            for conn in psutil.net_connections(kind="tcp"):
                if (conn.status == psutil.CONN_LISTEN and conn.laddr
                        and conn.laddr.port == port):
                    return True
            return False
        except (psutil.Error, OSError):
            pass
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host if host not in ("0.0.0.0", "::") else "0.0.0.0", port))
        except OSError:
            return True
    return False


def serve(
    host: str = typer.Option(DEFAULT_HOST, "--host",
                             help="监听地址；开放局域网用 0.0.0.0（决策 17）。"),
    port: int = typer.Option(DEFAULT_PORT, "--port"),
    cases_dir: Path = typer.Option(None, "--cases-dir"),
    log_dir: Path = typer.Option(DEFAULT_LOG_DIR, "--log-dir",
                                 help="结构化日志目录（默认仓库 logs/serve）。"),
    log_level: str = typer.Option(DEFAULT_LOG_LEVEL, "--log-level",
                                  help="DEBUG / INFO / WARNING / ERROR。"),
    log_keep_days: int = typer.Option(DEFAULT_LOG_KEEP_DAYS, "--log-keep-days",
                                      help="按天 JSONL 日志保留天数。"),
    open_browser: bool = typer.Option(False, "--open/--no-open",
                                      help="启动后自动打开浏览器（双击启动时默认打开）。"),
) -> None:
    """启动 web 控制台（总入口；case 列表 → 向导）。"""
    import uvicorn

    from .. import config as config_mod
    from ..config import ConfigError
    from ..web.app import build_app, sources_dir
    from ..web.logging import setup_logging
    from ..web.portguard import reclaim_port
    from ..web.tasks import QUEUE

    log_path = setup_logging(log_dir, log_level, log_keep_days)
    # 配置在这里加载并定格：起不来就带着原因退出，不要带着半新半旧的配置接请求。
    try:
        config_mod.activate()
    except ConfigError as exc:
        logging.getLogger("vrstudio.serve").error(
            "config.error", extra={"data": {"event": "config.error", "error": str(exc)}})
        typer.echo(f"配置有问题（{exc}），serve 没有启动。", err=True)
        raise typer.Exit(2) from exc
    logging.getLogger("vrstudio.serve").info("serve.start", extra={"data": {
        "event": "serve.start", "host": host, "port": port,
        "cases_dir": str(cases_dir or "仓库 cases/"),
        "log_dir": str(log_path),
    }})
    uvicorn_level = str(log_level).lower()
    if uvicorn_level not in ("critical", "error", "warning", "info", "debug", "trace"):
        uvicorn_level = "info"
    # 先接管本项目自己的旧实例，再判断端口是否仍被别人占着；占用就直说，别先打一堆横幅。
    reclaim_port(port)
    if _port_busy(host, port):
        typer.echo(
            f"端口 {port} 已被占用，控制台没有启动。"
            f"换一个端口重试（例如 --port {port + 1}），或先结束占用该端口的程序。",
            err=True)
        raise typer.Exit(1)
    print(f"vrstudio serve: http://{host}:{port}/")
    print(f"  sources/: {sources_dir()}   cases/: {cases_dir or '仓库 cases/'}")
    print(f"  logs/: {log_path}  (service-*.jsonl / client-*.jsonl)")
    if open_browser:
        # 监听 0.0.0.0 时浏览器要打 127.0.0.1；延迟一点等端口就绪，避免打开空页。
        target = f"http://127.0.0.1:{port}/" if host in ("0.0.0.0", "::") else f"http://{host}:{port}/"
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(target)).start()
        print(f"  浏览器将打开 {target}")
    # 队列历史在内存里，重启就空；日志索引落盘，启动时回填最近若干条。
    QUEUE.restore_history()
    uvicorn.run(build_app(cases_dir), host=host, port=port,
                log_level=uvicorn_level, log_config=None, access_log=False)
