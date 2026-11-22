"""measure_tilt: how crooked the picture is, as numbers; deciding to straighten is the skill."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from darkroom_ai.imaging.io import load_image
from darkroom_ai.imaging.tilt import TiltReading
from darkroom_ai.imaging.tilt import measure_tilt as measure
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, resolve_copy

TILT_LONG_EDGE = 1280


class MeasureTiltInput(BaseModel):
    """Measure how far the long straight lines in a photo (horizon, walls, door frames) are from
    level and upright, as degrees with a confidence for the near-horizontal and the near-vertical
    group. Positive means the content is rotated clockwise. It reports numbers only: look at the
    preview too, because a sloping street or a leaning building gives a false tilt. Reads only."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1, description="Catalog id of the photo or its working copy.")


class MeasureTiltResult(ToolResult):
    photo_id: str
    preview_path: str
    reading: TiltReading


def measure_tilt(ctx: ToolContext, args: MeasureTiltInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    preview = ctx.lightroom.export_preview(
        target.id, f"tilt-{uuid.uuid4().hex[:12]}", TILT_LONG_EDGE
    )
    reading = measure(load_image(preview.path))
    result = MeasureTiltResult(photo_id=target.id, preview_path=str(preview.path), reading=reading)
    return Outcome(result, applied={"photo_id": target.id})
