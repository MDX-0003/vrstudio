"""Config, manifest, report, verify_core, pipeline.

The ``test_builtin_defaults_match_the_fallback`` case is load-bearing: the
authoritative defaults are ``config/default.toml``, and ``_FALLBACK`` only exists
so an installed copy without the repo tree still runs. If they drift, an installed
run and a repo run would silently differ.
"""

from __future__ import annotations

import copy
import json
from fractions import Fraction

import numpy as np
import pytest

from vrstudio import config as config_mod
from vrstudio.config import ConfigError, builtin_defaults, load, parse_override
from vrstudio.procenv import decode_text, utf8_env
from vrstudio.redraw.geometry import PadSpec, plan_pad
from vrstudio.redraw.manifest import Manifest, VideoSettings, from_plan
from vrstudio.redraw.pipeline import TransformContext, apply_chain, build_chain
from vrstudio.redraw.report import BlendReport, RecomposeReport, RegistrationReport, RepairReport
from vrstudio.redraw.verify_core import (
    SeamAccumulator,
    metrics_over_frames,
    seam_metrics,
    verdict,
)

ASPECT = Fraction(21, 9)


# ---------------------------------------------------------- 子进程文本编码
def test_utf8_env_pins_child_stdout_encoding():
    env = utf8_env()
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_decode_text_falls_back_to_local_codepage():
    import locale
    text = "job x: 还没有 Session（记录根 E:\\runs\\x）"
    assert decode_text(text.encode("utf-8")) == text
    assert decode_text(text.encode(locale.getpreferredencoding(False))) == text


# ----------------------------------------------------------------- config
def test_builtin_defaults_match_the_fallback():
    assert builtin_defaults() == config_mod._FALLBACK, (
        "config/default.toml and config._FALLBACK have diverged; they must agree "
        "so an installed copy behaves like the repo"
    )


def test_defaults_load_and_validate():
    conf = load()
    assert conf.get("pad.target_aspect") == "21:9"
    assert conf.get("pad.pipeline.transforms") == ["pre_roll", "pad_h"]
    assert conf.get("recompose.d_star_mode") == "clip_median"
    assert conf.get("bin.allow_path_fallback") is False
    assert conf.get("video.pix_fmt") == "yuv420p"   # 唯一编码档，浏览器可播


def test_override_parsing_types():
    assert parse_override("recompose.endpoint_lock_pct=3") == ("recompose.endpoint_lock_pct", 3)
    assert parse_override("recompose.endpoint_lock_pct=3.5")[1] == 3.5
    assert parse_override("mask.enabled=false")[1] is False
    assert parse_override('pad.target_aspect="16:9"')[1] == "16:9"
    assert parse_override("verify.interior_band=[0.3,0.7]")[1] == [0.3, 0.7]


def test_override_is_applied():
    conf = load(overrides=["recompose.d_star_mode=per_frame", "mask.enabled=false"])
    assert conf.get("recompose.d_star_mode") == "per_frame"
    assert conf.get("mask.enabled") is False


def test_unknown_override_key_is_an_error():
    with pytest.raises(ConfigError, match="unknown config key"):
        load(overrides=["recompose.nope=1"])
    with pytest.raises(ConfigError, match="no such config section"):
        load(overrides=["nope.nope=1"])


def test_override_needs_key_value():
    with pytest.raises(ConfigError, match="key=value"):
        load(overrides=["recompose.d_star_mode"])


def test_validation_rejects_bad_values(tmp_path):
    def write(text: str):
        p = tmp_path / "c.toml"
        p.write_text(text, encoding="utf-8")
        return p

    with pytest.raises(ConfigError, match="unknown transform"):
        load(write("[pad.pipeline]\ntransforms = [\"nope\"]\n"))
    with pytest.raises(ConfigError, match="interior_band"):
        load(write("[verify]\ninterior_band = [0.9, 0.1]\n"))
    with pytest.raises(ConfigError, match="search_pct"):
        load(write("[recompose]\nsearch_pct = 0.0\n"))
    with pytest.raises(ConfigError, match="on_aspect_conflict"):
        load(write("[pad]\non_aspect_conflict = \"whatever\"\n"))
    with pytest.raises(ConfigError, match="yuv420p"):
        load(write("[video]\npix_fmt = \"yuv444p\"\n"))


