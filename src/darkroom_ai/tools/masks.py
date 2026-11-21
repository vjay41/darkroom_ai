"""Lightroom masks: list what a photo has, and create one on its virtual copy.

Two ways to make a mask, both checked by reading the photo's settings back:
- Gradient, radial gradient and luminance range masks are WRITTEN as Lightroom's own stored mask
  data (the format was captured from masks drawn by hand in Lightroom 15.2.1). Lightroom's SDK
  call for these only arms a drawing tool and waits for a person to drag, so it creates nothing.
- AI masks (subject, sky, background, objects, landscape) use the SDK call, which makes Lightroom
  switch to the Develop module and open Masking. People masks need the person chosen in Lightroom
  and "Create Mask" pressed there; the tool hands that step to the person and then checks.
It never reports a mask it could not see in the photo's settings.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from darkroom_ai.adapters.lightroom.models import MaskCreation, MaskValues
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, resolve_copy, working_copy
from darkroom_ai.tools.takeover import run_with_takeover, takeover_note

MASKS_KEY = "MaskGroupBasedCorrections"
AI_SUBTYPES = ("subject", "sky", "background", "objects", "people", "landscape")
RANGE_SUBTYPES = ("color", "luminance", "depth")
MAX_RAW_CHARS = 4000
READBACK_WAIT_S = 6.0
READBACK_POLL_S = 0.5
PEOPLE_WAIT_S = 12.0
_sleep = time.sleep

Point = tuple[float, float]
# Slider defaults of a new mask exactly as Lightroom stores them (all zero except the curve refine).
LOCAL_DEFAULTS: dict[str, float] = {
    "LocalBlacks2012": 0,
    "LocalBrightness": 0,
    "LocalClarity": 0,
    "LocalClarity2012": 0,
    "LocalContrast": 0,
    "LocalContrast2012": 0,
    "LocalCurveRefineSaturation": 100,
    "LocalDefringe": 0,
    "LocalDehaze": 0,
    "LocalExposure": 0,
    "LocalExposure2012": 0,
    "LocalGrain": 0,
    "LocalHighlights2012": 0,
    "LocalHue": 0,
    "LocalLuminanceNoise": 0,
    "LocalMoire": 0,
    "LocalSaturation": 0,
    "LocalShadows2012": 0,
    "LocalSharpness": 0,
    "LocalTemperature": 0,
    "LocalTexture": 0,
    "LocalTint": 0,
    "LocalToningHue": 0,
    "LocalToningSaturation": 0,
    "LocalWhites2012": 0,
}


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
    """Create a mask on the photo's virtual copy; it starts with no adjustments (use
    set_mask_adjustments next). Positions are fractions of the picture as shown in Lightroom:
    x 0 = left, 1 = right; y 0 = top, 1 = bottom (the same fractions as in get_preview).
    - mask_type 'gradient': give start and end. The effect is full at start and fades to nothing
      at end.
    - mask_type 'radialGradient': give center, radius_x, radius_y (fractions of the width and
      height; the ellipse spans center +- radius), optional feather (0-100, default 50) and
      angle (degrees, default 0). The effect is inside the ellipse; use invert_mask for outside.
    - mask_type 'rangeMask' with subtype 'luminance': give luminance_range = four brightness
      values 0-1 in rising order (a, b, c, d): the mask ramps in from a to b, is full from b to c
      and ramps out from c to d (0, 0, 1, 1 = the whole tonal range).
    - mask_type 'aiSelection' with subtype subject, sky, background, objects, landscape or people
      (people: Lightroom needs the person chosen and 'Create Mask' pressed in its panel; the
      tool asks the person to do that). Lightroom switches to Develop while this runs; the person
      should not click in Lightroom meanwhile.
    - rangeMask color or depth cannot be created automatically yet.
    Undo with the undo tool."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    mask_type: Literal["aiSelection", "rangeMask", "gradient", "radialGradient"]
    subtype: str | None = None
    start: Point | None = None
    end: Point | None = None
    center: Point | None = None
    radius_x: float | None = Field(default=None, gt=0.0, le=1.0)
    radius_y: float | None = Field(default=None, gt=0.0, le=1.0)
    feather: float | None = Field(default=None, ge=0.0, le=100.0)
    angle: float | None = Field(default=None, ge=-180.0, le=180.0)
    luminance_range: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def _shape_matches(self) -> Self:
        allowed = {"aiSelection": AI_SUBTYPES, "rangeMask": RANGE_SUBTYPES}.get(self.mask_type)
        if allowed is None:
            if self.subtype is not None:
                raise ValueError(f"{self.mask_type} takes no subtype")
        elif self.subtype not in allowed:
            raise ValueError(f"subtype for {self.mask_type} must be one of: {', '.join(allowed)}")
        needed = self.shape_fields()
        given = {name for name in SHAPE_FIELDS if getattr(self, name) is not None}
        missing = [name for name in needed if name not in given]
        extra = sorted(given - set(needed) - set(OPTIONAL_SHAPE_FIELDS.get(self.mask_type, ())))
        if missing:
            raise ValueError(f"{self.mask_type} {self.subtype or ''} needs: {', '.join(missing)}")
        if extra:
            raise ValueError(
                f"not used for {self.mask_type} {self.subtype or ''}: {', '.join(extra)}"
            )
        for name in ("start", "end", "center"):
            point = getattr(self, name)
            if point is not None and not all(0.0 <= value <= 1.0 for value in point):
                raise ValueError(f"{name} must be fractions between 0 and 1")
        if self.start is not None and self.start == self.end:
            raise ValueError("start and end must be different points")
        if self.luminance_range is not None:
            low = self.luminance_range
            if not all(0.0 <= v <= 1.0 for v in low) or list(low) != sorted(low):
                raise ValueError("luminance_range must be four values 0-1 in rising order")
        return self

    def shape_fields(self) -> tuple[str, ...]:
        """The fields this mask type needs; empty when Lightroom's own call makes the mask."""
        if self.mask_type == "gradient":
            return ("start", "end")
        if self.mask_type == "radialGradient":
            return ("center", "radius_x", "radius_y")
        if self.mask_type == "rangeMask" and self.subtype == "luminance":
            return ("luminance_range",)
        return ()

    @property
    def is_written(self) -> bool:
        return bool(self.shape_fields())


