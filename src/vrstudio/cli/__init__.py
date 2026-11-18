"""Subcommand dispatch.

同一注册模式沿用 vrpad：每个命令是 ``vrstudio.cli.*`` 里的一个普通函数/子 app，
注册在根 app 上。单一实现原则——批处理脚本或 web 前端 import
``vrstudio.cli.pad.main`` 拿到的就是 CLI 跑的东西（web 队列以 subprocess 调 CLI，
共享同一执行路径）。

- ← vrpad 迁入（fullredraw）：pad / recompose / plan / verify / calib / seam-center
- ← vrpatch 迁入（patch）：extract（子 app，含 case|args 两命令）/ merge / restore
- ★ 新增 stub（M3/M4 落地）：case / generate / serve
- ★ 新增（决策 48）：relight（把 AI 氛围图的光照搬到真 GT 上）
- ★ 合并（决策 60）：relight 按 GT 的媒体类型派发，图片出单帧、视频出整段；
  relight-video 退役
"""

from __future__ import annotations

import typer

from .. import __version__
from . import assemble as assemble_cli
from . import calib as calib_cli
from . import case as case_cli
from . import extract as extract_cli
from . import generate as generate_cli
from . import merge as merge_cli
from . import pad as pad_cli
from . import plan as plan_cli
from . import recompose as recompose_cli
from . import relight as relight_cli
from . import restore as restore_cli
from . import seam_center as seam_center_cli
from . import serve as serve_cli
from . import verify as verify_cli

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="360° 视频流程仓库：patch（局部重绘）/ fullredraw（全景重绘）。",
)


def _start_console(host: str, port: int, open_browser: bool) -> None:
    """不带子命令时直接启动控制台（双击 exe 的默认行为）。

    直接调用 ``serve`` 必须把每个参数都传齐：函数签名里的默认值是 ``typer.Option``
    对象，只有 Click 解析命令行时才会换成真实值。
    """
    from . import serve as serve_cli

    serve_cli.serve(
        host=host,
        port=port,
        cases_dir=None,
        log_dir=serve_cli.DEFAULT_LOG_DIR,
        log_level=serve_cli.DEFAULT_LOG_LEVEL,
        log_keep_days=serve_cli.DEFAULT_LOG_KEEP_DAYS,
        open_browser=open_browser,
    )


@app.callback()
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="打印版本并退出。"),
    host: str = typer.Option("127.0.0.1", "--host",
                             help="不带子命令启动控制台时的监听地址。"),
    port: int = typer.Option(8760, "--port",
                             help="不带子命令启动控制台时的端口。"),
    lan: bool = typer.Option(False, "--lan",
                             help="不带子命令启动时监听 0.0.0.0（局域网 host 模式）。"),
    open_browser: bool = typer.Option(True, "--open/--no-open",
                                      help="不带子命令启动时是否自动打开浏览器。"),
) -> None:
    if version:
        typer.echo(f"vrstudio {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _start_console("0.0.0.0" if lan else host, port, open_browser)


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
app.command(name="assemble", help=assemble_cli.assemble.__doc__)(assemble_cli.assemble)
app.command(name="restore", help=restore_cli.restore.__doc__)(restore_cli.restore)

# ── ★ 新增（M3 落地 case/generate；M4 落地 serve）───────────────────────
app.command(name="relight", help=relight_cli.relight.__doc__)(relight_cli.relight)
app.add_typer(case_cli.app, name="case", help=case_cli.app.info.help)
app.add_typer(generate_cli.app, name="generate", help=generate_cli.app.info.help)
app.command(name="serve", help=serve_cli.serve.__doc__)(serve_cli.serve)


if __name__ == "__main__":
    app()
