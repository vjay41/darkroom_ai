"""measure_region: colour and tone numbers for named rectangles (skin patches and the like)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from darkroom_ai.imaging.io import load_image
from darkroom_ai.imaging.region import (
    RegionReading,
    RegionSpread,
    check_box,
    read_region,
    spread,
)
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, resolve_copy

REGION_LONG_EDGE = 1024
MAX_REGIONS = 6


class RegionBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40, description="Your label, e.g. 'left cheek'.")
    box: tuple[float, float, float, float] = Field(
        description="[x0, y0, x1, y1] as fractions of the picture: x 0 = left, 1 = right; "
        "y 0 = top, 1 = bottom (the same fractions as get_preview and create_mask)."
    )

    @field_validator("box")
    @classmethod
    def _valid_box(
        cls, box: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        return check_box("box", box)


class MeasureRegionInput(BaseModel):
    """Measure the colour and brightness of up to 6 rectangles of a photo as numbers (CIELAB
    lightness, a*, b*, hue angle, chroma, median RGB, clipping). Use it to check skin tone: look at
    the preview, put a small box on clean skin (cheek or forehead, away from hair, shadow edges and
    highlights), measure, change something, and measure the same boxes again. Pass two or more
    boxes to see how far apart they are. Reads only; changes nothing."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1, description="Catalog id of the photo or its working copy.")
    regions: list[RegionBox] = Field(min_length=1, max_length=MAX_REGIONS)

    @field_validator("regions")
    @classmethod
    def _unique_names(cls, regions: list[RegionBox]) -> list[RegionBox]:
        names = [r.name for r in regions]
        if len(set(names)) != len(names):
            raise ValueError("region names must be different")
        return regions


class MeasureRegionResult(ToolResult):
    photo_id: str
    preview_path: str
    readings: tuple[RegionReading, ...]
    spread: RegionSpread | None
    note: str = (
        "Numbers come from a 1024 px render of the photo as it is now. Hue is the angle of "
        "(a*, b*): a higher angle is more yellow, a lower angle more red. Compare before and "
        "after with the same boxes; Lightroom shows mask edits a few seconds late, so wait "
        "about 5 seconds after a local change."
    )


def measure_region(ctx: ToolContext, args: MeasureRegionInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    preview = ctx.lightroom.export_preview(
        target.id, f"region-{uuid.uuid4().hex[:12]}", REGION_LONG_EDGE
    )
    pixels = load_image(preview.path)
    readings = tuple(read_region(pixels, r.name, r.box) for r in args.regions)
    result = MeasureRegionResult(
        photo_id=target.id,
        preview_path=str(preview.path),
        readings=readings,
        spread=spread(readings),
    )
    return Outcome(result, applied={"photo_id": target.id, "regions": len(readings)})
