"""pick 预览代理：源片的 4K H.264 副本，按需生成。

顶部播放器与服务端预览都从代理解码；源片仍只用于抽取、合并和几何权威。
代理是缓存，不是交付物：

- ``cases/<case>/derived/pick/proxy.mp4``
- ``cases/<case>/derived/pick/proxy.json`` 记录源文件 sha256、目标尺寸、
  帧率、帧数和生成参数。

第一次访问 pick 页时，前端发现代理不可用就 ``POST /api/case/<case>/proxy``；
本模块在后台线程跑一次 ffmpeg，完成后回调 app 清掉该 case 的 PreviewStore，
让下一次预览请求切到代理。源片本身已经是浏览器可直接播放的 H.264 8-bit
yuv420p MP4 时写一个 ``skipped`` 侧车，前端继续用源片，不做无意义转码。

单进程假设：当前 serve 只有一个 uvicorn worker；多 worker 时同一个 case 可能
被两个进程同时构建，需要换成文件锁或队列。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..case import Case
from ..config import REPO_ROOT
from ..media.ffmpeg import ffmpeg_path
from ..media.videoio import VideoIOError, probe
from ..procenv import utf8_env
from ..progress import logs_dir
from .logging import PROXY_LOGGER

PROXY_VERSION = 3
PROXY_MAX_W = 4096
PROXY_CRF = 22
PROXY_PRESET = "veryfast"

log = logging.getLogger(PROXY_LOGGER)


@dataclass
class Build:
    state: str                 # building | ready | failed | disabled
    error: str | None
    log_path: Path
    thread: threading.Thread | None = None


_lock = threading.Lock()
_builds: dict[str, Build] = {}


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


def proxy_ready(case: Case, root=None) -> bool:
    meta = _read_sidecar(case, root)
    if not meta or meta.get("version") != PROXY_VERSION:
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
                and meta.get("skipped") and _sha_matches(case, meta))


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


def _browser_safe(info, source: str) -> bool:
    """源片本身能被 Chrome/Opera 直接解码时不需要代理。"""
    return (info.codec == "h264" and info.pix_fmt == "yuv420p"
            and info.width <= PROXY_MAX_W
            and Path(source).suffix.lower() == ".mp4")


def _target_size(info) -> tuple[int, int]:
    if info.width <= 0 or info.height <= 0:
        raise ValueError("source has no usable width/height")
    width = min(PROXY_MAX_W, info.width)
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

    if _browser_safe(info, case.source_path):
        _write_sidecar(case, root, {
            "version": PROXY_VERSION, "source_sha256": case.sha256,
            "skipped": True, "reason": "source is h264 8-bit yuv420p mp4 <= 4096",
            "width": info.width, "height": info.height,
            "fps": info.fps, "frames": info.frames,
        })
        with _lock:
            _builds[case.name] = Build("disabled", None, Path(""))
        return "disabled"

    try:
        target = _target_size(info)
    except ValueError as exc:
        log.error("proxy.target_failed", extra={"data": {
            "event": "proxy.target_failed", "case": case.name, "error": str(exc),
        }})
        return "failed"

    with _lock:
        build = _builds.get(case.name)
        if build is not None and build.state == "building":
            return "building"
        log_path = logs_dir() / (
            f"proxy_{case.name}_{time.strftime('%Y%m%d-%H%M%S')}.log")
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
        ff = ffmpeg_path()
        if not ff:
            raise RuntimeError("ffmpeg binary not found")
        proxy_dir(case, root).mkdir(parents=True, exist_ok=True)
        tmp.unlink(missing_ok=True)
        cmd = [
            ff, "-y", "-hide_banner", "-loglevel", "warning",
            "-i", str(case.source_path), "-map", "0:v:0",
            "-vf", f"scale={target[0]}:{target[1]}:flags=bicubic",
            "-c:v", "libx264", "-preset", PROXY_PRESET, "-crf", str(PROXY_CRF),
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
            "-fps_mode", "passthrough", str(tmp),
        ]
        build.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(build.log_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(f"source: {case.source_path}\n")
            fh.write(f"target: {target[0]}x{target[1]}\n")
            fh.write(" ".join(cmd) + "\n")
            fh.flush()
            result = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                    cwd=str(REPO_ROOT), env=utf8_env())
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg exited with {result.returncode}; see {build.log_path}")

        made = probe(tmp)
        if info.frames and made.frames and info.frames != made.frames:
            raise RuntimeError(
                f"proxy frame count {made.frames} != source {info.frames}")
        if (info.fps_float and made.fps_float
                and abs(info.fps_float - made.fps_float) > 0.01):
            raise RuntimeError(
                f"proxy fps {made.fps_float} != source {info.fps_float}")
        os.replace(tmp, final)
        _write_sidecar(case, root, {
            "version": PROXY_VERSION, "source_sha256": case.sha256,
            "skipped": False, "width": made.width, "height": made.height,
            "fps": made.fps, "frames": made.frames,
            "duration_s": made.duration_s, "crf": PROXY_CRF,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
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
