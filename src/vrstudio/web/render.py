"""pick 页的视口预览服务端渲染（← vrpatch web/render.py 迁入，case 模型换统一版）。

ERP 面不需要服务端渲染：向导页用原生 ``<video>`` 逐帧走查源片（浏览器解码，
零文件）。只有视口预览是一次重投影，而投影数学的唯一权威在
``vrstudio.patch.projection``（AGENTS.md 硬规则 3），所以它留在服务端。
解码的 4K ERP 帧放一个小的内存 LRU（与几何无关，视口移动后仍命中）；
重投影 JPEG 缓存放在 case 目录 ``derived/pick/`` 下，是可随时删除的再生成缓存。

与 vrpatch 版的差异：高频几何只写 ``derived/pick/draft.toml``，不再每次点击
重写 ``case.toml``；显式保存的 ``[[range_preset]]`` 由 ``vrstudio.case`` 管理。
"""

from __future__ import annotations

import hashlib
import logging
import math
import shutil
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .. import case as case_mod
from ..media.imageio import read_image
from ..patch.projection import erp_to_rect
from . import pick_draft
from .logging import PREVIEW_LOGGER
from .proxy import proxy_identity, proxy_path, proxy_ready

log = logging.getLogger(PREVIEW_LOGGER)

VIEWPORT_PREVIEW_W = 960  # 视口预览宽度上限
MEDIUM_W = 4096        # 源帧的工作副本宽度上限
FRAME_JPEG_Q = 85      # 逐帧视口缓存质量
ERP_MEDIUM_LRU = 8     # 内存里保留的解码 ERP 帧数（8 * ~25MB ≈ 200MB）
FOV_MIN_DEG = 20.0     # 选择页 fov 滑杆下限
FOV_MAX_DEG = 120.0    # 单透视视口的可用上限（>110° 边缘拉伸迅速变大）


def _read_frame(video: str, index: int) -> np.ndarray:
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {video}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"cannot read frame {index} from {video}")
    return frame


def _scale_inner(inner: dict, from_w: int, from_h: int,
                 to_w: int, to_h: int) -> dict:
    """把 inner 从一套视口像素缩放到另一套，四舍五入并钳进目标视口。

    切换视口尺寸与跨 case 导入选区共用这一份规则：inner 是视口像素坐标，
    换到不同尺寸时只保留相对位置与相对大小。
    """
    x = round(int(inner.get("x", 0)) * to_w / from_w)
    y = round(int(inner.get("y", 0)) * to_h / from_h)
    w = round(int(inner.get("width", 0)) * to_w / from_w)
    h = round(int(inner.get("height", 0)) * to_h / from_h)
    w = max(1, min(int(w), to_w))
    h = max(1, min(int(h), to_h))
    x = max(0, min(int(x), to_w - w))
    y = max(0, min(int(y), to_h - h))
    return {"x": x, "y": y, "width": w, "height": h}


