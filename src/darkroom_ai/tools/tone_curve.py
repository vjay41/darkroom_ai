"""Tone curve tools: read a curve, and change it within the guardrail limits.

A curve is a list of points on a 0-255 scale (x = input, y = output). Lightroom stores it as a flat
list of numbers (x1, y1, x2, y2, ...) in the develop settings `ToneCurvePV2012` (the combined RGB
curve) and `ToneCurvePV2012Red/Green/Blue`. Changes go to the working copy, are limited against the
current curve (per pass) and against a straight line (total), are measured on real renders and are
undone with the ordinary `undo`.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.context import (
    MEASURED_SOURCE,
    Outcome,
    ToolContext,
    ToolResult,
    check_process_version,
    measure,
    measurement_before,
    resolve_copy,
    working_copy,
)

Channel = Literal["rgb", "red", "green", "blue"]
CHANNEL_KEYS: dict[str, str] = {
    "rgb": "ToneCurvePV2012",
    "red": "ToneCurvePV2012Red",
    "green": "ToneCurvePV2012Green",
    "blue": "ToneCurvePV2012Blue",
}
MIN_POINTS = 2
MAX_POINTS = 16
SCALE = 255.0


class CurvePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(ge=0.0, le=SCALE, description="Input level, 0 (black) to 255 (white).")
    y: float = Field(ge=0.0, le=SCALE, description="Output level, 0 to 255.")


def _points_from_flat(flat: Any) -> list[CurvePoint]:
    numbers = flat if isinstance(flat, list) else None
    if (
        numbers is None
        or len(numbers) < 2 * MIN_POINTS
        or len(numbers) % 2
        or not all(isinstance(v, int | float) for v in numbers)
    ):
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            "Lightroom reports no usable tone curve for this photo. Nothing was changed.",
            next_step="Check the photo's Tone Curve panel in Lightroom's Develop module, "
            "then retry.",
            details={"reported": flat if isinstance(flat, list | int | float | str) else None},
        )
    return [
        CurvePoint(x=float(numbers[i]), y=float(numbers[i + 1])) for i in range(0, len(numbers), 2)
    ]


def _flat(points: list[CurvePoint]) -> list[float]:
    flat: list[float] = []
    for point in points:
        flat += [round(point.x, 2), round(point.y, 2)]
    return [int(v) if float(v).is_integer() else v for v in flat]


def _value_at(curve: list[CurvePoint], x: float) -> float:
    """The curve's output at input `x` (straight lines between points)."""
    if x <= curve[0].x:
        return curve[0].y
    for left, right in pairwise(curve):
        if x <= right.x:
            span = right.x - left.x
            return left.y if span <= 0 else left.y + (right.y - left.y) * (x - left.x) / span
    return curve[-1].y


def limit_points(
    requested: list[CurvePoint], current: list[CurvePoint], per_pass: float, total: float
) -> list[CurvePoint]:
    """Clamp each point's output: within `per_pass` of the current curve and `total` of a straight
    line, inside 0..255, and never lower than the point before it (a curve must not fold back)."""
    limited: list[CurvePoint] = []
    floor = 0.0
    for point in requested:
        base = _value_at(current, point.x)
        y = min(max(point.y, base - per_pass), base + per_pass)
        y = min(max(y, point.x - total), point.x + total)
        y = min(max(y, floor, 0.0), SCALE)
        floor = y
        limited.append(CurvePoint(x=point.x, y=round(y, 2)))
    return limited


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1, description="Catalog id of the photo or its working copy.")


# -- get_tone_curve -------------------------------------------------------------------------------


class GetToneCurveInput(_Input):
    """Read a photo's tone curve as points on a 0-255 scale. Changes nothing.

    channel "rgb" is the combined curve; "red", "green" and "blue" are the colour curves. A straight
    line from (0,0) to (255,255) means no curve.
    """

    channel: Channel = "rgb"


class ToneCurveReading(ToolResult):
    photo_id: str
    channel: Channel
    points: list[CurvePoint]


