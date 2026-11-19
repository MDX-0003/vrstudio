"""``vrstudio config set/show``：按运行形态写入口，只改命中行，不动其它内容。"""

from __future__ import annotations

import pytest

from vrstudio import config as config_mod
from vrstudio import paths as paths_mod
from vrstudio.cli import config_cmd


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    """源码形态的写入口：仓库根的 ``vrstudio.toml``。"""
    target = tmp_path / "repo"
    target.mkdir()
    monkeypatch.setattr(paths_mod, "config_file", lambda: target / "vrstudio.toml")
    monkeypatch.setattr(paths_mod, "app_dir", lambda: target)
    return target


def test_set_port_creates_file(user_dir):
    path = config_cmd.set_value("web.port", "8861")

    assert path == user_dir / "vrstudio.toml"
    assert "[web]" in path.read_text(encoding="utf-8")
    assert "port = 8861" in path.read_text(encoding="utf-8")


def test_set_port_replaces_existing_line_and_keeps_other_sections(user_dir):
    (user_dir / "vrstudio.toml").write_text(
        "[paths]\ndata_root = \"D:/data\"\n\n[web]\nhost = \"127.0.0.1\"\nport = 8760\n",
        encoding="utf-8")

    config_cmd.set_value("web.port", "9000")

    text = (user_dir / "vrstudio.toml").read_text(encoding="utf-8")
    assert "port = 9000" in text
    assert "port = 8760" not in text
    assert 'data_root = "D:/data"' in text
    assert 'host = "127.0.0.1"' in text


def test_set_host_quotes_value(user_dir):
    config_cmd.set_value("web.host", "0.0.0.0")

    assert 'host = "0.0.0.0"' in (user_dir / "vrstudio.toml").read_text(encoding="utf-8")


def test_written_value_is_loaded(user_dir, tmp_path):
    config_cmd.set_value("web.port", "8123")
    conf = config_mod.load(cwd=tmp_path / "empty", home=tmp_path / "home")

    assert conf.get("web.port") == 8123


def test_rejects_unknown_key_and_bad_port(user_dir):
    with pytest.raises(ValueError):
        config_cmd.set_value("web.proxy_max_w", "2048")
    with pytest.raises(ValueError):
        config_cmd.set_value("web.port", "70000")
    with pytest.raises(ValueError):
        config_cmd.set_value("web.port", "abc")


def test_set_data_root_is_quoted(user_dir):
    config_cmd.set_value("paths.data_root", r"D:\VRAIGC-data")
    text = (user_dir / "vrstudio.toml").read_text(encoding="utf-8")
    assert "[paths]" in text
    assert 'data_root = "D:\\VRAIGC-data"' in text


def test_set_data_root_replaces_existing_value(user_dir):
    (user_dir / "vrstudio.toml").write_text(
        '[paths]\ndata_root = "D:/old"\n\n[web]\nport = 8760\n', encoding="utf-8")

    config_cmd.set_value("paths.data_root", "D:/new")

    text = (user_dir / "vrstudio.toml").read_text(encoding="utf-8")
    assert 'data_root = "D:/new"' in text
    assert "D:/old" not in text
    assert "port = 8760" in text