def test_config_file_missing_is_an_error():
    with pytest.raises(ConfigError, match="not found"):
        load("definitely-not-here.toml")


def test_need_reports_missing_key():
    conf = load()
    with pytest.raises(ConfigError, match="missing config key"):
        conf.need("nope.nope")


def test_missing_config_key_is_a_config_error():
    """代码与默认值不再一致时，缺键要报 ConfigError，不能漏出裸 KeyError。

    2026-10-27 的事故就是旧进程的 ``_validate`` 用下标读一个已被删掉的键：
    ``KeyError: 'default_frontend'`` 从请求里冒出来，而不是一条配置错误。
    """
    data = copy.deepcopy(builtin_defaults())
    del data["web"]["proxy_max_w"]
    with pytest.raises(ConfigError, match="missing config key"):
        config_mod._validate(data)


def test_active_config_is_loaded_once(monkeypatch):
    """装配之后再读 ``active()`` 不再碰磁盘：请求路径不逐次读盘与校验。"""
    conf = load()
    config_mod.activate(conf)
    monkeypatch.setattr(config_mod, "load",
                        lambda *args, **kwargs: pytest.fail("active() 不应重新加载配置"))
    assert config_mod.active() is conf


def test_upload_limit_is_not_configurable(tmp_path):
    """上传上限是产品级常量，写进配置要报未知键。

    曾经有一个 ``web.upload_max_bytes`` 读取分支，但键不在默认值里，而合并只允许
    覆盖已有键，于是它永远走内置默认——只能读、不能设的死键。删除读取分支后，
    这里钉住「它确实不是配置项」。
    """
    path = tmp_path / "c.toml"
    path.write_text("[web]\nupload_max_bytes = 1048576\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown config key"):
        load(path)


# ---------------------------------------------------------- manifest
def _manifest(tmp_path, frame, **kw):
    path = tmp_path / "src.mp4"
    path.write_bytes(b"not really a video but hashable")
    plan = plan_pad(PadSpec(frame.shape[:2][::-1], ASPECT))
    defaults = dict(
        source_path=path, source_frames=120, source_fps="30/1", source_pix_fmt="yuv420p",
        has_audio=True, write_hash=True, mask_path="src_mask.png",
        mask_strategy="redundant_white", mask_feather_px=0, mask_png_compression=0,
        restore_interp="lanczos4",
        video=VideoSettings("libx264", 18, "veryfast", "yuv420p", "copy", "copy"),
        tool_version="0.1.0",
    )
    defaults.update(kw)
    return plan, from_plan(plan, **defaults)


def test_manifest_round_trip(tmp_path, erp):
    plan, man = _manifest(tmp_path, erp)
    out = man.save(tmp_path / "m.json")
    back = Manifest.load(out)
    assert back.canvas == man.canvas
    assert back.source == man.source
    assert back.video == man.video
    assert back.to_dict()["tool"]["name"] == "vrstudio"


def test_manifest_rebuilds_the_same_plan(tmp_path, erp):
    plan, man = _manifest(tmp_path, erp)
    back = man.to_pad_plan()          # re-runs assert_closure
    assert back.canvas_size == plan.canvas_size
    assert back.P == plan.P and back.L == plan.L
    assert back.regions == plan.regions
    assert back.wrap_pair == plan.wrap_pair


def test_manifest_rejects_a_future_schema(tmp_path, erp):
    _, man = _manifest(tmp_path, erp)
    raw = man.to_dict()
    raw["schema"] = 99
    path = tmp_path / "m.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        Manifest.load(path)


