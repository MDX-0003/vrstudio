"""冒烟：命令面必须与 docs/architecture.md §3 声明的一致。

这些断言是架构的守卫——增删子命令而不同步改文档（或反过来）会在这里红。
"""

from typer.testing import CliRunner

from vrstudio.cli import app

runner = CliRunner()

# ★新增（本仓库原生） + ←迁入（旧仓库命令面）
EXPECTED_COMMANDS = {
    "case", "generate", "serve",
    "extract", "merge", "restore",
    "pad", "recompose",
    "seam-center", "calib", "verify", "plan",
}


def test_help_lists_the_declared_surface():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in EXPECTED_COMMANDS:
        assert cmd in r.output, f"声明的命令缺失: {cmd}"


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert "vrstudio" in r.output


def test_stubs_exit_2_until_implemented():
    for cmd in sorted(EXPECTED_COMMANDS):
        r = runner.invoke(app, [cmd])
        assert r.exit_code == 2, f"`vrstudio {cmd}` 应以退出码 2 表示未实现，got {r.exit_code}"
        assert "尚未实现" in r.output
