"""Lightroom tools: info, analysis, lighting, white balance, HSL, crop, presets, undo.

All edits go to a virtual copy (created on first use), are clamped to the configured limits, and
are recorded with the values needed to undo them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from darkroom_ai.adapters.lightroom.models import PhotoInfo
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.analysis import ImageAnalysis, analyze
from darkroom_ai.imaging.faces import FaceAnalysis, analyze_faces
from darkroom_ai.imaging.io import load_image
from darkroom_ai.safety.journal import JournalEntry
from darkroom_ai.tools.context import (
    MEASURED_SOURCE,
    EditResult,
    Outcome,
    ToolContext,
    ToolResult,
    apply_slider_changes,
    check_process_version,
    measure,
    measured_summary,
    measurement_before,
    original_values,
    resolve_copy,
    working_copy,
)
from darkroom_ai.tools.develop_keys import (
    CROP_KEYS,
    HSL_PREFIX,
    RAW_FORMATS,
    HslColor,
    capped_keys,
    hsl_limit,
    lighting_limits,
    white_balance_limits,
)

PREVIEW_LONG_EDGE = 1024


WHITE_BALANCE_MODE_KEY = "WhiteBalance"
CUSTOM_WHITE_BALANCE = "Custom"


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1, description="Catalog id of the photo or its working copy.")


# -- get_photo_info -------------------------------------------------------------------------------


class GetPhotoInfoInput(_Input):
    """Read facts about a photo and its current basic settings. Changes nothing."""


class PhotoInfoResult(ToolResult):
    photo: PhotoInfo
    working_copy_id: str | None
    settings: dict[str, Any]
    process_version: str


def get_photo_info(ctx: ToolContext, args: GetPhotoInfoInput) -> Outcome:
    photo = ctx.lightroom.get_photo(args.photo_id)
    copy = resolve_copy(ctx, args.photo_id)
    limits = {
        **lighting_limits(ctx.settings.guardrails),
        **white_balance_limits(ctx.settings.guardrails, photo.file_format),
    }
    keys = [limit.key for limit in limits.values()]
    raw = ctx.lightroom.get_develop_settings(copy.id, [*keys, "ProcessVersion"])
    settings = {name: raw.get(limit.key) for name, limit in limits.items()}
    result = PhotoInfoResult(
        photo=photo,
        working_copy_id=copy.id if copy.is_virtual_copy else None,
        settings=settings,
        process_version=str(raw.get("ProcessVersion", "")),
    )
    return Outcome(result)


# -- get_preview ----------------------------------------------------------------------------------


class GetPreviewInput(_Input):
    """Render the photo as Lightroom shows it right now and return the picture itself, so you can
    look at it. Use it to judge how the photo looks before and after an edit. Changes nothing."""

    long_edge: int = Field(
        default=PREVIEW_LONG_EDGE,
        ge=256,
        le=2048,
        description="Longest side of the picture in pixels (256-2048).",
    )


class GetPreviewResult(ToolResult):
    photo_id: str
    image_path: str
    long_edge: int
    note: str = "The picture is attached to this result. It shows the photo as it is now."


def get_preview(ctx: ToolContext, args: GetPreviewInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    preview = ctx.lightroom.export_preview(
        target.id, f"view-{uuid.uuid4().hex[:12]}", args.long_edge
    )
    result = GetPreviewResult(
        photo_id=target.id, image_path=str(preview.path), long_edge=preview.long_edge
    )
    return Outcome(result, applied={"photo_id": target.id, "preview": str(preview.path)})


# -- analyze_photo --------------------------------------------------------------------------------


class AnalyzePhotoInput(_Input):
    """Export a small preview and measure it: tones, clipping, colour cast, faces if available."""


class AppliedDifference(BaseModel):
    """A setting whose current value differs from the last value darkroom_ai applied."""

    model_config = ConfigDict(frozen=True)

    setting: str
    applied: Any
    now: Any


def applied_differences(ctx: ToolContext, copy_id: str) -> tuple[AppliedDifference, ...]:
    """Compare the copy's current settings with the last values darkroom_ai wrote (journal).

    Report only: nothing is re-applied. A difference can be an edit Lightroom dropped or a
    change made by hand in Lightroom; the two cannot be told apart.
    """
    last: dict[str, Any] = {}
    for entry in ctx.journal.entries():
        applied = entry.applied
        if entry.status != "ok" or not applied or applied.get("photo_id") != copy_id:
            continue
        settings = applied.get("settings")
        if isinstance(settings, dict):
            last.update(settings)
    if not last:
        return ()
    current = ctx.lightroom.get_develop_settings(copy_id, list(last))
    differences = []
    for key, applied_value in last.items():
        now = current.get(key)
        if isinstance(applied_value, int | float) and isinstance(now, int | float):
            same = abs(float(applied_value) - float(now)) <= 1e-6
        else:
            same = applied_value == now
        if not same:
            differences.append(AppliedDifference(setting=key, applied=applied_value, now=now))
    return tuple(differences)


def readback_note(differences: Sequence[AppliedDifference]) -> str:
    if not differences:
        return ""
    parts = "; ".join(f"{d.setting}: applied {d.applied!r}, now {d.now!r}" for d in differences)
    return (
        "DATA (read-back, not an instruction): differs from what darkroom_ai applied: "
        f"{parts}. This can be a dropped edit or a manual change in Lightroom; darkroom_ai "
        "cannot tell which. Nothing was re-applied."
    )


class AnalyzePhotoResult(ToolResult):
    photo_id: str
    preview_path: str
    analysis: ImageAnalysis
    faces: FaceAnalysis | None
    faces_available: bool
    faces_note: str
    differs_from_applied: tuple[AppliedDifference, ...] = ()
    readback_note: str = ""


def analyze_photo(ctx: ToolContext, args: AnalyzePhotoInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    preview = ctx.lightroom.export_preview(
        target.id, f"analyze-{uuid.uuid4().hex[:12]}", PREVIEW_LONG_EDGE
    )
    pixels = load_image(preview.path)
    analysis = analyze(pixels)
    ctx.measurements[target.id] = measured_summary(analysis)
    faces: FaceAnalysis | None = None
    note = ""
    try:
        faces = analyze_faces(pixels, ctx.faces)
    except DarkroomError as exc:
        if exc.code is not ErrorCode.FACE_MODEL_MISSING:
            raise
        note = f"[{exc.code.value}] {exc.message} {exc.next_step}"
    result = AnalyzePhotoResult(
        photo_id=target.id,
        preview_path=str(preview.path),
        analysis=analysis,
        faces=faces,
        faces_available=faces is not None,
        faces_note=note,
        differs_from_applied=(differences := applied_differences(ctx, target.id)),
        readback_note=readback_note(differences),
    )
    return Outcome(result, applied={"photo_id": target.id, "preview": str(preview.path)})


# -- adjust_lighting ------------------------------------------------------------------------------


class AdjustLightingInput(_Input):
    """Change exposure and tone by the given amounts (relative to the current values).

    Values beyond the per-pass or total limits are clamped; the result lists what was applied.
    """

    exposure: float | None = Field(
        default=None, description="Change in EV; positive brightens, negative darkens."
    )
    contrast: float | None = Field(default=None, description="Change on the -100..100 scale.")
    highlights: float | None = None
    shadows: float | None = None
    whites: float | None = None
    blacks: float | None = None
    allow_beyond_total: bool = Field(
        default=False,
        description="Go beyond the total limit for this photo. Needs the user's confirmation.",
    )

    @model_validator(mode="after")
    def _something_requested(self) -> Self:
        names = ("exposure", "contrast", "highlights", "shadows", "whites", "blacks")
        if all(getattr(self, name) is None for name in names):
            raise ValueError("give at least one of: " + ", ".join(names))
        return self


def adjust_lighting(ctx: ToolContext, args: AdjustLightingInput) -> Outcome:
    limits = lighting_limits(ctx.settings.guardrails)
    requests = [
        (limit, float(getattr(args, name)))
        for name, limit in limits.items()
        if getattr(args, name) is not None
    ]
    return apply_slider_changes(
        ctx,
        args.photo_id,
        requests,
        history_name="darkroom_ai: lighting",
        allow_beyond_total=args.allow_beyond_total,
    )


# -- adjust_white_balance -------------------------------------------------------------------------


class AdjustWhiteBalanceInput(_Input):
    """Change white balance by the given amounts.

    JPG/TIFF: both values are on Lightroom's relative -100..100 scale (positive temperature =
    warmer, positive tint = more magenta). RAW: temperature is a change in Kelvin. Values beyond
    the per-pass or total limits are clamped; the result lists what was applied.
    """

    temperature: float | None = None
    tint: float | None = None
    allow_beyond_total: bool = Field(
        default=False,
        description="Go beyond the total limit for this photo. Needs the user's confirmation.",
    )

    @model_validator(mode="after")
    def _something_requested(self) -> Self:
        if self.temperature is None and self.tint is None:
            raise ValueError("give temperature, tint or both")
        return self


def adjust_white_balance(ctx: ToolContext, args: AdjustWhiteBalanceInput) -> Outcome:
    photo = ctx.lightroom.get_photo(args.photo_id)
    limits = white_balance_limits(ctx.settings.guardrails, photo.file_format)
    requests = [
        (limits[name], float(getattr(args, name)))
        for name in ("temperature", "tint")
        if getattr(args, name) is not None
    ]
    # JPG / TIFF: Lightroom drops the relative white-balance values at the next export while
    # the mode is "As Shot" (observed 2026-11-21), so the mode is written with them. RAW Kelvin
    # values are not affected and are written alone.
    is_raw = photo.file_format.upper() in RAW_FORMATS
    return apply_slider_changes(
        ctx,
        args.photo_id,
        requests,
        history_name="darkroom_ai: white balance",
        allow_beyond_total=args.allow_beyond_total,
        written_with=None if is_raw else {WHITE_BALANCE_MODE_KEY: CUSTOM_WHITE_BALANCE},
    )


# -- adjust_hsl -----------------------------------------------------------------------------------


class HslAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: HslColor
    hue: float | None = None
    saturation: float | None = None
    luminance: float | None = None

    @model_validator(mode="after")
    def _something_requested(self) -> Self:
        if self.hue is None and self.saturation is None and self.luminance is None:
            raise ValueError("give hue, saturation or luminance")
        return self


class AdjustHslInput(_Input):
    """Change hue, saturation or luminance of single colours by the given amounts."""

    adjustments: list[HslAdjustment] = Field(min_length=1, max_length=8)


def adjust_hsl(ctx: ToolContext, args: AdjustHslInput) -> Outcome:
    requests = []
    seen: set[str] = set()
    for adjustment in args.adjustments:
        for channel in HSL_PREFIX:
            value = getattr(adjustment, channel)
            if value is None:
                continue
            limit = hsl_limit(ctx.settings.guardrails, channel, adjustment.color)
            if limit.key in seen:
                raise DarkroomError(
                    ErrorCode.GUARDRAIL_REJECTED,
                    f"{channel} of {adjustment.color} is given more than once.",
                    next_step="List each colour and channel only once.",
                )
            seen.add(limit.key)
            requests.append((limit, float(value)))
    return apply_slider_changes(ctx, args.photo_id, requests, history_name="darkroom_ai: HSL")


# -- crop -----------------------------------------------------------------------------------------


class CropInput(_Input):
    """Set the crop box as fractions of the frame (0 = left/top edge, 1 = right/bottom edge)."""

    left: float = Field(ge=0.0, le=1.0)
    top: float = Field(ge=0.0, le=1.0)
    right: float = Field(ge=0.0, le=1.0)
    bottom: float = Field(ge=0.0, le=1.0)
    angle: float = Field(default=0.0, ge=-45.0, le=45.0, description="Rotation in degrees.")

    @model_validator(mode="after")
    def _box_is_valid(self) -> Self:
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("right must be greater than left and bottom greater than top")
        return self


class CropResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    before: dict[str, Any]
    after: dict[str, Any]
    kept_fraction: float
    # Measured on real preview renders before and after the crop.
    measured: dict[str, Any] | None = None
    # True when the photo already had exactly these values: nothing was written.
    no_change: bool = False
    note: str = ""


NO_CHANGE_NOTE = "No change: the photo already has these values. Nothing was written."


def _same_number(current: Any, wanted: float) -> bool:
    return isinstance(current, int | float) and abs(float(current) - float(wanted)) <= 1e-6


def crop(ctx: ToolContext, args: CropInput) -> Outcome:
    kept = (args.right - args.left) * (args.bottom - args.top)
    minimum = ctx.settings.guardrails.min_crop_fraction
    if kept < minimum:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"This crop keeps {kept:.0%} of the frame; the minimum is {minimum:.0%}. "
            "Nothing was changed.",
            details={"kept_fraction": round(kept, 4), "minimum": minimum},
        )
    copy, created = working_copy(ctx, args.photo_id)
    before = ctx.lightroom.get_develop_settings(copy.id, list(CROP_KEYS))
    wanted = {
        "CropLeft": args.left,
        "CropTop": args.top,
        "CropRight": args.right,
        "CropBottom": args.bottom,
        "CropAngle": args.angle,
    }
    if all(_same_number(before.get(key), value) for key, value in wanted.items()):
        # An absolute setting that is already in place: information, not a refusal.
        current = {key: before.get(key) for key in CROP_KEYS}
        return Outcome(
            CropResult(
                photo_id=copy.id,
                working_copy_created=created,
                before=current,
                after=current,
                kept_fraction=round(kept, 4),
                no_change=True,
                note=NO_CHANGE_NOTE,
            )
        )
    before_measured = measurement_before(ctx, copy.id, args.photo_id, created)
    change = ctx.lightroom.apply_develop_settings(copy.id, wanted, "darkroom_ai: crop")
    measured = {
        "before": before_measured,
        "after": measure(ctx, copy.id),
        "source": MEASURED_SOURCE,
    }
    result = CropResult(
        photo_id=copy.id,
        working_copy_created=created,
        before=change.before,
        after=change.after,
        kept_fraction=round(kept, 4),
        measured=measured,
    )
    return Outcome(
        result,
        applied={"photo_id": copy.id, "settings": wanted},
        undo={"photo_id": copy.id, "settings": {k: before.get(k) for k in CROP_KEYS}},
    )


# -- apply_preset ---------------------------------------------------------------------------------


class ListPresetsInput(BaseModel):
    """List Lightroom's develop presets (name and folder). Reads only; changes nothing."""

    model_config = ConfigDict(extra="forbid")

    contains: str | None = Field(
        default=None, description="Only presets whose name or folder contains this text."
    )
    limit: int = Field(default=100, ge=1, le=500, description="Most presets to return.")


class PresetEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    folder: str


class ListPresetsResult(ToolResult):
    total: int
    matching: int
    returned: int
    presets: tuple[PresetEntry, ...]
    note: str = ""


def list_presets(ctx: ToolContext, args: ListPresetsInput) -> Outcome:
    presets = ctx.lightroom.list_presets()
    wanted = (args.contains or "").strip().lower()
    matching = [
        p for p in presets if not wanted or wanted in p.name.lower() or wanted in p.folder.lower()
    ]
    matching.sort(key=lambda p: (p.folder.lower(), p.name.lower()))
    shown = matching[: args.limit]
    note = ""
    if len(shown) < len(matching):
        note = f"{len(matching) - len(shown)} more match; narrow the search with `contains`."
    result = ListPresetsResult(
        total=len(presets),
        matching=len(matching),
        returned=len(shown),
        presets=tuple(PresetEntry(name=p.name, folder=p.folder) for p in shown),
        note=note,
    )
    return Outcome(result)


class ApplyPresetInput(_Input):
    """Apply one of Lightroom's develop presets by its exact name."""

    preset_name: str = Field(min_length=1)


class ApplyPresetResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    preset: str
    changed: dict[str, Any]
    changed_count: int


def apply_preset(ctx: ToolContext, args: ApplyPresetInput) -> Outcome:
    wanted = args.preset_name.strip().lower()
    presets = ctx.lightroom.list_presets()
    matches = [p for p in presets if p.name.strip().lower() == wanted]
    if not matches:
        similar = sorted(p.name for p in presets if wanted in p.name.lower())[:8]
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"There is no develop preset named '{args.preset_name}'. Nothing was changed.",
            next_step="Use the exact preset name as shown in Lightroom."
            + (f" Similar names: {', '.join(similar)}." if similar else ""),
            details={"similar": similar},
        )
    if len(matches) > 1:
        folders = sorted(p.folder for p in matches)
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"Several presets are named '{args.preset_name}' (in: {', '.join(folders)}). "
            "Nothing was changed.",
            details={"folders": folders},
        )

    copy, created = working_copy(ctx, args.photo_id)
    change = ctx.lightroom.apply_preset(copy.id, matches[0].uuid)

    # A preset can set anything, so check its result against the total limits and take it back
    # if it goes beyond them.
    caps = capped_keys(ctx.settings.guardrails)
    touched = {key: change.before.get(key) for key in caps if key in change.after}
    originals = original_values(ctx, copy.id, touched)
    for key, limit in caps.items():
        if key not in change.after or limit.total is None:
            continue
        after = float(change.after[key])
        original = float(originals.get(key) or 0.0)
        if abs(after - original) > limit.total + 1e-6:
            ctx.lightroom.apply_develop_settings(
                copy.id, change.before, "darkroom_ai: preset taken back"
            )
            raise DarkroomError(
                ErrorCode.GUARDRAIL_REJECTED,
                f"The preset '{matches[0].name}' would set {limit.name} to {after:+g}, which is "
                f"more than {limit.total:g} away from the original ({original:+g}). The preset "
                "was taken back; nothing is changed.",
                details={"setting": key, "after": after, "original": original},
            )
    result = ApplyPresetResult(
        photo_id=copy.id,
        working_copy_created=created,
        preset=matches[0].name,
        changed=change.after,
        changed_count=len(change.after),
    )
    return Outcome(
        result,
        applied={"photo_id": copy.id, "preset": matches[0].name, "settings": change.after},
        undo={"photo_id": copy.id, "settings": change.before} if change.before else None,
    )