def test_manifest_hash_check_catches_a_mismatched_clip(tmp_path, erp):
    _, man = _manifest(tmp_path, erp)
    other = tmp_path / "other.mp4"
    other.write_bytes(b"a different clip entirely")
    with pytest.raises(ValueError, match="hash mismatch"):
        man.check_source_hash(other)
    man.check_source_hash(tmp_path / "src.mp4")     # the right one passes


def test_manifest_survives_a_missing_tool_block(tmp_path, erp):
    _, man = _manifest(tmp_path, erp)
    raw = man.to_dict()
    raw.pop("tool")
    raw["tool"] = {}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert Manifest.load(path).tool_version == ""


# ---------------------------------------------------------- report
def test_report_round_trip(tmp_path):
    report = RecomposeReport(
        manifest_path="m.json", input_path="gen.mp4", input_size=(2520, 1080),
        input_frames=600, input_fps="60/1", restored_canvas=(4480, 1920),
        restore_interp="lanczos4",
        registration=RegistrationReport(
            method="circular_translate", search_px=160, d_star=3,
            d_star_mode="clip_median", frames_used=600, mae_min=4.12, mae_second=6.8,
            quality=1.65, correlation=0.97, interior_baseline=1.1, verdict="ok"),
        blend=BlendReport("smoothstep", 12), repair=RepairReport(False, 0),
        verify={"wrap": 0.31}, output_path="out.mp4", output_frames=600,
    )
    path = report.save(tmp_path / "r.json")
    back = RecomposeReport.load(path)
    assert back.registration.d_star == 3
    assert back.registration.correlation == 0.97
    assert back.registration.verdict == "ok"
    assert back.verify["wrap"] == 0.31


def test_report_rejects_a_future_schema(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"schema": 42}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        RecomposeReport.load(path)


# -------------------------------------------------------- verify core
def test_seam_metrics_on_a_clean_frame(erp):
    m = seam_metrics(erp)
    assert m.width == erp.shape[1]
    # the fixture is periodic in x, so the wrap is an ordinary adjacency, not an outlier
    assert m.wrap < m.interior_max
    assert m.wrap < m.interior_p99
    assert m.wrap_percentile < 95


def test_seam_metrics_flags_a_broken_wrap():
    """A monotonic ramp has one 255-level step, and it is at the wrap."""
    W = 384
    ramp = np.tile(np.linspace(0.0, 255.0, W), (64, 1))
    m = seam_metrics(ramp)
    assert m.wrap_percentile > 99
    assert m.wrap > m.interior_max
    assert m.wrap_over_worst > 100


