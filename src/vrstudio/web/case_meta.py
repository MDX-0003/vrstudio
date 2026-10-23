"""case 归属、修订号与 Web 层元数据。

`case-meta.json` 与 `case.toml` 分开：前者只服务 Web 层的负责人、版本检查与
冲突提示，CLI 读取的静态事实仍然只有 `case.toml`。文件不存在时按 revision 0
处理，第一次写操作创建它。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from .. import case as case_mod


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class CaseMeta:
    owner_id: str | None = None
    owner_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    updated_by_id: str | None = None
    updated_by_name: str | None = None
    revision: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


class RevisionConflict(Exception):
    def __init__(self, current: CaseMeta):
        self.current = current
        super().__init__("case 已被其他人修改")


class FileCaseMetaStore:
    """单进程文件实现；写盘用临时文件替换，避免读到半截 JSON。"""

    def __init__(self, root: str | Path | None = None):
        self.root = root
        self._lock = threading.Lock()

    def path(self, case: str) -> Path:
        return case_mod.case_dir(case, self.root) / "case-meta.json"

    def read(self, case: str) -> CaseMeta:
        try:
            raw = json.loads(self.path(case).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return CaseMeta()
        try:
            revision = int(raw.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        return CaseMeta(
            owner_id=raw.get("owner_id"),
            owner_name=raw.get("owner_name"),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
            updated_by_id=raw.get("updated_by_id"),
            updated_by_name=raw.get("updated_by_name"),
            revision=revision,
        )

    def ensure(self, case: str, user) -> CaseMeta:
        """第一次打开时补写负责人；已有元数据时只返回。"""
        with self._lock:
            meta = self.read(case)
            if meta.owner_id or meta.created_at:
                return meta
            now = _now()
            meta = replace(
                meta,
                owner_id=getattr(user, "id", None),
                owner_name=getattr(user, "display_name", None),
                created_at=now,
                updated_at=now,
                updated_by_id=getattr(user, "id", None),
                updated_by_name=getattr(user, "display_name", None),
                revision=0,
            )
            self._write(case, meta)
            return meta

    def touch(self, case: str, user, expected_revision: int | None = None) -> CaseMeta:
        with self._lock:
            meta = self.read(case)
            if expected_revision is not None and expected_revision != meta.revision:
                raise RevisionConflict(meta)
            now = _now()
            meta = replace(
                meta,
                owner_id=meta.owner_id or getattr(user, "id", None),
                owner_name=meta.owner_name or getattr(user, "display_name", None),
                created_at=meta.created_at or now,
                updated_at=now,
                updated_by_id=getattr(user, "id", None),
                updated_by_name=getattr(user, "display_name", None),
                revision=meta.revision + 1,
            )
            self._write(case, meta)
            return meta

    def _write(self, case: str, meta: CaseMeta) -> None:
        path = self.path(case)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(meta.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, path)
