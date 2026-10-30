"""vrstudio relight: 把 AI 氛围图的光照搬到真 GT 上。

定位（决策 48）：真 8K GT 提供结构与细节，AI 4K 氛围图只提供低频光照、色温与遮挡；
输出每个像素都落在 GT 的原始网格上，不做超分、不做内容回填。

流程：读两张图 → 校验 2:1 与同尺寸 → 估计相似变换并 warp 氛围图 → 判对齐是否可信
→ 在 2K 上估计照明表示 → 乘到 GT 上（可选高光软裁切）→ 写图、写报告。

退出码：0 成功；2 参数错（比例/尺寸/参数越界）；1 运行失败（读图、对齐不可信、写图）。
对齐不可信必须失败且不出图——错位的光照比不重打光更糟。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import NoReturn

import typer

from ..media.imageio import ImageError, read_image, write_gray_png, write_png
from ..relight.align import MIN_STRUCTURE_CORRELATION, estimate_similarity, warp_to_gt
from ..relight.compose import clipped_fraction, soft_clip
from ..relight.compose import relight as compose_relight
from ..relight.fields import estimate_fields
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


@app.command()
def relight(
    gt: Path = typer.Option(..., "--gt", help="真 GT：2:1 ERP，8-bit"),
    atmo: Path = typer.Option(..., "--atmo", help="AI 氛围图：与 GT 同尺寸、同比例"),
    out: Path = typer.Option(..., "--out", help="输出 PNG（与 GT 同尺寸）"),
    scene: str = typer.Option("night", "--scene", help="场景预设：night 1.0/0.35，day 0.8/0.2"),
    strength: float | None = typer.Option(
        None, "--strength", help="整体施加量 0..1；缺省取场景预设，0 时输出与 GT 逐位一致"),
    local_shading: float | None = typer.Option(
        None, "--local-shading",
        help="局部明暗权重 0..1；缺省取场景预设，0 只用低频光场，1 完全跟随 AI 的局部明暗"),
    highlight_clip: bool = typer.Option(False, "--highlight-clip", help="高光软裁切"),
    clip_knee: float = typer.Option(0.72, "--clip-knee", help="软裁切拐点（亮度 0..1）"),
    clip_max: float = typer.Option(0.98, "--clip-max", help="软裁切上限（亮度 0..1）"),
    dump_light: bool = typer.Option(False, "--dump-light", help="额外输出四份诊断图"),
    report: Path = typer.Option(None, "--report", help="报告 JSON 路径"),
) -> None:
    """把 AI 氛围图的光照搬到真 GT 上，输出与 GT 同网格的重打光图。"""
    started = time.perf_counter()
    strength, local_shading = resolve_params(scene, strength, local_shading)
    if highlight_clip and not 0.0 < clip_knee < clip_max <= 1.0:
        _param_error(f"软裁切参数须满足 0 < knee < max <= 1，"
                     f"收到 knee={clip_knee}，max={clip_max}")

    try:
        gt_image = read_image(gt)
        atmo_image = read_image(atmo)
    except ImageError as exc:
        _runtime_error(str(exc))
    check_inputs(gt_image, atmo_image)
    height, width = gt_image.shape[:2]
    typer.echo(f"relight: {width}x{height} scene={scene} "
               f"strength={strength} local-shading={local_shading}")

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
    out_image = compose_relight(gt_image, fields, strength, local_shading)
    clipped_before = clipped_fraction(out_image)
    clip_stats = None
    if highlight_clip:
        out_image = soft_clip(out_image, clip_knee, clip_max)
        clip_stats = {
            "knee": clip_knee,
            "max": clip_max,
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
        params={"scene": scene, "strength": strength, "local_shading": local_shading,
                "highlight_clip": highlight_clip,
                "clip_knee": clip_knee, "clip_max": clip_max},
        align=align_report(alignment, width),
        clip=clip_stats, light=light_stats,
        seconds=time.perf_counter() - started,
    )
    typer.echo(f"wrote {out} ({summary['seconds']}s)")
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        typer.echo(f"report -> {report}")


if __name__ == "__main__":
    app()
