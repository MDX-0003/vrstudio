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

``serve`` keeps one config for the whole process: :func:`activate` loads and
validates it at startup, and the request path only reads :func:`active`. Editing
a config file therefore cannot put a running process on a config its code does
not understand — the file and the code change together, at restart.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .procenv import decode_text

REPO_ROOT = paths.app_dir()
DEFAULTS_FILE = paths.resource_dir() / "config" / "default.toml"

#: Safety net for installs without the repo tree; kept in sync by a test.
_FALLBACK: dict = {
    "schema": 1,
    # 可写数据根：cases/sources/vrgen_runs/logs 都在它下面；空表示跟随程序目录。
    # 安装版由安装器写成非系统盘绝对路径（决策 102）。
    "paths": {"data_root": ""},
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
        "gate_max_frames": 48,
        "aspect_drift_warn_pct": 2.0,
        # 决策 69：0.80 -> 0.60。放宽后闸门只证明"两端足够像"，
        # 高对比接缝的重影风险交给人工看片。
        "min_band_correlation": 0.60,
        "warn_quality": 1.15,
    },
    "verify": {
        "interior_band": [0.25, 0.75],
        "max_wrap_percentile": 95,
        "worst_interior_tol": 1.0,
    },
    "video": {
        "encoder": "libx265",
        "crf": 14,
        "preset": "slow",
        "pix_fmt": "yuv420p10le",
        "audio": "copy",
        "metadata": "copy",
    },
    "manifest": {"path_template": "{stem}_pad.json", "write_hash": True},
    "generate": {
        "vrgen": "vrgen",        # vrgen CLI 的可执行文件（跨仓库 CLI 契约，绝对路径最稳）
        "workdir": "vrgen_runs",  # vrgen 台账根，相对仓库根（gitignore）
        "image_upload_max_side": 4096,  # 全幅提交的上传副本长边上限（平台拒绝超大 PNG）
        "image_max_refs": 13,    # 图片模型单次参考图上限（mglc 模型 44 的 maxReferenceCount）
    },
    "web": {
        "proxy_max_w": 4096,         # 预览代理宽度上限（选区全景 + 对比页两路）
        "host": "127.0.0.1",         # 双击启动时的监听地址；安装版可改成 0.0.0.0
        "port": 8760,                # 双击启动时的端口；用 vrstudio config set web.port 改
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


def _read_text(path: Path) -> str:
    """读配置文件：优先 UTF-8（含 BOM），失败再按本机代码页。

    安装器与记事本都可能写出非 UTF-8 的文件（决策 102 的安装器早期版本就是 ANSI），
    这里宽进严出：解码尽量兼容，TOML 本身是否合法仍由 tomllib 判定。
    """
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return decode_text(data)


def builtin_defaults() -> dict:
    if DEFAULTS_FILE.is_file():
        # utf-8-sig：手工用记事本另存时可能带 BOM，tomllib 不接受 BOM。
        return tomllib.loads(_read_text(DEFAULTS_FILE))
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

    for candidate in (
            home / ".config" / "vrstudio" / "default.toml",
            paths.user_config_dir() / "default.toml",
            paths.app_dir() / "vrstudio.toml",
            cwd / "vrstudio.toml"):
        if candidate.is_file():
            data = _deep_merge(data, tomllib.loads(_read_text(candidate)), str(candidate))
    if config_path:
        path = Path(config_path)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        data = _deep_merge(data, tomllib.loads(_read_text(path)), str(path))
    else:
        path = None

    for override in overrides:
        key, value = parse_override(override)
        _set_path(data, key, value)

    _validate(data)
    return Config(data=data, source_path=path)


#: 进程运行期配置：``serve`` 启动时装配一次，请求路径只读。
_active: Config | None = None


def activate(conf: Config | None = None) -> Config:
    """装配进程运行期配置并返回它。

    不传 ``conf`` 时按当前环境加载并校验，所以配置错误在启动期暴露，而不是
    等到某个请求里变成 500。装配之后再读 :func:`active` 就不再碰磁盘：配置
    文件与代码在启动时同时定格，单独编辑配置文件不会让运行中的进程拿到一套
    与代码不匹配的配置。
    """
    global _active
    _active = load() if conf is None else conf
    return _active


def active() -> Config:
    """当前进程的配置；尚未装配时按需装配一次（单测直接调用辅助函数的情形）。"""
    return _active if _active is not None else activate()


def _table(data: dict, path: str) -> dict:
    """取一个配置表；缺表或不是表报 ConfigError，不用裸 KeyError。"""
    node: object = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"missing config table {path!r}")
        node = node[part]
    if not isinstance(node, dict):
        raise ConfigError(f"config key {path!r} must be a table")
    return node


