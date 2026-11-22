"""Lightroom tools: info, analysis, lighting, white balance, HSL, crop, presets, undo.

All edits go to a virtual copy (created on first use), are clamped to the configured limits, and
are recorded with the values needed to undo them.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from darkroom_ai.adapters.lightroom.models import CatalogPage, PhotoInfo, PhotoMetadata
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
from darkroom_ai.tools.develop_parameters import (
    DEVELOP_PARAMETERS,
    OWN_TOOLS,
    READ_ONLY_TEXT,
    parameter_limit,
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


# -- get_selected_photos / find_photo ----------------------------------------------------------


class GetSelectedPhotosInput(BaseModel):
    """List the photo(s) selected in Lightroom Classic, with their catalog ids. Reads only;
    changes nothing. Use the ids with the other tools."""

    model_config = ConfigDict(extra="forbid")


class SelectedPhotosResult(ToolResult):
    active: PhotoInfo | None
    photos: tuple[PhotoInfo, ...]
    note: str = ""


def get_selected_photos(ctx: ToolContext, args: GetSelectedPhotosInput) -> Outcome:
    selection = ctx.lightroom.get_selection()
    note = "" if selection.active else "No photo is selected in Lightroom. Select one and retry."
    result = SelectedPhotosResult(active=selection.active, photos=selection.photos, note=note)
    return Outcome(result)


class FindPhotoInput(BaseModel):
    """Find a photo in the open catalog by its file path and return its catalog id. Reads only."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="Absolute path of the photo file.")


class FindPhotoResult(ToolResult):
    found: bool
    photo: PhotoInfo | None = None
    note: str = ""


def find_photo(ctx: ToolContext, args: FindPhotoInput) -> Outcome:
    photo = ctx.lightroom.find_photo(args.path)
    note = "" if photo else "That file is not in the open catalog (import it into Lightroom first)."
    return Outcome(FindPhotoResult(found=photo is not None, photo=photo, note=note))


# -- get_photo_metadata / list_catalog_photos -----------------------------------------------------


class GetPhotoMetadataInput(_Input):
    """Read the camera, lens and exposure facts of a photo (make, model, lens, focal length,
    aperture, shutter speed, ISO, capture time, size, file type). Facts only; changes nothing."""


class PhotoMetadataResult(ToolResult):
    metadata: PhotoMetadata
    note: str = "Values are as recorded in the file's metadata; a missing value is null."


def get_photo_metadata(ctx: ToolContext, args: GetPhotoMetadataInput) -> Outcome:
    return Outcome(PhotoMetadataResult(metadata=ctx.lightroom.get_metadata(args.photo_id)))


DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class ListCatalogPhotosInput(BaseModel):
    """List photos in the open Lightroom catalog, one page at a time, with their ids and
    camera/lens/exposure facts. Reads only; changes nothing."""

    model_config = ConfigDict(extra="forbid")

    offset: int = Field(default=0, ge=0, description="How many photos to skip.")
    limit: int = Field(default=25, ge=1, le=100, description="Most photos to return (1-100).")
    path_contains: str | None = Field(
        default=None, description="Only photos whose file path contains this text (e.g. a folder)."
    )
    include_virtual_copies: bool = Field(
        default=False, description="Also list virtual copies (normally only the originals)."
    )
    min_rating: int | None = Field(
        default=None,
        ge=0,
        le=5,
        description="Only photos rated at least this (unrated counts as 0).",
    )
    max_rating: int | None = Field(
        default=None,
        ge=0,
        le=5,
        description="Only photos rated at most this (0 finds the unrated).",
    )
    flag: Literal["pick", "reject", "none"] | None = Field(
        default=None, description="Only photos with this flag."
    )
    keyword: str | None = Field(
        default=None,
        min_length=1,
        description="Only photos that have this keyword (any letter case).",
    )

    file_format: str | None = Field(
        default=None,
        min_length=1,
        description="Only photos of this file format: RAW, DNG, JPG, TIFF, PSD or VIDEO.",
    )
    min_iso: int | None = Field(
        default=None, ge=0, description="Only photos at this ISO or higher."
    )
    max_iso: int | None = Field(default=None, ge=0, description="Only photos at this ISO or lower.")
    captured_from: str | None = Field(
        default=None,
        pattern=DATE_PATTERN,
        description="Only photos captured on or after this date, written YYYY-MM-DD.",
    )
    captured_to: str | None = Field(
        default=None,
        pattern=DATE_PATTERN,
        description="Only photos captured on or before this date, written YYYY-MM-DD.",
    )

    @model_validator(mode="after")
    def _ranges_in_order(self) -> Self:
        if (
            self.min_rating is not None
            and self.max_rating is not None
            and self.min_rating > self.max_rating
        ):
            raise ValueError("min_rating must not be greater than max_rating")
        if self.min_iso is not None and self.max_iso is not None and self.min_iso > self.max_iso:
            raise ValueError("min_iso must not be greater than max_iso")
        if self.captured_from and self.captured_to and self.captured_from > self.captured_to:
            raise ValueError("captured_from must not be later than captured_to")
        for name in ("captured_from", "captured_to"):
            value = getattr(self, name)
            if value is not None:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{name} is not a real date: {value}") from exc
        return self


