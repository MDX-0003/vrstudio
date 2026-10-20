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

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import typer

from .. import case as case_mod
from ..case import CaseError
from ..config import REPO_ROOT
from ..config import load as load_config
from ..media.videoio import VideoIOError, probe
from ..procenv import decode_text, utf8_env
from ..redraw.manifest import Manifest

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="组装输入包 → 调 vrgen 生成/注册 → adopt 结果落 case")

#: 每个 flow 首轮 submit 用的模板 id（模板本体在 vrgen templates/）。
TEMPLATE_BY_FLOW = {"fullredraw": "360-closure", "patch": "character-repaint",
                    "gen": "gen-multi"}

_ROUND_IN_OUTPUT = re.compile(r"round=([A-Za-z0-9._-]+)")


def template_for(flow: str, mode: str | None) -> str:
    if flow == "gen" or (flow == "patch" and mode == "firstLastFrame"):
        return "gen-multi"
    return TEMPLATE_BY_FLOW[flow]


def _image_size(path: Path) -> tuple[int, int]:
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise GenError(f"读不出图片尺寸：{path}")
    return int(image.shape[1]), int(image.shape[0])


def _require_16_9(size: tuple[int, int], what: str) -> None:
    width, height = int(size[0]), int(size[1])
    ratio = width / height if height else 0.0
    target = 16 / 9
    if abs(ratio - target) > target * 0.001:
        raise GenError(
            f"{what} 的宽高比 {width}:{height} 不是 16:9；"
            "firstLastFrame 首期只接受 16:9，请重新选区并 extract")


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
    return subprocess.run(argv, env=utf8_env()).returncode


def _capture(argv: list[str]) -> tuple[int, str]:
    """短操作（result）：捕获 stdout 以解析轮次 key。"""
    r = subprocess.run(argv, capture_output=True, env=utf8_env())
    return r.returncode, decode_text((r.stdout or b"") + (r.stderr or b""))


# ── 输入包解析（flow 特有知识全部在这里）─────────────────────────────────


def resolve_package(case: case_mod.Case, root: str | Path | None = None,
                    mode: str | None = None) -> tuple[Path | None, Path | None, str,
                                                      tuple[int, int], tuple[int, int]]:
    """→ (送生成的视频, mask, 模板 id, canvas 尺寸, source 尺寸)。

    fullredraw：manifest 读而不重推导——它记录的是 pad 当时真做了什么
    （此守卫连同理由一并继承自 vrpad 的 aigc/case.py）。canvas/source 是 vrgen
    提示词几何占位符的唯一来源（band = canvas_w - source_w）。
    patch：无冗余带概念，canvas=source=clip 实际尺寸（探测）。
    gen：母版是视频时直接上传；母版是图片时只取宽高，生成走 ``--refs``。
    ``mode=firstLastFrame`` 时首尾图都是文件名素材，视频位置返回 None。
    """
    template = template_for(case.flow, mode)
    if case.flow == "gen":
        if not case.assets:
            raise GenError(
                f"case {case.name} 还没有素材——先在生成页的「素材」步骤添加视频与图片")
        master = case.master()
        if master is None:
            raise GenError(f"case {case.name} 至少需要 1 个素材")
        try:
            case_mod.check_assets(case, root, selected=[master.id])
            path = master.resolved(case, root)
            if master.kind == "video":
                info = probe(path)
                canvas_size = source_size = (info.width, info.height)
                return path, None, template, canvas_size, source_size
            canvas_size = source_size = _image_size(path)
            return None, None, template, canvas_size, source_size
        except (CaseError, VideoIOError, OSError) as exc:
            raise GenError(str(exc)) from exc
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
        if mode == "firstLastFrame":
            _require_16_9(canvas_size, "patch viewport")
            return None, None, template, canvas_size, source_size
    if video is None:
        raise GenError(f"case {case.name} 没有送生成的视频")
    if not video.is_file():
        raise GenError(f"输入包的视频不存在：{video}")
    return video, mask, template, canvas_size, source_size


