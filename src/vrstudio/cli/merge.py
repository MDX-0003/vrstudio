"""vrstudio merge: paste the regenerated AI viewport clip back into the ERP video.

Streaming (read -> composite -> encode, one frame at a time) — a full-video
in-memory shape peaks near 48 GB on a 6K canvas. Encode parameters match the
verified pixel baseline exactly (libx264 crf/preset via media.FfmpegSink,
two-pass audio remux; see docs/knowledge/verification.md), which is what the
pixel regression gate relies on.

R5: single-segment only, enforced loudly (case.load_sidecar_single_segment).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import typer

from ..patch.sidecar import InnerRect, load_image
from ..patch.case import load_sidecar_single_segment
from ..patch.composite import SegmentMaps, normalize_ai_frame
from ..patch.framealign import index_map, short_by
from ..media.ffmpeg import FfmpegSink, ffmpeg_path, has_audio
from ..media.imageio import read_image, write_png
from ..progress import PhaseLog as Log, PhaseRateMeter as RateMeter, format_duration, open_log_file
from ..patch.restore import resolve_ai_clip
from ..patch import scalefit
from ..redraw.manifest import ImageManifest
from ..redraw.image import ImageFlowError, stitch_image
from .. import case as case_mod
from .. import config as config_mod
from ..jsonio import json_safe

app = typer.Typer(add_completion=False, help=__doc__)


def resolve_case_inputs(case_name: str, cases_dir,
                        extract_version: str | None = None) -> dict:
    """统一 case 模型 → merge 的四元组（input/ai/sidecar/output）。

    纯路径解析，无 ffmpeg / 无解码——web 与单测共用。``extract_version`` 缺省用
    ``[extract] current``；显式版本让合并页可以选旧版而不改 generate 的当前版本。
    output 落 ``out/<版本>_space.mp4``（空间合并只管中段；时间拼接是 ``assemble``）；
    AI 输入优先 ``[merge] input``，没有指针时回退该版本的 ``result.mp4``。
    """
    case = case_mod.load(case_name, cases_dir)
    if case.flow != "patch":
        raise typer.Exit(f"merge --case: case {case_name} 是 {case.flow} 流"
                         f"（patch 流专用入口；fullredraw 用 recompose）")
    version = extract_version or case.extract_current
    if not version:
        raise typer.Exit(f"merge --case: case {case_name} 没有抽取版本"
                         f"（[extract] current 未定，先 `vrstudio extract name`）")
    d = case.dir(cases_dir) / "extracts" / version
    if not (d / "clip.json").is_file():
        raise typer.Exit(f"merge --case: 抽取版本 {version} 缺 clip.json")
    ai = (case.merge_input_file(cases_dir) if case.merge_input
          else d / "result.mp4")
    if ai is None or not ai.is_file():
        raise typer.Exit(
            f"merge --case: 没有可用的 AI 输入（{d / 'result.mp4'} 不存在）——"
            f"先 `vrstudio generate adopt --case {case_name}`，或在合并页上传外部结果")
    src = case_mod.check_source(case, cases_dir)
    out_dir = case.dir(cases_dir) / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "input": Path(src), "ai": ai, "sidecar": d / "clip.json",
        "output": out_dir / f"{version}_space.mp4",
    }


class AiFrameSource:
    """Yields the AI clip's frames resampled to the segment's frame count.

    Reads the source clip sequentially and keeps only the current frame; the
    target->source index map is monotonic (framealign.index_map), so each source
    frame is decoded at most once and only decoded frames are resized.
    `correction` (optional 2x3, from scalefit) warps each frame onto the clip's
    geometry before handing it out -- undoing the AI tool's global scale/shift.
    """

    def __init__(self, path, target_size, dst_fps, n, log, correction=None,
                 allow_crop=False):
        self.path = path
        self.target_size = target_size
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise typer.Exit(f"cannot open AI clip: {path}")
        self.src_fps = self.cap.get(cv2.CAP_PROP_FPS) or dst_fps
        self.src_n = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.src_size = (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                         int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        self.n = n
        self.idx = index_map(n, self.src_fps, dst_fps, self.src_n)
        self._raw = None
        self._raw_i = -1
        self._scaled = None
        self._scaled_i = -1
        self.short = short_by(n, self.src_n)
        self.correction = None if correction is None else np.float32(correction)
        self.allow_crop = bool(allow_crop)

        src_aspect = self.src_size[0] / self.src_size[1] if self.src_size[1] else 0.0
        target_aspect = target_size[0] / target_size[1] if target_size[1] else 0.0
        mismatch = abs(src_aspect - target_aspect) > target_aspect * 0.001
        if mismatch and not self.allow_crop:
            raise typer.Exit(
                f"AI clip is {self.src_size[0]}x{self.src_size[1]} "
                f"(aspect {src_aspect:.4f}) but the viewport is "
                f"{target_size[0]}x{target_size[1]} (aspect {target_aspect:.4f}); "
                f"pass --allow-crop to centre-crop the AI clip")

        log.info(f"ai clip : {self.src_n} frames, {self.src_size[0]}x"
                 f"{self.src_size[1]} @ {self.src_fps:.2f}fps -> resample to "
                 f"{n} frames @ {dst_fps:.2f}fps, "
                 + ("centre-crop then resize to " if mismatch else "resize to ")
                 + f"{target_size[0]}x{target_size[1]}"
                 + (", scale-fit correction applied" if self.correction is not None else ""))

    def frame(self, i):
        want = int(self.idx[i])
        while self._raw_i < want:
            ok, f = self.cap.read()
            if not ok:
                break
            self._raw = f
            self._raw_i += 1
        if self._raw is None:
            raise typer.Exit(f"AI clip {self.path!r} yielded no decodable frames")
        if self._scaled_i != self._raw_i:
            self._scaled = normalize_ai_frame(
                self._raw, self.target_size, allow_crop=self.allow_crop)
            self._scaled_i = self._raw_i
        if self.correction is not None:
            # warp fresh off the resize cache: frame() may be called twice for
            # the same source frame (tail duplication) and must not double-warp
            return cv2.warpAffine(self._scaled, self.correction,
                                  self.target_size, flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REFLECT)
        return self._scaled

    def close(self):
        self.cap.release()


def _merge_image(case, cases_dir, *, feather: int, levels: int,
                 report: Path | None, quiet: bool, progress_every: int,
                 allow_crop: bool = False,
                 extract_version: str | None = None) -> None:
    """patchimg 的贴回：AI 视口结果归一 → composite_frame → 只写 inner。

    与视频 patch 同一条几何路径（SegmentMaps + composite_frame），区别只在
    单帧与逐位保证：贴回只覆盖 inner 覆盖的 ERP 像素，其余像素从源图原样
    复制，因此 ``np.array_equal(out[~support], src[~support])`` 是结构性成立
    的，不依赖容差。
    """
    version = extract_version or case.extract_current
    if not version:
        raise typer.Exit("merge: patchimg 还没有 extract 版本——先 extract")
    d = case.dir(cases_dir) / "extracts" / version
    geometry_path = d / "image.json"
    result_path = d / "result.png"
    if not geometry_path.is_file():
        raise typer.Exit(f"merge: 缺 {geometry_path.name}——重跑 extract")
    if not result_path.is_file():
        raise typer.Exit(
            f"merge: 没有采用的结果（{result_path} 不存在）——先 "
            f"`vrstudio generate adopt --case {case.name} --index N`")
    src = case_mod.check_source(case, cases_dir)
    try:
        geo = load_image(geometry_path)
    except (OSError, ValueError, TypeError) as exc:
        raise typer.Exit(f"merge: {geometry_path.name} 不合法：{exc}") from exc

    fh, log_path = open_log_file(f"merge_{case.name}_image")
    log = Log(every=progress_every, quiet=quiet, fh=fh)
    typer.echo(f"log -> {log_path}")
    try:
        source = read_image(src)
        erp_h, erp_w = source.shape[:2]
        if (erp_w, erp_h) != (geo.erp_width, geo.erp_height):
            raise typer.Exit(
                f"merge: 源图 {erp_w}x{erp_h} 与 image.json 记录的 "
                f"{geo.erp_width}x{geo.erp_height} 不一致——源图换过？")
        generated = read_image(result_path)
        try:
            ai = normalize_ai_frame(
                generated, (geo.viewport.width, geo.viewport.height),
                allow_crop=allow_crop)
        except ValueError as exc:
            raise typer.Exit(f"merge: {exc}") from exc

        maps = SegmentMaps.from_geometry(geo.viewport, geo.inner, erp_w, erp_h,
                                         feather_px=feather)
        if not maps.inner_support.any():
            raise typer.Exit("merge: inner 没有投影到任何 ERP 像素")

        x0, y0, x1, y1 = maps.inner_bbox
        support = maps.inner_support
        out_image = maps.paste_inner(source, ai, levels=levels)
        changed = int((out_image[y0:y1, x0:x1][support]
                       != source[y0:y1, x0:x1][support]).any(axis=1).sum())
        if changed == 0:
            log.info("WARNING: the model left the inner rect unchanged")

        out_dir = case.dir(cases_dir) / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{version}_image.png"
        write_png(out, out_image)

        data = {
            "case": case.name,
            "extract": version,
            "erp": [erp_w, erp_h],
            "viewport": {"yaw_deg": geo.viewport.yaw_deg,
                         "pitch_deg": geo.viewport.pitch_deg,
                         "fov_h_deg": geo.viewport.fov_h_deg,
                         "width": geo.viewport.width,
                         "height": geo.viewport.height},
            "inner": {"x": geo.inner.x, "y": geo.inner.y,
                      "width": geo.inner.width, "height": geo.inner.height},
            "normalize": {
                "from": [generated.shape[1], generated.shape[0]],
                "to": [geo.viewport.width, geo.viewport.height],
                "allow_crop": bool(allow_crop),
            },
            "local": {
                "support_pixels": int(support.sum()),
                "changed_inside": changed,
                "maxdiff_outside": 0,
            },
            "output": str(out),
        }
        target = report or (d / "report.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(json_safe(data), ensure_ascii=False, indent=2,
                       allow_nan=False),
            encoding="utf-8")

        log.phase(f"erp {erp_w}x{erp_h}  viewport "
                  f"{geo.viewport.width}x{geo.viewport.height}  "
                  f"inner {geo.inner.width}x{geo.inner.height}")
        log.info(f"support {int(support.sum())} px  changed {changed}  "
                 f"outside maxdiff 0（只写 inner）")
        typer.echo(f"image    {out}  changed {changed}/{int(support.sum())}  "
                   f"outside maxdiff 0")
        typer.echo(f"report   {target}")
    finally:
        fh.close()


#: 拼接输入宽高比的相对容差：模型输出与画布只差几像素，2% 足够；2:1 源图或成片
#: （偏差约 14%）会在这里被拒，而不是被 ``restore_canvas`` 先拉成 21:9 再靠相关性碰运气。
_INPUT_ASPECT_TOL = 0.02


def _check_stitch_input(generated, manifest, path: Path) -> None:
    """拼接输入必须是这次 extract 的 21:9 画布图（决策 66）。"""
    height_px, width_px = generated.shape[:2]
    canvas_w, canvas_h = (int(v) for v in manifest.canvas.size)
    ratio = width_px / max(1, height_px)
    target = canvas_w / max(1, canvas_h)
    if abs(ratio / target - 1.0) > _INPUT_ASPECT_TOL:
        typer.secho(
            f"merge: 拼接输入必须是 {canvas_w}x{canvas_h}（21:9）的画布图，"
            f"当前 {width_px}x{height_px}（{ratio:.3f}:1）：{path}；"
            f"2:1 源图或成片不能直接拼接", err=True)
        raise typer.Exit(2)


def _merge_full(case, cases_dir, *, report: Path | None, quiet: bool,
                progress_every: int, follow_input: bool = False,
                extract_version: str | None = None) -> None:
    """fullRedrawImg 的收口：把 4K 21:9 结果 stitch 回 2:1 的源图尺寸。

    与 fullredraw 的视频 recompose 同一条单帧核心；模型裁画布或重构图时
    ``stitch_image`` 按冗余带相关性拒绝出片。``follow_input`` 让输出按输入
    像素密度缩放，成片加尺寸后缀，避免覆盖案例分辨率的成片。
    """
    version = extract_version or case.extract_current
    if not version:
        raise typer.Exit("merge: fullRedrawImg 还没有 extract 版本——先 extract")
    d = case.dir(cases_dir) / "extracts" / version
    manifest_path = d / "image_pad.json"
    # 拼接输入：``[merge] input`` 指向的素材优先，没写指针才回退 adopt 落点（决策 66）。
    result_path = case.merge_input_file(cases_dir) or d / "result.png"
    if not manifest_path.is_file():
        raise typer.Exit(f"merge: 缺 {manifest_path.name}——重跑 extract")
    if not result_path.is_file():
        if case.merge_input:
            raise typer.Exit(
                f"merge: [merge] input 指向的素材文件不存在：{result_path}")
        raise typer.Exit(
            f"merge: 没有拼接输入（{result_path} 不存在）——在素材库里选一张 "
            f"21:9 画布图，或先 `vrstudio generate adopt --case {case.name} --index N`")
    src = case_mod.check_source(case, cases_dir)
    conf = config_mod.load()
    try:
        manifest = ImageManifest.load(manifest_path)
    except (OSError, ValueError, TypeError) as exc:
        raise typer.Exit(f"merge: {manifest_path.name} 不合法：{exc}") from exc

    fh, log_path = open_log_file(f"merge_{case.name}_full")
    log = Log(every=progress_every, quiet=quiet, fh=fh)
    typer.echo(f"log -> {log_path}")
    try:
        generated = read_image(result_path)
        _check_stitch_input(generated, manifest, result_path)
        repair = str(conf.get("recompose.boundary_repair", "off"))
        repair_px = (max(8, int(round(manifest.canvas.L * float(
            conf.get("recompose.boundary_repair_pct", 15.0)) / 100.0)))
            if repair == "feather" else 0)
        try:
            stitched = stitch_image(
                generated, manifest,
                search_pct=float(conf.get("recompose.search_pct", 25.0)),
                max_sample_rows=int(conf.get("recompose.max_sample_rows", 256)),
                min_band_correlation=float(
                    conf.get("recompose.min_band_correlation", 0.60)),
                warn_quality=float(conf.get("recompose.warn_quality", 1.15)),
                restore_interp=str(conf.get("recompose.restore_interp", "lanczos4")),
                blend_weight=str(conf.get("recompose.blend_weight", "smoothstep")),
                endpoint_lock_pct=float(conf.get("recompose.endpoint_lock_pct", 2.0)),
                repair=repair, repair_px=repair_px,
                interior_band=tuple(conf.get("verify.interior_band", (0.25, 0.75))),
                max_wrap_percentile=float(conf.get("verify.max_wrap_percentile", 95)),
                worst_interior_tol=float(conf.get("verify.worst_interior_tol", 1.0)),
                follow_input=follow_input,
            )
        except ImageFlowError as exc:
            raise typer.Exit(f"merge: {exc}") from exc

        width, height = manifest.source.size
        out_w, out_h = stitched.output_size
        if stitched.image.shape[:2] != (int(out_h), int(out_w)):
            raise typer.Exit(
                f"merge: stitch 输出 {stitched.image.shape[1]}x"
                f"{stitched.image.shape[0]} 与预期 {out_w}x{out_h} 不一致")

        out_dir = case.dir(cases_dir) / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = "" if stitched.output_mode == "case" else f"_{out_w}x{out_h}"
        out = out_dir / f"{version}_image{suffix}.png"
        write_png(out, stitched.image)

        data = {
            "case": case.name,
            "extract": version,
            "input": {"asset": case.merge_input, "path": str(result_path),
                      "sha256": case_mod.file_sha256(result_path)},
            "source": [int(width), int(height)],
            "canvas": list(manifest.canvas.size),
            "output_size": [int(out_w), int(out_h)],
            "output_mode": stitched.output_mode,
            "registration": {
                "d_star": stitched.registration.d_star,
                "correlation": stitched.registration.correlation,
                "quality": stitched.registration.quality,
                "mae_min": stitched.registration.mae_min,
            },
            "gate": stitched.gate,
            "seam": stitched.metrics.as_dict(),
            "verdict": stitched.verdict,
            "reasons": list(stitched.reasons),
            "aspect_drift_pct": stitched.aspect_drift_pct,
            "output": str(out),
        }
        target = report or (d / "report.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(json_safe(data), ensure_ascii=False, indent=2,
                       allow_nan=False),
            encoding="utf-8")

        log.phase(f"stitched {out_w}x{out_h}  d*={stitched.registration.d_star}"
                  f"  corr={stitched.registration.correlation:.3f}"
                  f"  gate={stitched.gate}  mode={stitched.output_mode}")
        log.info(f"seam wrap {stitched.metrics.wrap:.3f} percentile "
                 f"{stitched.metrics.wrap_percentile:.1f} -> {stitched.verdict}")
        if abs(stitched.aspect_drift_pct) > 2.0:
            log.info(f"WARNING: the model's aspect differs from the canvas by "
                     f"{stitched.aspect_drift_pct:+.1f}%")
        typer.echo(f"image    {out}")
        typer.echo(f"report   {target}")
    finally:
        fh.close()


@app.command()
def merge(
    case_name: str = typer.Option(None, "--case",
                                  help="统一 case 名：input/ai/sidecar/output 全部"
                                       "自动定位（源哈希守卫 + extracts/<版本> + out/）"),
    cases_dir: Path = typer.Option(None, help="default: <repo>/cases"),
    extract_version: str = typer.Option(
        None, "--extract",
        help="要合并的抽取版本；缺省用 [extract] current。合并页选旧版时显式传入"),
    input_video: Path = typer.Option(None, "--input", help="source ERP (360) video"),
    ai: Path = typer.Option(None, help="AI-regenerated viewport clip"),
    sidecar: Path = typer.Option(None, help="clip.json (single segment)"),
    output: Path = typer.Option(None, help="output ERP mp4"),
    inner: str = typer.Option(None, metavar="X,Y,W,H",
                              help="override the sidecar inner rect"),
    scale_fit: str = typer.Option("off", metavar="OFF|AUTO|JSON",
                                  help="correct the AI clip's global scale/shift: "
                                       "off | auto (fit the correction against the "
                                       "source clip outside the inner rect, cached "
                                       "next to the AI file) | path to a "
                                       ".scalefit.json"),
    max_frames: int = typer.Option(0, help="stop after N frames (preview)"),
    snapshots: str = typer.Option("", help="comma-separated frame indices to save as PNG"),
    feather: int = typer.Option(16, help="inner-rect boundary feather width"),
    levels: int = typer.Option(5, help="Laplacian pyramid levels"),
    crf: int = typer.Option(18, help="x264 crf"),
    preset: str = typer.Option("medium", help="x264 preset"),
    report: Path = typer.Option(None, help="write a JSON run report here"),
    no_restore: bool = typer.Option(False, help="feed the raw AI clip to the "
                                                "resampler even when it misses "
                                                "the segment contract "
                                                "(pre-restore behavior)"),
    allow_crop: bool = typer.Option(
        False, "--allow-crop",
        help="允许比例不一致的 AI 成片按 cover 缩放后中心裁切；默认报错，"
             "firstLastFrame 正常路径不需要"),
    follow_input: bool = typer.Option(
        False, "--follow-input",
        help="fullRedrawImg：输出按拼接输入的像素密度缩放，不放大到案例源分辨率；"
             "成片加 _<宽>x<高> 后缀，避免覆盖案例分辨率的成片"),
    progress_every: int = typer.Option(0),
    quiet: bool = typer.Option(False, "-q"),
):
    if case_name:
        case = case_mod.load(case_name, cases_dir)
        if follow_input and case.flow != "fullRedrawImg":
            raise typer.Exit("merge: --follow-input 只用于 fullRedrawImg 的拼接")
        if case.flow == "patchimg":
            if input_video or ai or sidecar or output:
                raise typer.Exit("merge: --case 与 --input/--ai/--sidecar/--output 互斥"
                                 "（--case 模式全部自动定位）")
            _merge_image(case, cases_dir, feather=feather, levels=levels,
                         report=report, quiet=quiet,
                         progress_every=progress_every, allow_crop=allow_crop,
                         extract_version=extract_version)
            return
        if case.flow == "fullRedrawImg":
            if input_video or ai or sidecar or output:
                raise typer.Exit("merge: --case 与 --input/--ai/--sidecar/--output 互斥"
                                 "（--case 模式全部自动定位）")
            _merge_full(case, cases_dir, report=report, quiet=quiet,
                        progress_every=progress_every, follow_input=follow_input,
                        extract_version=extract_version)
            return
        if input_video or ai or sidecar or output:
            raise typer.Exit("merge: --case 与 --input/--ai/--sidecar/--output 互斥"
                             "（--case 模式四者全部自动定位）")
        resolved = resolve_case_inputs(case_name, cases_dir, extract_version)
        input_video, ai, sidecar, output = (resolved["input"], resolved["ai"],
                                            resolved["sidecar"], resolved["output"])
    elif extract_version:
        raise typer.Exit("merge: --extract 只用于 --case 模式")
    elif follow_input:
        raise typer.Exit("merge: --follow-input 只用于 fullRedrawImg 的 --case 拼接")
    elif not (input_video and ai and sidecar and output):
        raise typer.Exit("merge: 需要 --case，或完整的 --input/--ai/--sidecar/--output")
    t_start = time.time()
    fh, log_path = open_log_file(f"merge_{output.stem}")
    log = Log(every=progress_every, quiet=quiet, fh=fh)
    typer.echo(f"log -> {log_path}")

    # ---- 1. sidecar (single segment, loud) -----------------------------------
    log.phase(f"sidecar: {sidecar}")
    seg = load_sidecar_single_segment(sidecar)
    inner_fit = seg.inner  # the version's rect: the region to avoid when fitting
    if inner:
        ix, iy, iw, ih = (int(v) for v in inner.split(","))
        if iw <= 0 or ih <= 0:
            raise typer.Exit("inner width/height must be positive")
        log.info(f"inner rect overridden by --inner: x={ix} y={iy} w={iw} h={ih} "
                 f"(sidecar said x={seg.inner.x} y={seg.inner.y} "
                 f"w={seg.inner.width} h={seg.inner.height})")
        seg.inner = InnerRect(x=ix, y=iy, width=iw, height=ih)
    vp = seg.viewport
    n_seg = seg.frame_end - seg.frame_start + 1
    n = n_seg
    if max_frames and max_frames < n:
        n = max_frames
        log.info(f"preview: writing {n} of {n_seg} frames (--max-frames {max_frames})")
    log.info(f"segment {seg.id}: frames {seg.frame_start}..{seg.frame_end} "
             f"({n_seg} frames) @ {fps_of(input_video):.2f}fps")
    log.info(f"erp {erp_size(input_video)[0]}x{erp_size(input_video)[1]}, "
             f"viewport {vp.width}x{vp.height} yaw={vp.yaw_deg} "
             f"pitch={vp.pitch_deg} fov={vp.fov_h_deg}")
    log.info(f"inner: x={seg.inner.x} y={seg.inner.y} "
             f"w={seg.inner.width} h={seg.inner.height}")

    # ---- 2. source video -----------------------------------------------------
    cap = cv2.VideoCapture(str(input_video))
    if not cap.isOpened():
        raise typer.Exit(f"cannot open source video: {input_video}")
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    src_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    sidecar_fps = sidecar_fps_value(sidecar)
    if seg.frame_end >= src_frames:
        log.info(f"WARNING: segment ends at frame {seg.frame_end} but the source "
                 f"has {src_frames} frames; truncating to what exists.")
        n_seg = max(0, src_frames - seg.frame_start)
        n = min(n, n_seg)
        if n_seg <= 0:
            raise typer.Exit("segment starts past the end of the source video")
    log.info(f"source : {src_frames} frames, {src_w}x{src_h}; "
             f"compositing {n} frames -> output {src_w}x{src_h}")

    # ---- 3. geometry once ----------------------------------------------------
    log.phase("building projection maps (once for the segment)")
    t0 = time.time()
    maps = SegmentMaps(seg, src_w, src_h, feather)
    log.info(f"viewport footprint on the ERP: "
             f"{maps.paste_map[0].shape[1]}x{maps.paste_map[0].shape[0]} px at "
             f"({maps.x0},{maps.y0}) = "
             f"{100.0 * maps.cover.size / (src_w * src_h):.1f}% of canvas; "
             f"built in {time.time() - t0:.2f}s")

    # ---- 4. AI clip ----------------------------------------------------------
    log.phase("aligning AI clip")
    if not Path(ai).is_file():
        raise typer.Exit(f"cannot open AI clip: {ai}")
    try:
        use_ai, restored = resolve_ai_clip(str(ai), n_seg, sidecar_fps,
                                           no_restore=no_restore, log=log)
    except FileNotFoundError as e:
        raise typer.Exit(str(e))
    correction = None
    if scale_fit.strip().lower() != "off":
        # fit against the version's viewport clip (the AI tool's input), never
        # against the ERP source -- different geometry entirely
        clip_ref = sidecar.with_name("clip.mp4")
        fit = scalefit.resolve(clip_ref, use_ai, inner_fit,
                               (vp.width, vp.height), seg.frame_start, n_seg,
                               scale_fit.strip(), log=log)
        if fit.applied:
            correction = fit.matrix32()
    ai_src = AiFrameSource(use_ai, (vp.width, vp.height), sidecar_fps, n_seg,
                           log, correction=correction, allow_crop=allow_crop)
    if ai_src.short:
        log.info(f"WARNING: AI clip is {ai_src.short} frames short; the last "
                 f"decodable frame is reused to fill the gap.")

    # ---- 5. encoder ----------------------------------------------------------
    ff = ffmpeg_path()
    if not ff:
        raise typer.Exit("no ffmpeg binary found on PATH; merge needs it "
                         "(same encode path as the verified pixel baseline)")
    audio_from = None
    if has_audio(str(input_video)):
        audio_from = str(input_video)
        log.info("encoder: ffmpeg/libx264 + source audio (video-only encode, "
                 "then audio muxed in with -c:v copy)")
    else:
        log.info("encoder: ffmpeg/libx264 (source has no audio track to keep)")

    # ---- 6. stream -----------------------------------------------------------
    log.phase(f"compositing {n} frames")
    if not cap.set(cv2.CAP_PROP_POS_FRAMES, seg.frame_start):
        log.info("WARNING: could not seek; decoding from the start instead.")

    sink = FfmpegSink(str(output), (src_w, src_h), sidecar_fps, crf, preset,
                      audio_from)
    meter = RateMeter(n)
    snap = {int(v) for v in snapshots.split(",") if v.strip()}
    written = 0
    for i in range(n):
        ok, frame = cap.read()
        if not ok:
            log.info(f"source ended early at output frame {i}; writing {written}.")
            break
        out = maps.composite_frame(frame, ai_src.frame(i), levels)
        if i in snap:
            p = output.with_name(output.stem + f"_f{i}.png")
            cv2.imwrite(str(p), out)
            log.info(f"snapshot -> {p}")
        sink.write(out)
        written += 1
        meter.tick()
        log.progress(meter, "compositing")

    log.done()
    sink.close()
    cap.release()
    ai_src.close()

    size_mb = output.stat().st_size / 1e6 if output.exists() else 0
    log.phase(f"wrote {output} ({src_w}x{src_h}, {written} frames, "
              f"{size_mb:.1f} MB, {format_duration(time.time() - t_start)} total)")
    if written < n:
        typer.echo(f"WARNING: only {written} of {n} frames were written")

    if report:
        report.write_text(json.dumps({
            "input": str(input_video), "ai": str(ai), "sidecar": str(sidecar),
            "ai_clip_used": use_ai,
            "restore_mode": (restored or {}).get("mode"),
            "scale_fit": (fit.to_dict() if scale_fit.strip().lower() != "off"
                          else {"mode": "off"}),
            "output": str(output), "frames_written": written,
            "frames_expected": n, "encoder": "ffmpeg/libx264",
            "crf": crf, "preset": preset, "feather": feather, "levels": levels,
            "allow_crop": bool(allow_crop),
            "inner": [seg.inner.x, seg.inner.y, seg.inner.width, seg.inner.height],
            "warnings": (["ai clip short by %d" % ai_src.short] if ai_src.short else []),
            "elapsed_s": round(time.time() - t_start, 1),
        }, indent=2), encoding="utf-8")
        typer.echo(f"report -> {report}")

    fh.close()


def fps_of(path) -> float:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise typer.Exit(f"cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    return float(fps)


def erp_size(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise typer.Exit(f"cannot open video: {path}")
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return size


def sidecar_fps_value(path) -> float:
    with open(path, "r", encoding="utf-8") as f:
        return float(json.load(f)["fps"])


if __name__ == "__main__":
    app()
