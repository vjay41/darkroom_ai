"""zone_map and find_candidates: where the dark areas are and what stands out. Numbers only."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from darkroom_ai.imaging.io import load_image
from darkroom_ai.imaging.scene import (
    Candidate,
    ZoneMap,
)
from darkroom_ai.imaging.scene import (
    find_candidates as find,
)
from darkroom_ai.imaging.scene import (
    zone_map as zones,
)
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, resolve_copy

SCENE_LONG_EDGE = 1024


class ZoneMapInput(BaseModel):
    """Map brightness over the photo: a grid of cells (mean luma, tonal zone 0-10, clipping) and
    the connected dark regions with their box, area, how much darker than their surroundings they
    are, and how hard their edge is (a hard edge behind a subject is typical of flash shadows).
    Numbers only; look at the picture to decide what a region is. Reads only."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    rows: int = Field(default=6, ge=2, le=12)
    cols: int = Field(default=8, ge=2, le=16)
    shadow_luma: float = Field(default=0.25, gt=0.02, lt=0.6, description="Dark below this luma.")


class ZoneMapResult(ToolResult):
    photo_id: str
    preview_path: str
    map: ZoneMap
    note: str = "Boxes are picture fractions [x0, y0, x1, y1], y from the top."


def zone_map(ctx: ToolContext, args: ZoneMapInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    preview = ctx.lightroom.export_preview(
        target.id, f"zones-{uuid.uuid4().hex[:12]}", SCENE_LONG_EDGE
    )
    measured = zones(load_image(preview.path), args.rows, args.cols, args.shadow_luma)
    result = ZoneMapResult(photo_id=target.id, preview_path=str(preview.path), map=measured)
    return Outcome(result, applied={"photo_id": target.id})


class FindCandidatesInput(BaseModel):
    """List small areas that stand out from their surroundings in colour or brightness, with box,
    size, colour difference to the surroundings, edge density and whether they touch the border.
    These are only measurements of what is different; they are not a list of things to remove.
    Look at the areas (view_region) before suggesting anything. Reads only."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    max_items: int = Field(default=8, ge=1, le=20)


class FindCandidatesResult(ToolResult):
    photo_id: str
    preview_path: str
    candidates: tuple[Candidate, ...]
    note: str = (
        "Boxes are picture fractions [x0, y0, x1, y1]. Higher saliency = stands out more. The "
        "subject of the photo can be on this list too."
    )


def find_candidates(ctx: ToolContext, args: FindCandidatesInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    preview = ctx.lightroom.export_preview(
        target.id, f"cands-{uuid.uuid4().hex[:12]}", SCENE_LONG_EDGE
    )
    found = find(load_image(preview.path), args.max_items)
    result = FindCandidatesResult(
        photo_id=target.id, preview_path=str(preview.path), candidates=found
    )
    return Outcome(result, applied={"photo_id": target.id, "count": len(found)})
