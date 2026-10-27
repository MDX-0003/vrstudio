"""端口被本项目旧实例占用时的接管。

serve 启动时若端口已被监听，检查占用进程是否属于本项目（exe 或命令行
落在当前 venv 之下；uv run 的跳板会让 exe 指向基础解释器，所以命令行
也要查）：是则判定为残留实例，打印控制台提示后强制结束；否则只报告
一行，交由后续 bind 自然失败——不碰与本项目无关的进程。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psutil


def _project_roots() -> list[Path]:
    """本项目进程的判定基准：当前解释器所在的 venv（含其 Scripts）。"""
    roots = {Path(sys.prefix).resolve()}
    roots.add(Path(sys.executable).resolve().parent)
    return sorted(roots)


def _under_roots(text: str) -> bool:
    try:
        path = Path(text).resolve()
    except (OSError, ValueError):
        return False
    return any(path.is_relative_to(root) for root in _project_roots())


def is_our_process(proc: psutil.Process) -> bool:
    try:
        if _under_roots(proc.exe() or ""):
            return True
        # uv run 的 venv 跳板：exe 是基础解释器，但命令行引用 venv 内的入口。
        return any(_under_roots(arg) for arg in proc.cmdline())
    except (psutil.Error, OSError, ValueError):
        return False


def reclaim_port(port: int, *, wait_s: float = 5.0) -> list[int]:
    """结束监听 ``port`` 且属于本项目的进程，返回已结束的 pid 列表。

    打印先于结束动作，让启动者在控制台直接看到接管了谁。
    """
    seen: set[int] = set()
    victims: list[tuple[int, str]] = []
    me = os.getpid()
    for conn in psutil.net_connections(kind="inet"):
        if conn.status != psutil.CONN_LISTEN or not conn.laddr:
            continue
        if conn.laddr.port != port or not conn.pid or conn.pid in seen or conn.pid == me:
            continue
        seen.add(conn.pid)
        try:
            proc = psutil.Process(conn.pid)
        except psutil.Error:
            continue
        if is_our_process(proc):
            victims.append((conn.pid, _describe(proc)))

    log = logging.getLogger("vrstudio.serve")
    killed: list[int] = []
    for pid, describe in victims:
        message = (f"端口 {port} 已被本项目的旧实例占用：PID {pid}（{describe}），"
                   f"自动结束它以便本次启动接管。")
        print(f"  {message}", flush=True)
        log.warning("serve.port_reclaim", extra={"data": {
            "event": "serve.port_reclaim", "port": port, "pid": pid,
            "describe": describe,
        }})
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=wait_s)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=wait_s)
            killed.append(pid)
        except psutil.Error as exc:
            print(f"  结束 PID {pid} 失败：{exc}；本次启动可能仍会绑定失败。")
    return killed


def _describe(proc: psutil.Process) -> str:
    try:
        cmdline = " ".join(proc.cmdline())
    except psutil.Error:
        cmdline = ""
    return cmdline or proc.exe() or f"pid {proc.pid}"
