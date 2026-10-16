"""统一 case 模型（src/vrstudio/case.py）的单元测试——docs/case-model.md §2 的守卫。"""

from __future__ import annotations

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


def test_create_fullredraw_hash_optional(tmp_path, src_video):
    case = case_mod.create("pano", "fullredraw", src_video, root=tmp_path)
    assert case.sha256 is None            # pad 之后源不再被读（决策：哈希可选）


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