# -- undo -----------------------------------------------------------------------------------------


class UndoInput(_Input):
    """Take back darkroom_ai's edits made in THIS session: the last one, or all of them.

    Edits from earlier sessions cannot be taken back with this tool.
    """

    scope: Literal["last", "all"] = "last"


class UndoResult(ToolResult):
    photo_id: str
    undone_steps: int
    restored: dict[str, Any]
    note: str = ""


UNDO_RECORDS = ("undo", "revert")  # journal entries that list edits they took back
NOTHING_TO_UNDO = (
    "There is nothing to undo in this session. Edits from earlier sessions can only be taken "
    "back by the user, with the command `darkroom revert`."
)


def pending_edits(ctx: ToolContext, copy_id: str, *, session: str | None) -> list[JournalEntry]:
    """Edits on this copy that have not been taken back yet, oldest first.

    `session` limits the list to one session's edits; None means every session.
    """
    entries = list(ctx.journal.entries())
    already = {
        str(entry_id)
        for entry in entries
        if entry.tool in UNDO_RECORDS and entry.status == "ok" and entry.applied
        for entry_id in entry.applied.get("undone", [])
    }
    return [
        entry
        for entry in entries
        if entry.undoable
        and entry.undo is not None
        and entry.undo.get("photo_id") == copy_id
        and entry.undo.get("settings")
        and entry.id not in already
        and (session is None or entry.session == session)
    ]


