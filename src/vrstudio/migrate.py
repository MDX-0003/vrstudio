"""一次性迁移脚本。

1. 把 patch case 的旧 ``out/<版本>.mp4`` 改名为 ``_space.mp4``。

新命名约定见决策 31：空间合并输出 ``<版本>_space.mp4``，时间合并输出
``<版本>_frame.mp4``。旧的 ``<版本>.mp4`` 不在新回退链里，本模块只负责把
已有产物改成新名字；目标已存在时跳过，不覆盖。

只处理 flow=patch 的 case；fullredraw 的 ``out/<轮次>.mp4`` 不动。默认只
输出计划，传 ``apply=True`` 才真正改名。

2. 把图片流程的 ``still`` 命名改成 ``image``（决策 54）。

``extracts/<版本>/still.json -> image.json``、``still_pad.json -> image_pad.json``、
``out/<版本>_still.png -> <版本>_image.png``，``derived/`` 里的副本同样处理。
只处理 flow=patchimg/fullRedrawImg 的 case；目标已存在时跳过，不做双读。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import case as case_mod

#: 图片流程旧文件名 → 新文件名。顺序即处理顺序。
IMAGE_RENAMES = {
    "still.json": "image.json",
    "still_pad.json": "image_pad.json",
}


@dataclass(frozen=True)
class Rename:
    case: str
    source: Path
    target: Path


def migrate_out_names(cases_root: str | Path | None = None,
                      *, apply: bool = False) -> tuple[list[Rename], list[str]]:
    """返回 (计划/已执行的改名, 跳过或冲突说明)。"""
    root = case_mod.cases_root(cases_root)
    if not root.is_dir():
        return [], [f"cases 目录不存在：{root}"]

    done: list[Rename] = []
    problems: list[str] = []
    for toml in sorted(root.glob("*/case.toml")):
        name = toml.parent.name
        try:
            case = case_mod.load(name, root)
        except (case_mod.CaseError, OSError) as exc:
            problems.append(f"{name}: 跳过，case.toml 读取失败：{exc}")
            continue
        if case.flow != "patch":
            continue

        out = case.dir(root) / "out"
        if not out.is_dir():
            continue
        extracts = case.dir(root) / "extracts"
        try:
            versions = ({p.name for p in extracts.iterdir() if p.is_dir()}
                        if extracts.is_dir() else set())
        except OSError as exc:
            problems.append(f"{name}: 跳过，extracts 读取失败：{exc}")
            continue

        for source in sorted(out.glob("*.mp4")):
            stem = source.stem
            if stem.endswith("_space") or stem.endswith("_frame"):
                continue
            if versions and stem not in versions:
                problems.append(
                    f"{source}: 跳过，文件名不是已记录的抽取版本")
                continue
            target = source.with_name(f"{stem}_space.mp4")
            if target.exists():
                problems.append(f"{source}: 跳过，目标已存在 {target.name}")
                continue
            if apply:
                try:
                    source.rename(target)
                except OSError as exc:
                    problems.append(f"{source}: 改名失败：{exc}")
                    continue
            done.append(Rename(name, source, target))
    return done, problems


def _rewrite_manifest_kind(path: Path, apply: bool) -> bool:
    """把 image_pad.json 里的 ``kind: "still"`` 改成 ``image``；返回是否需要改。

    改名只动了文件名，manifest 的内部字段还是旧值；新读取端要求 ``kind`` 是
    ``image``，不一起改的话几何读不出来（提示词展开与 stitch 都会失败）。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict) or raw.get("kind") != "still":
        return False
    if apply:
        raw["kind"] = "image"
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    return True


def migrate_image_names(cases_root: str | Path | None = None,
                        *, apply: bool = False) -> tuple[list[Rename], list[str]]:
    """图片流程的 still → image 改名；返回 (计划/已执行的改名, 跳过或冲突说明)。

    两步都做：``still*`` 文件改名，以及已改名 manifest 里 ``kind`` 字段的修正。
    两步都是幂等的，重复跑只会报 0 个。
    """
    root = case_mod.cases_root(cases_root)
    if not root.is_dir():
        return [], [f"cases 目录不存在：{root}"]

    done: list[Rename] = []
    problems: list[str] = []
    for toml in sorted(root.glob("*/case.toml")):
        name = toml.parent.name
        try:
            case = case_mod.load(name, root)
        except (case_mod.CaseError, OSError) as exc:
            problems.append(f"{name}: 跳过，case.toml 读取失败：{exc}")
            continue
        if case.flow not in case_mod.IMAGE_FLOWS:
            continue
        cdir = case.dir(root)
        for directory in (cdir / "extracts", cdir / "derived"):
            if not directory.is_dir():
                continue
            for old, new in IMAGE_RENAMES.items():
                for source in sorted(directory.glob(f"*/{old}")) + sorted(
                        directory.glob(old)):
                    target = source.with_name(new)
                    if target.exists():
                        problems.append(f"{source}: 跳过，目标已存在 {target.name}")
                        continue
                    if apply:
                        try:
                            source.rename(target)
                        except OSError as exc:
                            problems.append(f"{source}: 改名失败：{exc}")
                            continue
                    done.append(Rename(name, source, target))
            # 已经叫 image_pad.json 的文件也要查一遍 kind 字段（改名跑过之后的第二次执行）。
            for manifest in sorted(directory.glob("*/image_pad.json")) + sorted(
                    directory.glob("image_pad.json")):
                if _rewrite_manifest_kind(manifest, apply):
                    done.append(Rename(name, manifest, manifest))
        out = cdir / "out"
        if out.is_dir():
            for source in sorted(out.glob("*_still.png")):
                target = source.with_name(source.name[:-len("_still.png")] + "_image.png")
                if target.exists():
                    problems.append(f"{source}: 跳过，目标已存在 {target.name}")
                    continue
                if apply:
                    try:
                        source.rename(target)
                    except OSError as exc:
                        problems.append(f"{source}: 改名失败：{exc}")
                        continue
                done.append(Rename(name, source, target))
    return done, problems