class ListCatalogPhotosResult(ToolResult):
    page: CatalogPage
    note: str = ""


def list_catalog_photos(ctx: ToolContext, args: ListCatalogPhotosInput) -> Outcome:
    page = ctx.lightroom.list_catalog(
        args.offset,
        args.limit,
        include_virtual_copies=args.include_virtual_copies,
        path_contains=args.path_contains,
        min_rating=args.min_rating,
        max_rating=args.max_rating,
        flag=args.flag,
        keyword=args.keyword,
        file_format=args.file_format.upper() if args.file_format else None,
        min_iso=args.min_iso,
        max_iso=args.max_iso,
        captured_from=args.captured_from,
        captured_to=args.captured_to,
    )
    more = page.offset + page.returned < page.total
    note = (
        f"{page.total - page.offset - page.returned} more; call again with offset "
        f"{page.offset + page.returned}."
        if more
        else ""
    )
    return Outcome(ListCatalogPhotosResult(page=page, note=note))


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
        note = f"[{exc.code.value}] {exc.message} {exc.next_step}"  # informational, not a failure
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


# -- analyze_batch --------------------------------------------------------------------------------

BATCH_LONG_EDGE = 512
BATCH_MAX_PHOTOS = 25
BATCH_MAX_CONSECUTIVE_ERRORS = 3
_BATCH_STOP_CODES = frozenset({ErrorCode.LR_NOT_REACHABLE, ErrorCode.ADOBE_VERSION_UNSUPPORTED})


class AnalyzeBatchInput(BaseModel):
    """Measure many photos in one call and return a few numbers per photo (tones, clipping,
    colour cast). Numbers only: no pictures, no faces, no verdicts. Use it to compare or group a
    large set cheaply before looking at individual photos. Reads only; changes nothing."""

    model_config = ConfigDict(extra="forbid")

    photo_ids: list[str] = Field(
        min_length=1,
        max_length=BATCH_MAX_PHOTOS,
        description=f"Catalog ids to measure (1-{BATCH_MAX_PHOTOS}); repeats are ignored.",
    )
    long_edge: int = Field(
        default=BATCH_LONG_EDGE,
        ge=256,
        le=1024,
        description="Longest side of the measured preview in pixels (256-1024). Smaller is faster.",
    )
    time_budget_s: float = Field(
        default=90.0,
        ge=1.0,
        le=300.0,
        description="Stop starting new photos after this many seconds; the rest are listed in "
        "not_processed so you can call again with them.",
    )


class BatchPhotoNumbers(BaseModel):
    """The numbers measured for one photo, or why it could not be measured."""

    model_config = ConfigDict(frozen=True)

    photo_id: str
    measured_id: str | None = None
    file_name: str | None = None
    is_working_copy: bool | None = None
    median: float | None = None
    p05: float | None = None
    p95: float | None = None
    highlights_clipped_pct: float | None = None
    shadows_clipped_pct: float | None = None
    red_high_pct: float | None = None
    color_cast: str | None = None
    color_cast_strength: float | None = None
    error: str | None = None


class AnalyzeBatchResult(ToolResult):
    results: tuple[BatchPhotoNumbers, ...]
    not_processed: tuple[str, ...] = ()
    long_edge: int
    elapsed_s: float = 0.0
    note: str = ""


