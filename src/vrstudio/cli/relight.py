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
from contextlib import closing, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from ..relight import cache as relight_cache
from ..relight import regions as regions_mod
from ..relight.align import (
    MAX_REFERENCE_SCAN,
    MIN_REFERENCE_STRUCTURE,
    MIN_STRUCTURE_CORRELATION,
    AlignResult,
    estimate_similarity,
    gray8,
    reference_small,
    structure_correlation,
    structure_score,
    warp_to_gt,
    warp_with_matrix,
    work_size,
)
from ..relight.atmo import AtmoMapping, mapping_for, work_frame, work_size_for
from ..relight.bands import executor, workers_for
from ..relight.compose import (
    CHROMA_SIGMA,
    chroma_lowpass,
    clipped_fraction,
    light_compose,
    soft_clip,
)
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


def _now() -> datetime:
    """当前本地时间（带时区）；报告时间戳与产物文件名同源。"""
    return datetime.now().astimezone()


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


@dataclass(frozen=True)
class FixedGeometry:
    """视频氛围路径的固定几何：逐帧光场只需要相似变换，不需要参考帧的光场。"""

    alignment: AlignResult
    ref_frame: int
    ref_matched: float


@dataclass(frozen=True)
class SetupTiming:
    """固定光照准备各阶段耗时（秒）：选帧、参考帧解码、匹配、光照场。"""

    cache_hit: bool
    scan: float
    read: float
    align: float
    fields: float


@dataclass(frozen=True)
class ResolvedReference:
    """定下的固定光照、是否来自缓存，以及准备阶段的计时。"""

    fixed: FixedLight
    from_cache: bool
    timing: SetupTiming


@dataclass(frozen=True)
class ResolvedGeometry:
    """视频氛围路径：定下的相似变换、是否来自缓存，以及准备阶段的计时。"""

    fixed: FixedGeometry
    from_cache: bool
    timing: SetupTiming


@dataclass(frozen=True)
class RelightInputs:
    """一次 relight 的输入与缓存身份。

    ``cache_file`` 只在 ``--case`` 模式存在；显式 ``--gt/--atmo`` 不缓存。
    ``gt_sha256``/``atmo_sha256`` 可能为 ``None``（手写或旧 case），这时靠
    ``size + mtime_ns`` 区分文件。
    """

    gt: Path
    atmo: Path
    out: Path
    report: Path | None
    regions: Path | None = None
    cache_file: Path | None = None
    gt_sha256: str | None = None
    gt_size: int = 0
    gt_mtime_ns: int = 0
    atmo_sha256: str | None = None
    atmo_size: int = 0
    atmo_mtime_ns: int = 0


def _conf(config: Path | None, overrides: list[str]) -> config_mod.Config:
    try:
        return config_mod.load(config, overrides)
    except (config_mod.ConfigError, OSError, ValueError) as exc:
        _param_error(f"配置错误：{exc}")


def _load_region_list(path: Path | None) -> list[regions_mod.Region]:
    """读区域文件；没有给路径时返回空表（走原标量路径）。"""
    if path is None:
        return []
    try:
        return regions_mod.load_regions(path)
    except regions_mod.RegionError as exc:
        _param_error(str(exc))


def _region_maps(region_list: list[regions_mod.Region], params: Params,
                 work: tuple[int, int]) -> regions_mod.RegionMaps | None:
    """把区域叠加成 2K 参数场；没有区域时返回 ``None``。"""
    if not region_list:
        return None
    clip = (params.clip_knee, params.clip_max) if params.highlight_clip else None
    return regions_mod.build_maps(
        region_list, work, strength=params.strength,
        local_shading=params.local_shading, clip=clip)


def _parse_sample_at(value: str | None) -> list[int] | None:
    """``--sample-at 12,240,600``：去重排序后的帧号表。"""
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        _param_error("--sample-at 至少要给一个帧号")
    try:
        indices = [int(item) for item in items]
    except ValueError:
        _param_error(f"--sample-at 必须是逗号分隔的帧号，收到 {value!r}")
    if any(index < 0 for index in indices):
        _param_error(f"--sample-at 帧号不能为负：{indices}")
    return sorted(set(indices))


def _check_sample_at(indices: list[int], frames: int | None) -> list[int]:
    if frames:
        bad = [index for index in indices if index >= frames]
        if bad:
            _param_error(f"--sample-at 帧号越界：{bad}（共 {frames} 帧，0 起计）")
    return indices


def _apply_clip(frame: np.ndarray, params: Params,
                clip_full: tuple[np.ndarray, np.ndarray] | None, *,
                pool=None, workers: int = 1) -> np.ndarray:
    """区域裁切优先；没有区域裁切时退回全局开关。"""
    if clip_full is not None:
        return soft_clip(frame, clip_full[0], clip_full[1],
                         pool=pool, workers=workers)
    if params.highlight_clip:
        return soft_clip(frame, params.clip_knee, params.clip_max,
                         pool=pool, workers=workers)
    return frame


def _compose_light(fields: LightFields, size: tuple[int, int], params: Params,
                   maps: regions_mod.RegionMaps | None, *,
                   pool=None, workers: int = 1):
    """有区域时用逐像素场，否则用原来的标量参数。"""
    if maps is None:
        return light_compose(fields, size, params.strength, params.local_shading,
                             pool=pool, workers=workers)
    return light_compose(fields, size, maps.strength, maps.local_shading,
                         pool=pool, workers=workers)


def _regions_summary_line(region_list: list[regions_mod.Region],
                          source: Path | None) -> str:
    enabled = sum(1 for region in region_list if region.enabled)
    return f"regions: {len(region_list)} defined, {enabled} enabled, from {source}"


def _regions_report(region_list: list[regions_mod.Region],
                    maps: regions_mod.RegionMaps | None,
                    source: Path | None = None) -> dict | None:
    """报告里的区域摘要：文件身份、数量、逐区域的参数与像素占比。

    把完整的顶点与参数写进报告，是为了事后验收不依赖 regions.json 是否被改过：
    报告自己就能说明这次运行用的是哪一组区域。
    """
    if not region_list:
        return None
    report: dict = {
        "count": len(region_list),
        "enabled": sum(1 for region in region_list if region.enabled),
        "clip": bool(maps is not None and maps.has_clip),
        "regions": [
            {
                "id": region.id,
                "name": region.name,
                "enabled": region.enabled,
                "pole": region.pole,
                "feather_deg": region.feather_deg,
                "verts": [[round(lon, 6), round(lat, 6)] for lon, lat in region.verts],
                "params": {
                    "strength": region.params.strength,
                    "local_shading": region.params.local_shading,
                    "clip": None if region.params.clip is None else {
                        "knee": region.params.clip[0],
                        "max": region.params.clip[1],
                    },
                },
                "coverage": round(maps.coverage.get(region.id, 0.0), 5)
                if maps is not None else 0.0,
            }
            for region in region_list
        ],
    }
    if source is not None:
        report["file"] = str(source)
        try:
            report["sha256"] = case_mod.file_sha256(source)
        except OSError:
            report["sha256"] = None
    return report


