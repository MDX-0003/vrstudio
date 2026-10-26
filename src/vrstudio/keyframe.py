"""首尾帧工作台的 case 级状态：``keyframe.toml``。

这份文件只记录“当前这一对定稿基于什么”，不是 case.toml 的段，也不是
vrgen 的台账契约。图片步的参考图由它和原始帧解析；视频提交集仍只由
``case.toml`` 的 ``[submit]`` 决定，两者互不读取。

字段：

- ``extract``：这一对定稿基于哪个抽取版本；与当前版本不一致时视为过期。
- ``refs``：图片步的有序参考图清单，每项含 ``asset`` / ``role`` / ``note``。
  ``role`` 只能是 ``subject_ref``（人物三视图，恰好一张）或 ``detail_ref``
  （细节参考，任意多张）。旧格式的单值 ``sheet`` 读取时视为只有一项
  ``subject_ref`` 的清单。
- ``[first]`` / ``[last]``：轮次目录名、候选序号（1 起）、落进素材库的 id。
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

FILE = "keyframe.toml"
ROLES = ("subject_ref", "detail_ref")
NOTE_LIMIT = 80


class KeyframeError(ValueError):
    """keyframe.toml 不合约（页面显示原因，不打断 case 加载）。"""


@dataclass(frozen=True)
class Pick:
    round: str
    index: int
    asset: str


@dataclass(frozen=True)
class Ref:
    asset: str
    role: str = "subject_ref"
    note: str = ""


@dataclass
class State:
    extract: str | None = None
    refs: list[Ref] = field(default_factory=list)
    first: Pick | None = None
    last: Pick | None = None


def path(case_dir: str | Path) -> Path:
    return Path(case_dir) / FILE


def _pick(raw: object, where: str) -> Pick | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise KeyframeError(f"{FILE} 的 [{where}] 必须是表")
    round_name = str(raw.get("round") or "").strip()
    asset = str(raw.get("asset") or "").strip()
    try:
        index = int(raw.get("index") or 0)
    except (TypeError, ValueError) as exc:
        raise KeyframeError(f"{FILE} 的 [{where}].index 必须是整数") from exc
    if not round_name or not asset or index < 1:
        raise KeyframeError(f"{FILE} 的 [{where}] 缺 round / index / asset")
    return Pick(round=round_name, index=index, asset=asset)


def _refs(raw: object) -> list[Ref]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise KeyframeError(f"{FILE} 的 [[refs]] 必须是数组")
    out: list[Ref] = []
    seen: set[str] = set()
    primary = 0
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise KeyframeError(f"{FILE} 的 refs[{index}] 必须是表")
        asset = str(item.get("asset") or "").strip()
        role = str(item.get("role") or "subject_ref").strip()
        note = str(item.get("note") or "").strip()
        if not asset:
            raise KeyframeError(f"{FILE} 的 refs[{index}] 缺 asset")
        if asset in seen:
            raise KeyframeError(f"{FILE} 的 refs 重复引用素材 {asset!r}")
        if role not in ROLES:
            raise KeyframeError(
                f"{FILE} 的 refs[{index}] 角色 {role!r} 只能是 {' 或 '.join(ROLES)}")
        if len(note) > NOTE_LIMIT:
            raise KeyframeError(
                f"{FILE} 的 refs[{index}] 用途超过 {NOTE_LIMIT} 字")
        if role == "subject_ref":
            primary += 1
        seen.add(asset)
        out.append(Ref(asset=asset, role=role, note=note))
    if primary > 1:
        raise KeyframeError(f"{FILE} 的 refs 只能有一张 subject_ref（人物三视图）")
    return out


def validate_refs(refs: list[Ref], image_ids: set[str]) -> list[Ref]:
    """校验页面提交的 refs：角色、唯一主图、重复、用途长度与素材存在。"""
    out = _refs([{"asset": ref.asset, "role": ref.role, "note": ref.note}
                 for ref in refs])
    for ref in out:
        if ref.asset not in image_ids:
            raise KeyframeError(f"参考图素材不存在或不是图片：{ref.asset}")
    return out


def load(case_dir: str | Path) -> State:
    target = path(case_dir)
    if not target.is_file():
        return State()
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise KeyframeError(f"{target}: {exc}") from exc
    extract = str(data.get("extract") or "").strip() or None
    refs = _refs(data.get("refs"))
    if not refs:
        sheet = str(data.get("sheet") or "").strip()
        if sheet:
            refs = [Ref(asset=sheet, role="subject_ref", note="")]
    return State(extract=extract, refs=refs,
                 first=_pick(data.get("first"), "first"),
                 last=_pick(data.get("last"), "last"))


def _str(key: str, value: str) -> str:
    return f"{key} = {json.dumps(value, ensure_ascii=False)}"


def save(case_dir: str | Path, state: State) -> Path:
    lines: list[str] = []
    if state.extract:
        lines.append(_str("extract", state.extract))
    for ref in state.refs:
        if lines:
            lines.append("")
        lines += ["[[refs]]",
                  _str("asset", ref.asset),
                  _str("role", ref.role)]
        if ref.note:
            lines.append(_str("note", ref.note))
    for name, pick in (("first", state.first), ("last", state.last)):
        if pick is None:
            continue
        if lines:
            lines.append("")
        lines.append(f"[{name}]")
        lines.append(_str("round", pick.round))
        lines.append(f"index = {int(pick.index)}")
        lines.append(_str("asset", pick.asset))
    target = path(case_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def remove_asset(case_dir: str | Path, asset_id: str) -> bool:
    """删素材时同步清掉 refs 与首尾指针；有变化才写文件。"""
    state = load(case_dir)
    refs = [ref for ref in state.refs if ref.asset != asset_id]
    changed = len(refs) != len(state.refs)
    state.refs = refs
    if state.first is not None and state.first.asset == asset_id:
        state.first = None
        changed = True
    if state.last is not None and state.last.asset == asset_id:
        state.last = None
        changed = True
    if changed:
        save(case_dir, state)
    return changed
