"""统一 case 模型（src/vrstudio/case.py）的单元测试——docs/case-model.md §2 的守卫。"""

from __future__ import annotations

import json

import pytest

from vrstudio import case as case_mod
from vrstudio.case import CaseError


@pytest.fixture
def src_video(tmp_path) -> object:
    p = tmp_path / "ue_render.mp4"
    p.write_bytes(b"fake erp bytes")
    return p


def test_create_patch_pins_sha256(tmp_path, src_video):
    case = case_mod.create("ship", "patch", src_video, root=tmp_path)
    assert case.flow == "patch"
    assert case.sha256 == case_mod.file_sha256(src_video)
    text = case_mod.case_toml("ship", tmp_path).read_text(encoding="utf-8")
    assert 'flow = "patch"' in text and "[source]" in text
    back = case_mod.load("ship", tmp_path)
    assert back.source_path == str(src_video) and back.sha256 == case.sha256
    assert back.viewport is None          # 新 case 草稿未定，合法


def test_patch_default_draft_is_the_web_and_cli_seed(tmp_path, src_video):
    draft = case_mod.patch_default_draft(8000, 590)
    assert draft["viewport"]["width"] == 1920 and draft["viewport"]["height"] == 1080
    assert draft["viewport"]["fov_h_deg"] == 59.0
    assert draft["inner"] == {"x": 480, "y": 270, "width": 960, "height": 540}
    assert draft["frames"] == {"start": 0, "end": 589}
    # 窄源不放大：视口宽度跟随源宽，高度按 16:9 折算；帧数未知时先给一帧。
    narrow = case_mod.patch_default_draft(1280, 0)
    assert narrow["viewport"]["width"] == 1280 and narrow["viewport"]["height"] == 720
    assert narrow["frames"] == {"start": 0, "end": 0}
    # 播种后 case.toml 三段齐了，web pick 打开即有默认值。
    case_mod.create("p1", "patch", src_video, root=tmp_path)
    case_mod.set_draft("p1", root=tmp_path, **draft)
    back = case_mod.load("p1", tmp_path)
    assert back.viewport == draft["viewport"] and back.frames == draft["frames"]


def test_create_fullredraw_hash_optional(tmp_path, src_video):
    case = case_mod.create("pano", "fullredraw", src_video, root=tmp_path)
    assert case.sha256 is None            # pad 之后源不再被读（决策：哈希可选）


def test_create_gen_copies_source_as_v1(tmp_path, src_video):
    case = case_mod.create("simple", "gen", src_video, root=tmp_path)
    assert case.flow == "gen"
    assert case.source_path is None                      # gen 不再有 [source]
    assert len(case.assets) == 1
    asset = case.assets[0]
    assert (asset.id, asset.kind, asset.role) == ("v1", "video", "edit_master")
    assert asset.path.startswith("assets/v1_")
    assert asset.sha256 == case_mod.file_sha256(src_video)
    assert (tmp_path / "simple" / asset.path).is_file()
    text = case_mod.case_toml("simple", tmp_path).read_text(encoding="utf-8")
    assert "[[assets]]" in text and "[source]" not in text


def test_create_gen_without_source_is_empty(tmp_path):
    case = case_mod.create("empty", "gen", root=tmp_path)
    assert case.flow == "gen" and case.assets == [] and case.source_path is None
    assert case_mod.load("empty", tmp_path).assets == []


def test_gen_assets_add_role_reorder_remove(tmp_path, src_video):
    case_mod.create("g", "gen", root=tmp_path)
    image = tmp_path / "ref.png"
    image.write_bytes(b"fake png bytes")
    case = case_mod.add_assets("g", [(src_video, "motion_ref"),
                                     (image, "subject_ref")], root=tmp_path)
    assert [a.id for a in case.assets] == ["v1", "i1"]
    assert [a.role for a in case.assets] == ["motion_ref", "subject_ref"]
    case = case_mod.set_asset_role("g", "v1", "edit_master", root=tmp_path)
    assert case.assets[0].role == "edit_master"
    case = case_mod.reorder_assets("g", ["i1", "v1"], root=tmp_path)
    assert [a.id for a in case.assets] == ["i1", "v1"]
    image_path = tmp_path / "g" / case.assets[0].path
    assert image_path.is_file()
    case = case_mod.remove_asset("g", "i1", root=tmp_path)
    assert [a.id for a in case.assets] == ["v1"]
    assert not image_path.exists()


def test_gen_asset_hash_guard(tmp_path, src_video):
    case_mod.create("g", "gen", src_video, root=tmp_path)
    case = case_mod.load("g", tmp_path)
    assert case_mod.check_assets(case, tmp_path)
    (tmp_path / "g" / case.assets[0].path).write_bytes(b"changed")
    with pytest.raises(CaseError, match="sha256"):
        case_mod.check_assets(case, tmp_path)