class PreviewStore:
    """一个 case 一个实例；case.toml 变更（mtime 变化）时由 app 层重建。"""

    def __init__(self, name: str, cases_root: str | Path | None = None):
        self.name = name
        self.root = cases_root
        self.case = case_mod.load(name, cases_root)   # 失败抛 CaseError（404/400 由上层映射）
        self.dir = self.case.dir(cases_root) / "derived" / "pick"
        self.dir.mkdir(parents=True, exist_ok=True)
        # pick 页当前工作草稿；没有草稿时回退 active preset。
        self.draft = pick_draft.load_draft(self.case, cases_root)
        if self.draft is None:
            self.draft = pick_draft.draft_from_preset(self.case)
        if self.draft is not None:
            self.case.viewport = dict(self.draft.viewport) or None
            self.case.inner = dict(self.draft.inner) or None
            self.case.frames = (dict(self.draft.frames)
                                if self.draft.frames is not None else None)
        self._erp_lru: dict[int, np.ndarray] = {}
        self.is_image = self.case.flow in case_mod.IMAGE_FLOWS
        if self.is_image:
            # 图片流程的源是单张 2:1 ERP：只解一次，解码结果直接当第 0 帧的
            # 工作副本，之后所有视口预览都从这份 medium 重投影。
            self.proxy_file = Path(self.case.source_path)
            self.source_kind = "image"
            self.source_key = "image"
            frame = read_image(Path(self.case.source_path))
            self.source_size = (int(frame.shape[1]), int(frame.shape[0]))
            self.frame_count = 1
            self.fps = 0.0
            self.duration_s = 0.0
            self._erp_lru[0] = self._medium_of(frame)
            del frame
        else:
            self.proxy_file = proxy_path(self.case, cases_root)
            if proxy_ready(self.case, cases_root):
                self.source_kind = "proxy"
                identity = proxy_identity(self.case, cases_root) or "ready"
                self.source_key = f"proxy:{identity}"
            else:
                self.source_kind = "source"
                self.source_key = "source"
            # 元数据以源片为准：代理与源片帧数/帧率必须一致，滑块的帧号才不漂。
            cap = cv2.VideoCapture(str(Path(self.case.source_path)))
            self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            self.source_size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            cap.release()
            self.duration_s = (self.frame_count / self.fps
                               if self.fps and self.frame_count else 0.0)
        self.key = self._new_key()
        self._vp_bytes: tuple | None = None

    # ── 几何读写（统一 case 模型）────────────────────────────────────────

    def _vp(self) -> dict:
        return dict(self.case.viewport or {})

    def _inner(self) -> dict:
        return dict(self.case.inner or {})

    def save_geometry(self) -> None:
        """把当前几何写进 derived/pick/draft.toml；不碰 case.toml。"""
        self.draft = pick_draft.save_draft(
            self.case, self.root, viewport=self._vp(), inner=self._inner(),
            frames=dict(self.case.frames) if self.case.frames else None)

    def set_viewport_from_erp_click(self, fx: float, fy: float) -> None:
        """fx, fy 是 ERP 面上的 0..1 分数 → 视口中心的经纬度。"""
        vp = self._vp()
        vp["yaw_deg"] = round((fx - 0.5) * 360.0, 1)
        vp["pitch_deg"] = round(max(-89.0, min(89.0, (0.5 - fy) * 180.0)), 1)
        self.case.viewport = vp
        self.save_geometry()
        self.key = self._new_key()

    def set_viewport_size(self, width: int, height: int) -> None:
        """切换视口分辨率：保持 yaw/pitch/fov，按比例缩放 inner 选区。

        同一宽高比下，选区在画面里的相对位置不变；改成另一种宽高比时，
        inner 仍按宽高各自的比例缩放，取景内容由新的视口几何决定。
        """
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            raise ValueError("viewport width/height must be positive")
        vp = self._vp()
        old_w = int(vp.get("width") or 0)
        old_h = int(vp.get("height") or 0)
        inner = dict(self.case.inner or {})
        if old_w > 0 and old_h > 0 and inner:
            self.case.inner = _scale_inner(inner, old_w, old_h, width, height)
        vp["width"] = width
        vp["height"] = height
        self.case.viewport = vp
        self.save_geometry()
        self.key = self._new_key()

    def set_fov(self, value: float) -> float:
        """改变水平视场角；inner 的像素框不动，覆盖角度随 fov 变大。

        这是“扩大重绘区域”的唯一入口：换视口尺寸只改采样密度，不扩大角度。
        返回值是写盘后的实际角度（保留一位小数）。
        """
        try:
            fov = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("fov 必须是数字") from exc
        if not FOV_MIN_DEG <= fov <= FOV_MAX_DEG:
            raise ValueError(
                f"fov 必须在 {FOV_MIN_DEG:g}–{FOV_MAX_DEG:g}° 之间，收到 {fov:g}")
        vp = self._vp()
        vp["fov_h_deg"] = round(fov, 1)
        self.case.viewport = vp
        self.save_geometry()
        self.key = self._new_key()
        return float(vp["fov_h_deg"])

    def set_inner(self, x: int, y: int, w: int, h: int) -> None:
        self.case.inner = {"x": int(x), "y": int(y), "width": int(w), "height": int(h)}
        self.save_geometry()
        # inner 由页面的 #innerBox 单独绘制，图片内容与它无关：不刷新 preview
        # 标识，拖框就不会触发一次 8K 解码 + 重投影。

    def import_region(self, *, yaw_deg: float, pitch_deg: float, fov_h_deg: float,
                      viewport_size: tuple[int, int], inner: dict,
                      frames: dict, source_fps: float,
                      source_frame_count: int) -> tuple[bool, bool]:
        """把另一个 case 的选区写进本 case；目标视口尺寸不变。

        ``viewport_size`` 是来源视口的像素尺寸，只参与 inner 换算。
        ``frames`` 是来源 fps 下的绝对帧号，先换成秒再按目标 fps 还原：同一段
        时间窗口在不同 fps、不同总帧数的 case 之间仍然对得上。换算后结束帧超出
        目标末尾就截到最后一帧（返回 frames_clamped），起点都已超过目标时长才
        拒绝。所有校验都在写盘之前完成，任一字段不合法就抛 ValueError。

        返回 ``(inner 是否被缩放, 帧范围是否被目标时长截断)``。
        """
        vp = self._vp()
        try:
            target_w = int(vp["width"])
            target_h = int(vp["height"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("当前 case 还没有视口，无法导入选区") from None
        if not FOV_MIN_DEG <= fov_h_deg <= FOV_MAX_DEG:
            raise ValueError(
                f"fov 必须在 {FOV_MIN_DEG:g}–{FOV_MAX_DEG:g}° 之间，收到 {fov_h_deg:g}")
        if not -89.0 <= pitch_deg <= 89.0:
            raise ValueError(f"pitch 必须在 ±89° 之间，收到 {pitch_deg:g}")
        if not -180.0 <= yaw_deg <= 180.0:
            raise ValueError(f"yaw 必须在 ±180° 之间，收到 {yaw_deg:g}")
        source_w, source_h = viewport_size
        if source_w <= 0 or source_h <= 0:
            raise ValueError(f"来源视口尺寸不合法：{source_w}×{source_h}")
        x, y, w, h = (int(inner.get(k, 0)) for k in ("x", "y", "width", "height"))
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            raise ValueError(f"inner 不合法：x={x} y={y} {w}×{h}")
        if x + w > source_w or y + h > source_h:
            raise ValueError(
                f"inner 超出视口：x+w={x + w}（上限 {source_w}），"
                f"y+h={y + h}（上限 {source_h}）")
        if source_fps <= 0 or source_frame_count <= 0:
            raise ValueError(
                f"来源帧率/帧数不合法：{source_fps:g} fps，{source_frame_count} 帧")
        start = int(frames.get("start", -1))
        end = int(frames.get("end", -1))
        if not 0 <= start < end <= source_frame_count - 1:
            raise ValueError(
                f"帧范围 {start}–{end} 超出来源 case 的 0–{source_frame_count - 1}，"
                "且开始必须小于结束")
        if self.frame_count <= 0 or self.fps <= 0:
            raise ValueError("读不到当前 case 的帧率或帧数，无法换算帧范围")
        target_count = self.frame_count
        if math.isclose(source_fps, self.fps, rel_tol=1e-3, abs_tol=1e-6):
            new_start, new_end = start, end
        else:
            # 帧号是半开区间 [start, end+1) 的起点；换成秒再乘目标 fps，
            # 同一段 10 秒素材在 30 fps 与 24 fps 下得到各自的总帧数。
            new_start = round(start / source_fps * self.fps)
            new_end = round((end + 1) / source_fps * self.fps) - 1
        if new_start >= target_count:
            raise ValueError(
                f"帧范围换算后起点 {new_start} 超过目标 case 的最后一帧 "
                f"{target_count - 1}（目标 {target_count / self.fps:.2f} 秒，"
                f"来源区间 {start / source_fps:.2f}–{(end + 1) / source_fps:.2f} 秒）；"
                "请先在目标 case 上调整抽取范围")
        frames_clamped = new_end > target_count - 1
        new_end = min(new_end, target_count - 1)
        if new_end <= new_start:
            raise ValueError(
                f"帧范围换算后只剩 {new_end - new_start + 1} 帧，无法形成区间"
                f"（目标 0–{target_count - 1}）；请先在目标 case 上调整抽取范围")
        scaled = (target_w, target_h) != (source_w, source_h)
        inner = (_scale_inner(inner, source_w, source_h, target_w, target_h)
                 if scaled else {"x": x, "y": y, "width": w, "height": h})
        vp["yaw_deg"] = round(float(yaw_deg), 2)
        vp["pitch_deg"] = round(float(pitch_deg), 2)
        vp["fov_h_deg"] = round(float(fov_h_deg), 1)
        new_frames = {"start": new_start, "end": new_end}
        self.case.viewport = vp
        self.case.inner = inner
        self.case.frames = new_frames
        self.save_geometry()
        self.key = self._new_key()
        return scaled, frames_clamped

    def nudge(self, dyaw: float, dpitch: float) -> tuple[float, float]:
        """摇杆微调；服务端做算术并写回 draft，值永远是合法的
        （yaw 在 ±180 回绕，pitch 钳到 ±89）。"""
        vp = self._vp()
        yaw = ((vp.get("yaw_deg", 0.0) + dyaw + 180.0) % 360.0 + 360.0) % 360.0 - 180.0
        vp["yaw_deg"] = round(yaw, 2)
        vp["pitch_deg"] = round(max(-89.0, min(89.0, vp.get("pitch_deg", 0.0) + dpitch)), 2)
        self.case.viewport = vp
        self.save_geometry()
        self.key = self._new_key()
        return vp["yaw_deg"], vp["pitch_deg"]

    # ── 预览（缓存解码帧 + 重投影）──────────────────────────────────────

    def viewport_png(self, frame: int | None = None) -> bytes:
        """视口预览 PNG 字节：当前帧重投影。
        红框由页面的 #innerBox 单独绘制；服务端不再把 inner 叠加进图片，
        否则同一画面上会出现两个框，而且拖 inner 会强迫重画整张 8K 预览。
        内存组合并按 (视口, 帧) 记忆化——共享 vp.png 文件曾在并发响应下被
        边写边读、发过撕裂的图。frame=None 用第 0 帧。"""
        stamp = (self.key, frame)
        if self._vp_bytes and self._vp_bytes[0] == stamp:
            log.debug("preview.hit", extra={"data": {
                "event": "preview.hit", "case": self.name, "frame": frame,
                "key": self.key, "source": self.source_kind,
            }})
            return self._vp_bytes[1]

        started = time.perf_counter()
        if frame is None:
            t0 = time.perf_counter()
            med = self._erp_medium(0)
            t1 = time.perf_counter()
            view = self._reproject(med)
            t2 = time.perf_counter()
            timing = {"decode_ms": round((t1 - t0) * 1000, 1),
                      "reproject_ms": round((t2 - t1) * 1000, 1)}
        else:
            t0 = time.perf_counter()
            cached = self.viewport_frame_path(frame)
            view = cv2.imread(str(cached))
            t1 = time.perf_counter()
            timing = {"frame_ms": round((t1 - t0) * 1000, 1)}
            if view is None:
                raise RuntimeError(f"frame cache unreadable: {cached}")
        t2 = time.perf_counter()
        ok, buf = cv2.imencode(".png", view)
        if not ok:
            raise RuntimeError("viewport preview PNG encode failed")
        t3 = time.perf_counter()
        data = buf.tobytes()
        self._vp_bytes = (stamp, data)
        log.info("preview.render", extra={"data": {
            "event": "preview.render", "case": self.name, "frame": frame,
            "key": self.key, "source": self.source_kind, "hit": False, **timing,
            "png_ms": round((t3 - t2) * 1000, 1),
            "total_ms": round((t3 - started) * 1000, 1),
        }})
        self._gc()
        return data

    def viewport_frame_path(self, frame: int) -> Path:
        """单帧的干净重投影（无叠加框：拖 inner 不该打这张缓存作废）。
        按视口几何分目录缓存；原子写（临时名 + replace）。"""
        if not 0 <= frame < self.frame_count:
            raise ValueError(f"frame {frame} out of range 0..{self.frame_count - 1}")
        d = self.dir / "vpframes" / self._vp_geo_key()
        out = d / f"vp_{frame:06d}.jpg"
        if not out.exists():
            view = self._reproject(self._erp_medium(frame))
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f"{out.stem}.{threading.get_ident()}.tmp.jpg"
            cv2.imwrite(str(tmp), view, [cv2.IMWRITE_JPEG_QUALITY, FRAME_JPEG_Q])
            tmp.replace(out)
        return out

    def _video(self) -> str:
        if self.source_kind == "proxy" and self.proxy_file.is_file():
            return str(self.proxy_file)
        return str(Path(self.case.source_path))

    def _reproject(self, med: np.ndarray) -> np.ndarray:
        vp = self._vp()
        try:
            w0, h0 = int(vp["width"]), int(vp["height"])
            fov = float(vp["fov_h_deg"])
            yaw = float(vp["yaw_deg"])
            pitch = float(vp["pitch_deg"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "还没有可用的视口（case.toml 的 [viewport] 缺字段）："
                "先在选区页点全景图并选好视口尺寸，预览才有投影参数") from exc
        if w0 <= 0 or h0 <= 0:
            raise ValueError("视口宽高必须是正数：case.toml 的 [viewport] width/height")
        scale = min(VIEWPORT_PREVIEW_W / w0, 1.0)
        vw, vh = int(round(w0 * scale)), int(round(h0 * scale))
        return erp_to_rect(med, np.radians(yaw), np.radians(pitch),
                           np.radians(fov), vw, vh)

    def _medium_of(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w > MEDIUM_W:
            return cv2.resize(frame, (MEDIUM_W, round(h * MEDIUM_W / w)),
                              interpolation=cv2.INTER_AREA)
        return frame

    def _erp_medium(self, frame: int) -> np.ndarray:
        if self.is_image and frame != 0:
            raise ValueError("图片流程只有一帧（frame 0）")
        med = self._erp_lru.get(frame)
        if med is not None:
            self._erp_lru.pop(frame)
            self._erp_lru[frame] = med
            return med
        med = self._medium_of(_read_frame(self._video(), frame))
        self._erp_lru[frame] = med
        while len(self._erp_lru) > ERP_MEDIUM_LRU:
            self._erp_lru.pop(next(iter(self._erp_lru)))
        return med

    def _new_key(self) -> str:
        """预览图的缓存标识：只随视口和预览源（源片/代理）变化。
        inner 是页面叠加的框，不进图片，所以拖 inner 不应让预览失效；
        代理就绪或重建后 source_key 变化，新 URL 让浏览器重新取图。"""
        return hashlib.sha1(
            f"{self.source_key}|{self.case.viewport}".encode()).hexdigest()[:10]

    def _vp_geo_key(self) -> str:
        return hashlib.sha1(
            f"{self.source_key}|{self.case.viewport}".encode()).hexdigest()[:10]

    def _gc(self):
        """derived/pick 是可再生成缓存：清掉旧方案遗留与旧几何的帧目录。"""
        for legacy in (*self.dir.glob("erp_*.png"), *self.dir.glob("vp_*.png"),
                       *self.dir.glob("*.tmp"), *self.dir.glob("*.tmp.jpg")):
            legacy.unlink(missing_ok=True)
        for name in ("erp.png", "vp.png"):
            (self.dir / name).unlink(missing_ok=True)
        vpf = self.dir / "vpframes"
        if vpf.is_dir():
            for d in vpf.iterdir():
                if d.is_dir() and d.name != self._vp_geo_key():
                    shutil.rmtree(d, ignore_errors=True)
                elif d.is_file() and d.name.endswith(".tmp.jpg"):
                    d.unlink(missing_ok=True)
