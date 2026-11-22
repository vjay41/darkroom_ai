"""Catalog searches, smart collections, smart previews, metadata presets and AI Enhance.

Searches only read. Smart collections, smart previews and metadata presets change the catalog and
are announced; Enhance and Reflection Removal change what a photo shows, so they work on a virtual
copy, save a snapshot first and need the person's go-ahead.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from darkroom_ai.adapters.lightroom.models import AiRunResult, FoundPhotos, PresetNames
from darkroom_ai.safety.confirm import require_confirmation
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, working_copy

MAX_PREVIEW_PHOTOS = 25
MAX_PRESET_PHOTOS = 50

Criteria = Literal[
    "rating",
    "pick",
    "labelColor",
    "keywords",
    "fileFormat",
    "isoSpeedRating",
    "captureTime",
    "camera",
    "lens",
    "hasAdjustments",
]
Combine = Literal["intersect", "union"]


class SearchCondition(BaseModel):
    """One test. rating/isoSpeedRating use == != > >= < <= with a number; pick is == with 1
    (picked), 0 or -1 (rejected); labelColor is == or != with red, yellow, green, blue, purple or
    none; keywords use any/all/noneOf/beginsWith/endsWith with text; fileFormat is == with DNG,
    RAW, JPG, TIFF, PNG...; captureTime uses == != > < with a 'YYYY-MM-DD' date; camera and lens
    are == with exact text; hasAdjustments uses isTrue or isFalse (value is ignored)."""

    model_config = ConfigDict(extra="forbid")

    criteria: Criteria
    operation: Literal[
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "any",
        "all",
        "noneOf",
        "beginsWith",
        "endsWith",
        "isTrue",
        "isFalse",
    ] = "=="
    value: str | int | float | bool = True


class FindPhotosInput(BaseModel):
    """Find photos in the whole catalog by rating, pick flag, colour label, keyword, file format,
    ISO or capture date. Reads only; returns at most 200 photos."""

    model_config = ConfigDict(extra="forbid")

    conditions: list[SearchCondition] = Field(min_length=1, max_length=8)
    combine: Combine = "intersect"


class FindPhotosResult(ToolResult):
    found: FoundPhotos


def find_photos(ctx: ToolContext, args: FindPhotosInput) -> Outcome:
    found = ctx.lightroom.find_photos([c.model_dump() for c in args.conditions], args.combine)
    return Outcome(FindPhotosResult(found=found))


class CreateSmartCollectionInput(BaseModel):
    """Create a smart collection (a saved search that keeps itself up to date), for example
    all 4-star and better photos. The parent set must exist. It only adds a collection; nothing
    in the photos changes."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description='Such as "Smart/Best of 2026".')
    conditions: list[SearchCondition] = Field(min_length=1, max_length=8)
    combine: Combine = "intersect"


class SmartCollectionResult(ToolResult):
    path: str
    created: bool
    photo_count: int
    note: str = "UNVERIFIED on this Lightroom version: check the collection in the panel."


def create_smart_collection(ctx: ToolContext, args: CreateSmartCollectionInput) -> Outcome:
    made = ctx.lightroom.create_smart_collection(
        args.path, [c.model_dump() for c in args.conditions], args.combine
    )
    result = SmartCollectionResult(
        path=made.path, created=made.created, photo_count=made.photo_count
    )
    undo = {"collection_op": {"kind": "create", "type": "collection", "path": made.path}}
    return Outcome(
        result,
        applied={"path": made.path, "created": made.created},
        undo=undo if made.created else None,
    )


class BuildSmartPreviewsInput(BaseModel):
    """Build smart previews (small editable stand-ins) for up to 25 photos, so they can be edited
    while the original files are offline. It adds files to the catalog's preview folder only."""

    model_config = ConfigDict(extra="forbid")

    photo_ids: list[str] = Field(min_length=1, max_length=MAX_PREVIEW_PHOTOS)


class SmartPreviewsResult(ToolResult):
    requested: int
    note: str = "Lightroom may stop at the first photo it cannot build."


def build_smart_previews(ctx: ToolContext, args: BuildSmartPreviewsInput) -> Outcome:
    if len(args.photo_ids) > 10:
        require_confirmation(
            ctx.confirmer,
            f"build smart previews for {len(args.photo_ids)} photos (this can take a while)",
            {"count": len(args.photo_ids)},
        )
    done = ctx.lightroom.build_smart_previews(args.photo_ids)
    return Outcome(
        SmartPreviewsResult(requested=done.requested), applied={"requested": done.requested}
    )