# ── 命令 ─────────────────────────────────────────────────────────────────


def _load_case(name: str, cases_dir) -> case_mod.Case:
    try:
        return case_mod.load(name, cases_dir)
    except CaseError as exc:
        raise GenError(str(exc)) from exc


def selected_assets(case: case_mod.Case, mode: str | None) -> list[case_mod.Asset]:
    """本次提交集：显式勾选优先，否则按模式取默认集。"""
    if case.submit_selected is not None:
        if not case.submit_selected:
            raise GenError("提交集为空——请在素材侧边栏至少勾选一项")
        chosen = set(case.submit_selected)
        return [asset for asset in case.assets if asset.id in chosen]
    if mode == "firstLastFrame":
        firsts = [a for a in case.assets if a.role == "first_frame"]
        lasts = [a for a in case.assets if a.role == "last_frame"]
        if len(firsts) == 1 and len(lasts) == 1:
            return [firsts[0], lasts[0]]
        if len(firsts) > 1 or len(lasts) > 1:
            raise GenError("有多组候选定稿帧，请在素材侧边栏勾选首帧与尾帧")
        raise GenError("先在工作台生成并挑选首帧与尾帧")
    if mode in ("videoEdit", "videoExtend"):
        picks = [a for a in case.assets
                 if (a.kind == "video" and a.role == "edit_master")
                 or (a.kind == "image" and a.role == "subject_ref")]
        return picks or list(case.assets)
    return list(case.assets)


