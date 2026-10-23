"""case 占用锁：安全网，不做协同编辑。

一个用户打开 case 时拿锁，页面每 30 秒续期；别人打开时只提示占用者，
不阻断写入（写入冲突由 revision 检查负责）。锁超过 2 分钟没有心跳视为
失效，可以接管。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .. import case as case_mod


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class CaseLock:
    owner_id: str | None = None
    owner_name: str | None = None
    acquired_at: str | None = None
    heartbeat_at: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class LockConflict(Exception):
    def __init__(self, current: CaseLock):
        self.current = current
        super().__init__(f"case 正被 {current.owner_name or '其他人'} 使用")


class FileCaseLockStore:
    ttl_seconds = 120.0

    def __init__(self, root: str | Path | None = None):
        self.root = root
        self._lock = threading.Lock()

    def path(self, case: str) -> Path:
        return case_mod.case_dir(case, self.root) / ".web-lock.json"

    def read(self, case: str) -> CaseLock | None:
        try:
            raw = json.loads(self.path(case).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        item = CaseLock(
            owner_id=raw.get("owner_id"),
            owner_name=raw.get("owner_name"),
            acquired_at=raw.get("acquired_at"),
            heartbeat_at=raw.get("heartbeat_at"),
        )
        if self._stale(item):
            return None
        return item

    def acquire(self, case: str, user) -> CaseLock:
        with self._lock:
            current = self.read(case)
            uid = getattr(user, "id", None)
            if current is not None and current.owner_id != uid:
                raise LockConflict(current)
            now = _now()
            item = CaseLock(
                owner_id=uid,
                owner_name=getattr(user, "display_name", None),
                acquired_at=current.acquired_at if current else now,
                heartbeat_at=now,
            )
            self._write(case, item)
            return item

    def release(self, case: str, user) -> bool:
        with self._lock:
            current = self.read(case)
            if current is None:
                return True
            if current.owner_id != getattr(user, "id", None):
                return False
            self.path(case).unlink(missing_ok=True)
            return True

    def _stale(self, item: CaseLock) -> bool:
        raw = item.heartbeat_at or item.acquired_at
        if not raw:
            return True
        try:
            beat = datetime.fromisoformat(raw).timestamp()
        except ValueError:
            return True
        return datetime.now().timestamp() - beat > self.ttl_seconds

    def _write(self, case: str, item: CaseLock) -> None:
        path = self.path(case)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(item.as_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        os.replace(temp, path)