class ListPresetsInput(BaseModel):
    """List the names of Lightroom's metadata presets (for example a copyright template) or
    file-naming presets. Reads only."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["metadata", "filename"] = "metadata"


class PresetsResult(ToolResult):
    kind: str
    presets: list[str]


def list_presets(ctx: ToolContext, args: ListPresetsInput) -> Outcome:
    names: PresetNames = (
        ctx.lightroom.list_metadata_presets()
        if args.kind == "metadata"
        else ctx.lightroom.list_filename_presets()
    )
    return Outcome(PresetsResult(kind=args.kind, presets=names.presets))


class ApplyMetadataPresetInput(BaseModel):
    """Apply a metadata preset to photos. It overwrites the fields the preset fills in, so the
    person's go-ahead is needed first. Look the name up with list_catalog_presets."""

    model_config = ConfigDict(extra="forbid")

    photo_ids: list[str] = Field(min_length=1, max_length=MAX_PRESET_PHOTOS)
    preset: str = Field(min_length=1)


class MetadataPresetResult(ToolResult):
    preset: str
    applied: int
    note: str = (
        "Not undoable with `undo`; apply a different preset or edit with set_photo_metadata."
    )


def apply_metadata_preset(ctx: ToolContext, args: ApplyMetadataPresetInput) -> Outcome:
    require_confirmation(
        ctx.confirmer,
        f"apply the metadata preset '{args.preset}' to {len(args.photo_ids)} photo(s); it "
        "overwrites the fields it fills in",
        {"preset": args.preset, "photo_ids": args.photo_ids},
    )
    done = ctx.lightroom.apply_metadata_preset(args.photo_ids, args.preset)
    return Outcome(
        MetadataPresetResult(preset=done.preset, applied=done.applied),
        applied={"preset": done.preset, "applied": done.applied},
    )


class EnhanceInput(BaseModel):
    """Run Lightroom's AI Enhance on the working copy: 'denoise' (amount 1-100, RAW only) or
    'superRes' (doubles the pixels). Raw Details cannot be run alone: Lightroom applies it with
    those two. It can take minutes and uses the GPU, changes what the photo
    shows, saves a snapshot first and needs the person's go-ahead. Judge the result with
    get_preview and get_ai_panel_state."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    feature: Literal["denoise", "superRes"]
    amount: int | None = Field(default=None, ge=1, le=100)


class ReflectionRemovalInput(BaseModel):
    """Run Lightroom's AI Reflection Removal (for example glass glare) on the working copy.
    Quality preview is fast, best is slow. It saves a snapshot first and needs the person's
    go-ahead. Judge the result with get_preview."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    amount: int | None = Field(default=None, ge=-100, le=100)
    quality: Literal["preview", "standard", "best"] = "preview"


class AiRunToolResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    run: AiRunResult
    note: str = (
        "UNVERIFIED on this Lightroom version. If the callback was not called or the picture "
        "looks wrong, use restore_snapshot with the snapshot named in the run."
    )


def enhance(ctx: ToolContext, args: EnhanceInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    require_confirmation(
        ctx.confirmer,
        f"run Lightroom AI Enhance ({args.feature}) on {copy.file_name} (snapshot saved first; "
        "can take minutes)",
        {"photo_id": copy.id, "feature": args.feature},
    )
    run = ctx.lightroom.enhance(copy.id, args.feature, args.amount)
    ctx.measurements.pop(copy.id, None)
    return Outcome(
        AiRunToolResult(photo_id=copy.id, working_copy_created=created, run=run),
        applied={"photo_id": copy.id, "feature": args.feature},
    )


def reflection_removal(ctx: ToolContext, args: ReflectionRemovalInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    require_confirmation(
        ctx.confirmer,
        f"run Lightroom AI Reflection Removal ({args.quality}) on {copy.file_name} (snapshot "
        "saved first)",
        {"photo_id": copy.id, "quality": args.quality},
    )
    run = ctx.lightroom.reflection_removal(copy.id, args.amount, args.quality)
    ctx.measurements.pop(copy.id, None)
    return Outcome(
        AiRunToolResult(photo_id=copy.id, working_copy_created=created, run=run),
        applied={"photo_id": copy.id, "quality": args.quality},
    )
