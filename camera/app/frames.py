"""Image compositing: source image -> fixed-size RGB frame for the camera.

A "frame" is a numpy array of shape (H, W, 3), dtype uint8, RGB.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
from PIL import Image

from . import config

# Cache rendered frames so static slides aren't re-composited every tick.
# key: (path, mtime, fit, w, h, bg) -> np.ndarray
_cache: dict[tuple, np.ndarray] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 64


def _hex_to_rgb(hexstr: str) -> tuple[int, int, int]:
    s = hexstr.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return (0, 0, 0)


def solid_frame(color: str = "#000000", w: int = config.CAM_WIDTH, h: int = config.CAM_HEIGHT) -> np.ndarray:
    r, g, b = _hex_to_rgb(color)
    frame = np.empty((h, w, 3), dtype=np.uint8)
    frame[:, :] = (r, g, b)
    return frame


def render(
    image_path: Path,
    fit: str = "cover",
    background: str = "#000000",
    w: int = config.CAM_WIDTH,
    h: int = config.CAM_HEIGHT,
) -> np.ndarray:
    """Composite ``image_path`` onto a w*h RGB frame using cover or contain."""
    try:
        mtime = image_path.stat().st_mtime
    except OSError:
        return solid_frame(background, w, h)

    key = (str(image_path), mtime, fit, w, h, background)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    try:
        img = Image.open(image_path)
        img.load()
        img = img.convert("RGB")
    except Exception:
        return solid_frame(background, w, h)

    src_w, src_h = img.size
    target_ratio = w / h
    src_ratio = src_w / src_h

    if fit == "contain":
        # Fit entirely inside, pad with background.
        if src_ratio > target_ratio:
            new_w = w
            new_h = max(1, round(w / src_ratio))
        else:
            new_h = h
            new_w = max(1, round(h * src_ratio))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGB", (w, h), _hex_to_rgb(background))
        canvas.paste(resized, ((w - new_w) // 2, (h - new_h) // 2))
        frame = np.asarray(canvas, dtype=np.uint8)
    else:  # cover: fill frame, crop the overflow
        if src_ratio > target_ratio:
            new_h = h
            new_w = max(1, round(h * src_ratio))
        else:
            new_w = w
            new_h = max(1, round(w / src_ratio))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        cropped = resized.crop((left, top, left + w, top + h))
        frame = np.asarray(cropped, dtype=np.uint8)

    frame = np.ascontiguousarray(frame)
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = frame
    return frame


def flip_h(frame: np.ndarray) -> np.ndarray:
    """Mirror a frame horizontally (left-right)."""
    return np.ascontiguousarray(frame[:, ::-1])


def blend(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """Linear crossfade from frame ``a`` to frame ``b`` at progress ``t`` in [0,1]."""
    t = max(0.0, min(1.0, t))
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    out = a.astype(np.float32) * (1.0 - t) + b.astype(np.float32) * t
    return out.astype(np.uint8)
