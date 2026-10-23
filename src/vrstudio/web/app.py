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
import logging
import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import jinja2
import numpy as np
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from .. import case as case_mod
from .. import config as config_mod
from .. import keyframe as keyframe_mod
from ..case import CaseError
from ..cli.generate import (
    GenError,
    assemble,
    candidates,
    clip_duration_s,
    template_for,
)
from ..media.videoio import VideoIOError
from ..patch.projection import viewport_erp_rects
from ..procenv import decode_text, utf8_env
from ..redraw.manifest import Manifest
from .logging import CLIENT_LOGGER, GEOMETRY_LOGGER, RequestLogMiddleware
from .proxy import (
    ensure_file_proxy,
    ensure_proxy,
    file_proxy,
    file_proxy_state,
    proxy_path,
    proxy_ready,
    proxy_state,
)
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


def _remember_store(name: str, store: PreviewStore) -> None:
    """几何写入走 store 时保住它的解码缓存。

    ``get_store`` 以 case.toml 的 mtime 失效；store 自己写盘会改这个 mtime。
    不在这里同步缓存记录的话，下一次 ``get_store`` 会重建 PreviewStore，
    已解码的 ERP 帧（LRU）随之丢掉，视口微调又要从 8K 源片重新解码。
    """
    mtime = case_mod.case_toml(name, _ctx["cases_root"]).stat().st_mtime
    _stores[name] = (mtime, store)


def _drop_store(name: str) -> None:
    """代理生成完成后丢掉旧 PreviewStore，下一次预览请求切到代理解码。"""
    _stores.pop(name, None)


#: 选区视口的分辨率预设。新增档位只改这张表，前端下拉与后端校验自动跟随。
PATCH_VIEWPORT_OPTIONS = (
    {"label": "1920×1080", "width": 1920, "height": 1080, "aspect": "16:9"},
)


def _viewport_options(case: case_mod.Case) -> list[dict]:
    """可选视口尺寸；当前尺寸不在预设里时也列出来，保证下拉能回显。"""
    vp = case.viewport or {}
    current = (int(vp.get("width") or 0), int(vp.get("height") or 0))
    options = [dict(item) for item in PATCH_VIEWPORT_OPTIONS]
    if (current[0] > 0 and current[1] > 0
            and not any((o["width"], o["height"]) == current for o in options)):
        options.append({"label": f"{current[0]}×{current[1]}",
                        "width": current[0], "height": current[1], "aspect": ""})
    return options


def _footprint_payload(store: PreviewStore) -> list[dict]:
    """视口在 ERP 上的示意框，坐标是 0..1 分数（最多两块，跨经线时）。"""
    vp = store.case.viewport or {}
    try:
        width = int(vp["width"])
        height = int(vp["height"])
        fov = float(vp["fov_h_deg"])
        yaw = float(vp["yaw_deg"])
        pitch = float(vp["pitch_deg"])
    except (KeyError, TypeError, ValueError):
        return []
    erp_w, erp_h = store.source_size
    rects = viewport_erp_rects(width, height, yaw, pitch, fov, erp_w, erp_h)
    return [{"x": x0 / erp_w, "y": y0 / erp_h,
             "w": (x1 - x0) / erp_w, "h": (y1 - y0) / erp_h}
            for x0, y0, x1, y1 in rects]


def _geometry_payload(store: PreviewStore, name: str, **extra) -> dict:
    """几何交互（点全景、摇杆、拖框）的轻量应答。

    这些请求只改 viewport/inner，不需要重扫素材与 4K 原始帧；返回完整 case
    info 会让每次点击都等 4–7 秒，下方预览看起来就像没更新。
    """
    payload = {
        "viewport": dict(store.case.viewport or {}),
        "inner": dict(store.case.inner or {}),
        "previews": {"vp": f"/img/{name}/vp.png?k={store.key}"},
        "viewport_footprint": _footprint_payload(store),
    }
    payload.update(extra)
    return payload


def _log_geometry(request, name: str, event: str, old: dict, new: dict) -> None:
    """记录 pick 草稿的几何变更；请求 id 用来和访问日志对上。"""
    logging.getLogger(GEOMETRY_LOGGER).info("geometry", extra={"data": {
        "event": f"geometry.{event}", "case": name,
        "rid": getattr(request.state, "rid", None),
        "old": old, "new": new,
    }})


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


_NEXT_DIR = _WEB_DIR / "static" / "next"


class NextFrontendFiles(StaticFiles):
    """托管 /next 的 Vite 产物；未匹配的路径回退到 index.html，交给 Vue Router。"""

    async def check_config(self) -> None:
        if self.directory is None or Path(self.directory).exists():
            await super().check_config()

    async def get_response(self, path, scope):
        index_file = Path(self.directory) / "index.html"
        if not index_file.exists():
            return Response(
                "新前端尚未构建：请在 vrstudio/frontend 执行 npm install && npm run build",
                status_code=503,
                media_type="text/plain; charset=utf-8",
            )
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return FileResponse(index_file)
        if response.status_code == 404:
            return FileResponse(index_file)
        return response


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


