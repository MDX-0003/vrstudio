"""``vrstudio seam-center`` — move the seam from the frame edge to the frame centre.

A pure circular shift by half the width, so the discontinuity that lives at the
raster boundary ends up in the middle where it can be looked at, or where an
inpainting model would have valid context on both sides. Integer columns only, so
it is lossless.

It is **not** a fix and it is not part of the pad/recompose chain: a circular shift
relocates a seam without reducing it (measured on the reference clip: wrap 12.36 ->
0.69 while the discontinuity reappeared in the interior at 12.34). Use it to
diagnose, never to claim the seam is gone — and note that ``verify`` deliberately
measures the worst interior pair so this trick cannot pass the acceptance check.
"""

from __future__ import annotations

from pathlib import Path

import typer

from .. import config as config_mod
from ..redraw.preroll import pre_roll
from ..progress import Log, RateMeter
from ..media.videoio import FrameReader, FrameWriter, probe



def main(
    src: Path = typer.Option(..., "--in", exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out"),
    config: Path | None = typer.Option(None, "--config", help="TOML case config"),
    overrides: list[str] = typer.Option([], "--set", help="config override key=value"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Circularly shift a video so its wrap seam sits in the frame centre."""
    log = Log(quiet=quiet)
    try:
        conf = config_mod.load(config, overrides)
        info = probe(src, conf)
    except Exception as exc:                                  # noqa: BLE001
        log.error(str(exc))
        raise typer.Exit(2)

    shift = info.size[0] // 2
    log.info(f"shifting {info.size[0]}x{info.size[1]} by {shift} columns "
             f"({360.0 * shift / info.size[0]:.1f} degrees)")
    meter = RateMeter(info.frames or None, log)
    try:
        with FrameWriter(out, info.size, info.fps, conf.video,
                         audio_from=src, config=conf) as writer:
            with FrameReader(info, conf) as reader:
                for frame in reader:
                    writer.write(pre_roll(frame, shift))
                    meter.tick()
    except Exception as exc:                                  # noqa: BLE001
        log.error(f"seam-center failed: {exc}")
        raise typer.Exit(1)
    log.step(f"{out}  ({meter.summary()})")
    print("note: this relocates the seam, it does not remove it. "
          "Use `vrstudio verify` and read the worst interior pair.")
