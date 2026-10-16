"""vrstudio CLI 骨架。

命令面按架构（E:\\VRAIGC\\docs\\architecture.md §3）声明；当前全部为 stub。
- ★新增命令（case / generate / serve）：M3/M4 实现。
- ←迁入命令（extract / merge / restore / pad / recompose / calib / verify /
  seam-center / plan）：M2 从旧仓库原样搬入（vrgen 侧命令不在本仓库）。

stub 的退出码固定为 2，调用方（web 队列/脚本）可以统一处理。
"""

import typer

from . import __version__

app = typer.Typer(
    help="360° 视频流程仓库：patch（局部重绘）/ fullredraw（全景重绘）。",
    no_args_is_help=True,
    invoke_without_command=True,   # 允许 `vrstudio --version` 这类不带子命令的调用
)

_NOT_IMPLEMENTED = "尚未实现（M2–M4 落地）。架构见 E:\\VRAIGC\\docs\\architecture.md。"


def _stub(name: str) -> None:
    typer.secho(f"vrstudio {name}: {_NOT_IMPLEMENTED}", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(2)


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", help="打印版本并退出。"),
) -> None:
    if version:
        typer.echo(f"vrstudio {__version__}")
        raise typer.Exit()


# ---------- ★ 新增命令（本仓库原生） ----------


@app.command("case")
def case_() -> None:
    """新建/列出/查看 case（写 case.toml，唯一真源）。"""
    _stub("case")


@app.command()
def generate() -> None:
    """组装输入包 → subprocess 调 vrgen → adopt 结果落 case。"""
    _stub("generate")


@app.command()
def serve() -> None:
    """启动 web 控制台（总入口）。"""
    _stub("serve")


# ---------- ← 迁入命令（旧仓库原样，M2 落地） ----------


@app.command()
def extract() -> None:
    """[patch] 视口抽帧 + clip.json 契约 + 掩膜。← vrpatch extract"""
    _stub("extract")


@app.command()
def merge() -> None:
    """[patch] 结果贴回全景（自动 restore + scalefit）。← vrpatch merge"""
    _stub("merge")


@app.command()
def restore() -> None:
    """[patch] AI clip 时间对齐/补帧（rife）。← vrpatch restore"""
    _stub("restore")


@app.command()
def pad() -> None:
    """[fullredraw] 循环冗余 pad 到目标比例 + mask + manifest。← vrpad pad"""
    _stub("pad")


@app.command()
def recompose() -> None:
    """[fullredraw] 配准 + 混合 + 还原源宽。← vrpad recompose"""
    _stub("recompose")


@app.command("seam-center")
def seam_center() -> None:
    """[fullredraw] 把接缝移到画面中央（诊断用）。← vrpad seam-center"""
    _stub("seam-center")


@app.command()
def calib() -> None:
    """[fullredraw] 标定生成工具的重缩放/构图漂移。← vrpad calib"""
    _stub("calib")


@app.command()
def verify() -> None:
    """[fullredraw] 前后对比接缝指标。← vrpad verify"""
    _stub("verify")


@app.command()
def plan() -> None:
    """[fullredraw] 只算 pad 几何并打印。← vrpad plan"""
    _stub("plan")
