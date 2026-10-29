"""Still-image I/O: 8-bit BGR PNG, matching the video pipeline's colour order.

OpenCV's ``imread``/``imwrite`` do not accept non-ASCII paths on Windows, and
case/source names can contain Chinese characters. Encoding through an in-memory
buffer avoids that without adding Pillow: ``imdecode``/``imencode`` take bytes,
and ``Path.read_bytes``/``write_bytes`` handle the filesystem path.

The video pipeline is 8-bit BGR throughout. ``IMREAD_COLOR`` therefore drops
alpha and down-converts 16-bit sources to 8-bit; the still flow documents that
its sources must already be upright 8-bit sRGB ERP images.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


class ImageError(ValueError):
    """An image could not be decoded or encoded."""


def read_image(path: str | Path) -> np.ndarray:
    """Read an image as ``uint8`` BGR ``(H, W, 3)``."""
    path = Path(path)
    try:
        raw = np.fromfile(path, dtype=np.uint8)
    except OSError as exc:
        raise ImageError(f"cannot read image {path}: {exc}") from exc
    if raw.size == 0:
        raise ImageError(f"image is empty: {path}")
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if image is None:
        raise ImageError(f"cannot decode image: {path}")
    return image


def write_png(path: str | Path, image: np.ndarray, compression: int = 0) -> Path:
    """Write an 8-bit BGR image as a lossless PNG; returns the path."""
    path = Path(path)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ImageError(f"expected an (H, W, 3) BGR image, got {image.shape}")
    if image.dtype != np.uint8:
        raise ImageError(f"expected uint8, got {image.dtype}")
    return _encode(path, image, compression)


def write_gray_png(path: str | Path, image: np.ndarray, compression: int = 0) -> Path:
    """Write an 8-bit single-channel image (mask) as a lossless PNG."""
    path = Path(path)
    if image.ndim != 2:
        raise ImageError(f"expected a 2-D mask, got {image.shape}")
    if image.dtype != np.uint8:
        raise ImageError(f"expected uint8, got {image.dtype}")
    return _encode(path, image, compression)


def _encode(path: Path, image: np.ndarray, compression: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", image,
                           [cv2.IMWRITE_PNG_COMPRESSION, int(compression)])
    if not ok:
        raise ImageError(f"cannot encode PNG: {path}")
    path.write_bytes(buf.tobytes())
    return path
