"""keyframe.toml 的读写：工作台当前两帧指针。"""

from __future__ import annotations

import pytest

from vrstudio import keyframe


def test_roundtrip(tmp_path):
    state = keyframe.State(
        extract="20261022-164714", sheet="i1",
        first=keyframe.Pick("img-a", 2, "i3"),
        last=keyframe.Pick("img-b", 1, "i4"))
    keyframe.save(tmp_path, state)
    loaded = keyframe.load(tmp_path)
    assert loaded == state


def test_missing_file_is_empty_state(tmp_path):
    assert keyframe.load(tmp_path) == keyframe.State()


def test_malformed_pick_names_the_table(tmp_path):
    (tmp_path / "keyframe.toml").write_text("[first]\nround = 'img-a'\n",
                                            encoding="utf-8")
    with pytest.raises(keyframe.KeyframeError, match="first"):
        keyframe.load(tmp_path)