def test_legacy_gen_source_migrates_on_load(tmp_path, src_video):
    d = tmp_path / "old"
    d.mkdir()
    (d / "case.toml").write_text(
        'schema = 1\nflow = "gen"\n\n[source]\n'
        f'path = {json.dumps(str(src_video), ensure_ascii=False)}\n'
        f'sha256 = "{case_mod.file_sha256(src_video)}"\n',
        encoding="utf-8")
    case = case_mod.load("old", tmp_path)
    assert case.source_path is None and len(case.assets) == 1
    assert case.assets[0].role == "edit_master"
    text = (d / "case.toml").read_text(encoding="utf-8")
    assert "[source]" not in text and "[[assets]]" in text
    assert (d / case.assets[0].path).is_file()


def test_legacy_reference_images_migrate_to_assets(tmp_path, src_video):
    image = tmp_path / "ref.png"
    image.write_bytes(b"fake png bytes")
    d = tmp_path / "old"
    d.mkdir()
    (d / "case.toml").write_text(
        'schema = 1\nflow = "gen"\n\n[source]\n'
        f'path = {json.dumps(str(src_video), ensure_ascii=False)}\n\n'
        f'[gen]\nreference_images = [{json.dumps(str(image), ensure_ascii=False)}]\n',
        encoding="utf-8")
    case = case_mod.load("old", tmp_path)
    assert [a.kind for a in case.assets] == ["video", "image"]
    assert case.assets[1].role == "subject_ref"


def test_gen_rejects_patch_sections_and_other_flows_reject_gen_section(tmp_path, src_video):
    case_mod.create("simple", "gen", src_video, root=tmp_path)
    p = case_mod.case_toml("simple", tmp_path)
    p.write_text(p.read_text(encoding="utf-8") + '\n[viewport]\nyaw_deg = 0.0\n',
                 encoding="utf-8")
    with pytest.raises(CaseError, match="patch 专属段"):
        case_mod.load("simple", tmp_path)

    case_mod.create("pano", "fullredraw", src_video, root=tmp_path)
    p2 = case_mod.case_toml("pano", tmp_path)
    p2.write_text(p2.read_text(encoding="utf-8") + '\n[gen]\nreference_images = ["x.png"]\n',
                  encoding="utf-8")
    with pytest.raises(CaseError, match=r"\[gen\] 只属于"):
        case_mod.load("pano", tmp_path)


def test_fullredraw_rejects_patch_sections(tmp_path, src_video):
    case_mod.create("pano", "fullredraw", src_video, root=tmp_path)
    p = case_mod.case_toml("pano", tmp_path)
    p.write_text(p.read_text(encoding="utf-8") + '\n[viewport]\nyaw_deg = 0.0\n',
                 encoding="utf-8")
    with pytest.raises(CaseError, match="patch 专属段"):
        case_mod.load("pano", tmp_path)


def test_duplicate_and_bad_args(tmp_path, src_video):
    case_mod.create("a", "patch", src_video, root=tmp_path)
    with pytest.raises(CaseError, match="已存在"):
        case_mod.create("a", "patch", src_video, root=tmp_path)
    with pytest.raises(CaseError, match="flow"):
        case_mod.create("b", "repaint", src_video, root=tmp_path)
    with pytest.raises(CaseError):
        case_mod.create("../evil", "patch", src_video, root=tmp_path)


def test_adopt_and_extract_pointers_round_trip(tmp_path, src_video):
    case_mod.create("ship", "patch", src_video, root=tmp_path)
    case_mod.set_extract_current("ship", "20260923T073001", root=tmp_path)
    case_mod.set_adopt("ship", "manual-ab12cd34", root=tmp_path)
    back = case_mod.load("ship", tmp_path)
    assert back.extract_current == "20260923T073001"
    assert back.adopt_round == "manual-ab12cd34"
    # extract 指针只属于 patch 流
    case_mod.create("pano", "fullredraw", src_video, root=tmp_path)
    with pytest.raises(CaseError):
        case_mod.set_extract_current("pano", "x", root=tmp_path)


def test_check_source_guards_patch_merge(tmp_path, src_video, monkeypatch):
    case_mod.create("ship", "patch", src_video, root=tmp_path)
    case = case_mod.load("ship", tmp_path)
    assert case_mod.check_source(case, tmp_path) == src_video
    # 换内容 → 拒绝（防"几何配错视频"）
    src_video.write_bytes(b"different bytes")
    with pytest.raises(CaseError, match="sha256"):
        case_mod.check_source(case, tmp_path)


