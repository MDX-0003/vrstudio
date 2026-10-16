"""统一 case 模型：case.toml 只放这个视频任务的静态事实。

格式是对外契约（E:\\VRAIGC\\docs\\case-model.md §2）。原则：生成侧的一切
（提示词、task_id、台账）都不进 case；case → vrgen 的唯一耦合是 ``[adopt] round``
一行指针。读写只有这一份实现——CLI、generate、web（M4）三方共用。
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import REPO_ROOT

SCHEMA = 1
FLOWS = ("patch", "fullredraw")

#: 与 vrgen 的 job 名同一套约束：case 名是 cases/ 下的目录名。
_CASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_PATCH_SECTIONS = ("viewport", "inner", "frames", "extract")


class CaseError(ValueError):
    """case.toml 不合约（参数/配置错，CLI 层映射为退出码 2）。"""


def cases_root(root: str | Path | None = None) -> Path:
    return Path(root) if root else REPO_ROOT / "cases"


def case_dir(name: str, root: str | Path | None = None) -> Path:
    _check_name(name)
    return cases_root(root) / name


def case_toml(name: str, root: str | Path | None = None) -> Path:
    return case_dir(name, root) / "case.toml"


def _check_name(name: str) -> None:
    if not _CASE_NAME.match(name):
        raise CaseError(f"case 名 {name!r} 不合法（只允许字母数字与 ._-，且不以符号开头）")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Case:
    name: str
    flow: str
    source_path: str                     # 存 toml 原文（相对 paths/ 的写法原样保留）
    sha256: str | None = None
    # ── 仅 flow = patch：选区草稿（web pick / 手填，extract 时冻结进 clip.json）──
    viewport: dict | None = None
    inner: dict | None = None
    frames: dict | None = None
    extract_current: str | None = None
    # ── adopt 指针：vrgen 台账里被采用的轮次 ──
    adopt_round: str | None = None

    def dir(self, root: str | Path | None = None) -> Path:
        return case_dir(self.name, root)


def create(name: str, flow: str, source: str | Path,
           root: str | Path | None = None, compute_hash: bool = True) -> Case:
    _check_name(name)
    if flow not in FLOWS:
        raise CaseError(f"flow 必须是 {' 或 '.join(FLOWS)}，got {flow!r}")
    src = Path(source)
    if not src.is_file():
        raise CaseError(f"源视频不存在：{src}")
    target = case_toml(name, root)
    if target.is_file():
        raise CaseError(f"case 已存在：{target}")
    # patch 的 merge 要重读源视频贴回，源变了就静默错片——创建时就把身份钉死；
    # fullredraw 在 pad 之后源不再被读（manifest 已含源身份），哈希可选。
    sha = file_sha256(src) if (compute_hash and flow == "patch") else None
    case = Case(name=name, flow=flow, source_path=str(src), sha256=sha)
    return save(case, root)


def load(name: str, root: str | Path | None = None) -> Case:
    path = case_toml(name, root)
    if not path.is_file():
        raise CaseError(f"没有这个 case：{path}（新建：vrstudio case new）")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("schema", 0)) != SCHEMA:
        raise CaseError(f"{path}: 不支持的 schema {raw.get('schema')!r}")
    flow = raw.get("flow")
    if flow not in FLOWS:
        raise CaseError(f"{path}: flow 必须是 {' 或 '.join(FLOWS)}")
    source = raw.get("source", {})
    case = Case(
        name=name, flow=flow,
        source_path=str(source.get("path", "")),
        sha256=source.get("sha256"),
        viewport=raw.get("viewport"), inner=raw.get("inner"),
        frames=raw.get("frames"), extract_current=raw.get("extract", {}).get("current"),
        adopt_round=raw.get("adopt", {}).get("round"),
    )
    if not case.source_path:
        raise CaseError(f"{path}: [source] 缺 path")
    if flow == "fullredraw":
        for section in _PATCH_SECTIONS:
            if raw.get(section) is not None:
                raise CaseError(f"{path}: flow=fullredraw 不该有 [{section}]（patch 专属段）")
    return case


def save(case: Case, root: str | Path | None = None) -> Case:
    lines = [f"schema = {SCHEMA}", f'flow = "{case.flow}"']
    lines += ["", "[source]", f"path = {_s(case.source_path)}"]
    if case.sha256:
        lines.append(f"sha256 = {_s(case.sha256)}")
    if case.flow == "patch":
        for section, value in (("viewport", case.viewport), ("inner", case.inner),
                               ("frames", case.frames)):
            if value:
                lines += ["", f"[{section}]"]
                lines += [_kv(k, v) for k, v in value.items()]
        if case.extract_current:
            lines += ["", "[extract]", f"current = {_s(case.extract_current)}"]
    if case.adopt_round:
        lines += ["", "[adopt]", f"round = {_s(case.adopt_round)}"]
    path = case_toml(case.name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return case


def set_adopt(name: str, round_key: str, root: str | Path | None = None) -> Case:
    case = load(name, root)
    case.adopt_round = round_key
    return save(case, root)


def set_extract_current(name: str, version: str, root: str | Path | None = None) -> Case:
    case = load(name, root)
    if case.flow != "patch":
        raise CaseError(f"case {name} 是 {case.flow} 流，没有 extracts/ 版本")
    case.extract_current = version
    return save(case, root)


def check_source(case: Case, root: str | Path | None = None) -> Path:
    """patch 流的守卫：merge/extract 前确认源视频还是创建时的那份。"""
    src = Path(case.source_path)
    if not src.is_file():
        raise CaseError(f"源视频不在了：{src}（case.toml 记录的路径）")
    if case.sha256 and file_sha256(src) != case.sha256:
        raise CaseError(
            f"源视频内容与 case 创建时不同（sha256 不匹配）：{src}。"
            f"换源 = 新 case；这份几何契约只对创建时那份源有效。"
        )
    return src


def _s(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _kv(key: str, value) -> str:
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{key} = {value}"
    if isinstance(value, float):
        return f"{key} = {value!r}"
    return f"{key} = {_s(str(value))}"
