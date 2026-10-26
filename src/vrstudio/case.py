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
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT

SCHEMA = 1
FLOWS = ("patch", "fullredraw", "gen")

#: 与 vrgen 的 job 名同一套约束：case 名是 cases/ 下的目录名。
_CASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_PATCH_SECTIONS = ("viewport", "inner", "frames", "extract")

#: 素材种类与角色受控词表。mask 与首尾原始帧也在词表里（旧 case 与工作台需要
#: 解析它们），但不能作为用户可添加的 ``[[assets]]`` 角色——它们由流程产物提供。
KINDS = ("video", "image")
ROLE_LABELS = {
    "edit_master": "编辑母版（时间轴、构图与动作基准）",
    "motion_ref": "动作参考（只提供动作节奏与路径）",
    "space_ref": "空间参考（只提供机位、构图与起终点位置）",
    "scene_ref": "场景参考（只提供构图、背景与光照）",
    "subject_ref": "主体参考（只定义角色外观）",
    "first_frame": "首帧定稿图（视频第一帧，锁起点构图与人物外观）",
    "last_frame": "尾帧定稿图（视频最后一帧，锁终点位置、大小与朝向）",
    "mid_frame": "中间过渡帧（首尾间距过大时补充，预留）",
    "first_frame_raw": "首帧原始抽取图（工作台输入底图，不是生成素材）",
    "last_frame_raw": "尾帧原始抽取图（工作台输入底图，不是生成素材）",
    "mask": "编辑区域标注（流程产物，装配器自动附带）",
}
ROLES = tuple(ROLE_LABELS)

#: 用户不能给文件素材选的角色：由流程产物或工作台提供。
RESERVED_ROLES = ("mask", "first_frame_raw", "last_frame_raw")

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

    def dir(self, root: str | Path | None = None) -> Path:
        return case_dir(self.name, root)

    def asset(self, asset_id: str) -> Asset:
        for item in self.assets:
            if item.id == asset_id:
                return item
        raise CaseError(f"case {self.name} 没有素材 {asset_id!r}")

    def master(self) -> Asset | None:
        """提示词几何与上传基准：按契约的回退链取第一条可用素材。"""
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
            if _kind_of(src) != "video":
                raise CaseError(f"gen 的 --source 必须是视频：{src.name}")
        case = Case(name=name, flow=flow)
        save(case, root)
        if source is not None:
            # 新建 gen 仍接受 --source：复制为 v1/edit_master，与素材步骤同一条路。
            return add_assets(name, [(source, "edit_master")], root=root)
        return case
    if source is None:
        raise CaseError(f"flow={flow} 必须指定源视频")
    src = Path(source)
    if not src.is_file():
        raise CaseError(f"源视频不存在：{src}")
    # patch 的 merge 要重读源视频贴回，源变了就静默错片——创建时就把身份钉死；
    # fullredraw 在 pad 之后源不再被读（manifest 已含源身份），哈希可选。
    sha = file_sha256(src) if (compute_hash and flow == "patch") else None
    case = Case(name=name, flow=flow, source_path=str(src), sha256=sha)
    return save(case, root)


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
    )
    if flow != "patch":
        for section in _PATCH_SECTIONS:
            if raw.get(section) is not None:
                raise CaseError(f"{path}: flow={flow} 不该有 [{section}]（patch 专属段）")
    if flow != "gen" and raw.get("gen") is not None:
        raise CaseError(f"{path}: [gen] 只属于 flow=gen")
    if flow != "gen" and not case.source_path:
        raise CaseError(f"{path}: [source] 缺 path")
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
    """流程产物在 ``[submit] selected`` 里的固定 id（不进 ``[[assets]]``）。"""
    if flow == "patch":
        return {"clip"}
    if flow == "fullredraw":
        return {"pad"}
    return set()


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
    if not asset.path:
        raise CaseError(f"{path}: 素材 {asset.id} 缺 path")
    rel = Path(asset.path)
    if rel.is_absolute() or ".." in rel.parts:
        raise CaseError(f"{path}: 素材 {asset.id} 的 path 必须是 case 目录内的相对路径")


# ── 旧 gen case 迁移 ─────────────────────────────────────────────────────


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
        kind = _kind_of(src)
        if role not in ROLES:
            raise CaseError(f"素材 {src.name} 的角色 {role!r} 不合约；可选：{', '.join(ROLES)}")
        if role in RESERVED_ROLES:
            raise CaseError(
                f"角色 {role} 由流程产物或关键帧工作台提供，不能给文件素材指定")
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
    case.asset(asset_id).role = role
    if case.submit_selected and asset_id in case.submit_selected:
        roles = dict(case.submit_roles or {})
        roles[asset_id] = role
        case.submit_roles = roles
    return save(case, root)


def reorder_assets(name: str, ids: list[str], root: str | Path | None = None) -> Case:
    case = load(name, root)
    current = {a.id for a in case.assets}
    if sorted(ids) != sorted(current) or len(set(ids)) != len(ids):
        raise CaseError(f"排序必须给出全部素材 id，且不能重复；当前：{sorted(current)}")
    by_id = {a.id: a for a in case.assets}
    case.assets = [by_id[i] for i in ids]
    return save(case, root)


def _kind_of(path: Path) -> str:
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


def set_adopt(name: str, round_key: str, root: str | Path | None = None) -> Case:
    case = load(name, root)
    case.adopt_round = round_key
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
        for key, value in roles.items():
            asset_id, role = str(key), str(value)
            if asset_id not in selected:
                raise CaseError(f"submit roles 的 {asset_id!r} 不在 selected 里")
            if role not in ROLES:
                raise CaseError(f"submit roles 的 {asset_id!r} 角色 {role!r} 不合约")
            if role in RESERVED_ROLES:
                raise CaseError(f"submit roles 的 {asset_id!r} 使用了保留角色 {role!r}")
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


def set_draft(name: str, *, viewport: dict | None = None, inner: dict | None = None,
              frames: dict | None = None, root: str | Path | None = None) -> Case:
    """pick 草稿写入（web pick / CLI 都是调用方；唯一权威在 case 模型）。

    只更新传入的段，未传的段保持原样——pick 是迭代动作，逐字段收敛。
    """
    case = load(name, root)
    if case.flow != "patch":
        raise CaseError(f"case {name} 是 {case.flow} 流，没有 pick 草稿")
    if viewport is not None:
        case.viewport = dict(viewport)
    if inner is not None:
        case.inner = dict(inner)
    if frames is not None:
        case.frames = dict(frames)
    return save(case, root)


def draft_ready(case: Case) -> str | None:
    """草稿是否齐到可以 extract：缺哪段返回哪段名，齐了返回 None。"""
    if case.flow != "patch":
        return None
    for section in ("viewport", "inner", "frames"):
        if not getattr(case, section if section != "frames" else "frames"):
            return section
    return None


def set_extract_current(name: str, version: str, root: str | Path | None = None) -> Case:
    case = load(name, root)
    if case.flow != "patch":
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
    """patch / fullredraw 流的守卫：生成或合并前确认源视频还是创建时的那份。"""
    if not case.source_path:
        raise CaseError(f"case {case.name} 没有源视频")
    src = _stored_path(case.source_path)
    if not src.is_file():
        raise CaseError(f"源视频不在了：{src}（case.toml 记录的路径）")
    if case.sha256 and file_sha256(src) != case.sha256:
        raise CaseError(
            f"源视频内容与 case 创建时不同（sha256 不匹配）：{src}。"
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
