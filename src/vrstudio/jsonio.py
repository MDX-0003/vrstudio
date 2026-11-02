"""JSON 落盘与下发的数值清理。

``NaN`` 与 ``Infinity`` 不是合法 JSON：Python 的 ``json.dumps`` 默认把它们写成
裸 token，``json.loads`` 又能读回来，所以本地看着没问题；但 Starlette 的
``JSONResponse`` 用 ``allow_nan=False``，把这种值原样塞进响应会直接抛
``ValueError: Out of range float values are not JSON compliant``。

接缝指标在“完美匹配”这类边界上会算出 ``inf``（逐位相同时最优代价为 0，
备选比值的分母就是 0），非有限值统一转成 ``None``，由前端按“无该指标”处理。
"""

from __future__ import annotations

import math


def json_safe(value):
    """递归把非有限浮点数换成 None，其余结构原样返回。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value
