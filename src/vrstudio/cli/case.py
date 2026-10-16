"""``vrstudio case`` —— case 的建立与查看（刻意薄：不做编辑）。

case.toml 保持人可编辑（唯一真源在明面上）；改选区要么 web pick（M4）、
要么手改 toml。CLI 只管创建和查看。
"""

from __future__ import annotations

from pathlib import Path

import typer

from .. import case as case_mod
from ..case import CaseError

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="case 的建立与查看（case.toml 是唯一真源）")


def _fail(exc: CaseError) -> None:
    typer.secho(f"case: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(2) from exc


@app.command("new")
def new(
    name: str = typer.Argument(..., help="case 名（= cases/ 下的目录名 = vrgen 的 job 名）。"),
    flow: str = typer.Option(..., "--flow", help="patch | fullredraw"),
    source: Path = typer.Option(..., "--source", exists=True, dir_okay=False,
                                help="源视频。不要预先裁切或缩放。"),
    cases_dir: Path = typer.Option(None, "--cases-dir", help="覆盖 cases 根（测试用）。"),
) -> None:
    """新建 case：写最小 case.toml（patch 流顺带钉死源 sha256）。"""
    try:
        case = case_mod.create(name, flow, source, root=cases_dir)
    except CaseError as exc:
        _fail(exc)
    typer.echo(f"case     {case.dir(cases_dir)}")
    if case.flow == "patch":
        tycho = "下一步：web pick 选区（M4），或手填 case.toml 的 [viewport]/[inner]/[frames]"
    else:
        tycho = "下一步：vrstudio pad --in <源> --out <case>/seedance_input.mp4"
    typer.echo(tycho)


@app.command("list")
def list_(cases_dir: Path = typer.Option(None, "--cases-dir")) -> None:
    """列出全部 case（flow / adopt 状态）。"""
    root = case_mod.cases_root(cases_dir)
    if not root.is_dir():
        typer.echo(f"还没有任何 case（{root} 不存在）")
        return
    found = False
    for child in sorted(root.iterdir()):
        if (child / "case.toml").is_file():
            found = True
            try:
                case = case_mod.load(child.name, cases_dir)
                adopt = case.adopt_round or "-"
                typer.echo(f"  {case.name:<24} {case.flow:<12} adopt={adopt}")
            except CaseError as exc:
                typer.echo(f"  {child.name:<24} (读不了：{exc})")
    if not found:
        typer.echo(f"{root} 下没有 case")


@app.command("show")
def show(
    name: str = typer.Argument(...),
    cases_dir: Path = typer.Option(None, "--cases-dir"),
) -> None:
    """看一个 case 的全部静态事实。"""
    try:
        case = case_mod.load(name, cases_dir)
    except CaseError as exc:
        _fail(exc)
    typer.echo(f"case     {case.dir(cases_dir)}")
    typer.echo(f"flow     {case.flow}")
    typer.echo(f"source   {case.source_path}")
    if case.sha256:
        typer.echo(f"sha256   {case.sha256[:16]}…")
    if case.flow == "patch":
        for section, value in (("viewport", case.viewport), ("inner", case.inner),
                               ("frames", case.frames)):
            if value:
                items = "  ".join(f"{k}={v}" for k, v in value.items())
                typer.echo(f"{section:<8} {items}")
            else:
                typer.echo(f"{section:<8} (未定——web pick 或手填)")
        typer.echo(f"extract  {case.extract_current or '(未抽取)'}")
    typer.echo(f"adopt    {case.adopt_round or '(未采用)'}")
