"""``vrstudio generate`` —— 全系统唯一的接缝：组装提交清单 → subprocess 调 vrgen → adopt。

三件事（docs/architecture.md §3）：
1. 解析提交清单（flow 特有知识都在这里消化：fullredraw 读唯一的 ``*_pad.json``，
   patch 读 ``[extract] current`` 指向的 extracts/<ts>/）；
2. subprocess 调 vrgen CLI（stdout 原样流回，退出码透传——契约 0/1/2 两边一致）；
3. adopt：调 ``vrgen result`` 把结果拷进 case + 写一行 ``[adopt] round``。
这是 vrgen 文件进 case 的唯一时刻。

提交集只认 ``[submit] selected``（决策 33）：候选 = ``[[assets]]`` 文件素材 +
patch 的 clip / fullredraw 的 pad；mask 由这里自动追加，不进 selected。三个流程
共用同一段「按 selected 过滤 → 追加 mask → 写清单 → 调 vrgen」。

不做的事：不碰提示词内容（``--set`` 纯透传）、不读台账（问 vrgen）、
不当 web 的转发层。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, replace
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
                  help="组装提交清单 → 调 vrgen 生成/注册 → adopt 结果落 case")

#: 每个 flow 首轮 submit 用的模板 id（模板本体在 vrgen templates/）。
TEMPLATE_BY_FLOW = {"fullredraw": "360-closure", "patch": "character-repaint",
                    "gen": "gen-multi"}

#: 内部控制图：由流程产物提供，装配器自动追加，不进候选也不进 selected。
MASK_ID = {"patch": "mask", "fullredraw": "pad_mask"}

#: 不能进入提交清单的素材角色（工作台输入 / 内部控制图）。
NON_CANDIDATE_ROLES = ("mask", "first_frame_raw", "last_frame_raw")

_ROUND_IN_OUTPUT = re.compile(r"round=([A-Za-z0-9._-]+)")


def template_for(flow: str, mode: str | None) -> str:
    if flow == "gen" or (flow == "patch" and mode == "firstLastFrame"):
        return "gen-multi"
    return TEMPLATE_BY_FLOW[flow]


def clip_duration_s(case: case_mod.Case, root: str | Path | None = None) -> int | None:
    """patch 当前 clip 的时长（整秒，至少 4）：帧范围 ÷ clip.json 的帧率。

    firstLastFrame 没有输入视频，vrgen 的“自动”只能回退到 4 秒；patch 的时长
    其实由已冻结的 clip 决定，所以在 vrstudio 侧推导后显式传给 vrgen。
    """
    if case.flow != "patch" or not case.extract_current:
        return None
    path = case.dir(root) / "extracts" / case.extract_current / "clip.json"
    if not path.is_file():
        return None
    from ..patch.sidecar import load_sidecar
    try:
        sidecar = load_sidecar(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if len(sidecar.segments) != 1 or sidecar.fps <= 0:
        return None
    segment = sidecar.segments[0]
    frames = int(segment.frame_end) - int(segment.frame_start) + 1
    if frames <= 0:
        return None
    return max(4, round(frames / sidecar.fps))


def _image_size(path: Path) -> tuple[int, int]:
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise GenError(f"读不出图片尺寸：{path}")
    return int(image.shape[1]), int(image.shape[0])


def _size_of(path: Path, kind: str) -> tuple[int, int]:
    if kind == "image":
        return _image_size(path)
    info = probe(path)
    return info.width, info.height


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


# ── 候选与提交清单（三流程共用）──────────────────────────────────────────


@dataclass(frozen=True)
class Candidate:
    """一个可勾选的提交候选：文件素材或流程产物。"""

    id: str
    kind: str
    role: str
    path: Path
    display: str                 # 页面展示用的相对路径
    source: str                  # "asset" | "flow"
    sha256: str | None = None
    role_fixed: bool = False


@dataclass(frozen=True)
class Submission:
    """一次提交的全部输入：清单 + 自动附带的 mask + 几何 + 模板。"""

    refs: tuple[Candidate, ...]
    mask: Path | None
    mask_id: str
    template: str
    canvas_size: tuple[int, int]
    source_size: tuple[int, int]

    def payload(self) -> dict:
        items = [{"id": c.id, "kind": c.kind, "role": c.role,
                  "path": str(c.path.resolve()),
                  **({"sha256": c.sha256} if c.sha256 else {})}
                 for c in self.refs]
        if self.mask is not None:
            items.append({"id": self.mask_id, "kind": "image", "role": "mask",
                          "path": str(self.mask.resolve())})
        return {"assets": items}


def _manifest(case: case_mod.Case, root: str | Path | None) -> Manifest:
    manifests = sorted(case.dir(root).glob("*_pad.json"))
    if not manifests:
        raise GenError(
            f"{case.dir(root)} 里没有 *_pad.json——先跑 `vrstudio pad`，"
            f"manifest 是 recompose/generate 的几何唯一权威")
    if len(manifests) > 1:
        names = "\n".join(f"    {m.name}" for m in manifests)
        raise GenError(f"{len(manifests)} 份 manifest，指哪份？\n{names}\n"
                       f"一个 case 目录只该有一份（做对照实验请另开 case）")
    return Manifest.load(manifests[0])


def _flow_candidate(case: case_mod.Case,
                    root: str | Path | None) -> Candidate | None:
    """当前流程的母版候选：patch 的 clip、fullredraw 的 pad。"""
    if case.flow == "patch":
        if not case.extract_current:
            return None
        clip = case.dir(root) / "extracts" / case.extract_current / "clip.mp4"
        if not clip.is_file():
            return None
        return Candidate("clip", "video", "edit_master", clip,
                         f"extracts/{case.extract_current}/clip.mp4", "flow",
                         role_fixed=True)
    if case.flow == "fullredraw":
        manifests = sorted(case.dir(root).glob("*_pad.json"))
        if len(manifests) != 1:
            return None
        try:
            man = Manifest.load(manifests[0])
        except ValueError:
            return None
        if not man.padded:
            return None
        pad = manifests[0].parent / man.padded
        if not pad.is_file():
            return None
        return Candidate("pad", "video", "edit_master", pad, man.padded, "flow",
                         role_fixed=True)
    return None


def _flow_mask(case: case_mod.Case, root: str | Path | None) -> Path | None:
    if case.flow == "patch":
        if not case.extract_current:
            return None
        mask = case.dir(root) / "extracts" / case.extract_current / "clip_mask.png"
        return mask if mask.is_file() else None
    if case.flow == "fullredraw":
        manifests = sorted(case.dir(root).glob("*_pad.json"))
        if len(manifests) != 1:
            return None
        try:
            man = Manifest.load(manifests[0])
        except ValueError:
            return None
        if not man.mask.path:
            return None
        mask = manifests[0].parent / Path(man.mask.path).name
        return mask if mask.is_file() else None
    return None


def candidates(case: case_mod.Case,
               root: str | Path | None = None) -> list[Candidate]:
    """可勾选候选：流程母版在前，然后 ``[[assets]]``。"""
    out: list[Candidate] = []
    flow = _flow_candidate(case, root)
    if flow is not None:
        out.append(flow)
    try:
        for asset, path in case_mod.check_assets(case, root):
            if asset.role in NON_CANDIDATE_ROLES:
                continue
            out.append(Candidate(asset.id, asset.kind, asset.role, path,
                                 asset.path, "asset", asset.sha256))
    except CaseError:
        # 文件缺失不该让整个候选列表消失——卡片照样列出，页面标红。
        for asset in case.assets:
            if asset.role in NON_CANDIDATE_ROLES:
                continue
            out.append(Candidate(asset.id, asset.kind, asset.role,
                                 asset.resolved(case, root), asset.path,
                                 "asset", asset.sha256))
    return out


def _master(cands: list[Candidate]) -> Candidate:
    """母版回退链，与 vrgen.refs.master 同一条（两仓库不能互相 import）。"""
    videos = [c for c in cands if c.kind == "video"]
    for item in videos:
        if item.role == "edit_master":
            return item
    if videos:
        return videos[0]
    images = [c for c in cands if c.kind == "image"]
    for item in images:
        if item.role in ("space_ref", "scene_ref"):
            return item
    for item in images:
        if item.role == "first_frame":
            return item
    if images:
        return images[0]
    raise GenError("提交集里没有可用作母版的素材")


def _geometry(case: case_mod.Case, root: str | Path | None, refs: list[Candidate],
              mode: str | None) -> tuple[tuple[int, int], tuple[int, int]]:
    """canvas/source 的唯一来源：patch 取 clip.json，fullredraw 取 manifest，
    gen 取提交集里的母版。"""
    if case.flow == "patch":
        if not case.extract_current:
            raise GenError(
                f"case {case.name} 还没有抽取版本——先跑 `vrstudio extract case`，"
                f"generate 认 case.toml 的 [extract] current 指针")
        d = case.dir(root) / "extracts" / case.extract_current
        # clip 尺寸读冻结契约（clip.json 的 viewport），不探测视频：
        # 契约比 ffprobe 更权威，且不依赖外部二进制。
        from ..patch.sidecar import load_sidecar
        sidecar = load_sidecar(d / "clip.json")
        if len(sidecar.segments) != 1:
            raise GenError(f"{d / 'clip.json'}：patch 流契约是单段")
        vp = sidecar.segments[0].viewport
        size = (int(vp.width), int(vp.height))
        if mode == "firstLastFrame":
            _require_16_9(size, "patch viewport")
        return size, size
    if case.flow == "fullredraw":
        man = _manifest(case, root)
        if not man.padded:
            raise GenError("manifest 没记 padded 文件名——用当前版本重跑 `vrstudio pad`")
        plan = man.to_pad_plan()
        return plan.canvas_size, plan.source_size
    master = _master(refs)
    try:
        size = _size_of(master.path, master.kind)
    except (VideoIOError, OSError) as exc:
        raise GenError(str(exc)) from exc
    return size, size


def assemble(case: case_mod.Case, root: str | Path | None = None,
             mode: str | None = None) -> Submission:
    """按 ``[submit] selected`` 装配提交清单；mask 自动附带。"""
    if case.flow == "patch" and not case.extract_current:
        raise GenError(
            f"case {case.name} 还没有抽取版本——先跑 `vrstudio extract case`，"
            f"generate 认 case.toml 的 [extract] current 指针")
    if case.flow == "fullredraw":
        _manifest(case, root)                       # 缺 manifest 是多份/零份的明确报错
    cands = candidates(case, root)
    by_id = {c.id: c for c in cands}
    chosen_ids = case.submit_selected or []
    if not chosen_ids:
        raise GenError("没有勾选任何素材——在素材侧边栏勾选后再生成")
    unknown = [item for item in chosen_ids if item not in by_id]
    if unknown:
        raise GenError(f"提交集引用了不存在的候选：{unknown}；请重新勾选")
    chosen: list[Candidate] = []
    for item in chosen_ids:
        candidate = by_id[item]
        role = case.submit_role(item)
        chosen.append(replace(candidate, role=role)
                      if role and role != candidate.role else candidate)

    if mode == "firstLastFrame":
        firsts = [c for c in chosen if c.role == "first_frame"]
        lasts = [c for c in chosen if c.role == "last_frame"]
        if len(firsts) != 1 or len(lasts) != 1:
            missing = []
            if not firsts:
                missing.append("首帧定稿图（first_frame）")
            if not lasts:
                missing.append("尾帧定稿图（last_frame）")
            raise GenError(
                "firstLastFrame 需要勾选恰好一张首帧定稿图与一张尾帧定稿图；"
                + (f"还缺：{'、'.join(missing)}" if missing
                   else f"当前勾了 {len(firsts)} 张首帧、{len(lasts)} 张尾帧"))
        refs = [firsts[0], lasts[0]]
        mask = None
    else:
        bad = [c.id for c in chosen if c.role in NON_CANDIDATE_ROLES]
        if bad:
            raise GenError(
                f"勾选里含不能提交的素材：{bad}；"
                "mask 与首尾原始帧由流程或工作台提供，不参与提交")
        refs = chosen
        mask = _flow_mask(case, root)

    canvas, source = _geometry(case, root, refs, mode)
    return Submission(refs=tuple(refs), mask=mask,
                      mask_id=MASK_ID.get(case.flow, "mask"),
                      template=template_for(case.flow, mode),
                      canvas_size=canvas, source_size=source)


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
    """组装提交清单并提交生成（mglc 后端，M1 起可用；--dry-run 现在就能看 argv）。"""
    conf = load_config(overrides=overrides or [])
    case = _load_case(case_name, cases_dir)
    try:
        submission = assemble(case, cases_dir, mode=mode)
    except GenError as exc:
        _fail(exc)
    fd, raw = tempfile.mkstemp(prefix=f"vrgen-{case.name}-", suffix=".json")
    os.close(fd)
    refs_file = Path(raw)
    refs_file.write_text(json.dumps(submission.payload(), ensure_ascii=False, indent=1),
                         encoding="utf-8")
    argv = [_vrgen_exe(conf), "submit", "--job", case.name,
            "--refs", str(refs_file), "--template", submission.template,
            "--canvas", f"{submission.canvas_size[0]}x{submission.canvas_size[1]}",
            "--source", f"{submission.source_size[0]}x{submission.source_size[1]}"]
    for item in variables or []:
        argv += ["--set", item]
    if model_id is not None:
        argv += ["--model-id", str(model_id)]
    if mode is not None:
        argv += ["--mode", mode]
    if resolution is not None:
        argv += ["--resolution", resolution]
    if duration is None and mode == "firstLastFrame":
        # 首尾帧没有输入视频，vrgen 的自动值只有 4 秒；patch 用 clip 的实际
        # 帧范围与帧率推导，显式传下去，CLI 与网页走同一条规则。
        duration = clip_duration_s(case, cases_dir)
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
        typer.echo(f"  （素材清单保留在 {refs_file}，可直接复跑这条命令）")
        return
    if check:
        argv += ["--dry-run"]
    try:
        code = _stream(argv)
    finally:
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
