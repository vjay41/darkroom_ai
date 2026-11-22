"""remove_objects and fix_red_eye: cleanup that makes NEW files and never touches the original.

Which things to remove, and whether to suggest it, is the skill's job and the person's decision.
These tools only carry out the regions they are given.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.io import Pixels, load_image, save_mask_png, write_image
from darkroom_ai.imaging.region import check_box
from darkroom_ai.imaging.scene import fix_red_eye as redeye
from darkroom_ai.imaging.scene import inpaint, regions_mask
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, working_copy

MAX_REGIONS = 8
MAX_AREA_CONTENT_AWARE = 0.15  # share of the frame
MAX_AREA_INPAINT = 0.02
CONTEXT_MARGIN = 0.6  # before/after sheet: extra context around a region, as a share of its size


class RemoveRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40, description="Your label, e.g. 'bin on left'.")
    box: tuple[float, float, float, float] = Field(
        description="[x0, y0, x1, y1] as picture fractions (y from the top); cover the whole "
        "object, a little loosely."
    )
    shape: Literal["box", "ellipse"] = "box"

    @field_validator("box")
    @classmethod
    def _valid_box(
        cls, box: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        return check_box("box", box)


class RemoveObjectsInput(BaseModel):
    """Remove objects, blemishes, dust or sensor spots by filling the given regions from their
    surroundings. The result is a NEW 16-bit TIFF and JPG (and a PSD with Content-Aware Fill on
    its own layer when Photoshop is used) made from the working copy as it is now; the original
    photo and the catalog are unchanged. 'content_aware' uses Photoshop and suits larger or
    textured objects; 'inpaint' works without Photoshop and suits small spots on smooth areas.
    Show the person what you intend to remove and get their agreement before calling this."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    regions: list[RemoveRegion] = Field(min_length=1, max_length=MAX_REGIONS)
    method: Literal["auto", "content_aware", "inpaint"] = "auto"

    @model_validator(mode="after")
    def _unique_names(self) -> RemoveObjectsInput:
        names = [r.name for r in self.regions]
        if len(set(names)) != len(names):
            raise ValueError("region names must be different")
        return self


class RetouchResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    method: str
    jpg_path: str
    tiff_path: str | None = None
    psd_path: str | None = None
    before_after_path: str | None = None
    covered_pct: float
    regions: list[str]
    seconds: float
    note: str = (
        "New files only: the original photo and the catalog are unchanged, and the files are not "
        "imported into Lightroom. They were made from the working copy as it is now."
    )


def _stamp() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _output_dir(ctx: ToolContext) -> Path:
    folder = Path(ctx.settings.paths.outputs_dir) / "retouch"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _before_after(
    before: Pixels, after: Pixels, boxes: list[tuple[float, float, float, float]], target: Path
) -> str | None:
    """Side-by-side crops (before | after) of each region with some context, stacked."""
    try:
        height, width = before.shape[:2]
        if after.shape != before.shape:
            after = np.asarray(cv2.resize(after, (width, height)), dtype=np.float32)
        rows: list[Pixels] = []
        for x0, y0, x1, y1 in boxes:
            pad_x, pad_y = (x1 - x0) * CONTEXT_MARGIN, (y1 - y0) * CONTEXT_MARGIN
            left, right = round(max(0.0, x0 - pad_x) * width), round(min(1.0, x1 + pad_x) * width)
            top, bottom = round(max(0.0, y0 - pad_y) * height), round(min(1.0, y1 + pad_y) * height)
            pair = [before[top:bottom, left:right], after[top:bottom, left:right]]
            scale = 360 / max(pair[0].shape[:2])
            pair = [
                np.asarray(cv2.resize(p, None, fx=scale, fy=scale), dtype=np.float32) for p in pair
            ]
            rows.append(np.concatenate(pair, axis=1))
        sheet_width = max(r.shape[1] for r in rows)
        rows = [
            np.pad(r, ((0, 0), (0, sheet_width - r.shape[1]), (0, 0)), constant_values=0.5)
            for r in rows
        ]
        return str(write_image(target, np.concatenate(rows, axis=0)))
    except Exception:  # the sheet is a convenience; the result does not depend on it
        return None


