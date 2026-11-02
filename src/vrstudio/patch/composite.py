"""Paste-back: re-inject regenerated viewport clips into the ERP video.

For each frame in a segment: re-extract the original viewport, blend the AI clip's
inner rect over the original outer ring, then inverse-project the blended viewport
back into the ERP frame.

Two things here are per-frame and unavoidable: the viewport extraction and the
multi-band blend. Everything else depends only on the segment geometry, so
`SegmentMaps` builds it once — the original code rebuilt a full (H,W,3) ray grid
for the viewport *and* for the ERP footprint on every single frame, which was
about two thirds of the runtime on a 6K canvas.

R2: the old module-level `_MAPS_CACHE` is gone. `SegmentMaps` is built once by
the merge loop's caller and held there; caching behind global mutable state
(no invalidation, no bound) bought nothing the merge loop didn't already do.
"""

import numpy as np
import cv2

from .projection import (build_view_map, build_paste_map, remap_viewport,
                         rect_to_erp_bbox, erp_to_rect)
from .blend import make_inner_mask, multiband_blend
from .framealign import index_map

class SegmentMaps:
    """Frame-invariant geometry for one segment: sampling maps + inner mask.

    Cheap to build relative to the per-frame work it saves, but still worth
    hoisting out of the frame loop rather than rebuilding per call. The caller
    holds one instance for the whole merge pass.
    """

    def __init__(self, segment, erp_w, erp_h, feather_px=16):
        self.segment = segment
        self._build(segment.viewport, segment.inner, erp_w, erp_h, feather_px)

    @classmethod
    def from_geometry(cls, viewport, inner, erp_w, erp_h, feather_px=16):
        """Build the maps from bare geometry, without inventing a Segment.

        `flow = patchimg` is a single frame: it has a image.json with viewport
        and inner but no time range, so faking a Segment just to reach the
        constructor would put a fake frame range into the geometry path.
        """
        maps = cls.__new__(cls)
        maps.segment = None
        maps._build(viewport, inner, erp_w, erp_h, feather_px)
        return maps

    def _build(self, viewport, inner, erp_w, erp_h, feather_px):
        self.viewport = viewport
        self.inner = inner
        self.erp_w = erp_w
        self.erp_h = erp_h
        self.view_map = build_view_map(viewport, erp_w, erp_h)
        self.paste_map = build_paste_map(viewport, viewport.width, viewport.height,
                                         erp_w, erp_h)
        self.mask = make_inner_mask((viewport.height, viewport.width), inner,
                                    feather_px)
        self.cover = self.paste_map[2]
        self.x0 = self.paste_map[3]
        self.y0 = self.paste_map[4]
        # inner 在 ERP 上真正覆盖的像素（bbox 形状），贴回只写这些像素。
        u, v = self.paste_map[0], self.paste_map[1]
        self.inner_support = (self.cover
                              & (u >= inner.x) & (u < inner.x + inner.width)
                              & (v >= inner.y) & (v < inner.y + inner.height))
        self.inner_bbox = (self.x0, self.y0,
                           self.x0 + u.shape[1], self.y0 + v.shape[0])

    def composite_frame(self, erp_frame, ai_frame, levels=5):
        """Blend one AI frame into one ERP frame; returns a new uint8 ERP frame.

        Only the viewport footprint is touched: pixels outside `cover` are copied
        through unchanged, so the panorama outside the viewport is bit-identical.
        """
        orig_view = remap_viewport(erp_frame, self.view_map)
        blended = multiband_blend(orig_view, ai_frame, self.mask, levels)

        # INTER_CUBIC: the paste is a x~0.7 downscale (canvas > footprint) where
        # bilinear drops samples; policy documented in verification.md, kept in
        # lockstep with projection.rect_to_erp_bbox (gate 4 reference)
        patch = cv2.remap(blended, self.paste_map[0], self.paste_map[1],
                          cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT)

        out = erp_frame.copy()
        sub = out[self.y0:self.y0 + patch.shape[0],
                  self.x0:self.x0 + patch.shape[1]].astype(np.float32)
        sub[self.cover] = patch[self.cover]
        out[self.y0:self.y0 + patch.shape[0],
            self.x0:self.x0 + patch.shape[1]] = np.clip(sub, 0, 255).astype(np.uint8)
        return out

    def paste_inner(self, erp_frame, ai_frame, levels=5):
        """composite_frame, then write only `inner` back: outside is bit-equal.

        The blend feathers the inner boundary, and the inverse projection
        resamples, so the footprint ring would pick up sub-pixel differences if
        it were written back. Copying only `inner_support` from the composited
        frame makes the pixel guarantee structural instead of a tolerance.
        """
        composited = self.composite_frame(erp_frame, ai_frame, levels)
        x0, y0, x1, y1 = self.inner_bbox
        out = erp_frame.copy()
        support = self.inner_support
        out[y0:y1, x0:x1][support] = composited[y0:y1, x0:x1][support]
        return out


def normalize_ai_frame(frame, target_size, allow_crop: bool = False):
    """Resize one AI frame to ``target_size`` without silently stretching it.

    Same aspect: uniform scale (identity when the sizes already match). Different
    aspect: refuse by default; ``allow_crop`` explicitly opts into a cover-scale
    followed by a centre crop. The 0.1% tolerance is about one pixel of rounding
    at 1080p.
    """
    target_w, target_h = int(target_size[0]), int(target_size[1])
    src_h, src_w = int(frame.shape[0]), int(frame.shape[1])
    if (src_w, src_h) == (target_w, target_h):
        return frame
    src_aspect = src_w / src_h if src_h else 0.0
    target_aspect = target_w / target_h if target_h else 0.0
    mismatch = abs(src_aspect - target_aspect) > target_aspect * 0.001
    if not mismatch:
        return cv2.resize(frame, (target_w, target_h),
                          interpolation=cv2.INTER_AREA)
    if not allow_crop:
        raise ValueError(
            f"AI frame is {src_w}x{src_h} (aspect {src_aspect:.4f}) but the "
            f"viewport is {target_w}x{target_h} (aspect {target_aspect:.4f}); "
            f"enable --allow-crop to centre-crop the AI frame")
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(target_w, int(round(src_w * scale))), max(target_h, int(round(src_h * scale)))
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
    x0 = max(0, (new_w - target_w) // 2)
    y0 = max(0, (new_h - target_h) // 2)
    return resized[y0:y0 + target_h, x0:x0 + target_w]


def align_ai_clip(ai_frames, target_size, src_fps, dst_fps, n, allow_crop=False):
    """Resize and time-resample an AI clip to exactly `n` frames of `target_size`.

    External AI tools routinely disagree with the sidecar on resolution, fps and
    frame count (Viggle returns 25fps, VACE 16fps, and so on), so this is where
    that mismatch is absorbed. The resampling rule itself lives in
    :mod:`vrstudio.patch.framealign` (R1).
    """
    ai_frames = np.asarray(ai_frames)
    src_n = len(ai_frames)
    if src_n == 0:
        raise ValueError("ai clip has no frames to align")
    resized = np.stack([normalize_ai_frame(f, target_size, allow_crop=allow_crop)
                        for f in ai_frames])
    if src_n == n:
        return resized
    idx = index_map(n, src_fps, dst_fps, src_n)
    return resized[idx]
