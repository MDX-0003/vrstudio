"""Layered config: built-in defaults, files, then ``--set`` overrides.

Precedence, highest first::

    --set key=value  >  --config FILE  >  ./vrstudio.toml  >  ~/.config/vrpad/default.toml  >  built-in

The built-in defaults are the repo's ``config/default.toml`` — one human-readable
file rather than a Python dict that can drift from it. ``_FALLBACK`` exists only
so an installed copy without the repo tree still runs, and ``tests/test_config.py``
asserts the two agree so they cannot diverge silently.

Unknown keys are hard errors. A typo'd ``--set recompose.endpoint_lock_pctt=3``
that silently did nothing would be discovered by looking at the output, not by
the tool telling you.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS_FILE = REPO_ROOT / "config" / "default.toml"

#: Safety net for installs without the repo tree; kept in sync by a test.
_FALLBACK: dict = {
    "schema": 1,
    "pad": {
        "target_aspect": "21:9",
        "pre_roll": "auto",
        "on_aspect_conflict": "error",
        "even_dimensions": True,
        "pipeline": {"transforms": ["pre_roll", "pad_h"]},
    },
    "mask": {
        "enabled": True,
        "strategy": "redundant_white",
        "feather_px": 0,
        "png_compression": 0,
        "path_template": "{stem}_mask.png",
    },
    "recompose": {
        "registration": "circular_translate",
        "search_pct": 25.0,
        "d_star_mode": "clip_median",
        "per_frame_ema_alpha": 0.05,
        "blend_weight": "smoothstep",
        "endpoint_lock_pct": 2.0,
        "restore_interp": "lanczos4",
        "boundary_repair": "off",
        "boundary_repair_pct": 15.0,
        "max_sample_rows": 256,
        "aspect_drift_warn_pct": 2.0,
        "min_band_correlation": 0.80,
        "warn_quality": 1.15,
    },
    "verify": {
        "interior_band": [0.25, 0.75],
        "max_wrap_percentile": 95,
        "worst_interior_tol": 1.0,
    },
    "video": {
        "encoder": "libx264",
        "crf": 18,
        "preset": "veryfast",
        "pix_fmt": "yuv420p",
        "audio": "copy",
        "metadata": "copy",
    },
    "manifest": {"path_template": "{stem}_pad.json", "write_hash": True},
    "generate": {
        "vrgen": "vrgen",        # vrgen CLI 的可执行文件（跨仓库 CLI 契约，绝对路径最稳）
        "workdir": "vrgen_runs",  # vrgen 台账根，相对仓库根（gitignore）
    },
    "web": {
        "default_frontend": "next",  # next | legacy
        "proxy_max_w": 4096,         # 预览代理宽度上限（选区全景 + 对比页两路）
    },
    # [aigc] 段不迁 vrstudio：平台参数归 vrgen 仓库（E:\VRAIGC\vrgen），
    # 见 docs/architecture.md §2。
    "bin": {
        "dir": "bin",
        "ffmpeg": "ffmpeg.exe",
        "ffprobe": "ffprobe.exe",
        "allow_path_fallback": False,
    },
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    data: dict
    source_path: Path | None = None

    def get(self, path: str, default=None):
        node = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def need(self, path: str):
        sentinel = object()
        value = self.get(path, sentinel)
        if value is sentinel:
            raise ConfigError(f"missing config key {path!r}")
        return value

    # convenience accessors used by the CLIs
    @property
    def pad(self) -> dict:
        return self.data["pad"]

    @property
    def mask(self) -> dict:
        return self.data["mask"]

    @property
    def recompose(self) -> dict:
        return self.data["recompose"]

    @property
    def verify(self) -> dict:
        return self.data["verify"]

    @property
    def video(self) -> dict:
        return self.data["video"]

    @property
    def bin(self) -> dict:
        return self.data["bin"]


def _deep_merge(base: dict, overlay: dict, origin: str) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if key not in out:
            raise ConfigError(f"{origin}: unknown config key {key!r}")
        if isinstance(value, dict):
            if not isinstance(out[key], dict):
                raise ConfigError(f"{origin}: {key!r} is a table in the file but a scalar in defaults")
            out[key] = _deep_merge(out[key], value, f"{origin}:{key}")
        else:
            out[key] = value
    return out


def parse_override(text: str) -> tuple[str, object]:
    """``"recompose.endpoint_lock_pct=3"`` -> ``("recompose.endpoint_lock_pct", 3)``.

    The value is parsed as TOML so ints, floats, bools, strings and arrays behave
    the way they would in the config file. A bare word that is not valid TOML
    (``recompose.d_star_mode=per_frame``) is taken as a string, which is what a
    caller plainly means; validation then rejects a genuinely wrong type.
    """
    key, sep, raw = text.partition("=")
    if not sep:
        raise ConfigError(f"--set needs key=value, got {text!r}")
    literal = raw.strip()
    if not literal:
        raise ConfigError(f"--set {key}: empty value")
    try:
        value: object = tomllib.loads(f"v = {literal}")["v"]
    except tomllib.TOMLDecodeError:
        value = literal
    return key.strip(), value


def _set_path(data: dict, key: str, value: object) -> None:
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            raise ConfigError(f"--set {key}: no such config section {part!r}")
        node = node[part]
    if parts[-1] not in node:
        raise ConfigError(f"--set {key}: unknown config key")
    node[parts[-1]] = value


def builtin_defaults() -> dict:
    if DEFAULTS_FILE.is_file():
        with open(DEFAULTS_FILE, "rb") as fh:
            return tomllib.load(fh)
    return _FALLBACK


def load(
    config_path: str | Path | None = None,
    overrides: list[str] = (),
    cwd: str | Path | None = None,
    home: str | Path | None = None,
) -> Config:
    data = builtin_defaults()
    cwd = Path(cwd) if cwd else Path.cwd()
    home = Path(home) if home else Path(os.path.expanduser("~"))

    for candidate in (home / ".config" / "vrstudio" / "default.toml", cwd / "vrstudio.toml"):
        if candidate.is_file():
            with open(candidate, "rb") as fh:
                data = _deep_merge(data, tomllib.load(fh), str(candidate))
    if config_path:
        path = Path(config_path)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        with open(path, "rb") as fh:
            data = _deep_merge(data, tomllib.load(fh), str(path))
    else:
        path = None

    for override in overrides:
        key, value = parse_override(override)
        _set_path(data, key, value)

    _validate(data)
    return Config(data=data, source_path=path)


def _number(data: dict, path: str) -> float:
    node = data
    for part in path.split("."):
        node = node[part]
    try:
        return float(node)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be a number, got {node!r}") from exc


def _validate(data: dict) -> None:
    if int(data.get("schema", 0)) != 1:
        raise ConfigError(f"unsupported config schema {data.get('schema')!r} (expected 1)")

    names = data["pad"]["pipeline"]["transforms"]
    if not isinstance(names, list) or not names:
        raise ConfigError("pad.pipeline.transforms must be a non-empty list")

    from .redraw.blend import WEIGHTS
    from .redraw.mask import STRATEGIES
    from .redraw.pipeline import allowed_names

    for name in names:
        if name not in allowed_names():
            raise ConfigError(f"unknown transform {name!r}; registered: {allowed_names()}")
    if data["mask"]["strategy"] not in STRATEGIES:
        raise ConfigError(f"unknown mask.strategy; choose from {STRATEGIES}")
    if data["recompose"]["blend_weight"] not in WEIGHTS:
        raise ConfigError(f"unknown recompose.blend_weight; choose from {WEIGHTS}")
    if data["recompose"]["d_star_mode"] not in ("clip_median", "clip_mode", "per_frame"):
        raise ConfigError("recompose.d_star_mode must be clip_median | clip_mode | per_frame")
    if data["recompose"]["registration"] not in ("circular_translate", "none"):
        raise ConfigError("recompose.registration must be circular_translate | none")
    if data["recompose"]["boundary_repair"] not in ("off", "feather"):
        raise ConfigError("recompose.boundary_repair must be off | feather")
    if data["pad"]["on_aspect_conflict"] not in ("error", "grow_canvas"):
        raise ConfigError("pad.on_aspect_conflict must be error | grow_canvas")
    if data["video"]["audio"] not in ("copy", "none"):
        raise ConfigError("video.audio must be copy | none")
    if data["video"]["pix_fmt"] != "yuv420p":
        raise ConfigError(
            "video.pix_fmt must be yuv420p: the pipeline has a single "
            "browser-playable profile (4:4:4 archives were dropped in decision 19)"
        )

    band = data["verify"]["interior_band"]
    if not (isinstance(band, list) and len(band) == 2
            and all(isinstance(v, (int, float)) for v in band)
            and 0.0 <= band[0] < band[1] <= 1.0):
        raise ConfigError("verify.interior_band must be [lo, hi] with 0 <= lo < hi <= 1")

    search = _number(data, "recompose.search_pct")
    if not 0.0 < search <= 100.0:
        raise ConfigError("recompose.search_pct must be in (0, 100]")
    if _number(data, "recompose.endpoint_lock_pct") < 0.0:
        raise ConfigError("recompose.endpoint_lock_pct must be >= 0")
    if _number(data, "recompose.boundary_repair_pct") < 0.0:
        raise ConfigError("recompose.boundary_repair_pct must be >= 0")
    min_corr = _number(data, "recompose.min_band_correlation")
    if not -1.0 <= min_corr <= 1.0:
        raise ConfigError("recompose.min_band_correlation must be in [-1, 1]")
    if _number(data, "recompose.max_sample_rows") < 1:
        raise ConfigError("recompose.max_sample_rows must be >= 1")
    if _number(data, "recompose.aspect_drift_warn_pct") < 0.0:
        raise ConfigError("recompose.aspect_drift_warn_pct must be >= 0")
    alpha = _number(data, "recompose.per_frame_ema_alpha")
    if not 0.0 < alpha <= 1.0:
        raise ConfigError("recompose.per_frame_ema_alpha must be in (0, 1]")
    if _number(data, "verify.max_wrap_percentile") > 100.0:
        raise ConfigError("verify.max_wrap_percentile must be <= 100")

    gen = data["generate"]
    if not str(gen["vrgen"]).strip():
        raise ConfigError("generate.vrgen must not be empty")
    if not str(gen["workdir"]).strip():
        raise ConfigError("generate.workdir must not be empty")

    web = data["web"]
    if web["default_frontend"] not in ("next", "legacy"):
        raise ConfigError("web.default_frontend must be next | legacy")
    max_w = web["proxy_max_w"]
    if not isinstance(max_w, int) or isinstance(max_w, bool) or not 256 <= max_w <= 8192:
        raise ConfigError(
            f"web.proxy_max_w must be an int in [256, 8192], got {max_w!r}")