def remove_objects(ctx: ToolContext, args: RemoveObjectsInput) -> Outcome:
    method = args.method
    if method == "auto":
        method = "content_aware" if ctx.photoshop is not None else "inpaint"
    if method == "content_aware" and ctx.photoshop is None:
        raise DarkroomError(
            ErrorCode.PS_NOT_REACHABLE,
            "Content-Aware removal needs Photoshop, which is not set up here.",
            next_step="Use method 'inpaint' for small spots, or start Photoshop.",
        )
    started = time.monotonic()
    copy, created = working_copy(ctx, args.photo_id)
    stamp = _stamp()
    tiff = ctx.lightroom.export_tiff(copy.id, f"retouch-{stamp}")
    pixels = load_image(tiff.path)
    height, width = pixels.shape[:2]
    grow = max(2, round(max(height, width) * 0.003))
    regions = [r.model_dump() for r in args.regions]
    mask = regions_mask((height, width), regions, grow)
    covered = float((mask > 0).mean())
    limit = MAX_AREA_CONTENT_AWARE if method == "content_aware" else MAX_AREA_INPAINT
    if covered > limit:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"These regions cover {covered:.1%} of the picture; the limit for '{method}' is "
            f"{limit:.0%}. Nothing was changed.",
            next_step="Use smaller regions"
            + (", or method 'content_aware' (Photoshop)." if method == "inpaint" else "."),
            details={"covered": round(covered, 4), "limit": limit},
        )
    boxes = [r.box for r in args.regions]
    psd_path: str | None = None
    tiff_out: str | None = None
    if method == "inpaint":
        after = inpaint(pixels, mask)
        folder = _output_dir(ctx)
        tiff_out = str(write_image(folder / f"{copy.id[:8]}-{stamp}-retouched.tif", after, bits=16))
        jpg = write_image(folder / f"{copy.id[:8]}-{stamp}-retouched.jpg", after)
        sheet_after: Pixels | None = after
    else:
        assert ctx.photoshop is not None
        photoshop = ctx.photoshop
        working = photoshop.prepare_copy(tiff.path)
        mask_png = working.with_name(f"{working.stem}_fill.png")
        save_mask_png(mask.astype(np.float32) / 255.0, mask_png)
        photoshop.open_document(working)
        try:
            photoshop.fill_selection_from_mask(working, mask_png, 0, "removed")
            psd_path = str(photoshop.save_psd(working))
            jpg = photoshop.save_jpg(working)
        finally:
            with contextlib.suppress(DarkroomError):
                photoshop.close_document(working)
        try:
            sheet_after = load_image(jpg)
        except DarkroomError:
            sheet_after = None
    sheet = None
    if sheet_after is not None:
        sheet = _before_after(
            pixels, sheet_after, boxes, _output_dir(ctx) / f"{copy.id[:8]}-{stamp}-before-after.jpg"
        )
    result = RetouchResult(
        photo_id=copy.id,
        working_copy_created=created,
        method=method,
        jpg_path=str(jpg),
        tiff_path=tiff_out,
        psd_path=psd_path,
        before_after_path=sheet,
        covered_pct=round(covered * 100, 3),
        regions=[r.name for r in args.regions],
        seconds=round(time.monotonic() - started, 1),
    )
    return Outcome(
        result,
        applied={
            "photo_id": copy.id,
            "method": method,
            "outputs": [p for p in (str(jpg), tiff_out, psd_path) if p],
            "regions": result.regions,
        },
    )


class EyeBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    box: tuple[float, float, float, float] = Field(
        description="[x0, y0, x1, y1] picture fractions around ONE eye (pupil and a little white)."
    )

    @field_validator("box")
    @classmethod
    def _valid_box(
        cls, box: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        return check_box("box", box)


class FixRedEyeInput(BaseModel):
    """Remove red-eye (flash glow in pupils) inside the given eye boxes. The result is a NEW
    16-bit TIFF and JPG made from the working copy as it is now; the original is unchanged. Only
    strongly red pixels inside the boxes change. Measure the eyes' share fixed in the result."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    eyes: list[EyeBox] = Field(min_length=1, max_length=6)


class FixRedEyeResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    jpg_path: str
    tiff_path: str
    fixed_share_per_eye: list[float]
    note: str = (
        "fixed_share_per_eye is the share of each box's pixels that were red and darkened; 0 "
        "means nothing red was found there. New files only; the original is unchanged."
    )


def fix_red_eye(ctx: ToolContext, args: FixRedEyeInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    stamp = _stamp()
    tiff = ctx.lightroom.export_tiff(copy.id, f"redeye-{stamp}")
    pixels = load_image(tiff.path)
    fixed, shares = redeye(pixels, [e.box for e in args.eyes])
    folder = _output_dir(ctx)
    tiff_out = write_image(folder / f"{copy.id[:8]}-{stamp}-redeye.tif", fixed, bits=16)
    jpg = write_image(folder / f"{copy.id[:8]}-{stamp}-redeye.jpg", fixed)
    result = FixRedEyeResult(
        photo_id=copy.id,
        working_copy_created=created,
        jpg_path=str(jpg),
        tiff_path=str(tiff_out),
        fixed_share_per_eye=shares,
    )
    return Outcome(
        result,
        applied={"photo_id": copy.id, "outputs": [str(jpg), str(tiff_out)], "eyes": len(shares)},
    )
