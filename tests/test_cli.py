"""冒烟：命令面必须与 docs/architecture.md §3 声明的一致。

- 迁入命令（← vrpad / vrpatch）在 M2 起真实存在，断言 --help 可用；
- stub 命令（★ 新增）断言退出码 2 与提示文案。
  ⚠ 实现某命令后必须把它挪进 MIGRATED_COMMANDS——stub 测试是裸调用，
  对已实现的长驻命令（如 serve 会启动 uvicorn 永不返回）会挂死整个套件
  （2026-10-16 事故，详见 docs/testing-notes.md）。
"""

from typer.testing import CliRunner

from vrstudio import case as case_mod
from vrstudio.cli import app
from vrstudio.cli import case as case_cli

runner = CliRunner()

MIGRATED_COMMANDS = {
    "pad", "recompose", "plan", "verify", "calib", "seam-center",   # ← vrpad
    "extract", "merge", "restore",                                   # ← vrpatch
    "case", "generate",                                              # ★ M3 新增
    "serve",                                                         # ★ M4 新增
}

STUB_COMMANDS: set[str] = set()


def test_help_lists_the_declared_surface():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in MIGRATED_COMMANDS | STUB_COMMANDS:
        assert cmd in r.output, f"声明的命令缺失: {cmd}"


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert "vrstudio" in r.output


def test_migrated_commands_answer_help():
    for cmd in sorted(MIGRATED_COMMANDS):
        r = runner.invoke(app, [cmd, "--help"])
        assert r.exit_code == 0, f"`vrstudio {cmd} --help` 退出码 {r.exit_code}"


def test_case_new_gen_requires_mode_and_writes_it(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(case_cli, "save_mode", lambda name, mode: saved.append((name, mode)))
    r = runner.invoke(app, ["case", "new", "g1", "--flow", "gen",
                            "--cases-dir", str(tmp_path)])
    assert r.exit_code == 2 and "--mode" in r.output
    r = runner.invoke(app, ["case", "new", "g1", "--flow", "gen", "--mode", "allMediaRef",
                            "--cases-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert saved == [("g1", "allMediaRef")]
    case = case_mod.load("g1", tmp_path)
    assert case.assets == [] and case.source_path is None


def test_case_new_patch_rejects_mode(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    r = runner.invoke(app, ["case", "new", "p1", "--flow", "patch",
                            "--source", str(src), "--mode", "videoEdit",
                            "--cases-dir", str(tmp_path)])
    assert r.exit_code == 2 and "--mode" in r.output


def test_case_new_gen_keeps_case_when_mode_save_fails(tmp_path, monkeypatch):
    def boom(name, mode):
        raise case_mod.CaseError("vrgen down")
    monkeypatch.setattr(case_cli, "save_mode", boom)
    r = runner.invoke(app, ["case", "new", "g2", "--flow", "gen",
                            "--mode", "videoEdit", "--cases-dir", str(tmp_path)])
    assert r.exit_code == 2 and "已创建" in r.output
    assert case_mod.load("g2", tmp_path).flow == "gen"


def test_stubs_exit_2_until_implemented():
    for cmd in sorted(STUB_COMMANDS):
        r = runner.invoke(app, [cmd])
        assert r.exit_code == 2, f"`vrstudio {cmd}` 应以退出码 2 表示未实现，got {r.exit_code}"
        assert "尚未实现" in r.output