@app.command()
def submit(
    case_name: str = typer.Option(..., "--case", help="case 名（= vrgen job 名）。"),
    variables: list[str] = typer.Option(None, "--var",
                                        help="提示词变量 k=v，透传给 vrgen submit --set。"),
    model_id: int = typer.Option(None, "--model-id",
                                 help="透传 vrgen submit --model-id（← mglc --model-id）。"),
    mode: str = typer.Option(None, "--mode",
                             help="透传 vrgen submit --mode（← mglc --type）。"),
    resolution: str = typer.Option(None, "--resolution",
                                   help="输出档位 480p|720p|1080p（← mglc --resolution）。"),
    duration: int = typer.Option(None, "--duration",
                                 help="整数秒（← mglc --duration；默认按输入自动）。"),
    num: int = typer.Option(None, "--num",
                            help="生成数量 1..4（← mglc --num；当前只取第一个结果）。"),
    aspect_ratio: str = typer.Option(None, "--aspect-ratio",
                                      help="输出比例（← mglc --aspect-ratio）。"),
    scale: str = typer.Option(None, "--scale",
                              help="上传缩放 auto|full|half（vrgen 自有，无 mglc 对应）。"),
    session_id: int = typer.Option(
        None, "--session-id",
        help="项目生成会话 ID（>0）：在该项目额度下提交；不传则用 generation.toml/配置。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只打印 argv，不真调 vrgen。"),
    check: bool = typer.Option(
        False, "--check",
        help="调 vrgen submit --dry-run：校验素材与提示词并打印完整命令，不提交不花钱。"),
    cases_dir: Path = typer.Option(None, "--cases-dir"),
    overrides: list[str] = typer.Option(None, "--set", help="config 覆盖。"),
) -> None:
    """组装输入包并提交生成（mglc 后端，M1 起可用；--dry-run 现在就能看 argv）。"""
    conf = load_config(overrides=overrides or [])
    case = _load_case(case_name, cases_dir)
    try:
        video, mask, template, canvas_size, source_size = resolve_package(
            case, cases_dir, mode=mode)
    except GenError as exc:
        _fail(exc)
    refs_needed = case.flow == "gen" or (
        case.flow == "patch" and mode == "firstLastFrame")
    refs_file: Path | None = None
    if refs_needed:
        try:
            selected = selected_assets(case, mode)
        except GenError as exc:
            _fail(exc)
        if not selected:
            _fail(GenError("没有可提交的素材"))
        try:
            checked = case_mod.check_assets(
                case, cases_dir, selected=[asset.id for asset in selected])
        except CaseError as exc:
            _fail(GenError(str(exc)))
        by_id = {asset.id: (asset, path) for asset, path in checked}
        payload = {"assets": [
            {"id": asset.id, "kind": asset.kind, "role": asset.role,
             "path": str(by_id[asset.id][1].resolve()), "sha256": asset.sha256}
            for asset in selected if asset.id in by_id
        ]}
        fd, raw = tempfile.mkstemp(prefix=f"vrgen-{case.name}-", suffix=".json")
        os.close(fd)
        refs_file = Path(raw)
        refs_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        argv = [_vrgen_exe(conf), "submit", "--job", case.name,
                "--refs", str(refs_file), "--template", template,
                "--canvas", f"{canvas_size[0]}x{canvas_size[1]}",
                "--source", f"{source_size[0]}x{source_size[1]}"]
    else:
        if video is None:
            _fail(GenError(f"case {case.name} 没有送生成的视频"))
        argv = [_vrgen_exe(conf), "submit", "--job", case.name,
                "--input", str(video), "--template", template,
                "--canvas", f"{canvas_size[0]}x{canvas_size[1]}",
                "--source", f"{source_size[0]}x{source_size[1]}"]
        if mask:
            argv += ["--mask", str(mask)]
    for item in variables or []:
        argv += ["--set", item]
    if model_id is not None:
        argv += ["--model-id", str(model_id)]
    if mode is not None:
        argv += ["--mode", mode]
    if resolution is not None:
        argv += ["--resolution", resolution]
    if duration is not None:
        argv += ["--duration", str(duration)]
    if num is not None:
        argv += ["--num", str(num)]
    if aspect_ratio is not None:
        argv += ["--aspect-ratio", aspect_ratio]
    # gen 未显式给比例时不再补 auto：generation.toml 是唯一权威，videoEdit 的
    # auto 由 vrgen 在无比例列表的模式里自行兜底。网页仍会把下拉框的值显式传下来。
    if scale is not None:
        argv += ["--scale", scale]
    if session_id is not None:
        argv += ["--session-id", str(session_id)]
    argv += ["--workdir", str(_workdir(conf))]
    if dry_run and check:
        _fail(GenError("--dry-run 与 --check 互斥"))
    if dry_run:
        typer.echo("dry-run：将发出")
        for a in argv:
            typer.echo(f"  {a}")
        if refs_file is not None:
            typer.echo(f"  （素材清单保留在 {refs_file}，可直接复跑这条命令）")
        return
    if check:
        argv += ["--dry-run"]
    try:
        code = _stream(argv)
    finally:
        if refs_file is not None:
            refs_file.unlink(missing_ok=True)
    raise typer.Exit(code)


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
    if case.flow in ("fullredraw", "gen"):
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
    m = _ROUND_IN_OUTPUT.search(output)
    if m:
        # vrgen 总会回显 round=<轮次名>；认它而不是调用方传入值——
        # 调用方可以给 latest / key 前缀 / 轮次目录路径，指针统一记轮次名
        resolved = m.group(1)
    elif round_key == "latest":
        _fail(GenError(f"vrgen result 没报轮次 key，无法写 adopt 指针：{output.strip()!r}"))
    case_mod.set_adopt(case.name, resolved, root=cases_dir)
    typer.echo(f"adopt    {resolved}")
    typer.echo(f"result   {out}")
    if case.flow == "fullredraw":
        manifests = sorted(case.dir(cases_dir).glob("*_pad.json"))
        manifest = manifests[0] if manifests else "<case>/seedance_input_pad.json"
        typer.echo("next     vrstudio recompose "
                   f"--in {out} --manifest {manifest} "
                   f"--out {case.dir(cases_dir) / 'out' / (resolved + '.mp4')}")
    elif case.flow == "gen":
        typer.echo("next     结果已经拷进 case/result.mp4，可以直接播放或交给外部工具")
    else:
        typer.echo("next     vrstudio merge --case " + case.name)
