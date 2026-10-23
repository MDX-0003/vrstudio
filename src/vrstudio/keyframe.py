"""首尾帧工作台的 case 级状态：``keyframe.toml``。

这份文件只记录“当前这一对定稿基于什么”，不是 case.toml 的段，也不是
vrgen 的台账契约。图片步的参考图由它和原始帧解析；视频提交集仍只由
``case.toml`` 的 ``[submit] selected`` 决定，两者互不读取。

字段：

- ``extract``：这一对定稿基于哪个抽取版本；与当前版本不一致时视为过期。
- ``sheet``：图片步绑定的人物三视图素材 id；未写时由调用方按“唯一
  ``subject_ref``”推导，不在这里猜。
- ``[first]`` / ``[last]``：轮次目录名、候选序号（1 起）、落进素材库的 id。
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

FILE = "keyframe.toml"


class KeyframeError(ValueError):
    """keyframe.toml 不合约（页面显示原因，不打断 case 加载）。"""


@dataclass(frozen=True)
class Pick:
    round: str
    index: int
    asset: str


@dataclass
class State:
    extract: str | None = None
    sheet: str | None = None
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


def load(case_dir: str | Path) -> State:
    target = path(case_dir)
    if not target.is_file():
        return State()
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise KeyframeError(f"{target}: {exc}") from exc
    extract = str(data.get("extract") or "").strip() or None
    sheet = str(data.get("sheet") or "").strip() or None
    return State(extract=extract, sheet=sheet,
                 first=_pick(data.get("first"), "first"),
                 last=_pick(data.get("last"), "last"))


def _str(key: str, value: str) -> str:
    return f"{key} = {json.dumps(value, ensure_ascii=False)}"


def save(case_dir: str | Path, state: State) -> Path:
    lines: list[str] = []
    if state.extract:
        lines.append(_str("extract", state.extract))
    if state.sheet:
        lines.append(_str("sheet", state.sheet))
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