def _check_video(size: tuple[int, int]) -> None:
    """GT 视频必须是 2:1 ERP，yuv420p 需要偶数宽高。"""
    width, height = size
    if width != 2 * height:
        _param_error(f"GT 视频必须是 2:1 的 ERP，收到 {width}x{height}")
    if width % 2 or height % 2:
        _param_error(f"yuv420p 需要偶数宽高，收到 {width}x{height}")


def _check_atmo_video(size: tuple[int, int]) -> None:
    """氛围视频只要求 2:1；不要求与 GT 同分辨率，也不参与编码。"""
    width, height = size
    if width != 2 * height:
        _param_error(f"氛围视频必须是 2:1 的 ERP，收到 {width}x{height}")


def _read_atmo_frame(info, conf, index: int) -> np.ndarray | None:
    """按帧号取一帧氛围视频；帧率未知时顺序解码到目标帧。"""
    if index < 0:
        return None
    if info.fps_float > 0:
        with FrameReader(info, conf, seek=index / info.fps_float, count=1) as reader:
            for frame in reader:
                return np.array(frame, copy=True)
        return None
    with FrameReader(info, conf) as reader:
        for current, frame in enumerate(reader):
            if current == index:
                return np.array(frame, copy=True)
    return None


@dataclass
class _BestPair:
    """成对扫描时留下的最佳参考帧：GT 与氛围都留在 2K 工作尺度。"""

    score: float = -np.inf
    index: int = 0
    gt_small: np.ndarray | None = None
    atmo_small: np.ndarray | None = None


class _AtmoTrack:
    """顺序读氛围视频，按映射把 GT 帧号推进到目标帧；短了复用最后一帧。"""

    def __init__(self, mapping: AtmoMapping, work: tuple[int, int]):
        self.mapping = mapping
        self.work = work
        self.index = -1
        self.small: np.ndarray | None = None
        self.short = False

    def small_for(self, frames: Iterator[np.ndarray],
                  gt_index: int) -> np.ndarray | None:
        target = self.mapping.index_for(gt_index)
        while self.index < target:
            try:
                frame = next(frames)
            except StopIteration:
                if self.small is not None and target > self.index:
                    self.short = True
                break
            self.index += 1
            self.small = work_frame(frame, self.work)
        return self.small


def _resolve_pair_reference(open_gt: Callable[..., Iterator[np.ndarray]],
                            open_atmo: Callable[..., Iterator[np.ndarray]],
                            atmo_info, mapping: AtmoMapping,
                            gt_size: tuple[int, int], ref_frame: int | None,
                            log: Log, *, cached: relight_cache.CachedFixed | None = None,
                            conf=None) -> ResolvedGeometry:
    """视频氛围路径的参考帧对：GT 第 N 帧与它映射到的氛围帧。

    对齐仍只估一次；命中缓存时跳过成对扫描与 SIFT。逐帧光场不需要参考帧的光场，
    所以这里只返回相似变换。
    """
    work = work_size_for(*gt_size)
    if cached is not None:
        log.info(f"reference: frame {cached.ref_frame} (cached), "
                 f"structure {cached.ref_matched:.3f}")
        return ResolvedGeometry(
            fixed=FixedGeometry(cached.alignment, cached.ref_frame,
                                cached.ref_matched),
            from_cache=True,
            timing=SetupTiming(cache_hit=True, scan=0.0, read=0.0,
                               align=0.0, fields=0.0))

    setup_start = time.perf_counter()
    if ref_frame is None:
        best = _BestPair()
        scanned = 0
        track = _AtmoTrack(mapping, work)
        # 扫描的生成器在 break 时停在 FrameReader 的 with 里；closing 让它及时退出，
        # 否则那一路 ffmpeg 会挂着（与图片氛围路径同一个原因）。
        with closing(open_gt()) as gt_frames, closing(open_atmo()) as atmo_frames:
            atmo_iter = iter(atmo_frames)
            for index, frame in enumerate(gt_frames):
                scanned += 1
                atmo_small = track.small_for(atmo_iter, index)
                if atmo_small is None:
                    break
                gt_small = work_frame(frame, work)
                gray = gray8(gt_small)
                if float(gray.std()) < MIN_REFERENCE_STRUCTURE:
                    continue
                score = structure_score(gray, gray8(atmo_small))
                if score > best.score:
                    best = _BestPair(score, index, gt_small, atmo_small)
                if index >= MAX_REFERENCE_SCAN:
                    break
        scan = time.perf_counter() - setup_start
        if best.gt_small is None or best.atmo_small is None:
            _runtime_error(f"前 {scanned} 对帧里没有带结构的画面（可能是黑场或空镜）")
        ref_index, gt_small, atmo_small = best.index, best.gt_small, best.atmo_small
        score = best.score
        read = 0.0
        log.info(f"reference: frame {ref_index} -> atmo "
                 f"{mapping.index_for(ref_index)}, structure {score:.3f}")
    else:
        if ref_frame < 0:
            _param_error(f"--ref-frame 必须从 0 起，收到 {ref_frame}")
        read_start = time.perf_counter()
        reference = _read_frame(open_gt, ref_frame)
        if reference is None:
            _param_error(f"--ref-frame {ref_frame} 超出视频帧数")
        atmo_target = mapping.index_for(ref_frame)
        atmo_frame = _read_atmo_frame(atmo_info, conf, atmo_target)
        read = time.perf_counter() - read_start
        if atmo_frame is None:
            _runtime_error(
                f"氛围视频取不到第 {atmo_target} 帧（GT 参考帧 {ref_frame} 的对应帧）")
        gt_small = work_frame(reference, work)
        atmo_small = work_frame(atmo_frame, work)
        ref_index = ref_frame
        score = structure_score(gray8(gt_small), gray8(atmo_small))
        log.info(f"reference: frame {ref_index} -> atmo {atmo_target}, "
                 f"structure {score:.3f}")
        scan = 0.0

    if score < MIN_STRUCTURE_CORRELATION:
        # 与图片氛围路径同一条理由：未对齐的带通相关只作提示，真正的闸门是
        # ``_check_alignment`` 的对齐后重合度。
        log.warn(f"参考帧对未对齐时的结构重合度只有 {score:.3f}；"
                 f"按对齐后的重合度判定。")

    align_start = time.perf_counter()
    alignment = estimate_similarity(gt_small, atmo_small)
    align = time.perf_counter() - align_start
    return ResolvedGeometry(
        fixed=FixedGeometry(alignment, ref_index, score),
        from_cache=False,
        timing=SetupTiming(cache_hit=False, scan=scan, read=read,
                           align=align, fields=0.0))


@dataclass
class _BestReference:
    """扫描时留下的最佳帧：2K 小图用于比较，原分辨率图用于估光照场。"""

    score: float = -np.inf
    index: int = 0
    small: np.ndarray | None = None
    frame: np.ndarray | None = None


def _read_frame(open_frames: Callable[..., Iterator[np.ndarray]],
                index: int) -> np.ndarray | None:
    """按帧号取一帧原分辨率图；取不到返回 ``None``。

    显式参考帧与缓存命中的参考帧都只解这一帧，不再从片头顺序解码。
    """
    with closing(open_frames(index=index)) as frames:
        for frame in frames:
            return np.array(frame, copy=True)
    return None


