# -*- mode: python ; coding: utf-8 -*-
"""vrstudio 的 PyInstaller 目录式构建（决策 102）。

产物 ``dist/vrstudio/vrstudio.exe`` 与 ``_internal/``。前端产物、``roles.json``、
``config/default.toml`` 收进 ``_internal``，因为代码按 ``sys._MEIPASS`` 找它们；
``bin/`` 与 ``tools/`` 由安装器放到 exe 旁边，代码按 ``parents[3]``/``app_dir()`` 找。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "src" / "vrstudio" / "web" / "static" / "next"), "vrstudio/web/static/next"),
    (str(ROOT / "src" / "vrstudio" / "roles.json"), "vrstudio"),
    (str(ROOT / "config" / "default.toml"), "config"),
]

# uvicorn 的 loop / protocol / lifespan 实现是运行时按名导入的，必须显式收集。
hiddenimports = (
    collect_submodules("uvicorn")
    + ["anyio", "h11", "psutil", "typer", "click"]
)

a = Analysis(
    [str(ROOT / "packaging" / "entry_vrstudio.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="vrstudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="vrstudio",
)
