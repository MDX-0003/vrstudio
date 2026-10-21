"""一次性迁移：把 patch case 的旧 ``out/<版本>.mp4`` 改名为 ``_space.mp4``。

新命名约定见决策 31：空间合并输出 ``<版本>_space.mp4``，时间合并输出
``<版本>_frame.mp4``。旧的 ``<版本>.mp4`` 不在新回退链里，本模块只负责把
已有产物改成新名字；目标已存在时跳过，不覆盖。

只处理 flow=patch 的 case；fullredraw 的 ``out/<轮次>.mp4`` 不动。默认只
输出计划，传 ``apply=True`` 才真正改名。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import case as case_mod


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
