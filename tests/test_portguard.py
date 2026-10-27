"""serve 启动时的端口接管：只清本项目 exe/cmdline 的监听进程，外人不碰。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from vrstudio.web import portguard
from vrstudio.web.portguard import _under_roots, is_our_process, reclaim_port

OUR_EXE = str(Path(sys.executable))
OUR_ENTRY = str(Path(sys.prefix) / "Scripts" / "vrstudio.exe")
LISTEN = 1


class _FakeProc:
    def __init__(self, pid: int, exe: str, cmdline: list[str]):
        self.pid = pid
        self._exe = exe
        self._cmdline = cmdline
        self.dead = False

    def exe(self):
        return self._exe

    def cmdline(self):
        return self._cmdline

    def terminate(self):
        self.dead = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.dead = True


class _Conn:
    def __init__(self, pid: int, port: int):
        self.pid = pid
        self.status = LISTEN
        self.laddr = types.SimpleNamespace(port=port)


def _fake_psutil(monkeypatch, conns, processes):
    real = pytest.importorskip("psutil")
    fake = types.SimpleNamespace(
        CONN_LISTEN=LISTEN,
        Error=real.Error,
        TimeoutExpired=real.TimeoutExpired,
        net_connections=lambda kind: conns,
        Process=lambda pid: processes[pid] if pid in processes
        else (_ for _ in ()).throw(real.Error("gone")),
    )
    monkeypatch.setattr(portguard, "psutil", fake)


def test_under_roots_matches_venv_and_rejects_outside():
    assert _under_roots(OUR_EXE)
    assert _under_roots(OUR_ENTRY)
    outside = Path(Path(sys.executable).anchor) / "elsewhere" / "python.exe"
    assert not _under_roots(str(outside))
    assert not _under_roots("")
    assert not _under_roots("not-a-path-\x00bad")


def test_is_our_process_via_cmdline_trampoline():
    # uv run 的跳板：exe 是基础解释器，命令行引用 venv 内入口。
    trampoline = _FakeProc(1, r"C:\uv\cpython-3.11\python.exe",
                           [r"C:\uv\cpython-3.11\python.exe", OUR_ENTRY, "serve"])
    assert is_our_process(trampoline)
    foreign = _FakeProc(2, r"C:\uv\cpython-3.11\python.exe",
                        [r"C:\uv\cpython-3.11\python.exe", "-m", "http.server"])
    assert not is_our_process(foreign)


def test_reclaim_kills_only_our_listener(monkeypatch, capsys):
    processes = {
        101: _FakeProc(101, OUR_EXE, [OUR_EXE, "serve"]),
        102: _FakeProc(102, r"C:\other\python.exe", ["serve"]),
        103: _FakeProc(103, r"D:\tools\nginx.exe", ["nginx"]),
    }
    conns = [
        _Conn(101, 8760), _Conn(102, 8760), _Conn(103, 8760),
        _Conn(101, 9999),  # 同 pid 去重
    ]
    _fake_psutil(monkeypatch, conns, processes)

    killed = reclaim_port(8760)

    assert killed == [101]
    assert processes[101].dead and not processes[102].dead and not processes[103].dead
    out = capsys.readouterr().out
    assert "PID 101" in out and "自动结束" in out


def test_reclaim_skips_foreign_port_holder(monkeypatch, capsys):
    processes = {201: _FakeProc(201, r"D:\tools\nginx.exe", ["nginx"])}
    _fake_psutil(monkeypatch, [_Conn(201, 8760)], processes)

    assert reclaim_port(8760) == []
    assert not processes[201].dead
    assert capsys.readouterr().out == ""


def test_reclaim_ignores_self(monkeypatch):
    processes = {1: _FakeProc(1, OUR_EXE, [OUR_EXE, "serve"])}
    _fake_psutil(monkeypatch, [_Conn(1, 8760)], processes)
    monkeypatch.setattr(portguard.os, "getpid", lambda: 1)

    assert reclaim_port(8760) == []
