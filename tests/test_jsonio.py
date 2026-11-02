"""json_safe：非有限浮点数不能进 JSON（决策 68）。"""

from __future__ import annotations

import json
import math

from vrstudio.jsonio import json_safe


def test_swaps_non_finite_floats_for_none():
    data = {"inf": float("inf"), "neg": float("-inf"), "nan": float("nan"),
            "ok": 1.25, "count": 3, "flag": True, "text": "x"}
    assert json_safe(data) == {"inf": None, "neg": None, "nan": None,
                               "ok": 1.25, "count": 3, "flag": True, "text": "x"}


def test_walks_nested_structures_and_tuples():
    assert json_safe({"a": [1.0, float("nan"), (math.inf,)], "b": {"c": -math.inf}}) == {
        "a": [1.0, None, [None]], "b": {"c": None},
    }


def test_the_result_is_strict_json():
    payload = json.dumps(json_safe({"quality": math.inf}), allow_nan=False)
    assert json.loads(payload, parse_constant=lambda token: pytest_fail(token)) == {
        "quality": None,
    }


def pytest_fail(token: str):
    raise AssertionError(token)
