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
import math
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import cv2
import numpy as np
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from .. import case as case_mod
from .. import config as config_mod
from .. import keyframe as keyframe_mod
from .. import roles as roles_mod
from ..case import CaseError
from ..cli.generate import (
    GenError,
    assemble,
    candidates,
    clip_duration_s,
    template_for,
)
from ..jsonio import json_safe
from ..media.videoio import VideoIOError
from ..patch import sidecar as sidecar_mod
from ..patch.projection import viewport_erp_rects, viewport_erp_size
from ..procenv import decode_text, utf8_env
from ..redraw.geometry import follow_input_plan
from ..redraw.manifest import ImageManifest, Manifest
from ..relight import regions as regions_mod
from . import case_meta
from .identity import (
    MAX_NAME_LENGTH,
    NAME_COOKIE,
    IdentityMiddleware,
    current_user,
)
from .locks import FileCaseLockStore, LockConflict
from .logging import CLIENT_LOGGER, GEOMETRY_LOGGER, RequestLogMiddleware
from .proxy import (
    ensure_file_proxy,
    ensure_proxy,
    file_proxy,
    file_proxy_state,
    file_proxy_width,
    proxy_path,
    proxy_ready,
    proxy_state,
    proxy_width,
)
from .render import FOV_MAX_DEG, FOV_MIN_DEG, PreviewStore
from .tasks import QUEUE, vrstudio_argv

_WEB_DIR = Path(__file__).parent
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

#: ``/file`` 的展示类型：覆盖 case.py 与 app.py 两侧已接受的视频/图片扩展名；
#: 未知后缀不拒绝访问，只按下载展示。类型表不参与白名单判定。
_FILE_MEDIA_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8", ".toml": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
}

#: 单人本地用；serve --cases-dir / 未来多用户在此换全局上下文
_ctx = {"cases_root": None, "meta": None, "locks": None}


def _root() -> Path:
    return case_mod.cases_root(_ctx["cases_root"])


def _meta() -> case_meta.FileCaseMetaStore:
    store = _ctx["meta"]
    if store is None:
        store = case_meta.FileCaseMetaStore(_ctx["cases_root"])
        _ctx["meta"] = store
    return store


def _locks() -> FileCaseLockStore:
    store = _ctx["locks"]
    if store is None:
        store = FileCaseLockStore(_ctx["cases_root"])
        _ctx["locks"] = store
    return store


def sources_dir() -> Path:
    d = config_mod.REPO_ROOT / "sources"
    d.mkdir(exist_ok=True)
    return d


# ── 浏览器分片上传（会话与完成区都在 cases/.uploads）────────────────────
#
# 只接受追加写：客户端每次带 offset，服务端校验 offset == 已收字节数，
# 越界与重复写入都拒绝。文件不进内存，按 8 MB 分片落盘。完成后计算
# SHA-256，加入 case 时按哈希去重，避免同一文件被静默收两遍。

UPLOAD_CHUNK = 8 * 1024 * 1024
#: 单个上传文件的字节上限。产品级约束，不走配置：不同机器上传的是同一批素材，
#: 放宽这个值只会在平台侧被拒，所以不提供覆盖入口。
UPLOAD_MAX_BYTES = 10 * 1024 ** 3
UPLOAD_TTL_SECONDS = 24 * 3600
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")


def _uploads_root() -> Path:
    root = _root() / ".uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _upload_dir(upload_id: str) -> Path:
    if not _UPLOAD_ID.fullmatch(upload_id):
        raise CaseError("上传 id 不合法")
    direct = _uploads_root() / upload_id
    if direct.is_dir():
        return direct
    complete = _uploads_root() / "complete" / upload_id
    if complete.is_dir():
        return complete
    raise CaseError("上传会话不存在")