def resolve_reference(open_frames: Callable[..., Iterator[np.ndarray]], atmo,
                      width: int, ref_frame: int | None, log: Log,
                      *, cached: relight_cache.CachedFixed | None = None
                      ) -> ResolvedReference:
    """挑参考帧、定变换与光照场；命中缓存时跳过选帧扫描与 SIFT。

    ``open_frames`` 每次调用打开一遍视频、返回逐帧迭代器；传 ``index`` 时按帧号
    定位只取一帧。参考帧有两条来源：``ref_frame`` 显式给出（0 起，不合法是参数错），
    或扫前 ``MAX_REFERENCE_SCAN`` 帧在 2K 上选与氛围图最相关的一帧。扫描时保留
    最佳帧的原分辨率图，光照场直接用它，避免再从片头解码到参考帧。

    ``cached`` 命中时不再扫描、不做 SIFT，只按缓存的参考帧取一帧并重算光照场；
    返回值里的 ``from_cache`` 供调用方决定是否写回，``timing`` 是各阶段耗时。
    """
    if cached is not None:
        read_start = time.perf_counter()
        reference = _read_frame(open_frames, cached.ref_frame)
        read = time.perf_counter() - read_start
        if reference is not None:
            log.info(f"reference: frame {cached.ref_frame} (cached), "
                     f"structure {cached.ref_matched:.3f}")
            fields_start = time.perf_counter()
            fields = estimate_fields(reference, warp_to_gt(atmo, cached.alignment))
            fields_s = time.perf_counter() - fields_start
            return ResolvedReference(
                fixed=FixedLight(alignment=cached.alignment, fields=fields,
                                 ref_frame=cached.ref_frame,
                                 ref_matched=cached.ref_matched),
                from_cache=True,
                timing=SetupTiming(cache_hit=True, scan=0.0, read=read,
                                   align=0.0, fields=fields_s))
        log.warn(f"缓存的参考帧 {cached.ref_frame} 在视频里取不到；重新选帧。")

    setup_start = time.perf_counter()
    atmo_gray = gray8(reference_small(atmo, width)).astype(np.float32)
    read = 0.0
    if ref_frame is None:
        # 扫描在 90 帧就 break，生成器不会被消费到底；不显式 close 的话它停在
        # ``with FrameReader`` 里面，那一路 ffmpeg 会一直挂着并占住 4–5 GB（8K 实测）。
        with closing(open_frames()) as frames:
            best = _BestReference()
            scanned = 0
            for index, frame in enumerate(frames):
                scanned += 1
                small = reference_small(frame, width)
                gray = gray8(small)
                if float(gray.std()) < MIN_REFERENCE_STRUCTURE:
                    continue                      # 黑场/空镜：几乎没有结构可选
                score = structure_score(gray, atmo_gray)
                if score > best.score:
                    # 只留最佳帧的原分辨率图；它替换旧的最佳帧，峰值不多出一帧。
                    best = _BestReference(score, index, small,
                                          np.array(frame, copy=True))
                if index >= MAX_REFERENCE_SCAN:
                    break
        if best.frame is None or best.small is None:
            _runtime_error(f"前 {scanned} 帧里没有带结构的画面（可能是黑场或空镜）")
        score, ref_index = best.score, best.index
        ref_small, reference = best.small, best.frame
        log.info(f"reference: frame {ref_index}, structure {score:.3f}")
    else:
        if ref_frame < 0:
            _param_error(f"--ref-frame 必须从 0 起，收到 {ref_frame}")
        read_start = time.perf_counter()
        reference = _read_frame(open_frames, ref_frame)
        read = time.perf_counter() - read_start
        if reference is None:
            _param_error(f"--ref-frame {ref_frame} 超出视频帧数")
        ref_index = ref_frame
        ref_small = reference_small(reference, width)
        score = structure_score(gray8(ref_small), atmo_gray)
        log.info(f"reference: frame {ref_index}, structure {score:.3f}")
    scan = max(0.0, time.perf_counter() - setup_start - read)
    if score < MIN_STRUCTURE_CORRELATION:
        # 只提示，不在这里中断：这一项是「未对齐」的带通相关，几像素的平移就能把它
        # 压到 0（实测拼接后的氛围图有 −9 px 位移，相关从 0.55 掉到 −0.01），而
        # 对齐之后本来就可能对得上。真正的闸门是 ``_check_alignment`` 的
        # ``correlation_after``——决策 48 的「判据只有一条」也是这个意思。
        log.warn(f"参考帧与氛围图未对齐时的结构重合度只有 {score:.3f}；"
                 f"按对齐后的重合度判定。")

    align_start = time.perf_counter()
    alignment = estimate_similarity(ref_small, atmo)
    align = time.perf_counter() - align_start
    fields_start = time.perf_counter()
    fields = estimate_fields(reference, warp_to_gt(atmo, alignment))
    fields_s = time.perf_counter() - fields_start
    return ResolvedReference(
        fixed=FixedLight(alignment=alignment, fields=fields, ref_frame=ref_index,
                         ref_matched=score),
        from_cache=False,
        timing=SetupTiming(cache_hit=False, scan=scan, read=read,
                           align=align, fields=fields_s))


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


def _video_report(fixed: FixedLight | FixedGeometry, base: dict, frames: int,
                  fps: str, *, kind: str, started_at: datetime,
                  finished_at: datetime, cache: dict, timing: dict,
                  sample: dict | None = None,
                  atmo_source: dict | None = None) -> dict:
    """视频版报告：``clip``/``light`` 与图像版同构，新增帧数、帧率、参考帧与计时。

    ``sample`` 只在 ``--sample`` 时出现，记录小样的采样帧号。``kind``/``started_at``/
    ``finished_at``/``cache``/``timing`` 是决策 65 的运行记录：前端重点显示时间戳，
    分段耗时作为次要信息。``atmo_source`` 记录氛围依据的类型与光场模式：图片氛围是
    固定光场，视频氛围是逐帧光场并带映射与逐帧结构相关。
    """
    summary = {
        "gt": base["gt"], "atmo": base["atmo"], "size": base["size"],
        "frames": frames, "fps": fps,
        "kind": kind,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "cache": cache, "timing": timing,
        "ref_frame": fixed.ref_frame, "ref_matched": round(fixed.ref_matched, 4),
        "params": base["params"], "align": base["align"],
        "clip": base["clip"], "light": base["light"], "chroma": base["chroma"],
        "regions": base["regions"],
        "seconds": base["seconds"],
    }
    if atmo_source is not None:
        summary.update(atmo_source)
    if sample is not None:
        summary["sample"] = sample
    return summary