async def video_proxy(request):
    """源片代理：pick 顶部播放器与对比页左路共用（首次访问时后台生成）。"""
    try:
        case = case_mod.load(request.path_params["name"], _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    if not proxy_ready(case, _ctx["cases_root"]):
        return JSONResponse({"error": "proxy not ready"}, status_code=404)
    return FileResponse(str(proxy_path(case, _ctx["cases_root"])),
                        media_type="video/mp4")


async def video_out_proxy(request):
    """成片的 4K 预览代理：对比页右路；代理没就绪时 404，由前端触发构建。"""
    name = request.path_params["name"]
    fn = request.path_params["file"]
    if "/" in fn or "\\" in fn or ".." in fn:
        return JSONResponse({"error": "bad name"}, status_code=400)
    p = case_mod.case_dir(name, _ctx["cases_root"]) / "out" / fn
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    if file_proxy_state(case, p, _ctx["cases_root"]) != "ready":
        return JSONResponse({"error": "proxy not ready"}, status_code=404)
    fp = file_proxy(case, p, _ctx["cases_root"])
    return FileResponse(str(fp.video), media_type="video/mp4")


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


def _refs_payload(submission) -> dict:
    return submission.payload()


def _prompt_preview_args(name: str, mode: str | None = None) -> tuple[list[str], Path | None]:
    """提示词展开所需的 CLI 参数：几何尺寸 + 提交清单临时文件。

    拿不到候选或几何时不报错，只返回已能拿到的部分；vrgen 会把缺的占位符
    报成 render_error，页面显示具体缺什么，而不是干瞪占位符。
    """
    args: list[str] = []
    refs_file: Path | None = None
    try:
        case = case_mod.load(name, _ctx["cases_root"])
        submission = assemble(case, _ctx["cases_root"], mode=mode)
    except (CaseError, GenError, KeyError, ValueError, OSError):
        return args, None
    args += ["--canvas", f"{submission.canvas_size[0]}x{submission.canvas_size[1]}",
             "--source", f"{submission.source_size[0]}x{submission.source_size[1]}"]
    payload = _refs_payload(submission)
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
    preview_args, refs_file = _prompt_preview_args(name, mode)
    try:
        return _vrgen_json([*argv, *preview_args])
    finally:
        if refs_file is not None:
            refs_file.unlink(missing_ok=True)


def _sessions_tree() -> dict:
    """项目/剧集/生成会话树（只读；契约 case-model.md §4）。"""
    return _vrgen_json(["sessions", "list"])


def _generation_params(name: str) -> dict:
    """生成页的输出/输入分辨率选项（决策 20/34；契约 case-model.md §4）。

    先不带 ``--refs`` 读模式与当前草稿；提交集齐了才带上清单计算档位。这样
    纯图片 case 与空白 case 都不会撞上 `params show` 的“不接受输入视频”闸门。
    """
    base = _vrgen_json(["params", "show", "--job", name,
                        "--workdir", str(_workdir())])
    if "error" in base:
        return base
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return base
    mode = (base.get("current") or {}).get("mode")
    auto_duration = (clip_duration_s(case, _ctx["cases_root"])
                     if case.flow == "patch" and mode == "firstLastFrame" else None)
    if auto_duration is not None:
        base["auto_duration_s"] = auto_duration
        fixed = dict(base.get("fixed") or {})
        fixed["duration_s"] = auto_duration
        base["fixed"] = fixed
    refs_file: Path | None = None
    try:
        submission = assemble(case, _ctx["cases_root"], mode=mode)
        fd, raw = tempfile.mkstemp(prefix=f"vrgen-params-{name}-", suffix=".json")
        os.close(fd)
        refs_file = Path(raw)
        refs_file.write_text(
            json.dumps(submission.payload(), ensure_ascii=False, indent=1),
            encoding="utf-8")
        payload = _vrgen_json([
            "params", "show", "--job", name,
            "--refs", str(refs_file),
            "--canvas", f"{submission.canvas_size[0]}x{submission.canvas_size[1]}",
            "--source", f"{submission.source_size[0]}x{submission.source_size[1]}",
            "--workdir", str(_workdir())])
        if auto_duration is not None:
            payload["auto_duration_s"] = auto_duration
            fixed = dict(payload.get("fixed") or {})
            fixed["duration_s"] = auto_duration
            payload["fixed"] = fixed
        return payload
    except (CaseError, GenError, KeyError, ValueError, OSError):
        return base
    finally:
        if refs_file is not None:
            refs_file.unlink(missing_ok=True)


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
    """成片列表。patch 按版本选一个：``_frame.mp4`` 优先，缺失时回退 ``_space.mp4``。

    旧命名 ``<版本>.mp4`` 不进入回退链（由一次性迁移脚本改名）。
    ``partial=True`` 表示只有空间合并段、时间轴不是整片，页面必须标注"仅中间段"。
    """
    odir = case.dir(_ctx["cases_root"]) / "out"
    if not odir.is_dir():
        return []
    files = sorted(odir.glob("*.mp4"))
    if case.flow != "patch":
        return [{"file": p.name, "url": f"/video/out/{case.name}/{p.name}",
                 "proxy_url": f"/video/outproxy/{case.name}/{p.name}",
                 "proxy_state": file_proxy_state(case, p, _ctx["cases_root"])}
                for p in files]
    chosen: dict[str, tuple[str, Path]] = {}
    for p in files:
        stem = p.stem
        if stem.endswith("_frame"):
            chosen[stem[:-len("_frame")]] = ("frame", p)
        elif stem.endswith("_space"):
            version = stem[:-len("_space")]
            if chosen.get(version, ("", None))[0] != "frame":
                chosen[version] = ("space", p)
    out = []
    for version in sorted(chosen):
        kind, path = chosen[version]
        partial = kind == "space" and not _is_whole_extract(case, version)
        out.append({"file": path.name, "url": f"/video/out/{case.name}/{path.name}",
                    "proxy_url": f"/video/outproxy/{case.name}/{path.name}",
                    "proxy_state": file_proxy_state(case, path, _ctx["cases_root"]),
                    "version": version, "partial": partial})
    return out


def _video_frames(path: Path) -> int | None:
    """源片帧数；读不到返回 None（只影响"是否整片"的标注，不报错）。"""
    try:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return None
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        return frames if frames > 0 else None
    except Exception:                                    # noqa: BLE001
        return None


def _is_whole_extract(case: case_mod.Case, version: str) -> bool:
    """抽取区间是否覆盖整条源片（对比页据此决定是否标注"仅中间段"）。"""
    from ..patch.case import load_sidecar_single_segment

    sidecar = case.dir(_ctx["cases_root"]) / "extracts" / version / "clip.json"
    try:
        seg = load_sidecar_single_segment(sidecar)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not case.source_path:
        return False
    frames = _video_frames(Path(case.source_path))
    if frames is None:
        return False
    return seg.frame_start <= 0 and seg.frame_end >= frames - 1


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
    """候选卡片的数据源：流程母版（clip/pad）+ 文件素材；文件缺失也照常返回，页面标红。

    ``label`` 按当前顺序重新编号（@Video1/@Image1），不能拿素材 id 的数字代替：
    拖动排序只改数组顺序，id 保持不变。流程产物不可改角色、不可排序删除。
    """
    out = []
    counters = {"video": 0, "image": 0}
    for cand in candidates(case, _ctx["cases_root"]):
        counters[cand.kind] += 1
        prefix = "Video" if cand.kind == "video" else "Image"
        info = _file_info(cand.path)
        meta = _media_meta(cand.path, cand.kind)
        out.append({
            "id": cand.id, "kind": cand.kind, "role": cand.role,
            "label": f"@{prefix}{counters[cand.kind]}", "path": cand.display,
            "source": cand.source,
            "role_fixed": cand.role_fixed,
            "url": f"/img/asset/{case.name}/{cand.id}",
            **info, **meta,
        })
    return out


DEFAULT_KEYFRAME_PRESERVE = (
    "机位、构图、透视、场景建筑、背景人物、光照与色温保持图1原样；"
    "除指定人物外不新增、不删除任何物体。")


def _pick_row(pick: keyframe_mod.Pick | None, ids: set[str]) -> dict | None:
    """指针只有在素材仍在库里时才有效；素材被删后按“未定稿”处理。"""
    if pick is None or pick.asset not in ids:
        return None
    return {"round": pick.round, "index": pick.index, "asset": pick.asset}


def _keyframe_state(case: case_mod.Case) -> dict:
    """首尾帧工作台的只读状态：绑定关系、两帧指针、是否过期。

    图片步的输入全部从这里与原始帧解析，不读 ``[submit] selected``；视频提交集
    仍只由素材侧边栏的勾选决定。"""
    base = {"enabled": case.flow in ("patch", "gen"), "error": None,
            "sheet": None, "sheet_candidates": [], "first": None, "last": None,
            "extract": None, "stale": False, "complete": False}
    if case.flow not in ("patch", "gen"):
        return base
    try:
        state = keyframe_mod.load(case.dir(_ctx["cases_root"]))
    except keyframe_mod.KeyframeError as exc:
        base["error"] = str(exc)
        return base
    details = _asset_details(case)
    sheets = [d for d in details
              if d["kind"] == "image" and d["role"] == "subject_ref" and d.get("exists")]
    sheet_ids = {d["id"] for d in sheets}
    sheet = state.sheet if state.sheet in sheet_ids else None
    if state.sheet is None and len(sheets) == 1:
        sheet = sheets[0]["id"]           # 唯一一张三视图：默认绑定，不写文件
    first = _pick_row(state.first, {d["id"] for d in details
                                    if d["role"] == "first_frame"})
    last = _pick_row(state.last, {d["id"] for d in details
                                  if d["role"] == "last_frame"})
    stale = bool(state.extract and case.extract_current
                 and state.extract != case.extract_current)
    base.update({
        "sheet": sheet,
        "sheet_candidates": [{"id": d["id"], "label": d["label"], "path": d["path"]}
                             for d in sheets],
        "first": first, "last": last, "extract": state.extract,
        "stale": stale,
        "complete": bool(first and last and not stale),
    })
    return base


def _raw_frames(case: case_mod.Case) -> list[dict]:
    """patch 的当前 extract 导出的首尾原始帧：只进工作台输入区，不是提交候选。"""
    if case.flow != "patch" or not case.extract_current:
        return []
    d = case.dir(_ctx["cases_root"]) / "extracts" / case.extract_current
    rows = []
    for asset_id, role, name, label in (
            ("first_raw", "first_frame_raw", "first_frame_raw.png", "首帧原始帧"),
            ("last_raw", "last_frame_raw", "last_frame_raw.png", "尾帧原始帧")):
        path = d / name
        exists = path.is_file()
        meta = _media_meta(path, "image") if exists else {}
        rows.append({"id": asset_id, "role": role, "label": label,
                     "path": str(path), "exists": exists,
                     "width": meta.get("width"), "height": meta.get("height"),
                     "size": path.stat().st_size if exists else None,
                     "url": f"/img/asset/{case.name}/{asset_id}"})
    return rows


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
    frame = cdir / "out" / f"{case.extract_current}_frame.mp4"
    space = cdir / "out" / f"{case.extract_current}_space.mp4"
    outs = [frame] if frame.is_file() else ([space] if space.is_file() else [])
    # 首尾原始帧是这一步的产物，直接追加到已有的输出列表；它们只给工作台
    # 当输入底图，不是提交候选，不需要再拆一组。
    steps["extract"] = {
        "inputs": [_file_info(source)],
        "outputs": [_file_info(clip), _file_info(mask), _file_info(sidecar),
                    _file_info(d / "first_frame_raw.png"),
                    _file_info(d / "last_frame_raw.png")],
    }
    steps["generate"] = _generation_groups(
        case, [_file_info(clip), _file_info(mask), _file_info(sidecar)], ai)
    steps["merge"] = {
        "inputs": [_file_info(source), _file_info(ai),
                   _file_info(sidecar), _file_info(clip)],
        "outputs": [_file_info(p) for p in outs],
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
    partial_names = [o["file"] for o in outs if o.get("partial")]
    partial_note = (f"；{'、'.join(partial_names)} 仅中间段（时间拼接未完成）"
                    if partial_names else "")
    if case.flow == "patch":
        cur = cdir / "extracts" / (case.extract_current or "")
        kf = _keyframe_state(case)
        raw_ok = all((cur / name).is_file()
                     for name in ("first_frame_raw.png", "last_frame_raw.png"))
        if not extracted:
            kf_reason = "先完成选区并运行抽取；首尾帧来自 clip 的首末帧"
        elif not raw_ok:
            kf_reason = "当前抽取版本没有两张原始帧；重新抽取一次"
        elif kf["error"]:
            kf_reason = kf["error"]
        elif not kf["sheet"]:
            kf_reason = ("素材库有多张人物三视图，先在首尾帧步骤选定一张"
                         if len(kf["sheet_candidates"]) > 1
                         else "素材库缺少人物三视图；先在素材区添加一张 subject_ref 图片")
        elif kf["stale"]:
            kf_reason = "抽取版本已更新，需要重新定稿两帧"
        elif not kf["first"]:
            kf_reason = "首帧还没定稿；在首尾帧步骤生成并挑选一张"
        elif not kf["last"]:
            kf_reason = "首帧已定，接着生成并挑选尾帧"
        else:
            kf_reason = ""
        kf_ok = bool(extracted and raw_ok and kf["complete"])
        steps = {            "pick": {"ok": missing is None,
                     "reason": f"case.toml 里缺 [{missing}] 段，补齐后才能抽取" if missing else "",
                     "evidence": f"草稿三段（viewport/inner/frames）都在 {case_mod.case_toml(case.name, _ctx['cases_root'])} 里"},
            "extract": {"ok": extracted,
                        "reason": "" if extracted else "先完成选区并运行抽取",
                        "evidence": f"在 {cur}\\ 检测到 clip.json / clip.mp4 / clip_mask.png，当前抽取版本是 {case.extract_current}；想重新抽取，调好选区后再点一次按钮（会生成新版本，旧版本不动）"},
            "keyframe": {"ok": kf_ok, "ready": extracted and raw_ok,
                         "reason": kf_reason,
                         "evidence": (f"在 {cur}\\ 检测到两张原始帧；keyframe.toml 指向首帧 "
                                      f"{kf['first']['asset']} 与尾帧 {kf['last']['asset']}"
                                      if kf_ok else "")},
            "generate": {"ok": extracted and (adopted or any(
                e["has_result"] for e in extracts)),
                "reason": "" if extracted else "抽取之后才能送生成",
                "evidence": (f"在 {cur}\\result.mp4 检测到已选用的结果（来自 Session {case.adopt_round}）"
                             if adopted else f"在 {cur}\\result.mp4 检测到结果"),
                "ready": extracted},
            "merge": {"ok": has_out, "ready": extracted and adopted,
                      "reason": "" if has_out else "采用一次生成结果后，才能合并出片",
                      "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}；每次合并都会覆盖同名文件{partial_note}"},
            "compare": {"ok": has_out, "reason": "" if has_out else "合并出片后可以在这里对比",
                        "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}{partial_note}"},
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
    """不参与提交的只读条目：源视频、几何契约、manifest、已采用结果。

    clip/pad 已是可勾选候选（见 ``_asset_details``），mask 不出现在面板里。
    """
    rows: list[dict] = []
    cdir = case.dir(_ctx["cases_root"])
    if case.source_path:
        rows.append({
            "kind": "video", "role": "source", "label": "源视频",
            "editable": False, "submit": "流程输入，不作为生成候选",
            **_file_info(Path(case.source_path)),
        })
    if case.flow == "patch" and case.extract_current:
        d = cdir / "extracts" / case.extract_current
        for name, kind, label, submit in (
                ("clip.json", "file", "几何契约", "不提交"),
                ("result.mp4", "video", "已采用结果", "流程产物，不提交")):
            rows.append({"kind": kind, "role": "derived", "label": label,
                         "editable": False, "submit": submit,
                         **_file_info(d / name)})
    elif case.flow == "fullredraw":
        manifests = sorted(cdir.glob("*_pad.json"))
        for name, kind, label, submit in (
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
        "fps": store.fps if store else None,
        "duration_s": store.duration_s if store else None,
        "proxy": ({"state": proxy_state(case, _ctx["cases_root"]),
                   "url": f"/video/proxy/{case.name}"}
                  if case.flow in ("patch", "fullredraw") and case.source_path
                  else None),
        "extract_current": case.extract_current,
        "adopt_round": case.adopt_round,
        "assets": _asset_details(case),
        "raw_frames": _raw_frames(case),
        "keyframe": _keyframe_state(case),
        "submit_selected": case.submit_selected,
        "derived": _derived(case),
        "extracts": extracts,
        "outputs": _outputs(case),
        "step_files": _step_files(case),
        "gates": _gates(case, extracts),
        "previews": {"vp": f"/img/{case.name}/vp.png?k={store.key}"} if store else {},
        "viewport_options": _viewport_options(case) if case.flow == "patch" else [],
        "viewport_footprint": _footprint_payload(store) if store else [],
        "rounds": _rounds(case.name),
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
    case_toml = case_mod.case_dir(name, _ctx["cases_root"]) / "case.toml"
    try:
        updated_at = datetime.fromtimestamp(case_toml.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M")
    except OSError:
        updated_at = None
    return {"name": case.name, "flow": case.flow,
            "gates": _gates(case, extracts),
            "updated_at": updated_at,
            "outputs": len(_outputs(case)),
            "owner": None,
            "task_status": None}


def _is_16_9(width: int, height: int) -> bool:
    ratio = width / height if height else 0.0
    return abs(ratio - 16 / 9) <= (16 / 9) * 0.001


def _selected_candidates(case: case_mod.Case) -> list:
    ids = case.submit_selected or []
    by_id = {c.id: c for c in candidates(case, _ctx["cases_root"])}
    return [by_id[item] for item in ids if item in by_id]


def _mode_ready(case: case_mod.Case, mode: str | None) -> tuple[bool, str]:
    """提交前按当前模式检查；页面用它决定生成按钮是否可用（决策 33）。"""
    chosen = _selected_candidates(case)
    if not chosen:
        return False, "没有勾选任何素材——至少勾选一项再生成"
    if mode == "firstLastFrame":
        firsts = [c for c in chosen if c.role == "first_frame"]
        lasts = [c for c in chosen if c.role == "last_frame"]
        if len(firsts) != 1 or len(lasts) != 1:
            return False, ("firstLastFrame 需要勾选恰好一张首帧定稿图与一张尾帧定稿图"
                           f"（当前 {len(firsts)} 张首帧、{len(lasts)} 张尾帧）；"
                           "到「首尾帧」步骤生成并挑选两张定稿图")
        extras = [c.id for c in chosen if c.role not in ("first_frame", "last_frame")]
        if extras:
            return False, f"firstLastFrame 只用首尾定稿图；请取消勾选：{extras}"
        if case.flow == "patch":
            vp = case.viewport or {}
            width, height = int(vp.get("width") or 0), int(vp.get("height") or 0)
            if width and height and not _is_16_9(width, height):
                return False, "patch viewport 不是 16:9；firstLastFrame 首期只接受 16:9"
        return True, ""
    if mode in ("videoEdit", "videoExtend"):
        if case.flow == "gen" and not any(c.kind == "video" for c in chosen):
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
    """候选缩略图：视频取首帧，图片直接缩放；每次请求现算，不设缓存。

    同时服务流程母版（clip/pad）与工作台的首尾原始帧——它们的 id 不在
    ``[[assets]]`` 里，所以先按候选/原始帧解析，再退回文件素材。
    """
    name = request.path_params["name"]
    file_id = request.path_params["file"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "not found"}, status_code=404)
    path = None
    kind = "image"
    for cand in candidates(case, _ctx["cases_root"]):
        if cand.id == file_id:
            path, kind = cand.path, cand.kind
            break
    if path is None:
        for row in _raw_frames(case):
            if row["id"] == file_id:
                path, kind = Path(row["path"]), "image"
                break
    if path is None:
        try:
            asset = case.asset(file_id)
        except CaseError:
            return JSONResponse({"error": "not found"}, status_code=404)
        path, kind = asset.resolved(case, _ctx["cases_root"]), asset.kind
    if path is None or not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        data = await asyncio.to_thread(_thumb, path, kind)
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
    """已退役：extract 阶段直接产出首尾原始帧（决策 33），工作台不再单独抽帧。"""
    return JSONResponse({"error": "抽帧已并入 extract；重新 extract 或直接使用当前 extract 的原始帧"},
                        status_code=410)


def _keyframe_ratio(case: case_mod.Case) -> str:
    if case.flow == "patch":
        vp = case.viewport or {}
        if not _is_16_9(int(vp.get("width") or 0), int(vp.get("height") or 0)):
            raise CaseError("patch viewport 不是 16:9；首期工作台只接受 16:9")
    return "16:9"


#: 工作台的首尾原始帧：由 extract 写入当前版本目录，不是 ``[[assets]]``。
RAW_FRAME_FILES = {"first_raw": "first_frame_raw.png",
                   "last_raw": "last_frame_raw.png"}


KEYFRAME_VARS = "keyframe-vars.toml"
KEYFRAME_TEMPLATES = {"first": "keyframe-first", "last": "keyframe-last"}


def _keyframe_step(value: object) -> str:
    step = str(value or "first").strip()
    if step not in KEYFRAME_TEMPLATES:
        raise CaseError("首尾帧步骤只能是 first 或 last")
    return step


def _current_session(name: str) -> int:
    """图片生成按项目会话计费：读 generation.toml 的当前会话；读不到就报错。"""
    payload = _vrgen_json(["params", "show", "--job", name,
                           "--workdir", str(_workdir())])
    if "error" in payload:
        raise CaseError(payload["error"])
    return int((payload.get("current") or {}).get("session_id") or 0)


def _keyframe_inputs(case: case_mod.Case, step: str, *,
                     require_first: bool = True) -> list[tuple[Path, str]]:
    """图片步的固定参考图：原始帧 + 绑定的三视图（+ 尾帧的首帧定稿）。

    这里不读 ``[submit] selected``：视频提交集与图片步是两份独立的输入声明。"""
    state = _keyframe_state(case)
    if state["error"]:
        raise CaseError(state["error"])
    if state["stale"]:
        raise CaseError("抽取版本已更新，需要重新定稿两帧")
    if not state["sheet"]:
        raise CaseError(
            "素材库有多张人物三视图，先在首尾帧步骤选定一张"
            if len(state["sheet_candidates"]) > 1
            else "素材库缺少人物三视图；先添加一张 subject_ref 图片")
    if not case.extract_current:
        raise CaseError("工作台需要 patch 的当前 extract（首尾原始帧随 extract 产出）")
    raw_dir = case.dir(_ctx["cases_root"]) / "extracts" / case.extract_current
    sheet = case.asset(state["sheet"]).resolved(case, _ctx["cases_root"])
    if step == "first":
        pairs = [(raw_dir / RAW_FRAME_FILES["first_raw"], "first_frame_raw"),
                 (sheet, "subject_ref")]
    else:
        first = state["first"]
        if not first and require_first:
            raise CaseError("先生成并挑选首帧定稿，再生成尾帧")
        pairs = [(raw_dir / RAW_FRAME_FILES["last_raw"], "last_frame_raw"),
                 (sheet, "subject_ref")]
        if first:
            first_path = case.asset(first["asset"]).resolved(case, _ctx["cases_root"])
            pairs.append((first_path, "first_frame"))
    for path, _role in pairs:
        if not path.is_file():
            raise CaseError(f"参考图不存在：{path}")
    return pairs


def _keyframe_argv(name: str, body: dict) -> list[str]:
    case = case_mod.load(name, _ctx["cases_root"])
    if case.flow != "patch":
        raise CaseError("首尾帧工作台首期只接 patch（gen 的入口待上传原始帧的入口）")
    step = _keyframe_step(body.get("step"))
    pairs = _keyframe_inputs(case, step)
    session = _current_session(name)
    if session <= 0:
        raise CaseError("图片生成按项目会话计费；先在底栏选择项目生成会话")
    argv = [_vrgen_exe(), "image", "submit", "--job", name,
            "--template", KEYFRAME_TEMPLATES[step],
            "--vars-file", KEYFRAME_VARS,
            "--step", step,
            "--session-id", str(session),
            "--ratio", _keyframe_ratio(case),
            "--workdir", str(_workdir())]
    for path, role in pairs:
        argv += ["--image", str(path), "--image-role", role]
    if body.get("model_id") is not None:
        argv += ["--model-id", str(body["model_id"])]
    if body.get("resolution"):
        argv += ["--resolution", str(body["resolution"])]
    if body.get("num") is not None:
        argv += ["--num", str(body["num"])]
    return argv


def _keyframe_prompt(name: str, step: str) -> dict:
    """读图片步的提示词草稿并展开；首次访问把四个变量写成空值/通用默认。

    尾帧预览在首帧定稿前也允许读取（少图3 的分工说明），否则四个字段里
    「尾帧姿态」的显示名与说明要等首帧挑完才能出现。"""
    case = case_mod.load(name, _ctx["cases_root"])
    step = _keyframe_step(step)
    pairs = _keyframe_inputs(case, step, require_first=False)
    vars_path = _workdir() / name / KEYFRAME_VARS
    if not vars_path.is_file():
        seed = ["prompt", "save", "--job", name,
                "--template", KEYFRAME_TEMPLATES[step],
                "--vars-file", KEYFRAME_VARS,
                "--set", "figure=", "--set", "pose_first=",
                "--set", "pose_last=",
                "--set", f"preserve={DEFAULT_KEYFRAME_PRESERVE}",
                "--workdir", str(_workdir())]
        seeded = _vrgen_json(seed)
        if "error" in seeded:
            raise CaseError(seeded["error"])
    argv = ["prompt", "show", "--job", name,
            "--template", KEYFRAME_TEMPLATES[step],
            "--vars-file", KEYFRAME_VARS,
            "--workdir", str(_workdir())]
    for _path, role in pairs:
        argv += ["--image-role", role]
    payload = _vrgen_json(argv)
    if "error" in payload:
        raise CaseError(payload["error"])
    return payload


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
        params = blob.get("params") or {}
        rows.append({"round": directory.name, "key": blob.get("key", ""),
                     "stage": blob.get("stage", ""),
                     "step": str(params.get("step") or ""),
                     "model_id": params.get("model_id"),
                     "num": params.get("num"),
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


def _keyframe_save_prompt(name: str, step: str, sets: dict) -> dict:
    case = case_mod.load(name, _ctx["cases_root"])
    step = _keyframe_step(step)
    pairs = _keyframe_inputs(case, step)
    argv = ["prompt", "save", "--job", name,
            "--template", KEYFRAME_TEMPLATES[step],
            "--vars-file", KEYFRAME_VARS,
            "--workdir", str(_workdir())]
    for key, value in sets.items():
        argv += ["--set", f"{key}={value}"]
    for _path, role in pairs:
        argv += ["--image-role", role]
    payload = _vrgen_json(argv)
    if "error" in payload:
        raise CaseError(payload["error"])
    return payload


async def api_keyframe_sheet(request):
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    if request.method == "GET":
        state = _keyframe_state(case)
        return JSONResponse({"sheet": state["sheet"],
                             "candidates": state["sheet_candidates"]})
    body = await request.json()
    asset_id = str(body.get("asset") or "").strip()
    if asset_id:
        try:
            asset = case.asset(asset_id)
        except CaseError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if asset.kind != "image" or asset.role != "subject_ref":
            return JSONResponse({"error": "图片步绑定的三视图必须是 subject_ref 图片"},
                                status_code=400)
    try:
        state = keyframe_mod.load(case.dir(_ctx["cases_root"]))
        state.sheet = asset_id or None
        keyframe_mod.save(case.dir(_ctx["cases_root"]), state)
    except (keyframe_mod.KeyframeError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(await asyncio.to_thread(_info_with_generation, name))


async def api_keyframe_prompt(request):
    name = request.path_params["name"]
    if request.method == "GET":
        step = request.query_params.get("step") or "first"
        try:
            payload = await asyncio.to_thread(_keyframe_prompt, name, step)
        except (CaseError, OSError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(payload)
    body = await request.json()
    step = body.get("step") or "first"
    sets = body.get("vars") or {}
    if not isinstance(sets, dict):
        return JSONResponse({"error": "vars 必须是对象"}, status_code=400)
    allowed = {"figure", "pose_first", "pose_last", "preserve"}
    bad = sorted(set(sets) - allowed)
    if bad:
        return JSONResponse({"error": f"这些变量不能改：{bad}"}, status_code=400)
    try:
        payload = await asyncio.to_thread(
            _keyframe_save_prompt, name, str(step), {k: str(v) for k, v in sets.items()})
    except (CaseError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(payload)


async def api_keyframe_handoff(request):
    name = request.path_params["name"]

    def _do() -> dict:
        case = case_mod.load(name, _ctx["cases_root"])
        state = _keyframe_state(case)
        if state["error"]:
            raise CaseError(state["error"])
        if state["stale"]:
            raise CaseError("抽取版本已更新，需要重新定稿两帧")
        if not state["first"] or not state["last"]:
            raise CaseError("首帧与尾帧都定稿后才能交给视频")
        _keyframe_ratio(case)
        error = _save_gen_mode(name, "firstLastFrame")
        if error:
            raise CaseError(error)
        case_mod.set_submit_selected(
            name, [state["first"]["asset"], state["last"]["asset"]],
            _ctx["cases_root"])
        return _info_with_generation(name)

    try:
        info = await asyncio.to_thread(_do)
    except (CaseError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(info)


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
        if role == "last_frame":
            # 尾帧必须与当前首帧同宽高；首帧换新时旧尾帧会被废弃，不拿它比。
            current = case_mod.load(name, _ctx["cases_root"])
            first = next((a for a in current.assets if a.role == "first_frame"), None)
            if first is not None:
                other = await asyncio.to_thread(
                    _media_meta, first.resolved(current, _ctx["cases_root"]), "image")
                if (int(other.get("width") or 0),
                        int(other.get("height") or 0)) != (width, height):
                    return JSONResponse(
                        {"error": "首帧与尾帧宽高不一致，请重新出图"}, status_code=400)
        info = await asyncio.to_thread(_keyframe_accept, name, role, round_name,
                                       index, target)
    except (CaseError, OSError, VideoIOError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        target.unlink(missing_ok=True)
    return JSONResponse(info)


def _keyframe_accept(name: str, role: str, round_name: str, index: int,
                     target: Path) -> dict:
    """落素材、更新 keyframe.toml；首帧换新时清掉旧尾帧并自动排队尾帧批次。"""
    case = case_mod.load(name, _ctx["cases_root"])
    cdir = case.dir(_ctx["cases_root"])
    state = keyframe_mod.load(cdir)
    state.extract = case.extract_current
    old_last = state.last
    same_first = (role == "first_frame" and state.first is not None
                  and state.first.round == round_name and state.first.index == index)
    updated = case_mod.add_assets(name, [(target, role)], _ctx["cases_root"])
    new_id = updated.assets[-1].id
    if role == "first_frame":
        state.first = keyframe_mod.Pick(round_name, index, new_id)
        if not same_first:
            state.last = None
        keyframe_mod.save(cdir, state)
        if old_last is not None and not same_first:
            selected = [x for x in (updated.submit_selected or [])
                        if x != old_last.asset]
            if selected != (updated.submit_selected or []):
                case_mod.set_submit_selected(name, selected, _ctx["cases_root"])
        queued, hint = False, "首帧已定稿"
        if not same_first:
            try:
                payload = _keyframe_prompt(name, "last")
                values = payload.get("vars") or {}
                if (not str(values.get("figure") or "").strip()
                        or not str(values.get("pose_last") or "").strip()):
                    hint = ("首帧已定稿；尾帧提示词还缺人物外观或尾帧姿态，"
                            "补齐后点「生成尾帧候选」")
                else:
                    argv = _keyframe_argv(name, {"step": "last"})
                    QUEUE.submit("keyframe", name, argv)
                    queued, hint = True, "首帧已定稿；尾帧已自动开始生成"
            except CaseError as exc:
                hint = f"首帧已定稿；尾帧没有自动开始：{exc}"
    else:
        state.last = keyframe_mod.Pick(round_name, index, new_id)
        keyframe_mod.save(cdir, state)
        queued, hint = False, "尾帧已定稿"
    info = _info_with_generation(name)
    info["keyframe_queued"] = queued
    info["keyframe_hint"] = hint
    return info


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
    preview_args, refs_file = _prompt_preview_args(name, mode)
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
    name = request.path_params["name"]
    store = get_store(name)
    if store is None:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    old = dict(store.case.viewport or {})
    await asyncio.to_thread(store.set_viewport_from_erp_click,
                            float(body["fx"]), float(body["fy"]))
    _remember_store(name, store)
    _log_geometry(request, name, "viewport", old, dict(store.case.viewport or {}))
    return JSONResponse(_geometry_payload(store, name))


async def api_viewport_size(request):
    """切换选区视口分辨率；档位由 PATCH_VIEWPORT_OPTIONS 提供。"""
    name = request.path_params["name"]
    store = get_store(name)
    if store is None:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    try:
        width = int(body.get("width") or 0)
        height = int(body.get("height") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "width/height 必须是整数"}, status_code=400)
    options = _viewport_options(store.case)
    if not any(o["width"] == width and o["height"] == height for o in options):
        available = "、".join(f"{o['width']}×{o['height']}" for o in options)
        return JSONResponse(
            {"error": f"不支持的视口尺寸 {width}×{height}；可用：{available}"},
            status_code=400)
    old = dict(store.case.viewport or {})
    try:
        await asyncio.to_thread(store.set_viewport_size, width, height)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _remember_store(name, store)
    _log_geometry(request, name, "viewport_size", old, dict(store.case.viewport or {}))
    return JSONResponse(_geometry_payload(store, name))


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
    old = dict(store.case.inner or {})
    await asyncio.to_thread(store.set_inner, x, y, w, h)
    _remember_store(name, store)
    _log_geometry(request, name, "inner", old, dict(store.case.inner or {}))
    return JSONResponse(_geometry_payload(store, name))


async def api_nudge(request):
    name = request.path_params["name"]
    store = get_store(name)
    if store is None:
        return JSONResponse({"error": "no such case"}, status_code=404)
    body = await request.json()
    old = dict(store.case.viewport or {})
    yaw, pitch = await asyncio.to_thread(
        store.nudge, float(body.get("dyaw", 0.0)), float(body.get("dpitch", 0.0)))
    _remember_store(name, store)
    _log_geometry(request, name, "nudge", old, dict(store.case.viewport or {}))
    return JSONResponse(_geometry_payload(
        store, name, nudged={"yaw": yaw, "pitch": pitch}))


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
    store = get_store(name)
    if store is None:
        return JSONResponse({"error": "no such case"}, status_code=404)
    return JSONResponse(_geometry_payload(store, name))


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


def _assemble_followup(name: str) -> list[tuple[str, str, list[str]]]:
    """merge 成功后的时间拼接任务；整片抽取不排（决策 31/32）。"""
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return []
    if case.flow != "patch" or not case.extract_current:
        return []
    if _is_whole_extract(case, case.extract_current):
        return []
    argv = vrstudio_argv("assemble", "--case", name,
                         "--cases-dir", str(_root()))
    return [("assemble", name, argv)]


async def api_run(request):
    name = request.path_params["name"]
    body = await request.json()
    op = body.get("op", "")
    try:
        argv = await asyncio.to_thread(_op_argv, name, op, body)
    except CaseError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    follow = await asyncio.to_thread(_assemble_followup, name) if op == "merge" else None
    if follow:
        QUEUE.submit(op, name, argv, follow_up=follow)
    else:
        QUEUE.submit(op, name, argv)
    return JSONResponse({"submitted": op, "case": name})


async def api_task(request):
    return JSONResponse(QUEUE.status())


_CLIENT_LOG_MAX = 16 * 1024
_CLIENT_TEXT_LIMITS = {
    "message": 4000, "detail": 4000, "source": 1000,
    "stack": 4000, "url": 1000, "ua": 500, "context": 200, "case": 200,
}
_client_log = logging.getLogger(CLIENT_LOGGER)


async def api_client_log(request):
    """浏览器端错误入口：只写 client-*.jsonl，不混进服务日志。

    浏览器用 ``navigator.sendBeacon`` 或 ``fetch(keepalive)`` 发送；字段全部
    截断，避免一条异常堆栈把日志撑爆。重复上报由前端 helper 去重。
    """
    raw_len = request.headers.get("content-length")
    if raw_len:
        try:
            if int(raw_len) > _CLIENT_LOG_MAX:
                return Response(status_code=413)
        except ValueError:
            pass
    raw = await request.body()
    if len(raw) > _CLIENT_LOG_MAX:
        return Response(status_code=413)
    try:
        body = json.loads(raw)
    except ValueError:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "object expected"}, status_code=400)

    name = str(body.get("level") or "error").lower()
    level = {"error": logging.ERROR, "warn": logging.WARNING,
             "warning": logging.WARNING, "info": logging.INFO}.get(
                 name, logging.ERROR)
    data = {"event": "client.error", "cid": uuid.uuid4().hex[:12]}
    for key, limit in _CLIENT_TEXT_LIMITS.items():
        value = body.get(key)
        if value is not None:
            data[key] = str(value)[:limit]
    data.setdefault("message", "unknown client error")
    for key in ("line", "col", "code"):
        value = body.get(key)
        if value is None:
            continue
        try:
            data[key] = int(value)
        except (TypeError, ValueError):
            data[key] = str(value)[:100]
    _client_log.log(level, "client.error", extra={"data": data})
    return Response(status_code=204)


async def api_proxy(request):
    """预览代理：源片（pick/对比左路）或指定成片（对比右路）。

    POST 缺代理时启动一次后台生成，GET 只查状态。``file`` 指向 ``out/`` 下的
    成片文件名；省略时是源片代理。
    """
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    file = request.query_params.get("file")
    if file is None and request.method == "POST":
        try:
            body = await request.json()
        except ValueError:
            body = {}
        file = str(body.get("file") or "")
    if file:
        if "/" in file or "\\" in file or ".." in file:
            return JSONResponse({"error": "bad file name"}, status_code=400)
        target = case.dir(_ctx["cases_root"]) / "out" / file
        if not target.is_file():
            return JSONResponse({"error": f"out/ 里没有 {file}"}, status_code=404)
        if request.method == "POST":
            state = await asyncio.to_thread(
                ensure_file_proxy, case, target, _ctx["cases_root"], _drop_store)
        else:
            state = await asyncio.to_thread(
                file_proxy_state, case, target, _ctx["cases_root"])
        return JSONResponse({"state": state,
                             "url": f"/video/outproxy/{name}/{file}"})
    if request.method == "POST":
        state = await asyncio.to_thread(
            ensure_proxy, case, _ctx["cases_root"], _drop_store)
    else:
        state = await asyncio.to_thread(
            proxy_state, case, _ctx["cases_root"])
    return JSONResponse({"state": state, "url": f"/video/proxy/{name}"})


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
    Route("/video/proxy/{name}", video_proxy),
    Route("/video/out/{name}/{file}", video_out),
    Route("/video/outproxy/{name}/{file}", video_out_proxy),
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
    Route("/api/case/{name}/keyframe/sheet", api_keyframe_sheet,
          methods=["GET", "PUT"]),
    Route("/api/case/{name}/keyframe/prompt", api_keyframe_prompt,
          methods=["GET", "PUT"]),
    Route("/api/case/{name}/keyframe/handoff", api_keyframe_handoff,
          methods=["POST"]),
    Route("/api/sessions", api_sessions),
    Route("/api/modes", api_modes),
    Route("/api/case/{name}/viewport", api_viewport, methods=["POST"]),
    Route("/api/case/{name}/viewport-size", api_viewport_size, methods=["POST"]),
    Route("/api/case/{name}/inner", api_inner, methods=["POST"]),
    Route("/api/case/{name}/nudge", api_nudge, methods=["POST"]),
    Route("/api/case/{name}/reset-draft", api_reset_draft, methods=["POST"]),
    Route("/api/case/{name}/run", api_run, methods=["POST"]),
    Route("/api/case/{name}/proxy", api_proxy, methods=["GET", "POST"]),
    Route("/api/task", api_task),
    Route("/api/client-log", api_client_log, methods=["POST"]),
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
    app.add_middleware(RequestLogMiddleware)
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")),
              name="static")
    app.mount("/next", NextFrontendFiles(directory=str(_NEXT_DIR), html=True,
                                         check_dir=False), name="next")
    return app
