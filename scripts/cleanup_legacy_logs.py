"""列出并清理旧的按操作命名的日志文件。

Phase 5 之后新日志只出现在 ``logs/serve/``（事件）与 ``logs/tasks/``（任务原始
输出）。本脚本只处理 ``logs/`` 根目录下的旧前缀文件，默认只打印清单；加
``--yes`` 才删除。
"""

from __future__ import annotations

import argparse
import time

from vrstudio.progress import logs_dir

LEGACY_PREFIXES = (
    "web_", "proxy_", "extract_", "extract_e2e_", "merge_", "assemble_",
    "restore_", "restore_good_", "recompose_", "pad_", "register_",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="确认删除")
    args = parser.parse_args()

    root = logs_dir()
    files = [
        path for path in root.iterdir()
        if path.is_file() and path.name.startswith(LEGACY_PREFIXES)
    ]
    total = sum(path.stat().st_size for path in files)
    print(f"旧日志：{len(files)} 个，共 {total / 1024 / 1024:.1f} MB")
    for path in sorted(files, key=lambda item: item.stat().st_mtime,
                       reverse=True)[:20]:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        print(f"  {stamp}  {path.name}  {path.stat().st_size / 1024:.1f} KB")
    if not args.yes:
        print("未删除；确认后加 --yes 重跑。")
        return 0
    for path in files:
        path.unlink(missing_ok=True)
    print(f"已删除 {len(files)} 个文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
