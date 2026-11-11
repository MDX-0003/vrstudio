"""vrstudio assemble: 把空间合并段接回源片时间轴，输出完整成片。

输入是源片、``out/<版本>_space.mp4`` 和 ``extracts/<版本>/clip.json``；输出
``out/<版本>_frame.mp4``。前段 = 源片 [0, frame_start)，中段 = 空间合并段
（对应源片 [frame_start, frame_end]），后段 = 源片 (frame_end, 末帧]。

时间拼接不做帧率转换：空间合并已经保证「段帧数 == n_seg @ 抽取 fps」。这里先校验
帧数、fps、源片身份，任何一项不合约都直接报错，不静默重采样、不输出半成品。
整片区间（frame_start == 0 且 frame_end 是末帧）不需要拼接，命令直接返回。

视频统一 H.264 8-bit yuv420p（决策 19）；音频从源片复用。流式逐帧写，不把整片
驻留内存。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import typer

from .. import case as case_mod
from ..media.ffmpeg import FfmpegSink, has_audio
from ..patch.case import load_sidecar_single_segment
from ..progress import PhaseLog as Log
from ..progress import PhaseRateMeter as RateMeter
from ..progress import format_duration, open_log_file

app = typer.Typer(add_completion=False, help=__doc__)


class VideoFacts:
    def __init__(self, path: Path):
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise typer.Exit(f"cannot open video: {path}")
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        cap.release()

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


def resolve_case_inputs(case_name: str, cases_dir,
                        extract_version: str | None = None) -> dict:
    """统一 case 模型 → assemble 的四元组（src/space/sidecar/output）。"""
    case = case_mod.load(case_name, cases_dir)
    if case.flow != "patch":
        raise typer.Exit(f"assemble --case: case {case_name} 是 {case.flow} 流"
                         f"（时间拼接只属于 patch）")
    version = extract_version or case.extract_current
    if not version:
        raise typer.Exit(f"assemble --case: case {case_name} 没有抽取版本")
    d = case.dir(cases_dir) / "extracts" / version
    out_dir = case.dir(cases_dir) / "out"
    return {
        "input": Path(case_mod.check_source(case, cases_dir)),
        "space": out_dir / f"{version}_space.mp4",
        "sidecar": d / "clip.json",
        "output": out_dir / f"{version}_frame.mp4",
    }


def whole_segment(segment, source_frames: int) -> bool:
    """抽取区间覆盖整条源片：不需要时间拼接。"""
    return segment.frame_start <= 0 and segment.frame_end >= source_frames - 1


@app.command()
def assemble(
    case_name: str = typer.Option(None, "--case",
                                  help="统一 case 名：源片/空间合并段/clip.json/输出全部自动定位"),
    cases_dir: Path = typer.Option(None, help="default: <repo>/cases"),
    extract_version: str = typer.Option(
        None, "--extract",
        help="要拼接的抽取版本；缺省用 [extract] current，需与 merge 用同一版本"),
    input_video: Path = typer.Option(None, "--input", help="source ERP (360) video"),
    space: Path = typer.Option(None, "--space", help="空间合并段 mp4（merge 的输出）"),
    sidecar: Path = typer.Option(None, help="clip.json (single segment)"),
    output: Path = typer.Option(None, help="output full-length mp4"),
    crf: int = typer.Option(14, help="x265 crf"),
    preset: str = typer.Option("slow", help="x265 preset"),
    report: Path = typer.Option(None, help="run report JSON（缺省写到输出旁边）"),
    progress_every: int = typer.Option(0),
    quiet: bool = typer.Option(False, "-q"),
):
    if case_name:
        if input_video or space or sidecar or output:
            raise typer.Exit("assemble: --case 与 --input/--space/--sidecar/--output 互斥")
        resolved = resolve_case_inputs(case_name, cases_dir, extract_version)
        input_video, space, sidecar, output = (resolved["input"], resolved["space"],
                                               resolved["sidecar"], resolved["output"])
    elif extract_version:
        raise typer.Exit("assemble: --extract 只用于 --case 模式")
    elif not (input_video and space and sidecar and output):
        raise typer.Exit("assemble: 需要 --case，或完整的 --input/--space/--sidecar/--output")

    t_start = time.time()
    fh, log_path = open_log_file(f"assemble_{output.stem}")
    log = Log(every=progress_every, quiet=quiet, fh=fh)
    typer.echo(f"log -> {log_path}")

    seg = load_sidecar_single_segment(sidecar)
    with open(sidecar, encoding="utf-8") as f:
        sidecar_fps = float(json.load(f)["fps"])
    src = VideoFacts(input_video)
    n_seg = seg.frame_end - seg.frame_start + 1
    log.phase(f"segment {seg.id}: frames {seg.frame_start}..{seg.frame_end} "
              f"({n_seg} frames) @ {sidecar_fps:.2f}fps; source {src.frames} frames "
              f"@ {src.fps:.2f}fps, {src.width}x{src.height}")

    if whole_segment(seg, src.frames):
        log.phase("抽取区间覆盖整条源片：空间合并段本身就是整片，跳过时间拼接")
        fh.close()
        return
    if abs(src.fps - sidecar_fps) > 0.01:
        raise typer.Exit(
            f"源片 fps {src.fps:.3f} 与 clip.json 的 {sidecar_fps:.3f} 不一致；"
            "按帧拼接会漂移，先检查抽取或用 CFR 源片")
    if seg.frame_start < 0 or seg.frame_end >= src.frames:
        raise typer.Exit(
            f"抽取区间 {seg.frame_start}..{seg.frame_end} 超出源片帧数 {src.frames}")
    if not Path(space).is_file():
        raise typer.Exit(f"空间合并段不存在：{space}——先跑 vrstudio merge")

    mid = VideoFacts(space)
    if mid.frames != n_seg:
        raise typer.Exit(
            f"空间合并段 {mid.frames} 帧，契约要求 {n_seg} 帧：{space}")
    if abs(mid.fps - sidecar_fps) > 0.01:
        raise typer.Exit(
            f"空间合并段 fps {mid.fps:.3f} 与 clip.json 的 {sidecar_fps:.3f} 不一致")
    if mid.size != src.size:
        raise typer.Exit(
            f"空间合并段尺寸 {mid.width}x{mid.height} 与源片 "
            f"{src.width}x{src.height} 不一致")

    prefix_n = seg.frame_start
    suffix_n = src.frames - seg.frame_end - 1
    log.phase(f"assembling: {prefix_n} prefix + {n_seg} segment + {suffix_n} suffix "
              f"= {src.frames} frames")

    cap = cv2.VideoCapture(str(input_video))
    space_cap = cv2.VideoCapture(str(space))
    if not cap.isOpened() or not space_cap.isOpened():
        raise typer.Exit(f"cannot open input: {input_video} 或 {space}")
    audio_from = str(input_video) if has_audio(str(input_video)) else None
    sink = FfmpegSink(str(output), src.size, sidecar_fps, crf, preset, audio_from)
    meter = RateMeter(src.frames)
    written = 0

    def _write(frame, what: str) -> None:
        nonlocal written
        if frame is None:
            raise typer.Exit(f"{what} 解码失败（已写 {written} 帧）")
        sink.write(frame)
        written += 1
        meter.tick()
        log.progress(meter, "assembling")

    for i in range(prefix_n):
        ok, frame = cap.read()
        _write(frame if ok else None, f"源片前段第 {i} 帧")
    for i in range(n_seg):
        ok, frame = space_cap.read()
        _write(frame if ok else None, f"空间合并段第 {i} 帧")
    # 跳到源片中段之后；seek 失败时逐帧丢弃同样安全（顺序解码）。
    if not cap.set(cv2.CAP_PROP_POS_FRAMES, seg.frame_end + 1):
        for _ in range(n_seg):
            cap.grab()
    for i in range(suffix_n):
        ok, frame = cap.read()
        _write(frame if ok else None, f"源片后段第 {i} 帧")

    log.done()
    sink.close()
    cap.release()
    space_cap.release()

    if written != src.frames:
        raise typer.Exit(f"只写出 {written} 帧，契约要求 {src.frames} 帧")

    size_mb = output.stat().st_size / 1e6 if output.exists() else 0
    elapsed = time.time() - t_start
    log.phase(f"wrote {output} ({src.width}x{src.height}, {written} frames, "
              f"{size_mb:.1f} MB, {format_duration(elapsed)} total)")

    report_path = report or output.with_name(output.stem + ".assemble.json")
    report_path.write_text(json.dumps({
        "input": str(input_video), "space": str(space), "sidecar": str(sidecar),
        "output": str(output),
        "segment": {"start": seg.frame_start, "end": seg.frame_end,
                    "frames": n_seg, "fps": sidecar_fps},
        "frames_prefix": prefix_n, "frames_segment": n_seg,
        "frames_suffix": suffix_n, "frames_written": written,
        "frames_expected": src.frames, "fps": sidecar_fps,
        "encoder": "ffmpeg/libx264", "crf": crf, "preset": preset,
        "audio_from_source": bool(audio_from),
        "elapsed_s": round(elapsed, 1),
    }, indent=2), encoding="utf-8")
    typer.echo(f"report -> {report_path}")
    fh.close()


if __name__ == "__main__":
    app()
