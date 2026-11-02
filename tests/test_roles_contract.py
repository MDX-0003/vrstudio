"""roles.json 濂戠害蹇収锛氭湰鍦拌瘝琛ㄥ繀椤讳笌瀹冧竴鑷达紝蹇収鍐呭鐢?SHA256 閽変綇銆?

``roles.json`` 鏄牴浠撳簱 ``docs/roles.json`` 鐨勯€愬瓧鑺傚壇鏈€傛敼瑙掕壊鏃朵笁浠芥枃浠?
涓€璧锋敼锛屽苟鍚屾鏇存柊鏈枃浠剁殑 ``CONTRACT_SHA256``锛屽惁鍒欒繖閲屼細绾€?
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vrstudio import case as case_mod
from vrstudio import roles

SPEC = Path(roles.__file__).with_name("roles.json")
CONTRACT_SHA256 = "c304e5f5374c6d5c9e55f888d6007ee684ab909140ae90b8174d3b05870c7a82"


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
