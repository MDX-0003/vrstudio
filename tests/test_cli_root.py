"""根命令的默认行为：不带子命令时直接启动控制台（打包版双击即用）。"""

from __future__ import annotations

import typer
import pytest

from vrstudio import cli as cli_mod


class _Ctx:
    """只提供回调用到的 ``invoked_subcommand``。"""

    def __init__(self, invoked: str | None):
        self.invoked_subcommand = invoked


def test_no_subcommand_starts_console(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_mod, "_start_console",
                        lambda host, port, open_browser: calls.append((host, port, open_browser)))

    cli_mod._root(_Ctx(None), version=False, host="0.0.0.0", port=9000, open_browser=True)

    assert calls == [("0.0.0.0", 9000, True)]


def test_subcommand_does_not_start_console(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_mod, "_start_console",
                        lambda host, port, open_browser: calls.append((host, port, open_browser)))

    cli_mod._root(_Ctx("serve"), version=False, host="127.0.0.1", port=8760, open_browser=True)

    assert calls == []


def test_start_console_passes_plain_values(monkeypatch):
    """直接调用 serve 时必须把每个参数都换成真实值，不能漏出 typer.Option 对象。"""
    from vrstudio.cli import serve as serve_cli

    captured = {}

    def fake_serve(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(serve_cli, "serve", fake_serve)

    cli_mod._start_console("127.0.0.1", 8795, False)

    assert set(captured) == {"host", "port", "cases_dir", "log_dir", "log_level",
                             "log_keep_days", "open_browser"}
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8795
    assert captured["open_browser"] is False
    assert isinstance(captured["log_dir"], type(serve_cli.DEFAULT_LOG_DIR))


def test_version_option_exits_without_starting_console(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli_mod, "_start_console",
                        lambda host, port, open_browser: calls.append(1))

    with pytest.raises(typer.Exit):
        cli_mod._root(_Ctx(None), version=True, host="127.0.0.1", port=8760, open_browser=True)

    assert calls == []
    assert "vrstudio " in capsys.readouterr().out
