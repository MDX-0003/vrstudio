"""``vrstudio config set/show``：只改 web.host 与 web.port，不动文件其它内容。"""

from __future__ import annotations

import pytest

from vrstudio import config as config_mod
from vrstudio import paths as paths_mod
from vrstudio.cli import config_cmd


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    target = tmp_path / "userconfig"
    monkeypatch.setattr(paths_mod, "user_config_dir", lambda: target)
    return target


def test_set_port_creates_file(user_dir):
    path = config_cmd.set_value("web.port", "8861")

    assert path == user_dir / "default.toml"
    assert "[web]" in path.read_text(encoding="utf-8")
    assert "port = 8861" in path.read_text(encoding="utf-8")


def test_set_port_replaces_existing_line_and_keeps_other_sections(user_dir):
    user_dir.mkdir(parents=True)
    (user_dir / "default.toml").write_text(
        "[paths]\ndata_root = \"D:/data\"\n\n[web]\nhost = \"127.0.0.1\"\nport = 8760\n",
        encoding="utf-8")

    config_cmd.set_value("web.port", "9000")

    text = (user_dir / "default.toml").read_text(encoding="utf-8")
    assert "port = 9000" in text
    assert "port = 8760" not in text
    assert 'data_root = "D:/data"' in text
    assert 'host = "127.0.0.1"' in text


def test_set_host_quotes_value(user_dir):
    config_cmd.set_value("web.host", "0.0.0.0")

    assert 'host = "0.0.0.0"' in (user_dir / "default.toml").read_text(encoding="utf-8")


def test_written_value_is_loaded(user_dir, tmp_path, monkeypatch):
    config_cmd.set_value("web.port", "8123")
    monkeypatch.setattr(paths_mod, "app_dir", lambda: tmp_path / "app")
    conf = config_mod.load(cwd=tmp_path / "empty", home=tmp_path / "home")

    assert conf.get("web.port") == 8123


def test_rejects_unknown_key_and_bad_port(user_dir):
    with pytest.raises(ValueError):
        config_cmd.set_value("web.proxy_max_w", "2048")
    with pytest.raises(ValueError):
        config_cmd.set_value("web.port", "70000")
    with pytest.raises(ValueError):
        config_cmd.set_value("web.port", "abc")