def _upload_meta(directory: Path) -> dict:
    path = directory / "meta.json"
    if not path.is_file():
        raise CaseError("上传会话缺少元数据")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_upload_meta(directory: Path, meta: dict) -> None:
    (directory / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _cleanup_uploads() -> None:
    """启动时清掉超过 24 小时仍未完成或未被取走的会话。"""
    now = datetime.now().timestamp()
    for base in (_uploads_root(), _uploads_root() / "complete"):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if not child.is_dir() or child.name == "complete":
                continue
            try:
                created = datetime.fromisoformat(
                    str(_upload_meta(child).get("created_at", ""))).timestamp()
            except (OSError, ValueError, TypeError):
                created = 0
            if now - created > UPLOAD_TTL_SECONDS:
                shutil.rmtree(child, ignore_errors=True)


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
    {"label": "3840×2160", "width": 3840, "height": 2160, "aspect": "16:9"},
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


def _coverage_payload(store: PreviewStore) -> dict | None:
    """视口覆盖角度、ERP footprint、inner 角宽与 1080p/4K 密度。

    纯展示算术：footprint 由 ``patch.projection.viewport_erp_size`` 给，密度是
    输出宽度除以 footprint 宽度。视口跨经线时 footprint 取整幅宽度。
    """
    vp = store.case.viewport or {}
    inner = store.case.inner or {}
    try:
        width = int(vp["width"])
        height = int(vp["height"])
        fov = float(vp["fov_h_deg"])
        yaw = float(vp["yaw_deg"])
        pitch = float(vp["pitch_deg"])
        inner_w = int(inner.get("width") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    erp_w, erp_h = store.source_size
    footprint_w, footprint_h = viewport_erp_size(
        width, height, yaw, pitch, fov, erp_w, erp_h)
    if footprint_w <= 0 or footprint_h <= 0:
        return None
    return {
        "fov_deg": round(fov, 1),
        "footprint": [footprint_w, footprint_h],
        "inner_deg": round(fov * inner_w / width, 1) if width else 0.0,
        "density": {"1080p": round(1920 / footprint_w, 2),
                    "4K": round(3840 / footprint_w, 2)},
    }


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
        "coverage": _coverage_payload(store),
        "fov_range": [FOV_MIN_DEG, FOV_MAX_DEG],
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


# ── 选区范围的剪贴板负载（决策 92）──────────────────────────────────────

REGION_KIND = "vrstudio.patch.region"
REGION_VERSION = 1


def _region_payload(store: PreviewStore) -> dict:
    """导出当前 case 已保存的选区三段；前端直接序列化进剪贴板。"""
    case = store.case
    if not (case.viewport and case.inner and case.frames):
        raise ValueError("选区还没保存完整（viewport/inner/frames 缺一段）")
    if store.frame_count <= 0 or store.fps <= 0:
        raise ValueError("读不到源片的帧率或帧数，无法导出抽取范围")
    return {
        "kind": REGION_KIND,
        "version": REGION_VERSION,
        "case": case.name,
        # 只给人和日志看：导入忽略来源 case 与导出时间，跨 case 粘贴是正常用法。
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "viewport": dict(case.viewport),
        "inner": dict(case.inner),
        "frames": dict(case.frames),
        # 帧号是来源 fps 下的绝对帧；导入按时间换算到目标 fps，避免帧号直接搬运。
        "fps": float(store.fps),
        "frame_count": int(store.frame_count),
    }


def _region_fields(data) -> dict:
    """校验剪贴板负载，抽出 import_region() 需要的值；不合法抛 ValueError。"""
    if not isinstance(data, dict):
        raise ValueError("选区数据必须是 JSON 对象")
    if data.get("kind") != REGION_KIND:
        raise ValueError("剪贴板内容不是 vrstudio 选区范围")
    if data.get("version") != REGION_VERSION:
        raise ValueError(f"不支持的选区数据版本：{data.get('version')!r}")
    viewport = data.get("viewport")
    inner = data.get("inner")
    frames = data.get("frames")
    for label, value in (("viewport", viewport), ("inner", inner), ("frames", frames)):
        if not isinstance(value, dict):
            raise ValueError(f"选区数据缺少 {label}，或 {label} 不是对象")

    def number(obj: dict, key: str, label: str, *, integer: bool = False):
        raw = obj.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{label}.{key} 必须是数字")
        if not math.isfinite(raw):
            raise ValueError(f"{label}.{key} 必须是有限数字")
        if integer:
            if float(raw) != int(raw):
                raise ValueError(f"{label}.{key} 必须是整数")
            return int(raw)
        return float(raw)

    width = number(viewport, "width", "viewport", integer=True)
    height = number(viewport, "height", "viewport", integer=True)
    if width <= 0 or height <= 0:
        raise ValueError(f"viewport 尺寸必须为正：{width}×{height}")
    fps = number(data, "fps", "选区数据")
    frame_count = number(data, "frame_count", "选区数据", integer=True)
    if fps <= 0:
        raise ValueError(f"fps 必须为正：{fps:g}")
    if frame_count <= 0:
        raise ValueError(f"来源总帧数必须为正：{frame_count}")
    return {
        "yaw_deg": number(viewport, "yaw_deg", "viewport"),
        "pitch_deg": number(viewport, "pitch_deg", "viewport"),
        "fov_h_deg": number(viewport, "fov_h_deg", "viewport"),
        "viewport_size": (width, height),
        "inner": {key: number(inner, key, "inner", integer=True)
                  for key in ("x", "y", "width", "height")},
        "frames": {key: number(frames, key, "frames", integer=True)
                   for key in ("start", "end")},
        "source_fps": fps,
        "source_frame_count": frame_count,
    }


# ── 页面 ─────────────────────────────────────────────────────────────────


async def index(request):
    return RedirectResponse("/next/", status_code=307)


async def legacy_case(request):
    """旧版 `/case/{name}` 已退役：转到新前端的同一 case（保留旧书签）。"""
    name = request.path_params["name"]
    return RedirectResponse(f"/next/case/{quote(name, safe='')}", status_code=307)


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
    # relight 没有 [source]：对比页左路要播的是底版槽位里的视频。
    if case.flow == "relight":
        gt, _atmo = case.relight_slots()
        if gt is None or gt.kind != "video":
            return JSONResponse({"error": "relight 没有视频底版"}, status_code=404)
        p = gt.resolved(case, _ctx["cases_root"])
    else:
        p = Path(case.source_path)
    if not p.is_file():
        return JSONResponse({"error": "source video missing"}, status_code=404)
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


# ── 原片直开（/file）：白名单根只决定"能不能读"，类型只决定"怎么展示" ──


def _open_roots() -> dict[str, Path]:
    """``/file`` 的白名单根；位置判定与 open_url 生成共用这一份。"""
    return {"cases": _root(), "sources": sources_dir(), "workdir": _workdir()}


def _file_target(token: str, raw: str) -> Path | None:
    """把 root + 根内相对路径解析成真实文件；任何越界都返回 None。

    只认相对路径：绝对值、盘符、UNC 在归一后仍会被拒绝；解析后再做一次
    resolve() 包含判断，兜住 symlink/junction 逃逸。
    """
    root = _open_roots().get(token)
    if root is None or not raw:
        return None
    rel = Path(raw.replace("\\", "/"))
    if rel.is_absolute() or rel.drive or not rel.parts:
        return None
    if any(part in ("..", "") for part in rel.parts):
        return None
    try:
        base = root.resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            return None
    except (OSError, ValueError):
        return None
    return target


async def file_open(request):
    """``GET /file?root=<cases|sources|workdir>&p=<根内相对路径>``：只读原片。

    ``download=1`` 时按附件下发（浏览器直接落盘）；不带参数仍是内联打开。
    """
    target = _file_target(request.query_params.get("root", ""),
                          request.query_params.get("p", ""))
    if target is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    download = (request.query_params.get("download") or "").lower() in ("1", "true", "yes")
    response = FileResponse(
        str(target),
        media_type=_FILE_MEDIA_TYPES.get(target.suffix.lower(),
                                         "application/octet-stream"),
        filename=target.name,
        content_disposition_type="attachment" if download else "inline",
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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
    return str(config_mod.active().get("generate.vrgen", "vrgen"))


def _workdir() -> Path:
    conf = config_mod.active()
    raw = Path(str(conf.get("generate.workdir", "vrgen_runs")))
    return raw if raw.is_absolute() else config_mod.REPO_ROOT / raw


_ROUND_LINE = re.compile(
    r"^\s+(?P<mark>[*x])\s+(?P<round>\S+)\s+(?P<backend>\S+)\s+(?P<stage>\S+)\s+"
    r"gate=(?P<gate>\S+)\s+result=(?P<result>yes|no)\s+(?P<updated>\S+)")


def _round_files(round_dir: Path) -> dict:
    """轮次目录里的原始结果与 run.json；缺失也照常返回，页面标红。"""
    run_json = round_dir / "run.json"
    recorded = None
    try:
        recorded = json.loads(run_json.read_text(encoding="utf-8")).get("result")
    except (OSError, ValueError):
        pass
    result = Path(recorded) if recorded else round_dir / "result.mp4"
    return {"result": file_entry(result), "run_json": file_entry(run_json)}


def _image_rounds(case: case_mod.Case) -> list[dict]:
    """图片轮次与候选下标：``run.json`` 的 results 顺序就是 adopt 的下标。"""
    root = _workdir() / case.name
    if not root.is_dir():
        return []
    rows: list[dict] = []
    for directory in sorted((p for p in root.glob("img-*") if p.is_dir()),
                            key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            blob = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        results = []
        for index, raw in enumerate(blob.get("results") or [], start=1):
            path = Path(raw)
            results.append({"index": index, "name": path.name,
                            "exists": path.is_file(),
                            "url": f"/img/round/{case.name}/{directory.name}/{index}"})
        # 图片轮次的 gate 是对象（verdict/kind/images），页面只显示判定词。
        gate = blob.get("gate")
        verdict = gate.get("verdict") if isinstance(gate, dict) else gate
        rows.append({"round": directory.name, "stage": blob.get("stage"),
                     "gate": verdict, "results": results,
                     "adopted": directory.name == case.adopt_round})
    return rows


def _image_report(case: case_mod.Case) -> dict | None:
    """图片流程的 merge 报告：patchimg 的选区外统计、fullRedrawImg 的接缝闸门。"""
    if not case.extract_current:
        return None
    path = case.dir(_ctx["cases_root"]) / "extracts" / case.extract_current / "report.json"
    try:
        # 旧报告可能含 Infinity（完美匹配时的 quality），JSONResponse 会因此 500；
        # 读入时统一清掉非有限值，老文件不必重跑 merge。
        return json_safe(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


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
            d["files"] = _round_files(Path(d["round_dir"]))
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


def _image_geometry_sizes(case: case_mod.Case) -> tuple[tuple[int, int],
                                                        tuple[int, int]] | None:
    """图片流程的画布与源尺寸：展开模板几何占位符用，只读 sidecar 不写盘。"""
    if not case.extract_current:
        return None
    d = case.dir(_ctx["cases_root"]) / "extracts" / case.extract_current
    try:
        if case.flow == "patchimg":
            geo = sidecar_mod.load_image(d / "image.json")
            return ((int(geo.viewport.width), int(geo.viewport.height)),
                    (int(geo.erp_width), int(geo.erp_height)))
        manifest = ImageManifest.load(d / "image_pad.json")
        return ((int(manifest.canvas.size[0]), int(manifest.canvas.size[1])),
                (int(manifest.source.size[0]), int(manifest.source.size[1])))
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _prompt_preview_args(name: str, mode: str | None = None) -> tuple[list[str], Path | None]:
    """提示词展开所需的 CLI 参数：几何尺寸 + 提交清单临时文件。

    拿不到候选或几何时不报错，只返回已能拿到的部分；vrgen 会把缺的占位符
    报成 render_error，页面显示具体缺什么，而不是干瞪占位符。

    图片流程不用 ``--refs``：它的输入是「基图 + mask + 参考图」的固定顺序，
    按 ``--image-role`` 逐张报给 vrgen 就够展开 ``image_roles``。
    """
    args: list[str] = []
    refs_file: Path | None = None
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return args, None
    if case.flow in case_mod.IMAGE_FLOWS:
        roles = ["edit_master", "mask"]
        by_id = {c.id: c for c in candidates(case, _ctx["cases_root"])}
        for item in case.submit_selected or []:
            candidate = by_id.get(item)
            if candidate is None:
                continue
            roles.append(case.submit_role(item) or candidate.role)
        for role in roles:
            args += ["--image-role", role]
        sizes = _image_geometry_sizes(case)
        if sizes is not None:
            (canvas_w, canvas_h), (source_w, source_h) = sizes
            args += ["--canvas", f"{canvas_w}x{canvas_h}",
                     "--source", f"{source_w}x{source_h}"]
        return args, None
    try:
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
    """提示词模板 + 变量草稿 + 展开后的完整正文（决策 18；契约 case-model.md §4）。

    relight 不调 vrgen、也没有提示词模板：在这里统一短路，所有调用方都不必各自判断
    （否则 ``template_for`` 会以 ``KeyError: 'relight'`` 炸成 500）。
    """
    if flow == "relight":
        return {}
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


def _billing(name: str) -> dict:
    """任务提交时记录扣哪个项目/剧集/会话的额度；读不到就留空。"""
    try:
        params = _generation_params(name)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    current = params.get("current") or {}
    return {
        "project_id": current.get("project_id"),
        "episode_id": current.get("episode"),
        "session_id": current.get("session_id"),
    }


def _generation_params(name: str) -> dict:
    """生成页的输出/输入分辨率选项（决策 20/34；契约 case-model.md §4）。

    先不带 ``--refs`` 读模式与当前草稿；提交集齐了才带上清单计算档位。这样
    纯图片 case 与空白 case 都不会撞上 `params show` 的“不接受输入视频”闸门。
    relight 不调 vrgen，所以直接给空表，不 spawn 子进程。
    """
    try:
        flow = case_mod.load(name, _ctx["cases_root"]).flow
    except CaseError:
        flow = None
    if flow == "relight":
        return {}
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


#: 抽取版本的完成标记与被采用结果的文件名，按 flow 取。图片流程的交付是单帧。
_EXTRACT_FILES = {
    "patch": ("clip.json", "result.mp4"),
    "patchimg": ("image.json", "result.png"),
    "fullRedrawImg": ("image_pad.json", "result.png"),
}


def _extracts(case: case_mod.Case) -> list[dict]:
    marker, result_name = _EXTRACT_FILES.get(case.flow, ("clip.json", "result.mp4"))
    vroot = case.dir(_ctx["cases_root"]) / "extracts"
    out = []
    if vroot.is_dir():
        for vdir in sorted(p for p in vroot.iterdir() if p.is_dir()):
            out.append({"version": vdir.name,
                        "extracted": (vdir / marker).is_file(),
                        "has_result": (vdir / result_name).is_file()})
    return out


def _output_entry(case: case_mod.Case, path: Path) -> dict:
    """成片条目：文件信息 + 预览代理状态；打开走 open_url（原片）。

    图片交付物没有代理，直接列文件——浏览器按原图打开就是它该有的样子。
    """
    if path.suffix.lower() != ".mp4":
        return {"file": path.name, **file_entry(path)}
    return {
        "file": path.name,
        **file_entry(path),
        "proxy_url": f"/video/outproxy/{case.name}/{path.name}",
        "proxy_state": file_proxy_state(case, path, _ctx["cases_root"]),
        "proxy_width": file_proxy_width(case, path, _ctx["cases_root"]),
    }


def _output_sort_key(path: Path) -> tuple[float, str]:
    """按修改时间升序、文件名兜底；对比页默认取列表最后一项（即最新一份）。"""
    try:
        return (path.stat().st_mtime, path.name)
    except OSError:
        return (0.0, path.name)


def _output_files(case: case_mod.Case) -> list[tuple[Path, str | None, bool]]:
    """成片文件、patch 版本名与是否仅空间段；列表与详情共用同一套选择规则。

    返回 ``(路径, patch 版本名或 None, 是否 space-only)``，按修改时间升序。
    列表页只取路径与数量，不再读代理 sidecar、也不打开源片取帧数。

    只收流程自己写出的成片，``out/`` 里混进来的散件不展示：
    patch 认 ``<版本>_space.mp4`` / ``<版本>_frame.mp4``（frame 优先），
    fullredraw 认旁边有 recompose 报告 ``<轮次>_recompose.json`` 的 mp4。
    """
    odir = case.dir(_ctx["cases_root"]) / "out"
    if not odir.is_dir():
        return []
    if case.flow in case_mod.IMAGE_FLOWS:
        return [(p, None, False)
                for p in sorted(odir.glob("*.png"), key=_output_sort_key)]
    if case.flow == "relight":
        # 底版可能是视频也可能是图片，交付物两种后缀都会出现；小样在 out/samples/
        # 子目录里，不是 out/ 的直接子文件，所以这里不会误收。
        return [(p, None, False)
                for p in sorted((p for p in odir.iterdir()
                                 if p.is_file()
                                 and p.suffix.lower() in _RELICHT_OUT_SUFFIXES),
                                key=_output_sort_key)]
    if case.flow == "fullredraw":
        files = [p for p in odir.glob("*.mp4")
                 if p.with_name(f"{p.stem}_recompose.json").is_file()]
        return [(p, None, False) for p in sorted(files, key=_output_sort_key)]
    if case.flow != "patch":
        # gen 的采用结果在 case/result.mp4，out/ 不是它的产物目录。
        return []
    chosen: dict[str, tuple[str, Path]] = {}
    for p in odir.glob("*.mp4"):
        stem = p.stem
        if stem.endswith("_frame"):
            chosen[stem[:-len("_frame")]] = ("frame", p)
        elif stem.endswith("_space"):
            version = stem[:-len("_space")]
            if chosen.get(version, ("", None))[0] != "frame":
                chosen[version] = ("space", p)
    rows = [(path, version, kind == "space") for version, (kind, path) in chosen.items()]
    return sorted(rows, key=lambda row: _output_sort_key(row[0]))


def _outputs(case: case_mod.Case) -> list[dict]:
    """成片列表。patch 按版本选一个：``_frame.mp4`` 优先，缺失时回退 ``_space.mp4``。

    旧命名 ``<版本>.mp4`` 不进入回退链（由一次性迁移脚本改名）。
    ``partial=True`` 表示只有空间合并段、时间轴不是整片，页面必须标注"仅中间段"。
    """
    out = []
    for path, version, space_only in _output_files(case):
        entry = _output_entry(case, path)
        if version is not None:
            entry["version"] = version
            entry["partial"] = space_only and not _is_whole_extract(case, version)
        out.append(entry)
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


def _file_open_target(path: Path) -> tuple[str, str] | None:
    """文件落在哪个白名单根内，以及相对该根的 posix 路径；不在任何根内为 None。

    根之间可能重叠（例如把 workdir 配到 cases 下），取最深的一个：生成的 URL
    语义更具体，且结果不随根表的字典顺序漂移。
    """
    try:
        target = Path(path).resolve()
    except (OSError, ValueError):
        return None
    best: tuple[str, Path, Path] | None = None
    for token, root in _open_roots().items():
        try:
            base = Path(root).resolve()
            rel = target.relative_to(base)
        except (OSError, ValueError):
            continue
        if best is None or len(base.parts) > len(best[1].parts):
            best = (token, base, rel)
    if best is None:
        return None
    return best[0], best[2].as_posix()


def _open_url(path: Path) -> str | None:
    hit = _file_open_target(path)
    if hit is None:
        return None
    token, rel = hit
    return f"/file?root={token}&p={quote(rel, safe='')}"


def file_entry(path: Path) -> dict:
    """``_file_info`` + 可选的 open_url。

    文件不存在或落在白名单之外时条目照常返回（页面标红、显示路径），但不带
    open_url——不给出点了必然 404 的链接。
    """
    info = _file_info(path)
    if info["exists"]:
        url = _open_url(path)
        if url is not None:
            info["open_url"] = url
    return info


def _imread(path: Path, flags: int):
    """OpenCV 在 Windows 上不认非 ASCII 路径；从字节解码绕开它。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, flags) if data.size else None


def _empty_media() -> dict:
    return {"width": None, "height": None, "duration": None, "fps": None}


def _media_meta(path: Path, kind: str) -> dict:
    """尺寸/时长/帧率：素材卡片与缩略图共用的只读元数据。

    取一次宽高要把整张图解码（4K PNG 约 90ms），而这些值只随文件内容变化，
    所以按 路径 + mtime + 大小 记忆；文件被替换时缓存键自然变化。
    """
    try:
        stat = path.stat()
    except OSError:
        return _empty_media()
    if not path.is_file():
        return _empty_media()
    return dict(_probe_media_meta(str(path), stat.st_mtime_ns, stat.st_size, kind))


@lru_cache(maxsize=1024)
def _probe_media_meta(path: str, mtime_ns: int, size: int, kind: str) -> dict:
    file = Path(path)
    meta = _empty_media()
    if kind == "video":
        cap = cv2.VideoCapture(str(file))
        if cap.isOpened():
            meta["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
            meta["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            meta["fps"] = round(fps, 3) if fps else None
            meta["duration"] = round(frames / fps, 3) if fps and frames else None
        cap.release()
        return meta
    image = _imread(file, cv2.IMREAD_UNCHANGED)
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
        info = file_entry(cand.path)
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
    "机位、镜头与透视、构图与地平线高度保持图1原样；场景建筑、地面、背景人物与陈设、"
    "光照方向与色温不变；除【编辑对象】外不新增、不删除、不移动任何东西。")

#: 模板新增槽位时的默认值。键不必覆盖全部槽位：缺失时按空字符串补，正文仍可展开；
#: 这里只放“有合理通用值”的那几个。真正的槽位清单以模板占位符为准。
DEFAULT_KEYFRAME_VARS = {
    "target": "人物",
    "source_first": "",
    "source_last": "",
    "figure": "",
    "subject_last": "",
    "pose_first": "",
    "pose_last": "",
    "preserve": DEFAULT_KEYFRAME_PRESERVE,
    "cleanup": "",
}


def _pick_row(pick: keyframe_mod.Pick | None,
              by_id: dict[str, dict]) -> dict | None:
    """指针只有在素材仍在角色集合里时才有效；素材被删后按“未定稿”处理。

    选定图要能查名、查路径、单独打开，所以这里把素材的文件条目一并带回。
    """
    if pick is None:
        return None
    row = by_id.get(pick.asset)
    if row is None:
        return None
    return {"round": pick.round, "index": pick.index, "asset": pick.asset,
            "source": "raw" if pick.round == RAW_PICK_ROUND else "generated",
            "file": {key: row.get(key) for key in
                     ("name", "rel", "path", "exists", "mtime", "size", "open_url")}}


def _keyframe_state(case: case_mod.Case, details: list[dict] | None = None) -> dict:
    """首尾帧工作台的只读状态：参考图清单、两帧指针、是否过期。

    图片步的输入全部从这里与原始帧解析，不读 ``[submit]``；视频提交清单
    仍只由生成页的引用条目决定。``details`` 允许调用方传入已算好的素材明细，
    一次请求里不重复解码整批素材。"""
    role_help = {
        "subject_ref": "人物三视图：唯一外观依据。",
        "detail_ref": "细节参考：只继承所指细节的外观，不取构图、背景与姿态。",
    }
    base = {"enabled": case.flow in ("patch", "gen"), "error": None,
            "refs": [], "refs_default": False,
            "first": None, "last": None,
            "extract": None, "stale": False, "complete": False,
            "role_help": role_help}
    if case.flow not in ("patch", "gen"):
        return base
    try:
        state = keyframe_mod.load(case.dir(_ctx["cases_root"]))
    except keyframe_mod.KeyframeError as exc:
        base["error"] = str(exc)
        return base
    details = details if details is not None else _asset_details(case)
    by_id = {d["id"]: d for d in details}
    raw_refs = list(state.refs)
    default = False
    if not raw_refs:
        sheets = [d for d in details
                  if d["kind"] == "image" and d["role"] == "subject_ref" and d.get("exists")]
        if len(sheets) == 1:
            raw_refs = [keyframe_mod.Ref(asset=sheets[0]["id"], role="subject_ref")]
            default = True
    refs = []
    for ref in raw_refs:
        row = by_id.get(ref.asset)
        exists = bool(row and row.get("exists") and row.get("kind") == "image")
        refs.append({
            "asset": ref.asset, "role": ref.role, "note": ref.note,
            "label": row.get("label") if row else ref.asset,
            "url": f"/img/asset/{case.name}/{ref.asset}" if row else None,
            "exists": exists,
            "file": ({key: row.get(key) for key in
                      ("name", "rel", "path", "exists", "mtime", "size", "open_url")}
                     if row else None),
        })
    first = _pick_row(state.first, {d["id"]: d for d in details
                                    if d["role"] == "first_frame"})
    last = _pick_row(state.last, {d["id"]: d for d in details
                                  if d["role"] == "last_frame"})
    stale = bool(state.extract and case.extract_current
                 and state.extract != case.extract_current)
    base.update({
        "refs": refs,
        "refs_default": default,
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
                     **file_entry(path),
                     "width": meta.get("width"), "height": meta.get("height"),
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
        "outputs": ([file_entry(raw)] if raw is not None else []) +
                   ([file_entry(run_json)] if run_json is not None else []),
    }]
    if raw is not None:
        groups.append({
            "label": "采用",
            "inputs": [file_entry(raw)],
            "outputs": [file_entry(adopted)],
        })
    return {"groups": groups}


def _step_outputs(cdir: Path, base: str, suffix: str,
                  report_suffix: str | None = None) -> list[Path]:
    """某一步在 ``out/`` 里真实写出的产物。

    认 ``<base><suffix>``（案例分辨率）与 ``<base>_*<suffix>``（跟随输入的
    尺寸后缀），``report_suffix`` 非空时要求旁边有成对报告——与
    ``_output_files`` 的成片判据一致，不把散件当产物。按 mtime 升序。
    """
    outdir = cdir / "out"
    if not outdir.is_dir():
        return []
    files = [p for p in outdir.glob(f"{base}*{suffix}")
             if p.stem == base or p.stem.startswith(base + "_")]
    if report_suffix is not None:
        files = [p for p in files
                 if p.with_name(p.stem + report_suffix).is_file()]
    return sorted(files, key=_output_sort_key)


def _pad_upload(cdir: Path) -> tuple[list[dict], dict | None]:
    """``derived/`` 里最新一组上传件：视频、mask、参数 JSON，以及参数摘要。

    上传件按 ``<W>x<H>`` 命名，改模型或缩放档会多出一组；页面只展示最新一组，
    旧文件留在磁盘上不删。JSON 由 vrgen ``prep`` 写（尺寸与限额的唯一权威），
    读不到时仍返回文件条目，只是没有摘要。
    """
    d = cdir / "derived"
    if not d.is_dir():
        return [], None
    videos = sorted(d.glob("upload_pad_*.mp4"),
                    key=lambda p: p.stat().st_mtime if p.exists() else 0.0)
    if not videos:
        return [], None
    video = videos[-1]
    stem = video.stem                       # upload_pad_4398x1884
    prefix, _, size = stem.rpartition("_")  # ("upload_pad", "_", "4398x1884")
    files = [file_entry(video),
             file_entry(d / f"{prefix}_mask_{size}.png"),
             file_entry(d / f"{stem}.json")]
    info = None
    try:
        blob = json.loads((d / f"{stem}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        blob = None
    if isinstance(blob, dict):
        info = {
            "file": files[0],
            "canvas": blob.get("canvas"),
            "source": blob.get("source"),
            "upload": blob.get("upload"),
            "mask": blob.get("mask"),
            "scaled": blob.get("scaled"),
            "scale": blob.get("scale"),
            "scale_percent": blob.get("scale_percent"),
            "limit": blob.get("limit"),
            "video_model_id": blob.get("video_model_id"),
            "mode": blob.get("mode"),
            "created_utc": blob.get("created_utc"),
        }
    return files, info


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
    # 裁回输入按 ``[merge] input`` 解析：指针优先，缺省是 adopt 落点 result.mp4。
    recompose_input = case.merge_input_file(_ctx["cases_root"]) or result
    outputs = []
    if case.adopt_round:
        # 步骤页显示这一轮真实写出的文件；案例分辨率与跟随输入的尺寸后缀
        # 都可能存在，按 mtime 一并列出。一份都没有时才退回计划路径。
        actual = _step_outputs(cdir, case.adopt_round, ".mp4",
                               "_recompose.json")
        if not actual:
            actual = [cdir / "out" / f"{case.adopt_round}.mp4"]
        for path in actual:
            outputs.append(file_entry(path))
            outputs.append(file_entry(
                path.with_name(path.stem + "_recompose.json")))
    uploads, _ = _pad_upload(cdir)
    return {
        "pad": {
            "inputs": [file_entry(source)],
            "outputs": [file_entry(padded), file_entry(mask), file_entry(manifest)],
            "uploads": uploads,
        },
        "generate": _generation_groups(
            case, [file_entry(padded), file_entry(mask), file_entry(manifest)],
            result),
        "recompose": {
            "inputs": [file_entry(recompose_input), file_entry(manifest)],
            "outputs": outputs,
        },
    }


def _patch_step_files(case: case_mod.Case, cdir: Path) -> dict:
    """patch：源 → 选区片段 → 采用结果 → 贴回成片。没有 extract 指针时
    只显示已知的源，不猜版本目录。"""
    source = Path(case.source_path)
    steps = {
        "extract": {"inputs": [file_entry(source)], "outputs": []},
        "generate": {"inputs": [], "outputs": []},
        "merge": {"inputs": [file_entry(source)], "outputs": []},
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
        "inputs": [file_entry(source)],
        "outputs": [file_entry(clip), file_entry(mask), file_entry(sidecar),
                    file_entry(d / "first_frame_raw.png"),
                    file_entry(d / "last_frame_raw.png")],
    }
    steps["generate"] = _generation_groups(
        case, [file_entry(clip), file_entry(mask), file_entry(sidecar)], ai)
    steps["merge"] = {
        "inputs": [file_entry(source), file_entry(ai),
                   file_entry(sidecar), file_entry(clip)],
        "outputs": [file_entry(p) for p in outs],
    }
    return steps


def _patchimg_step_files(case: case_mod.Case, cdir: Path) -> dict:
    """patchimg：源图 → 视口裁剪 → 采用结果 → 贴回成片（单帧，没有时间轴）。"""
    source = [file_entry(Path(case.source_path))] if case.source_path else []
    steps = {
        "extract": {"inputs": source, "outputs": []},
        "generate": {"inputs": [], "outputs": []},
        "merge": {"inputs": source, "outputs": []},
    }
    if not case.extract_current:
        return steps
    d = cdir / "extracts" / case.extract_current
    clip = d / "clip.png"
    mask = d / "clip_mask.png"
    sidecar = d / "image.json"
    result = d / "result.png"
    out = cdir / "out" / f"{case.extract_current}_image.png"
    steps["extract"] = {
        "inputs": source,
        "outputs": [file_entry(clip), file_entry(mask), file_entry(sidecar)],
    }
    steps["generate"] = _generation_groups(
        case, [file_entry(clip), file_entry(mask), file_entry(sidecar)], result)
    steps["merge"] = {
        "inputs": [*source, file_entry(result), file_entry(sidecar), file_entry(clip)],
        "outputs": [file_entry(out), file_entry(d / "report.json")],
    }
    return steps


def _fullredraw_img_step_files(case: case_mod.Case, cdir: Path) -> dict:
    """fullRedrawImg：源图 → 21:9 画布与 mask → 采用结果 → stitch 还原 2:1。"""
    source = [file_entry(Path(case.source_path))] if case.source_path else []
    steps = {
        "extract": {"inputs": source, "outputs": []},
        "generate": {"inputs": [], "outputs": []},
        "merge": {"inputs": [], "outputs": []},
    }
    if not case.extract_current:
        return steps
    d = cdir / "extracts" / case.extract_current
    padded = d / "padded.png"
    mask = d / "padded_mask.png"
    manifest = d / "image_pad.json"
    upload = d / "upload.png"
    upload_mask = d / "upload_mask.png"
    result = d / "result.png"
    # 拼接输出既可能是案例分辨率 ``<版本>_image.png``，也可能是跟随输入的
    # ``<版本>_image_<W>x<H>.png``；按实际文件列出，一份都没有时退回计划路径。
    base = f"{case.extract_current}_image"
    images = _step_outputs(cdir, base, ".png")
    if not images:
        images = [cdir / "out" / f"{base}.png"]
    steps["extract"] = {
        "inputs": source,
        "outputs": [file_entry(padded), file_entry(mask), file_entry(manifest)],
    }
    steps["generate"] = _generation_groups(
        case, [file_entry(upload), file_entry(upload_mask)], result)
    # 拼接输入按 ``[merge] input`` 解析：指针优先，缺省是 adopt 落点 result.png。
    stitch_input = _stitch_input_file(case, cdir, result)
    steps["merge"] = {
        "inputs": [file_entry(stitch_input), file_entry(manifest)],
        "outputs": ([file_entry(p) for p in images]
                    + [file_entry(d / "report.json")]),
    }
    return steps


def _stitch_input_file(case: case_mod.Case, cdir: Path, fallback: Path) -> Path:
    """fullRedrawImg 的拼接输入文件：指针优先，没写指针才回退 adopt 结果。

    指针指向的文件缺失时仍然显示它（页面标「文件缺失」、门控不放行），不悄悄换回
    采用结果——否则用户不知道拼接用的是哪张。
    """
    return case.merge_input_file(_ctx["cases_root"]) or fallback


def _gen_step_files(case: case_mod.Case, cdir: Path) -> dict:
    """gen：素材副本是输入；采用后的副本落在 case/result.mp4。"""
    files = [file_entry(a.resolved(case, _ctx["cases_root"])) for a in case.assets]
    return {
        "assets": {"inputs": [], "outputs": files},
        "generate": _generation_groups(case, files, cdir / "result.mp4"),
    }


#: relight 的交付物扩展名：底版是视频出 mp4，是图片出 png。
_RELICHT_OUT_SUFFIXES = (".mp4", ".png")


def _relight_current(case: case_mod.Case, cdir: Path) -> Path | None:
    """当前成片：``[relight] current`` 指得到就用它，否则取 ``out/`` 下最新的交付物。

    后端不写 ``current``（写回需要任务完成回调，而 relight 的产物名字已经带时间戳），
    所以这里退一步按修改时间取最新；用户想钉住某一版就写 ``[relight] current``。
    """
    raw = (case.relight or {}).get("current")
    if raw:
        pinned = cdir / raw
        if pinned.is_file():
            return pinned
    odir = cdir / "out"
    if not odir.is_dir():
        return None
    candidates = [p for p in odir.iterdir()
                  if p.is_file() and p.suffix.lower() in _RELICHT_OUT_SUFFIXES]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _latest_sample_dir(cdir: Path) -> Path | None:
    """最近一次小样目录：``out/samples/<时间戳>``；没有小样时为 None。"""
    root = cdir / "out" / "samples"
    if not root.is_dir():
        return None
    dirs = sorted((p for p in root.iterdir() if p.is_dir()),
                  key=lambda p: p.name, reverse=True)
    return dirs[0] if dirs else None


def _relight_samples(cdir: Path) -> list[dict]:
    """最近一次小样的缩略图清单：``out/samples/<时间戳>/sample_*.png``。"""
    latest = _latest_sample_dir(cdir)
    if latest is None:
        return []
    return [file_entry(p) for p in sorted(latest.glob("sample_*.png"))]


def _relight_sample_report(cdir: Path) -> dict | None:
    """最近一次小样的报告：``out/samples/<时间戳>.json``。

    前端靠这份报告显示这次小样的时间戳与分段耗时；老小样没有报告时返回 None。
    """
    latest = _latest_sample_dir(cdir)
    if latest is None:
        return None
    report = latest.with_suffix(".json")
    return file_entry(report) if report.is_file() else None


def _relight_step_files(case: case_mod.Case, cdir: Path) -> dict:
    """relight：两个槽位是输入；产物在 out/，当前成片由 ``_relight_current`` 决定。"""
    gt, atmo = case.relight_slots()
    slots = [file_entry(a.resolved(case, _ctx["cases_root"]))
             for a in (gt, atmo) if a is not None]
    current = _relight_current(case, cdir)
    result = [file_entry(current)] if current is not None else []
    return {
        "assets": {"inputs": [], "outputs": slots},
        "relight": {"inputs": slots, "outputs": result},
        "compare": {"inputs": [*slots, *result], "outputs": []},
    }


def _output_options(case: case_mod.Case, cdir: Path) -> list[dict]:
    """Output sizes this flow can produce right now.

    The second option is derived from the recompose/merge input's own size (not
    from the case canvas), computed with the same ``follow_input_plan`` the CLIs
    use, so the page never re-implements the rule. The option is absent when the
    input cannot be followed or the derived size equals the case resolution.
    """
    plan = None
    input_path: Path | None = None
    if case.flow == "fullredraw":
        manifests = sorted(cdir.glob("*_pad.json"))
        if manifests:
            try:
                plan = Manifest.load(manifests[0]).to_pad_plan()
            except (OSError, ValueError, TypeError):
                plan = None
        input_path = case.merge_input_file(_ctx["cases_root"]) or (cdir / "result.mp4")
    elif case.flow == "fullRedrawImg" and case.extract_current:
        d = cdir / "extracts" / case.extract_current
        try:
            plan = ImageManifest.load(d / "image_pad.json").to_pad_plan()
        except (OSError, ValueError, TypeError):
            plan = None
        input_path = case.merge_input_file(_ctx["cases_root"]) or (d / "result.png")
    if plan is None:
        return []
    options = [{"mode": "case", "width": plan.W, "height": plan.H}]
    if input_path is not None and input_path.is_file():
        kind = "video" if input_path.suffix.lower() in _VIDEO_EXTS else "image"
        meta = _media_meta(input_path, kind)
        width, height = meta.get("width"), meta.get("height")
        if width and height:
            scaled = follow_input_plan(plan, (int(width), int(height)))
            if scaled is not None:
                options.append({"mode": "input", "width": scaled.W, "height": scaled.H})
    return options


def _step_files(case: case_mod.Case) -> dict:
    """每条 flow 的文件规范都不同，这里显式分流，禁止共用一张表。"""
    cdir = case.dir(_ctx["cases_root"])
    if case.flow == "fullredraw":
        return _fullredraw_step_files(case, cdir)
    if case.flow == "patch":
        return _patch_step_files(case, cdir)
    if case.flow == "patchimg":
        return _patchimg_step_files(case, cdir)
    if case.flow == "fullRedrawImg":
        return _fullredraw_img_step_files(case, cdir)
    if case.flow == "gen":
        return _gen_step_files(case, cdir)
    if case.flow == "relight":
        return _relight_step_files(case, cdir)
    raise ValueError(f"未知 flow：{case.flow}")


def _gates(case: case_mod.Case, extracts: list[dict],
           details: list[dict] | None = None, *,
           outs: list[dict] | None = None, list_only: bool = False) -> dict:
    """软向导的门控判据与证据（服务端唯一权威，前端只渲染结果）。

    每一步带两段文字：没完成时 reason 说差什么；完成时 evidence 说"在哪个
    绝对路径检测到了什么文件、所以认为完成、想重跑怎么办"——页面上每一次
    置灰/放行都可以对照文件复查。``details`` 透传给 ``_keyframe_state``，
    避免同一次请求里再解码一遍素材。

    ``list_only=True`` 是列表页的轻量路径：不算首尾帧工作台状态（那会解码并
    校验全部素材），也不重复计算成片列表。``outs`` 允许调用方传入已算好的条目。
    """
    cdir = case.dir(_ctx["cases_root"])
    missing = case_mod.draft_ready(case)
    extracted = bool(case.extract_current) and any(
        e["version"] == case.extract_current and e["extracted"] for e in extracts)
    adopted = bool(case.adopt_round)
    result_file = (case.flow in ("fullredraw", "gen") and (cdir / "result.mp4").is_file())
    pad_manifests = sorted(cdir.glob("*_pad.json"))
    padded = bool(pad_manifests)
    if outs is None:
        outs = _outputs(case)
    has_out = bool(outs)
    out_names = "、".join(o["file"] for o in outs)
    partial_names = [o["file"] for o in outs if o.get("partial")]
    partial_note = (f"；{'、'.join(partial_names)} 仅中间段（时间拼接未完成）"
                    if partial_names else "")
    if case.flow == "patch":
        cur = cdir / "extracts" / (case.extract_current or "")
        steps = {
            "pick": {"ok": missing is None,
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
                      "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}；每次合并都会覆盖同名文件{partial_note}"},
            "compare": {"ok": has_out, "reason": "" if has_out else "合并出片后可以在这里对比",
                        "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}{partial_note}"},
        }
        if not list_only:
            kf = _keyframe_state(case, details)
            raw_ok = all((cur / name).is_file()
                         for name in ("first_frame_raw.png", "last_frame_raw.png"))
            primary = next((row for row in kf["refs"]
                            if row["role"] == "subject_ref" and row["exists"]), None)
            missing_refs = [row["asset"] for row in kf["refs"] if not row["exists"]]
            if not extracted:
                kf_reason = "先完成选区并运行抽取；首尾帧来自 clip 的首末帧"
            elif not raw_ok:
                kf_reason = "当前抽取版本没有两张原始帧；重新抽取一次"
            elif kf["error"]:
                kf_reason = kf["error"]
            elif kf["stale"]:
                kf_reason = "抽取版本已更新，需要重新定稿两帧"
            elif missing_refs:
                kf_reason = f"参考图文件不存在：{'、'.join(missing_refs)}；重新加入或删除"
            elif primary is None:
                kf_reason = "参考图清单缺少人物三视图；在首尾帧页加入一张并设为主三视图"
            elif not kf["first"]:
                kf_reason = "首帧还没定稿；在首尾帧步骤生成并挑选一张"
            elif not kf["last"]:
                kf_reason = "首帧已定，接着生成并挑选尾帧"
            else:
                kf_reason = ""
            kf_ok = bool(extracted and raw_ok and primary is not None
                         and not missing_refs and kf["complete"])
            steps["keyframe"] = {"ok": kf_ok, "ready": extracted and raw_ok,
                                 "reason": kf_reason,
                                 "evidence": (f"在 {cur}\\ 检测到两张原始帧；keyframe.toml 指向首帧 "
                                              f"{kf['first']['asset']} 与尾帧 {kf['last']['asset']}"
                                              if kf_ok else "")}
    elif case.flow == "fullredraw":
        # evidence 只在 ok 时被页面读到，但仍须防御求值——全新 case 没有
        # manifest，无条件取 [0] 会让整个 /api/case 500（2026-10-16 验收发现）
        # 裁回输入可以是采用结果，也可以是素材库里选定的一段 21:9 画布视频。
        recompose_input = case.merge_input_file(_ctx["cases_root"])
        recompose_ready = bool(recompose_input and recompose_input.is_file())
        steps = {
            "pad": {"ok": padded, "ready": bool(case.source_path),
                    "reason": "" if padded else "还没有扩充过画面；先扩充，生成步才可用",
                    "evidence": (f"在 {pad_manifests[0]} 检测到扩充画面的记录文件，"
                                 f"这一步已完成；再点一次按钮会重新扩充并覆盖它"
                                 ) if padded else ""},
            "generate": {"ok": result_file, "ready": padded,
                         "reason": "" if result_file else "先送生成，并采用一次结果",
                         "evidence": (f"在 {cdir}\\result.mp4 检测到已选用的结果"
                                      f"（来自 Session {case.adopt_round}）；想换一个，回到上面点别的「选用」"
                                      if result_file else "")},
            "recompose": {"ok": has_out, "ready": recompose_ready,
                          "reason": "" if has_out else
                          "采用结果后才能裁回原尺寸；也可以在下方上传或选择一份 21:9 画布视频",
                          "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}；每次裁回都会覆盖同名文件"},
            "compare": {"ok": has_out,
                        "reason": "" if has_out else "裁回原尺寸出片后，在这里对比",
                        "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}"},
        }
    elif case.flow in case_mod.IMAGE_FLOWS:
        cur = cdir / "extracts" / (case.extract_current or "")
        result_png = cur / "result.png"
        picked = bool(case.adopt_round) and result_png.is_file()
        patchimg = case.flow == "patchimg"
        produced = (("clip.png", "clip_mask.png", "image.json") if patchimg
                    else ("padded.png", "padded_mask.png", "image_pad.json"))
        steps = {}
        if patchimg:
            steps["pick"] = {
                "ok": missing is None,
                "reason": (f"case.toml 里缺 [{missing}] 段，补齐后才能抽取"
                           if missing else ""),
                "evidence": (f"草稿两段（viewport/inner）都在 "
                             f"{case_mod.case_toml(case.name, _ctx['cases_root'])} 里"),
            }
        steps["extract"] = {
            "ok": extracted,
            "ready": (missing is None) if patchimg else True,
            "reason": "" if extracted else (
                "先完成选区并运行抽取" if patchimg
                else "运行抽取：整幅 pad 成 21:9 并生成冗余带 mask"),
            "evidence": (f"在 {cur}\\ 检测到 {' / '.join(produced)}，当前抽取版本是 "
                         f"{case.extract_current}；重新抽取会生成新版本，旧版本不动"
                         if extracted else ""),
        }
        steps["generate"] = {
            "ok": picked,
            "ready": extracted,
            "reason": "" if extracted else "抽取之后才能送生成",
            "evidence": (f"在 {cur}\\result.png 检测到已采用的第 {case.adopt_index} 张候选"
                         f"（来自 Session {case.adopt_round}）" if picked else ""),
        }
        if patchimg:
            merge_ready = extracted and picked
            merge_reason = "采用一张候选后才能贴回"
        else:
            # 拼接输入可以来自 adopt，也可以是素材库里选定的一张 21:9 画布图。
            merge_ready = extracted and _stitch_input_file(
                case, cdir, result_png).is_file()
            merge_reason = "采用一张候选，或在素材库里选定一张 21:9 画布图后才能拼回 2:1"
        steps["merge"] = {
            "ok": has_out,
            "ready": merge_ready,
            "reason": "" if has_out else merge_reason,
            "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}；每次合并都会覆盖同名文件",
        }
        steps["compare"] = {
            "ok": has_out,
            "reason": "" if has_out else "合并出片后可以在这里对比",
            "evidence": f"在 {cdir}\\out\\ 检测到成片：{out_names}",
        }
    elif case.flow == "gen":
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
    elif case.flow == "relight":
        gt, atmo = case.relight_slots()
        pair_error = None
        try:
            case_mod.check_relight_pair(case)
        except CaseError as exc:
            pair_error = str(exc)
        slots_ok = gt is not None and atmo is not None and pair_error is None
        if not case.assets:
            asset_reason = "先在素材步选定底版与氛围依据"
        elif gt is None:
            asset_reason = "还缺底版：选一个 2:1 ERP 的视频或图片"
        elif atmo is None:
            asset_reason = "还缺氛围依据：上传或选一张图片，或与视频底版同时长的视频"
        elif pair_error:
            asset_reason = pair_error
        else:
            asset_reason = ""
        current = _relight_current(case, cdir)
        steps = {
            "assets": {
                "ok": slots_ok,
                "reason": asset_reason,
                "evidence": (f"在 {cdir}\\assets\\ 检测到底版 {gt.path} 与氛围依据 "
                             f"{atmo.path}，两个槽位已写入 case.toml" if slots_ok else ""),
            },
            "relight": {
                "ok": current is not None,
                "ready": slots_ok,
                "reason": ("" if current is not None
                           else ("运行重打光：视频可以先出几帧小样看看" if slots_ok
                                 else asset_reason)),
                "evidence": (f"在 {current} 检测到当前成片；再跑一次会写出新的时间戳文件"
                             if current is not None else ""),
            },
            "compare": {
                "ok": current is not None,
                "reason": "" if current is not None else "出片后可以在这里对比",
                "evidence": (f"在 {current} 检测到当前成片"
                             if current is not None else ""),
            },
        }
    else:
        # FLOWS 是闭集，落到这里说明加了 flow 却没写门链——直接报出来，
        # 不能让页面拿到别的流程的门。
        raise ValueError(f"未知 flow：{case.flow}（_gates 没有它的分支）")
    return steps


def _derived(case: case_mod.Case) -> list[dict]:
    """不参与提交的只读条目：源视频、几何契约、manifest、已采用结果。

    clip/pad 已是可勾选候选（见 ``_asset_details``），mask 不出现在面板里。
    """
    rows: list[dict] = []
    cdir = case.dir(_ctx["cases_root"])
    image_flow = case.flow in case_mod.IMAGE_FLOWS
    if case.source_path:
        rows.append({
            "kind": "image" if image_flow else "video", "role": "source",
            "label": "源图" if image_flow else "源视频",
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
    elif case.flow in case_mod.IMAGE_FLOWS:
        d = cdir / "extracts" / (case.extract_current or "")
        geometry = "image.json" if case.flow == "patchimg" else "image_pad.json"
        for name, kind, label, submit in (
                (geometry, "file", "几何契约",
                 "不提交" if case.flow == "patchimg"
                 else "stitch 的几何权威，不提交"),
                ("result.png", "image", "已采用结果", "流程产物，不提交")):
            rows.append({"kind": kind, "role": "derived", "label": label,
                         "editable": False, "submit": submit,
                         **_file_info(d / name)})
    elif case.flow == "gen":
        rows.append({"kind": "video", "role": "derived", "label": "已采用结果",
                     "editable": False, "submit": "流程产物，不提交",
                     **_file_info(cdir / "result.mp4")})
    return rows


def _submit_entries(case: case_mod.Case, assets: list[dict]) -> list[dict]:
    """提交清单的逐条角色：``[submit.roles]`` 覆盖候选的默认角色。"""
    by_id = {row["id"]: row for row in assets}
    out: list[dict] = []
    for item in case.submit_selected or []:
        row = by_id.get(item)
        if row is None:
            continue
        role = (case.submit_roles or {}).get(item) or row["role"]
        out.append({"asset": item, "role": role})
    return out


RELIGHT_REGIONS_FILE = "relight/regions.json"
"""区域覆盖定义的固定落点；``[relight] regions`` 指向它（决策 88）。"""


def _relight_regions_path(case: case_mod.Case, cdir: Path) -> Path | None:
    pointer = (case.relight or {}).get("regions")
    if not pointer:
        return None
    return cdir / pointer


def _relight_regions_payload(case: case_mod.Case,
                             cdir: Path) -> tuple[dict | None, str | None]:
    """区域文件的内容与校验错误：坏文件不阻塞整个 case 页面，只带 error。"""
    path = _relight_regions_path(case, cdir)
    if path is None or not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        parsed = regions_mod.parse_regions(data)
    except (OSError, json.JSONDecodeError, regions_mod.RegionError) as exc:
        return None, str(exc)
    return _canonical_ranges(data, parsed), None


def _canonical_ranges(data: dict, parsed: list,
                      *, drop: bool = False) -> dict:
    """把 ``ranges`` 换成并集后的对象数组；其余字段原样保留。

    前端不做并集，读与写都经过这一处，页面看到的永远是规范形式（决策 91）。
    ``drop`` 为真时（图片底版）一律写 ``null``。
    """
    for raw, region in zip(data["regions"], parsed, strict=True):
        if drop or region.ranges is None:
            raw["ranges"] = None
        else:
            raw["ranges"] = [{"start": item.start, "end": item.end}
                             for item in region.ranges]
    return data


def _relight_payload(case: case_mod.Case) -> dict | None:
    """relight 面板数据：两个槽位、``[relight]`` 草稿、当前成片与小样。

    非 relight 流程返回 None，前端据此不渲染这块。
    """
    if case.flow != "relight":
        return None
    cdir = case.dir(_ctx["cases_root"])
    gt, atmo = case.relight_slots()
    current = _relight_current(case, cdir)
    media = "image" if gt is not None and gt.kind == "image" else "video"
    regions_data, regions_error = _relight_regions_payload(case, cdir)
    return {
        "gt": _slot_payload(case, gt),
        "atmo": _slot_payload(case, atmo),
        "media": media if gt is not None else None,
        "draft": dict(case.relight or {}),
        "regions": regions_data,
        "regions_error": regions_error,
        "current": file_entry(current) if current is not None else None,
        "current_report": (file_entry(current.with_suffix(".json"))
                           if current is not None and current.with_suffix(".json").is_file()
                           else None),
        "samples": _relight_samples(cdir),
        "sample_report": _relight_sample_report(cdir),
    }


def _slot_payload(case: case_mod.Case, asset) -> dict | None:
    if asset is None:
        return None
    path = asset.resolved(case, _ctx["cases_root"])
    return {"asset": asset.id, "kind": asset.kind, "path": asset.path,
            "sha256": asset.sha256, "size": _file_info(path).get("size"),
            "meta": _media_meta(path, asset.kind),
            "entry": file_entry(path)}


def _case_info(name: str) -> dict | None:
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return None
    store = get_store(name) if case.flow in ("patch", "patchimg") else None
    cdir = case.dir(_ctx["cases_root"])
    extracts = (_extracts(case)
                if case.flow in ("patch", *case_mod.IMAGE_FLOWS) else [])
    # 素材明细要解码整批图片，一次请求只算一遍：assets / keyframe / gates 共用。
    assets = _asset_details(case)
    info = {
        "name": case.name,
        "flow": case.flow,
        "source_url": f"/video/source/{case.name}",
        "source_path": case.source_path,
        "source_file": (file_entry(Path(case.source_path))
                        if case.source_path else None),
        "viewport": case.viewport, "inner": case.inner, "frames": case.frames,
        "frame_count": store.frame_count if store else None,
        "fps": store.fps if store else None,
        "duration_s": store.duration_s if store else None,
        "proxy": ({"state": proxy_state(case, _ctx["cases_root"]),
                   "url": f"/video/proxy/{case.name}",
                   "width": proxy_width(case, _ctx["cases_root"])}
                  if case.flow in ("patch", "fullredraw") and case.source_path
                  else None),
        "extract_current": case.extract_current,
        "adopt_round": case.adopt_round,
        "adopt_index": case.adopt_index,
        "merge_input": case.merge_input,
        "assets": assets,
        "raw_frames": _raw_frames(case),
        "keyframe": _keyframe_state(case, assets),
        "submit_selected": case.submit_selected,
        "submit_roles": case.submit_roles,
        "submit_entries": _submit_entries(case, assets),
        "role_help": dict(roles_mod.ROLE_LABELS),
        "roles": roles_mod.roles_payload(),
        "derived": _derived(case),
        "extracts": extracts,
        "outputs": _outputs(case),
        "step_files": _step_files(case),
        "pad_upload": (_pad_upload(cdir)[1]
                       if case.flow == "fullredraw" else None),
        "gates": _gates(case, extracts, assets),
        "output_options": _output_options(case, cdir),
        "previews": {"vp": f"/img/{case.name}/vp.png?k={store.key}"} if store else {},
        "viewport_options": (_viewport_options(case)
                             if case.flow in ("patch", "patchimg") else []),
        "viewport_footprint": _footprint_payload(store) if store else [],
        "coverage": _coverage_payload(store) if store else None,
        "fov_range": [FOV_MIN_DEG, FOV_MAX_DEG],
        "rounds": _rounds(case.name),
        "image_rounds": (_image_rounds(case)
                         if case.flow in case_mod.IMAGE_FLOWS else []),
        "image_report": (_image_report(case)
                         if case.flow in case_mod.IMAGE_FLOWS else None),
        "relight": _relight_payload(case),
        "meta": _meta().read(case.name).as_dict(),
    }
    return info


def _save_gen_mode(name: str, mode: str) -> str | None:
    """创建 gen case 后立即写模式；返回错误文案，成功返回 None。"""
    payload = _vrgen_json(["params", "save", "--job", name,
                           "--mode", mode, "--workdir", str(_workdir())])
    return payload.get("error")


def _image_size(path: Path) -> tuple[int, int] | None:
    """图片宽高 (w, h)：PNG 只读文件头，其余交给 cv2。

    8K 全景图解开一次要一百多 MB；这里只需要一行尺寸给默认草稿用。
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(26)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        return (int.from_bytes(head[16:20], "big"),
                int.from_bytes(head[20:24], "big"))
    image = cv2.imread(str(path))
    if image is None:
        return None
    return (int(image.shape[1]), int(image.shape[0]))


async def api_cases(request):
    if request.method == "GET":
        names = sorted(p.parent.name for p in _root().glob("*/case.toml"))
        infos = await asyncio.to_thread(
            lambda: [i for n in names if (i := _brief(n)) is not None])
        # 默认按「最近活动」倒序：最常动、刚在做的 case 排最前。
        infos.sort(key=lambda i: i.get("updated_ts") or 0.0, reverse=True)
        return JSONResponse(infos)
    # POST：建新 case。gen 不要求源文件但必须选模式；视频流程要视频；图片
    # 流程（patchimg/fullRedrawImg）要 2:1 ERP 图片，比例校验在 case.create。
    body = await request.json()
    flow = body.get("flow")
    if flow not in case_mod.FLOWS:
        return JSONResponse({"error": f"flow 必须是 {' 或 '.join(case_mod.FLOWS)}"},
                            status_code=400)
    image_flow = flow in case_mod.IMAGE_FLOWS
    if flow == "relight":
        # relight 建 case 不绑源：GT 与氛围图进 case 之后在素材步用两个槽位选定。
        if body.get("video") or body.get("upload_id"):
            return JSONResponse(
                {"error": "relight 建 case 不绑源文件；GT 与氛围图在 case 的素材步选定"},
                status_code=400)
    exts = _IMAGE_EXTS if image_flow else _VIDEO_EXTS
    what = "源图片" if image_flow else "源视频"
    raw_source = str(body.get("video") or "")
    upload_id = str(body.get("upload_id") or "")
    if raw_source and upload_id:
        return JSONResponse({"error": "upload_id 与源文件只能二选一"}, status_code=400)
    if upload_id:
        # 外部用户直接上传源文件：复用分片上传会话，落进 sources/ 后走原路径。
        try:
            paths, upload_dirs = _uploaded_paths([upload_id])
        except CaseError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        uploaded = paths[0]
        if uploaded.suffix.lower() not in exts:
            return JSONResponse(
                {"error": f"{what}必须是 {' / '.join(sorted(exts))} 之一"},
                status_code=400)
        target = sources_dir() / uploaded.name
        if target.exists():
            return JSONResponse(
                {"error": f"sources/ 里已有同名文件：{uploaded.name}，请重命名后重新上传"},
                status_code=409)
        sources_dir().mkdir(parents=True, exist_ok=True)
        shutil.move(str(uploaded), str(target))
        for directory in upload_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        raw_source = uploaded.name
    source = sources_dir() / Path(raw_source).name if raw_source else None
    mode = body.get("mode")
    if flow == "gen":
        if mode not in case_mod.GEN_MODES:
            return JSONResponse(
                {"error": f"gen 必须选择模式：{' / '.join(case_mod.GEN_MODES)}"},
                status_code=400)
        if source is not None and not source.is_file():
            return JSONResponse({"error": f"sources/ 里没有这个视频：{source.name}"},
                                status_code=400)
        if source is None and not str(body.get("name") or "").strip():
            return JSONResponse({"error": "没有视频素材时，必须填写 case 名"},
                                status_code=400)
    elif flow != "relight" and (source is None or not source.is_file()):
        return JSONResponse({"error": f"sources/ 里没有这个{what}"}, status_code=400)

    name = str(body.get("name") or (source.stem if source else "")).strip()
    if not name:
        return JSONResponse({"error": "必须填写 case 名"}, status_code=400)
    n = w = 0
    fps = 30.0
    if source is not None and not image_flow:
        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            return JSONResponse({"error": "打不开这个视频"}, status_code=400)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
    elif source is not None:
        size = _image_size(source)
        if size is None:
            return JSONResponse({"error": "打不开这张图片"}, status_code=400)
        w = size[0]
        fps = 0.0
    try:
        case = case_mod.create(name, flow, source, root=_ctx["cases_root"])
        if flow == "patch":
            case_mod.set_draft(name, root=_ctx["cases_root"],
                               **case_mod.patch_default_draft(w, n))
        elif flow == "patchimg":
            case_mod.set_draft(name, root=_ctx["cases_root"],
                               **case_mod.patchimg_default_draft(w))
    except CaseError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    if flow == "gen":
        error = await asyncio.to_thread(_save_gen_mode, name, mode)
        if error:
            return JSONResponse(
                {"error": f"case 已创建，但模式没保存：{error}",
                 "name": name, "created": True}, status_code=502)
    return JSONResponse({"name": case.name, "flow": flow,
                         "fps": round(fps, 3), "width": w})


def _last_activity(case: case_mod.Case) -> tuple[str | None, float]:
    """「最近活动」：配置、首尾帧草稿、成片、抽取与生成轮次里最新的 mtime。

    只看文件名与 stat，不解码、不哈希；列表按它排序，所以口径要覆盖用户真正
    会产生的动作：改配置写 case.toml，定稿写 keyframe.toml，抽取建 extracts/
    目录，合并写成片，提交/轮询写 vrgen 轮次 run.json。
    """
    cdir = case.dir(_ctx["cases_root"])
    paths = [cdir / "case.toml", cdir / "keyframe.toml",
             cdir / "extracts", cdir / "extracts" / (case.extract_current or "")]
    odir = cdir / "out"
    if odir.is_dir():
        paths.append(odir)
        paths.extend(p for p in odir.iterdir() if p.is_file())
    runs = _workdir() / case.name
    if runs.is_dir():
        paths.append(runs)
        paths.extend(d / "run.json" for d in runs.iterdir() if d.is_dir())
    best = 0.0
    for path in paths:
        try:
            best = max(best, path.stat().st_mtime)
        except OSError:
            continue
    if not best:
        return None, 0.0
    return datetime.fromtimestamp(best).strftime("%Y-%m-%d %H:%M"), best


def _brief(name: str) -> dict | None:
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return None
    extracts = _extracts(case) if case.flow == "patch" else []
    files = _output_files(case)
    # 列表页只算「下一步」需要的门：不算素材明细、不解码图片、不哈希文件。
    gates = _gates(case, extracts, outs=[{"file": p.name} for p, _v, _s in files],
                   list_only=True)
    updated_at, updated_ts = _last_activity(case)
    return {"name": case.name, "flow": case.flow,
            "gates": gates,
            "updated_at": updated_at,
            "updated_ts": updated_ts,
            "outputs": len(files),
            "owner": _meta().read(name).owner_name,
            "task_status": None}


def _is_16_9(width: int, height: int) -> bool:
    ratio = width / height if height else 0.0
    return abs(ratio - 16 / 9) <= (16 / 9) * 0.001


def _selected_candidates(case: case_mod.Case) -> list:
    ids = case.submit_selected or []
    by_id = {c.id: c for c in candidates(case, _ctx["cases_root"])}
    out = []
    for item in ids:
        candidate = by_id.get(item)
        if candidate is None:
            continue
        role = case.submit_role(item)
        out.append(replace(candidate, role=role)
                   if role and role != candidate.role else candidate)
    return out


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


def _duration_limit(info: dict, mode: str | None) -> tuple[int, int] | None:
    """当前模式允许的时长区间（来自 ``params show`` 的 modes[].duration）。"""
    for item in (info.get("generation_params") or {}).get("modes") or []:
        if item.get("value") != mode:
            continue
        duration = item.get("duration") or {}
        low, high = duration.get("min"), duration.get("max")
        if isinstance(low, int) and isinstance(high, int) and low and high:
            return low, high
    return None


def _clip_seconds(case: case_mod.Case) -> float | None:
    """当前 clip 的精确时长（秒）；读不到返回 None。

    平台按片段真实时长判超限，不是按取整后的输出档，所以这里读 clip.json
    的帧数与帧率，别用 ``clip_duration_s`` 的四舍五入值。
    """
    if case.flow != "patch" or not case.extract_current:
        return None
    from ..patch.sidecar import load_sidecar

    path = case.dir(_ctx["cases_root"]) / "extracts" / case.extract_current / "clip.json"
    try:
        sidecar = load_sidecar(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not sidecar.segments or sidecar.fps <= 0:
        return None
    segment = sidecar.segments[0]
    frames = int(segment.frame_end) - int(segment.frame_start) + 1
    return round(frames / sidecar.fps, 1) if frames > 0 else None


def _apply_mode_gate(info: dict, case: case_mod.Case, mode: str | None) -> None:
    gate = (info.get("gates") or {}).get("generate")
    if gate is None or not mode:
        return
    if case.flow in case_mod.IMAGE_FLOWS:
        # 图片流程按候选张数生成、参考图可选，没有"必须勾选素材"的模式约束：
        # 生成步的放行由「是否已抽取」决定，不要被视频的选片规则改写。
        return
    ready, reason = _mode_ready(case, mode)
    if not ready:
        gate["ready"] = False
        gate["reason"] = reason
        return
    # clip 的真实时长必须落在模式的区间内：超了平台只会回一句「素材 clip
    # 不合约」，看不出是哪一项，所以在提交前把真实原因说出来。
    limit = _duration_limit(info, mode)
    seconds = _clip_seconds(case)
    if seconds is None:
        fixed = (info.get("generation_params") or {}).get("fixed") or {}
        raw = fixed.get("duration_s")
        seconds = float(raw) if isinstance(raw, (int, float)) else None
    if seconds is not None and limit and not limit[0] <= seconds <= limit[1]:
        gate["ready"] = False
        gate["reason"] = (
            f"clip 时长 {seconds:g} 秒，超出 {mode} 的 {limit[0]}–{limit[1]} 秒；"
            f"把抽取范围缩到 {limit[1]} 秒以内，或换一个允许更长片段的模式/模型")


async def _delete_case(request, name: str):
    """删除 case 目录与 vrgen 台账；sources/ 与 logs/ 不动。

    不可逆，所以两道闸门：case 被他人占用、或有运行中/排队任务时拒绝。
    """
    try:
        await asyncio.to_thread(case_mod.load, name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    lock = _locks().read(name)
    user = current_user(request)
    if lock is not None and lock.owner_id != getattr(user, "id", None):
        return JSONResponse(
            {"error": f"case 正被 {lock.owner_name or '其他人'} 使用，等对方关闭后再删"},
            status_code=409)
    if QUEUE.active_for(name):
        return JSONResponse(
            {"error": "case 有运行中或排队任务，等任务结束后再删"}, status_code=409)
    run_dir = _workdir() / name
    try:
        await asyncio.to_thread(case_mod.delete, name, _ctx["cases_root"])
    except CaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if run_dir.is_dir():
        try:
            await asyncio.to_thread(shutil.rmtree, run_dir)
        except OSError as exc:
            return JSONResponse(
                {"error": f"case 目录已删，但 vrgen 台账删不掉：{exc}"},
                status_code=500)
    return JSONResponse({"deleted": name})


async def api_case(request):
    name = request.path_params["name"]
    if request.method == "DELETE":
        return await _delete_case(request, name)
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
    meta = await asyncio.to_thread(_meta().ensure, name, current_user(request))
    info["meta"] = meta.as_dict()
    return JSONResponse(info)


async def api_relight_draft(request):
    """写 relight 的 ``[relight]`` 草稿：参数面板的持久值，权威仍在校验过的 case 模型。"""
    name = request.path_params["name"]
    body = await request.json()
    try:
        case_mod.set_relight(name, body, _ctx["cases_root"])
    except CaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(await asyncio.to_thread(_info_with_generation, name))


async def api_relight_regions(request):
    """读写区域覆盖定义（决策 88）：GET 取整份 JSON，POST 校验后整份落盘。"""
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    if case.flow != "relight":
        return JSONResponse({"error": f"case {name} 是 {case.flow} 流，不是 relight"},
                            status_code=400)
    cdir = case.dir(_ctx["cases_root"])
    if request.method == "GET":
        data, error = await asyncio.to_thread(_relight_regions_payload, case, cdir)
        return JSONResponse({"regions": data, "error": error})

    body = await request.json()
    try:
        parsed = regions_mod.parse_regions(body)
    except regions_mod.RegionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    # 图片底版没有时间维度：落盘前一律清空，与前端保存规则一致。
    gt, _ = case.relight_slots()
    body = _canonical_ranges(
        body, parsed, drop=gt is not None and gt.kind == "image")
    path = cdir / RELIGHT_REGIONS_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return JSONResponse({"error": f"写区域文件失败：{exc}"}, status_code=500)
    try:
        case_mod.set_relight(name, {"regions": RELIGHT_REGIONS_FILE},
                             _ctx["cases_root"])
    except CaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(await asyncio.to_thread(_info_with_generation, name))


async def api_case_lock(request):
    name = request.path_params["name"]
    try:
        case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    user = current_user(request)
    if request.method == "GET":
        lock = _locks().read(name)
        return JSONResponse({
            "lock": None if lock is None else lock.as_dict(),
            "mine": bool(lock and lock.owner_id == user.id),
        })
    if request.method == "DELETE":
        released = _locks().release(name, user)
        return JSONResponse({"released": released})
    try:
        lock = _locks().acquire(name, user)
    except LockConflict as exc:
        return JSONResponse({"error": str(exc), "lock": exc.current.as_dict()},
                            status_code=409)
    return JSONResponse({"lock": lock.as_dict(), "mine": True})


async def api_case_owner(request):
    """归属到我的名下；revision 检查与推进由 CaseRevisionMiddleware 统一做。"""
    name = request.path_params["name"]
    try:
        case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    meta = await asyncio.to_thread(_meta().set_owner, name, current_user(request))
    return JSONResponse({"meta": meta.as_dict()})


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


async def api_uploads_create(request):
    body = await request.json()
    name = Path(str(body.get("name") or "")).name
    if not name or name in (".", ".."):
        return JSONResponse({"error": "缺少文件名"}, status_code=400)
    suffix = Path(name).suffix.lower()
    if suffix not in _VIDEO_EXTS | _IMAGE_EXTS:
        allowed = "/".join(sorted(_VIDEO_EXTS | _IMAGE_EXTS))
        return JSONResponse({"error": f"不支持的文件类型：{suffix or '无后缀'}（允许 {allowed}）"},
                            status_code=400)
    try:
        size = int(body.get("size"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "缺少文件大小 size"}, status_code=400)
    if size <= 0:
        return JSONResponse({"error": "文件大小必须大于 0"}, status_code=400)
    if size > UPLOAD_MAX_BYTES:
        return JSONResponse({"error": f"文件超过上限 {UPLOAD_MAX_BYTES} 字节"},
                            status_code=413)
    try:
        _cleanup_uploads()
    except OSError:
        pass
    upload_id = uuid.uuid4().hex
    directory = _uploads_root() / upload_id
    directory.mkdir(parents=True)
    (directory / "data.part").touch()
    meta = {
        "upload_id": upload_id,
        "name": name,
        "size": size,
        "received": 0,
        "status": "uploading",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sha256": None,
        "stored_name": None,
    }
    _save_upload_meta(directory, meta)
    return JSONResponse({"upload_id": upload_id, "chunk_size": UPLOAD_CHUNK,
                         "max_size": UPLOAD_MAX_BYTES})


async def api_upload(request):
    upload_id = request.path_params["upload_id"]
    try:
        directory = _upload_dir(upload_id)
        meta = _upload_meta(directory)
    except CaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if request.method == "GET":
        return JSONResponse({key: meta.get(key) for key in
                             ("upload_id", "name", "size", "received", "status", "sha256")})
    if request.method == "DELETE":
        shutil.rmtree(directory, ignore_errors=True)
        return JSONResponse({"deleted": upload_id})
    if meta.get("status") != "uploading":
        return JSONResponse({"error": "这个上传已经完成，不能再追加"}, status_code=409)
    try:
        offset = int(request.query_params.get("offset", "0"))
    except ValueError:
        return JSONResponse({"error": "offset 必须是整数"}, status_code=400)
    if offset != meta["received"]:
        return JSONResponse({"error": "偏移不连续，请从已接收位置续传",
                             "received": meta["received"]}, status_code=409)
    body = await request.body()
    if not body:
        return JSONResponse({"error": "分片为空"}, status_code=400)
    if meta["received"] + len(body) > meta["size"]:
        return JSONResponse({"error": "分片超过声明的文件大小"}, status_code=400)
    part = directory / "data.part"
    with open(part, "r+b") as fh:
        fh.seek(offset)
        fh.write(body)
    meta["received"] = offset + len(body)
    _save_upload_meta(directory, meta)
    return JSONResponse({"upload_id": upload_id, "received": meta["received"],
                         "size": meta["size"]})


async def api_upload_complete(request):
    upload_id = request.path_params["upload_id"]
    try:
        directory = _upload_dir(upload_id)
        meta = _upload_meta(directory)
    except CaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if meta.get("status") == "complete":
        return JSONResponse({"file_id": upload_id, "name": meta["name"],
                             "size": meta["size"], "sha256": meta["sha256"]})
    if meta.get("received") != meta.get("size"):
        return JSONResponse({"error": "文件还没有传完",
                             "received": meta.get("received"),
                             "size": meta.get("size")}, status_code=400)
    part = directory / "data.part"
    if not part.is_file():
        return JSONResponse({"error": "分片文件不存在"}, status_code=400)
    sha256 = await asyncio.to_thread(case_mod.file_sha256, part)
    target = _uploads_root() / "complete" / upload_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.move(str(directory), str(target))
    (target / "data.part").rename(target / meta["name"])
    meta.update(status="complete", sha256=sha256, stored_name=meta["name"])
    _save_upload_meta(target, meta)
    return JSONResponse({"file_id": upload_id, "name": meta["name"],
                         "size": meta["size"], "sha256": sha256})


def _uploaded_paths(upload_ids: list[str]) -> tuple[list[Path], list[Path]]:
    """把已完成的 upload id 解析成文件路径；返回 (路径, 会话目录)。"""
    paths: list[Path] = []
    dirs: list[Path] = []
    for upload_id in upload_ids:
        directory = _upload_dir(upload_id)
        meta = _upload_meta(directory)
        if meta.get("status") != "complete":
            raise CaseError(f"上传 {upload_id} 还没有完成")
        path = directory / str(meta.get("stored_name") or "")
        if not path.is_file():
            raise CaseError(f"上传 {upload_id} 的文件不存在")
        paths.append(path)
        dirs.append(directory)
    return paths, dirs


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
            roles = [str(r) for r in (body.get("roles") or [])]
            upload_ids = [str(i) for i in (body.get("upload_ids") or [])]
            if upload_ids:
                if len(upload_ids) != len(roles):
                    return JSONResponse({"error": "upload_ids 与 roles 要一一对应"},
                                        status_code=400)
                paths, dirs = _uploaded_paths(upload_ids)
                known = {a.sha256 for a in case.assets if a.sha256}
                seen: set[str] = set()
                for path in paths:
                    digest = case_mod.file_sha256(path)
                    if digest in known or digest in seen:
                        return JSONResponse(
                            {"error": f"素材已存在：{path.name}（SHA-256 相同）"},
                            status_code=409)
                    seen.add(digest)
                await asyncio.to_thread(
                    case_mod.add_assets, name,
                    list(zip(paths, roles, strict=True)), _ctx["cases_root"])
                for directory in dirs:
                    shutil.rmtree(directory, ignore_errors=True)
            else:
                paths = [str(p) for p in (body.get("paths") or [])]
                if not paths or len(paths) != len(roles):
                    return JSONResponse({"error": "paths 与 roles 要一一对应且非空"},
                                        status_code=400)
                await asyncio.to_thread(case_mod.add_assets, name,
                                        list(zip(paths, roles, strict=True)), _ctx["cases_root"])
        elif op == "slot":
            # relight 的槽位：role + 文件 = 加一条候选并选中；role + asset_id = 只改指针。
            role = str(body.get("role") or "")
            asset_id = str(body.get("asset_id") or "")
            slot = "gt" if role == case_mod.RELIGHT_GT else "atmo"
            upload_ids = [str(i) for i in (body.get("upload_ids") or [])]
            if asset_id:
                await asyncio.to_thread(case_mod.set_relight_pick, name, slot,
                                        asset_id, _ctx["cases_root"])
            elif upload_ids or body.get("path"):
                if upload_ids:
                    if len(upload_ids) != 1:
                        return JSONResponse({"error": "槽位一次只收一个文件"},
                                            status_code=400)
                    paths, dirs = _uploaded_paths(upload_ids)
                else:
                    paths, dirs = [Path(str(body["path"]))], []
                await asyncio.to_thread(case_mod.add_relight_candidate, name, role,
                                        paths[0], _ctx["cases_root"])
                for directory in dirs:
                    shutil.rmtree(directory, ignore_errors=True)
            else:
                return JSONResponse(
                    {"error": "槽位需要 asset_id、upload_ids 或 path"}, status_code=400)
        elif op == "merge-input":
            # 三种用法：asset_id 选库里已有的；upload_ids 上传入库并选中；
            # 都没有 = 清空指针、回退采用结果（决策 66/67）。
            asset_id = str(body.get("asset_id") or "")
            upload_ids = [str(i) for i in (body.get("upload_ids") or [])]
            if asset_id:
                await asyncio.to_thread(case_mod.set_merge_input, name, asset_id,
                                        _ctx["cases_root"])
            elif upload_ids:
                if len(upload_ids) != 1:
                    return JSONResponse({"error": "拼接输入一次只收一个文件"},
                                        status_code=400)
                paths, dirs = _uploaded_paths(upload_ids)
                await asyncio.to_thread(case_mod.add_merge_input, name, paths[0],
                                        _ctx["cases_root"])
                for directory in dirs:
                    shutil.rmtree(directory, ignore_errors=True)
            else:
                await asyncio.to_thread(case_mod.set_merge_input, name, None,
                                        _ctx["cases_root"])
        elif op == "remove":
            asset_id = str(body.get("id") or "")
            await asyncio.to_thread(case_mod.remove_asset, name,
                                    asset_id, _ctx["cases_root"])
            await asyncio.to_thread(keyframe_mod.remove_asset,
                                    case.dir(_ctx["cases_root"]), asset_id)
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
    info["meta"] = _meta().read(name).as_dict()
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
        return JSONResponse({"selected": case.submit_selected,
                             "roles": case.submit_roles})
    body = await request.json()
    if "selected" not in body:
        return JSONResponse({"error": "缺少 selected"}, status_code=400)
    selected = body.get("selected")
    if selected is not None:
        if not isinstance(selected, list):
            return JSONResponse({"error": "selected 必须是数组"}, status_code=400)
        selected = [str(item) for item in selected]
    roles = body.get("roles")
    if roles is not None:
        if not isinstance(roles, dict):
            return JSONResponse({"error": "roles 必须是对象"}, status_code=400)
        roles = {str(key): str(value) for key, value in roles.items()}
    try:
        await asyncio.to_thread(case_mod.set_submit_selected, name, selected,
                                _ctx["cases_root"], roles=roles)
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

#: 定稿来自「直接用原始帧」时写进 keyframe.toml 的轮次名（不是真轮次）。
RAW_PICK_ROUND = "extract"


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


def _keyframe_refs(case: case_mod.Case) -> tuple[keyframe_mod.State, list[keyframe_mod.Ref]]:
    """读 refs，并把“素材库恰好一张 subject_ref”当作未写入的默认清单。"""
    try:
        state = keyframe_mod.load(case.dir(_ctx["cases_root"]))
    except keyframe_mod.KeyframeError as exc:
        raise CaseError(str(exc)) from exc
    refs = list(state.refs)
    if not refs:
        sheets = [a for a in case.assets
                  if a.kind == "image" and a.role == "subject_ref"]
        if len(sheets) == 1:
            refs = [keyframe_mod.Ref(asset=sheets[0].id, role="subject_ref")]
    return state, refs


def _keyframe_inputs(case: case_mod.Case, step: str, *,
                     require_first: bool = True,
                     allow_stale: bool = False) -> list[tuple[Path, str, str]]:
    """图片步的最终参考图顺序：原始帧、主三视图、（尾帧）首帧定稿、细节图。

    这里不读 ``[submit]``：视频提交清单与图片步是两份独立的输入声明。

    ``allow_stale`` 放行的场景只有「不依赖旧定稿」的两处：提示词的看与改、
    以及首帧候选的生成——它们只用到当前抽取的原始帧与三视图。尾帧生成与
    交接依赖首帧定稿，旧定稿属于旧抽取，必须先在当前抽取上重新定稿。
    """
    state, refs = _keyframe_refs(case)
    stale = bool(state.extract and case.extract_current
                 and state.extract != case.extract_current)
    if stale and not allow_stale:
        raise CaseError("抽取版本已更新，需要重新定稿两帧")
    primary = [ref for ref in refs if ref.role == "subject_ref"]
    if len(primary) != 1:
        raise CaseError(
            "参考图清单只能有一张人物三视图；先加入一张并设为主三视图"
            if primary else
            "参考图清单缺少人物三视图；先加入一张并设为主三视图")
    if not case.extract_current:
        raise CaseError("工作台需要 patch 的当前 extract（首尾原始帧随 extract 产出）")
    raw_dir = case.dir(_ctx["cases_root"]) / "extracts" / case.extract_current
    raw_key = "first_raw" if step == "first" else "last_raw"
    raw_role = "first_frame_raw" if step == "first" else "last_frame_raw"
    pairs: list[tuple[Path, str, str]] = [
        (raw_dir / RAW_FRAME_FILES[raw_key], raw_role, "")]

    def resolved(ref: keyframe_mod.Ref) -> Path:
        asset = case.asset(ref.asset)
        if asset.kind != "image":
            raise CaseError(f"图片步参考图必须是图片：{ref.asset}")
        path = asset.resolved(case, _ctx["cases_root"])
        if not path.is_file():
            raise CaseError(f"参考图不存在：{path}")
        return path

    pairs.append((resolved(primary[0]), "subject_ref", ""))
    if step == "last":
        # 旧抽取上定稿的首帧不能当尾帧参考：它画的是旧版本的画面。
        first = None if stale else state.first
        if not first and require_first:
            raise CaseError(
                "抽取版本已更新：先生成并挑选首帧定稿，再生成尾帧" if stale
                else "先生成并挑选首帧定稿，再生成尾帧")
        if first:
            first_path = case.asset(first.asset).resolved(case, _ctx["cases_root"])
            if not first_path.is_file():
                raise CaseError(f"首帧定稿不存在：{first_path}")
            pairs.append((first_path, "first_frame", ""))
    for ref in refs:
        if ref.role == "detail_ref":
            pairs.append((resolved(ref), "detail_ref", ref.note))
    return pairs


def _keyframe_argv(name: str, body: dict) -> list[str]:
    case = case_mod.load(name, _ctx["cases_root"])
    if case.flow != "patch":
        raise CaseError("首尾帧工作台首期只接 patch（gen 的入口待上传原始帧的入口）")
    step = _keyframe_step(body.get("step"))
    # 生成首帧只用到当前抽取的原始帧与三视图，旧定稿作废不影响它；
    # 生成尾帧由 _keyframe_inputs 自己按 stale 判「先重定首帧」。
    pairs = _keyframe_inputs(case, step, allow_stale=True)
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
    for path, role, note in pairs:
        argv += ["--image", str(path), "--image-role", role,
                 "--image-note", note]
    if body.get("image_model_id") is not None:
        argv += ["--image-model-id", str(body["image_model_id"])]
    if body.get("resolution"):
        argv += ["--resolution", str(body["resolution"])]
    if body.get("num") is not None:
        argv += ["--num", str(body["num"])]
    return argv


def _step_payload(payload: dict) -> dict:
    """两帧步共用的 vars 草稿里，另一帧的变量不属于当前模板，不返回给页面。

    展开提示词时 format_map 只取模板正文引用的键，裁掉它们不影响保存：
    草稿文件里另一帧的值原样保留。
    """
    placeholders = set(payload.get("placeholders") or [])
    payload["vars"] = {key: value for key, value in payload.get("vars", {}).items()
                       if key in placeholders}
    return payload


def _keyframe_prompt_argv(name: str, step: str, pairs: list) -> list[str]:
    argv = ["prompt", "show", "--job", name,
            "--template", KEYFRAME_TEMPLATES[step],
            "--vars-file", KEYFRAME_VARS,
            "--workdir", str(_workdir())]
    for _path, role, note in pairs:
        argv += ["--image-role", role, "--image-note", note]
    return argv


def _seed_keyframe_vars(name: str, step: str, pairs: list) -> dict:
    """读草稿并把模板新增的槽位按默认值补进去。

    槽位清单以模板占位符为准，这里不维护第二份键白名单：``prompt show``
    报出的 placeholders 与草稿现有 ``vars`` 的差集就是要补的键。旧 case 的
    四键草稿因此能直接跨模板版本继续用，不需要手工迁移。
    """
    show = _keyframe_prompt_argv(name, step, pairs)
    payload = _vrgen_json(show)
    if "error" in payload:
        raise CaseError(payload["error"])
    have = payload.get("vars") or {}
    computed = set(payload.get("computed_keys") or ())
    missing = [key for key in payload.get("placeholders") or ()
               if key not in have and key not in computed]
    if not missing:
        return payload
    seed = ["prompt", "save", "--job", name,
            "--template", KEYFRAME_TEMPLATES[step],
            "--vars-file", KEYFRAME_VARS,
            "--workdir", str(_workdir())]
    for key in missing:
        seed += ["--set", f"{key}={DEFAULT_KEYFRAME_VARS.get(key, '')}"]
    saved = _vrgen_json(seed)
    if "error" in saved:
        raise CaseError(saved["error"])
    # save 只回草稿概览，没有展开后的正文；补完键再读一次拿 rendered。
    payload = _vrgen_json(show)
    if "error" in payload:
        raise CaseError(payload["error"])
    return payload


def _keyframe_prompt(name: str, step: str) -> dict:
    """读图片步的提示词草稿并展开；缺失的槽位按默认值补写。

    尾帧预览在首帧定稿前也允许读取（少图3 的分工说明），否则尾帧槽位的
    显示名与说明要等首帧挑完才能出现。"""
    case = case_mod.load(name, _ctx["cases_root"])
    step = _keyframe_step(step)
    pairs = _keyframe_inputs(case, step, require_first=False, allow_stale=True)
    return _step_payload(_seed_keyframe_vars(name, step, pairs))


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
            path = Path(raw)
            if path.is_file():
                results.append({"index": index,
                                "url": f"/img/round/{name}/{directory.name}/{index}",
                                **file_entry(path)})
        params = blob.get("params") or {}
        rows.append({"round": directory.name, "key": blob.get("key", ""),
                     "stage": blob.get("stage", ""),
                     "step": str(params.get("step") or ""),
                     # 旧轮次台账记的是 model_id（图片轮次），读取时兼容
                     "image_model_id": params.get(
                         "image_model_id", params.get("model_id")),
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
    QUEUE.submit("keyframe", name, argv, owner=current_user(request),
                 request_id=getattr(request.state, "rid", None),
                 billing=await asyncio.to_thread(_billing, name))
    return JSONResponse({"submitted": "keyframe", "case": name})


async def api_keyframe_rounds(request):
    name = request.path_params["name"]
    if not case_mod.case_toml(name, _ctx["cases_root"]).is_file():
        return JSONResponse({"error": "no such case"}, status_code=404)
    return JSONResponse(await asyncio.to_thread(_keyframe_rounds, name))


def _keyframe_save_prompt(name: str, step: str, sets: dict) -> dict:
    case = case_mod.load(name, _ctx["cases_root"])
    step = _keyframe_step(step)
    pairs = _keyframe_inputs(case, step, allow_stale=True)
    argv = ["prompt", "save", "--job", name,
            "--template", KEYFRAME_TEMPLATES[step],
            "--vars-file", KEYFRAME_VARS,
            "--workdir", str(_workdir())]
    for key, value in sets.items():
        argv += ["--set", f"{key}={value}"]
    for _path, role, note in pairs:
        argv += ["--image-role", role, "--image-note", note]
    payload = _vrgen_json(argv)
    if "error" in payload:
        raise CaseError(payload["error"])
    return _step_payload(payload)


async def api_keyframe_refs(request):
    """图片步参考图清单：GET 只读，PUT 全量替换。"""
    name = request.path_params["name"]
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return JSONResponse({"error": "no such case"}, status_code=404)
    if request.method == "GET":
        state = _keyframe_state(case)
        return JSONResponse({"refs": state["refs"],
                             "role_help": state["role_help"]})
    body = await request.json()
    raw = body.get("refs")
    if not isinstance(raw, list):
        return JSONResponse({"error": "refs 必须是数组"}, status_code=400)
    refs: list[keyframe_mod.Ref] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return JSONResponse({"error": f"refs[{index}] 必须是对象"},
                                status_code=400)
        refs.append(keyframe_mod.Ref(
            asset=str(item.get("asset") or "").strip(),
            role=str(item.get("role") or "subject_ref").strip(),
            note=str(item.get("note") or "").strip()))
    image_ids = {asset.id for asset in case.assets if asset.kind == "image"}
    try:
        refs = keyframe_mod.validate_refs(refs, image_ids)
        state = keyframe_mod.load(case.dir(_ctx["cases_root"]))
        state.refs = refs
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


def _keyframe_use_raw(name: str, step: str) -> dict:
    """把当前抽取的原始首/尾帧直接当定稿，不花钱、不重绘。

    落进 ``[[assets]]`` 的角色是 ``first_frame`` / ``last_frame``（不是保留角色
    ``*_raw``），所以交接给视频与之后的提交都走常规路径，``--refs`` 里仍然不会
    出现原始帧角色。这一步同时把这一对定稿锚到当前抽取版本：首帧是这一对的锚，
    落尾帧不移动锚点（过期状态下挑尾帧会拼出跨版本的一对，直接拒绝）。
    """
    case = case_mod.load(name, _ctx["cases_root"])
    if case.flow != "patch":
        raise CaseError("首尾帧工作台首期只接 patch（gen 的首尾帧入口另接）")
    step = _keyframe_step(step)
    if not case.extract_current:
        raise CaseError("还没有抽取版本：首尾原始帧随 extract 产出")
    cdir = case.dir(_ctx["cases_root"])
    state = keyframe_mod.load(cdir)
    if step == "last" and state.extract and state.extract != case.extract_current:
        raise CaseError("抽取版本已更新：先把首帧定稿到当前版本，再定尾帧")
    raw = (cdir / "extracts" / case.extract_current
           / RAW_FRAME_FILES["first_raw" if step == "first" else "last_raw"])
    if not raw.is_file():
        raise CaseError(f"当前抽取版本没有这张原始帧：{raw.name}；重新抽取一次")
    role = "first_frame" if step == "first" else "last_frame"
    updated = case_mod.add_assets(name, [(raw, role)], _ctx["cases_root"])
    new_id = updated.assets[-1].id
    state.extract = case.extract_current
    if step == "first":
        state.first = keyframe_mod.Pick(RAW_PICK_ROUND, 1, new_id)
        state.last = None
        hint = "首帧用原始帧定稿；尾帧可以同样用原始帧，或生成重绘候选"
    else:
        state.last = keyframe_mod.Pick(RAW_PICK_ROUND, 1, new_id)
        hint = "尾帧用原始帧定稿，现在可以交接给视频"
    keyframe_mod.save(cdir, state)
    info = _info_with_generation(name)
    info["keyframe_hint"] = hint
    return info


async def api_keyframe_use_raw(request):
    name = request.path_params["name"]
    body = await request.json()
    try:
        info = await asyncio.to_thread(
            _keyframe_use_raw, name, str(body.get("step") or "first"))
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
            # 尾帧必须与「当前定稿的首帧」同宽高——不是 assets 里最老的那张首帧。
            # 旧抽取上定稿的首帧已经作废，这时挑尾帧只会拼出一对跨版本的图。
            current = case_mod.load(name, _ctx["cases_root"])
            state = keyframe_mod.load(current.dir(_ctx["cases_root"]))
            if state.extract and current.extract_current \
                    and state.extract != current.extract_current:
                return JSONResponse(
                    {"error": "抽取版本已更新：先生成并挑选首帧定稿，再挑尾帧"},
                    status_code=400)
            first = (next((a for a in current.assets if a.id == state.first.asset), None)
                     if state.first is not None else None)
            if first is None:
                # 还没有定稿首帧（例如 gen 的首尾帧入口）：退回最近加入的那张首帧稿。
                first = next((a for a in reversed(current.assets)
                              if a.role == "first_frame"), None)
            if first is not None:
                other = await asyncio.to_thread(
                    _media_meta, first.resolved(current, _ctx["cases_root"]), "image")
                if (int(other.get("width") or 0),
                        int(other.get("height") or 0)) != (width, height):
                    return JSONResponse(
                        {"error": (f"尾帧与当前首帧 {first.id} 的宽高不一致"
                                   f"（{other.get('width')}x{other.get('height')} vs "
                                   f"{width}x{height}）；请按首帧的分辨率重新出图")},
                        status_code=400)
        info = await asyncio.to_thread(_keyframe_accept, name, role, round_name,
                                       index, target)
    except (CaseError, OSError, VideoIOError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        target.unlink(missing_ok=True)
    return JSONResponse(info)


def _keyframe_accept(name: str, role: str, round_name: str, index: int,
                     target: Path) -> dict:
    """落素材、更新 keyframe.toml；首帧换新时清掉旧尾帧。

    这里只写台账与素材，不提交生成批次：消耗额度的操作只能由用户点
    「生成首帧候选 / 生成尾帧候选」发起，定稿不产生副作用。
    """
    case = case_mod.load(name, _ctx["cases_root"])
    cdir = case.dir(_ctx["cases_root"])
    state = keyframe_mod.load(cdir)
    old_last = state.last
    same_first = (role == "first_frame" and state.first is not None
                  and state.first.round == round_name and state.first.index == index)
    updated = case_mod.add_assets(name, [(target, role)], _ctx["cases_root"])
    new_id = updated.assets[-1].id
    if role == "first_frame":
        # 首帧是这一对定稿的锚：它落下时，这一对才属于当前抽取版本。
        state.extract = case.extract_current
        state.first = keyframe_mod.Pick(round_name, index, new_id)
        if not same_first:
            state.last = None
        keyframe_mod.save(cdir, state)
        if old_last is not None and not same_first:
            selected = [x for x in (updated.submit_selected or [])
                        if x != old_last.asset]
            if selected != (updated.submit_selected or []):
                case_mod.set_submit_selected(name, selected, _ctx["cases_root"])
        hint = "首帧已定稿；尾帧不会自动生成，点「生成尾帧候选」提交"
    else:
        # 尾帧跟着当前首帧走，不改写版本锚点（过期状态在挑片入口已经拦住）。
        state.last = keyframe_mod.Pick(round_name, index, new_id)
        keyframe_mod.save(cdir, state)
        hint = "尾帧已定稿"
    info = _info_with_generation(name)
    info["keyframe_hint"] = hint
    return info


async def round_img(request):
    """一轮结果的第 N 张图：按 ``<case>/<round>/run.json`` 的 results 下标取。

    与 flow 无关：首尾帧工作台、patchimg 与 fullRedrawImg 的候选网格都从这里
    取图；名字里不再带 keyframe，它取的是轮次结果，不是首尾帧专用。
    """
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
    # save 只回草稿概览（没有 rendered/render_error），页面据此判断不了还差哪些变量、
    # 也刷新不了正文；再读一次 overview，读失败就保留 save 的回显。
    overview = await asyncio.to_thread(_prompt, name, case.flow, mode)
    if "error" not in overview:
        payload = overview
    info = await asyncio.to_thread(_case_info, name)
    info["prompt"] = payload
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
               ("video_model_id", "image_model_id", "resolution", "scale",
                "mode", "aspect_ratio",
                "session_id", "project_id", "episode")):
        return JSONResponse({"error": "没有要保存的参数"}, status_code=400)
    argv = ["params", "save", "--job", name]
    if resolution:
        argv += ["--resolution", resolution]
    if scale:
        argv += ["--scale", scale]
    for field, flag in (("video_model_id", "--video-model-id"),
                        ("image_model_id", "--image-model-id"),
                        ("mode", "--mode"),
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
    if case.flow not in ("patch", "patchimg"):
        return JSONResponse(
            {"error": f"{case.flow} 没有选区草稿：视口与 inner 只用于 "
                      f"patch（视频局部）与 patchimg（图片局部）"},
            status_code=400)
    body = await request.json()
    sections = {}
    for key in ("viewport", "inner", "frames"):
        if key in body:
            sections[key] = body[key]
    if not sections:
        return JSONResponse({"error": "没有要写的段"}, status_code=400)
    try:
        case_mod.set_draft(name, root=_ctx["cases_root"], **sections)
    except CaseError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _stores.pop(name, None)
    return JSONResponse(_case_info(name))


def _selection_store(name: str) -> tuple[PreviewStore | None, JSONResponse | None]:
    """选区类端点的唯一 flow 判定：视口/inner 草稿只属于 patch 与 patchimg。

    fullredraw 的几何都由 pad 决定，图片全幅流程由 extract 决定，两者都没有
    草稿可写——在这里一次拒掉，端点里不再各自判断，也不会把非法段写进
    case.toml 才在 ``set_draft`` 里炸成 500。
    """
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return None, JSONResponse({"error": "no such case"}, status_code=404)
    if case.flow not in ("patch", "patchimg"):
        return None, JSONResponse(
            {"error": (f"{case.flow} 没有选区草稿：视口与 inner 只用于 "
                       f"patch（视频局部）与 patchimg（图片局部）")},
            status_code=400)
    store = get_store(name)
    if store is None:
        return None, JSONResponse({"error": "no such case"}, status_code=404)
    return store, None


async def api_viewport(request):
    name = request.path_params["name"]
    store, bad = _selection_store(name)
    if bad is not None:
        return bad
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
    store, bad = _selection_store(name)
    if bad is not None:
        return bad
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


async def api_fov(request):
    """设置视口水平视场角；这是扩大重绘覆盖范围的入口。

    只改 fov，不缩放 inner：inner 的像素框保持不动，覆盖角度随 fov 变大。
    """
    name = request.path_params["name"]
    store, bad = _selection_store(name)
    if bad is not None:
        return bad
    if store.case.flow != "patch":
        return JSONResponse({"error": "只有 patch 有 fov 草稿"}, status_code=400)
    body = await request.json()
    raw = body.get("value")
    if raw is None:
        return JSONResponse({"error": "缺 value"}, status_code=400)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return JSONResponse({"error": "value 必须是数字"}, status_code=400)
    old = dict(store.case.viewport or {})
    try:
        applied = await asyncio.to_thread(store.set_fov, value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _remember_store(name, store)
    _log_geometry(request, name, "fov", old, dict(store.case.viewport or {}))
    return JSONResponse(_geometry_payload(store, name, fov=applied))


async def api_inner(request):
    name = request.path_params["name"]
    store, bad = _selection_store(name)
    if bad is not None:
        return bad
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
    store, bad = _selection_store(name)
    if bad is not None:
        return bad
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
    if case.flow not in ("patch", "patchimg"):
        return JSONResponse(
            {"error": f"{case.flow} 没有选区草稿，不存在可回退的选区"},
            status_code=400)
    body = await request.json()
    version = body.get("version") or case.extract_current
    if not version:
        return JSONResponse({"error": "还没有任何抽取版本可回退"}, status_code=400)
    d = case.dir(_ctx["cases_root"]) / "extracts" / version
    # 视频 patch 的选区冻结在 clip.json 的段里，patchimg 冻结在 image.json 里。
    marker = "clip.json" if case.flow == "patch" else "image.json"
    sj = d / marker
    if not sj.is_file():
        return JSONResponse({"error": f"抽取 {version} 缺 {marker}"}, status_code=400)
    if case.flow == "patch":
        seg = json.loads(sj.read_text(encoding="utf-8"))["segments"][0]
        viewport, inner = seg["viewport"], seg["inner"]
    else:
        try:
            geometry = sidecar_mod.load_image(sj)
        except (OSError, ValueError, TypeError) as exc:
            return JSONResponse({"error": f"{marker} 不合法：{exc}"}, status_code=400)
        viewport, inner = asdict(geometry.viewport), asdict(geometry.inner)
    case_mod.set_draft(name, root=_ctx["cases_root"], viewport=viewport, inner=inner)
    _stores.pop(name, None)
    store = get_store(name)
    if store is None:
        return JSONResponse({"error": "no such case"}, status_code=404)
    return JSONResponse(_geometry_payload(store, name))


async def api_region_export(request):
    """导出选区范围：读 case.toml 的已保存值，不读前端可能未落盘的状态。"""
    name = request.path_params["name"]
    store, bad = _selection_store(name)
    if bad is not None:
        return bad
    if store.case.flow != "patch":
        return JSONResponse(
            {"error": "只有视频 patch 支持选区范围导出；patchimg 没有 fov 与抽取范围"},
            status_code=400)
    try:
        payload = _region_payload(store)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(payload)


async def api_region_import(request):
    """导入选区范围：一次校验后写入 viewport/inner/frames，并返回轻量几何应答。"""
    name = request.path_params["name"]
    store, bad = _selection_store(name)
    if bad is not None:
        return bad
    if store.case.flow != "patch":
        return JSONResponse(
            {"error": "只有视频 patch 支持选区范围导入；patchimg 没有 fov 与抽取范围"},
            status_code=400)
    try:
        data = await request.json()
    except (ValueError, TypeError):
        return JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)
    try:
        values = _region_fields(data)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    old = {
        "viewport": dict(store.case.viewport or {}),
        "inner": dict(store.case.inner or {}),
        "frames": dict(store.case.frames or {}),
    }
    try:
        scaled, frames_clamped = await asyncio.to_thread(store.import_region, **values)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _remember_store(name, store)
    _log_geometry(request, name, "region_import", old, {
        "viewport": dict(store.case.viewport or {}),
        "inner": dict(store.case.inner or {}),
        "frames": dict(store.case.frames or {}),
    })
    return JSONResponse(_geometry_payload(
        store, name, frames=dict(store.case.frames or {}),
        scaled=scaled, frames_clamped=frames_clamped))


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
    if op == "export_upload":
        # 扩充件按平台限额缩放并落进 case 的 derived/（决策 87）。
        argv = vrstudio_argv("generate", "export-upload", "--case", name, *root)
        if body.get("scale"):
            argv += ["--scale", str(body["scale"])]
        return argv
    if op in ("submit", "dry_run"):
        argv = vrstudio_argv("generate", "submit", "--case", name, *root)
        if op == "dry_run":
            argv += ["--check"]
        # 再抽一版会重新计费，所以只挂在真提交上；预演不碰它。
        if op == "submit" and body.get("resubmit"):
            argv += ["--resubmit"]
        for item in body.get("vars") or []:
            argv += ["--var", item]
        if body.get("resolution"):
            argv += ["--resolution", str(body["resolution"])]
        if body.get("scale"):
            argv += ["--scale", str(body["scale"])]
        if body.get("audio"):
            argv += ["--audio", str(body["audio"])]
        if body.get("mode"):
            argv += ["--mode", str(body["mode"])]
        if body.get("duration") is not None:
            argv += ["--duration", str(body["duration"])]
        if body.get("aspect_ratio"):
            argv += ["--aspect-ratio", str(body["aspect_ratio"])]
        # 图片流程的三个参数：候选张数、图片模型、扣额度的项目会话。
        if body.get("num") is not None:
            argv += ["--num", str(body["num"])]
        if body.get("image_model_id") is not None:
            argv += ["--image-model-id", str(body["image_model_id"])]
        if body.get("session_id") is not None:
            argv += ["--session-id", str(body["session_id"])]
        return argv
    if op == "adopt":
        argv = vrstudio_argv("generate", "adopt", "--case", name, *root)
        if body.get("round") and body["round"] != "latest":
            argv += ["--round", body["round"]]
        # 图片流程必须显式挑候选，CLI 在缺 --index 时直接拒绝。
        if body.get("index") is not None:
            argv += ["--index", str(int(body["index"]))]
        return argv
    if op == "merge":
        argv = vrstudio_argv("merge", "--case", name, "-q", *root)
        if body.get("allow_crop"):
            argv += ["--allow-crop"]
        if body.get("output_mode") == "input" and case.flow == "fullRedrawImg":
            argv += ["--follow-input"]
        return argv
    if op == "recompose":
        manifests = sorted(cdir.glob("*_pad.json"))
        if not manifests:
            raise CaseError("还没有扩充两侧画面——先做第一步")
        result = cdir / "result.mp4"
        input_path = case.merge_input_file(_ctx["cases_root"]) or result
        if not input_path.is_file():
            raise CaseError(
                "还没有裁回输入：先在「生成」里采用一轮结果，"
                "或上传/选择一份 21:9 画布视频作为拼接输入")
        out = cdir / "out" / (case.adopt_round or "result")
        argv = vrstudio_argv("recompose", "--in", str(input_path),
                             "--manifest", str(manifests[0]),
                             "--out", str(out.with_suffix(".mp4")),
                             "--quiet")
        if body.get("output_mode") == "input":
            argv += ["--follow-input"]
        # 几何前提闸门拒绝出片时，用户仍可能想先看一眼画面；``--force`` 是 CLI 的
        # 诊断开关，是否给这个入口由页面按 error_kind 决定，web 不做第二套判定。
        if body.get("force"):
            argv += ["--force"]
        return argv
    if op == "relight":
        # 槽位解析、产物命名与 [relight] current 的回写都在 CLI 一处（唯一权威点），
        # web 只负责把面板参数翻成 flag。
        argv = vrstudio_argv("relight", "--case", name, *root)
        sample = body.get("sample")
        if sample:
            argv += ["--sample", str(int(sample))]
        if body.get("ref_frame") is not None:
            argv += ["--ref-frame", str(int(body["ref_frame"]))]
        if not sample and body.get("crf") is not None:
            argv += ["--crf", str(int(body["crf"]))]
        if body.get("scene"):
            argv += ["--scene", str(body["scene"])]
        for key, flag in (("strength", "--strength"),
                          ("local_shading", "--local-shading"),
                          ("clip_knee", "--clip-knee"),
                          ("clip_max", "--clip-max")):
            if body.get(key) is not None:
                argv += [flag, str(body[key])]
        if body.get("highlight_clip"):
            argv += ["--highlight-clip"]
        # 不加 --quiet：relight 的步骤/小样逐帧行是过程信息，任务卡片靠它们显示进度；
        # 整段渲染的速率行不走 Log，加不加 --quiet 都会写进日志。
        return argv
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


def _pad_followup(name: str) -> list[tuple[str, str, list[str]]]:
    """fullredraw 扩充成功后的上传件导出（决策 87）；其它 flow 不排。

    上传件只是送给外部工具的副本，失败不影响 pad 本身：任务日志里能看到原因，
    页面上的「生成上传件」按钮可以单独重试。
    """
    try:
        case = case_mod.load(name, _ctx["cases_root"])
    except CaseError:
        return []
    if case.flow != "fullredraw":
        return []
    if not sorted(case.dir(_ctx["cases_root"]).glob("*_pad.json")):
        return []
    argv = vrstudio_argv("generate", "export-upload", "--case", name,
                         "--cases-dir", str(_root()))
    return [("export_upload", name, argv)]


async def api_run(request):
    name = request.path_params["name"]
    body = await request.json()
    op = body.get("op", "")
    try:
        argv = await asyncio.to_thread(_op_argv, name, op, body)
    except CaseError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if op == "merge":
        follow = await asyncio.to_thread(_assemble_followup, name)
    elif op == "pad":
        follow = await asyncio.to_thread(_pad_followup, name)
    else:
        follow = None
    owner = current_user(request)
    request_id = getattr(request.state, "rid", None)
    billing = await asyncio.to_thread(_billing, name) if op in ("submit", "dry_run") else None
    if follow:
        QUEUE.submit(op, name, argv, follow_up=follow, owner=owner,
                     request_id=request_id, billing=billing)
    else:
        QUEUE.submit(op, name, argv, owner=owner, request_id=request_id,
                     billing=billing)
    return JSONResponse({"submitted": op, "case": name})


async def api_task(request):
    return JSONResponse(QUEUE.status())


async def api_tasks(request):
    scope = request.query_params.get("scope", "all")
    if scope not in ("all", "mine"):
        return JSONResponse({"error": "scope 必须是 all 或 mine"}, status_code=400)
    case = (request.query_params.get("case") or "").strip() or None
    return JSONResponse({"tasks": QUEUE.list(current_user(request), scope, case)})


async def api_task_detail(request):
    task = QUEUE.get(request.path_params["task_id"])
    if task is None:
        return JSONResponse({"error": "没有这个任务"}, status_code=404)
    return JSONResponse(task.view())


async def api_task_log(request):
    task = QUEUE.get(request.path_params["task_id"])
    if task is None:
        return JSONResponse({"error": "没有这个任务"}, status_code=404)
    if task.out_path is None or not task.out_path.is_file():
        return JSONResponse({"error": "这个任务还没有日志"}, status_code=404)
    raw = request.query_params.get("tail")
    text = task.out_path.read_text(encoding="utf-8", errors="replace")
    if raw is not None:
        try:
            count = max(1, int(raw))
        except ValueError:
            return JSONResponse({"error": "tail 必须是整数"}, status_code=400)
        lines = text.splitlines()
        text = "\n".join(lines[-count:])
    return PlainTextResponse(text)


async def api_me(request):
    user = current_user(request)
    if request.method == "GET":
        return JSONResponse({
            "id": user.id,
            "display_name": user.display_name,
            "authenticated": user.authenticated,
        })
    body = await request.json()
    name = str(body.get("display_name") or "").strip()
    if not name:
        return JSONResponse({"error": "昵称不能为空"}, status_code=400)
    if len(name) > MAX_NAME_LENGTH:
        return JSONResponse(
            {"error": f"昵称不能超过 {MAX_NAME_LENGTH} 个字符"}, status_code=400)
    response = JSONResponse({
        "id": user.id,
        "display_name": name,
        "authenticated": False,
    })
    # cookie 值只能是 ASCII：昵称按 UTF-8 百分号编码写入，读取时解码。
    response.set_cookie(NAME_COOKIE, quote(name, safe=""), max_age=365 * 24 * 3600,
                        httponly=True, samesite="lax")
    return response


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
    if case.flow in case_mod.IMAGE_FLOWS:
        return JSONResponse(
            {"error": "图片流程没有预览代理：图直接按原图打开"}, status_code=400)
    if case.flow == "relight":
        # 底版不是 [source]，源片代理的生成入口没有它；页面按原分辨率播即可。
        return JSONResponse(
            {"error": "relight 没有源片代理：对比页按原分辨率播放"}, status_code=400)
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
                             "url": f"/video/outproxy/{name}/{file}",
                             "width": await asyncio.to_thread(
                                 file_proxy_width, case, target,
                                 _ctx["cases_root"])})
    if request.method == "POST":
        state = await asyncio.to_thread(
            ensure_proxy, case, _ctx["cases_root"], _drop_store)
    else:
        state = await asyncio.to_thread(
            proxy_state, case, _ctx["cases_root"])
    return JSONResponse({"state": state, "url": f"/video/proxy/{name}",
                         "width": await asyncio.to_thread(
                             proxy_width, case, _ctx["cases_root"])})


async def api_sources(request):
    """sources/ 里的可选源文件：视频流程用视频，图片流程用 2:1 图片。"""
    files = sorted(p for p in sources_dir().iterdir()
                   if p.is_file() and p.suffix.lower() in (_VIDEO_EXTS | _IMAGE_EXTS))
    return JSONResponse({
        "dir": str(sources_dir()),
        "files": [p.name for p in files],
        "videos": [p.name for p in files if p.suffix.lower() in _VIDEO_EXTS],
        "images": [p.name for p in files if p.suffix.lower() in _IMAGE_EXTS],
    })


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
    Route("/case/{name}", legacy_case),
    Route("/img/{name}/{file}", img),
    Route("/file", file_open),
    Route("/img/asset/{name}/{file}", asset_img),
    Route("/img/round/{name}/{round}/{index}", round_img),
    Route("/video/source/{name}", video_source),
    Route("/video/proxy/{name}", video_proxy),
    Route("/video/outproxy/{name}/{file}", video_out_proxy),
    Route("/api/cases", api_cases, methods=["GET", "POST"]),
    Route("/api/case/{name}", api_case, methods=["GET", "DELETE"]),
    Route("/api/case/{name}/lock", api_case_lock, methods=["GET", "POST", "DELETE"]),
    Route("/api/case/{name}/owner", api_case_owner, methods=["POST"]),
    Route("/api/case/{name}/draft", api_draft, methods=["POST"]),
    Route("/api/case/{name}/relight", api_relight_draft, methods=["POST"]),
    Route("/api/case/{name}/relight/regions", api_relight_regions,
          methods=["GET", "POST"]),
    Route("/api/case/{name}/prompt", api_prompt, methods=["POST"]),
    Route("/api/case/{name}/params", api_params, methods=["POST"]),
    Route("/api/case/{name}/assets", api_assets, methods=["POST"]),
    Route("/api/uploads", api_uploads_create, methods=["POST"]),
    Route("/api/uploads/{upload_id}", api_upload, methods=["GET", "PUT", "DELETE"]),
    Route("/api/uploads/{upload_id}/complete", api_upload_complete, methods=["POST"]),
    Route("/api/case/{name}/derived", api_derived),
    Route("/api/case/{name}/submit", api_submit_set, methods=["GET", "PUT"]),
    Route("/api/case/{name}/extract-frames", api_extract_frames, methods=["POST"]),
    Route("/api/case/{name}/keyframe/run", api_keyframe_run, methods=["POST"]),
    Route("/api/case/{name}/keyframe/rounds", api_keyframe_rounds),
    Route("/api/case/{name}/keyframe/pick", api_keyframe_pick, methods=["POST"]),
    Route("/api/case/{name}/keyframe/use-raw", api_keyframe_use_raw, methods=["POST"]),
    Route("/api/case/{name}/keyframe/refs", api_keyframe_refs,
          methods=["GET", "PUT"]),
    Route("/api/case/{name}/keyframe/prompt", api_keyframe_prompt,
          methods=["GET", "PUT"]),
    Route("/api/case/{name}/keyframe/handoff", api_keyframe_handoff,
          methods=["POST"]),
    Route("/api/sessions", api_sessions),
    Route("/api/modes", api_modes),
    Route("/api/case/{name}/viewport", api_viewport, methods=["POST"]),
    Route("/api/case/{name}/viewport-size", api_viewport_size, methods=["POST"]),
    Route("/api/case/{name}/fov", api_fov, methods=["POST"]),
    Route("/api/case/{name}/inner", api_inner, methods=["POST"]),
    Route("/api/case/{name}/nudge", api_nudge, methods=["POST"]),
    Route("/api/case/{name}/reset-draft", api_reset_draft, methods=["POST"]),
    Route("/api/case/{name}/region/export", api_region_export),
    Route("/api/case/{name}/region/import", api_region_import, methods=["POST"]),
    Route("/api/case/{name}/run", api_run, methods=["POST"]),
    Route("/api/case/{name}/proxy", api_proxy, methods=["GET", "POST"]),
    Route("/api/task", api_task),
    Route("/api/tasks", api_tasks),
    Route("/api/tasks/{task_id}", api_task_detail),
    Route("/api/tasks/{task_id}/log", api_task_log),
    Route("/api/me", api_me, methods=["GET", "POST"]),
    Route("/api/client-log", api_client_log, methods=["POST"]),
    Route("/api/browse", api_browse),
    Route("/api/sources", api_sources),
]


class NoStoreMiddleware:
    """Cache-Control: no-store：本地工具绝不能端出旧 UI——浏览器启发式缓存
    曾让一个修复整个会话看起来是坏的。"""

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


#: 需要 revision 检查的写端点。几何高频接口（viewport/inner/nudge）不在这里，
#: 否则每次拖框都会让另一页的提示词保存撞 409。
_REVISION_WRITES = re.compile(
    r"^/api/case/(?P<name>[^/]+)/"
    r"(?:draft|prompt|params|assets|submit|reset-draft|owner|region/import|keyframe/(?:prompt|refs|pick|handoff))$"
)

#: 几何高频写端点：拖动帧不带 revision 头，照旧放行不推进 revision；
#: 落定帧（拖拽结束后的最后一次）带头时做可选校验，一致才落盘并推进。
#: 没有这层，几何草稿就是纯粹的最后写入者胜出，会静默覆盖别人的草稿。
_REVISION_GEOMETRY = re.compile(
    r"^/api/case/(?P<name>[^/]+)/(?:viewport|inner|nudge|viewport-size)$"
)


class CaseRevisionMiddleware:
    """可选 revision 的乐观并发检查。

    客户端带 ``X-Case-Revision``（或 JSON 里的 ``revision``）时，与
    ``case-meta.json`` 的当前值比较；不一致返回 409。写成功以后自动加一。
    老前端不传 revision，照旧写入，只是没有冲突保护。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"].upper()
        match = _REVISION_WRITES.match(scope["path"])
        geometry = _REVISION_GEOMETRY.match(scope["path"])
        if not match and not geometry:
            await self.app(scope, receive, send)
            return
        if method in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        name = (match or geometry).group("name")
        expected: int | None = None
        raw = request.headers.get("x-case-revision")
        if geometry and raw in (None, ""):
            # 几何拖动帧：不带头不打断。
            await self.app(scope, receive, send)
            return
        body = b""
        if raw not in (None, ""):
            try:
                expected = int(raw)
            except ValueError:
                await JSONResponse({"error": "X-Case-Revision 必须是整数"},
                                   status_code=400)(scope, receive, send)
                return
        else:
            body = await request.body()
            if body:
                try:
                    value = json.loads(body).get("revision")
                except (ValueError, AttributeError):
                    value = None
                if value is not None:
                    try:
                        expected = int(value)
                    except (TypeError, ValueError):
                        await JSONResponse({"error": "revision 必须是整数"},
                                           status_code=400)(scope, receive, send)
                        return
            sent = {"done": False}

            async def replay():
                if not sent["done"]:
                    sent["done"] = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            receive = replay

        current = _meta().read(name)
        if expected is not None and expected != current.revision:
            payload = {
                "code": "revision_conflict",
                "error": f"case 已被 {current.updated_by_name or '其他人'} 修改，请重新加载",
                "current_revision": current.revision,
                "updated_by": current.updated_by_name,
                "updated_at": current.updated_at,
            }
            await JSONResponse(payload, status_code=409)(scope, receive, send)
            return

        user = scope.get("state", {}).get("user")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                if 200 <= message["status"] < 300:
                    try:
                        updated = _meta().touch(name, user)
                        message.setdefault("headers", []).append(
                            (b"x-case-revision", str(updated.revision).encode("ascii")))
                    except OSError:
                        pass
            await send(message)

        await self.app(scope, receive, send_wrapper)


def build_app(cases_root: str | Path | None = None) -> Starlette:
    # 配置在装配应用时定格：错误在这里抛，不会变成每个请求的 500。
    config_mod.active()
    _ctx["cases_root"] = cases_root
    _ctx["meta"] = case_meta.FileCaseMetaStore(cases_root)
    _ctx["locks"] = FileCaseLockStore(cases_root)
    try:
        _cleanup_uploads()
    except OSError:
        pass
    app = Starlette(routes=routes)
    app.add_middleware(NoStoreMiddleware)
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(CaseRevisionMiddleware)
    app.add_middleware(IdentityMiddleware)
    app.mount("/next", NextFrontendFiles(directory=str(_NEXT_DIR), html=True,
                                         check_dir=False), name="next")
    return app
