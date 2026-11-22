"""Colour and tone numbers for a rectangle of the picture (for example a cheek).

Pure numbers, no verdicts: CIELAB lightness, a*, b*, hue angle and chroma of the median pixel,
so that an edit to skin tone (or any other small area) can be checked on real renders.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.analysis import luma
from darkroom_ai.imaging.io import Pixels

MIN_PIXELS = 100  # a patch smaller than this is too noisy to measure


class RegionReading(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    pixels: int
    lightness: float  # CIELAB L*, 0..100, of the median pixel
    a: float  # CIELAB a* (green - red)
    b: float  # CIELAB b* (blue - yellow)
    hue_deg: float  # atan2(b*, a*) in degrees, 0..360 (red ~ 40, yellow ~ 90 on skin-like colours)
    chroma: float  # sqrt(a*^2 + b*^2)
    rgb: tuple[int, int, int]  # median colour, 0..255
    luma_p10: float
    luma_p90: float
    clipped_fraction: float  # share of pixels at pure black or white (0..1)


class RegionSpread(BaseModel):
    model_config = ConfigDict(frozen=True)

    lightness_range: float
    chroma_range: float
    hue_range_deg: float  # largest circular difference between any two regions


def check_box(name: str, box: Sequence[float]) -> tuple[float, float, float, float]:
    if len(box) != 4:
        raise ValueError(f"{name}: a box is [x0, y0, x1, y1]")
    x0, y0, x1, y1 = (float(v) for v in box)
    if not all(0.0 <= v <= 1.0 for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        raise ValueError(f"{name}: x0 < x1 and y0 < y1, all between 0 and 1")
    return x0, y0, x1, y1


def read_region(pixels: Pixels, name: str, box: Sequence[float]) -> RegionReading:
    """Measure one rectangle, given as picture fractions [x0, y0, x1, y1] (y from the top)."""
    x0, y0, x1, y1 = check_box(name, box)
    height, width = pixels.shape[:2]
    left, right = round(x0 * width), round(x1 * width)
    top, bottom = round(y0 * height), round(y1 * height)
    patch = np.ascontiguousarray(pixels[top:bottom, left:right, :3], dtype=np.float32)
    count = int(patch.shape[0] * patch.shape[1])
    if count < MIN_PIXELS:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"Region '{name}' covers only {count} pixels; too small to measure.",
            next_step="Use a larger box (at least about 10 x 10 pixels of the preview).",
        )
    lab = cv2.cvtColor(np.clip(patch, 0.0, 1.0), cv2.COLOR_RGB2Lab).reshape(-1, 3)
    lightness, a, b = (float(np.median(lab[:, i])) for i in range(3))
    rgb = np.median(patch.reshape(-1, 3), axis=0)
    y = luma(patch)
    extreme = (patch <= 0.002).all(axis=2) | (patch >= 0.998).all(axis=2)
    return RegionReading(
        name=name,
        pixels=count,
        lightness=round(lightness, 2),
        a=round(a, 2),
        b=round(b, 2),
        hue_deg=round(math.degrees(math.atan2(b, a)) % 360.0, 1),
        chroma=round(math.hypot(a, b), 2),
        rgb=(round(rgb[0] * 255), round(rgb[1] * 255), round(rgb[2] * 255)),
        luma_p10=round(float(np.percentile(y, 10)), 4),
        luma_p90=round(float(np.percentile(y, 90)), 4),
        clipped_fraction=round(float(extreme.mean()), 4),
    )


def spread(readings: Sequence[RegionReading]) -> RegionSpread | None:
    """How far apart the regions are (None for fewer than two)."""
    if len(readings) < 2:
        return None
    hue_gap = max(
        min(abs(p.hue_deg - q.hue_deg), 360.0 - abs(p.hue_deg - q.hue_deg))
        for i, p in enumerate(readings)
        for q in readings[i + 1 :]
    )
    return RegionSpread(
        lightness_range=round(
            max(r.lightness for r in readings) - min(r.lightness for r in readings), 2
        ),
        chroma_range=round(max(r.chroma for r in readings) - min(r.chroma for r in readings), 2),
        hue_range_deg=round(hue_gap, 1),
    )
