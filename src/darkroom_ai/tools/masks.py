"""Lightroom masks: list what a photo has, and create one on its virtual copy.

Creating a mask makes Lightroom switch to the Develop module and open Masking (the SDK only allows
it there), so the person sees Lightroom change view while it runs. The tool checks the result by
counting masks before and after: it never reports a mask it could not see being created.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from darkroom_ai.adapters.lightroom.models import MaskCreation
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, resolve_copy, working_copy

MASKS_KEY = "MaskGroupBasedCorrections"
AI_SUBTYPES = ("subject", "sky", "background", "objects", "people", "landscape")
RANGE_SUBTYPES = ("color", "luminance", "depth")
MAX_RAW_CHARS = 4000


class ListMasksInput(BaseModel):
    """List the masks (local adjustments) on a photo or its working copy: how many, their names and
    the local adjustment values they carry. Reads only; changes nothing."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1, description="Catalog id of the photo or its working copy.")


class MaskEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    name: str | None = None
    active: Any = None
    local_adjustments: dict[str, Any] = Field(default_factory=dict)
    mask_parts: int = 0


class ListMasksResult(ToolResult):
    photo_id: str
    count: int
    masks: tuple[MaskEntry, ...]
    note: str = ""


def summarize_masks(corrections: Any) -> tuple[MaskEntry, ...]:
    """Reduce Lightroom's MaskGroupBasedCorrections value to a short, tolerant summary."""
    if not isinstance(corrections, list):
        return ()
    entries: list[MaskEntry] = []
    for index, item in enumerate(corrections):
        if not isinstance(item, dict):
            entries.append(MaskEntry(index=index))
            continue
        parts = item.get("CorrectionMasks")
        entries.append(
            MaskEntry(
                index=index,
                name=str(item["CorrectionName"]) if "CorrectionName" in item else None,
                active=item.get("CorrectionActive"),
                local_adjustments={
                    key: value for key, value in item.items() if str(key).startswith("Local")
                },
                mask_parts=len(parts) if isinstance(parts, list) else 0,
            )
        )
    return tuple(entries)


def list_masks(ctx: ToolContext, args: ListMasksInput) -> Outcome:
    copy = resolve_copy(ctx, args.photo_id)
    raw = ctx.lightroom.get_develop_settings(copy.id, [MASKS_KEY])
    masks = summarize_masks(raw.get(MASKS_KEY))
    note = "" if masks else "This photo has no masks."
    return Outcome(ListMasksResult(photo_id=copy.id, count=len(masks), masks=masks, note=note))


class CreateMaskInput(BaseModel):
    """Create a mask on the photo's virtual copy. mask_type 'aiSelection' needs subtype subject,
    sky, background, objects, people or landscape; 'rangeMask' needs color, luminance or depth;
    'gradient' and 'radialGradient' take no subtype. Lightroom switches to the Develop module while
    this runs (it takes a few seconds; the person should not click in Lightroom meanwhile). The
    mask is empty of adjustments: nothing changes in the picture until you set values in it.
    Undo with the undo tool."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    mask_type: Literal["aiSelection", "rangeMask", "gradient", "radialGradient"]
    subtype: str | None = None

    @model_validator(mode="after")
    def _subtype_matches(self) -> Self:
        allowed = {"aiSelection": AI_SUBTYPES, "rangeMask": RANGE_SUBTYPES}.get(self.mask_type)
        if allowed is None:
            if self.subtype is not None:
                raise ValueError(f"{self.mask_type} takes no subtype")
        elif self.subtype not in allowed:
            raise ValueError(f"subtype for {self.mask_type} must be one of: {', '.join(allowed)}")
        return self


class CreateMaskResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    creation: MaskCreation
    masks: tuple[MaskEntry, ...]
    note: str = ""


def create_mask(ctx: ToolContext, args: CreateMaskInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    before = ctx.lightroom.get_develop_settings(copy.id, [MASKS_KEY]).get(MASKS_KEY)
    creation = ctx.lightroom.create_mask(copy.id, args.mask_type, args.subtype)
    if not creation.created:
        raise DarkroomError(
            ErrorCode.ADOBE_FEATURE_UNAVAILABLE,
            f"Lightroom did not create the {args.mask_type} mask: the mask count stayed at "
            f"{creation.masks_before} after {creation.waited_s:g} s.",
            next_step="Check the photo in Lightroom's Develop module (masking may need the photo "
            "to finish loading, or this mask type is not available in this Lightroom version).",
            details={"mask_type": args.mask_type, "subtype": args.subtype},
        )
    after = ctx.lightroom.get_develop_settings(copy.id, [MASKS_KEY]).get(MASKS_KEY)
    previous = before if isinstance(before, list) else []
    result = CreateMaskResult(
        photo_id=copy.id,
        working_copy_created=created,
        creation=creation,
        masks=summarize_masks(after),
        note="Mask created. It has no adjustments yet.",
    )
    return Outcome(
        result,
        applied={"photo_id": copy.id, "mask_type": args.mask_type, "subtype": args.subtype},
        undo={"photo_id": copy.id, "settings": {MASKS_KEY: previous}},
    )
