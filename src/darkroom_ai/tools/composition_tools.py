"""plan_crop and view_region: crop arithmetic and a closer look. Both only read."""

from __future__ import annotations

import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.geometry import CropPlan, parse_aspect
from darkroom_ai.imaging.geometry import plan_crop as plan
from darkroom_ai.imaging.io import load_image, write_image
from darkroom_ai.imaging.region import check_box
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, resolve_copy

VIEW_SOURCE_EDGE = 2400
VIEW_MAX_EDGE = 1600


class PlanCropInput(BaseModel):
    """Work out the largest crop with a given shape (aspect ratio) that stays inside the picture
    after rotating it by `angle` degrees, as numbers for the `crop` tool. It changes nothing.
    To level a horizon: measure with measure_tilt, rotate by the opposite amount, plan the crop,
    apply it with `crop`, then measure again; if the tilt grew, use the other sign."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    aspect: str = Field(
        default="original",
        description="'original', or width:height such as 3:2, 4:5, 16:9, 1:1 (landscape or "
        "portrait is set by the order of the numbers).",
    )
    angle: float = Field(default=0.0, ge=-45.0, le=45.0, description="Rotation in degrees.")
    anchor: tuple[float, float] | None = Field(
        default=None,
        description="[x, y] picture fractions to keep the crop near (for example a face), "
        "as far as the rotated picture allows. Omit to centre the crop.",
    )

    @field_validator("anchor")
    @classmethod
    def _anchor_in_frame(cls, anchor: tuple[float, float] | None) -> tuple[float, float] | None:
        if anchor is not None and not all(0.0 <= v <= 1.0 for v in anchor):
            raise ValueError("anchor values must be between 0 and 1")
        return anchor


class PlanCropResult(ToolResult):
    photo_id: str
    frame: tuple[int, int]
    plan: CropPlan
    crop_arguments: dict[str, float]
    note: str = (
        "Fractions are of the original frame; the box is centred on the picture (or moved towards "
        "the anchor). The sign of the angle that levels a given tilt depends on Lightroom's "
        "convention: check with measure_tilt after applying, and flip the sign if it got worse."
    )


def _frame_size(ctx: ToolContext, photo_id: str) -> tuple[int, int]:
    meta = ctx.lightroom.get_metadata(photo_id)
    if meta.width and meta.height:
        return int(meta.width), int(meta.height)
    preview = ctx.lightroom.export_preview(photo_id, f"frame-{uuid.uuid4().hex[:12]}", 256)
    pixels = load_image(preview.path)
    return int(pixels.shape[1]), int(pixels.shape[0])


def plan_crop(ctx: ToolContext, args: PlanCropInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    width, height = _frame_size(ctx, target.id)
    try:
        aspect = parse_aspect(args.aspect, width / height)
    except ValueError as exc:
        raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, str(exc)) from exc
    chosen = plan(width, height, args.angle, aspect, args.anchor)
    result = PlanCropResult(
        photo_id=target.id,
        frame=(width, height),
        plan=chosen,
        crop_arguments={
            "left": chosen.left,
            "top": chosen.top,
            "right": chosen.right,
            "bottom": chosen.bottom,
            "angle": chosen.angle,
        },
    )
    return Outcome(result, applied={"photo_id": target.id})


class ViewRegionInput(BaseModel):
    """Make a close-up picture of part of the photo as it is now, so you can look at it with your
    image viewer (for example a face, a shadow edge or a spot). Reads only."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    box: tuple[float, float, float, float] = Field(
        default=(0.0, 0.0, 1.0, 1.0), description="[x0, y0, x1, y1] as picture fractions."
    )
    long_edge: int = Field(default=1024, ge=256, le=VIEW_MAX_EDGE)

    @field_validator("box")
    @classmethod
    def _valid_box(
        cls, box: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        return check_box("box", box)


class ViewRegionResult(ToolResult):
    photo_id: str
    path: str
    size: tuple[int, int]
    box: tuple[float, float, float, float]
    note: str = "Open the file with your image viewer. Viewed pictures are sent to the model."


def view_region(ctx: ToolContext, args: ViewRegionInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    preview = ctx.lightroom.export_preview(
        target.id, f"view-src-{uuid.uuid4().hex[:12]}", VIEW_SOURCE_EDGE
    )
    pixels = load_image(preview.path)
    height, width = pixels.shape[:2]
    x0, y0, x1, y1 = args.box
    patch = pixels[round(y0 * height) : round(y1 * height), round(x0 * width) : round(x1 * width)]
    if patch.size == 0:
        raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, "That box is empty.")
    import cv2
    import numpy as np

    scale = args.long_edge / max(patch.shape[:2])
    if scale != 1.0:
        patch = np.asarray(
            cv2.resize(
                patch,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
            ),
            dtype=np.float32,
        )
    out = write_image(
        Path(ctx.settings.paths.previews_dir) / f"view-{uuid.uuid4().hex[:12]}.jpg",
        np.clip(patch, 0.0, 1.0),
    )
    result = ViewRegionResult(
        photo_id=target.id,
        path=str(out),
        size=(int(patch.shape[1]), int(patch.shape[0])),
        box=args.box,
    )
    return Outcome(result, applied={"photo_id": target.id})