def test_rolling_hides_the_wrap_but_verify_still_fails_it(erp):
    """The anti-gaming property, and the reason `verify` needs a `--before`.

    A circular shift moves a broken wrap into the middle of the raster, so the
    wrap *metric* collapses and a percentile-only acceptance check would pass a
    still-broken video. The worst-interior comparison against `before` catches it.
    """
    W = erp.shape[1]
    ramp = np.tile(np.linspace(0.0, 255.0, W), (64, 1))

    before = seam_metrics(erp)                       # the clean source
    broken = seam_metrics(ramp)
    assert broken.wrap_percentile > 99
    assert verdict(broken, before=before)[0] == "bad"

    rolled = seam_metrics(np.roll(ramp, W // 2, axis=1))

    # the wrap number collapses, and by the percentile rule alone this would pass...
    assert rolled.wrap < broken.wrap / 10
    assert rolled.wrap_percentile < 95
    # ...but the discontinuity did not go anywhere
    assert rolled.interior_max > 200
    status, reasons = verdict(rolled, before=before)
    assert status == "bad"
    assert any("worst interior" in r for r in reasons)


def test_verdict_ok_when_nothing_regressed(erp):
    status, reasons = verdict(seam_metrics(erp), before=seam_metrics(erp))
    assert status == "ok" and reasons == []


def test_verdict_warns_on_percentile_alone(erp):
    import dataclasses

    m = seam_metrics(erp)
    rough = dataclasses.replace(m, wrap=m.interior_max * 0.5, wrap_percentile=99.0)
    status, reasons = verdict(rough, before=m)
    assert status == "warn"
    assert any("percentile" in r for r in reasons)


def test_verdict_without_a_before_uses_the_worst_pair(erp):
    W = 384
    ramp = np.tile(np.linspace(0.0, 255.0, W), (64, 1))
    status, reasons = verdict(seam_metrics(ramp))
    assert status == "bad"
    assert any("worst interior" in r for r in reasons)


def test_wrap_metric_is_a_single_column_pair_not_a_band(erp):
    """Why there is no edge_band_px: a reversed band pairs col W-1-k with col k.

    For k > 0 those columns are almost a full revolution apart, so a band-averaged
    "wrap" is dominated by unrelated content and stops being comparable with the
    interior distribution the percentile is taken over. On the clean fixture the
    two definitions disagree by 7x.
    """
    from vrstudio.redraw.registration import to_gray

    g = to_gray(erp)
    W = g.shape[1]
    single = float(np.abs(g[:, 0] - g[:, W - 1]).mean())
    band = float(np.abs(g[:, -8:][:, ::-1] - g[:, :8]).mean())
    assert band > 5.0 * single
    assert seam_metrics(erp).wrap == pytest.approx(single, rel=1e-6)


def test_accumulator_agrees_with_single_frame_metrics(erp):
    acc = SeamAccumulator(erp.shape[1])
    for _ in range(3):
        acc.add(erp)
    assert acc.result().wrap == pytest.approx(seam_metrics(erp).wrap, rel=1e-9)
    assert acc.result().interior_max == pytest.approx(seam_metrics(erp).interior_max, rel=1e-9)


def test_accumulator_tracks_the_running_maximum(erp):
    """interior_max is a max over frames, not a mean — a one-frame artifact must show."""
    W = erp.shape[1]
    spike = erp.copy()
    spike[:, W // 3] = 255
    acc = SeamAccumulator(W)
    acc.add(erp)
    acc.add(spike)
    assert acc.result().interior_max > seam_metrics(erp).interior_max


def test_metrics_over_frames_matches_the_accumulator(erp):
    a = metrics_over_frames([erp, erp, erp])
    assert a.width == erp.shape[1]


def test_accumulator_rejects_a_width_change(erp):
    acc = SeamAccumulator(erp.shape[1])
    with pytest.raises(ValueError, match="width"):
        acc.add(erp[:, :-2])


def test_accumulator_requires_frames():
    with pytest.raises(ValueError, match="no frames"):
        SeamAccumulator(64).result()
    with pytest.raises(ValueError, match="width >= 4"):
        SeamAccumulator(3)


# ------------------------------------------------------------- pipeline
def test_chain_order_is_config_order(erp):
    plan = plan_pad(PadSpec(erp.shape[:2][::-1], ASPECT))
    ctx = TransformContext(plan=plan)
    out = apply_chain(erp, build_chain(["pre_roll", "pad_h"]), ctx)
    assert out.shape[1] == plan.Wc

    # reversed order is a different (and here, wrong) operation: pad then roll
    other = apply_chain(erp, build_chain(["pad_h", "pre_roll"]), ctx)
    assert other.shape == out.shape
    assert not np.array_equal(out, other)


def test_unknown_transform_names_the_offender():
    with pytest.raises(ValueError, match="unknown transform"):
        build_chain(["pad_h", "burn_marker"])


# --------------------------------------------------------------- encoding
def test_gop_frames_pins_keyframes_half_a_second_apart():
    from vrstudio.media.ffmpeg import KEYFRAME_INTERVAL_S, gop_frames

    assert KEYFRAME_INTERVAL_S == 0.5
    assert gop_frames(60) == 30
    assert gop_frames(30) == 15
    assert gop_frames(10) == 5
    assert gop_frames("30000/1001") == 15
    assert gop_frames(0) == 250          # 读不到 fps 时退回 x264 默认
