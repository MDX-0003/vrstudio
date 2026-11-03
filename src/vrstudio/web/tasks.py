"""单通道任务队列（← vrpatch web/tasks.py 迁入，改跑 ``python -m vrstudio``）。

冻结决策：重计算一次只跑一个——8K 任务并行只会互相拖慢（决策 17 串行层）。
每个任务是 CLI 子进程，stdout+stderr 写 logs/ 下的日志文件；控制台用户与
web 用户共享同一条执行路径，捕获的流就是进度源：前端轮询
:meth:`TaskQueue.status`，status 会对日志文件做尾部读取。

多用户扩展点（决策 17，本轮不实现）：Task 预留 ``owner`` 字段位（自报昵称），
submit 增参即可；调度策略已是单队列先进先出，无需改动。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from ..config import REPO_ROOT
from ..procenv import decode_text, utf8_env
from ..progress import logs_dir
from .logging import TASK_LOGGER

_TAIL_BYTES = 8 * 1024
#: 复用标记在日志开头（提示词与命令行之前）；读到这一段就够。
_HEAD_BYTES = 256 * 1024
_REUSED_LINE = re.compile(
    r"^reused\s+(?P<round>\S+)\s+reason=(?P<reason>key|resumed)\s+"
    r"task=(?P<task>\d+|-)(?=\s|$)")
log = logging.getLogger(TASK_LOGGER)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _task_log_path(task_id: str, kind: str, case: str) -> Path:
    day = time.strftime("%Y%m%d")
    directory = logs_dir() / "tasks" / day
    directory.mkdir(parents=True, exist_ok=True)
    safe_case = re.sub(r"[^\w.-]", "_", case)
    return directory / f"{task_id}_{kind}_{safe_case}.log"


def _history_path() -> Path:
    """任务历史索引；日志正文仍在各自的 .log 文件里，这里只记摘要与路径。"""
    directory = logs_dir() / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "history.jsonl"


def _reused_marker(path: Path | None) -> dict | None:
    """vrgen 的 ``reused`` 行 → 「这一轮没花额度」的机器可读事实。

    契约见 ``docs/case-model.md`` 的 submit 输出段；标记在日志开头，所以只读头部。
    """
    if path is None or not path.is_file():
        return None
    try:
        with open(path, "rb") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return None
    for line in decode_text(head).splitlines():
        match = _REUSED_LINE.match(line)
        if match:
            raw = match.group("task")
            return {
                "round": match.group("round"),
                "reason": match.group("reason"),
                "task_id": None if raw == "-" else int(raw),
            }
    return None


class Task:
    def __init__(self, kind: str, case: str, argv: list[str],
                 follow_up: list[tuple[str, str, list[str]]] | None = None,
                 owner=None, request_id: str | None = None,
                 billing: dict | None = None):
        self.kind = kind          # extract | pad | register | submit | adopt
                                  # | merge | assemble | recompose
        self.case = case
        self.argv = argv
        #: 成功后自动排到队首的后续任务（如 merge → assemble）：各自独立日志与重试。
        self.follow_up = list(follow_up or [])
        self.state = "queued"     # queued -> running -> done | failed
        self.returncode: int | None = None
        self.id = uuid.uuid4().hex[:12]
        self.owner_id = getattr(owner, "id", None) if owner is not None else None
        self.owner_name = (getattr(owner, "display_name", None)
                           if owner is not None else None)
        self.billing = dict(billing or {})
        self.request_id = request_id
        self.created_at = _now()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.progress = None
        self.error: str | None = None
        #: 复用已有轮次（本次未提交、未花费）；字段见 _reused_marker。
        self.reused: dict | None = None
        self.submitted = time.strftime("%m-%d %H:%M:%S")   # 用户点按钮的时刻
        self.started = 0.0
        self.out_path: Path | None = None
        self.proc: subprocess.Popen | None = None

    def _run(self):
        self.out_path = _task_log_path(self.id, self.kind, self.case)
        log.info("task.start", extra={"data": {
            "event": "task.start", "task_id": self.id, "kind": self.kind,
            "case": self.case, "owner_id": self.owner_id,
            "user_id": self.owner_id, "op": self.kind,
            "request_id": self.request_id, "log": str(self.out_path),
            "log_ref": str(self.out_path),
        }})
        started = time.monotonic()
        with open(self.out_path, "w", encoding="utf-8", newline="") as fh:
            env = utf8_env()
            env["VRSTUDIO_CLI_LOG_PATH"] = str(self.out_path)
            self.proc = subprocess.Popen(
                self.argv, stdout=fh, stderr=subprocess.STDOUT,
                cwd=str(REPO_ROOT), env=env)
            self.state = "running"
            self.started = time.time()
            self.started_at = _now()
            self.returncode = self.proc.wait()
        if self.kind == "submit":
            self.reused = _reused_marker(self.out_path)
        self.state = "done" if self.returncode == 0 else "failed"
        self.finished_at = _now()
        data = {
            "event": "task.finish", "task_id": self.id, "kind": self.kind,
            "case": self.case, "owner_id": self.owner_id,
            "user_id": self.owner_id, "op": self.kind,
            "request_id": self.request_id, "rc": self.returncode,
            "ms": round((time.monotonic() - started) * 1000, 1),
            "log": str(self.out_path), "log_ref": str(self.out_path),
        }
        if self.returncode != 0:
            tail = self.tail(5)
            self.error = tail[-1] if tail else None
            data["error"] = self.error
        log.log(logging.INFO if self.returncode == 0 else logging.ERROR,
                "task.finish", extra={"data": data})

    def view(self, position: int | None = None) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "case": self.case,
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
            "project_id": self.billing.get("project_id"),
            "episode_id": self.billing.get("episode_id"),
            "session_id": self.billing.get("session_id"),
            "status": self.state,
            "queue_position": position,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress": self.progress,
            "log_path": str(self.out_path) if self.out_path else None,
            "error": self.error,
            "reused": self.reused,
            "request_id": self.request_id,
            "tail": self.tail(8),
        }

    def tail(self, max_lines: int = 14) -> list[str]:
        if not self.out_path or not self.out_path.exists():
            return []
        # 失败时错误行可能远在提示词/命令行之前：窗口放大到 256KB 再找。
        window = 256 * 1024 if self.state == "failed" else _TAIL_BYTES
        with open(self.out_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - window))
            lines = decode_text(f.read()).splitlines()
        out = lines[-max_lines:]
        # 失败时的原因行可能被超长提示词挤出尾部窗口（真实额度错误已遇到）：
        # 把最后一条 ERROR/vrgen 报错补在末尾，页面上不至于只看到"失败"。
        if self.state == "failed":
            errors = [line for line in lines
                      if line.startswith("ERROR ") or line.startswith("vrgen:")]
            if errors:
                # 放到最后：页面读 tail 的最后几行，失败原因必须最先被看见。
                out = [line for line in out if line != errors[-1]]
                out = out + ["……", errors[-1]]
        return out


def _task_from_record(raw: str) -> Task | None:
    """从 history.jsonl 的一行还原任务视图；日志文件已不在的记录直接丢弃。"""
    try:
        data = json.loads(raw)
        status = str(data.get("status") or "")
        log_path = data.get("log_path")
        if status not in ("done", "failed") or not log_path:
            return None
        if not Path(log_path).is_file():
            return None
        task = Task(str(data["kind"]), str(data["case"]), [])
        task.id = str(data.get("id") or task.id)
        task.owner_id = data.get("owner_id")
        task.owner_name = data.get("owner_name")
        task.request_id = data.get("request_id")
        task.billing = {
            "project_id": data.get("project_id"),
            "episode_id": data.get("episode_id"),
            "session_id": data.get("session_id"),
        }
        task.state = status
        task.progress = data.get("progress")
        task.error = data.get("error")
        task.reused = data.get("reused")
        task.created_at = data.get("created_at") or task.created_at
        task.started_at = data.get("started_at")
        task.finished_at = data.get("finished_at")
        task.out_path = Path(str(log_path))
        return task
    except (KeyError, TypeError, ValueError):
        return None


class TaskQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.current: Task | None = None
        self.pending: list[Task] = []
        self.history: list[Task] = []

    def submit(self, kind: str, case: str, argv: list[str],
               follow_up: list[tuple[str, str, list[str]]] | None = None,
               owner=None, request_id: str | None = None,
               billing: dict | None = None) -> Task:
        with self._lock:
            task = Task(kind, case, argv, follow_up=follow_up,
                        owner=owner, request_id=request_id, billing=billing)
            self.pending.append(task)
            log.info("task.submit", extra={"data": {
                "event": "task.submit", "task_id": task.id, "kind": kind,
                "case": case, "owner_id": task.owner_id,
                "request_id": request_id, "queued": len(self.pending),
            }})
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._drain, daemon=True)
                self._thread.start()
            return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            if self.current is not None and self.current.id == task_id:
                return self.current
            for task in self.pending:
                if task.id == task_id:
                    return task
            for task in reversed(self.history):
                if task.id == task_id:
                    return task
        return None

    def list(self, user=None, scope: str = "all", case: str | None = None) -> list[dict]:
        """任务视图：当前任务、排队任务、最近历史；history 新→旧。"""
        with self._lock:
            current = self.current
            pending = list(self.pending)
            history = list(reversed(self.history[-50:]))
        rows: list[dict] = []
        if current is not None:
            rows.append(current.view(1))
        for index, task in enumerate(pending, start=2):
            rows.append(task.view(index))
        rows.extend(task.view(None) for task in history)
        if scope == "mine" and user is not None:
            rows = [row for row in rows if row["owner_id"] == user.id]
        if case:
            rows = [row for row in rows if row["case"] == case]
        return rows

    def active_for(self, case: str) -> list[Task]:
        """正在跑或排队的该 case 任务；删除 case 前用它拒绝。"""
        with self._lock:
            tasks = ([self.current] if self.current is not None else []) + self.pending
            return [task for task in tasks if task.case == case]

    def record(self, task: Task) -> None:
        """把已结束任务追加到 history.jsonl，serve 重启后还能查日志。"""
        try:
            row = task.view(None)
            row.pop("tail", None)
            with open(_history_path(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            # 历史索引写不进去不影响任务本身；下一次 serve 只是少一条记录。
            log.warning("task.history.write_failed", extra={"data": {
                "event": "task.history.write_failed", "task_id": task.id}})

    def restore_history(self, limit: int = 50) -> None:
        """从 history.jsonl 回填最近的记录；只回填有日志文件的 finished 任务。"""
        path = _history_path()
        if not path.is_file():
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                window = 256 * 1024
                fh.seek(max(0, size - window))
                lines = decode_text(fh.read()).splitlines()
            if size > window and lines:
                lines = lines[1:]          # 首行可能被窗口截断
        except OSError:
            return
        restored: list[Task] = []
        for raw in lines[-limit:]:
            task = _task_from_record(raw)
            if task is not None:
                restored.append(task)
        restored.sort(key=lambda item: item.finished_at or "")
        with self._lock:
            self.history = restored

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
                self.record(task)
                # 连续任务：merge 成功后才排 assemble；merge 失败就停在原地。
                if task.state == "done" and task.follow_up:
                    follow = []
                    for kind, case, argv in task.follow_up:
                        child = Task(kind, case, argv)
                        child.owner_id = task.owner_id
                        child.owner_name = task.owner_name
                        child.billing = dict(task.billing)
                        child.request_id = task.request_id
                        follow.append(child)
                    self.pending[:0] = follow

    def status(self) -> dict:
        with self._lock:
            cur = self.current
            return {
                "current": None if cur is None else {
                    "kind": cur.kind, "case": cur.case, "state": cur.state,
                    "submitted": cur.submitted,
                    "tail": cur.tail(),
                    "log": str(cur.out_path) if cur.out_path else None,
                },
                "queued": [{"kind": t.kind, "case": t.case,
                            "submitted": t.submitted} for t in self.pending],
                "last": ([{"kind": t.kind, "case": t.case, "state": t.state,
                           "returncode": t.returncode, "submitted": t.submitted,
                           "log": str(t.out_path) if t.out_path else None,
                           "tail": t.tail(5)} for t in self.history[-10:]]),
            }


QUEUE = TaskQueue()


def vrstudio_argv(*args: str) -> list[str]:
    """与控制台同一条执行路径：当前解释器 ``-m vrstudio <子命令>``。"""
    return [sys.executable, "-m", "vrstudio", *args]
