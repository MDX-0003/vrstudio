"""程序目录、资源目录与数据根的解析规则（决策 102）。"""

from __future__ import annotations

import sys

import pytest

from vrstudio import config as config_mod
from vrstudio import paths as paths_mod
from vrstudio import procenv
from vrstudio.web import tasks as tasks_mod
from vrstudio.config import ConfigError


class _Conf:
    """只回答 ``paths.data_root`` 的最小配置替身。"""

    def __init__(self, value):
        self.value = value

    def get(self, key, default=None):
        return self.value if key == "paths.data_root" else default


def _frozen(tmp_path, monkeypatch):
    """把进程伪装成 PyInstaller 目录式：exe 在 app_dir，代码在 _internal。"""
    app = tmp_path / "VRAIGC"
    internal = app / "_internal"
    internal.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "vrstudio.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    return app, internal


def test_source_checkout_dirs():
    assert not paths_mod.is_frozen()
    # 源码检出时程序目录与资源目录都是仓库根
    assert paths_mod.app_dir() == paths_mod.resource_dir()
    assert (paths_mod.app_dir() / "pyproject.toml").is_file()


def test_frozen_dirs_point_at_app_and_internal(tmp_path, monkeypatch):
    app, internal = _frozen(tmp_path, monkeypatch)

    assert paths_mod.is_frozen()
    assert paths_mod.app_dir() == app
    assert paths_mod.resource_dir() == internal


def test_data_root_defaults_to_app_dir(tmp_path, monkeypatch):
    app, _ = _frozen(tmp_path, monkeypatch)
    monkeypatch.setattr(config_mod, "active", lambda: _Conf(""))

    assert paths_mod.data_root() == app


def test_data_root_absolute_value(tmp_path, monkeypatch):
    _frozen(tmp_path, monkeypatch)
    target = tmp_path / "VRData"
    monkeypatch.setattr(config_mod, "active", lambda: _Conf(str(target)))

    assert paths_mod.data_root() == target


def test_data_root_relative_value_resolves_against_app_dir(tmp_path, monkeypatch):
    app, _ = _frozen(tmp_path, monkeypatch)
    monkeypatch.setattr(config_mod, "active", lambda: _Conf("data"))

    assert paths_mod.data_root() == app / "data"


def test_user_config_dir_uses_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    assert paths_mod.user_config_dir() == tmp_path / "Roaming" / "vrstudio"


def test_config_load_reads_app_dir_and_appdata(tmp_path, monkeypatch):
    app = tmp_path / "VRAIGC"
    app.mkdir()
    (app / "vrstudio.toml").write_text('[paths]\ndata_root = "D:/from-app"\n', encoding="utf-8")
    appdata = tmp_path / "Roaming"
    (appdata / "vrstudio").mkdir(parents=True)
    (appdata / "vrstudio" / "default.toml").write_text(
        '[paths]\ndata_root = "D:/from-appdata"\n', encoding="utf-8")

    monkeypatch.setattr(paths_mod, "app_dir", lambda: app)
    monkeypatch.setattr(paths_mod, "user_config_dir", lambda: appdata / "vrstudio")

    # 程序目录的 vrstudio.toml 优先级更高
    conf = config_mod.load(cwd=tmp_path / "empty", home=tmp_path / "home")
    assert conf.get("paths.data_root") == "D:/from-app"

    (app / "vrstudio.toml").unlink()
    conf = config_mod.load(cwd=tmp_path / "empty", home=tmp_path / "home")
    assert conf.get("paths.data_root") == "D:/from-appdata"


def test_config_accepts_utf8_bom(tmp_path, monkeypatch):
    """记事本另存会带 BOM，tomllib 不接受，加载层要按 utf-8-sig 读。"""
    cfg = tmp_path / "vrstudio.toml"
    cfg.write_text('[paths]\ndata_root = "D:/bom"\n', encoding="utf-8-sig")

    conf = config_mod.load(cwd=tmp_path, home=tmp_path / "home")

    assert conf.get("paths.data_root") == "D:/bom"


def test_config_accepts_ansi_encoded_file(tmp_path):
    """安装器早期版本用 ANSI 写配置；解码要兼容，TOML 合法就照常读。"""
    cfg = tmp_path / "vrstudio.toml"
    cfg.write_bytes(
        "# 安装器写入\n[paths]\ndata_root = \"D:/ansi\"\n".encode("gbk"))

    conf = config_mod.load(cwd=tmp_path, home=tmp_path / "home")

    assert conf.get("paths.data_root") == "D:/ansi"


def test_config_rejects_non_string_data_root(tmp_path):
    with pytest.raises(ConfigError):
        config_mod.load(cwd=tmp_path, home=tmp_path, overrides=["paths.data_root=1"])


def test_vrstudio_argv_frozen_runs_exe_directly(tmp_path, monkeypatch):
    _frozen(tmp_path, monkeypatch)

    assert tasks_mod.vrstudio_argv("generate", "submit") == [
        sys.executable, "generate", "submit"]


def test_vrstudio_argv_source_uses_module_entry(monkeypatch):
    monkeypatch.setattr(paths_mod, "is_frozen", lambda: False)

    assert tasks_mod.vrstudio_argv("serve") == [sys.executable, "-m", "vrstudio", "serve"]


def test_utf8_env_injects_bundled_mglc(tmp_path, monkeypatch):
    app = tmp_path / "VRAIGC"
    tools = app / "tools"
    tools.mkdir(parents=True)
    (tools / "mglc.exe").write_bytes(b"stub")
    monkeypatch.setattr(paths_mod, "app_dir", lambda: app)
    monkeypatch.delenv("VRGEN_MGLC_BIN", raising=False)
    monkeypatch.delenv("VRGEN_BIN_DIR", raising=False)

    env = procenv.utf8_env()

    assert env["VRGEN_MGLC_BIN"] == str(tools / "mglc.exe")
    assert env["PYTHONUTF8"] == "1"


def test_utf8_env_points_vrgen_at_shared_bin(tmp_path, monkeypatch):
    app = tmp_path / "VRAIGC"
    (app / "bin").mkdir(parents=True)
    (app / "bin" / "ffmpeg.exe").write_bytes(b"stub")
    monkeypatch.setattr(paths_mod, "app_dir", lambda: app)
    monkeypatch.delenv("VRGEN_BIN_DIR", raising=False)

    assert procenv.utf8_env()["VRGEN_BIN_DIR"] == str(app / "bin")


def test_utf8_env_keeps_existing_mglc_override(tmp_path, monkeypatch):
    app = tmp_path / "VRAIGC"
    (app / "tools").mkdir(parents=True)
    (app / "tools" / "mglc.exe").write_bytes(b"stub")
    monkeypatch.setattr(paths_mod, "app_dir", lambda: app)
    monkeypatch.setenv("VRGEN_MGLC_BIN", "D:/tools/mglc.exe")

    assert procenv.utf8_env()["VRGEN_MGLC_BIN"] == "D:/tools/mglc.exe"
