"""Subcommand dispatch.

同一注册模式沿用 vrpad：每个命令是 ``vrstudio.cli.*`` 里的一个普通函数/子 app，
注册在根 app 上。单一实现原则——批处理脚本或 web 前端 import
``vrstudio.cli.pad.main`` 拿到的就是 CLI 跑的东西（web 队列以 subprocess 调 CLI，
共享同一执行路径）。

- ← vrpad 迁入（fullredraw）：pad / recompose / plan / verify / calib / seam-center
- ← vrpatch 迁入（patch）：extract（子 app，含 case|args 两命令）/ merge / restore
- ★ 新增 stub（M3/M4 落地）：case / generate / serve
"""

from __future__ import annotations

import typer

from .. import __version__
from . import calib as calib_cli
from . import case as case_cli
from . import extract as extract_cli
from . import generate as generate_cli
from . import merge as merge_cli
from . import pad as pad_cli
from . import plan as plan_cli
from . import recompose as recompose_cli
from . import restore as restore_cli
from . import seam_center as seam_center_cli
from . import serve as serve_cli
from . import verify as verify_cli

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    invoke_without_command=True,
    help="360° 视频流程仓库：patch（局部重绘）/ fullredraw（全景重绘）。",
)


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", help="打印版本并退出。"),
) -> None:
    if version:
        typer.echo(f"vrstudio {__version__}")
        raise typer.Exit()


# ── ← vrpad 迁入（fullredraw）──────────────────────────────────────────
app.command(name="pad", help=pad_cli.main.__doc__)(pad_cli.main)
app.command(name="recompose", help=recompose_cli.main.__doc__)(recompose_cli.main)
app.command(name="plan", help=plan_cli.main.__doc__)(plan_cli.main)
app.command(name="verify", help=verify_cli.main.__doc__)(verify_cli.main)
app.command(name="calib", help=calib_cli.main.__doc__)(calib_cli.main)
app.command(name="seam-center", help=seam_center_cli.main.__doc__)(seam_center_cli.main)

# ── ← vrpatch 迁入（patch）─────────────────────────────────────────────
# extract 是双子命令（case | args）的子 app，add_typer 挂载；
# merge / restore 是单命令，摊平注册保持 `vrstudio merge --input ...` 的形态。
app.add_typer(extract_cli.app, name="extract", help=extract_cli.app.info.help)
app.command(name="merge", help=merge_cli.merge.__doc__)(merge_cli.merge)
app.command(name="restore", help=restore_cli.restore.__doc__)(restore_cli.restore)

# ── ★ 新增（M3 落地 case/generate；M4 落地 serve）───────────────────────
app.add_typer(case_cli.app, name="case", help=case_cli.app.info.help)
app.add_typer(generate_cli.app, name="generate", help=generate_cli.app.info.help)
app.command(name="serve", help=serve_cli.serve.__doc__)(serve_cli.serve)


if __name__ == "__main__":
    app()
