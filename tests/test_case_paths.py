"""case 路径锚点：``[source] path`` 按数据根存相对路径（决策 101、102）。"""

from __future__ import annotations

import tomllib

from vrstudio import case as case_mod
from vrstudio import paths as paths_mod
from vrstudio.migrate import migrate_source_paths


def _repo(tmp_path, monkeypatch):
    """把一个临时目录当成数据根：``sources/`` 与 case 都挂在这下面。"""
    repo = tmp_path / "repo"
    (repo / "sources").mkdir(parents=True)
    monkeypatch.setattr(paths_mod, "data_root", lambda: repo)
    return repo


def _source(repo, name: str = "clip.mp4"):
    path = repo / "sources" / name
    path.write_bytes(b"fake source")
    return path


def test_stored_path_is_repo_relative_inside_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    src = _source(repo)

    assert case_mod.stored_path(src) == "sources/clip.mp4"


def test_stored_path_keeps_absolute_outside_repo(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere" / "clip.mp4"
    outside.parent.mkdir()
    outside.write_bytes(b"fake source")

    assert case_mod.stored_path(outside) == str(outside.resolve())


def test_create_writes_repo_relative_source(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    src = _source(repo)

    case_mod.create("c1", "patch", src, root=repo / "cases", compute_hash=False)

    raw = tomllib.loads((repo / "cases" / "c1" / "case.toml").read_text(encoding="utf-8"))
    assert raw["source"]["path"] == "sources/clip.mp4"


def test_relative_source_resolves_against_repo_root(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    src = _source(repo)

    assert case_mod._stored_path("sources/clip.mp4") == src
    assert case_mod._stored_path(str(src)) == src


def test_check_source_accepts_relative_path(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    src = _source(repo)
    case = case_mod.create("c1", "patch", src, root=repo / "cases", compute_hash=False)

    assert case_mod.check_source(case) == src


def test_missing_source_hints_at_migration_script(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    src = _source(repo)
    case = case_mod.create("c1", "patch", src, root=repo / "cases", compute_hash=False)
    src.unlink()

    try:
        case_mod.check_source(case)
    except case_mod.CaseError as exc:
        assert "migrate_source_paths" in str(exc)
    else:                                            # pragma: no cover - 守卫失效
        raise AssertionError("源文件缺失时 check_source 必须报错")


def _legacy_toml(repo, name: str, old_path: str) -> None:
    cases = repo / "cases" / name
    cases.mkdir(parents=True)
    (cases / "case.toml").write_text(
        "schema = 1\n"
        'flow = "patch"\n'
        "\n"
        "[source]\n"
        f'path = "{old_path}"\n'
        "\n"
        "[pick]\n"
        'active = "r1"\n',
        encoding="utf-8")


def test_migrate_source_paths_dry_run_then_apply(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    src = _source(repo)
    _legacy_toml(repo, "c1", str(src).replace("\\", "\\\\"))
    toml = repo / "cases" / "c1" / "case.toml"

    planned, problems = migrate_source_paths(repo / "cases", apply=False)
    assert not problems
    assert [(p.case, p.new) for p in planned] == [("c1", "sources/clip.mp4")]
    assert str(src).replace("\\", "\\\\") in toml.read_text(encoding="utf-8")

    done, problems = migrate_source_paths(repo / "cases", apply=True)
    assert not problems
    raw = tomllib.loads(toml.read_text(encoding="utf-8"))
    assert raw["source"]["path"] == "sources/clip.mp4"
    assert raw["pick"]["active"] == "r1"             # 其余字段不动

    again, problems = migrate_source_paths(repo / "cases", apply=True)
    assert again == [] and not problems


def test_migrate_source_paths_reports_outside_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"fake source")
    _legacy_toml(repo, "c1", str(outside).replace("\\", "\\\\"))

    done, problems = migrate_source_paths(repo / "cases", apply=True)
    assert done == []
    assert any("仓库外" in p for p in problems)
