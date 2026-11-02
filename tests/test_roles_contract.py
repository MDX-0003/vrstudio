"""roles.json 契约快照：本地词表必须与它一致，快照内容由 SHA256 钉住。

``roles.json`` 是根仓库 ``docs/roles.json`` 的逐字节副本。改角色时三份文件
一起改，并同步更新本文件的 ``CONTRACT_SHA256``，否则这里会红。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vrstudio import case as case_mod
from vrstudio import roles

SPEC = Path(roles.__file__).with_name("roles.json")
CONTRACT_SHA256 = "4c7a53dde440e76a9208ea898e7bfb442dab092f8b4265c2df15b4b8bc451a1f"


def _entries() -> list[dict]:
    return json.loads(SPEC.read_text(encoding="utf-8"))["roles"]


def test_snapshot_matches_the_pinned_digest():
    assert hashlib.sha256(SPEC.read_bytes()).hexdigest() == CONTRACT_SHA256


def test_vocabulary_matches_the_snapshot():
    entries = _entries()
    assert list(roles.ROLES) == [item["name"] for item in entries]
    assert roles.ROLE_LABELS == {item["name"]: item["help"] for item in entries}
    assert roles.ROLE_KINDS == {
        item["name"]: tuple(item["kinds"]) for item in entries if item["kinds"]
    }
    assert roles.RESERVED_ROLES == tuple(
        item["name"] for item in entries if item["reserved"])


def test_payload_carries_everything_the_dropdown_needs():
    payload = {item["name"]: item for item in roles.roles_payload()}
    assert payload["lighting_ref"]["kinds"] == ["image"]
    assert payload["lighting_ref"]["open"] is True
    assert payload["lighting_ref"]["modes"] == [
        "videoEdit", "allMediaRef", "videoExtend"]
    assert payload["scene_ref"]["open"] is False
    assert payload["mask"]["reserved"] is True
    for item in payload.values():
        assert item["label"] and item["help"]
        assert set(item["kinds"] or ()) <= {"video", "image"}
        assert set(item["modes"]) <= set(case_mod.GEN_MODES)
    for spec in roles.ROLE_SPECS:
        assert spec.prompt


def test_case_module_reexports_the_registry():
    assert case_mod.ROLES == roles.ROLES
    assert case_mod.RESERVED_ROLES == roles.RESERVED_ROLES
