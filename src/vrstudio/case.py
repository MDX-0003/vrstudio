"""统一 case 模型：case.toml 只放这个视频任务的静态事实。

格式是对外契约（E:\\VRAIGC\\docs\\case-model.md §2）。原则：生成侧的一切
（提示词、task_id、台账）都不进 case；case → vrgen 的唯一耦合是 ``[adopt] round``
一行指针。读写只有这一份实现——CLI、generate、web（M4）三方共用。

gen 流自决策 24/25 起以有序 ``[[assets]]`` 为素材真源：视频与图片复制进
``cases/<name>/assets/``，路径相对 case 目录；源视频不再是独立字段。旧 gen case
（只有 ``[source]``）在 load 时自动迁移并落盘。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .config import REPO_ROOT
from .media.imageio import read_image
from .roles import RESERVED_ROLES, ROLES, check_kinds

SCHEMA = 1
FLOWS = ("patch", "patchimg", "fullRedrawImg", "fullredraw", "gen", "relight")

#: 图片流程：源是单张 2:1 ERP 图，产物是单帧（image.json / image_pad.json），
#: 没有时间轴、没有 mp4 交付，采用时必须显式给候选下标。
IMAGE_FLOWS = ("patchimg", "fullRedrawImg")

#: relight 的素材角色：底版候选与氛围图候选。库里可以有多条同角色的候选（美术每改一版
#: 都传一份），``[relight]`` 的 ``gt``/``atmo`` 用素材 id 指向当前选中的那一条。
RELIGHT_GT = "relight_gt"
RELIGHT_ATMO = "relight_atmo"

#: ``[relight]`` 是这条流程唯一的草稿段：``current`` 指当前成片，``gt``/``atmo`` 指两个
#: 槽位选中的素材 id，其余是上次运行的参数。
_RELIGHT_KEYS = ("current", "gt", "atmo", "scene", "strength", "local_shading",
                 "highlight_clip", "clip_knee", "clip_max", "ref_frame", "regions")

#: ``[relight]`` 里指向素材的两把指针，以及各自要求的素材角色。
_RELIGHT_SLOTS = {"gt": RELIGHT_GT, "atmo": RELIGHT_ATMO}

#: ``[merge]`` 属于 fullRedrawImg 与 fullredraw：``input`` 指向素材库里的拼接输入，
#: 缺省时回退到 adopt 落点（图片是 ``extracts/<current>/result.png``，视频是
#: ``result.mp4``）——决策 66/78。
_MERGE_INPUT_KEYS = ("input",)

#: 每条流程的拼接输入种类；不在表里的流程没有 ``[merge]`` 段。
_MERGE_INPUT_KINDS = {"fullRedrawImg": "image", "fullredraw": "video"}

#: 拼接输入专用的素材角色（决策 67/78）：种类由流程决定，不进任何生成下拉与提交。
MERGE_INPUT = "merge_input"

#: 与 vrgen 的 job 名同一套约束：case 名是 cases/ 下的目录名。
_CASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_PATCH_SECTIONS = ("viewport", "inner", "frames", "extract")
_PATCHIMG_SECTIONS = ("viewport", "inner", "extract")
#: fullRedrawImg 没有选区草稿：整幅 21:9 画布与关键区块都由 extract 决定。
_FULLREDRAW_IMG_SECTIONS = ("extract",)

#: 素材种类受控词表。角色词表在 roles.py（来自 roles.json 契约快照）。
KINDS = ("video", "image")

#: 本期支持的生成模式；创建 gen case 与页面下拉框共用。
GEN_MODES = ("videoEdit", "allMediaRef", "videoExtend", "firstLastFrame")

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}


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
class Asset:
    """一条文件素材；``path`` 是相对 case 目录的写法。"""

    id: str
    kind: str
    path: str
    role: str
    sha256: str | None = None

    def resolved(self, case: Case, root: str | Path | None = None) -> Path:
        return case.dir(root) / self.path


@dataclass
class Case:
    name: str
    flow: str
    source_path: str | None = None            # 仅 patch/fullredraw；gen 用 assets
    sha256: str | None = None
    # ── 仅 flow = patch：选区草稿（web pick / 手填，extract 时冻结进 clip.json）──
    viewport: dict | None = None
    inner: dict | None = None
    frames: dict | None = None
    extract_current: str | None = None
    # ── 三流程通用的文件素材列表（顺序即 @VideoN / @ImageN 编号）──
    assets: list[Asset] = field(default_factory=list)
    # ── 可选提交集：候选 id 列表；None 或空列表 = 空集（没有默认集）──
    submit_selected: list[str] | None = None
    # ── 可选：逐 id 的提交角色；缺省回退到 [[assets]] role ──
    submit_roles: dict[str, str] | None = None
    # ── adopt 指针：vrgen 台账里被采用的轮次 ──
    adopt_round: str | None = None
    adopt_index: int | None = None
    # ── 仅 flow = fullRedrawImg：``[merge] input`` 指向素材库里的拼接输入 ──
    merge_input: str | None = None
    # ── 仅 flow = relight：``[relight]`` 草稿与当前成片指针 ──
    relight: dict | None = None

    def dir(self, root: str | Path | None = None) -> Path:
        return case_dir(self.name, root)

    def asset(self, asset_id: str) -> Asset:
        for item in self.assets:
            if item.id == asset_id:
                return item
        raise CaseError(f"case {self.name} 没有素材 {asset_id!r}")

    def master(self) -> Asset | None:
        """提示词几何与上传基准：按契约的回退链取第一条可用素材。"""
        if self.flow == "relight":
            # 预览几何要跟底版走：通用回退链在「图片 GT + 图片氛围图」时会先取到
            # 氛围图（它是第一条图片），预览就会用错尺寸。
            gt, _atmo = self.relight_slots()
            if gt is not None:
                return gt
        videos = [a for a in self.assets if a.kind == "video"]
        for item in videos:
            if item.role == "edit_master":
                return item
        if videos:
            return videos[0]
        for item in self.assets:
            if item.kind == "image" and item.role in ("space_ref", "scene_ref"):
                return item
        for item in self.assets:
            if item.kind == "image" and item.role == "first_frame":
                return item
        images = [a for a in self.assets if a.kind == "image"]
        return images[0] if images else None

    def submit_role(self, asset_id: str) -> str | None:
        """提交清单里的角色：先看 ``[submit.roles]``，再回退到素材角色。"""
        if self.submit_roles and asset_id in self.submit_roles:
            return self.submit_roles[asset_id]
        try:
            return self.asset(asset_id).role
        except CaseError:
            return None

    def relight_slots(self) -> tuple[Asset | None, Asset | None]:
        """relight 当前选中的两个槽位：(底版, 氛围图)；没选或指向的素材不存在时是 None。"""
        relight = self.relight or {}
        by_id = {asset.id: asset for asset in self.assets}
        return by_id.get(relight.get("gt")), by_id.get(relight.get("atmo"))

    def merge_input_asset(self) -> Asset | None:
        """``[merge] input`` 指向的素材；没写指针时是 None（回退 adopt 结果）。"""
        if not self.merge_input:
            return None
        return self.asset(self.merge_input)

    def merge_input_file(self, root: str | Path | None = None) -> Path | None:
        """拼接输入的落点文件：指针优先，否则 adopt 落点 result.png；都没有时 None。

        CLI 与 web 共用这一处判定，避免两边各写一套“先看指针再回退”的规则。
        """
        asset = self.merge_input_asset()
        if asset is not None:
            return asset.resolved(self, root)
        kind = merge_input_kind(self)
        if kind == "video":
            fallback = self.dir(root) / "result.mp4"
        elif kind == "image" and self.extract_current:
            fallback = self.dir(root) / "extracts" / self.extract_current / "result.png"
        else:
            return None
        return fallback if fallback.is_file() else None


def merge_input_kind(case: Case) -> str | None:
    """这条流程的拼接输入该是什么种类；``None`` 表示没有 ``[merge]`` 段。

    唯一判定点：case 模型的校验、web 的解析与角色规则都读它，避免两边各写一套
    「图片流程收图、视频流程收视频」。
    """
    return _MERGE_INPUT_KINDS.get(case.flow)


# ── 创建 ─────────────────────────────────────────────────────────────────


def create(name: str, flow: str, source: str | Path | None = None,
           root: str | Path | None = None, compute_hash: bool = True) -> Case:
    _check_name(name)
    if flow not in FLOWS:
        raise CaseError(f"flow 必须是 {' 或 '.join(FLOWS)}，got {flow!r}")
    target = case_toml(name, root)
    if target.is_file():
        raise CaseError(f"case 已存在：{target}")
    if flow == "gen":
        if source is not None:
            src = Path(source)
            if not src.is_file():
                raise CaseError(f"源视频不存在：{src}")
            if kind_of(src) != "video":
                raise CaseError(f"gen 的 --source 必须是视频：{src.name}")
        case = Case(name=name, flow=flow)
        save(case, root)
        if source is not None:
            # 新建 gen 仍接受 --source：复制为 v1/edit_master，与素材步骤同一条路。
            return add_assets(name, [(source, "edit_master")], root=root)
        return case
    if flow == "relight":
        # 建 case 不绑源文件：GT 与氛围图进 case 之后在素材步用两个固定槽位选定。
        # 给了 --source 就明确拒绝，避免建出一个「有源但没有槽位」的半定义 case。
        if source is not None:
            raise CaseError(
                f"relight 建 case 不绑源文件：{Path(source).name} 请在 case 的素材步"
                f"用 {RELIGHT_GT} 槽位加入")
        return save(Case(name=name, flow=flow), root)
    if source is None:
        wanted = "图片" if flow in ("patchimg", "fullRedrawImg") else "源视频"
        raise CaseError(f"flow={flow} 必须指定{wanted}")
    src = Path(source)
    if not src.is_file():
        raise CaseError(f"源文件不存在：{src}")
    kind = kind_of(src)
    if flow in ("patchimg", "fullRedrawImg"):
        if kind != "image":
            raise CaseError(f"{flow} 的 --source 必须是图片：{src.name}")
        image = read_image(src)
        height, width = image.shape[:2]
        if height <= 0 or abs(width / height - 2.0) > 0.001:
            raise CaseError(
                f"{flow} 的源图必须是 2:1 ERP：{src.name} 是 {width}x{height}"
                f"（比例 {width / height:.4f}）")
    elif kind != "video":
        raise CaseError(f"flow={flow} 的 --source 必须是视频：{src.name}")
    # patch/patchimg/fullRedrawImg 后续要重读源文件（贴回或 pad），源变了就静默错片——
    # 创建时就把身份钉死；fullredraw 在 pad 之后源不再被读（manifest 已含源身份），哈希可选。
    sha = file_sha256(src) if (compute_hash and flow in
                               ("patch", "patchimg", "fullRedrawImg")) else None
    case = Case(name=name, flow=flow, source_path=str(src), sha256=sha)
    return save(case, root)


# ── 删除 ─────────────────────────────────────────────────────────────────


def source_references(root: str | Path | None = None) -> dict[str, list[str]]:
    """``[source] path`` → 引用它的 case 名，供源文件回收判断。

    只直读 toml，不走 ``load``：一个坏 case 不该让别人的删除失败，这里也不需要
    校验、迁移或 sha256。路径统一经 ``_stored_path`` 解析后按真实路径归一。
    """
    refs: dict[str, list[str]] = {}
    for path in sorted(cases_root(root).glob("*/case.toml")):
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        source = raw.get("source")
        if not isinstance(source, dict):
            continue
        raw_path = str(source.get("path") or "").strip()
        if not raw_path:
            continue
        refs.setdefault(source_key(_stored_path(raw_path)), []).append(path.parent.name)
    return refs


def source_key(path: str | Path) -> str:
    """同一个源文件的不同写法归一到同一个键（Windows 下大小写不敏感）。"""
    return os.path.normcase(str(Path(path).resolve()))


def source_file(case: Case) -> Path | None:
    """case 的源文件真实路径；没有源时 None。回收与诊断共用同一解析。"""
    if not case.source_path:
        return None
    return _stored_path(case.source_path)


def delete(name: str, root: str | Path | None = None) -> Path:
    """删除整个 case 目录。只碰 cases/<name>/；vrgen 台账与 sources/ 由调用方负责。

    先直接删；Windows 下浏览器或代理可能还握着视频句柄，整棵删不掉时改名移进
    ``cases/.trash/`` 再删，让 case 名立刻从列表消失。墓碑目录不在
    ``*/case.toml`` 的扫描范围内；仍然删不动就把占用原因报出来，留给人工处理。
    """
    target = case_dir(name, root)
    if not case_toml(name, root).is_file():
        raise CaseError(f"case 不存在：{name}")
    try:
        shutil.rmtree(target)
        return target
    except OSError:
        trash = cases_root(root) / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        tomb = trash / f"{name}-{uuid4().hex[:8]}"
        try:
            target.rename(tomb)
        except OSError as exc:
            raise CaseError(f"case 目录被占用，删除失败：{exc}") from exc
        try:
            shutil.rmtree(tomb)
        except OSError as exc:
            raise CaseError(
                f"case 已移出 {target}，但仍有文件被占用：{exc}；"
                f"残留目录 {tomb} 可稍后手工删除") from exc
        return target


# ── 读写 ─────────────────────────────────────────────────────────────────


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
    gen = raw.get("gen", {})
    if not isinstance(gen, dict):
        raise CaseError(f"{path}: [gen] 必须是表")
    references = gen.get("reference_images")
    if references is not None and not (
            isinstance(references, list) and all(isinstance(r, str) for r in references)):
        raise CaseError(f"{path}: [gen] reference_images 必须是字符串数组")
    assets = _parse_assets(raw.get("assets"), path)
    selected, roles = _parse_submit(raw.get("submit"), assets, flow, path)
    case = Case(
        name=name, flow=flow,
        source_path=str(source.get("path", "")) or None,
        sha256=source.get("sha256"),
        viewport=raw.get("viewport"), inner=raw.get("inner"),
        frames=raw.get("frames"), extract_current=raw.get("extract", {}).get("current"),
        assets=assets,
        submit_selected=selected,
        submit_roles=roles,
        adopt_round=raw.get("adopt", {}).get("round"),
        adopt_index=raw.get("adopt", {}).get("index"),
        merge_input=(_parse_merge(raw.get("merge"), path)
                     if flow in _MERGE_INPUT_KINDS else None),
    )
    allowed = (_PATCH_SECTIONS if flow == "patch"
               else _PATCHIMG_SECTIONS if flow == "patchimg"
               else _FULLREDRAW_IMG_SECTIONS if flow == "fullRedrawImg" else ())
    for section in _PATCH_SECTIONS:
        if raw.get(section) is not None and section not in allowed:
            raise CaseError(f"{path}: flow={flow} 不该有 [{section}]（patch 专属段）")
    if flow != "gen" and raw.get("gen") is not None:
        raise CaseError(f"{path}: [gen] 只属于 flow=gen")
    if flow != "relight" and raw.get("relight") is not None:
        raise CaseError(f"{path}: [relight] 只属于 flow=relight")
    if flow not in _MERGE_INPUT_KINDS and raw.get("merge") is not None:
        raise CaseError(f"{path}: [merge] 只属于 fullRedrawImg 与 fullredraw")
    if flow in _MERGE_INPUT_KINDS:
        _check_merge_input(case, path)
    if flow != "gen" and flow != "relight" and not case.source_path:
        raise CaseError(f"{path}: [source] 缺 path")
    if flow == "relight":
        case.relight = _parse_relight(raw.get("relight"), path)
        _check_relight_assets(case, path)
    if flow == "gen":
        case = _migrate_gen(case, references, path, root)
    return case


def save(case: Case, root: str | Path | None = None) -> Case:
    lines = [f"schema = {SCHEMA}", f'flow = "{case.flow}"']
    for asset in case.assets:
        lines += ["", "[[assets]]",
                  f"id = {_s(asset.id)}",
                  f"kind = {_s(asset.kind)}",
                  f"path = {_s(asset.path)}",
                  f"role = {_s(asset.role)}"]
        if asset.sha256:
            lines.append(f"sha256 = {_s(asset.sha256)}")
    if case.flow != "gen" and case.source_path is not None:
        lines += ["", "[source]", f"path = {_s(case.source_path)}"]
        if case.sha256:
            lines.append(f"sha256 = {_s(case.sha256)}")
    if case.submit_selected is not None:
        lines += ["", "[submit]", f"selected = {_toml_list(case.submit_selected)}"]
    if case.submit_roles:
        lines += ["", "[submit.roles]"]
        lines += [f"{_s(key)} = {_s(value)}"
                  for key, value in case.submit_roles.items()]
    if case.flow in ("patch", "patchimg"):
        sections = [("viewport", case.viewport), ("inner", case.inner)]
        if case.flow == "patch":
            sections.append(("frames", case.frames))
        for section, value in sections:
            if value:
                lines += ["", f"[{section}]"]
                lines += [_kv(k, v) for k, v in value.items()]
    if case.flow in ("patch", "patchimg", "fullRedrawImg") and case.extract_current:
        lines += ["", "[extract]", f"current = {_s(case.extract_current)}"]
    if case.adopt_round:
        lines += ["", "[adopt]", f"round = {_s(case.adopt_round)}"]
        if case.adopt_index is not None:
            lines.append(f"index = {int(case.adopt_index)}")
    if merge_input_kind(case) and case.merge_input:
        _check_merge_input(case, case_toml(case.name, root))
        lines += ["", "[merge]", f"input = {_s(case.merge_input)}"]
    if case.flow == "relight":
        # 槽位约束在每次写盘时检查，所以「多挂一条别的角色」在 add_assets 就失败，
        # 不会等到下次 load 才暴露。配对规则同样在写盘时检查：视频氛围必须配视频底版。
        _check_relight_assets(case, case_toml(case.name, root))
        check_relight_pair(case, case_toml(case.name, root))
        if case.relight:
            lines += ["", "[relight]"]
            lines += [_kv(key, value) for key, value in case.relight.items()
                      if key in _RELIGHT_KEYS]
    path = case_toml(case.name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return case


def _parse_assets(raw, path: Path) -> list[Asset]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CaseError(f"{path}: [[assets]] 必须是数组")
    out: list[Asset] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise CaseError(f"{path}: [[assets]] 每项必须是表")
        asset = Asset(
            id=str(item.get("id", "")),
            kind=str(item.get("kind", "")),
            path=str(item.get("path", "")).replace("\\", "/"),
            role=str(item.get("role", "")),
            sha256=item.get("sha256"),
        )
        _validate_asset(asset, path)
        if asset.id in seen:
            raise CaseError(f"{path}: 素材 id 重复：{asset.id}")
        seen.add(asset.id)
        out.append(asset)
    return out


def _flow_asset_ids(flow: str) -> set[str]:
    """流程产物在 ``[submit] selected`` 里的固定 id（不进 ``[[assets]]``）。

    patchimg 的 padded 图由装配器自动附带，不作为可勾选候选，因此返回空集。
    """
    if flow == "patch":
        return {"clip"}
    if flow == "fullredraw":
        return {"pad"}
    return set()


def _flow_asset_kinds(flow: str) -> dict[str, str]:
    """流程产物都是视频：clip 与 pad 由 pad/extract 从源视频生成。"""
    return {asset_id: "video" for asset_id in _flow_asset_ids(flow)}


def _require_role_kind(role: str, kind: str, where: str) -> None:
    """角色与素材种类不兼容时在这里拦住，接口直调也无法构造。"""
    reason = check_kinds(role, kind)
    if reason:
        raise CaseError(f"{where}：{reason}")


def _parse_submit(raw, assets: list[Asset], flow: str,
                  path: Path) -> tuple[list[str] | None, dict[str, str] | None]:
    """``[submit] selected`` 与可选的 ``[submit.roles]``。

    roles 用 id 做键，只能引用 selected 里的候选；缺省时由调用方回退到
    ``[[assets]] role``，旧 case 不需要迁移。
    """
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        raise CaseError(f"{path}: [submit] 必须是表")
    selected = raw.get("selected")
    if selected is None:
        if raw.get("roles") is not None:
            raise CaseError(f"{path}: [submit.roles] 需要同时有 [submit] selected")
        return None, None
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        raise CaseError(f"{path}: [submit] selected 必须是字符串数组")
    if len(set(selected)) != len(selected):
        raise CaseError(f"{path}: [submit] selected 有重复 id")
    known = {asset.id for asset in assets} | _flow_asset_ids(flow)
    unknown = sorted(set(selected) - known)
    if unknown:
        raise CaseError(f"{path}: [submit] selected 引用了不存在的候选：{unknown}")
    selected = list(selected)
    roles_raw = raw.get("roles")
    if roles_raw is None:
        return selected, None
    if not isinstance(roles_raw, dict):
        raise CaseError(f"{path}: [submit.roles] 必须是表")
    roles: dict[str, str] = {}
    kinds = {asset.id: asset.kind for asset in assets} | _flow_asset_kinds(flow)
    for key, value in roles_raw.items():
        asset_id, role = str(key), str(value)
        if asset_id not in selected:
            raise CaseError(
                f"{path}: [submit.roles] 的 {asset_id!r} 不在 [submit] selected 里")
        if role not in ROLES:
            raise CaseError(f"{path}: [submit.roles] 的 {asset_id!r} 角色 {role!r} 不合约")
        if role in RESERVED_ROLES:
            raise CaseError(
                f"{path}: [submit.roles] 的 {asset_id!r} 使用了保留角色 {role!r}")
        _require_role_kind(role, kinds[asset_id],
                           f"{path}: [submit.roles] 的 {asset_id!r}")
        roles[asset_id] = role
    return selected, roles or None


def _validate_asset(asset: Asset, path: Path) -> None:
    if asset.kind not in KINDS:
        raise CaseError(f"{path}: 素材 {asset.id} 的 kind 必须是 {' 或 '.join(KINDS)}")
    prefix = "v" if asset.kind == "video" else "i"
    if not re.fullmatch(rf"{prefix}\d+", asset.id):
        raise CaseError(
            f"{path}: 素材 id {asset.id!r} 不合约"
            f"（kind={asset.kind!r} 应为 {prefix}1、{prefix}2…）")
    if asset.role not in ROLES:
        raise CaseError(f"{path}: 素材 {asset.id} 的 role {asset.role!r} 不合约")
    _require_role_kind(asset.role, asset.kind, f"{path}: 素材 {asset.id}")
    if not asset.path:
        raise CaseError(f"{path}: 素材 {asset.id} 缺 path")
    rel = Path(asset.path)
    if rel.is_absolute() or ".." in rel.parts:
        raise CaseError(f"{path}: 素材 {asset.id} 的 path 必须是 case 目录内的相对路径")


# ── 旧 gen case 迁移 ─────────────────────────────────────────────────────


def _parse_relight(raw, path: Path) -> dict | None:
    """``[relight]`` 段：只认 ``_RELIGHT_KEYS``，键名写错按真实原因拒绝。

    这里只校验形状（键名、类型、``current`` 是 case 内的相对路径），不重复校验数值
    区间——强度、局部明暗与软裁切的允许范围只有 relight 引擎一处权威。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CaseError(f"{path}: [relight] 必须是表")
    unknown = sorted(set(raw) - set(_RELIGHT_KEYS))
    if unknown:
        raise CaseError(
            f"{path}: [relight] 不认识的键 {unknown}；可用：{'、'.join(_RELIGHT_KEYS)}")
    out: dict = {}
    for key in _RELIGHT_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "current" or key == "regions":
            if not isinstance(value, str) or not value:
                raise CaseError(f"{path}: [relight] {key} 必须是非空字符串")
            rel = Path(value)
            if rel.is_absolute() or ".." in rel.parts:
                raise CaseError(
                    f"{path}: [relight] {key} 必须是 case 目录内的相对路径")
        elif key in _RELIGHT_SLOTS:
            if not isinstance(value, str) or not re.fullmatch(r"[vi]\d+", value):
                raise CaseError(
                    f"{path}: [relight] {key} 必须是素材 id（v1、i2…），收到 {value!r}")
        elif key == "highlight_clip":
            if not isinstance(value, bool):
                raise CaseError(f"{path}: [relight] highlight_clip 必须是布尔值")
        elif key == "scene":
            if not isinstance(value, str):
                raise CaseError(f"{path}: [relight] scene 必须是字符串")
        elif key == "ref_frame":
            if isinstance(value, bool) or not isinstance(value, int):
                raise CaseError(f"{path}: [relight] ref_frame 必须是整数")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CaseError(f"{path}: [relight] {key} 必须是数字")
        out[key] = value
    return out


