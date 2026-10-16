"""pick 页的视口预览服务端渲染（← vrpatch web/render.py 迁入，case 模型换统一版）。

ERP 面不需要服务端渲染：向导页用原生 ``<video>`` 逐帧走查源片（浏览器解码，
零文件）。只有视口预览是一次重投影，而投影数学的唯一权威在
``vrstudio.patch.projection``（AGENTS.md 硬规则 3），所以它留在服务端。
解码的 4K ERP 帧放一个小的内存 LRU（与几何无关，视口移动后仍命中）；
重投影 JPEG 缓存放在 case 目录 ``derived/pick/`` 下，是可随时删除的再生成缓存。

与 vrpatch 版的差异：草稿读写从"vrpatch 私有 case.toml 段落改写"改为统一
case 模型（``vrstudio.case.set_draft``），本模块不再拥有任何写 case 的语法。
"""

from __future__ import annotations

import hashlib
import shutil
import threading
from pathlib import Path

import cv2
import numpy as np

from .. import case as case_mod
from ..patch.projection import erp_to_rect

VIEWPORT_PREVIEW_W = 960  # 视口预览宽度上限
MEDIUM_W = 4096        # 源帧的工作副本宽度上限
FRAME_JPEG_Q = 85      # 逐帧视口缓存质量
ERP_MEDIUM_LRU = 8     # 内存里保留的解码 ERP 帧数（8 * ~25MB ≈ 200MB）


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


class PreviewStore:
    """一个 case 一个实例；case.toml 变更（mtime 变化）时由 app 层重建。"""

    def __init__(self, name: str, cases_root: str | Path | None = None):
        self.name = name
        self.root = cases_root
        self.case = case_mod.load(name, cases_root)   # 失败抛 CaseError（404/400 由上层映射）
        self.dir = self.case.dir(cases_root) / "derived" / "pick"
        self.dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(self._video())
        self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        self.key = self._new_key()
        self._vp_bytes: tuple | None = None
        self._erp_lru: dict[int, np.ndarray] = {}

    # ── 几何读写（统一 case 模型）────────────────────────────────────────

    def _vp(self) -> dict:
        return dict(self.case.viewport or {})

    def _inner(self) -> dict:
        return dict(self.case.inner or {})

    def save_geometry(self) -> None:
        case_mod.set_draft(self.name, viewport=self._vp(), inner=self._inner(),
                           root=self.root)

    def set_viewport_from_erp_click(self, fx: float, fy: float) -> None:
        """fx, fy 是 ERP 面上的 0..1 分数 → 视口中心的经纬度。"""
        vp = self._vp()
        vp["yaw_deg"] = round((fx - 0.5) * 360.0, 1)
        vp["pitch_deg"] = round(max(-89.0, min(89.0, (0.5 - fy) * 180.0)), 1)
        self.case.viewport = vp
        self.save_geometry()
        self.key = self._new_key()

    def set_inner(self, x: int, y: int, w: int, h: int) -> None:
        self.case.inner = {"x": int(x), "y": int(y), "width": int(w), "height": int(h)}
        self.save_geometry()
        self.key = self._new_key()

    def nudge(self, dyaw: float, dpitch: float) -> tuple[float, float]:
        """摇杆微调；服务端做算术并写回，case.toml 里永远是合法值
        （yaw 在 ±180 回绕，pitch 钳到 ±89）。"""
        vp = self._vp()
        yaw = ((vp.get("yaw_deg", 0.0) + dyaw + 180.0) % 360.0 + 360.0) % 360.0 - 180.0
        vp["yaw_deg"] = round(yaw, 2)
        vp["pitch_deg"] = round(max(-89.0, min(89.0, vp.get("pitch_deg", 0.0) + dpitch)), 2)
        self.case.viewport = vp
        self.save_geometry()
        self.key = self._new_key()
        return vp["yaw_deg"], vp["pitch_deg"]

    # ── 预览（便宜：在缓存底图上叠加）────────────────────────────────────

    def viewport_png(self, frame: int | None = None) -> bytes:
        """视口预览 PNG 字节：当前帧重投影 + inner 红框叠加。
        内存组合并按 (几何, 帧) 记忆化——共享 vp.png 文件曾在并发响应下被
        边写边读、发过撕裂的图。frame=None 用第 0 帧。"""
        stamp = (self.key, frame)
        if self._vp_bytes and self._vp_bytes[0] == stamp:
            return self._vp_bytes[1]
        if frame is None:
            view = self._reproject(self._erp_medium(0))
        else:
            cached = self.viewport_frame_path(frame)
            view = cv2.imread(str(cached))
            if view is None:
                raise RuntimeError(f"frame cache unreadable: {cached}")
        self._draw_inner(view)
        ok, buf = cv2.imencode(".png", view)
        if not ok:
            raise RuntimeError("viewport preview PNG encode failed")
        data = buf.tobytes()
        self._vp_bytes = (stamp, data)
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
        return str(Path(self.case.source_path))

    def _reproject(self, med: np.ndarray) -> np.ndarray:
        vp = self._vp()
        w0, h0 = int(vp["width"]), int(vp["height"])
        scale = min(VIEWPORT_PREVIEW_W / w0, 1.0)
        vw, vh = int(round(w0 * scale)), int(round(h0 * scale))
        return erp_to_rect(med, np.radians(vp["yaw_deg"]),
                           np.radians(vp["pitch_deg"]),
                           np.radians(vp["fov_h_deg"]), vw, vh)

    def _draw_inner(self, view: np.ndarray) -> None:
        inner = self.case.inner
        if not inner:
            return
        vp = self._vp()
        scale = min(VIEWPORT_PREVIEW_W / vp["width"], 1.0)
        cv2.rectangle(view,
                      (int(inner["x"] * scale), int(inner["y"] * scale)),
                      (int((inner["x"] + inner["width"]) * scale),
                       int((inner["y"] + inner["height"]) * scale)),
                      (0, 0, 255), 2)

    def _medium_of(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w > MEDIUM_W:
            return cv2.resize(frame, (MEDIUM_W, round(h * MEDIUM_W / w)),
                              interpolation=cv2.INTER_AREA)
        return frame

    def _erp_medium(self, frame: int) -> np.ndarray:
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
        return hashlib.sha1(
            f"{self.case.viewport}|{self.case.inner}".encode()).hexdigest()[:10]

    def _vp_geo_key(self) -> str:
        return hashlib.sha1(str(self.case.viewport).encode()).hexdigest()[:10]

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
