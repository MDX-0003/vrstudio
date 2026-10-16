"""``vrstudio generate`` —— 全系统唯一的接缝：组装输入包 → subprocess 调 vrgen → adopt。

三件事（docs/architecture.md §3）：
1. 解析输入包（flow 特有知识都在这里消化：fullredraw 读唯一的 ``*_pad.json``，
   patch 读 ``[extract] current`` 指向的 extracts/<ts>/）；
2. subprocess 调 vrgen CLI（stdout 原样流回，退出码透传——契约 0/1/2 两边一致）；
3. adopt：调 ``vrgen result`` 把结果拷进 case + 写一行 ``[adopt] round``。
这是 vrgen 文件进 case 的唯一时刻。

不做的事：不碰提示词内容（``--set`` 纯透传）、不读台账（问 vrgen）、
不当 web 的转发层。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import typer

from .. import case as case_mod
from ..case import CaseError
from ..config import REPO_ROOT
from ..config import load as load_config
from ..redraw.manifest import Manifest

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="组装输入包 → 调 vrgen 生成/注册 → adopt 结果落 case")

#: 每个 flow 首轮 submit 用的模板 id（模板本体在 vrgen templates/）。
TEMPLATE_BY_FLOW = {"fullredraw": "360-closure", "patch": "character-repaint"}

_ROUND_IN_OUTPUT = re.compile(r"round=([A-Za-z0-9._-]+)")


class GenError(Exception):
    """参数/合约错 → 退出码 2。"""


def _fail(exc: Exception) -> None:
    typer.secho(f"generate: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(2) from exc


# ── vrgen 子进程 ─────────────────────────────────────────────────────────


def _vrgen_exe(conf) -> str:
    exe = str(conf.get("generate.vrgen", "vrgen"))
    if not exe.strip():
        raise GenError("generate.vrgen 不能为空（config [generate] 段）")
    return exe


def _workdir(conf) -> Path:
    raw = Path(str(conf.get("generate.workdir", "vrgen_runs")))
    return raw if raw.is_absolute() else REPO_ROOT / raw


def _stream(argv: list[str]) -> int:
    """长操作（submit/register）：输出原样流回终端与日志。"""
    return subprocess.run(argv).returncode


def _capture(argv: list[str]) -> tuple[int, str]:
    """短操作（result）：捕获 stdout 以解析轮次 key。"""
    r = subprocess.run(argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ── 输入包解析（flow 特有知识全部在这里）─────────────────────────────────


def resolve_package(case: case_mod.Case,
                    root: str | Path | None = None) -> tuple[Path, Path | None, str,
                                                             tuple[int, int], tuple[int, int]]:
    """→ (送生成的视频, mask, 模板 id, canvas 尺寸, source 尺寸)。

    fullredraw：manifest 读而不重推导——它记录的是 pad 当时真做了什么
    （此守卫连同理由一并继承自 vrpad 的 aigc/case.py）。canvas/source 是 vrgen
    提示词几何占位符的唯一来源（band = canvas_w - source_w）。
    patch：无冗余带概念，canvas=source=clip 实际尺寸（探测）。
    """
    if case.flow == "fullredraw":
        manifests = sorted(case.dir(root).glob("*_pad.json"))
        if not manifests:
            raise GenError(
                f"{case.dir(root)} 里没有 *_pad.json——先跑 `vrstudio pad`，"
                f"manifest 是 recompose/generate 的几何唯一权威")
        if len(manifests) > 1:
            names = "\n".join(f"    {m.name}" for m in manifests)
            raise GenError(f"{len(manifests)} 份 manifest，指哪份？\n{names}\n"
                           f"一个 case 目录只该有一份（做对照实验请另开 case）")
        man = Manifest.load(manifests[0])
        if not man.padded:
            raise GenError(f"{manifests[0].name} 没记 padded 文件名——"
                           f"用当前版本重跑 `vrstudio pad`")
        video = manifests[0].parent / man.padded
        mask = None
        if man.mask.path:
            candidate = manifests[0].parent / Path(man.mask.path).name
            mask = candidate if candidate.is_file() else None
        plan = man.to_pad_plan()
        canvas_size, source_size = plan.canvas_size, plan.source_size
    else:
        if not case.extract_current:
            raise GenError(
                f"case {case.name} 还没有抽取版本——先跑 `vrstudio extract case`，"
                f"generate 认 case.toml 的 [extract] current 指针")
        d = case.dir(root) / "extracts" / case.extract_current
        video = d / "clip.mp4"
        mask = d / "clip_mask.png"
        if not video.is_file():
            raise GenError(f"{video} 不存在——重新 extract")
        if not mask.is_file():
            mask = None
        # clip 尺寸读冻结契约（clip.json 的 viewport），不探测视频：
        # 契约比 ffprobe 更权威，且不依赖外部二进制。
        from ..patch.sidecar import load_sidecar
        sidecar = load_sidecar(d / "clip.json")
        if len(sidecar.segments) != 1:
            raise GenError(f"{d / 'clip.json'}：patch 流契约是单段")
        vp = sidecar.segments[0].viewport
        canvas_size = source_size = (int(vp.width), int(vp.height))
    if not video.is_file():
        raise GenError(f"输入包的视频不存在：{video}")
    return video, mask, TEMPLATE_BY_FLOW[case.flow], canvas_size, source_size


# ── 命令 ─────────────────────────────────────────────────────────────────


def _load_case(name: str, cases_dir) -> case_mod.Case:
    try:
        return case_mod.load(name, cases_dir)
    except CaseError as exc:
        raise GenError(str(exc)) from exc


@app.command()
def submit(
    case_name: str = typer.Option(..., "--case", help="case 名（= vrgen job 名）。"),
    variables: list[str] = typer.Option(None, "--var",
                                        help="提示词变量 k=v，透传给 vrgen submit --set。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只打印 argv，不真调 vrgen。"),
    cases_dir: Path = typer.Option(None, "--cases-dir"),
    overrides: list[str] = typer.Option(None, "--set", help="config 覆盖。"),
) -> None:
    """组装输入包并提交生成（mglc 后端，M1 起可用；--dry-run 现在就能看 argv）。"""
    conf = load_config(overrides=overrides or [])
    case = _load_case(case_name, cases_dir)
    try:
        video, mask, template, canvas_size, source_size = resolve_package(case, cases_dir)
    except GenError as exc:
        _fail(exc)
    argv = [_vrgen_exe(conf), "submit", "--job", case.name,
            "--input", str(video), "--template", template,
            "--canvas", f"{canvas_size[0]}x{canvas_size[1]}",
            "--source", f"{source_size[0]}x{source_size[1]}"]
    if mask:
        argv += ["--mask", str(mask)]
    for item in variables or []:
        argv += ["--set", item]
    argv += ["--workdir", str(_workdir(conf))]
    if dry_run:
        typer.echo("dry-run：将发出")
        for a in argv:
            typer.echo(f"  {a}")
        return
    raise typer.Exit(_stream(argv))


@app.command()
def register(
    case_name: str = typer.Option(..., "--case"),
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False,
                              help="外部工具产出的结果文件，注册为一轮。"),
    cases_dir: Path = typer.Option(None, "--cases-dir"),
    overrides: list[str] = typer.Option(None, "--set", help="config 覆盖。"),
) -> None:
    """手动后端：把本地文件经 vrgen 登记为本 case 的一轮结果（同构台账）。"""
    conf = load_config(overrides=overrides or [])
    case = _load_case(case_name, cases_dir)
    argv = [_vrgen_exe(conf), "register", "--job", case.name,
            "--file", str(file), "--workdir", str(_workdir(conf))]
    raise typer.Exit(_stream(argv))


@app.command()
def adopt(
    case_name: str = typer.Option(..., "--case"),
    round_key: str = typer.Option("latest", "--round",
                                  help="轮次 key；缺省取最新可用轮（gate != error）。"),
    cases_dir: Path = typer.Option(None, "--cases-dir"),
    overrides: list[str] = typer.Option(None, "--set", help="config 覆盖。"),
) -> None:
    """采用一轮结果：拷进 case + 写 [adopt] round + 打印下一步命令。"""
    conf = load_config(overrides=overrides or [])
    case = _load_case(case_name, cases_dir)
    if case.flow == "fullredraw":
        out = case.dir(cases_dir) / "result.mp4"
    else:
        if not case.extract_current:
            _fail(GenError("patch 流 adopt 前先 extract（[extract] current 未定）"))
        out = (case.dir(cases_dir) / "extracts" / case.extract_current / "result.mp4")
    argv = [_vrgen_exe(conf), "result", "--job", case.name,
            "--round", round_key, "--out", str(out), "--workdir", str(_workdir(conf))]
    code, output = _capture(argv)
    if code != 0:
        typer.secho(output.strip(), err=True)
        raise typer.Exit(code)
    resolved = round_key
    if round_key == "latest":
        m = _ROUND_IN_OUTPUT.search(output)
        if not m:
            _fail(GenError(f"vrgen result 没报轮次 key，无法写 adopt 指针：{output.strip()!r}"))
        resolved = m.group(1)
    case_mod.set_adopt(case.name, resolved, root=cases_dir)
    typer.echo(f"adopt    {resolved}")
    typer.echo(f"result   {out}")
    if case.flow == "fullredraw":
        manifests = sorted(case.dir(cases_dir).glob("*_pad.json"))
        manifest = manifests[0] if manifests else "<case>/seedance_input_pad.json"
        typer.echo("next     vrstudio recompose "
                   f"--in {out} --manifest {manifest} "
                   f"--out {case.dir(cases_dir) / 'out' / (resolved + '.mp4')}")
    else:
        d = case.dir(cases_dir) / "extracts" / (case.extract_current or "")
        typer.echo("next     vrstudio merge "
                   f"--input {case.source_path} --ai {d / 'result.mp4'} "
                   f"--sidecar {d / 'clip.json'}")