def test_name_rules(tmp_path, src_video):
    """允许 A-Za-z0-9._-（与 vrgen 的 job 名同规则）；中文/路径分隔符拒绝。"""
    case_mod.create("ship.01-beta", "patch", src_video, root=tmp_path)
    assert case_mod.load("ship.01-beta", tmp_path).name == "ship.01-beta"
    for bad in ("船", "a/b", "..", "-x"):
        with pytest.raises(CaseError):
            case_mod.create(bad, "patch", src_video, root=tmp_path)


def test_assets_allowed_for_all_flows(tmp_path, src_video):
    case_mod.create("ship", "patch", src_video, root=tmp_path)
    image = tmp_path / "ref.png"
    image.write_bytes(b"fake png bytes")
    case = case_mod.add_assets("ship", [(image, "subject_ref")], root=tmp_path)
    assert [a.kind for a in case.assets] == ["image"]
    assert case.source_path is not None
    text = case_mod.case_toml("ship", tmp_path).read_text(encoding="utf-8")
    assert "[[assets]]" in text and "[source]" in text
    back = case_mod.load("ship", tmp_path)
    assert back.assets[0].id == "i1" and back.source_path == case.source_path

    case_mod.create("pano", "fullredraw", src_video, root=tmp_path)
    case = case_mod.add_assets("pano", [(image, "scene_ref")], root=tmp_path)
    assert [a.role for a in case.assets] == ["scene_ref"]
    assert case_mod.load("pano", tmp_path).assets

    # 光照参考只对图片开放：只能给照片读光，不承载场景内容。
    assert "lighting_ref" in case_mod.ROLES
    case = case_mod.set_asset_role("pano", "i1", "lighting_ref", root=tmp_path)
    assert [a.role for a in case.assets] == ["lighting_ref"]
    assert case_mod.load("pano", tmp_path).assets[0].role == "lighting_ref"


def test_asset_crud_all_flows(tmp_path, src_video):
    case_mod.create("ship", "patch", src_video, root=tmp_path)
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    case = case_mod.add_assets("ship", [(first, "subject_ref")], root=tmp_path)
    case = case_mod.add_assets("ship", [(last, "scene_ref")], root=tmp_path)
    assert [a.id for a in case.assets] == ["i1", "i2"]
    case = case_mod.set_asset_role("ship", "i1", "first_frame", root=tmp_path)
    assert case.assets[0].role == "first_frame"
    case = case_mod.reorder_assets("ship", ["i2", "i1"], root=tmp_path)
    assert [a.id for a in case.assets] == ["i2", "i1"]
    assert len(case_mod.check_assets(case, tmp_path)) == 2
    case = case_mod.remove_asset("ship", "i1", root=tmp_path)
    assert [a.id for a in case.assets] == ["i2"]


def test_reserved_roles_cannot_be_given_to_file_assets(tmp_path):
    case_mod.create("g", "gen", root=tmp_path)
    image = tmp_path / "x.png"
    image.write_bytes(b"x")
    with pytest.raises(CaseError, match="流程产物"):
        case_mod.add_assets("g", [(image, "mask")], root=tmp_path)
    case_mod.add_assets("g", [(image, "subject_ref")], root=tmp_path)
    with pytest.raises(CaseError, match="流程产物"):
        case_mod.set_asset_role("g", "i1", "first_frame_raw", root=tmp_path)


def test_flow_asset_id_is_a_valid_selection(tmp_path, src_video):
    case_mod.create("p", "patch", src_video, root=tmp_path)
    case = case_mod.set_submit_selected("p", ["clip"], root=tmp_path)
    assert case.submit_selected == ["clip"]
    with pytest.raises(CaseError, match="不存在"):
        case_mod.set_submit_selected("p", ["pad"], root=tmp_path)


def test_submit_selected_roundtrip(tmp_path, src_video):
    case_mod.create("g", "gen", root=tmp_path)
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    case_mod.add_assets("g", [(first, "first_frame"), (last, "last_frame")],
                        root=tmp_path)
    case = case_mod.set_submit_selected("g", ["i1", "i2"], root=tmp_path)
    assert case.submit_selected == ["i1", "i2"]
    text = case_mod.case_toml("g", tmp_path).read_text(encoding="utf-8")
    assert "[submit]" in text and 'selected = ["i1", "i2"]' in text
    assert case_mod.load("g", tmp_path).submit_selected == ["i1", "i2"]

    with pytest.raises(CaseError, match="不存在"):
        case_mod.set_submit_selected("g", ["i9"], root=tmp_path)
    case = case_mod.remove_asset("g", "i1", root=tmp_path)
    assert case.submit_selected == ["i2"]


