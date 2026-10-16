"""``python -m vrstudio`` 入口（测试与 web 任务队列用它起 CLI）。"""

from __future__ import annotations

from .cli import app

if __name__ == "__main__":
    app()