SHAPE_FIELDS = (
    "start",
    "end",
    "center",
    "radius_x",
    "radius_y",
    "feather",
    "angle",
    "luminance_range",
)
OPTIONAL_SHAPE_FIELDS = {"radialGradient": ("feather", "angle")}


class CreateMaskResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    creation: MaskCreation
    mask_id: str | None = None
    masks: tuple[MaskEntry, ...]
    stored: dict[str, Any] | None = None
    note: str = ""


def _new_id() -> str:
    return str(uuid.uuid4()).upper()


def _sync_id() -> str:
    return uuid.uuid4().hex.upper()


def _fmt(value: float) -> str:
    return f"{value:.6f}"


def build_correction(args: CreateMaskInput, number: int) -> dict[str, Any]:
    """One new mask in Lightroom's stored format (MaskGroupBasedCorrections entry)."""
    base: dict[str, Any] = {
        "MaskActive": True,
        "MaskBlendMode": 0,
        "MaskID": _new_id(),
        "MaskInverted": False,
        "MaskSyncID": _sync_id(),
        "MaskValue": 1,
    }
    reference = (0.5, 0.5)
    if args.mask_type == "gradient" and args.start and args.end:
        base.update(
            What="Mask/Gradient",
            MaskName=f"Linear Gradient {number}",
            FullX=args.start[0],
            FullY=args.start[1],
            ZeroX=args.end[0],
            ZeroY=args.end[1],
        )
        reference = ((args.start[0] + args.end[0]) / 2, (args.start[1] + args.end[1]) / 2)
    elif args.mask_type == "radialGradient" and args.center:
        assert args.radius_x is not None and args.radius_y is not None  # validated
        cx, cy = args.center
        base.update(
            What="Mask/CircularGradient",
            MaskName=f"Radial Gradient {number}",
            Left=cx - args.radius_x,
            Right=cx + args.radius_x,
            Top=cy - args.radius_y,
            Bottom=cy + args.radius_y,
            Angle=args.angle if args.angle is not None else 0,
            Feather=args.feather if args.feather is not None else 50,
            Flipped=True,
            Midpoint=50,
            Roundness=0,
            Version=2,
        )
        reference = (cx, cy)
    elif args.luminance_range is not None:
        base.update(
            What="Mask/RangeMask",
            MaskName=f"Luminance Range {number}",
            CorrectionRangeMask={
                "Invert": False,
                "LumRange": " ".join(_fmt(v) for v in args.luminance_range),
                "SampleType": 0,
                "Type": 2,
                "Version": 3,
            },
        )
    else:  # pragma: no cover - guarded by is_written
        raise ValueError("not a written mask type")
    correction: dict[str, Any] = {
        "CorrectionActive": True,
        "CorrectionAmount": 1,
        "CorrectionID": _new_id(),
        "CorrectionMasks": [base],
        "CorrectionName": f"Mask {number}",
        "CorrectionReferenceX": reference[0],
        "CorrectionReferenceY": reference[1],
        "CorrectionSyncID": _sync_id(),
        "What": "Correction",
    }
    correction.update(LOCAL_DEFAULTS)
    return correction


