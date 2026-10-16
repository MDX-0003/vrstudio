"""M4 落地前的占位命令。

退出码固定 2（与"参数或配置错"语义一致），调用方（web 队列/脚本）可统一处理。
命令契约见 E:\\VRAIGC\\docs\\architecture.md §3 / case-model.md。
"""

from __future__ import annotations

import typer

_NOT_IMPLEMENTED = "尚未实现（M4 落地）。架构见 E:\\VRAIGC\\docs\\architecture.md。"


def _stub(name: str) -> None:
    typer.secho(f"vrstudio {name}: {_NOT_IMPLEMENTED}", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(2)


def serve() -> None:
    """启动 web 控制台（总入口）。"""
    _stub("serve")
