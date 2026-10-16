"""``vrstudio serve`` —— 启动 web 控制台（M4）。

web 层本体在 ``vrstudio.web``（app/tasks/render）；这里只做参数接线。
"""

from __future__ import annotations

from pathlib import Path

import typer


def serve(
    host: str = typer.Option("127.0.0.1", "--host",
                             help="监听地址；开放局域网用 0.0.0.0（决策 17）。"),
    port: int = typer.Option(8760, "--port"),
    cases_dir: Path = typer.Option(None, "--cases-dir"),
) -> None:
    """启动 web 控制台（总入口；case 列表 → 向导）。"""
    import uvicorn

    from ..web.app import build_app, sources_dir

    print(f"vrstudio serve: http://{host}:{port}/")
    print(f"  sources/: {sources_dir()}   cases/: {cases_dir or '仓库 cases/'}")
    uvicorn.run(build_app(cases_dir), host=host, port=port, log_level="warning")
