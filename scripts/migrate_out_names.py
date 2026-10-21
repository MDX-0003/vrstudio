"""把旧 patch 产物 out/<版本>.mp4 改名为 out/<版本>_space.mp4。

默认只打印计划；确认后加 --apply。目标已存在时跳过，不覆盖。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vrstudio.migrate import migrate_out_names  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=None,
                        help="cases 根目录；默认仓库 cases/")
    parser.add_argument("--apply", action="store_true",
                        help="真正执行改名；默认 dry-run")
    args = parser.parse_args(argv)

    done, problems = migrate_out_names(args.cases_dir, apply=args.apply)
    verb = "已改名" if args.apply else "计划改名"
    for item in done:
        print(f"[{verb}] {item.case}: {item.source.name} -> {item.target.name}")
    for problem in problems:
        print(f"[跳过] {problem}")
    print(f"{verb} {len(done)} 个；跳过/冲突 {len(problems)} 个")
    if not args.apply:
        print("这是 dry-run；确认后加 --apply 执行。")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