def restore_values(entries: Sequence[JournalEntry]) -> dict[str, Any]:
    """The values to put back to take `entries` back: each setting's oldest "before" value."""
    restore: dict[str, Any] = {}
    for entry in reversed(entries):
        assert entry.undo is not None
        restore.update(entry.undo["settings"])
    return restore


def undo(ctx: ToolContext, args: UndoInput) -> Outcome:
    copy = resolve_copy(ctx, args.photo_id)
    pending = pending_edits(ctx, copy.id, session=ctx.journal.session)
    if not pending:
        result = UndoResult(photo_id=copy.id, undone_steps=0, restored={}, note=NOTHING_TO_UNDO)
        return Outcome(result)

    chosen = pending if args.scope == "all" else pending[-1:]
    restore = restore_values(chosen)
    check_process_version(ctx.lightroom.get_develop_settings(copy.id, ["ProcessVersion"]))
    ctx.lightroom.apply_develop_settings(copy.id, restore, "darkroom_ai: undo")
    ctx.measurements.pop(copy.id, None)  # the last measurement no longer describes the photo
    result = UndoResult(photo_id=copy.id, undone_steps=len(chosen), restored=restore)
    return Outcome(
        result,
        applied={"photo_id": copy.id, "undone": [e.id for e in chosen], "settings": restore},
    )


__all__ = [
    "AdjustHslInput",
    "AdjustLightingInput",
    "AdjustWhiteBalanceInput",
    "AnalyzePhotoInput",
    "ApplyPresetInput",
    "CropInput",
    "EditResult",
    "GetPhotoInfoInput",
    "ListPresetsInput",
    "UndoInput",
    "adjust_hsl",
    "adjust_lighting",
    "adjust_white_balance",
    "analyze_photo",
    "applied_differences",
    "apply_preset",
    "crop",
    "get_photo_info",
    "list_presets",
    "pending_edits",
    "restore_values",
    "undo",
]
