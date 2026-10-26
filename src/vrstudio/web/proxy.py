"""4K 预览代理：源片与成片的可播副本，按需生成。

pick 页的顶部播放器、服务端预览，以及对比页的左右两路都从代理解码；源片与 8K
成片仍只用于抽取、合并和交付。代理是缓存，不是交付物：

- 源片代理（pick）：``cases/<case>/derived/pick/proxy.mp4`` + ``proxy.json``，
  侧车记录源片 sha256，源片变了就重建。
- 任意文件代理（对比页）：``cases/<case>/derived/preview/<stem>_proxy.mp4`` +
  ``.json``，侧车记录源路径、字节数与 mtime；文件被合并覆盖后自动重建。

第一次访问时前端发现代理不可用就 ``POST /api/case/<case>/proxy``（可选
``file`` 指定成片）；本模块在后台线程跑一次 ffmpeg，完成后回调 app 刷新状态。
文件本身已经是浏览器可直接播放的 H.264 8-bit yuv420p MP4 且不超过 ``web.proxy_max_w``
（默认 4096，可配）时写一个 ``skipped`` 侧车，前端继续用原文件，不做无意义转码。
代理的宽度上限来自 ``web.proxy_max_w``：选区全景与对比页两路共用，改配置后
侧车记录的 ``max_w`` 不再匹配，旧代理按新宽度自动重建。

单进程假设：当前 serve 只有一个 uvicorn worker；多 worker 时同一个文件可能
被两个进程同时构建，需要换成文件锁或队列。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .. import config as config_mod
from ..case import Case
from ..config import REPO_ROOT
from ..media.ffmpeg import ffmpeg_path
from ..media.videoio import VideoIOError, probe
from ..procenv import utf8_env
from ..progress import logs_dir
from .logging import PROXY_LOGGER

PROXY_VERSION = 3
PROXY_CRF = 22
PROXY_PRESET = "veryfast"

log = logging.getLogger(PROXY_LOGGER)


def proxy_max_w() -> int:
    """预览代理的宽度上限（``web.proxy_max_w``）。

    每次调用都读配置：这个值是机器级取舍（远程桌面软解环境要调低），
    改 vrstudio.toml 后无需重启 serve；侧车记录构建时的 max_w，配置变了
    旧代理就不再是 ready，按需按新宽度重建。选区全景与对比页两路共用。
    """
    return int(config_mod.load().get("web.proxy_max_w", 4096))


@dataclass
class Build:
    state: str                 # building | ready | failed | disabled
    error: str | None
    log_path: Path
    thread: threading.Thread | None = None


@dataclass(frozen=True)
class FileProxy:
    """某个视频文件的 4K 代理及其侧车。"""

    source: Path
    video: Path
    sidecar: Path
    key: str


_lock = threading.Lock()
_builds: dict[str, Build] = {}


# ── 源片代理（pick 页；路径是既有契约，测试与 PreviewStore 都认它）────────

def proxy_dir(case: Case, root: str | Path | None = None) -> Path:
    return case.dir(root) / "derived" / "pick"


def proxy_path(case: Case, root: str | Path | None = None) -> Path:
    return proxy_dir(case, root) / "proxy.mp4"


def sidecar_path(case: Case, root: str | Path | None = None) -> Path:
    return proxy_dir(case, root) / "proxy.json"


def _read_sidecar(case: Case, root=None) -> dict | None:
    try:
        return json.loads(sidecar_path(case, root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha_matches(case: Case, meta: dict) -> bool:
    return not case.sha256 or meta.get("source_sha256") == case.sha256


def _sidecar_current(meta: dict) -> bool:
    """侧车是不是按当前配置建的：max_w 变了（或旧侧车没这个字段）就重建。"""
    return meta.get("max_w") == proxy_max_w()


def proxy_ready(case: Case, root=None) -> bool:
    meta = _read_sidecar(case, root)
    if not meta or meta.get("version") != PROXY_VERSION or not _sidecar_current(meta):
        return False
    if meta.get("skipped") or not _sha_matches(case, meta):
        return False
    path = proxy_path(case, root)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def proxy_disabled(case: Case, root=None) -> bool:
    meta = _read_sidecar(case, root)
    return bool(meta and meta.get("version") == PROXY_VERSION
                and _sidecar_current(meta)
                and meta.get("skipped") and _sha_matches(case, meta))


def proxy_width(case: Case, root=None) -> int | None:
    """就绪/直连时代理（或直连的源）宽度；页面据此显示“2048 代理”这类标签。"""
    meta = _read_sidecar(case, root)
    if not meta or not _sha_matches(case, meta) or not _sidecar_current(meta):
        return None
    width = meta.get("width")
    return int(width) if isinstance(width, int) else None


def proxy_identity(case: Case, root=None) -> str | None:
    """代理内容的稳定标识：重建（created_at 变化）时预览缓存必须换 key。"""
    meta = _read_sidecar(case, root)
    if not meta or meta.get("skipped") or not _sha_matches(case, meta):
        return None
    return str(meta.get("created_at") or meta.get("source_sha256") or "proxy")


def proxy_state(case: Case, root=None) -> str:
    if proxy_ready(case, root):
        return "ready"
    if proxy_disabled(case, root):
        return "disabled"
    with _lock:
        build = _builds.get(case.name)
        if build is not None:
            return build.state
    return "missing"


def _browser_safe(info, source: str, max_w: int) -> bool:
    """文件本身能被 Chrome/Opera 直接解码、且不超过当前代理上限时不需要代理。"""
    return (info.codec == "h264" and info.pix_fmt == "yuv420p"
            and info.width <= max_w
            and Path(source).suffix.lower() == ".mp4")


def _target_size(info, max_w: int) -> tuple[int, int]:
    if info.width <= 0 or info.height <= 0:
        raise ValueError("source has no usable width/height")
    width = min(max_w, info.width)
    height = round(width * info.height / info.width)
    width -= width % 2
    height -= height % 2
    return max(2, width), max(2, height)


def _write_sidecar(case: Case, root, meta: dict) -> None:
    directory = proxy_dir(case, root)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / "proxy.tmp.json"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, sidecar_path(case, root))


# ── 共用编码核心 ─────────────────────────────────────────────────────────

def _encode_proxy(source: Path, info, target: tuple[int, int], tmp: Path,
                  log_path: Path) -> None:
    """把 source 缩放到 target 写成 tmp；帧数/fps 必须与 source 一致。"""
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg binary not found")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.unlink(missing_ok=True)
    cmd = [
        ff, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(source), "-map", "0:v:0",
        "-vf", f"scale={target[0]}:{target[1]}:flags=bicubic",
        "-c:v", "libx264", "-preset", PROXY_PRESET, "-crf", str(PROXY_CRF),
        "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
        "-fps_mode", "passthrough", str(tmp),
    ]
    with open(log_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"source: {source}\n")
        fh.write(f"target: {target[0]}x{target[1]}\n")
        fh.write(" ".join(cmd) + "\n")
        fh.flush()
        result = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                cwd=str(REPO_ROOT), env=utf8_env())
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg exited with {result.returncode}; see {log_path}")
    made = probe(tmp)
    if info.frames and made.frames and info.frames != made.frames:
        raise RuntimeError(f"proxy frame count {made.frames} != source {info.frames}")
    if (info.fps_float and made.fps_float
            and abs(info.fps_float - made.fps_float) > 0.01):
        raise RuntimeError(f"proxy fps {made.fps_float} != source {info.fps_float}")


def _finish_sidecar(meta: dict, made) -> dict:
    meta.update({
        "version": PROXY_VERSION, "skipped": False, "max_w": proxy_max_w(),
        "width": made.width, "height": made.height,
        "fps": made.fps, "frames": made.frames,
        "duration_s": made.duration_s, "crf": PROXY_CRF,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    return meta


# ── 任意视频文件的代理（对比页左右两路）────────────────────────────────

def _safe_stem(stem: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return (clean or "media")[:80]


def file_proxy(case: Case, source: str | Path,
               root: str | Path | None = None) -> FileProxy:
    source = Path(source)
    base = case.dir(root) / "derived" / "preview" / f"{_safe_stem(source.stem)}_proxy"
    return FileProxy(source=source, video=base.with_suffix(".mp4"),
                     sidecar=base.with_suffix(".json"), key=base.name)


def _file_identity(source: Path) -> tuple[int, int] | None:
    try:
        st = source.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def file_proxy_state(case: Case, source: str | Path, root=None) -> str:
    """ready / disabled / missing；构建中的状态取自内存。"""
    fp = file_proxy(case, source, root)
    identity = _file_identity(fp.source)
    if identity is None:
        return "missing"
    try:
        meta = json.loads(fp.sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = None
    if (not meta or meta.get("version") != PROXY_VERSION
            or not _sidecar_current(meta)
            or meta.get("source") != str(fp.source.resolve())
            or (meta.get("size"), meta.get("mtime_ns")) != identity):
        with _lock:
            build = _builds.get(_build_key(case, fp))
            if build is not None and build.state in ("building", "failed"):
                return build.state
        return "missing"
    if meta.get("skipped"):
        return "disabled"
    try:
        return "ready" if fp.video.is_file() and fp.video.stat().st_size > 0 else "missing"
    except OSError:
        return "missing"


def file_proxy_width(case: Case, source: str | Path, root=None) -> int | None:
    """成片代理（或直连的成片）宽度；对比页标签用。"""
    fp = file_proxy(case, source, root)
    try:
        meta = json.loads(fp.sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not meta or not _sidecar_current(meta):
        return None
    width = meta.get("width")
    return int(width) if isinstance(width, int) else None


def _build_key(case: Case, fp: FileProxy) -> str:
    return f"{case.name}:{fp.key}"


def ensure_file_proxy(case: Case, source: str | Path, root=None,
                      on_done=None) -> str:
    """返回 ready/disabled/building/failed；缺代理时启动一次后台生成。"""
    state = file_proxy_state(case, source, root)
    if state in ("ready", "disabled"):
        return state
    fp = file_proxy(case, source, root)
    identity = _file_identity(fp.source)
    if identity is None:
        return "failed"
    try:
        info = probe(fp.source)
    except (VideoIOError, OSError) as exc:
        log.error("proxy.probe_failed", extra={"data": {
            "event": "proxy.probe_failed", "case": case.name,
            "source": str(fp.source), "error": str(exc),
        }})
        return "failed"

    if _browser_safe(info, str(fp.source), proxy_max_w()):
        fp.sidecar.parent.mkdir(parents=True, exist_ok=True)
        fp.sidecar.write_text(json.dumps({
            "version": PROXY_VERSION, "max_w": proxy_max_w(),
            "source": str(fp.source.resolve()),
            "size": identity[0], "mtime_ns": identity[1],
            "skipped": True,
            "reason": f"source is h264 8-bit yuv420p mp4 <= {proxy_max_w()}",
            "width": info.width, "height": info.height,
            "fps": info.fps, "frames": info.frames,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        with _lock:
            _builds[_build_key(case, fp)] = Build("disabled", None, Path(""))
        return "disabled"

    try:
        target = _target_size(info, proxy_max_w())
    except ValueError as exc:
        log.error("proxy.target_failed", extra={"data": {
            "event": "proxy.target_failed", "case": case.name,
            "source": str(fp.source), "error": str(exc),
        }})
        return "failed"

    key = _build_key(case, fp)
    with _lock:
        build = _builds.get(key)
        if build is not None and build.state == "building":
            return "building"
        log_path = _proxy_log_path(case, fp.source.stem)
        build = Build("building", None, log_path)
        _builds[key] = build
        thread = threading.Thread(
            target=_build_file,
            args=(case, root, fp, identity, build, info, target, on_done),
            name=f"proxy-{key}", daemon=True)
        build.thread = thread
        thread.start()
    return "building"


def _proxy_log_path(case: Case, stem: str) -> Path:
    """代理构建的原始输出：统一进 logs/tasks/，不再散落 logs/proxy_*。"""
    day = time.strftime("%Y%m%d")
    directory = logs_dir() / "tasks" / day
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"proxy_{case.name}_{stem}_{time.strftime('%Y%m%d-%H%M%S')}.log"


def _build_file(case: Case, root, fp: FileProxy, identity, build: Build,
                info, target, on_done) -> None:
    started = time.monotonic()
    tmp = fp.video.with_name(fp.video.name + ".tmp.mp4")
    log.info("proxy.start", extra={"data": {
        "event": "proxy.start", "case": case.name,
        "source": str(fp.source), "target": f"{target[0]}x{target[1]}",
        "log": str(build.log_path),
    }})
    try:
        _encode_proxy(fp.source, info, target, tmp, build.log_path)
        os.replace(tmp, fp.video)
        fp.sidecar.parent.mkdir(parents=True, exist_ok=True)
        meta = _finish_sidecar({
            "source": str(fp.source.resolve()),
            "size": identity[0], "mtime_ns": identity[1],
        }, probe(fp.video))
        fp.sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        build.state = "ready"
        if on_done is not None:
            try:
                on_done(case.name)
            except Exception:             # 缓存失效失败不能毁掉已生成的文件
                log.exception("proxy.on_done_failed", extra={"data": {
                    "event": "proxy.on_done_failed", "case": case.name,
                }})
        log.info("proxy.finish", extra={"data": {
            "event": "proxy.finish", "case": case.name,
            "source": str(fp.source),
            "ms": round((time.monotonic() - started) * 1000, 1),
            "bytes": fp.video.stat().st_size,
            "log": str(build.log_path),
        }})
    except Exception as exc:
        build.state = "failed"
        build.error = str(exc)
        tmp.unlink(missing_ok=True)
        log.exception("proxy.error", extra={"data": {
            "event": "proxy.error", "case": case.name,
            "source": str(fp.source), "error": str(exc),
            "log": str(build.log_path),
        }})


# ── 源片代理生成 ─────────────────────────────────────────────────────────

def ensure_proxy(case: Case, root=None, on_done=None) -> str:
    """返回 ready/disabled/building/failed；缺代理时启动一次后台生成。"""
    if not case.source_path:
        return "failed"
    if proxy_ready(case, root):
        return "ready"
    if proxy_disabled(case, root):
        return "disabled"
    try:
        info = probe(case.source_path)
    except (VideoIOError, OSError) as exc:
        log.error("proxy.probe_failed", extra={"data": {
            "event": "proxy.probe_failed", "case": case.name, "error": str(exc),
        }})
        return "failed"

    if _browser_safe(info, case.source_path, proxy_max_w()):
        _write_sidecar(case, root, {
            "version": PROXY_VERSION, "max_w": proxy_max_w(),
            "source_sha256": case.sha256,
            "skipped": True,
            "reason": f"source is h264 8-bit yuv420p mp4 <= {proxy_max_w()}",
            "width": info.width, "height": info.height,
            "fps": info.fps, "frames": info.frames,
        })
        with _lock:
            _builds[case.name] = Build("disabled", None, Path(""))
        return "disabled"

    try:
        target = _target_size(info, proxy_max_w())
    except ValueError as exc:
        log.error("proxy.target_failed", extra={"data": {
            "event": "proxy.target_failed", "case": case.name, "error": str(exc),
        }})
        return "failed"

    with _lock:
        build = _builds.get(case.name)
        if build is not None and build.state == "building":
            return "building"
        log_path = _proxy_log_path(case, "source")
        build = Build("building", None, log_path)
        _builds[case.name] = build
        thread = threading.Thread(
            target=_build, args=(case, root, build, info, target, on_done),
            name=f"proxy-{case.name}", daemon=True)
        build.thread = thread
        thread.start()
    return "building"


def _build(case: Case, root, build: Build, info, target, on_done) -> None:
    started = time.monotonic()
    final = proxy_path(case, root)
    tmp = proxy_dir(case, root) / "proxy.tmp.mp4"
    log.info("proxy.start", extra={"data": {
        "event": "proxy.start", "case": case.name,
        "source": case.source_path, "target": f"{target[0]}x{target[1]}",
        "log": str(build.log_path),
    }})
    try:
        _encode_proxy(Path(case.source_path), info, target, tmp, build.log_path)
        os.replace(tmp, final)
        made = probe(final)
        _write_sidecar(case, root, _finish_sidecar({
            "source_sha256": case.sha256,
        }, made))
        build.state = "ready"
        if on_done is not None:
            try:
                on_done(case.name)
            except Exception:                 # 缓存失效失败不能毁掉已生成的文件
                log.exception("proxy.on_done_failed", extra={"data": {
                    "event": "proxy.on_done_failed", "case": case.name,
                }})
        log.info("proxy.finish", extra={"data": {
            "event": "proxy.finish", "case": case.name,
            "ms": round((time.monotonic() - started) * 1000, 1),
            "bytes": final.stat().st_size, "width": made.width,
            "height": made.height, "frames": made.frames, "fps": made.fps,
            "log": str(build.log_path),
        }})
    except Exception as exc:
        build.state = "failed"
        build.error = str(exc)
        tmp.unlink(missing_ok=True)
        log.exception("proxy.error", extra={"data": {
            "event": "proxy.error", "case": case.name, "error": str(exc),
            "ms": round((time.monotonic() - started) * 1000, 1),
            "log": str(build.log_path),
        }})
