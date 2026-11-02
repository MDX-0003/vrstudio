"""vrstudio relight: 把 AI 氛围图的光照搬到真 GT 上。

定位（决策 48）：真 8K GT 提供结构与细节，AI 4K 氛围图只提供低频光照、色温与遮挡；
输出每个像素都落在 GT 的原始网格上，不做超分、不做内容回填。

一条命令按 GT 的媒体类型派发（决策 59、60）：

- GT 是图片：整幅单帧重打光，``--out`` 写 PNG，可以 ``--dump-light`` 出诊断图。
- GT 是视频：一张氛围图只估一次对齐与光照场、整段逐帧复用（决策 56），``--out`` 写
  MP4；``--ref-frame`` 指定对齐帧，``--sample N`` 只渲染 N 帧采样小样而不编码整段。

派发看 GT 的扩展名（视频见 ``case.kind_of`` 的视频集合，其余按图片处理）。类型专属
参数用错一律按参数错退出 2，不静默忽略。

退出码：0 成功；2 参数错（比例/尺寸/参数越界、参数与媒体类型不符、GT 类型不支持、
配置错）；1 运行失败（读图、对齐不可信、编解码、写报告）。对齐不可信必须失败且不出
产物——错位的光照比不重打光更糟。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import numpy as np
import typer

from .. import case as case_mod
from .. import config as config_mod
from ..case import CaseError, kind_of
from ..media.imageio import ImageError, read_image, write_gray_png, write_png
from ..media.videoio import FrameReader, FrameWriter, VideoIOError, probe
from ..progress import Log, RateMeter
from ..relight.align import (
    MAX_REFERENCE_SCAN,
    MIN_FRAME_HOLD,
    MIN_REFERENCE_STRUCTURE,
    MIN_STRUCTURE_CORRELATION,
    AlignResult,
    estimate_similarity,
    frame_hold,
    gray8,
    reference_small,
    structure_score,
    warp_to_gt,
)
from ..relight.compose import clipped_fraction, light_compose, soft_clip
from ..relight.compose import relight as compose_relight
from ..relight.fields import LightFields, estimate_fields
from ..relight.report import (
    align_report,
    build_report,
    comparison_image,
    gain_image,
    light_image,
    light_report,
    visibility_image,
)

app = typer.Typer(add_completion=False, help=__doc__)

SCENE_PRESETS = {"night": (1.0, 0.35), "day": (0.8, 0.2)}
"""场景预设只给默认值：夜戏 1.0/0.35，日光 0.8/0.2（实测 strength 1.0 过曝 8.9%）。"""


def _param_error(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(2)


def _runtime_error(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)


@dataclass(frozen=True)
class Params:
    """两种媒体共用的打光参数：命令只解析一次，两条路径读同一份。"""

    scene: str
    strength: float
    local_shading: float
    highlight_clip: bool
    clip_knee: float
    clip_max: float

    def as_dict(self) -> dict:
        return {"scene": self.scene, "strength": self.strength,
                "local_shading": self.local_shading,
                "highlight_clip": self.highlight_clip,
                "clip_knee": self.clip_knee, "clip_max": self.clip_max}


def resolve_params(scene: str, strength: float | None,
                   local_shading: float | None) -> tuple[float, float]:
    """场景预设打底，显式参数覆盖；越界与未知场景都是参数错。"""
    if scene not in SCENE_PRESETS:
        _param_error(f"未知场景 {scene!r}，可选：{', '.join(sorted(SCENE_PRESETS))}")
    preset_strength, preset_shading = SCENE_PRESETS[scene]
    strength = preset_strength if strength is None else strength
    local_shading = preset_shading if local_shading is None else local_shading
    if not 0.0 <= strength <= 1.0:
        _param_error(f"--strength 必须在 0..1 之间，收到 {strength}")
    if not 0.0 <= local_shading <= 1.0:
        _param_error(f"--local-shading 必须在 0..1 之间，收到 {local_shading}")
    return strength, local_shading


def check_inputs(gt, atmo) -> None:
    """两张图必须是 2:1 的 ERP、同尺寸。"""
    gt_h, gt_w = gt.shape[:2]
    atmo_h, atmo_w = atmo.shape[:2]
    if gt_w != 2 * gt_h:
        _param_error(f"GT 必须是 2:1 的 ERP，收到 {gt_w}x{gt_h}")
    if (gt_w, gt_h) != (atmo_w, atmo_h):
        _param_error(f"尺寸不一致：GT {gt_w}x{gt_h}，氛围图 {atmo_w}x{atmo_h}")


def dump_diagnostics(out: Path, gt_image, aligned, out_image, fields) -> dict:
    """写 light / gain / visibility / 六联四份诊断图，返回要进报告的统计。"""
    height, width = gt_image.shape[:2]
    size = (width, height)
    light = light_image(fields, size)
    gain = gain_image(fields, size)
    visibility = visibility_image(fields, size)
    stem = Path(out).with_suffix("")
    write_png(Path(f"{stem}_light.png"), light)
    write_gray_png(Path(f"{stem}_gain.png"), gain)
    write_gray_png(Path(f"{stem}_visibility.png"), visibility)
    write_png(Path(f"{stem}_light_cmp.png"), comparison_image(
        ("GT", gt_image),
        ("atmo (aligned)", aligned),
        ("light = guided low(AI)", light),
        ("gain (128 = 1.0x)", gain),
        ("visibility (128 = 1.0)", visibility),
        ("relit", out_image),
    ))
    return light_report(fields)


@dataclass(frozen=True)
class FixedLight:
    """参考帧定下的几何与光照：整段视频共用同一份（决策 56）。"""

    alignment: AlignResult
    fields: LightFields
    ref_frame: int
    ref_matched: float        # 参考帧与氛围图的结构重合度（带通相关）
    frames_checked: int       # 与参考帧按同一坐标比过的其余帧数；0 表示没有
    min_hold: float | None    # 这些帧与参考帧的最低重合度；没有可比帧时为 None

    @property
    def static(self) -> bool:
        return self.min_hold is None or self.min_hold >= MIN_FRAME_HOLD


def _conf(config: Path | None, overrides: list[str]) -> config_mod.Config:
    try:
        return config_mod.load(config, overrides)
    except (config_mod.ConfigError, OSError, ValueError) as exc:
        _param_error(f"配置错误：{exc}")


def _check_video(size: tuple[int, int]) -> None:
    """GT 视频必须是 2:1 ERP，yuv420p 需要偶数宽高。"""
    width, height = size
    if width != 2 * height:
        _param_error(f"GT 视频必须是 2:1 的 ERP，收到 {width}x{height}")
    if width % 2 or height % 2:
        _param_error(f"yuv420p 需要偶数宽高，收到 {width}x{height}")


def resolve_reference(open_frames: Callable[[], Iterator[np.ndarray]], atmo,
                      width: int, ref_frame: int | None, log: Log,
                      *, check_hold: bool = True) -> tuple[FixedLight, np.ndarray]:
    """挑参考帧、定变换与光照场，并检查其余帧是否与参考帧同位。

    ``open_frames`` 每次调用打开一遍视频、返回逐帧迭代器。这里要读两遍：第一遍在
    2K 小图上选参考帧并比较各帧，第二遍取参考帧原分辨率图来估光照场。

    参考帧有两条来源：``ref_frame`` 显式给出（0 起，不合法是参数错），或跑一遍整段
    在 2K 上选与氛围图最相关的一帧。选中的帧还要过结构闸门，不过就退出 1。

    其余帧与参考帧**直接按像素**比结构重合度（``frame_hold``）。固定变换打光要求
    画面在参考帧的坐标里不动，这条比较就是那个要求本身，不需要先估位移；机位移动时
    这条比较会掉下来，命中后在报告里记最低值，由命令给出警告。

    ``check_hold=False`` 供 ``--sample`` 使用：小样预览的全部意义是快，而这一遍要解码
    整段。跳过时 ``frames_checked`` 记为 0、``min_hold`` 记为 None。
    """
    atmo_gray = gray8(reference_small(atmo, width)).astype(np.float32)
    # 扫描在 90 帧就 break，生成器不会被消费到底；不显式 close 的话它停在
    # ``with FrameReader`` 里面，那一路 ffmpeg 会一直挂着并占住 4–5 GB（8K 实测）。
    with closing(open_frames()) as frames:
        if ref_frame is None:
            best = (-np.inf, 0, None)
            scanned = 0
            for index, frame in enumerate(frames):
                scanned += 1
                small = reference_small(frame, width)
                gray = gray8(small)
                if float(gray.std()) < MIN_REFERENCE_STRUCTURE:
                    continue                      # 黑场/空镜：几乎没有结构可选
                score = structure_score(gray, atmo_gray)
                if score > best[0]:
                    best = (score, index, small)
                if index >= MAX_REFERENCE_SCAN:
                    break
            if best[2] is None:
                _runtime_error(f"前 {scanned} 帧里没有带结构的画面（可能是黑场或空镜）")
            score, ref_index, ref_small = best
            log.info(f"reference: frame {ref_index}, structure {score:.3f}")
        else:
            if ref_frame < 0:
                _param_error(f"--ref-frame 必须从 0 起，收到 {ref_frame}")
            ref_small = None
            for index, frame in enumerate(frames):
                if index == ref_frame:
                    ref_small = reference_small(frame, width)
                    break
            if ref_small is None:
                _param_error(f"--ref-frame {ref_frame} 超出视频帧数")
            ref_index = ref_frame
            score = structure_score(gray8(ref_small), atmo_gray)
            log.info(f"reference: frame {ref_index}, structure {score:.3f}")
    if score < MIN_STRUCTURE_CORRELATION:
        # 只提示，不在这里中断：这一项是「未对齐」的带通相关，几像素的平移就能把它
        # 压到 0（实测拼接后的氛围图有 −9 px 位移，相关从 0.55 掉到 −0.01），而
        # 对齐之后本来就可能对得上。真正的闸门是 ``_check_alignment`` 的
        # ``correlation_after``——决策 48 的「判据只有一条」也是这个意思。
        log.warn(f"参考帧与氛围图未对齐时的结构重合度只有 {score:.3f}；"
                 f"按对齐后的重合度判定。")

    alignment = estimate_similarity(ref_small, atmo)

    ref_gray = gray8(ref_small)
    checked = 0
    min_hold = None
    if check_hold:
        # 重新打开：``open_frames()`` 的生成器被完整消费过一遍后再迭代什么也拿不到。
        with closing(open_frames()) as held:
            for index, frame in enumerate(held):
                if index == ref_index:
                    continue
                hold = frame_hold(ref_gray, gray8(reference_small(frame, width)))
                checked += 1
                min_hold = hold if min_hold is None else min(min_hold, hold)

    reference, fields = _reference_fields(open_frames, ref_index, atmo, alignment)
    fixed = FixedLight(alignment=alignment, fields=fields, ref_frame=ref_index,
                       ref_matched=score, frames_checked=checked, min_hold=min_hold)
    return fixed, reference


def _reference_fields(open_frames: Callable[[], Iterator[np.ndarray]], ref_index: int,
                      atmo, alignment: AlignResult) -> tuple[np.ndarray, LightFields]:
    """再读一遍视频，取参考帧的原分辨率图并估光照场。

    扫描时只留了 2K 小图，光场要原分辨率的 GT 引导（引导滤波的半径按 2K 换算到
    原尺寸，小图放大会丢掉遮挡边界）。再解一遍首段视频比把每帧原图留在内存里便宜，
    也符合逐帧流式的约束。``ref_index`` 不超过扫描上限，所以这一遍的解码量有界。
    """
    with closing(open_frames()) as frames:
        for index, frame in enumerate(frames):
            if index == ref_index:
                reference = np.ascontiguousarray(frame)
                return reference, estimate_fields(reference, warp_to_gt(atmo, alignment))
    _runtime_error(f"参考帧 {ref_index} 在第二次读视频时没有出现")


def _write_report(report: Path | None, summary: dict) -> None:
    if not report:
        return
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    except OSError as exc:
        _runtime_error(f"写报告失败：{exc}")
    typer.echo(f"report -> {report}")


def _video_report(fixed: FixedLight, base: dict, frames: int, fps: str,
                  sample: dict | None = None) -> dict:
    """视频版报告：``clip``/``light`` 与图像版同构，新增帧数、帧率与同位检查。

    ``sample`` 只在 ``--sample`` 时出现，记录小样的采样帧号；整段执行时该键不存在，
    所以整段报告仍然是决策 56 定的那一组字段。
    """
    summary = {
        "gt": base["gt"], "atmo": base["atmo"], "size": base["size"],
        "frames": frames, "fps": fps,
        "ref_frame": fixed.ref_frame, "ref_matched": round(fixed.ref_matched, 4),
        "frames_checked": fixed.frames_checked,
        "min_hold": None if fixed.min_hold is None else round(fixed.min_hold, 4),
        "params": base["params"], "align": base["align"],
        "clip": base["clip"], "light": base["light"], "seconds": base["seconds"],
    }
    if sample is not None:
        summary["sample"] = sample
    return summary


def _check_alignment(fixed: FixedLight) -> None:
    alignment = fixed.alignment
    typer.echo(f"align: inliers={alignment.inliers} matches={alignment.matches} "
               f"scale={alignment.scale:.5f} angle={alignment.angle_deg:.4f}° "
               f"shift=({alignment.tx:.1f}, {alignment.ty:.1f})@2K "
               f"fallback={alignment.fallback} "
               f"correlation {alignment.correlation_before:.3f} "
               f"-> {alignment.correlation_after:.3f}")
    if not alignment.trusted:
        _runtime_error(
            f"对齐不可信：结构重合度 {alignment.correlation_after:.3f} "
            f"低于 {MIN_STRUCTURE_CORRELATION}（内点 {alignment.inliers}，"
            f"回退 {alignment.fallback}）。氛围图可能换了构图，"
            f"错位的光照比不重打光更糟，不出产物。")


def _relight_image(gt: Path, atmo: Path, out: Path, params: Params, *,
                   dump_light: bool, report: Path | None, started: float) -> None:
    """单帧路径：读两张图、估一次变换与光照场、乘一次光场、写一张图。"""
    try:
        gt_image = read_image(gt)
        atmo_image = read_image(atmo)
    except ImageError as exc:
        _runtime_error(str(exc))
    check_inputs(gt_image, atmo_image)
    height, width = gt_image.shape[:2]
    typer.echo(f"relight: {width}x{height} scene={params.scene} "
               f"strength={params.strength} local-shading={params.local_shading}")

    alignment = estimate_similarity(gt_image, atmo_image)
    typer.echo(f"align: inliers={alignment.inliers} matches={alignment.matches} "
               f"scale={alignment.scale:.5f} angle={alignment.angle_deg:.4f}° "
               f"shift=({alignment.tx:.1f}, {alignment.ty:.1f})@2K "
               f"fallback={alignment.fallback} "
               f"correlation {alignment.correlation_before:.3f} "
               f"-> {alignment.correlation_after:.3f}")
    if not alignment.trusted:
        _runtime_error(
            f"对齐不可信：结构重合度 {alignment.correlation_after:.3f} "
            f"低于 {MIN_STRUCTURE_CORRELATION}（内点 {alignment.inliers}，"
            f"回退 {alignment.fallback}）。氛围图可能换了构图，"
            f"错位的光照比不重打光更糟，不出图。")

    aligned = warp_to_gt(atmo_image, alignment)
    fields = estimate_fields(gt_image, aligned)
    out_image = compose_relight(gt_image, fields, params.strength, params.local_shading)
    clipped_before = clipped_fraction(out_image)
    clip_stats = None
    if params.highlight_clip:
        out_image = soft_clip(out_image, params.clip_knee, params.clip_max)
        clip_stats = {
            "knee": params.clip_knee,
            "max": params.clip_max,
            "clipped_before": round(clipped_before, 5),
            "clipped_after": round(clipped_fraction(out_image), 5),
        }
        typer.echo(f"clip: over-exposure {clip_stats['clipped_before']:.3%} "
                   f"-> {clip_stats['clipped_after']:.3%}")

    try:
        write_png(out, out_image)
    except (ImageError, OSError) as exc:
        _runtime_error(f"写图失败：{exc}")

    light_stats = None
    if dump_light:
        light_stats = dump_diagnostics(out, gt_image, aligned, out_image, fields)
        typer.echo(f"diagnostics -> {Path(out).with_suffix('')}_light.png 等四份")

    summary = build_report(
        gt=str(gt), atmo=str(atmo), size=(width, height),
        params=params.as_dict(),
        align=align_report(alignment, width),
        clip=clip_stats, light=light_stats,
        seconds=time.perf_counter() - started,
    )
    typer.echo(f"wrote {out} ({summary['seconds']}s)")
    _write_report(report, summary)


def _sample_indices(frames: int, count: int) -> list[int]:
    """全片均匀采样：取 count 段各自的中心帧；帧数不比 count 多时就是全部帧。"""
    if frames <= 0:
        return list(range(count))
    if count >= frames:
        return list(range(frames))
    indices = [min(frames - 1, int(frames * (i + 0.5) / count)) for i in range(count)]
    return sorted(set(indices))


def _write_samples(out_dir: Path, info, conf, light, indices: list[int],
                   params: Params, log: Log) -> list[Path]:
    """按时间点 seek 逐帧取图：只解码被采样的那几帧，不整段解码。"""
    if out_dir.exists() and not out_dir.is_dir():
        _param_error(f"--sample 要求 --out 是目录，但它是文件：{out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index in indices:
        seek = index / info.fps_float if info.fps_float > 0 else 0.0
        with FrameReader(info, conf, seek=seek, count=1) as reader:
            frame = next(iter(reader), None)
        if frame is None:
            log.warn(f"第 {index} 帧取不到（seek {seek:.3f}s），跳过")
            continue
        relit = light.apply(np.ascontiguousarray(frame))
        if params.highlight_clip:
            relit = soft_clip(relit, params.clip_knee, params.clip_max)
        path = out_dir / f"sample_{index:06d}.png"
        write_png(path, relit)
        written.append(path)
        log.info(f"sample: frame {index} -> {path.name}")
    return written


def _relight_video(gt: Path, atmo: Path, out: Path, params: Params, conf,
                   log: Log, *, ref_frame: int | None, sample: int | None,
                   crf: int | None, report: Path | None, started: float) -> None:
    """整段路径：对齐与光照场只估一次，整段逐帧复用；``--sample`` 时只写采样帧。"""
    video_cfg = dict(conf.video, **({"crf": crf} if crf is not None else {}))
    try:
        atmo_image = read_image(atmo)
    except ImageError as exc:
        _runtime_error(str(exc))
    try:
        info = probe(gt, conf)
    except VideoIOError as exc:
        _runtime_error(str(exc))
    _check_video(info.size)
    width, height = info.size
    atmo_h, atmo_w = atmo_image.shape[:2]
    if (atmo_w, atmo_h) != (width, height):
        _param_error(f"尺寸不一致：GT 视频 {width}x{height}，氛围图 {atmo_w}x{atmo_h}")
    if not info.frames:
        log.info("ffprobe 未报告帧数：进度条只有速率，没有 ETA")
    mode = f"sample={sample}" if sample is not None else "整段"
    log.step(f"relight: {width}x{height} {info.frames or '?'} frames @ {info.fps} "
             f"{mode} scene={params.scene} strength={params.strength} "
             f"local-shading={params.local_shading}")

    def open_frames() -> Iterator[np.ndarray]:
        with FrameReader(info, conf) as reader:
            yield from reader

    try:
        fixed, reference = resolve_reference(
            open_frames, atmo_image, width, ref_frame, log,
            check_hold=sample is None)
    except VideoIOError as exc:
        _runtime_error(str(exc))
    _check_alignment(fixed)
    if sample is None and not fixed.static:
        log.warn(f"机位在动：{fixed.frames_checked} 帧里有帧与参考帧的结构重合度只有 "
                 f"{fixed.min_hold:.3f}（下限 {MIN_FRAME_HOLD}）。固定光照场在这类"
                 f"画面上会错位，逐帧重估与时间平滑尚未实现。")
    del reference

    light = light_compose(fixed.fields, (width, height), params.strength,
                          params.local_shading)

    if sample is not None:
        indices = _sample_indices(info.frames, sample)
        if not info.frames:
            log.warn("ffprobe 未报告帧数：小样从片头连续取，不是全片均匀采样")
        written = _write_samples(out, info, conf, light, indices, params, log)
        if not written:
            _runtime_error("小样一帧都没写出来：GT 视频解码不出任何帧")
        base = build_report(
            gt=str(gt), atmo=str(atmo), size=(width, height),
            params=params.as_dict(), align=align_report(fixed.alignment, width),
            clip=None, light=light_report(fixed.fields),
            seconds=time.perf_counter() - started,
        )
        typer.echo(f"wrote {len(written)} sample frames -> {out}")
        _write_report(report, _video_report(
            fixed, base, info.frames, info.fps,
            sample={"count": len(written), "frames": indices}))
        return

    sample_out = None
    meter = RateMeter(info.frames or None, log)
    log.step("render pass")
    try:
        with FrameWriter(out, info.size, info.fps, video_cfg,
                         audio_from=gt, config=conf) as writer:
            with FrameReader(info, conf) as reader:
                for frame in reader:
                    relit = light.apply(np.ascontiguousarray(frame))
                    if writer.frames_written == fixed.ref_frame:
                        sample_out = relit
                    if params.highlight_clip:
                        relit = soft_clip(relit, params.clip_knee, params.clip_max)
                    writer.write(relit)
                    meter.tick()
        written = writer.frames_written
    except VideoIOError as exc:
        Path(out).unlink(missing_ok=True)
        _runtime_error(f"编码失败：{exc}")
    if not written:
        Path(out).unlink(missing_ok=True)
        _runtime_error("GT 视频解码不出任何帧")

    clip_stats = None
    if params.highlight_clip:
        clip_stats = {"knee": params.clip_knee, "max": params.clip_max,
                      "clipped_before": round(clipped_fraction(sample_out), 5)
                      if sample_out is not None else None}
        log.info(f"clip: over-exposure of the reference frame "
                 f"{clip_stats['clipped_before']}")
    meter.done()
    base = build_report(
        gt=str(gt), atmo=str(atmo), size=(width, height),
        params=params.as_dict(), align=align_report(fixed.alignment, width),
        clip=clip_stats, light=light_report(fixed.fields),
        seconds=time.perf_counter() - started,
    )
    typer.echo(f"wrote {out}  ({meter.summary()})")
    _write_report(report, _video_report(fixed, base, written, info.fps))


@app.command()
def relight(
    gt: Path = typer.Option(None, "--gt", help="真 GT：2:1 ERP；视频走整段，图片走单帧"),
    atmo: Path = typer.Option(None, "--atmo", help="AI 氛围图：与 GT 同尺寸、同比例"),
    out: Path = typer.Option(
        None, "--out", help="输出：图片 GT 写 PNG，视频 GT 写 MP4；--sample 时按目录写 PNG"),
    case: str = typer.Option(
        None, "--case",
        help="以 case 为单位：GT 与氛围图取自两个槽位，产物写 case 的 out/ 并回写 [relight]"),
    cases_dir: Path = typer.Option(None, "--cases-dir", help="覆盖 cases 根（测试用）"),
    scene: str = typer.Option("night", "--scene", help="场景预设：night 1.0/0.35，day 0.8/0.2"),
    strength: float | None = typer.Option(
        None, "--strength", help="整体施加量 0..1；缺省取场景预设，0 时输出与 GT 逐位一致"),
    local_shading: float | None = typer.Option(
        None, "--local-shading",
        help="局部明暗权重 0..1；缺省取场景预设，0 只用低频光场，1 完全跟随 AI 的局部明暗"),
    highlight_clip: bool = typer.Option(False, "--highlight-clip", help="高光软裁切"),
    clip_knee: float = typer.Option(0.72, "--clip-knee", help="软裁切拐点（亮度 0..1）"),
    clip_max: float = typer.Option(0.98, "--clip-max", help="软裁切上限（亮度 0..1）"),
    ref_frame: int | None = typer.Option(
        None, "--ref-frame",
        help="仅视频 GT：对齐与估光照场用的帧（0 起）；缺省自动选，指定即首帧"),
    sample: int | None = typer.Option(
        None, "--sample",
        help="仅视频 GT：只渲染 N 帧均匀采样的小样 PNG 到 --out 目录，不编码整段"),
    crf: int | None = typer.Option(
        None, "--crf", min=0, max=51,
        help="仅视频 GT：覆盖 video.crf；0 为无损，像素基线测试用它"),
    dump_light: bool = typer.Option(
        False, "--dump-light", help="仅图片 GT：额外输出四份诊断图"),
    report: Path = typer.Option(None, "--report", help="报告 JSON 路径"),
    config: Path | None = typer.Option(
        None, "--config", help="TOML 配置（只影响视频 GT 的编码）"),
    overrides: list[str] = typer.Option([], "--set", help="配置覆盖 key=value，可重复"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """把 AI 氛围图的光照搬到真 GT 上：图片出单帧，视频出整段（固定机位）。"""
    started = time.perf_counter()
    log = Log(quiet=quiet)
    strength, local_shading = resolve_params(scene, strength, local_shading)
    if highlight_clip and not 0.0 < clip_knee < clip_max <= 1.0:
        _param_error(f"软裁切参数须满足 0 < knee < max <= 1，"
                     f"收到 knee={clip_knee}，max={clip_max}")

    gt, atmo, out, report = _resolve_inputs(gt, atmo, out, case, cases_dir, sample, report)

    try:
        kind = kind_of(gt)
    except CaseError as exc:
        _param_error(str(exc))
    if kind == "image":
        if ref_frame is not None:
            _param_error("--ref-frame 只适用于视频 GT：图片 GT 没有参考帧的概念")
        if sample is not None:
            _param_error("--sample 只适用于视频 GT：图片 GT 本身就只出一帧")
        if crf is not None:
            _param_error("--crf 只适用于视频 GT：图片 GT 不经过视频编码")
    else:
        if dump_light:
            _param_error("--dump-light 只适用于图片 GT：诊断图是单帧概念，"
                         "需要时用 --sample 或对参考帧单独跑图片 GT")
        if sample is not None and sample <= 0:
            _param_error(f"--sample 必须不小于 1，收到 {sample}")

    params = Params(scene=scene, strength=strength, local_shading=local_shading,
                    highlight_clip=highlight_clip, clip_knee=clip_knee,
                    clip_max=clip_max)
    if kind == "image":
        _relight_image(gt, atmo, out, params, dump_light=dump_light,
                       report=report, started=started)
    else:
        _relight_video(gt, atmo, out, params, _conf(config, overrides), log,
                       ref_frame=ref_frame, sample=sample, crf=crf,
                       report=report, started=started)
    if case:
        _record_into_case(case, cases_dir, out, sample is not None)


def _resolve_inputs(gt: Path | None, atmo: Path | None, out: Path | None,
                    case: str | None, cases_dir: Path | None, sample: int | None,
                    report: Path | None) -> tuple[Path, Path, Path, Path | None]:
    """把 ``--case`` 或三个显式路径解析成同一组入参。

    ``--case`` 只从两个槽位取文件，不接受同时给 ``--gt``/``--atmo``：一份文件有两个来源
    会让「case 里记的是哪张」变得含糊。产物位置也由 case 决定，所以 ``--out`` 同样不接受。
    """
    if case is None:
        missing = [flag for flag, value in (("--gt", gt), ("--atmo", atmo),
                                            ("--out", out)) if value is None]
        if missing:
            _param_error(f"没有 --case 时必须给 {'、'.join(missing)}")
        return gt, atmo, out, report
    if gt is not None or atmo is not None or out is not None:
        _param_error("--case 与 --gt/--atmo/--out 只能选一种：以 case 为单位时"
                     "输入取自两个槽位、产物写 case 的 out/")
    try:
        loaded = case_mod.load(case, cases_dir)
    except CaseError as exc:
        _param_error(str(exc))
    if loaded.flow != "relight":
        _param_error(f"case {case} 是 {loaded.flow} 流，不是 relight")
    gt_asset, atmo_asset = loaded.relight_slots()
    if gt_asset is None or atmo_asset is None:
        missing = "底版" if gt_asset is None else "氛围图"
        _param_error(f"case {case} 还缺{missing}：先放好两个槽位"
                     f"（vrstudio case relight --case {case} …）")
    cdir = loaded.dir(cases_dir)
    suffix = ".png" if gt_asset.kind == "image" else ".mp4"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = (cdir / "out" / "samples" / stamp if sample is not None
              else cdir / "out" / f"{stamp}_relit{suffix}")
    return (gt_asset.resolved(loaded, cases_dir),
            atmo_asset.resolved(loaded, cases_dir),
            target,
            report if report is not None else target.with_suffix(".json"))


def _record_into_case(case: str, cases_dir: Path | None, out: Path,
                      is_sample: bool) -> None:
    """把这次运行记进 ``[relight]``：整段更新 current，小样不改指针。"""
    if is_sample:
        return
    loaded = case_mod.load(case, cases_dir)
    try:
        relative = out.relative_to(loaded.dir(cases_dir)).as_posix()
    except ValueError:
        _param_error(f"产物不在 case 目录里，不能回写 [relight] current：{out}")
    try:
        case_mod.set_relight(case, {"current": relative}, cases_dir)
    except CaseError as exc:
        _param_error(str(exc))
    typer.echo(f"case     {case}  [relight] current = {relative}")


if __name__ == "__main__":
    app()
