"""vrstudio extract: ERP video -> viewport clip + sidecar + inner mask.

Streaming: one 8K source frame (~96 MB) is decoded, projected and written at a
time — never load the frame range into a list (590 frames would be ~55 GB and
thrash the machine; that was tried once and is why this module looks the way it
does). The mask is part of the extract contract (black = the region merge
replaces with AI content, white = the anchor margin kept from the source; the
mask is for this tool's merge only and never reaches the external AI tool);
test_extract_contract enforces it.

Every run mirrors its console output to logs/<name>_<timestamp>.log.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import cv2
import numpy as np
import typer

from ..patch.case import Case, load_case, set_extract_version
from ..patch.sidecar import Sidecar, Segment, Viewport, InnerRect, save_sidecar
from ..patch.extract import extract_viewport_frame, open_writer, make_mask_image
from ..progress import PhaseLog as Log, PhaseRateMeter as RateMeter, open_log_file
from .. import case as case_mod

app = typer.Typer(add_completion=False, help=__doc__)


def _sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stream_extract(cap, fps, seg: Segment, clip_path: Path, sidecar_path: Path,
                    mask_path: Path, erp_size: tuple[int, int], log: Log):
    n = seg.frame_end - seg.frame_start + 1
    meter = RateMeter(n)
    writer = open_writer(str(clip_path), fps, (seg.viewport.width, seg.viewport.height))
    written = 0
    for _ in range(n):
        ok, frame = cap.read()
        if not ok:
            log.info(f"source ended early at frame {written} of {n}")
            break
        writer.write(extract_viewport_frame(frame, seg.viewport))
        written += 1
        meter.tick()
        log.progress(meter, "extracting")
    writer.release()

    mask = make_mask_image(seg.viewport, seg.inner)
    cv2.imwrite(str(mask_path), mask)
    sc = Sidecar(erp_width=erp_size[0], erp_height=erp_size[1], fps=fps,
                 segments=[seg])
    save_sidecar(sc, sidecar_path)
    log.phase(f"clip    -> {clip_path} ({written} frames)")
    log.phase(f"sidecar -> {sidecar_path}")
    log.phase(f"mask    -> {mask_path} (black=regenerate, white=anchor)")


def _write_raw_frames(clip: Path, out_dir: Path, log: Log) -> None:
    """从写完的 clip.mp4 导出首帧与末帧原始图（不缩放，工作台输入底图）。

    决策 33：准备工作在 extract 阶段一次完成，firstLastFrame 不再依赖工作台里
    的「从 clip 抽帧」按钮。这两张图只给工作台，不是生成候选。
    """
    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        raise typer.Exit(f"cannot reopen clip for raw frames: {clip}")
    ok, first = cap.read()
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if count > 1:
        cap.set(cv2.CAP_PROP_POS_FRAMES, count - 1)
        ok_last, last = cap.read()
    else:
        ok_last, last = ok, first
    cap.release()
    if not ok or not ok_last:
        raise typer.Exit(f"cannot read first/last frame from {clip}")
    for name, image in (("first_frame_raw.png", first),
                        ("last_frame_raw.png", last)):
        if not cv2.imwrite(str(out_dir / name), image):
            raise typer.Exit(f"cannot write {out_dir / name}")
    log.phase(f"raw     -> first/last frame ({first.shape[1]}x{first.shape[0]}, "
              f"{count or '?'} frames)")


@app.command("case")
def from_case(
    case_file: Path = typer.Argument(..., exists=True, help="case.toml path"),
    out_dir: Path = typer.Option(None, help="default: <case dir>/derived"),
):
    """Extract using a case.toml (verifies the source hash first)."""
    case: Case = load_case(case_file)  # raises on hash mismatch
    out = out_dir or (case_file.parent / "derived")
    out.mkdir(parents=True, exist_ok=True)
    fh, log_path = open_log_file(f"extract_{case.name}")
    log = Log(fh=fh)
    try:
        log.phase(f"case: {case_file} (source sha256 verified)")
        src = case.source.path
        if not Path(src).is_absolute():
            src = str(case_file.parent / src)
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise typer.Exit(f"cannot open video: {src}")
        fps = cap.get(cv2.CAP_PROP_FPS) or case.fps
        seg = Segment(id="seg_0", frame_start=case.frame_start,
                      frame_end=case.frame_end, viewport=case.viewport,
                      inner=case.inner)
        if not cap.set(cv2.CAP_PROP_POS_FRAMES, case.frame_start):
            log.info("WARNING: could not seek; decoding from the start instead.")
        # Versioned snapshot (workflow decision: picking is a draft; extract
        # records). Immutable per run; derived/ mirrors the latest version so
        # plain `vrstudio merge ... --sidecar derived/clip.json` keeps working.
        version = time.strftime("%Y%m%d-%H%M%S")
        vdir = case_file.parent / "extracts" / version
        vdir.mkdir(parents=True, exist_ok=True)
        _stream_extract(cap, fps, seg, vdir / "clip.mp4", vdir / "clip.json",
                        vdir / "clip_mask.png",
                        (case.erp_width, case.erp_height), log)
        cap.release()
        _write_raw_frames(vdir / "clip.mp4", vdir, log)
        geometry_sha = _sha256_file(vdir / "clip.json")
        set_extract_version(case_file, version)
        derived = case_file.parent / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        for f in ("clip.mp4", "clip.json", "clip_mask.png"):
            shutil.copyfile(vdir / f, derived / f)
        log.phase(f"extract version: {version} (geometry sha {geometry_sha[:12]}…)")
        log.phase(f"log -> {log_path}")
    finally:
        fh.close()


@app.command("name")
def from_case_name(
    case_name: str = typer.Option(..., "--case", help="统一 case 名（cases/ 下目录）"),
    cases_dir: Path = typer.Option(None, help="default: <repo>/cases"),
):
    """Extract by unified case name: 草稿（viewport/inner/frames）冻结进 extracts/<版本>。

    与 ``case`` 命令（vrpatch 老式 case.toml）的区别只在入口：源路径/哈希守卫/
    草稿都来自统一 case 模型（src/vrstudio/case.py），extracts 版本目录结构与
    clip.json 契约两者完全一致。
    """
    case = case_mod.load(case_name, cases_dir)
    if case.flow != "patch":
        typer.secho(f"extract: case {case_name} 是 {case.flow} 流（extract 是 patch 专属步骤）",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    missing = case_mod.draft_ready(case)
    if missing:
        typer.secho(f"extract: pick 草稿未定（缺 [{missing}]）——先在 web pick 或"
                    f" case.toml 里补齐 viewport/inner/frames", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    src = case_mod.check_source(case, cases_dir)

    fh, log_path = open_log_file(f"extract_{case.name}")
    log = Log(fh=fh)
    try:
        log.phase(f"case: {case.name} (source sha256 verified)")
        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            raise typer.Exit(f"cannot open video: {src}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        erp_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        erp_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        seg = Segment(id="seg_0", frame_start=int(case.frames["start"]),
                      frame_end=int(case.frames["end"]),
                      viewport=Viewport(**case.viewport), inner=InnerRect(**case.inner))
        if not cap.set(cv2.CAP_PROP_POS_FRAMES, seg.frame_start):
            log.info("WARNING: could not seek; decoding from the start instead.")
        version = time.strftime("%Y%m%d-%H%M%S")
        vdir = case.dir(cases_dir) / "extracts" / version
        vdir.mkdir(parents=True, exist_ok=True)
        _stream_extract(cap, fps, seg, vdir / "clip.mp4", vdir / "clip.json",
                        vdir / "clip_mask.png", (erp_w, erp_h), log)
        cap.release()
        _write_raw_frames(vdir / "clip.mp4", vdir, log)
        geometry_sha = _sha256_file(vdir / "clip.json")
        case_mod.set_extract_current(case.name, version, root=cases_dir)
        derived = case.dir(cases_dir) / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        for f in ("clip.mp4", "clip.json", "clip_mask.png"):
            shutil.copyfile(vdir / f, derived / f)
        log.phase(f"extract version: {version} (geometry sha {geometry_sha[:12]}…)")
        log.phase(f"log -> {log_path}")
    finally:
        fh.close()


@app.command("args")
def from_args(
    input_video: Path = typer.Option(..., help="source ERP (360) video"),
    clip: Path = typer.Option(...),
    sidecar: Path = typer.Option(...),
    start: int = typer.Option(0),
    end: int = typer.Option(..., help="inclusive frame index"),
    yaw: float = typer.Option(..., help="deg"),
    pitch: float = typer.Option(..., help="deg"),
    fov: float = typer.Option(..., help="horizontal FOV deg"),
    vpw: int = typer.Option(..., help="viewport width px"),
    vph: int = typer.Option(..., help="viewport height px"),
    inner: str = typer.Option(..., help="X,Y,W,H"),
    mask: Path = typer.Option(None, help="default: <clip stem>_mask.png"),
):
    """Extract with explicit arguments (no case.toml needed)."""
    ix, iy, iw, ih = (int(v) for v in inner.split(","))
    seg = Segment(id="seg_0", frame_start=start, frame_end=end,
                  viewport=Viewport(yaw_deg=yaw, pitch_deg=pitch,
                                    fov_h_deg=fov, width=vpw, height=vph),
                  inner=InnerRect(x=ix, y=iy, width=iw, height=ih))
    fh, log_path = open_log_file(f"extract_{input_video.stem}")
    log = Log(fh=fh)
    try:
        log.phase(f"input: {input_video}")
        cap = cv2.VideoCapture(str(input_video))
        if not cap.isOpened():
            raise typer.Exit(f"cannot open video: {input_video}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        erp_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        erp_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not cap.set(cv2.CAP_PROP_POS_FRAMES, start):
            log.info("WARNING: could not seek; decoding from the start instead.")
        _stream_extract(cap, fps, seg, clip, sidecar,
                        mask or clip.with_name(clip.stem + "_mask.png"),
                        (erp_w, erp_h), log)
        cap.release()
        log.phase(f"log -> {log_path}")
    finally:
        fh.close()


if __name__ == "__main__":
    app()
