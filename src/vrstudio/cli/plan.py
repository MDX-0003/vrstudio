"""``vrstudio plan`` — print the pad geometry and exit. Same arithmetic as ``pad``."""

from __future__ import annotations

from pathlib import Path

import typer

from .. import config as config_mod
from ..redraw.geometry import GeometryError
from ..progress import Log
from ..media.videoio import probe
from .pad import build_plan, derived_path, summary_lines



def main(
    src: Path = typer.Option(..., "--in", exists=True, dir_okay=False, help="source ERP video"),
    out: Path = typer.Option(Path("padded.mp4"), "--out",
                             help="only used to name the derived artifacts in the summary"),
    config: Path | None = typer.Option(None, "--config", help="TOML case config"),
    overrides: list[str] = typer.Option([], "--set", help="config override key=value"),
) -> None:
    """Print the canvas geometry `pad` would produce, without rendering."""
    log = Log()
    try:
        conf = config_mod.load(config, overrides)
        info = probe(src, conf)
        plan = build_plan(info, conf)
    except (config_mod.ConfigError, GeometryError) as exc:
        log.error(str(exc))
        raise typer.Exit(2)
    except Exception as exc:                                  # noqa: BLE001
        log.error(str(exc))
        raise typer.Exit(2)

    manifest = derived_path(out, str(conf.get("manifest.path_template")))
    mask = (derived_path(out, str(conf.get("mask.path_template")))
            if conf.get("mask.enabled", True) else None)

    print("pad plan")
    for line in summary_lines(plan, info, out, mask, manifest):
        print(f"    {line}")
    for warning in plan.warnings:
        log.warn(warning)
    if not plan.warnings:
        print("\nno warnings: the geometry satisfies the closure invariant")