def check_relight_pair(case: Case, path: Path | None = None) -> None:
    """两个槽位都在时，视频氛围只能配视频底版。

    底版还没选时允许先把视频氛围候选放进库并选中（门控只提示缺底版）；坏组合只在
    两侧都存在时才成立。写盘（``save``）与运行入口都会调用这一条；``load`` 不调用，
    这样手写坏组合的 case 仍能被改成合法状态。
    """
    if case.flow != "relight":
        return
    gt, atmo = case.relight_slots()
    if gt is None or atmo is None:
        return
    if atmo.kind == "video" and gt.kind != "video":
        where = f"{path}: " if path is not None else ""
        raise CaseError(
            f"{where}氛围依据 {atmo.id} 是视频，但底版 {gt.id} 是图片；"
            "视频氛围只适用于视频底版")


def _check_relight_assets(case: Case, path: Path) -> None:
    """relight 的素材只承担两个角色，且 ``[relight]`` 的指针必须指对角色。

    库里可以有多条同角色候选（氛围图每改一版就多一条），这是「从素材库挑」能成立的
    前提；槽位是 ``[relight]`` 里的两把指针，不是角色本身。
    """
    stray = [a.id for a in case.assets if a.role not in (RELIGHT_GT, RELIGHT_ATMO)]
    if stray:
        raise CaseError(
            f"{path}: relight 的素材只能是 {RELIGHT_GT} 或 {RELIGHT_ATMO}，"
            f"这些素材用了别的角色：{'、'.join(stray)}")
    relight = case.relight or {}
    by_id = {asset.id: asset for asset in case.assets}
    for key, role in _RELIGHT_SLOTS.items():
        chosen = relight.get(key)
        if chosen is None:
            continue
        asset = by_id.get(chosen)
        if asset is None:
            raise CaseError(f"{path}: [relight] {key} 指向不存在的素材 {chosen}")
        if asset.role != role:
            raise CaseError(
                f"{path}: [relight] {key} 指向的 {chosen} 角色是 {asset.role}，"
                f"应为 {role}")


