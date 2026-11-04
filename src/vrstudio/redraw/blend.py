"""Blend weights and the endpoint lock.

The endpoint lock is **not an aesthetic knob**. The closure invariant is that the
output's last column carries canvas column ``2P - 1`` (because the output's first
column carries canvas column ``2P``, and those two canvas columns are adjacent).
The blend's right end is ``roll(G, d*)``, so without the lock a non-zero ``d*``
would walk the wrap off that adjacent pair and re-open the seam the tool exists
to close. The lock pins the last ``E`` columns to *unshifted* ``G``.

The shifted version still serves the interior: it is what aligns the two bands'
content. Only the endpoints must stay raw.
"""

from __future__ import annotations

from typing import Literal

import numpy as np

BlendWeight = Literal["smoothstep", "linear", "cosine"]
WEIGHTS: tuple[str, ...] = ("smoothstep", "linear", "cosine")


def weights(n: int, kind: BlendWeight = "smoothstep") -> np.ndarray:
    """Monotone ``0 -> 1`` ramp of length ``n``, sampled at pixel centres.

    Sampling at centres (``x + 0.5``) rather than edges keeps the ramp symmetric
    about the band, so the two endpoints are not biased toward one source.
    """
    if n <= 0:
        return np.zeros(0, np.float64)
    if kind not in WEIGHTS:
        raise ValueError(f"unknown blend weight {kind!r}; want one of {WEIGHTS}")
    x = (np.arange(n, dtype=np.float64) + 0.5) / n
    if kind == "linear":
        return x
    if kind == "cosine":
        return 0.5 * (1.0 - np.cos(np.pi * x))
    return 3 * x**2 - 2 * x**3          # smoothstep: zero slope at both ends


def _broadcast(w: np.ndarray, ndim: int) -> np.ndarray:
    return w[None, :, None] if ndim == 3 else w[None, :]


def _shift_into(a: np.ndarray, shift: int, out: np.ndarray) -> np.ndarray:
    """Circular shift along the column axis, written into ``out``.

    Identical to ``np.roll(a, shift, axis=1)`` but without allocating the result.
    The video pass calls this once per frame with a reused buffer.
    """
    n = a.shape[1]
    s = int(shift) % n if n else 0
    if s == 0:
        out[...] = a
    else:
        out[:, s:] = a[:, : n - s]
        out[:, :s] = a[:, n - s :]
    return out


def blend_into(
    G: np.ndarray,
    B: np.ndarray,
    d_star: int,
    kind: BlendWeight,
    endpoint_lock_px: int,
    out: np.ndarray,
    temp: np.ndarray,
    shifted: np.ndarray | None = None,
) -> np.ndarray:
    """Blend ``B`` into ``roll(G, d*)`` into caller-owned buffers.

    ``out`` receives the float64 band; ``temp`` is scratch of the same shape and
    ``shifted`` (optional) receives the shifted ``G``. Passing the three buffers
    in lets a streaming pass reuse them instead of allocating hundreds of
    megabytes per frame, which on 8K material costs more in page faults than the
    arithmetic itself. The expression and its order match :func:`blend_block`, so
    the values are bit-identical.
    """
    if G.shape != B.shape:
        raise ValueError(f"G {G.shape} and B {B.shape} must match")
    L = B.shape[1]
    w = _broadcast(weights(L, kind), B.ndim)
    if d_star:
        src = shifted if shifted is not None else np.roll(G, d_star, axis=1)
        if shifted is not None:
            _shift_into(G, d_star, shifted)
    else:
        src = G
    np.multiply(B, 1.0 - w, out=out)
    np.multiply(src, w, out=temp)
    np.add(out, temp, out=out)

    E = int(endpoint_lock_px)
    if E > 0 and L:
        E = min(E, L // 2)              # never let the two locks collide
        if E > 0:
            out[:, :E] = B[:, :E]
            out[:, L - E :] = G[:, L - E :]
    return out


def blend_block(
    G: np.ndarray,
    B: np.ndarray,
    d_star: int = 0,
    kind: BlendWeight = "smoothstep",
    endpoint_lock_px: int = 0,
) -> np.ndarray:
    """Blend ``B`` into ``roll(G, d*)`` across the band, in float64.

    Left end is 100% ``B``, right end 100% ``roll(G, d*)``; the endpoint lock
    then overwrites the outer ``E`` columns with raw ``B`` / raw ``G``.
    """
    if G.shape != B.shape:
        raise ValueError(f"G {G.shape} and B {B.shape} must match")
    out = np.empty(B.shape, np.float64)
    temp = np.empty(B.shape, np.float64)
    return blend_into(G, B, d_star, kind, endpoint_lock_px, out, temp)


def endpoint_lock_px(L: int, pct: float) -> int:
    """Column count from a percentage of the band width."""
    return int(round(L * float(pct) / 100.0))
