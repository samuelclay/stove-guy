"""Render the IR-thermometer HUD overlay as an RGBA tile.

Drawn with PIL so it can be composited straight into the camera frame (and
therefore shows in Photo Booth / Zoom / the web preview alike). Returns
(rgba_ndarray, x, y) for the camera engine to alpha-blend onto the frame.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# panel geometry (for a 1920x1080 frame)
PANEL_W, PANEL_H = 660, 430
MARGIN = 36
PAD = 26

COL_BG = (12, 14, 18, 168)
COL_BORDER = (255, 255, 255, 28)
COL_TEXT = (231, 233, 238, 255)
COL_MUTED = (150, 156, 168, 255)
COL_COLD = (76, 201, 240)
COL_GOOD = (84, 214, 153)
COL_BURN = (255, 72, 62)
COL_ACCENT = (255, 122, 77)

_ZONE_COLOR = {"cold": COL_COLD, "good": COL_GOOD, "burn": COL_BURN}
_ZONE_WORD = {"cold": "TOO COLD", "good": "GOOD", "burn": "BURNING!"}

_FONT_PATHS = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]
_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _font_cache:
        for p in _FONT_PATHS:
            try:
                _font_cache[size] = ImageFont.truetype(p, size)
                break
            except Exception:
                continue
        else:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


def _reticle(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int, color) -> None:
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color + (220,), width=2)
    d.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], outline=color + (160,), width=1)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        d.line([cx + dx * (r - 2), cy + dy * (r - 2), cx + dx * (r + 5), cy + dy * (r + 5)], fill=color + (220,), width=2)


def _dashed_h(d: ImageDraw.ImageDraw, x0: int, x1: int, y: int, color, dash=12, gap=8, width=2) -> None:
    x = x0
    while x < x1:
        d.line([x, y, min(x + dash, x1), y], fill=color, width=width)
        x += dash + gap


def render(snap: dict, frame_w: int = 1920, frame_h: int = 1080):
    """snap is TemperatureModel.snapshot(); returns (rgba, x, y)."""
    img = Image.new("RGBA", (PANEL_W, PANEL_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, PANEL_W - 1, PANEL_H - 1], radius=22, fill=COL_BG, outline=COL_BORDER, width=1)

    temp = snap["temp"]
    unit = snap.get("unit", "F")
    zone = snap.get("zone", "good")
    zcolor = _ZONE_COLOR.get(zone, COL_GOOD)

    # --- header -------------------------------------------------------------
    _reticle(d, PAD + 12, PAD + 12, 11, COL_ACCENT)
    d.text((PAD + 32, PAD + 2), "I R   T H E R M O M E T E R", font=_font(18), fill=COL_MUTED)

    # --- big reading --------------------------------------------------------
    big = _font(104)
    reading = f"{round(temp)}°"
    d.text((PAD - 2, PAD + 34), reading, font=big, fill=zcolor + (255,))
    rb = d.textbbox((PAD - 2, PAD + 34), reading, font=big)
    d.text((rb[2] + 8, PAD + 78), unit, font=_font(40), fill=COL_MUTED)
    d.text((rb[2] + 8, PAD + 44), _ZONE_WORD.get(zone, "GOOD"), font=_font(26), fill=zcolor + (255,))

    # --- sparkline ----------------------------------------------------------
    sx, sw = PAD, PANEL_W - 2 * PAD
    sy, sh = PAD + 168, 168
    cold, burn = snap.get("cold"), snap.get("burn")
    hist = snap.get("history") or [temp]

    lo = snap.get("tmin")
    hi = snap.get("tmax")
    if lo is None or hi is None:
        vals = list(hist) + [temp] + [v for v in (cold, burn) if v is not None]
        lo = min(vals) - 15 if lo is None else lo
        hi = max(vals) + 15 if hi is None else hi
    if hi - lo < 1:
        hi = lo + 1

    def vy(v):
        return sy + sh - (v - lo) / (hi - lo) * sh

    # plot background
    d.rounded_rectangle([sx, sy, sx + sw, sy + sh], radius=12, fill=(255, 255, 255, 10))

    # cold band (bottom) + burn band (top)
    if cold is not None:
        cy = max(sy, min(sy + sh, vy(cold)))
        d.rectangle([sx, cy, sx + sw, sy + sh], fill=COL_COLD + (46,))
        _dashed_h(d, sx, sx + sw, int(cy), COL_COLD + (200,))
    if burn is not None:
        by = max(sy, min(sy + sh, vy(burn)))
        d.rectangle([sx, sy, sx + sw, by], fill=COL_BURN + (42,))
        _dashed_h(d, sx, sx + sw, int(by), COL_BURN + (220,))

    # history polyline
    n = len(hist)
    if n >= 2:
        step = sw / (n - 1)
        pts = [(sx + i * step, max(sy, min(sy + sh, vy(v)))) for i, v in enumerate(hist)]
        d.line(pts, fill=zcolor + (255,), width=3, joint="curve")
        cxp, cyp = pts[-1]
    else:
        cxp, cyp = sx + sw, max(sy, min(sy + sh, vy(temp)))
    d.ellipse([cxp - 5, cyp - 5, cxp + 5, cyp + 5], fill=zcolor + (255,), outline=(255, 255, 255, 230), width=2)

    # band labels
    if burn is not None:
        d.text((sx + 8, sy + 6), snap.get("burnLabel") or "Burning above this", font=_font(17), fill=COL_BURN + (235,))
    if cold is not None:
        d.text((sx + 8, sy + sh - 24), snap.get("coldLabel") or "Too cold", font=_font(17), fill=COL_COLD + (235,))

    # y-axis end labels
    d.text((sx + sw + 6, sy - 8), f"{round(hi)}°", font=_font(15), fill=COL_MUTED)
    d.text((sx + sw + 6, sy + sh - 16), f"{round(lo)}°", font=_font(15), fill=COL_MUTED)

    rgba = np.asarray(img, dtype=np.uint8)
    x = frame_w - PANEL_W - MARGIN
    y = MARGIN
    return rgba, x, y