def _batch_numbers(ctx: ToolContext, photo_id: str, long_edge: int) -> BatchPhotoNumbers:
    target = resolve_copy(ctx, photo_id)
    preview = ctx.lightroom.export_preview(target.id, f"batch-{uuid.uuid4().hex[:12]}", long_edge)
    analysis = analyze(load_image(preview.path))
    return BatchPhotoNumbers(
        photo_id=photo_id,
        measured_id=target.id,
        file_name=target.file_name,
        is_working_copy=target.is_virtual_copy,
        median=analysis.tones.p50,
        p05=analysis.tones.p05,
        p95=analysis.tones.p95,
        highlights_clipped_pct=analysis.clipping.highlights_pct,
        shadows_clipped_pct=analysis.clipping.shadows_pct,
        red_high_pct=analysis.clipping.red_high_pct,
        color_cast=analysis.color_cast.description,
        color_cast_strength=analysis.color_cast.strength,
    )


def analyze_batch(ctx: ToolContext, args: AnalyzeBatchInput) -> Outcome:
    ids = list(dict.fromkeys(args.photo_ids))
    began = time.monotonic()
    results: list[BatchPhotoNumbers] = []
    errors_in_a_row = 0
    stopped = ""
    done = 0
    for photo_id in ids:
        if time.monotonic() - began >= args.time_budget_s:
            stopped = f"the {args.time_budget_s:g} s time budget ran out"
            break
        if errors_in_a_row >= BATCH_MAX_CONSECUTIVE_ERRORS:
            stopped = f"{BATCH_MAX_CONSECUTIVE_ERRORS} photos in a row failed"
            break
        try:
            results.append(_batch_numbers(ctx, photo_id, args.long_edge))
            errors_in_a_row = 0
        except DarkroomError as exc:
            if exc.code in _BATCH_STOP_CODES:
                raise
            errors_in_a_row += 1
            results.append(
                BatchPhotoNumbers(photo_id=photo_id, error=f"[{exc.code.value}] {exc.message}")
            )
        done += 1
    rest = tuple(ids[done:])
    note = (
        "Numbers only, from a small preview of each photo as it is now; "
        "they are evidence, not verdicts. Clipping shares depend on the preview size, so compare "
        "photos measured at the same long_edge."
    )
    if rest:
        note += f" Stopped early: {stopped}. Call again with not_processed."
    result = AnalyzeBatchResult(
        results=tuple(results),
        not_processed=rest,
        long_edge=args.long_edge,
        elapsed_s=round(time.monotonic() - began, 2),
        note=note,
    )
    return Outcome(result, applied={"photos": len(results), "not_processed": len(rest)})


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


# -- list / get / set develop parameters ---------------------------------------------------------


class ListDevelopParametersInput(BaseModel):
    """List the Lightroom develop parameters you can read and set with get_develop_parameters and
    set_develop_parameters (name, panel, allowed range). Changes nothing."""

    model_config = ConfigDict(extra="forbid")

    panel: str | None = Field(
        default=None,
        description="Panel: basic, detail, lens, geometry, effects, color_grading, calibration.",
    )


class ParameterEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    panel: str
    minimum: float
    maximum: float
    note: str = ""


class ListDevelopParametersResult(ToolResult):
    parameters: tuple[ParameterEntry, ...]
    set_with_other_tools: str = OWN_TOOLS
    readable_text_settings: dict[str, str] = Field(default_factory=lambda: dict(READ_ONLY_TEXT))
    note: str = (
        "set_develop_parameters changes a value by an amount you give (not to an absolute value), "
        "clamped to the range and to a per-call limit. Read the current value first."
    )


def list_develop_parameters(ctx: ToolContext, args: ListDevelopParametersInput) -> Outcome:
    entries = tuple(
        ParameterEntry(name=name, panel=panel, minimum=low, maximum=high, note=note)
        for name, (panel, low, high, note) in DEVELOP_PARAMETERS.items()
        if args.panel is None or panel == args.panel
    )
    return Outcome(ListDevelopParametersResult(parameters=entries))


class GetDevelopParametersInput(_Input):
    """Read the current value of develop settings of a photo (or its working copy). Give the exact
    Lightroom setting names, e.g. "Clarity2012"; leave names empty for all listed parameters. Any
    Lightroom develop setting name can be read, not only the listed ones: for example the text
    settings in list_develop_parameters (readable_text_settings), such as LensProfileName, show
    whether Lightroom found a lens profile."""

    names: list[str] = Field(default_factory=list, max_length=80)


class GetDevelopParametersResult(ToolResult):
    photo_id: str
    values: dict[str, Any]
    note: str = "A value that is null is not set on this photo (Lightroom's default applies)."