def _check_alignment(fixed: FixedLight | FixedGeometry) -> None:
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
                   dump_light: bool, report: Path | None, started: float,
                   region_list: list[regions_mod.Region] | None = None,
                   regions_source: Path | None = None) -> None:
    """单帧路径：读两张图、估一次变换与光照场、乘一次光场、写一张图。"""
    try:
        gt_image = read_image(gt)
        atmo_image = read_image(atmo)
    except ImageError as exc:
        _runtime_error(str(exc))
    check_inputs(gt_image, atmo_image)
    height, width = gt_image.shape[:2]
    maps = _region_maps(region_list or [], params, work_size(width, height))
    if region_list:
        typer.echo(_regions_summary_line(region_list, regions_source))
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
    if maps is None:
        out_image = compose_relight(gt_image, fields, params.strength,
                                    params.local_shading)
        clip_full = None
    else:
        out_image = _compose_light(fields, (width, height), params, maps).apply(gt_image)
        clip_full = regions_mod.upscale_clip(maps, (width, height))
    clipped_before = clipped_fraction(out_image)
    clip_stats = None
    if clip_full is not None or params.highlight_clip:
        out_image = _apply_clip(out_image, params, clip_full)
        clip_stats = {
            "knee": params.clip_knee,
            "max": params.clip_max,
            "clipped_before": round(clipped_before, 5),
            "clipped_after": round(clipped_fraction(out_image), 5),
        }
        if clip_full is not None:
            clip_stats["regional"] = True
        typer.echo(f"clip: over-exposure {clip_stats['clipped_before']:.3%} "
                   f"-> {clip_stats['clipped_after']:.3%}")

    out_image = chroma_lowpass(out_image, gt_image)

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
        chroma={"sigma": CHROMA_SIGMA},
        regions=_regions_report(region_list or [], maps, regions_source),
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


@dataclass(frozen=True)
class SampleOutput:
    """小样输出与各段累计耗时（秒）：GT/氛围解码、合成、写图。"""

    paths: list[Path]
    decode: float
    compose: float
    write: float
    atmo_decode: float = 0.0
    match: dict | None = None