def _parse_merge(raw, path: Path) -> str | None:
    """``[merge]`` 段：只有 ``input``，值是素材 id；写错按真实原因拒绝。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CaseError(f"{path}: [merge] 必须是表")
    unknown = sorted(set(raw) - set(_MERGE_INPUT_KEYS))
    if unknown:
        raise CaseError(
            f"{path}: [merge] 不认识的键 {unknown}；可用：{'、'.join(_MERGE_INPUT_KEYS)}")
    value = raw.get("input")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[vi]\d+", value):
        raise CaseError(
            f"{path}: [merge] input 必须是素材 id（v1、i2…），收到 {value!r}")
    return value


def _merge_kind_label(kind: str) -> str:
    return "图片" if kind == "image" else "视频"


def _check_merge_input(case: Case, path: Path) -> None:
    """拼接输入指针必须指向本流程对应种类的素材：图片流读 21:9 画布图，视频流读画布视频。"""
    if not case.merge_input:
        return
    expected = merge_input_kind(case)
    asset = next((a for a in case.assets if a.id == case.merge_input), None)
    if asset is None:
        raise CaseError(
            f"{path}: [merge] input 指向不存在的素材 {case.merge_input}")
    if asset.kind != expected:
        raise CaseError(
            f"{path}: [merge] input 指向的 {asset.id} 是 {asset.kind}；"
            f"拼接输入必须是{_merge_kind_label(expected)}")


def _migrate_gen(case: Case, references: list[str] | None, path: Path,
                 root: str | Path | None) -> Case:
    """旧 ``[source]`` 与 ``[gen] reference_images`` → assets，并落盘一次。

    只在 load 时触发；源视频不存在时明确报错，不悄悄留下半迁移的 case.toml。
    """
    if case.assets and case.source_path:
        raise CaseError(
            f"{path}: gen case 同时有 [source] 与 [[assets]]；"
            f"源视频应只作为一条素材，请手动删掉 [source]")
    if not case.assets and case.source_path:
        src = _stored_path(case.source_path)
        if not src.is_file():
            raise CaseError(f"{path}: 旧 gen case 的源视频不在了，无法迁移：{src}")
        # 先在内存里摘掉 [source]，再复制：即使中途失败，落盘的也是“无源的
        # gen case + 已复制素材”，不会停在 source 与 assets 并存的状态。
        case.source_path = None
        case.sha256 = None
        case = _add_assets_to(case, [(src, "edit_master")], root)
    if references:
        pairs = [(Path(r), "subject_ref") for r in references]
        for src, _role in pairs:
            if not src.is_file():
                raise CaseError(f"{path}: [gen] reference_images 里的图片不在了：{src}")
        case = _add_assets_to(case, pairs, root)
    return case


# ── 素材增删改排（web/CLI 共用）──────────────────────────────────────────


def add_assets(name: str, pairs: list[tuple[str | Path, str]],
               root: str | Path | None = None) -> Case:
    """按 (本地文件, 角色) 复制进 assets/ 并追加到素材列表。

    角色必须由调用方显式给出（用户手动选择，不按顺序推断）。先整体校验再复制；
    复制中途失败时清掉本次已复制的文件，不留下半条记录。
    """
    case = load(name, root)
    return _add_assets_to(case, pairs, root)


def _add_assets_to(case: Case, pairs: list[tuple[str | Path, str]],
                   root: str | Path | None) -> Case:
    """已加载 case 的素材追加本体；迁移路径直接调用它，避免 load 递归。"""
    if not pairs:
        raise CaseError("没有要添加的素材")
    counters = {"video": _next_asset_number(case, "video"),
                "image": _next_asset_number(case, "image")}
    prepared: list[tuple[Path, str, str, str, str]] = []   # src, kind, role, id, filename
    assets_dir = case.dir(root) / "assets"
    existing_names = {Path(a.path).name for a in case.assets}
    for raw_path, role in pairs:
        src = Path(raw_path)
        if not src.is_file():
            raise CaseError(f"素材不存在：{src}")
        kind = kind_of(src)
        if role not in ROLES:
            raise CaseError(f"素材 {src.name} 的角色 {role!r} 不合约；可选：{', '.join(ROLES)}")
        if role in RESERVED_ROLES:
            raise CaseError(
                f"角色 {role} 由流程产物或关键帧工作台提供，不能给文件素材指定")
        _require_role_kind(role, kind, f"素材 {src.name}")
        prefix = "v" if kind == "video" else "i"
        asset_id = f"{prefix}{counters[kind]}"
        counters[kind] += 1
        stem = _safe_stem(src.stem)
        filename = f"{asset_id}_{stem}{src.suffix.lower()}"
        n = 2
        while filename in existing_names or (assets_dir / filename).exists():
            filename = f"{asset_id}_{stem}-{n}{src.suffix.lower()}"
            n += 1
        existing_names.add(filename)
        prepared.append((src, kind, role, asset_id, filename))

    copied: list[Path] = []
    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        for src, kind, role, asset_id, filename in prepared:
            target = assets_dir / filename
            shutil.copyfile(src, target)
            copied.append(target)
            case.assets.append(Asset(
                id=asset_id, kind=kind, path=f"assets/{filename}", role=role,
                sha256=file_sha256(target)))
    except OSError as exc:
        for p in copied:
            p.unlink(missing_ok=True)
        raise CaseError(f"复制素材失败：{exc}") from exc
    return save(case, root)


def remove_asset(name: str, asset_id: str, root: str | Path | None = None) -> Case:
    case = load(name, root)
    target = case.asset(asset_id).resolved(case, root)
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        raise CaseError(f"素材文件删不掉：{target}（{exc}）；记录未改动") from exc
    case.assets = [a for a in case.assets if a.id != asset_id]
    # 删掉拼接输入指向的素材时清空指针，回退到 adopt 结果；否则 load 会因悬空指针失败。
    if case.merge_input == asset_id:
        case.merge_input = None
    if case.submit_selected is not None:
        case.submit_selected = [item for item in case.submit_selected
                                if item != asset_id]
    if case.submit_roles:
        case.submit_roles.pop(asset_id, None)
        if not case.submit_roles:
            case.submit_roles = None
    return save(case, root)


def set_asset_role(name: str, asset_id: str, role: str,
                   root: str | Path | None = None) -> Case:
    case = load(name, root)
    if role not in ROLES:
        raise CaseError(f"角色 {role!r} 不合约；可选：{', '.join(ROLES)}")
    if role in RESERVED_ROLES:
        raise CaseError(f"角色 {role} 由流程产物或关键帧工作台提供，不能给文件素材指定")
    asset = case.asset(asset_id)
    _require_role_kind(role, asset.kind, f"素材 {asset_id}")
    asset.role = role
    return save(case, root)


def reorder_assets(name: str, ids: list[str], root: str | Path | None = None) -> Case:
    case = load(name, root)
    current = {a.id for a in case.assets}
    if sorted(ids) != sorted(current) or len(set(ids)) != len(ids):
        raise CaseError(f"排序必须给出全部素材 id，且不能重复；当前：{sorted(current)}")
    by_id = {a.id: a for a in case.assets}
    case.assets = [by_id[i] for i in ids]
    return save(case, root)


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _VIDEO_EXTS:
        return "video"
    if suffix in _IMAGE_EXTS:
        return "image"
    raise CaseError(
        f"不支持的素材类型：{path.name}（视频 {'/'.join(sorted(_VIDEO_EXTS))}，"
        f"图片 {'/'.join(sorted(_IMAGE_EXTS))}）")


def _next_asset_number(case: Case, kind: str) -> int:
    prefix = "v" if kind == "video" else "i"
    used = [int(a.id[1:]) for a in case.assets
            if a.id.startswith(prefix) and a.id[1:].isdigit()]
    return max(used, default=0) + 1


def _safe_stem(stem: str) -> str:
    clean = re.sub(r"[^\w.-]", "_", stem, flags=re.UNICODE).strip("._-")
    return clean or "asset"


# ── 草稿与采用指针 ───────────────────────────────────────────────────────


def set_adopt(name: str, round_key: str, root: str | Path | None = None, *,
              index: int | None = None) -> Case:
    """写 adopt 指针；``index`` 只对 patchimg 有意义（图片候选下标）。"""
    case = load(name, root)
    case.adopt_round = round_key
    case.adopt_index = index
    return save(case, root)


def set_submit_selected(name: str, selected: list[str] | None,
                        root: str | Path | None = None, *,
                        roles: dict[str, str] | None = None) -> Case:
    """写 ``[submit] selected`` 与可选的 ``[submit.roles]``；``None`` 清除两段。

    只传 ``selected`` 的旧调用方保留仍在选中的旧角色覆盖；新 id 没有覆盖时
    回退到 ``[[assets]] role``。``roles`` 给定时只接受 selected 里的 id。
    """
    case = load(name, root)
    if selected is None:
        case.submit_selected = None
        case.submit_roles = None
        return save(case, root)
    if not all(isinstance(item, str) for item in selected):
        raise CaseError("submit selected 必须是字符串数组")
    if len(set(selected)) != len(selected):
        raise CaseError("submit selected 有重复 id")
    known = {asset.id for asset in case.assets} | _flow_asset_ids(case.flow)
    unknown = sorted(set(selected) - known)
    if unknown:
        raise CaseError(f"submit selected 引用了不存在的候选：{unknown}")
    selected = list(selected)
    if roles is None:
        current = case.submit_roles or {}
        roles = {item: current[item] for item in selected if item in current} or None
    else:
        clean: dict[str, str] = {}
        kinds = {asset.id: asset.kind for asset in case.assets} | _flow_asset_kinds(case.flow)
        for key, value in roles.items():
            asset_id, role = str(key), str(value)
            if asset_id not in selected:
                raise CaseError(f"submit roles 的 {asset_id!r} 不在 selected 里")
            if role not in ROLES:
                raise CaseError(f"submit roles 的 {asset_id!r} 角色 {role!r} 不合约")
            if role in RESERVED_ROLES:
                raise CaseError(f"submit roles 的 {asset_id!r} 使用了保留角色 {role!r}")
            _require_role_kind(role, kinds[asset_id], f"submit roles 的 {asset_id!r}")
            clean[asset_id] = role
        roles = clean or None
    case.submit_selected = selected
    case.submit_roles = roles
    return save(case, root)


def patch_default_draft(source_width: int, source_frames: int) -> dict:
    """patch 建立时的默认草稿三段（web 抽屉与 CLI ``case new`` 共用）。

    视口取 16:9、宽度封顶 1920；inner 先放中央一半；帧范围铺满全片。
    都只是起点，之后由 web pick 或手改 toml 逐项收敛。
    """
    vpw = min(1920, max(1, int(source_width)))
    vph = int(vpw * 9 / 16)
    return {
        "viewport": {"yaw_deg": 0.0, "pitch_deg": 0.0, "fov_h_deg": 59.0,
                     "width": vpw, "height": vph},
        "inner": {"x": vpw // 4, "y": vph // 4, "width": vpw // 2, "height": vph // 2},
        "frames": {"start": 0, "end": max(0, int(source_frames) - 1)},
    }


def patchimg_default_draft(source_width: int) -> dict:
    """patchimg 建立时的默认草稿：视口与 inner，没有时间段。"""
    vpw = min(1920, max(1, int(source_width)))
    vph = int(vpw * 9 / 16)
    return {
        "viewport": {"yaw_deg": 0.0, "pitch_deg": 0.0, "fov_h_deg": 59.0,
                     "width": vpw, "height": vph},
        "inner": {"x": vpw // 4, "y": vph // 4, "width": vpw // 2, "height": vph // 2},
    }


def set_draft(name: str, *, viewport: dict | None = None, inner: dict | None = None,
              frames: dict | None = None, root: str | Path | None = None) -> Case:
    """pick 草稿写入（web pick / CLI 都是调用方；唯一权威在 case 模型）。

    只更新传入的段，未传的段保持原样——pick 是迭代动作，逐字段收敛。
    patchimg 没有时间轴，传 ``frames`` 直接拒绝。
    """
    case = load(name, root)
    if case.flow not in ("patch", "patchimg"):
        raise CaseError(f"case {name} 是 {case.flow} 流，没有 pick 草稿")
    if case.flow == "patchimg" and frames is not None:
        raise CaseError(
            f"case {name} 是 patchimg 流，没有 [frames]；局部区域由 viewport/inner 定义")
    if viewport is not None:
        case.viewport = dict(viewport)
    if inner is not None:
        case.inner = dict(inner)
    if frames is not None:
        case.frames = dict(frames)
    return save(case, root)


def draft_ready(case: Case) -> str | None:
    """草稿是否齐到可以 extract：缺哪段返回哪段名，齐了返回 None。"""
    if case.flow == "patch":
        required = ("viewport", "inner", "frames")
    elif case.flow == "patchimg":
        required = ("viewport", "inner")
    else:
        return None
    for section in required:
        if not getattr(case, section):
            return section
    return None


def set_relight(name: str, values: dict, root: str | Path | None = None) -> Case:
    """写 ``[relight]``：只改传入的键，未知键拒绝，值传 ``None`` 表示删掉该键。

    合并后的整段再走一遍 ``_parse_relight``，所以类型错在写盘之前就被拒绝，不会留下
    一个下次打开才报错的 case.toml。
    """
    case = load(name, root)
    if case.flow != "relight":
        raise CaseError(f"case {name} 是 {case.flow} 流，没有 [relight] 草稿")
    unknown = sorted(set(values) - set(_RELIGHT_KEYS))
    if unknown:
        raise CaseError(
            f"[relight] 不认识的键 {unknown}；可用：{'、'.join(_RELIGHT_KEYS)}")
    merged = dict(case.relight or {})
    for key, value in values.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    case.relight = _parse_relight(merged, case_toml(name, root)) or None
    return save(case, root)


def add_relight_candidate(name: str, role: str, path: str | Path,
                          root: str | Path | None = None) -> tuple[Case, str]:
    """把一份文件加进 relight 的素材库（底版或氛围图候选），并选中它。

    库里可以有多条同角色候选：美术每改一版氛围图就多一条，挑哪条由 ``[relight]`` 的
    指针决定。返回加入后的 case 与新素材 id。
    """
    if role not in (RELIGHT_GT, RELIGHT_ATMO):
        raise CaseError(f"relight 没有 {role!r} 角色；只有 {RELIGHT_GT} 与 {RELIGHT_ATMO}")
    case = load(name, root)
    if case.flow != "relight":
        raise CaseError(f"case {name} 是 {case.flow} 流，没有 relight 素材")
    src = Path(path)
    if not src.is_file():
        raise CaseError(f"文件不存在：{src}")
    kind = kind_of(src)
    gt, atmo = case.relight_slots()
    if role == RELIGHT_ATMO and kind == "video" and gt is not None and gt.kind != "video":
        raise CaseError(
            f"氛围依据 {src.name} 是视频，但底版 {gt.id} 是图片；"
            "视频氛围只适用于视频底版")
    if role == RELIGHT_GT and kind != "video" and atmo is not None and atmo.kind == "video":
        raise CaseError(
            f"底版 {src.name} 是{kind}，但氛围依据 {atmo.id} 是视频；"
            "视频氛围只适用于视频底版")
    digest = file_sha256(src)
    same = next((a for a in case.assets
                 if a.role == role and a.sha256 == digest), None)
    if same is not None:
        # 同一个文件重复加入没有意义，直接指向已有的那一条。
        case = set_relight_pick(name, "gt" if role == RELIGHT_GT else "atmo",
                                same.id, root)
        return case, same.id
    count = len(case.assets)
    case = _add_assets_to(case, [(src, role)], root)
    asset_id = case.assets[count].id
    case = set_relight_pick(name, "gt" if role == RELIGHT_GT else "atmo",
                            asset_id, root)
    return case, asset_id


def set_relight_pick(name: str, slot: str, asset_id: str,
                     root: str | Path | None = None) -> Case:
    """把 ``[relight]`` 的 ``gt``/``atmo`` 指向库里某一条候选。"""
    if slot not in _RELIGHT_SLOTS:
        raise CaseError(f"relight 没有 {slot!r} 槽位；只有 gt 与 atmo")
    case = load(name, root)
    if case.flow != "relight":
        raise CaseError(f"case {name} 是 {case.flow} 流，没有 relight 槽位")
    wanted = _RELIGHT_SLOTS[slot]
    asset = case.asset(asset_id)
    if asset.role != wanted:
        raise CaseError(
            f"素材 {asset_id} 的角色是 {asset.role}，不能放进 {slot} 槽位（应 {wanted}）")
    return set_relight(name, {slot: asset_id}, root)


def set_merge_input(name: str, asset_id: str | None,
                    root: str | Path | None = None) -> Case:
    """设置拼接输入；``None`` 清空指针、回退 adopt 结果。种类由流程决定。"""
    case = load(name, root)
    expected = merge_input_kind(case)
    if expected is None:
        raise CaseError(f"case {name} 是 {case.flow} 流，没有 [merge] 拼接输入")
    if asset_id:
        asset = case.asset(asset_id)
        if asset.kind != expected:
            hint = ("21:9 画布图，不是 2:1 源图或成片" if expected == "image"
                    else "21:9 画布视频，不是 2:1 源片或成片")
            raise CaseError(
                f"素材 {asset_id} 是 {asset.kind}；拼接输入必须是"
                f"{_merge_kind_label(expected)}（{hint}）")
    case.merge_input = asset_id or None
    return save(case, root)


def add_merge_input(name: str, path: str | Path,
                    root: str | Path | None = None) -> tuple[Case, str]:
    """把一份文件以拼接输入角色加入素材库并选中它（决策 67/78）。

    同一 sha256 的同类文件已经在本 case 里时直接指向那一条，不重复拷贝；这样从
    素材库再传一遍同一份编辑稿不会产生两份。返回 ``(case, 素材 id)``。
    """
    case = load(name, root)
    expected = merge_input_kind(case)
    if expected is None:
        raise CaseError(f"case {name} 是 {case.flow} 流，没有 [merge] 拼接输入")
    src = Path(path)
    if not src.is_file():
        raise CaseError(f"文件不存在：{src}")
    if kind_of(src) != expected:
        hint = ("png/jpg/webp 的 21:9 画布图" if expected == "image"
                else "mp4/mov/mkv/avi 的 21:9 画布视频")
        raise CaseError(
            f"{src.name} 不是{_merge_kind_label(expected)}；拼接输入必须是 {hint}")
    digest = file_sha256(src)
    same = next((a for a in case.assets
                 if a.kind == expected and a.sha256 == digest), None)
    if same is None:
        case = _add_assets_to(case, [(src, MERGE_INPUT)], root)
        same = case.assets[-1]
    return set_merge_input(name, same.id, root), same.id


def set_extract_current(name: str, version: str, root: str | Path | None = None) -> Case:
    case = load(name, root)
    if case.flow not in ("patch", "patchimg", "fullRedrawImg"):
        raise CaseError(f"case {name} 是 {case.flow} 流，没有 extracts/ 版本")
    case.extract_current = version
    return save(case, root)


def _stored_path(raw: str) -> Path:
    """解析 case.toml 里的路径；旧文件的相对路径相对仓库根（当前工作目录）解析。"""
    p = Path(raw)
    if not p.is_absolute() and not p.is_file():
        alt = REPO_ROOT / p
        if alt.is_file():
            return alt
    return p


def check_source(case: Case, root: str | Path | None = None) -> Path:
    """patch / patchimg / fullredraw 流的守卫：确认源文件还是创建时的那份。"""
    if not case.source_path:
        raise CaseError(f"case {case.name} 没有源文件")
    src = _stored_path(case.source_path)
    if not src.is_file():
        raise CaseError(f"源文件不在了：{src}（case.toml 记录的路径）")
    if case.sha256 and file_sha256(src) != case.sha256:
        raise CaseError(
            f"源文件内容与 case 创建时不同（sha256 不匹配）：{src}。"
            f"换源 = 新 case；这份几何契约只对创建时那份源有效。"
        )
    return src


def check_assets(case: Case, root: str | Path | None = None,
                 selected: list[str] | None = None) -> list[tuple[Asset, Path]]:
    """三流程通用的守卫：每个素材都在、且内容与添加时一致。

    ``selected`` 只校验本次提交集；``None`` 校验全部素材。
    """
    keep = None if selected is None else set(selected)
    out: list[tuple[Asset, Path]] = []
    for asset in case.assets:
        if keep is not None and asset.id not in keep:
            continue
        target = asset.resolved(case, root)
        if not target.is_file():
            raise CaseError(f"素材文件不在了：{target}（case.toml 记录的路径）")
        if asset.sha256 and file_sha256(target) != asset.sha256:
            raise CaseError(
                f"素材 {asset.id} 的内容与添加时不同（sha256 不匹配）：{target}。"
                f"请重新添加，或删除后换文件。")
        out.append((asset, target))
    return out


def _s(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join(_s(item) for item in values) + "]"


def _kv(key: str, value) -> str:
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{key} = {value}"
    if isinstance(value, float):
        return f"{key} = {value!r}"
    return f"{key} = {_s(str(value))}"