def get_develop_parameters(ctx: ToolContext, args: GetDevelopParametersInput) -> Outcome:
    copy = resolve_copy(ctx, args.photo_id)
    keys = args.names or list(DEVELOP_PARAMETERS)
    raw = ctx.lightroom.get_develop_settings(copy.id, keys)
    return Outcome(
        GetDevelopParametersResult(photo_id=copy.id, values={k: raw.get(k) for k in keys})
    )


class SetDevelopParametersInput(_Input):
    """Change listed develop parameters of a photo's virtual copy. Each value is the CHANGE to
    apply (positive or negative), not the final value; it is limited to the parameter's range and
    to a per-call amount. The original is never touched. Undo with the undo tool."""

    changes: dict[str, float] = Field(min_length=1, max_length=20)


def set_develop_parameters(ctx: ToolContext, args: SetDevelopParametersInput) -> Outcome:
    unknown = [name for name in args.changes if name not in DEVELOP_PARAMETERS]
    if unknown:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"Not a settable parameter: {', '.join(unknown)}.",
            next_step="Call list_develop_parameters for the names. Exposure, tone, white balance "
            "and colour mixer have their own tools: " + OWN_TOOLS,
        )
    requests = [
        (parameter_limit(ctx.settings.guardrails, name), float(change))
        for name, change in args.changes.items()
    ]
    return apply_slider_changes(
        ctx, args.photo_id, requests, history_name="darkroom_ai: develop parameters"
    )


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


# A preset may carry framing or processing settings. Those are not part of a "look": cropping has
# its own tool, and the process version decides how every other setting is rendered. A preset that
# changes any of them is taken back (the person can apply it in Lightroom themselves).
PRESET_PROTECTED_KEYS = frozenset(
    {
        "ProcessVersion",
        "CropLeft",
        "CropTop",
        "CropRight",
        "CropBottom",
        "CropAngle",
        "CropConstrainToWarp",
        "HasCrop",
        "AspectRatio",
        "orientation",
        "UprightVersion",
    }
)


def _compact(value: Any) -> Any:
    """Keep results readable: big structures (masks, curves) become a short description."""
    if isinstance(value, dict | list | tuple):
        return f"<{type(value).__name__} with {len(value)} entries>"
    return value


class ApplyPresetInput(_Input):
    """Apply one of Lightroom's develop presets (a saved look) to the photo's virtual copy, by its
    exact name from list_presets. The result lists every setting the preset changed, with before
    and after, so look at get_preview and judge it. A preset that moves exposure far from the
    original, or changes the crop or process version, is taken back automatically. Undo with the
    undo tool."""

    preset_name: str = Field(min_length=1)


class ApplyPresetResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    preset: str
    changed: dict[str, Any]
    before: dict[str, Any]
    changed_count: int
    note: str = ""


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
    # A list-valued setting the photo did not have (masks) is missing from "before": undoing the
    # preset must set it to an empty list, or what the preset added would stay.
    restore = dict(change.before)
    for key, value in change.after.items():
        if key not in restore and isinstance(value, list):
            restore[key] = []

    protected = sorted(PRESET_PROTECTED_KEYS & set(change.after))
    if protected:
        if restore:
            ctx.lightroom.apply_develop_settings(copy.id, restore, "darkroom_ai: preset taken back")
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"The preset '{matches[0].name}' also changes {', '.join(protected)} (framing or "
            "processing, not a look). It was taken back; nothing is changed.",
            next_step="Use the crop tool for framing, or apply this preset yourself in Lightroom.",
            details={"settings": protected},
        )

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
            ctx.lightroom.apply_develop_settings(copy.id, restore, "darkroom_ai: preset taken back")
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
        changed={key: _compact(value) for key, value in change.after.items()},
        before={key: _compact(value) for key, value in change.before.items()},
        changed_count=len(change.after),
        note="Look at get_preview to judge the result; undo takes the whole preset back."
        if change.after
        else "The preset changed nothing: the photo already had these values.",
    )
    return Outcome(
        result,
        applied={"photo_id": copy.id, "preset": matches[0].name, "settings": change.after},
        undo={"photo_id": copy.id, "settings": restore} if restore else None,
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


UNDO_RECORDS = ("undo", "revert", "undo_batch")  # journal entries that list edits they took back
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
    "analyze_batch",
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