def _value(node: dict, key: str, where: str):
    """取表里的一个值；缺键报 ConfigError。"""
    if key not in node:
        raise ConfigError(f"missing config key {where!r}")
    return node[key]


def _number(data: dict, path: str) -> float:
    node: object = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"missing config key {path!r}")
        node = node[part]
    try:
        return float(node)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be a number, got {node!r}") from exc


def _validate(data: dict) -> None:
    if int(data.get("schema", 0)) != 1:
        raise ConfigError(f"unsupported config schema {data.get('schema')!r} (expected 1)")

    pad = _table(data, "pad")
    mask = _table(data, "mask")
    recompose = _table(data, "recompose")
    verify = _table(data, "verify")
    video = _table(data, "video")
    generate = _table(data, "generate")
    web = _table(data, "web")
    paths_table = _table(data, "paths")

    data_root = _value(paths_table, "data_root", "paths.data_root")
    if not isinstance(data_root, str):
        raise ConfigError(f"paths.data_root must be a string, got {data_root!r}")

    names = _value(_table(pad, "pipeline"), "transforms", "pad.pipeline.transforms")
    if not isinstance(names, list) or not names:
        raise ConfigError("pad.pipeline.transforms must be a non-empty list")

    from .redraw.blend import WEIGHTS
    from .redraw.mask import STRATEGIES
    from .redraw.pipeline import allowed_names

    for name in names:
        if name not in allowed_names():
            raise ConfigError(f"unknown transform {name!r}; registered: {allowed_names()}")
    if _value(mask, "strategy", "mask.strategy") not in STRATEGIES:
        raise ConfigError(f"unknown mask.strategy; choose from {STRATEGIES}")
    if _value(recompose, "blend_weight", "recompose.blend_weight") not in WEIGHTS:
        raise ConfigError(f"unknown recompose.blend_weight; choose from {WEIGHTS}")
    if _value(recompose, "d_star_mode", "recompose.d_star_mode") not in (
            "clip_median", "clip_mode", "per_frame"):
        raise ConfigError("recompose.d_star_mode must be clip_median | clip_mode | per_frame")
    if _value(recompose, "registration", "recompose.registration") not in (
            "circular_translate", "none"):
        raise ConfigError("recompose.registration must be circular_translate | none")
    if _value(recompose, "boundary_repair", "recompose.boundary_repair") not in (
            "off", "feather"):
        raise ConfigError("recompose.boundary_repair must be off | feather")
    if _value(pad, "on_aspect_conflict", "pad.on_aspect_conflict") not in (
            "error", "grow_canvas"):
        raise ConfigError("pad.on_aspect_conflict must be error | grow_canvas")
    if _value(video, "audio", "video.audio") not in ("copy", "none"):
        raise ConfigError("video.audio must be copy | none")
    if _value(video, "pix_fmt", "video.pix_fmt") != "yuv420p10le":
        raise ConfigError(
            "video.pix_fmt must be yuv420p10le: the pipeline has a single "
            "10-bit delivery profile (8-bit was retired in decision 99; the "
            "browser proxy is the only 8-bit artifact and has its own constant)"
        )

    band = _value(verify, "interior_band", "verify.interior_band")
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

    if not str(_value(generate, "vrgen", "generate.vrgen")).strip():
        raise ConfigError("generate.vrgen must not be empty")
    if not str(_value(generate, "workdir", "generate.workdir")).strip():
        raise ConfigError("generate.workdir must not be empty")

    max_refs = _value(generate, "image_max_refs", "generate.image_max_refs")
    if not isinstance(max_refs, int) or isinstance(max_refs, bool) or max_refs < 1:
        raise ConfigError(
            f"generate.image_max_refs must be a positive int, got {max_refs!r}")

    max_w = _value(web, "proxy_max_w", "web.proxy_max_w")
    if not isinstance(max_w, int) or isinstance(max_w, bool) or not 256 <= max_w <= 8192:
        raise ConfigError(
            f"web.proxy_max_w must be an int in [256, 8192], got {max_w!r}")

    host = _value(web, "host", "web.host")
    if not isinstance(host, str) or not host.strip():
        raise ConfigError(f"web.host must be a non-empty string, got {host!r}")
    port = _value(web, "port", "web.port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError(f"web.port must be an int in [1, 65535], got {port!r}")

