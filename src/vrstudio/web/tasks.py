"""单通道任务队列（← vrpatch web/tasks.py 迁入，改跑 ``python -m vrstudio``）。

冻结决策：重计算一次只跑一个——8K 任务并行只会互相拖慢（决策 17 串行层）。
每个任务是 CLI 子进程，stdout+stderr 写 logs/ 下的日志文件；控制台用户与
web 用户共享同一条执行路径，捕获的流就是进度源：前端轮询
:meth:`TaskQueue.status`，status 会对日志文件做尾部读取。

多用户扩展点（决策 17，本轮不实现）：Task 预留 ``owner`` 字段位（自报昵称），
submit 增参即可；调度策略已是单队列先进先出，无需改动。
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from ..config import REPO_ROOT
from ..progress import logs_dir

_TAIL_BYTES = 8 * 1024


class Task:
    def __init__(self, kind: str, case: str, argv: list[str]):
        self.kind = kind          # extract | pad | register | submit | adopt | merge | recompose
        self.case = case
        self.argv = argv
        self.state = "queued"     # queued -> running -> done | failed
        self.returncode: int | None = None
        self.started = 0.0
        self.out_path: Path | None = None
        self.proc: subprocess.Popen | None = None

    def _run(self):
        self.out_path = (logs_dir() /
                         f"web_{self.kind}_{self.case}_{time.strftime('%Y%m%d-%H%M%S')}.log")
        with open(self.out_path, "w", encoding="utf-8", newline="") as fh:
            self.proc = subprocess.Popen(
                self.argv, stdout=fh, stderr=subprocess.STDOUT,
                cwd=str(REPO_ROOT))
            self.state = "running"
            self.started = time.time()
            self.returncode = self.proc.wait()
        self.state = "done" if self.returncode == 0 else "failed"

    def tail(self, max_lines: int = 14) -> list[str]:
        if not self.out_path or not self.out_path.exists():
            return []
        with open(self.out_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
        return lines[-max_lines:]


class TaskQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.current: Task | None = None
        self.pending: list[Task] = []
        self.history: list[Task] = []

    def submit(self, kind: str, case: str, argv: list[str]) -> Task:
        with self._lock:
            task = Task(kind, case, argv)
            self.pending.append(task)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._drain, daemon=True)
                self._thread.start()
            return task

    def _drain(self):
        while True:
            with self._lock:
                if not self.pending:
                    return
                task = self.pending.pop(0)
                self.current = task
            task._run()
            with self._lock:
                self.history.append(task)
                self.current = None

    def status(self) -> dict:
        with self._lock:
            cur = self.current
            return {
                "current": None if cur is None else {
                    "kind": cur.kind, "case": cur.case, "state": cur.state,
                    "tail": cur.tail(),
                    "log": str(cur.out_path) if cur.out_path else None,
                },
                "queued": [{"kind": t.kind, "case": t.case} for t in self.pending],
                "last": ([{"kind": t.kind, "case": t.case, "state": t.state,
                           "returncode": t.returncode,
                           "log": str(t.out_path) if t.out_path else None,
                           "tail": t.tail(5)} for t in self.history[-3:]]),
            }


QUEUE = TaskQueue()


def vrstudio_argv(*args: str) -> list[str]:
    """与控制台同一条执行路径：当前解释器 ``-m vrstudio <子命令>``。"""
    return [sys.executable, "-m", "vrstudio", *args]
