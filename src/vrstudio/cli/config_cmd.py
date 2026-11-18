"""``vrstudio config`` —— 查看与修改本机配置里的少数开关。

支持 ``paths.data_root``、``web.host``、``web.port``。写的是
``%APPDATA%\\vrstudio\\default.toml``（用户级配置，安装器也会写这个文件），
只改命中那一行，不动其它段与注释。
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

from .. import config as config_mod
from .. import paths

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="查看或修改本机配置（只支持 web.host / web.port）。")

#: 允许改的键 → (段名, 键名)
_KEYS = {
    "paths.data_root": ("paths", "data_root"),
    "web.host": ("web", "host"),
    "web.port": ("web", "port"),
}


def user_config_path() -> Path:
    return paths.user_config_dir() / "default.toml"


def _format_value(key: str, raw: str) -> str:
    if key == "web.port":
        try:
            port = int(raw)
        except ValueError as exc:
            raise ValueError(f"web.port 必须是整数，收到 {raw!r}") from exc
        if not 1 <= port <= 65535:
            raise ValueError(f"web.port 必须在 1..65535，收到 {port}")
        return str(port)
    value = raw.strip()
    if not value:
        raise ValueError(f"{key} 不能为空")
    return f'"{value}"'


def set_value(key: str, raw: str) -> Path:
    """把 ``key`` 写进用户配置；返回配置文件路径。只改命中行，其余内容不动。"""
    if key not in _KEYS:
        raise ValueError(f"不支持改 {key}；可改：{', '.join(sorted(_KEYS))}")
    section, name = _KEYS[key]
    literal = _format_value(key, raw)

    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = text.splitlines()

    header = f"[{section}]"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        lines += ["", header, f"{name} = {literal}"]
    else:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].lstrip().startswith("[")), len(lines))
        pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
        hit = next((i for i in range(start + 1, end) if pattern.match(lines[i])), None)
        if hit is None:
            lines.insert(start + 1, f"{name} = {literal}")
        else:
            lines[hit] = f"{name} = {literal}"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@app.command("show")
def show() -> None:
    """打印生效的数据根、监听地址、端口与配置文件位置。"""
    conf = config_mod.activate()
    typer.echo(f"data_root = {paths.data_root()}")
    typer.echo(f"host = {conf.get('web.host')}")
    typer.echo(f"port = {conf.get('web.port')}")
    typer.echo(f"config = {user_config_path()}")


@app.command("set")
def set_command(
    key: str = typer.Argument(..., help="paths.data_root / web.host / web.port"),
    value: str = typer.Argument(..., help="新值；端口写整数，路径直接写。"),
) -> None:
    """写入本机配置；下一次启动生效。"""
    try:
        path = set_value(key, value)
    except ValueError as exc:
        typer.echo(f"config: {exc}", err=True)
        raise typer.Exit(2) from exc
    except OSError as exc:
        typer.echo(f"config: 写入失败：{exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"已写入 {path}：{key} = {value}（重启控制台后生效）")