def test_submit_roles_roundtrip_and_fallback(tmp_path):
    case_mod.create("g", "gen", root=tmp_path)
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    case_mod.add_assets("g", [(first, "first_frame"), (last, "last_frame")],
                        root=tmp_path)
    case = case_mod.set_submit_selected(
        "g", ["i1", "i2"], root=tmp_path,
        roles={"i1": "subject_ref"})
    assert case.submit_roles == {"i1": "subject_ref"}
    assert case.submit_role("i1") == "subject_ref"
    assert case.submit_role("i2") == "last_frame"
    text = case_mod.case_toml("g", tmp_path).read_text(encoding="utf-8")
    assert "[submit.roles]" in text and '"i1" = "subject_ref"' in text
    loaded = case_mod.load("g", tmp_path)
    assert loaded.submit_roles == {"i1": "subject_ref"}
    # 只给 selected 时保留仍在选中的旧覆盖；新 id 回退到素材角色。
    case = case_mod.set_submit_selected("g", ["i1", "i2"], root=tmp_path)
    assert case.submit_roles == {"i1": "subject_ref"}
    assert case.submit_role("i2") == "last_frame"


def test_submit_roles_reject_unknown_entries(tmp_path):
    case_mod.create("g", "gen", root=tmp_path)
    image = tmp_path / "x.png"
    image.write_bytes(b"x")
    case_mod.add_assets("g", [(image, "subject_ref")], root=tmp_path)
    with pytest.raises(CaseError, match="不在 selected"):
        case_mod.set_submit_selected("g", ["i1"], root=tmp_path,
                                     roles={"i9": "subject_ref"})
    with pytest.raises(CaseError, match="不合约"):
        case_mod.set_submit_selected("g", ["i1"], root=tmp_path,
                                     roles={"i1": "nope"})
    with pytest.raises(CaseError, match="保留角色"):
        case_mod.set_submit_selected("g", ["i1"], root=tmp_path,
                                     roles={"i1": "mask"})


def test_remove_asset_removes_submit_role(tmp_path):
    case_mod.create("g", "gen", root=tmp_path)
    image = tmp_path / "x.png"
    image.write_bytes(b"x")
    case_mod.add_assets("g", [(image, "subject_ref")], root=tmp_path)
    case_mod.set_submit_selected("g", ["i1"], root=tmp_path,
                                 roles={"i1": "subject_ref"})
    case = case_mod.remove_asset("g", "i1", root=tmp_path)
    assert case.submit_selected == []
    assert case.submit_roles is None


def test_master_image_fallback(tmp_path):
    case_mod.create("g", "gen", root=tmp_path)
    first = tmp_path / "first.png"
    first.write_bytes(b"first")
    case = case_mod.add_assets("g", [(first, "first_frame")], root=tmp_path)
    assert case.master().id == "i1"

    scene = tmp_path / "scene.png"
    scene.write_bytes(b"scene")
    case = case_mod.add_assets("g", [(scene, "scene_ref")], root=tmp_path)
    assert case.master().id == "i2"       # space/scene 先于 first_frame

    case_mod.create("g2", "gen", root=tmp_path)
    last = tmp_path / "last.png"
    last.write_bytes(b"last")
    case = case_mod.add_assets("g2", [(last, "last_frame")], root=tmp_path)
    assert case.master().id == "i1"       # 兜底第一条图片


def test_kind_limited_roles(tmp_path, src_video):
    case_mod.create("ship", "patch", src_video, root=tmp_path)
    # 视频不能承担光照参考：添加、改角色、写提交角色三条路径都要拦。
    with pytest.raises(CaseError, match="lighting_ref"):
        case_mod.add_assets("ship", [(src_video, "lighting_ref")], root=tmp_path)
    case = case_mod.add_assets("ship", [(src_video, "motion_ref")], root=tmp_path)
    assert case.assets[0].kind == "video"
    with pytest.raises(CaseError, match="lighting_ref"):
        case_mod.set_asset_role("ship", "v1", "lighting_ref", root=tmp_path)
    with pytest.raises(CaseError, match="lighting_ref"):
        case_mod.set_submit_selected("ship", ["v1"], root=tmp_path,
                                     roles={"v1": "lighting_ref"})
    # 手改 case.toml 的加载路径同样拦。
    case_mod.set_submit_selected("ship", ["v1"], root=tmp_path,
                                 roles={"v1": "motion_ref"})
    path = case_mod.case_toml("ship", tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"v1" = "motion_ref"', '"v1" = "lighting_ref"'),
        encoding="utf-8")
    with pytest.raises(CaseError, match="lighting_ref"):
        case_mod.load("ship", tmp_path)
