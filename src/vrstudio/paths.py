"""程序目录、资源目录与可写数据根的唯一解析点（决策 102）。

源码检出、绿色版与 PyInstaller 安装版共用这一份规则：

- :func:`app_dir`：冻结时是 exe 所在目录，源码时是仓库根。``bin\\``、``tools\\``、
  ``vrgen\\`` 这类同级的可执行资源按它找。
- :func:`resource_dir`：只读资源的根。冻结时是 ``sys._MEIPASS``（PyInstaller 6 的
  ``_internal``），源码时是仓库根；``config\\default.toml``、前端产物、``roles.json``、
  vrgen 的 ``templates\\`` 都在它下面。
- :func:`data_root`：可写数据根，读 ``[paths] data_root``；空表示跟随
  :func:`app_dir`。``cases``、``sources``、``vrgen_runs``、``logs`` 都挂在它下面。
- :func:`user_config_dir`：``%APPDATA%\\vrstudio``，安装版写本机配置的位置。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """PyInstaller 运行时为真；源码检出与 ``uv run`` 为假。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """程序目录：冻结时 exe 所在目录，源码时仓库根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    """只读资源根：冻结时 ``sys._MEIPASS``，源码时程序目录。"""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else app_dir()


def user_config_dir() -> Path:
    """本机配置目录：Windows 用 ``%APPDATA%\\vrstudio``，其它环境退回 ``~/.config/vrstudio``。"""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "vrstudio"
    return Path.home() / ".config" / "vrstudio"


def data_root() -> Path:
    """可写数据根；相对路径按程序目录解析，空值表示跟随程序目录。"""
    from . import config as config_mod  # 延迟导入：config 自己要用 resource_dir()

    raw = str(config_mod.active().get("paths.data_root", "") or "").strip()
    if not raw:
        return app_dir()
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    return expanded if expanded.is_absolute() else app_dir() / expanded
