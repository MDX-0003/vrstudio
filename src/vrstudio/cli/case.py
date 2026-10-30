"""``vrstudio case`` —— case 的建立与查看（刻意薄：不做编辑）。

case.toml 保持人可编辑（唯一真源在明面上）；改选区要么 web pick（M4）、
要么手改 toml。CLI 只管创建和查看；素材增删改排由 web 素材步骤调用
``vrstudio.case`` 的同一组函数。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer

from .. import case as case_mod
from .. import config as config_mod
from ..case import CaseError
from ..media.imageio import ImageError, read_image
from ..media.videoio import VideoIOError, probe
from ..procenv import decode_text, utf8_env

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="case 的建立与查看（case.toml 是唯一真源）")


def _fail(exc: CaseError) -> None:
    typer.secho(f"case: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(2) from exc


def _vrgen_exe(conf) -> str:
    exe = str(conf.get("generate.vrgen", "vrgen"))
    if not exe.strip():
        raise CaseError("generate.vrgen 不能为空（config [generate] 段）")
    return exe


def _workdir(conf) -> Path:
    raw = Path(str(conf.get("generate.workdir", "vrgen_runs")))
    return raw if raw.is_absolute() else config_mod.REPO_ROOT / raw


def save_mode(name: str, mode: str) -> None:
    """把 gen case 的模式写进 vrgen 的 generation.toml（唯一权威）。

    单独成函数是为了测试可以替换它；失败时抛 CaseError，case 本身保留。
    """
    conf = config_mod.load()
    argv = [_vrgen_exe(conf), "params", "save", "--job", name,
            "--mode", mode, "--workdir", str(_workdir(conf))]
    try:
        proc = subprocess.run(argv, capture_output=True, env=utf8_env())
    except OSError as exc:
        raise CaseError(f"模式没保存（找不到 vrgen）：{exc}") from exc
    if proc.returncode != 0:
        detail = decode_text((proc.stderr or b"") + (proc.stdout or b"")).strip() \
            or f"退出码 {proc.returncode}"
        raise CaseError(f"模式没保存：{detail}")


def seed_patch_draft(name: str, source: Path, cases_dir: Path | None,
                     *, flow: str = "patch") -> None:
    """建立后写默认草稿（与 web 抽屉共用 case 模型的默认值）。

    probe/读图失败不拦创建：只是没有默认值，web pick / 手改 toml 照常可用。
    单独成函数是为了测试可以替换它。
    """
    if flow == "patchimg":
        try:
            image = read_image(source)
        except ImageError:
            return
        case_mod.set_draft(name, root=cases_dir,
                           **case_mod.patchimg_default_draft(image.shape[1]))
        return
    try:
        info = probe(source)
    except VideoIOError:
        return
    case_mod.set_draft(name, root=cases_dir,
                       **case_mod.patch_default_draft(info.width, info.frames))


@app.command("new")
def new(
    name: str = typer.Argument(..., help="case 名（= cases/ 下的目录名 = vrgen 的 job 名）。"),
    flow: str = typer.Option(..., "--flow", help="patch | patchimg | fullredraw | gen"),
    source: Path = typer.Option(
        None, "--source", exists=True, dir_okay=False,
        help="源文件。patch/fullredraw 要视频；patchimg 要 2:1 ERP 图片；gen 可选。"),
    mode: str = typer.Option(None, "--mode",
                             help="gen 必填：videoEdit | allMediaRef | videoExtend。"),
    cases_dir: Path = typer.Option(None, "--cases-dir", help="覆盖 cases 根（测试用）。"),
) -> None:
    """新建 case：gen 写空素材列表并立即把模式写入 vrgen 草稿。"""
    if flow not in case_mod.FLOWS:
        _fail(CaseError(f"flow 必须是 {' 或 '.join(case_mod.FLOWS)}，got {flow!r}"))
    if flow == "gen":
        if mode not in case_mod.GEN_MODES:
            _fail(CaseError(
                f"flow=gen 必须给 --mode（{' | '.join(case_mod.GEN_MODES)}）"))
    elif mode is not None:
        _fail(CaseError(f"flow={flow} 的模式由流程固定，不接受 --mode"))
    if flow != "gen" and source is None:
        _fail(CaseError(f"flow={flow} 必须给 --source"))
    try:
        case = case_mod.create(name, flow, source, root=cases_dir)
        if flow in ("patch", "patchimg"):
            seed_patch_draft(case.name, source, cases_dir, flow=flow)
        if flow == "gen":
            save_mode(case.name, mode)
    except CaseError as exc:
        created = case_mod.case_toml(name, cases_dir)
        if created.is_file():
            typer.secho(f"case 已创建，但模式没保存：{case_mod.case_dir(name, cases_dir)}",
                        fg=typer.colors.YELLOW, err=True)
            typer.secho("可以重试保存，不需要重建 case。", err=True)
        _fail(exc)
    typer.echo(f"case     {case.dir(cases_dir)}")
    if flow == "gen":
        typer.echo(f"mode     {mode} → runs/{case.name}/generation.toml")
        typer.echo("下一步：web 生成页的「素材」步骤添加视频与图片，或手改 case.toml 的 [[assets]]")
    elif flow == "patch":
        typer.echo("下一步：web pick 选区（已写默认草稿），"
                   "或手改 case.toml 的 [viewport]/[inner]/[frames]")
    elif flow == "patchimg":
        typer.echo("下一步：vrstudio case draft --case " + case.name
                   + " --file <draft.json> 定视口与 inner，然后 vrstudio extract name --case "
                   + case.name)
    elif flow == "fullRedrawImg":
        typer.echo("下一步：vrstudio extract name --case " + case.name
                   + "（pad 成 21:9 + 冗余带 mask）")
    else:
        typer.echo("下一步：vrstudio pad --in <源> --out <case>/seedance_input.mp4")


@app.command("draft")
def draft(
    name: str = typer.Option(..., "--case", help="case 名"),
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False,
                              help='JSON：{"viewport": {...}, "inner": {...}}'),
    cases_dir: Path = typer.Option(None, "--cases-dir"),
) -> None:
    """CLI 写 pick 草稿（与 web pick 共用 case_mod.set_draft）。"""
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("顶层必须是对象")
        case = case_mod.set_draft(name, viewport=raw.get("viewport"),
                                  inner=raw.get("inner"), frames=raw.get("frames"),
                                  root=cases_dir)
    except CaseError as exc:
        _fail(exc)
    except (OSError, ValueError) as exc:
        _fail(CaseError(str(exc)))
    parts = [f"viewport={'✓' if case.viewport else '—'}",
             f"inner={'✓' if case.inner else '—'}"]
    if case.flow == "patch":
        parts.append(f"frames={'✓' if case.frames else '—'}")
    typer.echo(f"draft    {case.name}  " + "  ".join(parts))


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
                extra = f" assets={len(case.assets)}" if case.flow == "gen" else ""
                typer.echo(f"  {case.name:<24} {case.flow:<12} adopt={adopt}{extra}")
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
    if case.flow == "gen":
        typer.echo(f"assets   {len(case.assets)} 条")
        for asset in case.assets:
            typer.echo(f"  {asset.id:<4} {asset.kind:<5} {asset.role:<12} {asset.path}")
    else:
        typer.echo(f"source   {case.source_path}")
        if case.sha256:
            typer.echo(f"sha256   {case.sha256[:16]}…")
    if case.flow in ("patch", "patchimg"):
        sections = [("viewport", case.viewport), ("inner", case.inner)]
        if case.flow == "patch":
            sections.append(("frames", case.frames))
        for section, value in sections:
            if value:
                items = "  ".join(f"{k}={v}" for k, v in value.items())
                typer.echo(f"{section:<8} {items}")
            else:
                typer.echo(f"{section:<8} (未定——web pick 或手填)")
    if case.flow in ("patch", "patchimg", "fullRedrawImg"):
        typer.echo(f"extract  {case.extract_current or '(未抽取)'}")
    adopt = case.adopt_round or "(未采用)"
    if case.adopt_index is not None:
        adopt += f"  index={case.adopt_index}"
    typer.echo(f"adopt    {adopt}")
