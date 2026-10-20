"""vrstudio web 控制台后端（← vrpatch web/app.py 骨架，全面接统一 case 模型）。

单人本地优先（决策 17：多用户分层设计已记录，队列/锁的扩展点留好未启用）。
页面流（architecture.md §3）：case 列表 → 向导（软向导，步骤可自由查看，
"下一步"由服务端判据置灰——决策 12/13/14/15/16 的载体）。

web 层的铁律：它只是编排层——读 case 用 ``vrstudio.case``（唯一权威），
跑长任务用队列 subprocess 调 CLI（与控制台同一条执行路径），
问 vrgen 用子进程（跨仓库只走 CLI 契约，AGENTS.md 硬规则 1）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import jinja2
import numpy as np
from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from .. import case as case_mod
from .. import config as config_mod
from ..case import CaseError
from ..cli.generate import (
    GenError,
    resolve_package,
    selected_assets,
    template_for,
)
from ..media.frames import extract_frame
from ..media.videoio import VideoIOError, probe
from ..procenv import decode_text, utf8_env
from ..redraw.manifest import Manifest
from .render import PreviewStore
from .tasks import QUEUE, vrstudio_argv

_WEB_DIR = Path(__file__).parent
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

_env = jinja2.Environment(loader=jinja2.FileSystemLoader(_WEB_DIR / "templates"),
                          autoescape=True)

#: 单人本地用；serve --cases-dir / 未来多用户在此换全局上下文
_ctx = {"cases_root": None}


def _root() -> Path:
    return case_mod.cases_root(_ctx["cases_root"])


def sources_dir() -> Path:
    d = config_mod.REPO_ROOT / "sources"
    d.mkdir(exist_ok=True)
    return d


# ── 预览 store 缓存（case.toml mtime 变更即失效）────────────────────────

_stores: dict[str, tuple[float, PreviewStore]] = {}


def get_store(name: str) -> PreviewStore | None:
    try:
        store = PreviewStore(name, _ctx["cases_root"])
    except CaseError:
        return None
    mtime = case_mod.case_toml(name, _ctx["cases_root"]).stat().st_mtime
    hit = _stores.get(name)
    if hit and hit[0] == mtime and hit[1].case.viewport == store.case.viewport:
        return hit[1]
    _stores[name] = (mtime, store)
    return store


# ── 页面 ─────────────────────────────────────────────────────────────────


async def index(request):
    return HTMLResponse(_env.get_template("cases.html").render())


async def wizard(request):
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": f"没有这个 case：{name}"}, status_code=404)
    return HTMLResponse(_env.get_template("wizard.html").render(
        name=case.name, flow=case.flow))


# ── 媒体：源片 / 成片 / 预览图 ──────────────────────────────────────────


async def video_source(request):
    try:
        case = case_mod.load(request.path_params["name"], _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    p = Path(case.source_path)
    if not p.is_file():
        return JSONResponse({"error": "source video missing"}, status_code=404)
    return FileResponse(str(p), media_type="video/mp4")


async def video_out(request):
    name = request.path_params["name"]
    fn = request.path_params["file"]
    if "/" in fn or "\\" in fn or ".." in fn:
        return JSONResponse({"error": "bad name"}, status_code=400)
    p = case_mod.case_dir(name, _ctx["cases_root"]) / "out" / fn
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(p), media_type="video/mp4")


async def img(request):
    name = request.path_params["name"]
    fn = request.path_params["file"]
    store = get_store(name)
    if store is None or fn != "vp.png" or "/" in fn or "\\" in fn:
        return JSONResponse({"error": "not found"}, status_code=404)
    raw = request.query_params.get("frame")
    frame = None
    if raw is not None:
        try:
            frame = int(raw)
        except ValueError:
            return JSONResponse({"error": "frame must be an integer"},
                                status_code=400)
    try:
        # 8K 解码+重投影不能冻住事件循环：nudge/轮询会排在其后
        data = await asyncio.to_thread(store.viewport_png, frame)
    except (ValueError, RuntimeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return Response(content=data, media_type="image/png")


# ── case 信息（向导的数据源 + 门控判据）─────────────────────────────────


def _vrgen_exe() -> str:
    return str(config_mod.load().get("generate.vrgen", "vrgen"))


def _workdir() -> Path:
    conf = config_mod.load()
    raw = Path(str(conf.get("generate.workdir", "vrgen_runs")))
    return raw if raw.is_absolute() else config_mod.REPO_ROOT / raw


_ROUND_LINE = re.compile(
    r"^\s+(?P<mark>[*x])\s+(?P<round>\S+)\s+(?P<backend>\S+)\s+(?P<stage>\S+)\s+"
    r"gate=(?P<gate>\S+)\s+result=(?P<result>yes|no)\s+(?P<updated>\S+)")


def _rounds(name: str) -> dict:
    """问 vrgen 要轮次列表（子进程，跨仓库只走 CLI 契约）。
    vrgen 不在（未装/路径未配）不炸页面——轮次区显示提示。"""
    try:
        r = subprocess.run([_vrgen_exe(), "status", "--job", name,
                            "--workdir", str(_workdir())],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, env=utf8_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"rounds": [], "hint": f"生成服务（vrgen）没有找到，生成记录暂时显示不了：{e}"}
    rounds = []
    for line in (r.stdout or "").splitlines():
        m = _ROUND_LINE.match(line)
        if m:
            d = m.groupdict()
            # status 只显示目录名（≠ run key）；adopt 走目录全路径最稳
            d["round_dir"] = str(_workdir() / name / d["round"])
            rounds.append(d)
    return {"rounds": rounds,
            "hint": "" if rounds else (r.stdout or r.stderr or "").strip()}


def _vrgen_json(argv: list[str]) -> dict:
    """跑一条 vrgen 短命令并解析 JSON 输出（prompt show/save）。
    vrgen 不在（未装/路径未配）不炸页面——回 {"error": …} 给面板显示提示。"""
    try:
        r = subprocess.run([_vrgen_exe(), *argv], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           env=utf8_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": f"生成服务（vrgen）没有找到，提示词暂时显示不了：{e}"}
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout or "").strip() or "vrgen 命令失败"}
    try:
        return json.loads(r.stdout or "")
    except ValueError:
        return {"error": "vrgen 的返回不是合法 JSON"}


def _capture(argv: list[str]) -> tuple[int, str]:
    """短命令（result）：捕获输出文本，供关键帧挑选判断成功。"""
    try:
        r = subprocess.run(argv, capture_output=True, env=utf8_env(), timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    text = decode_text((r.stdout or b"") + (r.stderr or b""))
    return r.returncode, text


def _current_mode(name: str) -> str | None:
    payload = _vrgen_json(["params", "show", "--job", name,
                           "--workdir", str(_workdir())])
    if "error" in payload:
        return None
    return (payload.get("current") or {}).get("mode")


def _refs_payload(case: case_mod.Case, selected: list[case_mod.Asset]) -> dict:
    checked = case_mod.check_assets(
        case, _ctx["cases_root"], selected=[asset.id for asset in selected])
    by_id = {asset.id: path for asset, path in checked}
    return {"assets": [
        {"id": asset.id, "kind": asset.kind, "role": asset.role,
         "path": str(by_id[asset.id].resolve()), "sha256": asset.sha256}
        for asset in selected if asset.id in by_id
    ]}


def _prompt_preview_args(name: str, flow: str,
                         mode: str | None = None) -> tuple[list[str], Path | None]:
    """提示词展开所需的 CLI 参数：几何尺寸 + 提交集素材清单临时文件。

    拿不到素材或尺寸时不报错，只返回已能拿到的部分；vrgen 会把缺的占位符
    报成 render_error，页面显示具体缺什么，而不是干瞪占位符。
    """
    args: list[str] = []
    refs_file: Path | None = None
    try:
        case = case_mod.load(name, _ctx["cases_root"])
        _video, _mask, _template, canvas, source = resolve_package(
            case, _ctx["cases_root"], mode=mode)
    except (CaseError, GenError, KeyError, ValueError, OSError):
        return args, None
    args += ["--canvas", f"{canvas[0]}x{canvas[1]}",
             "--source", f"{source[0]}x{source[1]}"]
    refs_needed = flow == "gen" or (flow == "patch" and mode == "firstLastFrame")
    if refs_needed:
        try:
            payload = _refs_payload(case, selected_assets(case, mode))
        except (CaseError, GenError, OSError):
            return args, None
        fd, raw = tempfile.mkstemp(prefix=f"vrgen-prompt-{name}-", suffix=".json")
        os.close(fd)
        refs_file = Path(raw)
        refs_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        args += ["--refs", str(refs_file)]
    return args, refs_file


def _prompt(name: str, flow: str, mode: str | None = None) -> dict:
    """提示词模板 + 变量草稿 + 展开后的完整正文（决策 18；契约 case-model.md §4）。"""
    if mode is None:
        mode = _current_mode(name)
    argv = ["prompt", "show", "--job", name,
            "--template", template_for(flow, mode),
            "--workdir", str(_workdir())]
    preview_args, refs_file = _prompt_preview_args(name, flow, mode)
    try:
        return _vrgen_json([*argv, *preview_args])
    finally:
        if refs_file is not None:
            refs_file.unlink(missing_ok=True)


def _sessions_tree() -> dict:
    """项目/剧集/生成会话树（只读；契约 case-model.md §4）。"""
    return _vrgen_json(["sessions", "list"])


def _generation_params(name: str) -> dict:
    """生成页的输出/输入分辨率选项（决策 20；契约 case-model.md §4）。

    先不带 ``--input`` 读模式与当前草稿；只有确实有一个视频输入包、且当前
    模式不是 ``firstLastFrame`` 时，才带上 ``--input`` 计算档位。这样纯图片
    case 不会撞上 `params show` 的“不接受输入视频”闸门。
    """
    base = _vrgen_json(["params", "show", "--job", name,
                        "--workdir", str(_workdir())])
    if "error" in base:
        return base
    mode = (base.get("current") or {}).get("mode")
    try:
        case = case_mod.load(name, _ctx["cases_root"])
        video, _mask, _template, canvas, source = resolve_package(
            case, _ctx["cases_root"], mode=mode)
    except (CaseError, GenError, KeyError, ValueError, OSError):
        return base
    if video is None or mode == "firstLastFrame":
        return base
    argv = ["params", "show", "--job", name,
            "--input", str(video),
            "--canvas", f"{canvas[0]}x{canvas[1]}",
            "--source", f"{source[0]}x{source[1]}",
            "--workdir", str(_workdir())]
    return _vrgen_json(argv)


def _log_tail(name: str, round_key: str | None, lines: int = 24) -> list[str]:
    """vrgen 台账里的调用日志尾部（决策 15：generate 页日志 tail）。
    台账文件是两仓库的对外接口（case-model.md），读它不越契约。"""
    if not round_key:
        return []
    p = _workdir() / name / round_key / "mglc.log"
    if not p.is_file():
        return []
    try:
        with open(p, "rb") as f:
            data = f.read()
        return decode_text(data).splitlines()[-lines:]
    except OSError:
        return []


def _extracts(case: case_mod.Case) -> list[dict]:
    vroot = case.dir(_ctx["cases_root"]) / "extracts"
    out = []
    if vroot.is_dir():
        for vdir in sorted(p for p in vroot.iterdir() if p.is_dir()):
            out.append({"version": vdir.name,
                        "extracted": (vdir / "clip.json").is_file(),
                        "has_result": (vdir / "result.mp4").is_file()})
    return out


def _outputs(case: case_mod.Case) -> list[dict]:
    out = []
    odir = case.dir(_ctx["cases_root"]) / "out"
    if odir.is_dir():
        for p in sorted(odir.glob("*.mp4")):
            out.append({"file": p.name,
                        "url": f"/video/out/{case.name}/{p.name}"})
    return out


# ── 每步的输入/输出文件（按 flow 分流，两套规范不混用）──────────────────


def _file_info(path: Path) -> dict:
    """文件的名词/位置/修改时间/大小；不存在也是可展示的事实，不是错误。

    ``rel`` 是相对仓库根（E:\\VRAIGC\\vrstudio）的路径，页面优先显示它；
    仓库外的文件（例如外部源视频）为 None，页面改显示绝对路径。
    """
    p = Path(path)
    try:
        rel = os.path.relpath(p, config_mod.REPO_ROOT)
        if rel.startswith(".."):
            rel = None
    except ValueError:                     # 不同盘符
        rel = None
    try:
        st = p.stat()
    except OSError:
        return {"name": p.name, "path": str(p), "rel": rel, "exists": False,
                "mtime": None, "size": None}
    return {"name": p.name, "path": str(p), "rel": rel, "exists": True,
            "mtime": st.st_mtime, "size": st.st_size}


def _imread(path: Path, flags: int):
    """OpenCV 在 Windows 上不认非 ASCII 路径；从字节解码绕开它。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, flags) if data.size else None


