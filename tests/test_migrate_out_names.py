"""一次性迁移：旧 out 命名与图片流程的 still → image 改名。"""

from __future__ import annotations

import json

from vrstudio import case as case_mod
from vrstudio.media.imageio import write_png
from tests.conftest import synthetic_erp
from vrstudio.migrate import migrate_image_names, migrate_out_names


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


def _image_case(tmp_path, name: str, flow: str):
    src = tmp_path / f"{name}.png"
    write_png(src, synthetic_erp(320, 160))
    case_mod.create(name, flow, src, root=tmp_path)
    return case_mod.load(name, tmp_path)


def test_migrate_image_names_renames_sidecars_and_out(tmp_path):
    case = _image_case(tmp_path, "i1", "patchimg")
    version = case.dir(tmp_path) / "extracts" / "v1"
    version.mkdir(parents=True)
    (version / "still.json").write_text("{}", encoding="utf-8")
    derived = case.dir(tmp_path) / "derived"
    derived.mkdir()
    (derived / "still.json").write_text("{}", encoding="utf-8")
    out = case.dir(tmp_path) / "out"
    out.mkdir()
    (out / "v1_still.png").write_bytes(b"png")

    planned, problems = migrate_image_names(tmp_path, apply=False)
    assert not problems
    assert len(planned) == 3
    assert (version / "still.json").is_file()      # dry-run 不动文件

    done, problems = migrate_image_names(tmp_path, apply=True)
    assert not problems
    assert {r.target.name for r in done} == {"image.json", "v1_image.png"}
    assert (version / "image.json").is_file()
    assert (derived / "image.json").is_file()
    assert (out / "v1_image.png").is_file()
    assert not (version / "still.json").exists()

    again, problems = migrate_image_names(tmp_path, apply=True)
    assert again == [] and not problems


def test_migrate_image_names_rewrites_manifest_kind(tmp_path):
    case = _image_case(tmp_path, "f1", "fullRedrawImg")
    version = case.dir(tmp_path) / "extracts" / "v1"
    version.mkdir(parents=True)
    (version / "still_pad.json").write_text(
        json.dumps({"schema": 1, "kind": "still"}), encoding="utf-8")

    done, problems = migrate_image_names(tmp_path, apply=True)
    assert not problems
    assert any(r.source.name == "still_pad.json" for r in done)
    raw = json.loads((version / "image_pad.json").read_text(encoding="utf-8"))
    assert raw["kind"] == "image"

    again, problems = migrate_image_names(tmp_path, apply=True)
    assert again == [] and not problems


def test_migrate_image_names_skips_video_flows_and_conflicts(tmp_path):
    case = _image_case(tmp_path, "f1", "fullRedrawImg")
    version = case.dir(tmp_path) / "extracts" / "v1"
    version.mkdir(parents=True)
    (version / "still_pad.json").write_text("old", encoding="utf-8")
    (version / "image_pad.json").write_text("new", encoding="utf-8")

    patch = _case(tmp_path, "p1", "patch")
    pver = patch.dir(tmp_path) / "extracts" / "v1"
    pver.mkdir(parents=True)
    (pver / "still.json").write_text("{}", encoding="utf-8")

    done, problems = migrate_image_names(tmp_path, apply=True)
    assert done == []
    assert any("image_pad.json" in p for p in problems)
    assert (version / "still_pad.json").is_file()
    assert (version / "image_pad.json").read_text(encoding="utf-8") == "new"
    assert (pver / "still.json").is_file()          # patch 不是图片流程，不动
