"""keyframe.toml 的读写：工作台当前两帧指针。"""

from __future__ import annotations

import pytest

from vrstudio import keyframe


def test_roundtrip(tmp_path):
    state = keyframe.State(
        extract="20261022-164714",
        refs=[keyframe.Ref("i1", "subject_ref"),
              keyframe.Ref("i2", "detail_ref", "头冠纹样与金色饰物")],
        first=keyframe.Pick("img-a", 2, "i3"),
        last=keyframe.Pick("img-b", 1, "i4"))
    keyframe.save(tmp_path, state)
    loaded = keyframe.load(tmp_path)
    assert loaded == state


def test_legacy_sheet_becomes_primary_ref(tmp_path):
    (tmp_path / "keyframe.toml").write_text(
        'extract = "v1"\nsheet = "i1"\n', encoding="utf-8")
    loaded = keyframe.load(tmp_path)
    assert loaded.refs == [keyframe.Ref("i1", "subject_ref", "")]
    assert loaded.refs_explicit is False


def test_explicit_empty_refs_roundtrip(tmp_path):
    state = keyframe.State(
        extract="v1", refs=[], refs_explicit=True,
        first=keyframe.Pick("img-a", 1, "i2"))
    keyframe.save(tmp_path, state)
    text = (tmp_path / "keyframe.toml").read_text(encoding="utf-8")
    assert "refs = []" in text
    loaded = keyframe.load(tmp_path)
    assert loaded.refs == []
    assert loaded.refs_explicit is True
    assert loaded.first == keyframe.Pick("img-a", 1, "i2")


def test_explicit_empty_refs_wins_over_legacy_sheet(tmp_path):
    (tmp_path / "keyframe.toml").write_text(
        'refs = []\nsheet = "i1"\n', encoding="utf-8")
    loaded = keyframe.load(tmp_path)
    assert loaded.refs == []
    assert loaded.refs_explicit is True


def test_lighting_ref_roundtrip(tmp_path):
    state = keyframe.State(
        refs=[keyframe.Ref("i4", "lighting_ref", "暖色侧光")])
    keyframe.save(tmp_path, state)
    loaded = keyframe.load(tmp_path)
    assert loaded.refs == [keyframe.Ref("i4", "lighting_ref", "暖色侧光")]


def test_remove_last_ref_writes_explicit_empty(tmp_path):
    state = keyframe.State(refs=[keyframe.Ref("i1", "subject_ref")])
    keyframe.save(tmp_path, state)
    assert keyframe.remove_asset(tmp_path, "i1") is True
    loaded = keyframe.load(tmp_path)
    assert loaded.refs == []
    assert loaded.refs_explicit is True
    assert "refs = []" in (tmp_path / "keyframe.toml").read_text(encoding="utf-8")


def test_validate_refs_accepts_lighting_ref():
    refs = keyframe.validate_refs(
        [keyframe.Ref("i1", "subject_ref"), keyframe.Ref("i4", "lighting_ref", "侧光")],
        {"i1", "i4"})
    assert [ref.role for ref in refs] == ["subject_ref", "lighting_ref"]


def test_refs_reject_two_primary_images(tmp_path):
    (tmp_path / "keyframe.toml").write_text(
        '[[refs]]\nasset = "i1"\nrole = "subject_ref"\n\n'
        '[[refs]]\nasset = "i2"\nrole = "subject_ref"\n',
        encoding="utf-8")
    with pytest.raises(keyframe.KeyframeError, match="subject_ref"):
        keyframe.load(tmp_path)


def test_remove_asset_cleans_refs_and_picks(tmp_path):
    state = keyframe.State(
        refs=[keyframe.Ref("i1", "subject_ref"),
              keyframe.Ref("i2", "detail_ref", "细节")],
        first=keyframe.Pick("img-a", 1, "i2"),
        last=keyframe.Pick("img-b", 1, "i3"))
    keyframe.save(tmp_path, state)
    assert keyframe.remove_asset(tmp_path, "i2") is True
    loaded = keyframe.load(tmp_path)
    assert loaded.refs == [keyframe.Ref("i1", "subject_ref")]
    assert loaded.first is None
    assert loaded.last == keyframe.Pick("img-b", 1, "i3")


def test_missing_file_is_empty_state(tmp_path):
    assert keyframe.load(tmp_path) == keyframe.State()


def test_malformed_pick_names_the_table(tmp_path):
    (tmp_path / "keyframe.toml").write_text("[first]\nround = 'img-a'\n",
                                            encoding="utf-8")
    with pytest.raises(keyframe.KeyframeError, match="first"):
        keyframe.load(tmp_path)