def _media_meta(path: Path, kind: str) -> dict:
    """尺寸/时长/帧率：素材卡片与缩略图共用的只读元数据。"""
    meta: dict = {"width": None, "height": None, "duration": None, "fps": None}
    if not path.is_file():
        return meta
    if kind == "video":
        cap = cv2.VideoCapture(str(path))
        if cap.isOpened():
            meta["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
            meta["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            meta["fps"] = round(fps, 3) if fps else None
            meta["duration"] = round(frames / fps, 3) if fps and frames else None
        cap.release()
        return meta
    image = _imread(path, cv2.IMREAD_UNCHANGED)
    if image is not None:
        meta["height"], meta["width"] = int(image.shape[0]), int(image.shape[1])
    return meta


def _asset_details(case: case_mod.Case) -> list[dict]:
    """gen 素材卡片的数据源；文件缺失也照常返回，页面标红。

    ``label`` 按当前顺序重新编号（@Video1/@Image1），不能拿素材 id 的数字代替：
    拖动排序只改数组顺序，id 保持不变。
    """
    out = []
    counters = {"video": 0, "image": 0}
    for asset in case.assets:
        counters[asset.kind] += 1
        prefix = "Video" if asset.kind == "video" else "Image"
        path = asset.resolved(case, _ctx["cases_root"])
        info = _file_info(path)
        meta = _media_meta(path, asset.kind)
        out.append({
            "id": asset.id, "kind": asset.kind, "role": asset.role,
            "label": f"@{prefix}{counters[asset.kind]}", "path": asset.path,
            "url": f"/img/asset/{case.name}/{asset.id}",
            **info, **meta,
        })
    return out


def _round_dir(case: case_mod.Case) -> Path | None:
    """被采用轮次的目录。adopt_round 可能是轮次 key，也可能是历史写法里的
    绝对路径（web_demo 就是后者），两种都认。"""
    raw = case.adopt_round
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute() or p.parent != Path("."):
        return p
    return _workdir() / case.name / raw


def _generation_groups(case: case_mod.Case, inputs: list[dict],
                       adopted: Path) -> dict:
    """生成步骤拆两组：vrgen 轮次目录里的原始结果，以及 adopt 拷进 case 的副本。

    原始结果路径读 round/run.json 的 ``result`` 字段（vrgen 台账契约），读不到
    时退回 round/result.mp4；没有 adopt 指针时只显示生成输入，不猜输出。
    """
    round_dir = _round_dir(case)
    raw = run_json = None
    if round_dir is not None:
        run_json = round_dir / "run.json"
        try:
            recorded = json.loads(run_json.read_text(encoding="utf-8")).get("result")
        except (OSError, ValueError):
            recorded = None
        raw = Path(recorded) if recorded else round_dir / "result.mp4"

    groups = [{
        "label": "生成",
        "inputs": inputs,
        "outputs": ([_file_info(raw)] if raw is not None else []) +
                   ([_file_info(run_json)] if run_json is not None else []),
    }]
    if raw is not None:
        groups.append({
            "label": "采用",
            "inputs": [_file_info(raw)],
            "outputs": [_file_info(adopted)],
        })
    return {"groups": groups}


def _fullredraw_step_files(case: case_mod.Case, cdir: Path) -> dict:
    """fullredraw：源 → 扩宽件 → 采用结果 → 还原成片。

    pad 的产物名以 manifest 为准（它记录的是 pad 真做了什么）；manifest 还没
    生成时退回 web `pad` op 的固定输出名，让页面能显示"这一步会写到哪"。
    """
    padded = cdir / "seedance_input.mp4"
    mask = cdir / "seedance_input_mask.png"
    manifests = sorted(cdir.glob("*_pad.json"))
    manifest = manifests[0] if manifests else cdir / "seedance_input_pad.json"
    if manifests:
        try:
            man = Manifest.load(manifests[0])
        except (OSError, ValueError):
            man = None
        if man is not None:
            if man.padded:
                padded = cdir / man.padded
            if man.mask.path:
                mask = cdir / Path(man.mask.path).name
    source = Path(case.source_path)
    result = cdir / "result.mp4"
    outputs = []
    if case.adopt_round:
        out = cdir / "out" / f"{case.adopt_round}.mp4"
        outputs = [out, out.with_name(out.stem + "_recompose.json")]
    return {
        "pad": {
            "inputs": [_file_info(source)],
            "outputs": [_file_info(padded), _file_info(mask), _file_info(manifest)],
        },
        "generate": _generation_groups(
            case, [_file_info(padded), _file_info(mask), _file_info(manifest)],
            result),
        "recompose": {
            "inputs": [_file_info(result), _file_info(manifest)],
            "outputs": [_file_info(p) for p in outputs],
        },
    }


def _patch_step_files(case: case_mod.Case, cdir: Path) -> dict:
    """patch：源 → 选区片段 → 采用结果 → 贴回成片。没有 extract 指针时
    只显示已知的源，不猜版本目录。"""
    source = Path(case.source_path)
    steps = {
        "extract": {"inputs": [_file_info(source)], "outputs": []},
        "generate": {"inputs": [], "outputs": []},
        "merge": {"inputs": [_file_info(source)], "outputs": []},
    }
    if not case.extract_current:
        return steps
    d = cdir / "extracts" / case.extract_current
    clip = d / "clip.mp4"
    mask = d / "clip_mask.png"
    sidecar = d / "clip.json"
    ai = d / "result.mp4"
    out = cdir / "out" / f"{case.extract_current}.mp4"
    steps["extract"] = {
        "inputs": [_file_info(source)],
        "outputs": [_file_info(clip), _file_info(mask), _file_info(sidecar)],
    }
    steps["generate"] = _generation_groups(
        case, [_file_info(clip), _file_info(mask), _file_info(sidecar)], ai)
    steps["merge"] = {
        "inputs": [_file_info(source), _file_info(ai),
                   _file_info(sidecar), _file_info(clip)],
        "outputs": [_file_info(out)],
    }
    return steps


def _gen_step_files(case: case_mod.Case, cdir: Path) -> dict:
    """gen：素材副本是输入；采用后的副本落在 case/result.mp4。"""
    files = [_file_info(a.resolved(case, _ctx["cases_root"])) for a in case.assets]
    return {
        "assets": {"inputs": [], "outputs": files},
        "generate": _generation_groups(case, files, cdir / "result.mp4"),
    }


def _step_files(case: case_mod.Case) -> dict:
    """三条 flow 的文件规范完全不同，这里显式分流，禁止共用一张表。"""
    cdir = case.dir(_ctx["cases_root"])
    if case.flow == "fullredraw":
        return _fullredraw_step_files(case, cdir)
    if case.flow == "patch":
        return _patch_step_files(case, cdir)
    if case.flow == "gen":
        return _gen_step_files(case, cdir)
    return {}


def _gates(case: case_mod.Case, extracts: list[dict]) -> dict:
    """软向导的门控判据与证据（服务端唯一权威，前端只渲染结果）。

    每一步带两段文字：没完成时 reason 说差什么；完成时 evidence 说"在哪个
    绝对路径检测到了什么文件、所以认为完成、想重跑怎么办"——页面上每一次
    置灰/放行都可以对照文件复查。
    """
    cdir = case.dir(_ctx["cases_root"])
    missing = case_mod.draft_ready(case)
    extracted = bool(case.extract_current) and any(
        e["version"] == case.extract_current and e["extracted"] for e in extracts)
    adopted = bool(case.adopt_round)
    result_file = (case.flow in ("fullredraw", "gen") and (cdir / "result.mp4").is_file())
    pad_manifests = sorted(cdir.glob("*_pad.json"))
    padded = bool(pad_manifests)
    outs = _outputs(case)
    has_out = bool(outs)
    out_names = "、".join(o["file"] for o in outs)
    if case.flow == "patch":
        cur = cdir / "extracts" / (case.extract_current or "")
        steps = {            "pick": {"ok": missing is None,
                     "reason": f"case.toml 里缺 [{missing}] 段，补齐后才能抽取" if missing else "",
                     "evidence": f"草稿三段（viewport/inner/frames）都在 {case_mod.case_toml(case.name, _ctx['cases_root'])} 里"},
            "extract": {"ok": extracted,
                        "reason": "" if extracted else "先完成选区并运行抽取",
                        "evidence": f"在 {cur}\\ 检测到 clip.json / clip.mp4 / clip_mask.png，当前抽取版本是 {case.extract_current}；想重新抽取，调好选区后再点一次按钮（会生成新版本，旧版本不动）"},
            "generate": {"ok": extracted and (adopted or any(
                e["has_result"] for e in extracts)),
                "reason": "" if extracted else "抽取之后才能送生成",
                "evidence": (f"在 {cur}\\result.mp4 检测到已选用的结果（来自 Session {case.adopt_round}）"
                             if adopted else f"在 {cur}\\result.mp4 检测到结果"),
                "ready": extracted},
            "merge": {"ok": has_out, "ready": extracted and adopted,
                      "reason": "" if has_out else "采用一次生成结果后，才能合并出片",
                      "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}；每次合并都会覆盖同名文件"},
            "compare": {"ok": has_out, "reason": "" if has_out else "合并出片后可以在这里对比",
                        "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}"},
        }
    elif case.flow == "fullredraw":
        # evidence 只在 ok 时被页面读到，但仍须防御求值——全新 case 没有
        # manifest，无条件取 [0] 会让整个 /api/case 500（2026-10-16 验收发现）
        steps = {
            "pad": {"ok": padded,
                    "reason": "" if padded else "先把两侧画面扩充好，再送生成",
                    "evidence": (f"在 {pad_manifests[0]} 检测到扩充画面的记录文件，"
                                 f"这一步已完成；再点一次按钮会重新扩充并覆盖它"
                                 ) if padded else ""},
            "generate": {"ok": result_file, "ready": padded,
                         "reason": "" if result_file else "先送生成，并采用一次结果",
                         "evidence": (f"在 {cdir}\\result.mp4 检测到已选用的结果"
                                      f"（来自 Session {case.adopt_round}）；想换一个，回到上面点别的「选用」"
                                      if result_file else "")},
            "recompose": {"ok": has_out, "ready": result_file,
                          "reason": "" if has_out else "采用结果后才能裁回原尺寸",
                          "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}；每次裁回都会覆盖同名文件"},
            "compare": {"ok": has_out,
                        "reason": "" if has_out else "裁回原尺寸出片后，在这里对比",
                        "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}"},
        }
    else:
        roles_ok = bool(case.assets) and all(a.role in case_mod.ROLES for a in case.assets)
        assets_ok = bool(case.assets) and roles_ok
        if not case.assets:
            asset_reason = "先添加至少 1 个素材"
        elif not roles_ok:
            asset_reason = "每个素材都要选择角色"
        else:
            asset_reason = ""
        steps = {
            "assets": {
                "ok": assets_ok,
                "reason": asset_reason,
                "evidence": (f"在 {cdir}\\assets\\ 检测到 {len(case.assets)} 条素材，"
                             f"顺序与角色已写入 case.toml" if assets_ok else ""),
            },
            "generate": {"ok": result_file, "ready": assets_ok,
                         "reason": "" if result_file else "填好提示词和分辨率后提交生成，并采用一轮结果",
                         "evidence": (f"在 {cdir}\\result.mp4 检测到已选用的结果"
                                      f"（来自 Session {case.adopt_round}）；想换一个，回到上面点别的「选用」"
                                      if result_file else "")},
        }
    return steps


def _derived(case: case_mod.Case) -> list[dict]:
    """源视频与流程产物：只读派生条目，不进 ``[[assets]]``。"""
    rows: list[dict] = []
    cdir = case.dir(_ctx["cases_root"])
    if case.source_path:
        rows.append({
            "kind": "video", "role": "source", "label": "源视频",
            "editable": False, "submit": "videoEdit 自动作为输入，无需勾选",
            **_file_info(Path(case.source_path)),
        })
    if case.flow == "patch" and case.extract_current:
        d = cdir / "extracts" / case.extract_current
        for name, kind, label, submit in (
                ("clip.mp4", "video", "当前 clip", "videoEdit 自动作为输入；firstLastFrame 不提交流程视频"),
                ("clip_mask.png", "image", "当前 mask", "仅 videoEdit 使用"),
                ("clip.json", "file", "几何契约", "不提交"),
                ("result.mp4", "video", "已采用结果", "流程产物，不提交")):
            rows.append({"kind": kind, "role": "derived", "label": label,
                         "editable": False, "submit": submit,
                         **_file_info(d / name)})
    elif case.flow == "fullredraw":
        manifests = sorted(cdir.glob("*_pad.json"))
        for name, kind, label, submit in (
                ("seedance_input.mp4", "video", "pad 画布", "videoEdit 自动作为输入"),
                ("seedance_input_mask.png", "image", "pad mask", "随画布提交"),
                ("seedance_input_pad.json", "file", "pad 几何契约", "不提交"),
                ("result.mp4", "video", "已采用结果", "流程产物，不提交")):
            rows.append({"kind": kind, "role": "derived", "label": label,
                         "editable": False, "submit": submit,
                         **_file_info(cdir / name)})
        for manifest in manifests:
            rows.append({"kind": "file", "role": "derived", "label": "manifest",
                         "editable": False, "submit": "recompose 的几何权威，不提交",
                         **_file_info(manifest)})
    elif case.flow == "gen":
        rows.append({"kind": "video", "role": "derived", "label": "已采用结果",
                     "editable": False, "submit": "流程产物，不提交",
                     **_file_info(cdir / "result.mp4")})
    return rows


def _case_info(name: str) -> dict | None:
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return None
    store = get_store(name) if case.flow == "patch" else None
    extracts = _extracts(case) if case.flow == "patch" else []
    info = {
        "name": case.name,
        "flow": case.flow,
        "source_url": f"/video/source/{case.name}",
        "source_path": case.source_path,
        "viewport": case.viewport, "inner": case.inner, "frames": case.frames,
        "frame_count": store.frame_count if store else None,
        "extract_current": case.extract_current,
        "adopt_round": case.adopt_round,
        "assets": _asset_details(case),
        "submit_selected": case.submit_selected,
        "derived": _derived(case),
        "extracts": extracts,
        "outputs": _outputs(case),
        "step_files": _step_files(case),
        "gates": _gates(case, extracts),
        "previews": {"vp": f"/img/{case.name}/vp.png?k={store.key}"} if store else {},
        "rounds": _rounds(case.name),
        "log_tail": _log_tail(case.name, case.adopt_round),
    }
    return info


def _save_gen_mode(name: str, mode: str) -> str | None:
    """创建 gen case 后立即写模式；返回错误文案，成功返回 None。"""
    payload = _vrgen_json(["params", "save", "--job", name,
                           "--mode", mode, "--workdir", str(_workdir())])
    return payload.get("error")


async def api_cases(request):
    if request.method == "GET":
        names = sorted(p.parent.name for p in _root().glob("*/case.toml"))
        infos = await asyncio.to_thread(
            lambda: [i for n in names if (i := _brief(n)) is not None])
        return JSONResponse(infos)
    # POST：建新 case。gen 不再要求视频，但必须选模式；patch/fullredraw 仍要视频。
    body = await request.json()
    flow = body.get("flow")
    if flow not in case_mod.FLOWS:
        return JSONResponse({"error": f"flow 必须是 {' 或 '.join(case_mod.FLOWS)}"},
                            status_code=400)
    raw_video = str(body.get("video") or "")
    video = sources_dir() / Path(raw_video).name if raw_video else None
    mode = body.get("mode")
    if flow == "gen":
        if mode not in case_mod.GEN_MODES:
            return JSONResponse(
                {"error": f"gen 必须选择模式：{' / '.join(case_mod.GEN_MODES)}"},
                status_code=400)
        if video is not None and not video.is_file():
            return JSONResponse({"error": f"sources/ 里没有这个视频：{video.name}"},
                                status_code=400)
        if video is None and not str(body.get("name") or "").strip():
            return JSONResponse({"error": "没有视频素材时，必须填写 case 名"},
                                status_code=400)
    elif video is None or not video.is_file():
        return JSONResponse({"error": "sources/ 里没有这个视频"}, status_code=400)

    name = str(body.get("name") or (video.stem if video else "")).strip()
    if not name:
        return JSONResponse({"error": "必须填写 case 名"}, status_code=400)
    n = w = 0
    fps = 30.0
    if video is not None:
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            return JSONResponse({"error": "打不开这个视频"}, status_code=400)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
    try:
        case = case_mod.create(name, flow, video, root=_ctx["cases_root"])
        if flow == "patch":
            vpw = min(1920, w)
            vph = int(vpw * 9 / 16)
            case_mod.set_draft(name, root=_ctx["cases_root"],
                               viewport={"yaw_deg": 0.0, "pitch_deg": 0.0,
                                         "fov_h_deg": 59.0,
                                         "width": vpw, "height": vph},
                               inner={"x": vpw // 4, "y": vph // 4,
                                      "width": vpw // 2, "height": vph // 2},
                               frames={"start": 0, "end": max(0, n - 1)})
    except CaseError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    if flow == "gen":
        error = await asyncio.to_thread(_save_gen_mode, name, mode)
        if error:
            return JSONResponse(
                {"error": f"case 已创建，但模式没保存：{error}",
                 "name": name, "created": True}, status_code=502)
    return JSONResponse({"name": case.name, "flow": flow,
                         "fps": round(fps, 3)})


def _brief(name: str) -> dict | None:
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return None
    extracts = _extracts(case) if case.flow == "patch" else []
    return {"name": case.name, "flow": case.flow,
            "gates": _gates(case, extracts)}


def _is_16_9(width: int, height: int) -> bool:
    ratio = width / height if height else 0.0
    return abs(ratio - 16 / 9) <= (16 / 9) * 0.001


def _mode_ready(case: case_mod.Case, mode: str | None) -> tuple[bool, str]:
    """提交前按当前模式检查；页面用它决定生成按钮是否可用。"""
    if mode == "firstLastFrame":
        firsts = [a for a in case.assets if a.role == "first_frame"]
        lasts = [a for a in case.assets if a.role == "last_frame"]
        if len(firsts) != 1 or len(lasts) != 1:
            return False, "firstLastFrame 需要恰好一张首帧与一张尾帧，先在工作台挑选"
        if case.flow == "patch":
            vp = case.viewport or {}
            width, height = int(vp.get("width") or 0), int(vp.get("height") or 0)
            if not _is_16_9(width, height):
                return False, "patch viewport 不是 16:9；firstLastFrame 首期只接受 16:9"
        return True, ""
    if mode == "allMediaRef":
        if not case.assets:
            return False, "allMediaRef 至少需要 1 个素材"
        return True, ""
    if mode in ("videoEdit", "videoExtend"):
        if case.flow == "gen" and not any(a.kind == "video" for a in case.assets):
            return False, f"{mode} 需要 1 个视频母版"
        return True, ""
    return True, ""


def _apply_mode_gate(info: dict, case: case_mod.Case, mode: str | None) -> None:
    gate = (info.get("gates") or {}).get("generate")
    if gate is None or not mode:
        return
    ready, reason = _mode_ready(case, mode)
    if not ready:
        gate["ready"] = False
        gate["reason"] = reason


async def api_case(request):
    name = request.path_params["name"]
    info = await asyncio.to_thread(_case_info, name)
    if info is None:
        return JSONResponse({"error": "no such case"}, status_code=404)
    # prompt / 生成参数只挂在这一个读端点上：draft/nudge 等高频路径不多 spawn
    # 两个子进程
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    params = await asyncio.to_thread(_generation_params, name)
    info["generation_params"] = params
    mode = (params.get("current") or {}).get("mode")
    _apply_mode_gate(info, case, mode)
    info["prompt"] = await asyncio.to_thread(_prompt, name, info["flow"], mode)
    return JSONResponse(info)


async def api_sessions(request):
    """项目/剧集/生成会话树：每次请求都重新问 vrgen（不缓存）。"""
    payload = await asyncio.to_thread(_sessions_tree)
    status = 400 if "error" in payload else 200
    return JSONResponse(payload, status_code=status)


async def api_modes(request):
    """新建 gen case 的模式列表（模型能力；无需已有 case）。"""
    payload = await asyncio.to_thread(
        _vrgen_json, ["params", "show", "--job", "modes-probe",
                      "--workdir", str(_workdir())])
    status = 400 if "error" in payload else 200
    return JSONResponse(payload, status_code=status)


def _thumb(path: Path, kind: str) -> bytes:
    image = None
    if kind == "video":
        cap = cv2.VideoCapture(str(path))
        ok, frame = cap.read()
        cap.release()
        image = frame if ok else None
    else:
        image = _imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"读不出缩略图：{path.name}")
    height, width = image.shape[:2]
    if width > 320:
        scale = 320 / width
        image = cv2.resize(image, (320, max(1, int(height * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        raise ValueError(f"缩略图编码失败：{path.name}")
    return buffer.tobytes()


async def asset_img(request):
    """素材缩略图：视频取首帧，图片直接缩放；每次请求现算，不设缓存。"""
    try:
        case = case_mod.load(request.path_params["name"], _ctx["cases_root"])
        asset = case.asset(request.path_params["file"])
    except CaseError:
        return JSONResponse({"error": "not found"}, status_code=404)
    path = asset.resolved(case, _ctx["cases_root"])
    if not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        data = await asyncio.to_thread(_thumb, path, asset.kind)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return Response(content=data, media_type="image/jpeg")


async def api_assets(request):
    """三流程素材增删改排（唯一权威仍是 vrstudio.case 的素材函数）。"""
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    op = body.get("op")
    try:
        if op == "add":
            paths = [str(p) for p in (body.get("paths") or [])]
            roles = [str(r) for r in (body.get("roles") or [])]
            if not paths or len(paths) != len(roles):
                return JSONResponse({"error": "paths 与 roles 要一一对应且非空"},
                                    status_code=400)
            await asyncio.to_thread(case_mod.add_assets, name,
                                    list(zip(paths, roles, strict=True)), _ctx["cases_root"])
        elif op == "remove":
            await asyncio.to_thread(case_mod.remove_asset, name,
                                    str(body.get("id") or ""), _ctx["cases_root"])
        elif op == "role":
            await asyncio.to_thread(case_mod.set_asset_role, name,
                                    str(body.get("id") or ""),
                                    str(body.get("role") or ""), _ctx["cases_root"])
        elif op == "reorder":
            ids = [str(i) for i in (body.get("ids") or [])]
            await asyncio.to_thread(case_mod.reorder_assets, name, ids,
                                    _ctx["cases_root"])
        else:
            return JSONResponse({"error": f"未知素材操作：{op}"}, status_code=400)
    except CaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    case = await asyncio.to_thread(case_mod.load, name, _ctx["cases_root"])
    info = await asyncio.to_thread(_case_info, name)
    params = await asyncio.to_thread(_generation_params, name)
    info["generation_params"] = params
    mode = (params.get("current") or {}).get("mode")
    _apply_mode_gate(info, case, mode)
    info["prompt"] = await asyncio.to_thread(_prompt, name, case.flow, mode)
    return JSONResponse(info)


def _info_with_generation(name: str) -> dict:
    case = case_mod.load(name, _ctx["cases_root"])
    info = _case_info(name) or {}
    params = _generation_params(name)
    info["generation_params"] = params
    mode = (params.get("current") or {}).get("mode")
    _apply_mode_gate(info, case, mode)
    info["prompt"] = _prompt(name, case.flow, mode)
    return info


async def api_derived(request):
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    return JSONResponse({"derived": _derived(case)})


async def api_submit_set(request):
    """``[submit] selected`` 的读写（侧边栏勾选即时保存）。"""
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    if request.method == "GET":
        return JSONResponse({"selected": case.submit_selected})
    body = await request.json()
    if "selected" not in body:
        return JSONResponse({"error": "缺少 selected"}, status_code=400)
    selected = body.get("selected")
    if selected is not None:
        if not isinstance(selected, list):
            return JSONResponse({"error": "selected 必须是数组"}, status_code=400)
        selected = [str(item) for item in selected]
    try:
        await asyncio.to_thread(case_mod.set_submit_selected, name, selected,
                                _ctx["cases_root"])
    except CaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(await asyncio.to_thread(_info_with_generation, name))


async def api_extract_frames(request):
    """从当前 clip 抽首尾原始帧，按源分辨率导出，不缩放。"""
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    if case.flow != "patch":
        return JSONResponse({"error": "只有 patch 流可以从 clip 抽帧"}, status_code=400)
    if not case.extract_current:
        return JSONResponse({"error": "还没有抽取版本"}, status_code=400)
    clip = (case.dir(_ctx["cases_root"]) / "extracts" / case.extract_current / "clip.mp4")
    if not clip.is_file():
        return JSONResponse({"error": f"clip 不存在：{clip}"}, status_code=400)
    body = await request.json()
    try:
        info = await asyncio.to_thread(probe, clip)
    except VideoIOError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    duration = info.duration_s
    fps = info.fps_float or 30.0
    try:
        t_first = float(body.get("t_first", 0.0))
        t_last = float(body.get("t_last", max(0.0, duration - 1.0 / fps)))
    except (TypeError, ValueError):
        return JSONResponse({"error": "t_first / t_last 必须是秒数"}, status_code=400)
    t_first = min(max(0.0, t_first), duration)
    t_last = min(max(0.0, t_last), max(0.0, duration - 1.0 / fps))
    if t_last <= t_first:
        return JSONResponse({"error": "t_last 必须大于 t_first"}, status_code=400)
    tmpdir = Path(tempfile.mkdtemp(prefix=f"frames-{name}-"))
    try:
        pairs = []
        for role, t in (("first_frame_raw", t_first), ("last_frame_raw", t_last)):
            out = tmpdir / f"{role}.png"
            await asyncio.to_thread(extract_frame, clip, t, out)
            pairs.append((out, role))
        await asyncio.to_thread(case_mod.add_assets, name, pairs, _ctx["cases_root"])
    except (VideoIOError, CaseError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return JSONResponse(await asyncio.to_thread(_info_with_generation, name))


def _keyframe_ratio(case: case_mod.Case) -> str:
    if case.flow == "patch":
        vp = case.viewport or {}
        if not _is_16_9(int(vp.get("width") or 0), int(vp.get("height") or 0)):
            raise CaseError("patch viewport 不是 16:9；首期工作台只接受 16:9")
    return "16:9"


def _keyframe_argv(name: str, body: dict) -> list[str]:
    case = case_mod.load(name, _ctx["cases_root"])
    ids = [str(item) for item in (body.get("assets") or [])]
    if not ids:
        raise CaseError("工作台至少需要 1 张参考图")
    paths = [case.asset(asset_id).resolved(case, _ctx["cases_root"]) for asset_id in ids]
    for path in paths:
        if not path.is_file():
            raise CaseError(f"参考图不存在：{path}")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise CaseError("提示词不能为空")
    argv = [_vrgen_exe(), "image", "submit", "--job", name, "--prompt", prompt]
    for path in paths:
        argv += ["--image", str(path)]
    argv += ["--ratio", _keyframe_ratio(case)]
    if body.get("model_id") is not None:
        argv += ["--model-id", str(body["model_id"])]
    if body.get("resolution"):
        argv += ["--resolution", str(body["resolution"])]
    if body.get("num") is not None:
        argv += ["--num", str(body["num"])]
    argv += ["--workdir", str(_workdir())]
    return argv


def _keyframe_rounds(name: str) -> dict:
    root = _workdir() / name
    rows: list[dict] = []
    if not root.is_dir():
        return {"rounds": []}
    for directory in sorted((p for p in root.glob("img-*") if p.is_dir()),
                            key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            blob = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        results = []
        for index, raw in enumerate(blob.get("results") or [], start=1):
            if Path(raw).is_file():
                results.append({"index": index,
                                "url": f"/img/keyframe/{name}/{directory.name}/{index}"})
        rows.append({"round": directory.name, "key": blob.get("key", ""),
                     "stage": blob.get("stage", ""),
                     "gate": blob.get("gate") or {}, "results": results})
    return {"rounds": rows}


async def api_keyframe_run(request):
    name = request.path_params["name"]
    body = await request.json()
    try:
        argv = await asyncio.to_thread(_keyframe_argv, name, body)
    except (CaseError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    QUEUE.submit("keyframe", name, argv)
    return JSONResponse({"submitted": "keyframe", "case": name})


async def api_keyframe_rounds(request):
    name = request.path_params["name"]
    if not case_mod.case_toml(name, _ctx["cases_root"]).is_file():
        return JSONResponse({"error": "no such case"}, status_code=404)
    return JSONResponse(await asyncio.to_thread(_keyframe_rounds, name))


async def api_keyframe_pick(request):
    name = request.path_params["name"]
    body = await request.json()
    role = str(body.get("role") or "first_frame")
    if role not in ("first_frame", "last_frame"):
        return JSONResponse({"error": "角色只能是 first_frame 或 last_frame"},
                            status_code=400)
    round_name = str(body.get("round") or "")
    directory = _workdir() / name / round_name
    try:
        blob = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return JSONResponse({"error": "找不到这个图片轮次"}, status_code=404)
    results = blob.get("results") or []
    try:
        index = int(body.get("index") or 1)
    except (TypeError, ValueError):
        return JSONResponse({"error": "index 必须是整数"}, status_code=400)
    if not 1 <= index <= len(results):
        return JSONResponse({"error": f"这一轮没有第 {index} 张候选"}, status_code=400)
    suffix = Path(results[index - 1]).suffix or ".png"
    fd, raw = tempfile.mkstemp(prefix=f"keyframe-{name}-", suffix=suffix)
    os.close(fd)
    target = Path(raw)
    argv = [_vrgen_exe(), "result", "--job", name, "--round",
            str(blob.get("key") or round_name), "--index", str(index),
            "--out", str(target), "--workdir", str(_workdir())]
    try:
        code, output = await asyncio.to_thread(_capture, argv)
        if code != 0:
            return JSONResponse({"error": output.strip() or "vrgen result 失败"},
                                status_code=502)
        meta = await asyncio.to_thread(_media_meta, target, "image")
        width, height = int(meta.get("width") or 0), int(meta.get("height") or 0)
        if not _is_16_9(width, height):
            return JSONResponse({"error": f"候选图不是 16:9（{width}x{height}）"},
                                status_code=400)
        other_role = "last_frame" if role == "first_frame" else "first_frame"
        existing = next((a for a in case_mod.load(name, _ctx["cases_root"]).assets
                         if a.role == other_role), None)
        if existing is not None:
            other = await asyncio.to_thread(
                _media_meta, existing.resolved(case_mod.load(name, _ctx["cases_root"]),
                                               _ctx["cases_root"]), "image")
            if (int(other.get("width") or 0), int(other.get("height") or 0)) != (width, height):
                return JSONResponse(
                    {"error": "首帧与尾帧宽高不一致，请重新出图"}, status_code=400)
        await asyncio.to_thread(case_mod.add_assets, name, [(target, role)],
                                _ctx["cases_root"])
    except (CaseError, OSError, VideoIOError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        target.unlink(missing_ok=True)
    return JSONResponse(await asyncio.to_thread(_info_with_generation, name))


async def keyframe_img(request):
    name = request.path_params["name"]
    round_name = request.path_params["round"]
    try:
        index = int(request.path_params["index"])
    except ValueError:
        return JSONResponse({"error": "bad index"}, status_code=400)
    directory = _workdir() / name / round_name
    try:
        blob = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return JSONResponse({"error": "not found"}, status_code=404)
    results = blob.get("results") or []
    if not 1 <= index <= len(results):
        return JSONResponse({"error": "not found"}, status_code=404)
    path = Path(results[index - 1])
    if not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(str(path), media_type=media)


async def api_prompt(request):
    """提示词变量草稿的保存（决策 18：实时保存，草稿权威在 vrgen 侧）。
    body：{"set": key, "value": …} 新增/覆盖；{"unset": key} 删除。"""
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    mode = await asyncio.to_thread(_current_mode, name)
    argv = ["prompt", "save", "--job", name,
            "--template", template_for(case.flow, mode), "--workdir", str(_workdir())]
    preview_args, refs_file = _prompt_preview_args(name, case.flow, mode)
    argv += preview_args
    if body.get("unset"):
        argv += ["--unset", str(body["unset"])]
    elif body.get("set"):
        argv += ["--set", f"{body['set']}={body.get('value', '')}"]
    else:
        if refs_file is not None:
            refs_file.unlink(missing_ok=True)
        return JSONResponse({"error": "没有要改的内容"}, status_code=400)
    try:
        payload = await asyncio.to_thread(_vrgen_json, argv)
    finally:
        if refs_file is not None:
            refs_file.unlink(missing_ok=True)
    if "error" in payload:
        return JSONResponse(payload, status_code=400)
    info = await asyncio.to_thread(_case_info, name)
    info["prompt"] = payload          # save 的回显就是保存后的状态，不再多问一次
    return JSONResponse(info)


async def api_params(request):
    """生成参数的保存（决策 20：输出档位 + 输入缩放，写 vrgen generation.toml）。"""
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    resolution = str(body.get("resolution") or "")
    scale = str(body.get("scale") or "")
    if not any(body.get(k) is not None for k in
               ("resolution", "scale", "mode", "aspect_ratio",
                "session_id", "project_id", "episode")):
        return JSONResponse({"error": "没有要保存的参数"}, status_code=400)
    argv = ["params", "save", "--job", name]
    if resolution:
        argv += ["--resolution", resolution]
    if scale:
        argv += ["--scale", scale]
    for field, flag in (("mode", "--mode"),
                        ("aspect_ratio", "--aspect-ratio"),
                        ("session_id", "--session-id"),
                        ("project_id", "--project-id"),
                        ("episode", "--episode")):
        if body.get(field) is not None:
            argv += [flag, str(body[field])]
    argv += ["--workdir", str(_workdir())]
    payload = await asyncio.to_thread(_vrgen_json, argv)
    if "error" in payload:
        return JSONResponse(payload, status_code=400)
    info = await asyncio.to_thread(_case_info, name)
    params = await asyncio.to_thread(_generation_params, name)
    info["generation_params"] = params
    mode = (params.get("current") or {}).get("mode")
    _apply_mode_gate(info, case, mode)
    info["prompt"] = await asyncio.to_thread(_prompt, name, case.flow, mode)
    return JSONResponse(info)


# ── 草稿 API（pick；唯一权威 = vrstudio.case.set_draft）─────────────────


async def api_draft(request):
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    if case.flow != "patch":
        return JSONResponse({"error": "fullredraw 没有 pick 草稿"}, status_code=400)
    body = await request.json()
    sections = {}
    for key in ("viewport", "inner", "frames"):
        if key in body:
            sections[key] = body[key]
    if not sections:
        return JSONResponse({"error": "没有要写的段"}, status_code=400)
    case_mod.set_draft(name, root=_ctx["cases_root"], **sections)
    _stores.pop(name, None)
    return JSONResponse(_case_info(name))


async def api_viewport(request):
    store = get_store(request.path_params["name"])
    if store is None:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    await asyncio.to_thread(store.set_viewport_from_erp_click,
                            float(body["fx"]), float(body["fy"]))
    _stores.pop(request.path_params["name"], None)
    return JSONResponse(_case_info(request.path_params["name"]))


async def api_inner(request):
    name = request.path_params["name"]
    store = get_store(name)
    if store is None:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    # 客户端契约：提交的是预览图上的 0..1 分数（与预览分辨率、CSS 尺寸都无关）
    fx, fy, fw, fh = (float(body[k]) for k in ("fx", "fy", "fw", "fh"))
    vp = store.case.viewport
    x, y = int(fx * vp["width"]), int(fy * vp["height"])
    w, h = int(fw * vp["width"]), int(fh * vp["height"])
    if w <= 0 or h <= 0:
        return JSONResponse({"error": "width/height must be positive"}, status_code=400)
    await asyncio.to_thread(store.set_inner, x, y, w, h)
    _stores.pop(name, None)
    return JSONResponse(_case_info(name))


async def api_nudge(request):
    name = request.path_params["name"]
    store = get_store(name)
    if store is None:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    yaw, pitch = await asyncio.to_thread(
        store.nudge, float(body.get("dyaw", 0.0)), float(body.get("dpitch", 0.0)))
    _stores.pop(name, None)
    info = _case_info(name)                  # 回完整 info：前端 post() 统一就地刷新
    info["nudged"] = {"yaw": yaw, "pitch": pitch}
    return JSONResponse(info)


async def api_reset_draft(request):
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    version = body.get("version") or case.extract_current
    if not version:
        return JSONResponse({"error": "还没有任何抽取版本可回退"}, status_code=400)
    sj = case.dir(_ctx["cases_root"]) / "extracts" / version / "clip.json"
    if not sj.is_file():
        return JSONResponse({"error": f"抽取 {version} 缺 clip.json"}, status_code=400)
    seg = json.loads(sj.read_text(encoding="utf-8"))["segments"][0]
    case_mod.set_draft(name, root=_ctx["cases_root"],
                       viewport=seg["viewport"], inner=seg["inner"])
    _stores.pop(name, None)
    return JSONResponse(_case_info(name))


# ── 流水线：统一入口 submit(op) → 单通道队列跑 CLI ───────────────────────


def _op_argv(name: str, op: str, body: dict) -> list[str]:
    """op → CLI argv。所有长操作都走队列（决策 17 串行层），本函数是唯一
    的 op 注册表——多用户扩展（owner 字段等）加在这里。"""
    case = case_mod.load(name, _ctx["cases_root"])
    cdir = case.dir(_ctx["cases_root"])
    root = ["--cases-dir", str(_root())]
    if op == "extract":
        return vrstudio_argv("extract", "name", "--case", name, *root)
    if op == "pad":
        return vrstudio_argv("pad", "--in", case.source_path,
                             "--out", str(cdir / "seedance_input.mp4"),
                             "--quiet")
    if op == "register":
        f = body.get("file", "")
        if not Path(f).is_file():
            raise CaseError(f"不是文件：{f}")
        return vrstudio_argv("generate", "register", "--case", name,
                             "--file", f, *root)
    if op in ("submit", "dry_run"):
        argv = vrstudio_argv("generate", "submit", "--case", name, *root)
        if op == "dry_run":
            argv += ["--check"]
        for item in body.get("vars") or []:
            argv += ["--var", item]
        if body.get("resolution"):
            argv += ["--resolution", str(body["resolution"])]
        if body.get("scale"):
            argv += ["--scale", str(body["scale"])]
        if body.get("mode"):
            argv += ["--mode", str(body["mode"])]
        if body.get("duration") is not None:
            argv += ["--duration", str(body["duration"])]
        if body.get("aspect_ratio"):
            argv += ["--aspect-ratio", str(body["aspect_ratio"])]
        return argv
    if op == "adopt":
        argv = vrstudio_argv("generate", "adopt", "--case", name, *root)
        if body.get("round") and body["round"] != "latest":
            argv += ["--round", body["round"]]
        return argv
    if op == "merge":
        argv = vrstudio_argv("merge", "--case", name, "-q", *root)
        if body.get("allow_crop"):
            argv += ["--allow-crop"]
        return argv
    if op == "recompose":
        manifests = sorted(cdir.glob("*_pad.json"))
        if not manifests:
            raise CaseError("还没有扩充两侧画面——先做第一步")
        result = cdir / "result.mp4"
        if not result.is_file():
            raise CaseError("还没有采用的结果——先在「生成」里采用一轮")
        out = cdir / "out" / (case.adopt_round or "result")
        return vrstudio_argv("recompose", "--in", str(result),
                             "--manifest", str(manifests[0]),
                             "--out", str(out.with_suffix(".mp4")),
                             "--quiet")
    raise CaseError(f"未知操作：{op}")


async def api_run(request):
    name = request.path_params["name"]
    body = await request.json()
    op = body.get("op", "")
    try:
        argv = await asyncio.to_thread(_op_argv, name, op, body)
    except CaseError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    QUEUE.submit(op, name, argv)
    return JSONResponse({"submitted": op, "case": name})


async def api_task(request):
    return JSONResponse(QUEUE.status())


async def api_sources(request):
    vids = sorted(p for p in sources_dir().iterdir()
                  if p.is_file() and p.suffix.lower() in _VIDEO_EXTS)
    return JSONResponse({"dir": str(sources_dir()), "files": [p.name for p in vids]})


async def api_browse(request):
    """本地文件浏览器（register 的返回件 / gen 素材多选；单人本地工具，不设限）。"""
    d = Path(request.query_params.get("path", str(config_mod.REPO_ROOT)))
    if not d.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=400)
    dirs = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
    assets_mode = request.query_params.get("kinds") == "assets"
    allowed = _VIDEO_EXTS | _IMAGE_EXTS if assets_mode else _VIDEO_EXTS
    files = sorted(p for p in d.iterdir()
                   if p.is_file() and p.suffix.lower() in allowed)
    if assets_mode:
        rows = [{"path": str(p), "name": p.name,
                 "kind": "video" if p.suffix.lower() in _VIDEO_EXTS else "image"}
                for p in files[:200]]
    else:
        rows = [str(p) for p in files[:200]]
    return JSONResponse({
        "path": str(d),
        "parent": str(d.parent) if d.parent != d else None,
        "dirs": [p.name for p in dirs[:200]],
        "files": rows,
    })


routes = [
    Route("/", index),
    Route("/case/{name}", wizard),
    Route("/img/{name}/{file}", img),
    Route("/img/asset/{name}/{file}", asset_img),
    Route("/img/keyframe/{name}/{round}/{index}", keyframe_img),
    Route("/video/source/{name}", video_source),
    Route("/video/out/{name}/{file}", video_out),
    Route("/api/cases", api_cases, methods=["GET", "POST"]),
    Route("/api/case/{name}", api_case),
    Route("/api/case/{name}/draft", api_draft, methods=["POST"]),
    Route("/api/case/{name}/prompt", api_prompt, methods=["POST"]),
    Route("/api/case/{name}/params", api_params, methods=["POST"]),
    Route("/api/case/{name}/assets", api_assets, methods=["POST"]),
    Route("/api/case/{name}/derived", api_derived),
    Route("/api/case/{name}/submit", api_submit_set, methods=["GET", "PUT"]),
    Route("/api/case/{name}/extract-frames", api_extract_frames, methods=["POST"]),
    Route("/api/case/{name}/keyframe/run", api_keyframe_run, methods=["POST"]),
    Route("/api/case/{name}/keyframe/rounds", api_keyframe_rounds),
    Route("/api/case/{name}/keyframe/pick", api_keyframe_pick, methods=["POST"]),
    Route("/api/sessions", api_sessions),
    Route("/api/modes", api_modes),
    Route("/api/case/{name}/viewport", api_viewport, methods=["POST"]),
    Route("/api/case/{name}/inner", api_inner, methods=["POST"]),
    Route("/api/case/{name}/nudge", api_nudge, methods=["POST"]),
    Route("/api/case/{name}/reset-draft", api_reset_draft, methods=["POST"]),
    Route("/api/case/{name}/run", api_run, methods=["POST"]),
    Route("/api/task", api_task),
    Route("/api/browse", api_browse),
    Route("/api/sources", api_sources),
]


class NoStoreMiddleware:
    """Cache-Control: no-store：本地工具绝不能端出旧 UI——浏览器对
    dashboard.js 的启发式缓存曾让一个修复整个会话看起来是坏的。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"cache-control", b"no-store"))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def build_app(cases_root: str | Path | None = None) -> Starlette:
    _ctx["cases_root"] = cases_root
    app = Starlette(routes=routes)
    app.add_middleware(NoStoreMiddleware)
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")),
              name="static")
    return app
