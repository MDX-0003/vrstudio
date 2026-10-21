"""旧 out/<版本>.mp4 -> <版本>_space.mp4 的一次性迁移。"""

from __future__ import annotations

from vrstudio import case as case_mod
from vrstudio.migrate import migrate_out_names


def _case(tmp_path, name: str, flow: str):
    src = tmp_path / f"{name}.mp4"
    src.write_bytes(b"fake source")
    case_mod.create(name, flow, src, root=tmp_path)
    return case_mod.load(name, tmp_path)


def test_migrate_patch_out_dry_run_then_apply(tmp_path):
    case = _case(tmp_path, "p1", "patch")
    (case.dir(tmp_path) / "extracts" / "v1").mkdir(parents=True)
    out = case.dir(tmp_path) / "out"
    out.mkdir()
    (out / "v1.mp4").write_bytes(b"old")

    planned, problems = migrate_out_names(tmp_path, apply=False)
    assert not problems
    assert [(r.source.name, r.target.name) for r in planned] == [
        ("v1.mp4", "v1_space.mp4")]
    assert (out / "v1.mp4").is_file()             # dry-run 不动文件

    done, problems = migrate_out_names(tmp_path, apply=True)
    assert not problems
    assert [r.target.name for r in done] == ["v1_space.mp4"]
    assert not (out / "v1.mp4").exists()
    assert (out / "v1_space.mp4").is_file()


def test_migrate_skips_fullredraw_non_version_and_conflict(tmp_path):
    _case(tmp_path, "f1", "fullredraw")
    fout = tmp_path / "f1" / "out"
    fout.mkdir()
    (fout / "round-1.mp4").write_bytes(b"full")

    p2 = _case(tmp_path, "p2", "patch")
    (p2.dir(tmp_path) / "extracts" / "v1").mkdir(parents=True)
    p2out = p2.dir(tmp_path) / "out"
    p2out.mkdir()
    (p2out / "misc.mp4").write_bytes(b"not a version")

    p3 = _case(tmp_path, "p3", "patch")
    (p3.dir(tmp_path) / "extracts" / "v1").mkdir(parents=True)
    p3out = p3.dir(tmp_path) / "out"
    p3out.mkdir()
    (p3out / "v1.mp4").write_bytes(b"old")
    (p3out / "v1_space.mp4").write_bytes(b"already migrated")

    done, problems = migrate_out_names(tmp_path, apply=True)
    assert done == []
    assert any("misc.mp4" in p for p in problems)
    assert any("v1_space.mp4" in p for p in problems)
    assert (fout / "round-1.mp4").is_file()      # fullredraw 不动
    assert (p3out / "v1.mp4").is_file()          # 冲突不覆盖
    assert (p3out / "v1_space.mp4").read_bytes() == b"already migrated"
