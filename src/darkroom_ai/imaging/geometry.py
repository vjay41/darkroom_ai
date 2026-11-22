"""Crop geometry: the largest crop of a given shape that fits inside a rotated picture.

Pure arithmetic. The rectangle is returned as fractions of the ORIGINAL frame, centred (or moved
towards an anchor as far as the rotated picture allows), together with a check that all four
corners really lie inside the picture.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict


class CropPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    left: float
    top: float
    right: float
    bottom: float
    angle: float
    aspect: float  # width / height of the crop
    kept_fraction: float  # crop area / frame area
    fits_inside: bool
    centre: tuple[float, float]


def parse_aspect(text: str, frame_aspect: float) -> float:
    """'3:2', '4:5', '16:9', '1:1', '1.5' or 'original' -> width / height."""
    value = text.strip().lower()
    if value in ("original", "keep", ""):
        return frame_aspect
    try:
        if ":" in value:
            w, h = value.split(":", 1)
            ratio = float(w) / float(h)
        else:
            ratio = float(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"'{text}' is not an aspect ratio (use for example 3:2 or 4:5)") from exc
    if not 0.2 <= ratio <= 5.0:
        raise ValueError("aspect ratio must be between 1:5 and 5:1")
    return ratio


def plan_crop(
    width: int,
    height: int,
    angle_deg: float,
    aspect: float,
    anchor: tuple[float, float] | None = None,
) -> CropPlan:
    """Largest crop with `aspect` (w/h) that stays inside the picture rotated by `angle_deg`.

    `anchor` (x, y as fractions of the frame) moves the crop towards that point, as far as the
    rotated picture allows; None keeps it centred.
    """
    theta = math.radians(abs(angle_deg))
    c, s = math.cos(theta), math.sin(theta)
    # Crop a x b, a = aspect * b, rotated by theta, must fit in width x height.
    b = min(width / (aspect * c + s), height / (aspect * s + c))
    a = aspect * b
    extent_x = a * c + b * s  # horizontal / vertical reach of the rotated crop
    extent_y = a * s + b * c
    slack_x, slack_y = (width - extent_x) / 2.0, (height - extent_y) / 2.0
    dx = dy = 0.0
    if anchor is not None:
        dx = max(-slack_x, min(slack_x, anchor[0] * width - width / 2.0))
        dy = max(-slack_y, min(slack_y, anchor[1] * height - height / 2.0))
    cx, cy = width / 2.0 + dx, height / 2.0 + dy
    plan_left, plan_right = (cx - a / 2.0) / width, (cx + a / 2.0) / width
    plan_top, plan_bottom = (cy - b / 2.0) / height, (cy + b / 2.0) / height
    fits = (
        cx - extent_x / 2.0 >= -1e-6
        and cx + extent_x / 2.0 <= width + 1e-6
        and cy - extent_y / 2.0 >= -1e-6
        and cy + extent_y / 2.0 <= height + 1e-6
    )
    return CropPlan(
        left=round(plan_left, 5),
        top=round(plan_top, 5),
        right=round(plan_right, 5),
        bottom=round(plan_bottom, 5),
        angle=round(angle_deg, 3),
        aspect=round(aspect, 4),
        kept_fraction=round(a * b / (width * height), 4),
        fits_inside=fits,
        centre=(round(cx / width, 4), round(cy / height, 4)),
    )
