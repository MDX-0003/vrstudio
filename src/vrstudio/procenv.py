"""子进程文本编码：Windows 管道默认按 GBK 写 stdout，跨进程必须钉成 UTF-8。

vrgen 是 Python CLI。父进程用 ``capture_output`` 或把 stdout 重定向到文件时，
子进程的 stdout 编码跟随 ANSI 代码页（中文 Windows 是 cp936），父进程再按
UTF-8 解码就会得到 ``��û��`` 这类乱码。给子进程注入 ``PYTHONUTF8`` 与
``PYTHONIOENCODING`` 后，新产生的输出稳定是 UTF-8；读旧日志时再用
:func:`decode_text` 做一次 UTF-8 → 本地代码页的回退。
"""

from __future__ import annotations

import locale
import os


def utf8_env() -> dict[str, str]:
    """在当前环境上钉死 UTF-8 输出，返回可直接传给 subprocess 的 env。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def decode_text(data: bytes) -> str:
    """先按 UTF-8 严格解码，失败再试本地代码页；最后兜底替换。"""
    encodings: list[str] = ["utf-8", locale.getpreferredencoding(False), "mbcs"]
    seen: set[str] = set()
    for encoding in encodings:
        if not encoding or encoding.lower() in seen:
            continue
        seen.add(encoding.lower())
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")