def get_tone_curve(ctx: ToolContext, args: GetToneCurveInput) -> Outcome:
    photo = resolve_copy(ctx, args.photo_id)
    key = CHANNEL_KEYS[args.channel]
    settings = ctx.lightroom.get_develop_settings(photo.id, [key])
    return Outcome(
        ToneCurveReading(
            photo_id=photo.id, channel=args.channel, points=_points_from_flat(settings.get(key))
        )
    )


# -- adjust_tone_curve ----------------------------------------------------------------------------


class AdjustToneCurveInput(_Input):
    """Set a tone curve on the photo's working copy (a virtual copy; never the original).

    Give the whole curve as points on a 0-255 scale, from x=0 to x=255 with x increasing (2 to 16
    points). Read the current curve first with get_tone_curve and change it a little: each output
    is limited to a small step from the current curve and to a bounded distance from a straight
    line, and outputs never fall below the point before. Anything limited is reported in `note`.
    channel "rgb" is the combined curve; "red", "green", "blue" are the colour curves. Undo with
    the ordinary undo tool.
    """

    channel: Channel = "rgb"
    points: list[CurvePoint] = Field(min_length=MIN_POINTS, max_length=MAX_POINTS)

    @model_validator(mode="after")
    def _is_a_curve(self) -> Self:
        xs = [point.x for point in self.points]
        if any(b <= a for a, b in pairwise(xs)):
            raise ValueError("x must increase from one point to the next")
        if xs[0] != 0 or xs[-1] != SCALE:
            raise ValueError("the first point must have x=0 and the last x=255")
        return self


class AdjustToneCurveResult(ToolResult):
    photo_id: str  # the virtual copy that was edited (use this id from now on)
    working_copy_created: bool
    channel: Channel
    requested: list[CurvePoint]
    applied: list[CurvePoint]
    before: list[CurvePoint]
    clamped: bool
    no_change: bool = False
    note: str = ""
    measured: dict[str, Any] | None = None


def adjust_tone_curve(ctx: ToolContext, args: AdjustToneCurveInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    key = CHANNEL_KEYS[args.channel]
    current_settings = ctx.lightroom.get_develop_settings(copy.id, [key, "ProcessVersion"])
    check_process_version(current_settings)
    current = _points_from_flat(current_settings.get(key))

    limits = ctx.settings.guardrails
    applied = limit_points(args.points, current, limits.curve_per_pass, limits.curve_total)
    clamped = applied != args.points
    note = ""
    if clamped:
        moved = [
            f"x={a.x:g}: asked {r.y:g}, applied {a.y:g}"
            for r, a in zip(args.points, applied, strict=True)
            if r.y != a.y
        ]
        note = (
            f"Limited (at most {limits.curve_per_pass:g} from the current curve, "
            f"{limits.curve_total:g} from a straight line) - " + "; ".join(moved)
        )

    if applied == current:
        return Outcome(
            AdjustToneCurveResult(
                photo_id=copy.id,
                working_copy_created=created,
                channel=args.channel,
                requested=args.points,
                applied=applied,
                before=current,
                clamped=clamped,
                no_change=True,
                note=(note + " " if note else "") + "The curve already has these points.",
            )
        )

    before_measured = measurement_before(ctx, copy.id, args.photo_id, created)
    change = ctx.lightroom.apply_develop_settings(
        copy.id, {key: _flat(applied)}, "darkroom_ai: tone curve"
    )
    shown = _points_from_flat(change.after.get(key))
    measured = {
        "before": before_measured,
        "after": measure(ctx, copy.id),
        "source": MEASURED_SOURCE,
    }
    if shown != applied:
        note = (note + " " if note else "") + (
            "Lightroom reports different points than were sent; the points shown are what it has."
        )
    result = AdjustToneCurveResult(
        photo_id=copy.id,
        working_copy_created=created,
        channel=args.channel,
        requested=args.points,
        applied=shown,
        before=current,
        clamped=clamped,
        note=note,
        measured=measured,
    )
    return Outcome(
        result,
        applied={"photo_id": copy.id, "settings": {key: _flat(applied)}},
        undo={"photo_id": copy.id, "settings": {key: _flat(current)}},
    )


__all__ = [
    "AdjustToneCurveInput",
    "GetToneCurveInput",
    "adjust_tone_curve",
    "get_tone_curve",
    "limit_points",
]