def _stored_masks(ctx: ToolContext, photo_id: str) -> list[Any]:
    value = ctx.lightroom.get_develop_settings(photo_id, [MASKS_KEY]).get(MASKS_KEY)
    return value if isinstance(value, list) else []


def _entries(masks: Any) -> list[dict[str, Any]]:
    """Lightroom's getAllMasks result as a list, whether it arrives as a list or a numbered map."""
    if isinstance(masks, dict):

        def order(key: Any) -> tuple[int, str]:
            text = str(key)
            return (int(text), "") if text.isdigit() else (10**9, text)

        masks = [masks[key] for key in sorted(masks, key=order)]
    return [item for item in masks if isinstance(item, dict)] if isinstance(masks, list) else []


def _entry_id(entry: dict[str, Any]) -> str | None:
    for key in ("ID", "maskId", "id"):
        if isinstance(entry.get(key), str):
            return str(entry[key])
    return None


def _create_written(
    ctx: ToolContext, args: CreateMaskInput, photo_id: str, created: bool
) -> Outcome:
    previous = _stored_masks(ctx, photo_id)
    correction = build_correction(args, len(previous) + 1)
    ctx.lightroom.apply_develop_settings(
        photo_id, {MASKS_KEY: [*previous, correction]}, "darkroom_ai: add mask"
    )
    waited = 0.0
    found: dict[str, Any] | None = None
    while True:
        stored = _stored_masks(ctx, photo_id)
        found = next(
            (
                item
                for item in stored
                if isinstance(item, dict) and item.get("CorrectionID") == correction["CorrectionID"]
            ),
            None,
        )
        if found is not None or waited >= READBACK_WAIT_S:
            break
        _sleep(READBACK_POLL_S)
        waited += READBACK_POLL_S
    if found is None:
        raise DarkroomError(
            ErrorCode.LR_COMMAND_FAILED,
            f"Lightroom accepted the write but the new {args.mask_type} mask is not in the "
            f"photo's settings after {waited:g} s ({len(stored)} masks there, expected "
            f"{len(previous) + 1}). Nothing else was changed.",
            next_step="Look at the photo in Lightroom's Develop module; if the mask is not there, "
            "check the state with inspect_masks before trying again.",
            details={"mask_type": args.mask_type, "masks_before": len(previous)},
        )
    parts = found.get("CorrectionMasks")
    stored_part = parts[0] if isinstance(parts, list) and parts else None
    mask_id: str | None = None
    note = ""
    try:
        seen, _ = run_with_takeover(
            ctx, photo_id, lambda: ctx.lightroom.inspect_masks(photo_id), what="inspect masks"
        )
        entries = _entries(seen.masks)
        if len(entries) == len(stored) and len(entries) > len(previous):
            mask_id = _entry_id(entries[len(previous)])
    except DarkroomError as error:
        note = f" Lightroom could not be asked for the mask id ({error.code}); use inspect_masks."
    result = CreateMaskResult(
        photo_id=photo_id,
        working_copy_created=created,
        creation=MaskCreation(
            created=True,
            masks_before=len(previous),
            masks_after=len(stored),
            waited_s=waited,
            mask_id=mask_id,
        ),
        masks=summarize_masks(stored),
        mask_id=mask_id,
        stored=stored_part if isinstance(stored_part, dict) else None,
        note="Mask written and read back from the photo's settings. It has no adjustments yet: "
        "use set_mask_adjustments with this mask_id (call inspect_masks if mask_id is empty)."
        + note,
    )
    return Outcome(
        result,
        applied={"photo_id": photo_id, "mask_type": args.mask_type, "subtype": args.subtype},
        undo={"photo_id": photo_id, "settings": {MASKS_KEY: previous}},
    )


