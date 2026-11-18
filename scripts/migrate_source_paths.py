"""把 case.toml 的 [source] path 从绝对路径改成仓库根相对写法（决策 101）。

默认只打印计划；确认后加 --apply。落在仓库外的源文件保留绝对路径并列为待人工处理。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vrstudio.migrate import migrate_source_paths  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=None,
                        help="cases 根目录；默认仓库 cases/")
    parser.add_argument("--apply", action="store_true",
                        help="真正写回 case.toml；默认 dry-run")
    args = parser.parse_args(argv)

    done, problems = migrate_source_paths(args.cases_dir, apply=args.apply)
    verb = "已改写" if args.apply else "计划改写"
    for item in done:
        print(f"[{verb}] {item.case}: {item.old} -> {item.new}")
    for problem in problems:
        print(f"[跳过] {problem}")
    print(f"{verb} {len(done)} 个；跳过/待人工处理 {len(problems)} 个")
    if not args.apply:
        print("这是 dry-run；确认后加 --apply 执行。")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
