"""Measure how crooked a picture is, from its long straight lines.

Pure numbers: for the near-horizontal and the near-vertical lines it reports how far the content is
rotated, so a skill can decide what to do. Positive means the content looks rotated CLOCKWISE (a
horizon that falls towards the right, a vertical that leans to the right at the top); correcting it
means rotating the other way by the same amount.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

from darkroom_ai.imaging.io import Pixels

WORK_LONG_EDGE = 1024
FAMILY_LIMIT_DEG = (
    20.0  # lines further than this from horizontal/vertical are not "meant" to be level
)
MIN_LINE_FRACTION = 0.10  # shortest line used, as a share of the long edge


class LineFamily(BaseModel):
    model_config = ConfigDict(frozen=True)

    tilt_deg: float  # content rotation, positive = clockwise
    line_count: int
    length_share: float  # total length of these lines / long edge
    spread_deg: float  # weighted median absolute deviation of the lines from tilt_deg
    confidence: str  # "high" | "medium" | "low"


class TiltReading(BaseModel):
    model_config = ConfigDict(frozen=True)

    horizontal: LineFamily | None
    vertical: LineFamily | None
    families_agree: bool | None  # None when one family is missing
    note: str


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights)
    return float(values[int(np.searchsorted(cumulative, cumulative[-1] / 2.0))])


def _family(deviations: list[float], lengths: list[float], long_edge: float) -> LineFamily | None:
    if not deviations:
        return None
    values, weights = np.array(deviations), np.array(lengths)
    median = _weighted_median(values, weights)
    spread = _weighted_median(np.abs(values - median), weights)
    share = float(weights.sum() / long_edge)
    count = len(deviations)
    if count >= 6 and share >= 1.5 and spread <= 0.6:
        confidence = "high"
    elif count >= 3 and share >= 0.6 and spread <= 1.5:
        confidence = "medium"
    else:
        confidence = "low"
    return LineFamily(
        tilt_deg=round(median, 2),
        line_count=count,
        length_share=round(share, 2),
        spread_deg=round(spread, 2),
        confidence=confidence,
    )


def measure_tilt(pixels: Pixels) -> TiltReading:
    height, width = pixels.shape[:2]
    scale = WORK_LONG_EDGE / max(height, width)
    if scale < 1.0:
        pixels = np.asarray(
            cv2.resize(pixels, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA),
            dtype=np.float32,
        )
    gray = cv2.cvtColor(np.clip(pixels * 255.0, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 60, 160)
    long_edge = float(max(gray.shape))
    found = cv2.HoughLinesP(
        edges,
        1,
        math.radians(0.25),
        threshold=60,
        minLineLength=int(MIN_LINE_FRACTION * long_edge),
        maxLineGap=int(0.01 * long_edge) + 2,
    )
    horizontal: tuple[list[float], list[float]] = ([], [])
    vertical: tuple[list[float], list[float]] = ([], [])
    # The array shape of the result differs between cv2 versions, so normalise it.
    segments = np.zeros((0, 4)) if found is None else np.asarray(found).reshape(-1, 4)
    for segment in segments:
        x1, y1, x2, y2 = (float(v) for v in segment)
        length = math.hypot(x2 - x1, y2 - y1)
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))  # image y points down
        if angle > 90.0:
            angle -= 180.0
        elif angle <= -90.0:
            angle += 180.0
        if abs(angle) <= FAMILY_LIMIT_DEG:
            horizontal[0].append(angle)  # falls towards the right = clockwise = positive
            horizontal[1].append(length)
        elif abs(abs(angle) - 90.0) <= FAMILY_LIMIT_DEG:
            # Near vertical: leaning right at the top (clockwise) gives a negative dx going down.
            deviation = angle - 90.0 if angle > 0 else angle + 90.0
            vertical[0].append(deviation)
            vertical[1].append(length)
    h_family = _family(*horizontal, long_edge)
    v_family = _family(*vertical, long_edge)
    agree: bool | None = None
    if h_family and v_family:
        agree = abs(h_family.tilt_deg - v_family.tilt_deg) <= 1.0
    note = (
        "Positive = content rotated clockwise; correct by rotating the other way. Lines in "
        "furniture, shelves and walls are used as they are: a leaning building or a sloping "
        "street gives a false tilt, so check the picture and prefer the family that agrees."
    )
    return TiltReading(horizontal=h_family, vertical=v_family, families_agree=agree, note=note)