def _finish_people(
    ctx: ToolContext, photo_id: str, previous_count: int, creation: MaskCreation
) -> MaskCreation:
    """Lightroom shows its person list and waits; ask the person to finish it, then re-count."""
    confirmed = ctx.confirmer.confirm(
        "finish the people mask in Lightroom: choose the person, tick the parts you want and "
        "press 'Create Mask' in Lightroom's Masking panel, then answer here",
        {"photo_id": photo_id},
    )
    if not confirmed:
        return creation
    waited = 0.0
    while True:
        count = len(_stored_masks(ctx, photo_id))
        if count > previous_count or waited >= PEOPLE_WAIT_S:
            break
        _sleep(READBACK_POLL_S)
        waited += READBACK_POLL_S
    return MaskCreation(
        created=count > previous_count,
        masks_before=previous_count,
        masks_after=count,
        waited_s=creation.waited_s + waited,
        mask_id=None,
    )


def create_mask(ctx: ToolContext, args: CreateMaskInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    if args.is_written:
        return _create_written(ctx, args, copy.id, created)
    before = ctx.lightroom.get_develop_settings(copy.id, [MASKS_KEY]).get(MASKS_KEY)
    creation, waited = run_with_takeover(
        ctx,
        copy.id,
        lambda: ctx.lightroom.create_mask(copy.id, args.mask_type, args.subtype),
        what="create mask",
    )
    handed_over = False
    if not creation.created and args.subtype == "people":
        creation = _finish_people(
            ctx, copy.id, len(before) if isinstance(before, list) else 0, creation
        )
        handed_over = True
    if not creation.created:
        if args.subtype in ("color", "depth"):
            next_step = (
                "Colour and depth range masks cannot be created automatically yet: Lightroom's "
                "call only arms the tool and waits for a person to click a sample. Use a "
                "luminance range, a gradient or an AI mask instead."
            )
        elif args.subtype == "people":
            next_step = (
                "Lightroom needs the person chosen and 'Create Mask' pressed in its Masking "
                "panel. Do that, then call inspect_masks."
            )
        else:
            next_step = (
                "Look at the photo in Lightroom's Develop module (masking may need the photo to "
                "finish loading, or the photo may have nothing this selection can find)."
            )
        raise DarkroomError(
            ErrorCode.LR_COMMAND_FAILED,
            f"No {args.mask_type} {args.subtype or ''} mask appeared: the mask count stayed at "
            f"{creation.masks_before} after {creation.waited_s:g} s. The cause is not known; "
            "this does not prove the mask type is unavailable.".replace("  ", " "),
            next_step=next_step,
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
        "with this mask_id (call inspect_masks if mask_id is empty)."
        + (" The person finished it in Lightroom." if handed_over else "")
        + takeover_note(waited),
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
    seen, _waited = run_with_takeover(
        ctx, copy.id, lambda: ctx.lightroom.inspect_masks(copy.id), what="inspect masks"
    )
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
    got, waited = run_with_takeover(
        ctx,
        copy.id,
        lambda: ctx.lightroom.set_mask_values(copy.id, args.mask_id, values),
        what="adjust mask",
    )
    applied = {key: round(got.after[key] - got.before[key], 4) for key in got.after}
    result = SetMaskAdjustmentsResult(
        photo_id=copy.id,
        mask=got,
        requested_change=dict(args.changes),
        applied_change=applied,
        note="Values are inside the mask only. Use get_preview to look at the result."
        + takeover_note(waited),
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
    got, _waited = run_with_takeover(
        ctx,
        copy.id,
        lambda: ctx.lightroom.mask_operation(copy.id, args.mask_id, operation),
        what=f"{operation} mask",
    )
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
