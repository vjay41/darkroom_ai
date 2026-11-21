"""Lightroom masks: list what a photo has, and create one on its virtual copy.

Creating a mask makes Lightroom switch to the Develop module and open Masking (the SDK only allows
it there), so the person sees Lightroom change view while it runs. The tool checks the result by
counting masks before and after: it never reports a mask it could not see being created.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from darkroom_ai.adapters.lightroom.models import MaskCreation, MaskValues
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
    mask_id: str | None = None
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
        mask_id=creation.mask_id,
        note="Mask created and selected. It has no adjustments yet: use set_mask_adjustments "
        "with this mask_id (call inspect_masks if mask_id is empty).",
    )
    return Outcome(
        result,
        applied={"photo_id": copy.id, "mask_type": args.mask_type, "subtype": args.subtype},
        undo={"photo_id": copy.id, "settings": {MASKS_KEY: previous}},
    )


# -- inspect, adjust, invert, delete -------------------------------------------------------------

# Local (mask) adjustment names Lightroom's SDK accepts, with slider ranges. This is DATA: it says
# what exists and what values are valid, not what an edit should be.
LOCAL_PARAMETERS: dict[str, tuple[float, float, str]] = {
    "local_Exposure": (-5.0, 5.0, "EV"),
    "local_Contrast": (-100, 100, ""),
    "local_Highlights": (-100, 100, ""),
    "local_Shadows": (-100, 100, ""),
    "local_Whites": (-100, 100, ""),
    "local_Blacks": (-100, 100, ""),
    "local_Temperature": (-100, 100, "relative: cooler/warmer"),
    "local_Tint": (-100, 100, ""),
    "local_Saturation": (-100, 100, ""),
    "local_Clarity": (-100, 100, ""),
    "local_Texture": (-100, 100, ""),
    "local_Dehaze": (-100, 100, ""),
    "local_Sharpness": (-100, 100, ""),
    "local_LuminanceNoise": (0, 100, "noise reduction"),
    "local_Moire": (0, 100, ""),
    "local_Defringe": (0, 100, ""),
    "local_ToningHue": (0, 360, "colour tint hue"),
    "local_ToningSaturation": (0, 100, "colour tint amount"),
}
PER_PASS_SHARE = 0.25
EXPOSURE_STEP_EV = 1.5


class InspectMasksInput(BaseModel):
    """Show the masks of a photo's working copy as Lightroom reports them, including each mask's
    id (needed by set_mask_adjustments, invert_mask and delete_mask). Lightroom switches to the
    Develop module while this runs. Changes nothing in the picture."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)


class InspectMasksResult(ToolResult):
    photo_id: str
    selected_mask: str | None = None
    masks: Any = None
    note: str = (
        "Mask ids are in the entries (maskId or similar). selected_mask is the mask currently "
        "open in Lightroom."
    )


def inspect_masks(ctx: ToolContext, args: InspectMasksInput) -> Outcome:
    copy = resolve_copy(ctx, args.photo_id)
    seen = ctx.lightroom.inspect_masks(copy.id)
    return Outcome(
        InspectMasksResult(photo_id=copy.id, selected_mask=seen.selected_mask, masks=seen.masks)
    )


class SetMaskAdjustmentsInput(BaseModel):
    """Change local adjustments inside ONE mask, so only the masked area changes. Each value is the
    CHANGE to apply (positive or negative), not the final value, limited to the parameter's range
    and to a per-call amount (exposure 1.5 EV; others a quarter of their range). Names: see
    LOCAL_PARAMETERS in the error text if you use a wrong one. mask_id comes from create_mask or
    inspect_masks; leave it out to use the mask currently selected in Lightroom. Undo with the
    undo tool."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    mask_id: str | None = None
    changes: dict[str, float] = Field(min_length=1, max_length=12)


class SetMaskAdjustmentsResult(ToolResult):
    photo_id: str
    mask: MaskValues
    requested_change: dict[str, float]
    applied_change: dict[str, float]
    note: str = ""


def _local_spec(key: str, change: float) -> dict[str, float]:
    low, high, _unit = LOCAL_PARAMETERS[key]
    step = EXPOSURE_STEP_EV if key == "local_Exposure" else (high - low) * PER_PASS_SHARE
    return {"change": float(change), "min": float(low), "max": float(high), "max_step": float(step)}


def set_mask_adjustments(ctx: ToolContext, args: SetMaskAdjustmentsInput) -> Outcome:
    unknown = [key for key in args.changes if key not in LOCAL_PARAMETERS]
    if unknown:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"Not a mask adjustment: {', '.join(unknown)}.",
            next_step="Use one of: " + ", ".join(sorted(LOCAL_PARAMETERS)),
        )
    copy = resolve_copy(ctx, args.photo_id)
    before_masks = ctx.lightroom.get_develop_settings(copy.id, [MASKS_KEY]).get(MASKS_KEY)
    values = {key: _local_spec(key, change) for key, change in args.changes.items()}
    got = ctx.lightroom.set_mask_values(copy.id, args.mask_id, values)
    applied = {key: round(got.after[key] - got.before[key], 4) for key in got.after}
    result = SetMaskAdjustmentsResult(
        photo_id=copy.id,
        mask=got,
        requested_change=dict(args.changes),
        applied_change=applied,
        note="Values are inside the mask only. Use get_preview to look at the result.",
    )
    previous = before_masks if isinstance(before_masks, list) else []
    return Outcome(
        result,
        applied={"photo_id": copy.id, "mask_id": got.mask_id, "changes": applied},
        undo={"photo_id": copy.id, "settings": {MASKS_KEY: previous}},
    )


class MaskTargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    mask_id: str = Field(min_length=1, description="From create_mask or inspect_masks.")


class InvertMaskInput(MaskTargetInput):
    """Invert one mask so it covers everything it did not (for example: select the subject, then
    invert to adjust the background). Undo with the undo tool."""


class DeleteMaskInput(MaskTargetInput):
    """Delete one mask from the working copy (the original photo is never touched). Undo with the
    undo tool restores it."""


class MaskOperationResult(ToolResult):
    photo_id: str
    operation: str
    masks_before: int
    masks_after: int


def _mask_operation(ctx: ToolContext, args: MaskTargetInput, operation: str) -> Outcome:
    copy = resolve_copy(ctx, args.photo_id)
    previous = ctx.lightroom.get_develop_settings(copy.id, [MASKS_KEY]).get(MASKS_KEY)
    got = ctx.lightroom.mask_operation(copy.id, args.mask_id, operation)
    result = MaskOperationResult(
        photo_id=copy.id,
        operation=operation,
        masks_before=got.masks_before,
        masks_after=got.masks_after,
    )
    return Outcome(
        result,
        applied={"photo_id": copy.id, "mask_id": args.mask_id, "operation": operation},
        undo={
            "photo_id": copy.id,
            "settings": {MASKS_KEY: previous if isinstance(previous, list) else []},
        },
    )


def invert_mask(ctx: ToolContext, args: InvertMaskInput) -> Outcome:
    return _mask_operation(ctx, args, "invert")


def delete_mask(ctx: ToolContext, args: DeleteMaskInput) -> Outcome:
    return _mask_operation(ctx, args, "delete")
