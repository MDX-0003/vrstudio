"""素材角色注册表：本仓库唯一的词表，内容来自同目录的 ``roles.json``。

``roles.json`` 是根仓库 ``docs/roles.json`` 的逐字节副本（契约快照）。
``tests/test_roles_contract.py`` 断言这里的派生常量与快照一致，并用 SHA256
钉住快照内容；改角色要三份文件一起改，见 ``docs/case-model.md`` §2。

角色 = 提示词里的职责声明。``kinds`` 为空表示不限素材种类；``modes`` 是允许
出现该角色的生成模式；``open`` 决定它是否出现在生成页的下拉里（false 只用于
旧 case 的「旧角色」标记）；``reserved`` 表示由流程产物或工作台提供、用户不能
给文件素材指定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_SPEC_PATH = Path(__file__).with_name("roles.json")


@dataclass(frozen=True)
class RoleSpec:
    name: str
    label: str
    help: str
    prompt: str
    kinds: tuple[str, ...] | None
    modes: tuple[str, ...]
    open: bool
    reserved: bool


def _load() -> tuple[RoleSpec, ...]:
    data = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))
    return tuple(
        RoleSpec(
            name=str(item["name"]),
            label=str(item["label"]),
            help=str(item["help"]),
            prompt=str(item["prompt"]),
            kinds=(tuple(str(kind) for kind in item["kinds"])
                   if item.get("kinds") else None),
            modes=tuple(str(mode) for mode in item.get("modes") or ()),
            open=bool(item["open"]),
            reserved=bool(item["reserved"]),
        )
        for item in data["roles"]
    )


ROLE_SPECS: tuple[RoleSpec, ...] = _load()
ROLES = tuple(spec.name for spec in ROLE_SPECS)
#: 网页 tooltip 与旧接口用的长说明（带括号）。
ROLE_LABELS = {spec.name: spec.help for spec in ROLE_SPECS}
#: 只对列出的素材种类成立的角色；未列入 = 不限。
ROLE_KINDS: dict[str, tuple[str, ...]] = {
    spec.name: spec.kinds for spec in ROLE_SPECS if spec.kinds
}
RESERVED_ROLES = tuple(spec.name for spec in ROLE_SPECS if spec.reserved)


def roles_payload() -> list[dict]:
    """``/api/case`` 下发的角色清单：前端下拉只认这一份，不再自带词表。"""
    return [
        {
            "name": spec.name,
            "label": spec.label,
            "help": spec.help,
            "kinds": list(spec.kinds) if spec.kinds else None,
            "modes": list(spec.modes),
            "open": spec.open,
            "reserved": spec.reserved,
        }
        for spec in ROLE_SPECS
    ]


def check_kinds(role: str, kind: str) -> str | None:
    """角色能否用于该素材种类；不能则返回人读原因。"""
    allowed = ROLE_KINDS.get(role)
    if allowed and kind not in allowed:
        return f"角色 {role} 只能用于 {' / '.join(allowed)} 素材，当前是 {kind}"
    return None
