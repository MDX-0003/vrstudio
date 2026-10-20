"""Extract one frame as an image, without scaling or re-timing the source.

The keyframe workbench uses the extracted frame as a *draft* input; the geometry
contract is the clip's original raster, so this helper deliberately does not
resize. The video page's ``-ss`` seeks before ``-i`` (fast seek) followed by
``-frames:v 1``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .binres import ffmpeg
from .videoio import VideoIOError


def extract_frame(video: str | Path, t_seconds: float, out_png: str | Path,
                  *, config=None) -> Path:
    video, out_png = Path(video), Path(out_png)
    if not video.is_file():
        raise VideoIOError(f"no such video: {video}")
    if float(t_seconds) < 0:
        raise ValueError("t_seconds must be >= 0")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ffmpeg(config), "-y", "-v", "error",
         "-ss", f"{float(t_seconds):.3f}", "-i", str(video),
         "-frames:v", "1", str(out_png)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise VideoIOError(
            f"ffmpeg could not extract a frame at {t_seconds:.3f}s from "
            f"{video.name}:\n{(result.stderr or '').strip()[:400]}")
    if not out_png.is_file():
        raise VideoIOError(f"ffmpeg produced no frame at {t_seconds:.3f}s ({video.name})")
    return out_png
