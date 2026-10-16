"""Locate the external binaries vrpad drives.

Rule (CLAUDE.md 12): any exe this tool calls directly lives in ``<repo>/bin`` and
is addressed by a path **relative to the repo root**, computed from ``__file__``
— never by a bare PATH lookup. A build that only runs on the machine it was
written on is not a deliverable.

Resolution order:

1. ``$VRSTUDIO_BIN_DIR/<name>``   explicit override for CI or a shared tool cache
2. ``<repo>/bin/<name>``        the shipped location
3. ``PATH``                     only when ``bin.allow_path_fallback = true``

Provenance (sha256 + version) is recorded in ``bin/README.md`` by
``tools/fetch_bin.py``; the encode parameters are part of the pixel baseline, so
which build produced a result has to be recoverable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BIN_DIR = REPO_ROOT / "bin"


class BinNotFound(RuntimeError):
    """The requested external binary is not where the contract says it must be."""


def repo_root() -> Path:
    return REPO_ROOT


def configured_bin_dir(config=None) -> Path:
    """``bin.dir`` is documented as relative to the repo root."""
    rel = "bin"
    if config is not None:
        rel = str(config.get("bin.dir", rel))
    path = Path(rel)
    return path if path.is_absolute() else (REPO_ROOT / path)


def find_tool(name: str, config=None, env: dict | None = None) -> str | None:
    env = os.environ if env is None else env

    override = env.get("VRSTUDIO_BIN_DIR") or env.get("VRPAD_BIN_DIR")
    if override:
        candidate = Path(override) / name
        if candidate.is_file():
            return str(candidate)

    candidate = configured_bin_dir(config) / name
    if candidate.is_file():
        return str(candidate)

    allow = True if config is None else bool(config.get("bin.allow_path_fallback", False))
    if allow:
        return shutil.which(name)
    return None


def require_tool(name: str, config=None, env: dict | None = None) -> str:
    path = find_tool(name, config=config, env=env)
    if path:
        return path
    allowed = True if config is None else bool(config.get("bin.allow_path_fallback", False))
    raise BinNotFound(
        f"{name} not found. Looked in $VRSTUDIO_BIN_DIR, {configured_bin_dir(config)}"
        + (", and PATH" if allowed else "")
        + f".\nPopulate it with:  uv run python tools/fetch_bin.py --from <ffmpeg bin dir>"
    )


def ffmpeg(config=None) -> str:
    return require_tool(str(config.get("bin.ffmpeg", "ffmpeg.exe")) if config else "ffmpeg.exe",
                        config=config)


def ffprobe(config=None) -> str:
    return require_tool(str(config.get("bin.ffprobe", "ffprobe.exe")) if config else "ffprobe.exe",
                        config=config)


def tool_version(path: str) -> str:
    """First line of ``-version``, for the provenance record and error messages."""
    try:
        r = subprocess.run([path, "-version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        text = (r.stdout or r.stderr or "").strip()
        return text.splitlines()[0] if text else "?"
    except OSError as exc:
        return f"<failed: {exc}>"