def _write_samples(out_dir: Path, info, conf, light, indices: list[int],
                   params: Params, log: Log,
                   clip_full: tuple[np.ndarray, np.ndarray] | None = None) -> SampleOutput:
    """按时间点 seek 逐帧取图：只解码被采样的那几帧，不整段解码。"""
    if out_dir.exists() and not out_dir.is_dir():
        _param_error(f"--sample 要求 --out 是目录，但它是文件：{out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    decode = compose = write = 0.0
    workers = workers_for()
    with (executor(workers) or nullcontext()) as pool:
        for index in indices:
            seek = index / info.fps_float if info.fps_float > 0 else 0.0
            decode_start = time.perf_counter()
            with FrameReader(info, conf, seek=seek, count=1) as reader:
                frame = next(iter(reader), None)
            decode += time.perf_counter() - decode_start
            if frame is None:
                log.warn(f"第 {index} 帧取不到（seek {seek:.3f}s），跳过")
                continue
            compose_start = time.perf_counter()
            relit = light.apply(np.ascontiguousarray(frame), pool=pool, workers=workers)
            relit = _apply_clip(relit, params, clip_full, pool=pool, workers=workers)
            relit = chroma_lowpass(relit, frame, pool=pool, workers=workers)
            compose += time.perf_counter() - compose_start
            path = out_dir / f"sample_{index:06d}.png"
            write_start = time.perf_counter()
            write_png(path, relit)
            write += time.perf_counter() - write_start
            written.append(path)
            log.info(f"sample: frame {index} -> {path.name}")
    return SampleOutput(paths=written, decode=decode, compose=compose, write=write)


def _frame_opener(info, conf) -> Callable[..., Iterator[np.ndarray]]:
    """打开视频；传 ``index`` 时按帧号定位，只取一帧。"""
    def open_frames(index: int | None = None) -> Iterator[np.ndarray]:
        if index is None:
            with FrameReader(info, conf) as reader:
                yield from reader
            return
        if info.fps_float > 0:
            with FrameReader(info, conf, seek=index / info.fps_float,
                             count=1) as reader:
                yield from reader
            return
        with FrameReader(info, conf) as reader:
            for current, frame in enumerate(reader):
                if current == index:
                    yield frame
                    return

    return open_frames


def _relight_video(inputs: RelightInputs, params: Params, conf,
                   log: Log, *, ref_frame: int | None, sample: int | None,
                   crf: int | None, started: float,
                   started_at: datetime,
                   sample_at: list[int] | None = None,
                   region_list: list[regions_mod.Region] | None = None,
                   regions_source: Path | None = None) -> None:
    """整段路径：对齐与光照场只估一次，整段逐帧复用；``--sample`` 时只写采样帧。"""
    video_cfg = dict(conf.video, **({"crf": crf} if crf is not None else {}))
    try:
        atmo_image = read_image(inputs.atmo)
    except ImageError as exc:
        _runtime_error(str(exc))
    try:
        info = probe(inputs.gt, conf)
    except VideoIOError as exc:
        _runtime_error(str(exc))
    _check_video(info.size)
    width, height = info.size
    maps = _region_maps(region_list or [], params, work_size(width, height))
    if region_list:
        log.info(_regions_summary_line(region_list, regions_source))
    atmo_h, atmo_w = atmo_image.shape[:2]
    if (atmo_w, atmo_h) != (width, height):
        _param_error(f"尺寸不一致：GT 视频 {width}x{height}，氛围图 {atmo_w}x{atmo_h}")
    if not info.frames:
        log.info("ffprobe 未报告帧数：进度条只有速率，没有 ETA")
    mode = (f"sample-at={sample_at}" if sample_at is not None
            else (f"sample={sample}" if sample is not None else "整段"))
    log.step(f"relight: {width}x{height} {info.frames or '?'} frames @ {info.fps} "
             f"{mode} scene={params.scene} strength={params.strength} "
             f"local-shading={params.local_shading}")

    open_frames = _frame_opener(info, conf)

    cached = None
    cache_key = None
    cache_load = 0.0
    if inputs.cache_file is not None:
        cache_key = relight_cache.CacheKey(
            gt_sha256=inputs.gt_sha256, gt_size=inputs.gt_size,
            gt_mtime_ns=inputs.gt_mtime_ns,
            atmo_sha256=inputs.atmo_sha256, atmo_size=inputs.atmo_size,
            atmo_mtime_ns=inputs.atmo_mtime_ns,
            ref_frame="auto" if ref_frame is None else ref_frame,
        )
        load_start = time.perf_counter()
        cached = relight_cache.load(inputs.cache_file, cache_key)
        cache_load = time.perf_counter() - load_start
        if cached is not None:
            log.info(f"fixed: cache hit, reference frame {cached.ref_frame}")
        else:
            log.info("fixed: cache miss; scanning reference frame")

    try:
        resolved = resolve_reference(
            open_frames, atmo_image, width, ref_frame, log, cached=cached)
    except VideoIOError as exc:
        _runtime_error(str(exc))
    fixed = resolved.fixed
    _check_alignment(fixed)
    if (inputs.cache_file is not None and cache_key is not None
            and not resolved.from_cache):
        saved = relight_cache.save(
            inputs.cache_file, cache_key,
            relight_cache.CachedFixed(ref_frame=fixed.ref_frame,
                                      ref_matched=fixed.ref_matched,
                                      alignment=fixed.alignment))
        if not saved:
            log.warn(f"fixed: 缓存写入失败，下次仍会重算：{inputs.cache_file}")

    cache = {"enabled": inputs.cache_file is not None,
             "hit": resolved.from_cache, "load": round(cache_load, 3)}
    setup_timing = {
        "scan": round(resolved.timing.scan, 3),
        "read": round(resolved.timing.read, 3),
        "align": round(resolved.timing.align, 3),
        "fields": round(resolved.timing.fields, 3),
    }

    light = _compose_light(fixed.fields, (width, height), params, maps)
    clip_full = regions_mod.upscale_clip(maps, (width, height)) if maps else None
    clip_active = clip_full is not None or params.highlight_clip

    if sample is not None or sample_at is not None:
        if sample_at is not None:
            indices = _check_sample_at(sample_at, info.frames)
        else:
            indices = _sample_indices(info.frames, sample)
            if not info.frames:
                log.warn("ffprobe 未报告帧数：小样从片头连续取，不是全片均匀采样")
        samples = _write_samples(inputs.out, info, conf, light, indices, params, log,
                                 clip_full)
        written = samples.paths
        if not written:
            _runtime_error("小样一帧都没写出来：GT 视频解码不出任何帧")
        total = time.perf_counter() - started
        base = build_report(
            gt=str(inputs.gt), atmo=str(inputs.atmo), size=(width, height),
            params=params.as_dict(), align=align_report(fixed.alignment, width),
            clip=None, light=light_report(fixed.fields),
            chroma={"sigma": CHROMA_SIGMA},
            regions=_regions_report(region_list or [], maps, regions_source),
            seconds=total,
        )
        timing = {
            "cache_load": round(cache_load, 3),
            **setup_timing,
            "sample_decode": round(samples.decode, 3),
            "sample_compose": round(samples.compose, 3),
            "sample_write": round(samples.write, 3),
            "total": base["seconds"],
        }
        log.info("timing: " + ", ".join(f"{key}={value:.2f}s"
                                        for key, value in timing.items()))
        typer.echo(f"wrote {len(written)} sample frames -> {inputs.out}")
        _write_report(inputs.report, _video_report(
            fixed, base, info.frames, info.fps,
            kind="sample", started_at=started_at,
            finished_at=started_at + timedelta(seconds=total),
            cache=cache, timing=timing,
            sample={"count": len(written), "frames": indices},
            atmo_source={"atmo_kind": "image", "fields_mode": "fixed"}))
        return

    clip_before = None
    meter = RateMeter(info.frames or None, log)
    log.step("render pass")
    render_decode = render_compose = render_write = render_chroma = 0.0
    render_start = time.perf_counter()
    loop_end = render_start
    workers = workers_for()
    pool = executor(workers)
    try:
        with FrameWriter(inputs.out, info.size, info.fps, video_cfg,
                         audio_from=inputs.gt, config=conf) as writer:
            with FrameReader(info, conf) as reader:
                reader_iter = iter(reader)
                while True:
                    decode_start = time.perf_counter()
                    try:
                        frame = next(reader_iter)
                    except StopIteration:
                        break
                    render_decode += time.perf_counter() - decode_start
                    compose_start = time.perf_counter()
                    relit = light.apply(np.ascontiguousarray(frame),
                                        pool=pool, workers=workers)
                    # 小样统计在参考帧那一刻算完，只留一个浮点数，不跨整段持有 8K 帧。
                    if clip_active and writer.frames_written == fixed.ref_frame:
                        clip_before = clipped_fraction(relit)
                    relit = _apply_clip(relit, params, clip_full,
                                        pool=pool, workers=workers)
                    chroma_start = time.perf_counter()
                    relit = chroma_lowpass(relit, frame, pool=pool, workers=workers)
                    render_chroma += time.perf_counter() - chroma_start
                    render_compose += time.perf_counter() - compose_start
                    write_start = time.perf_counter()
                    writer.write(relit)
                    render_write += time.perf_counter() - write_start
                    meter.tick()
            loop_end = time.perf_counter()
        render_flush = time.perf_counter() - loop_end
        render_total = time.perf_counter() - render_start
        written = writer.frames_written
    except VideoIOError as exc:
        Path(inputs.out).unlink(missing_ok=True)
        _runtime_error(f"编码失败：{exc}")
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
    if not written:
        Path(inputs.out).unlink(missing_ok=True)
        _runtime_error("GT 视频解码不出任何帧")

    clip_stats = None
    if clip_active:
        clip_stats = {"knee": params.clip_knee, "max": params.clip_max,
                      "clipped_before": round(clip_before, 5)
                      if clip_before is not None else None}
        if clip_full is not None:
            clip_stats["regional"] = True
        log.info(f"clip: over-exposure of the reference frame "
                 f"{clip_stats['clipped_before']}")
    meter.done()
    total = time.perf_counter() - started
    base = build_report(
        gt=str(inputs.gt), atmo=str(inputs.atmo), size=(width, height),
        params=params.as_dict(), align=align_report(fixed.alignment, width),
        clip=clip_stats, light=light_report(fixed.fields),
        chroma={"sigma": CHROMA_SIGMA},
        regions=_regions_report(region_list or [], maps, regions_source),
        seconds=total,
    )
    timing = {
        "cache_load": round(cache_load, 3),
        **setup_timing,
        "render_decode": round(render_decode, 3),
        "render_compose": round(render_compose, 3),
        "render_chroma": round(render_chroma, 3),
        "render_write": round(render_write, 3),
        "render_flush": round(render_flush, 3),
        "render_total": round(render_total, 3),
        "total": base["seconds"],
    }
    log.info("timing: " + ", ".join(f"{key}={value:.2f}s"
                                    for key, value in timing.items()))
    typer.echo(f"wrote {inputs.out}  ({meter.summary()})")
    _write_report(inputs.report, _video_report(
        fixed, base, written, info.fps,
        kind="full", started_at=started_at,
        finished_at=started_at + timedelta(seconds=total),
        cache=cache, timing=timing,
        atmo_source={"atmo_kind": "image", "fields_mode": "fixed"}))


@dataclass(frozen=True)
class PairRender:
    """视频氛围整段渲染的结果：帧数、裁切统计、计时、逐帧结构相关。"""

    frames: int
    clip: dict | None
    timing: dict
    match: dict | None
    short: bool


def _match_stats(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"min": round(float(np.min(values)), 4),
            "mean": round(float(np.mean(values)), 4),
            "frames": len(values)}


def _compose_pair(gt_frame: np.ndarray, atmo_small: np.ndarray,
                  alignment: AlignResult, params: Params,
                  work: tuple[int, int],
                  size: tuple[int, int], *,
                  pool=None, workers: int = 1,
                  maps: regions_mod.RegionMaps | None = None) -> tuple[np.ndarray, float]:
    """一帧 GT 与对应氛围帧：2K 上估光场、原尺寸乘算。

    ``strength=0`` 时不估光场，直接返回 GT 的副本；这与 ``light_compose`` 的
    单位变换等价，但省掉每帧一次的引导滤波与 8K 光场放大。有区域时只有在场的
    施加量全为 0 时才走这条捷径。
    """
    gt_small = work_frame(gt_frame, work)
    aligned = warp_with_matrix(atmo_small, alignment.matrix, work)
    match = structure_correlation(gray8(gt_small), gray8(aligned))
    if params.strength == 0.0 and (maps is None or not maps.strength.any()):
        return np.ascontiguousarray(gt_frame), match
    fields = estimate_fields(gt_small, aligned, pool=pool)
    light = _compose_light(fields, size, params, maps, pool=pool, workers=workers)
    return light.apply(np.ascontiguousarray(gt_frame),
                       pool=pool, workers=workers), match


def _write_samples_pair(out_dir: Path, gt_info, atmo_info, conf,
                        fixed: FixedGeometry, mapping: AtmoMapping,
                        params: Params, indices: list[int], log: Log,
                        work: tuple[int, int],
                        maps: regions_mod.RegionMaps | None = None,
                        clip_full: tuple[np.ndarray, np.ndarray] | None = None,
                        ) -> SampleOutput:
    """视频氛围的小样：每个采样点同时取 GT 帧与映射到的氛围帧。"""
    if out_dir.exists() and not out_dir.is_dir():
        _param_error(f"--sample 要求 --out 是目录，但它是文件：{out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    matches: list[float] = []
    decode = atmo_decode = compose = write = 0.0
    open_gt = _frame_opener(gt_info, conf)
    workers = workers_for()
    with (executor(workers) or nullcontext()) as pool:
        for index in indices:
            decode_start = time.perf_counter()
            frame = _read_frame(open_gt, index)
            decode += time.perf_counter() - decode_start
            if frame is None:
                log.warn(f"GT 第 {index} 帧取不到，跳过")
                continue
            atmo_start = time.perf_counter()
            atmo_frame = _read_atmo_frame(atmo_info, conf, mapping.index_for(index))
            atmo_decode += time.perf_counter() - atmo_start
            if atmo_frame is None:
                log.warn(f"氛围第 {mapping.index_for(index)} 帧取不到，跳过 GT 第 {index} 帧")
                continue
            atmo_small = work_frame(atmo_frame, work)
            compose_start = time.perf_counter()
            relit, match = _compose_pair(frame, atmo_small, fixed.alignment,
                                         params, work, gt_info.size,
                                         pool=pool, workers=workers, maps=maps)
            relit = _apply_clip(relit, params, clip_full,
                                pool=pool, workers=workers)
            relit = chroma_lowpass(relit, frame, pool=pool, workers=workers)
            compose += time.perf_counter() - compose_start
            matches.append(match)
            path = out_dir / f"sample_{index:06d}.png"
            write_start = time.perf_counter()
            write_png(path, relit)
            write += time.perf_counter() - write_start
            written.append(path)
            log.info(f"sample: frame {index} -> atmo {mapping.index_for(index)} "
                     f"-> {path.name}")
    return SampleOutput(paths=written, decode=decode, compose=compose,
                        write=write, atmo_decode=atmo_decode,
                        match=_match_stats(matches))


def _render_pair(inputs: RelightInputs, gt_info, atmo_info,
                 fixed: FixedGeometry, mapping: AtmoMapping, params: Params,
                 conf, log: Log, video_cfg: dict,
                 work: tuple[int, int],
                 maps: regions_mod.RegionMaps | None = None,
                 clip_full: tuple[np.ndarray, np.ndarray] | None = None
                 ) -> PairRender:
    """整段渲染：两路顺序读，每帧取对应氛围帧、估光场、乘算、编码。"""
    meter = RateMeter(gt_info.frames or None, log)
    log.step("render pass")
    render_decode = render_atmo_decode = render_compose = render_write = 0.0
    render_chroma = 0.0
    matches: list[float] = []
    clip_before: float | None = None
    track = _AtmoTrack(mapping, work)
    clip_active = clip_full is not None or params.highlight_clip
    workers = workers_for()
    pool = executor(workers)
    render_start = time.perf_counter()
    loop_end = render_start
    try:
        with FrameWriter(inputs.out, gt_info.size, gt_info.fps, video_cfg,
                         audio_from=inputs.gt, config=conf) as writer:
            with FrameReader(gt_info, conf) as gt_reader, \
                    FrameReader(atmo_info, conf) as atmo_reader:
                gt_iter = iter(gt_reader)
                atmo_iter = iter(atmo_reader)
                index = -1
                while True:
                    decode_start = time.perf_counter()
                    try:
                        frame = next(gt_iter)
                    except StopIteration:
                        break
                    index += 1
                    render_decode += time.perf_counter() - decode_start
                    atmo_start = time.perf_counter()
                    atmo_small = track.small_for(atmo_iter, index)
                    render_atmo_decode += time.perf_counter() - atmo_start
                    if atmo_small is None:
                        raise VideoIOError("氛围视频解码不出任何帧")
                    compose_start = time.perf_counter()
                    relit, match = _compose_pair(frame, atmo_small,
                                                 fixed.alignment, params,
                                                 work, gt_info.size,
                                                 pool=pool, workers=workers,
                                                 maps=maps)
                    matches.append(match)
                    if clip_active and index == fixed.ref_frame:
                        clip_before = clipped_fraction(relit)
                    relit = _apply_clip(relit, params, clip_full,
                                        pool=pool, workers=workers)
                    chroma_start = time.perf_counter()
                    relit = chroma_lowpass(relit, frame, pool=pool, workers=workers)
                    render_chroma += time.perf_counter() - chroma_start
                    render_compose += time.perf_counter() - compose_start
                    write_start = time.perf_counter()
                    writer.write(relit)
                    render_write += time.perf_counter() - write_start
                    meter.tick()
            loop_end = time.perf_counter()
        render_flush = time.perf_counter() - loop_end
        render_total = time.perf_counter() - render_start
        written = writer.frames_written
    except VideoIOError as exc:
        Path(inputs.out).unlink(missing_ok=True)
        _runtime_error(f"编解码失败：{exc}")
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
    if not written:
        Path(inputs.out).unlink(missing_ok=True)
        _runtime_error("GT 视频解码不出任何帧")

    clip_stats = None
    if clip_active:
        clip_stats = {"knee": params.clip_knee, "max": params.clip_max,
                      "clipped_before": round(clip_before, 5)
                      if clip_before is not None else None}
        if clip_full is not None:
            clip_stats["regional"] = True
        log.info(f"clip: over-exposure of the reference frame "
                 f"{clip_stats['clipped_before']}")
    meter.done()
    timing = {
        "render_decode": round(render_decode, 3),
        "render_decode_atmo": round(render_atmo_decode, 3),
        "render_compose": round(render_compose, 3),
        "render_chroma": round(render_chroma, 3),
        "render_write": round(render_write, 3),
        "render_flush": round(render_flush, 3),
        "render_total": round(render_total, 3),
    }
    return PairRender(frames=written, clip=clip_stats, timing=timing,
                      match=_match_stats(matches), short=track.short)


def _relight_video_atmo(inputs: RelightInputs, params: Params, conf,
                        log: Log, *, ref_frame: int | None, sample: int | None,
                        crf: int | None, started: float,
                        started_at: datetime,
                        sample_at: list[int] | None = None,
                        region_list: list[regions_mod.Region] | None = None,
                        regions_source: Path | None = None) -> None:
    """视频 GT + 视频氛围：固定相似变换，逐帧从对应氛围帧重估光场。"""
    video_cfg = dict(conf.video, **({"crf": crf} if crf is not None else {}))
    try:
        gt_info = probe(inputs.gt, conf)
        atmo_info = probe(inputs.atmo, conf)
    except VideoIOError as exc:
        _runtime_error(str(exc))
    _check_video(gt_info.size)
    _check_atmo_video(atmo_info.size)
    width, height = gt_info.size
    mapping = mapping_for(gt_info.frames, gt_info.fps_float,
                          atmo_info.frames, atmo_info.fps_float)
    work = work_size_for(width, height)
    maps = _region_maps(region_list or [], params, work)
    if region_list:
        log.info(_regions_summary_line(region_list, regions_source))
    if not gt_info.frames:
        log.info("GT ffprobe 未报告帧数：进度条只有速率，没有 ETA")
    mode = (f"sample-at={sample_at}" if sample_at is not None
            else (f"sample={sample}" if sample is not None else "整段"))
    log.step(f"relight: {width}x{height} {gt_info.frames or '?'} frames @ {gt_info.fps} "
             f"{mode} scene={params.scene} strength={params.strength} "
             f"local-shading={params.local_shading} atmo=video "
             f"{atmo_info.size[0]}x{atmo_info.size[1]} "
             f"{atmo_info.frames or '?'} frames @ {atmo_info.fps} "
             f"mapping={mapping.mode}")

    open_gt = _frame_opener(gt_info, conf)

    def open_atmo(index: int | None = None) -> Iterator[np.ndarray]:
        yield from _frame_opener(atmo_info, conf)(index)

    cached = None
    cache_key = None
    cache_load = 0.0
    if inputs.cache_file is not None:
        cache_key = relight_cache.CacheKey(
            gt_sha256=inputs.gt_sha256, gt_size=inputs.gt_size,
            gt_mtime_ns=inputs.gt_mtime_ns,
            atmo_sha256=inputs.atmo_sha256, atmo_size=inputs.atmo_size,
            atmo_mtime_ns=inputs.atmo_mtime_ns,
            ref_frame="auto" if ref_frame is None else ref_frame,
        )
        load_start = time.perf_counter()
        cached = relight_cache.load(inputs.cache_file, cache_key)
        cache_load = time.perf_counter() - load_start
        if cached is not None:
            log.info(f"fixed: cache hit, reference frame {cached.ref_frame}")
        else:
            log.info("fixed: cache miss; scanning paired reference frame")

    try:
        resolved = _resolve_pair_reference(
            open_gt, open_atmo, atmo_info, mapping, (width, height),
            ref_frame, log, cached=cached, conf=conf)
    except VideoIOError as exc:
        _runtime_error(str(exc))
    fixed = resolved.fixed
    _check_alignment(fixed)
    if (inputs.cache_file is not None and cache_key is not None
            and not resolved.from_cache):
        saved = relight_cache.save(
            inputs.cache_file, cache_key,
            relight_cache.CachedFixed(ref_frame=fixed.ref_frame,
                                      ref_matched=fixed.ref_matched,
                                      alignment=fixed.alignment))
        if not saved:
            log.warn(f"fixed: 缓存写入失败，下次仍会重算：{inputs.cache_file}")

    cache = {"enabled": inputs.cache_file is not None,
             "hit": resolved.from_cache, "load": round(cache_load, 3)}
    setup_timing = {
        "scan": round(resolved.timing.scan, 3),
        "read": round(resolved.timing.read, 3),
        "align": round(resolved.timing.align, 3),
        "fields": round(resolved.timing.fields, 3),
    }
    atmo_source = {
        "atmo_kind": "video",
        "fields_mode": "per_frame",
        "atmo_frames": atmo_info.frames,
        "atmo_fps": atmo_info.fps,
        "atmo_mapping": mapping.mode,
        "atmo_ref_frame": mapping.index_for(fixed.ref_frame),
    }

    if sample is not None or sample_at is not None:
        if sample_at is not None:
            indices = _check_sample_at(sample_at, gt_info.frames)
        else:
            indices = _sample_indices(gt_info.frames, sample)
            if not gt_info.frames:
                log.warn("GT ffprobe 未报告帧数：小样从片头连续取，不是全片均匀采样")
        clip_full = regions_mod.upscale_clip(maps, (width, height)) if maps else None
        samples = _write_samples_pair(
            inputs.out, gt_info, atmo_info, conf, fixed, mapping, params,
            indices, log, work, maps, clip_full)
        written = samples.paths
        if not written:
            _runtime_error("小样一帧都没写出来：GT 或氛围视频解码不出任何帧")
        total = time.perf_counter() - started
        base = build_report(
            gt=str(inputs.gt), atmo=str(inputs.atmo), size=(width, height),
            params=params.as_dict(), align=align_report(fixed.alignment, width),
            clip=None, light=None,
            chroma={"sigma": CHROMA_SIGMA}, seconds=total,
            regions=_regions_report(region_list or [], maps, regions_source),
        )
        timing = {
            "cache_load": round(cache_load, 3),
            **setup_timing,
            "sample_decode": round(samples.decode, 3),
            "sample_decode_atmo": round(samples.atmo_decode, 3),
            "sample_compose": round(samples.compose, 3),
            "sample_write": round(samples.write, 3),
            "total": base["seconds"],
        }
        log.info("timing: " + ", ".join(f"{key}={value:.2f}s"
                                        for key, value in timing.items()))
        typer.echo(f"wrote {len(written)} sample frames -> {inputs.out}")
        _write_report(inputs.report, _video_report(
            fixed, base, gt_info.frames, gt_info.fps,
            kind="sample", started_at=started_at,
            finished_at=started_at + timedelta(seconds=total),
            cache=cache, timing=timing,
            sample={"count": len(written), "frames": indices},
            atmo_source={**atmo_source, "atmo_match": samples.match}))
        return

    clip_full = regions_mod.upscale_clip(maps, (width, height)) if maps else None
    render = _render_pair(inputs, gt_info, atmo_info, fixed, mapping, params,
                          conf, log, video_cfg, work, maps, clip_full)
    total = time.perf_counter() - started
    base = build_report(
        gt=str(inputs.gt), atmo=str(inputs.atmo), size=(width, height),
        params=params.as_dict(), align=align_report(fixed.alignment, width),
        clip=render.clip, light=None,
        chroma={"sigma": CHROMA_SIGMA}, seconds=total,
        regions=_regions_report(region_list or [], maps, regions_source),
    )
    timing = {
        "cache_load": round(cache_load, 3),
        **setup_timing,
        **render.timing,
        "total": base["seconds"],
    }
    log.info("timing: " + ", ".join(f"{key}={value:.2f}s"
                                    for key, value in timing.items()))
    typer.echo(f"wrote {inputs.out}  ({render.frames} frames)")
    _write_report(inputs.report, _video_report(
        fixed, base, render.frames, gt_info.fps,
        kind="full", started_at=started_at,
        finished_at=started_at + timedelta(seconds=total),
        cache=cache, timing=timing,
        atmo_source={**atmo_source, "atmo_match": render.match,
                     "atmo_short": render.short}))


@app.command()
def relight(
    gt: Path = typer.Option(None, "--gt", help="真 GT：2:1 ERP；视频走整段，图片走单帧"),
    atmo: Path = typer.Option(
        None, "--atmo",
        help="AI 氛围依据：图片（整段复用固定光场）或视频（逐帧光场）；"
             "图片与 GT 同尺寸，视频只要求 2:1"),
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
        help="仅视频 GT：对齐与估光照场用的帧（0 起）；缺省自动选。"
             "氛围是视频时，同时决定与它配对的那一帧"),
    sample: int | None = typer.Option(
        None, "--sample",
        help="仅视频 GT：只渲染 N 帧均匀采样的小样 PNG 到 --out 目录，不编码整段"),
    sample_at: str | None = typer.Option(
        None, "--sample-at",
        help="仅视频 GT：按帧号出小样，逗号分隔（如 12,240,600），与 --sample 互斥"),
    regions: Path | None = typer.Option(
        None, "--regions",
        help="区域覆盖定义 JSON（version=1）；--case 模式缺省读 [relight] regions"),
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
    """把 AI 氛围依据的光照搬到真 GT 上：图片出单帧，视频出整段（固定机位）。"""
    started = time.perf_counter()
    started_at = _now()
    log = Log(quiet=quiet)
    sample_indices = _parse_sample_at(sample_at)
    sample_count = sample
    if sample_count is None and sample_indices is not None:
        sample_count = len(sample_indices)
    strength, local_shading = resolve_params(scene, strength, local_shading)
    if highlight_clip and not 0.0 < clip_knee < clip_max <= 1.0:
        _param_error(f"软裁切参数须满足 0 < knee < max <= 1，"
                     f"收到 knee={clip_knee}，max={clip_max}")

    inputs = _resolve_inputs(gt, atmo, out, case, cases_dir, sample_count, report,
                             started_at)

    try:
        kind = kind_of(inputs.gt)
        atmo_kind = kind_of(inputs.atmo)
    except CaseError as exc:
        _param_error(str(exc))
    if kind == "image":
        if atmo_kind == "video":
            _param_error("氛围依据是视频，但底版是图片：视频氛围只适用于视频底版")
        if ref_frame is not None:
            _param_error("--ref-frame 只适用于视频 GT：图片 GT 没有参考帧的概念")
        if sample is not None:
            _param_error("--sample 只适用于视频 GT：图片 GT 本身就只出一帧")
        if sample_indices is not None:
            _param_error("--sample-at 只适用于视频 GT：图片 GT 本身就只出一帧")
        if crf is not None:
            _param_error("--crf 只适用于视频 GT：图片 GT 不经过视频编码")
    else:
        if dump_light:
            _param_error("--dump-light 只适用于图片 GT：诊断图是单帧概念，"
                         "需要时用 --sample 或对参考帧单独跑图片 GT")
        if sample is not None and sample <= 0:
            _param_error(f"--sample 必须不小于 1，收到 {sample}")
        if sample_indices is not None and sample is not None:
            _param_error("--sample 与 --sample-at 只能选一种：两者都是小样模式")

    params = Params(scene=scene, strength=strength, local_shading=local_shading,
                    highlight_clip=highlight_clip, clip_knee=clip_knee,
                    clip_max=clip_max)
    regions_source = regions or inputs.regions
    region_list = _load_region_list(regions_source)
    if kind == "image":
        _relight_image(inputs.gt, inputs.atmo, inputs.out, params,
                       dump_light=dump_light, report=inputs.report, started=started,
                       region_list=region_list, regions_source=regions_source)
    else:
        conf = _conf(config, overrides)
        if atmo_kind == "video":
            _relight_video_atmo(inputs, params, conf, log,
                                ref_frame=ref_frame, sample=sample, crf=crf,
                                started=started, started_at=started_at,
                                sample_at=sample_indices, region_list=region_list,
                                regions_source=regions_source)
        else:
            _relight_video(inputs, params, conf, log,
                           ref_frame=ref_frame, sample=sample, crf=crf,
                           started=started, started_at=started_at,
                           sample_at=sample_indices, region_list=region_list,
                           regions_source=regions_source)
    if case:
        _record_into_case(case, cases_dir, inputs.out, sample is not None)


def _resolve_inputs(gt: Path | None, atmo: Path | None, out: Path | None,
                    case: str | None, cases_dir: Path | None, sample: int | None,
                    report: Path | None, started_at: datetime) -> RelightInputs:
    """把 ``--case`` 或三个显式路径解析成同一组入参。

    ``--case`` 只从两个槽位取文件，不接受同时给 ``--gt``/``--atmo``：一份文件有两个来源
    会让「case 里记的是哪张」变得含糊。产物位置也由 case 决定，所以 ``--out`` 同样不接受。
    ``--case`` 模式额外带缓存身份；显式路径没有 ``cache_file``，不缓存。产物文件名里的
    时间戳取自命令开始时捕获的 ``started_at``，与报告里的 ``started_at`` 同源。
    """
    if case is None:
        missing = [flag for flag, value in (("--gt", gt), ("--atmo", atmo),
                                            ("--out", out)) if value is None]
        if missing:
            _param_error(f"没有 --case 时必须给 {'、'.join(missing)}")
        return RelightInputs(gt=gt, atmo=atmo, out=out, report=report)
    if gt is not None or atmo is not None or out is not None:
        _param_error("--case 与 --gt/--atmo/--out 只能选一种：以 case 为单位时"
                     "输入取自两个槽位、产物写 case 的 out/")
    try:
        loaded = case_mod.load(case, cases_dir)
    except CaseError as exc:
        _param_error(str(exc))
    if loaded.flow != "relight":
        _param_error(f"case {case} 是 {loaded.flow} 流，不是 relight")
    try:
        case_mod.check_relight_pair(loaded)
    except CaseError as exc:
        _param_error(str(exc))
    gt_asset, atmo_asset = loaded.relight_slots()
    if gt_asset is None or atmo_asset is None:
        missing = "底版" if gt_asset is None else "氛围依据"
        _param_error(f"case {case} 还缺{missing}：先放好两个槽位"
                     f"（vrstudio case relight --case {case} …）")
    cdir = loaded.dir(cases_dir)
    regions_path = None
    pointer = (loaded.relight or {}).get("regions")
    if pointer:
        regions_path = cdir / pointer
    suffix = ".png" if gt_asset.kind == "image" else ".mp4"
    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    target = (cdir / "out" / "samples" / stamp if sample is not None
              else cdir / "out" / f"{stamp}_relit{suffix}")
    gt_path = gt_asset.resolved(loaded, cases_dir)
    atmo_path = atmo_asset.resolved(loaded, cases_dir)
    cache_file = None
    gt_size = gt_mtime_ns = atmo_size = atmo_mtime_ns = 0
    try:
        gt_stat = gt_path.stat()
        atmo_stat = atmo_path.stat()
    except OSError:
        pass                     # 文件不在了：后面的读图/探测会按运行失败报出来
    else:
        cache_file = relight_cache.cache_path(cdir)
        gt_size, gt_mtime_ns = gt_stat.st_size, gt_stat.st_mtime_ns
        atmo_size, atmo_mtime_ns = atmo_stat.st_size, atmo_stat.st_mtime_ns
    return RelightInputs(
        gt=gt_path, atmo=atmo_path, out=target,
        report=report if report is not None else target.with_suffix(".json"),
        regions=regions_path,
        cache_file=cache_file,
        gt_sha256=gt_asset.sha256, gt_size=gt_size, gt_mtime_ns=gt_mtime_ns,
        atmo_sha256=atmo_asset.sha256, atmo_size=atmo_size,
        atmo_mtime_ns=atmo_mtime_ns,
    )


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
