"""Lightroom actions that are not slider edits: save points, resets, rotate, metadata, retouch.

Develop changes go to virtual copies only. Anything that removes edits or changes what a photo
shows takes a snapshot (a named save point) first and asks for the person's go-ahead.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from darkroom_ai.adapters.lightroom.models import (
    AiPanelState,
    DistractionsResult,
    KeywordList,
    MetadataChange,
    ResetResult,
    RetouchState,
    RotateResult,
    Snapshot,
)
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.safety.confirm import require_confirmation
from darkroom_ai.tools.context import (
    Outcome,
    ToolContext,
    ToolResult,
    resolve_copy,
    working_copy,
)

# -- snapshots ------------------------------------------------------------------------------------


class ListSnapshotsInput(BaseModel):
    """List a photo's snapshots (named save points of its develop settings). Reads only."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)


class SnapshotsResult(ToolResult):
    photo_id: str
    snapshots: tuple[Snapshot, ...]


def list_snapshots(ctx: ToolContext, args: ListSnapshotsInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    found = ctx.lightroom.list_snapshots(target.id)
    return Outcome(SnapshotsResult(photo_id=target.id, snapshots=found))


class CreateSnapshotInput(BaseModel):
    """Save the working copy's current develop settings as a named snapshot (a save point you can
    come back to with restore_snapshot). Use it before trying a bold look or a big change."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)


class CreateSnapshotResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    created: bool
    snapshot: Snapshot | None


def create_snapshot(ctx: ToolContext, args: CreateSnapshotInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    made = ctx.lightroom.create_snapshot(copy.id, args.name)
    result = CreateSnapshotResult(
        photo_id=copy.id,
        working_copy_created=created,
        created=made.created,
        snapshot=made.snapshot,
    )
    return Outcome(result, applied={"photo_id": copy.id, "snapshot": args.name})


class RestoreSnapshotInput(BaseModel):
    """Put the working copy back to a snapshot's develop settings. This replaces its current
    settings, so it needs the person's go-ahead. Give the snapshot id or its exact name."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    snapshot_id: str | None = None
    name: str | None = None


class RestoreSnapshotResult(ToolResult):
    photo_id: str
    restored: Snapshot
    note: str = (
        "Look at get_preview to check the result. Restoring a snapshot is UNVERIFIED on this "
        "Lightroom version: if the picture did not change, say so."
    )


def restore_snapshot(ctx: ToolContext, args: RestoreSnapshotInput) -> Outcome:
    if (args.snapshot_id is None) == (args.name is None):
        raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, "give exactly one of snapshot_id, name")
    copy = resolve_copy(ctx, args.photo_id)
    if not copy.is_virtual_copy:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            "Snapshots are only restored on a working copy, never on the original.",
            next_step="Edit the photo first (a working copy is made), or pass its working copy.",
        )
    wanted = [
        s
        for s in ctx.lightroom.list_snapshots(copy.id)
        if s.id == args.snapshot_id or s.name == args.name
    ]
    if len(wanted) != 1:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"{len(wanted)} snapshots match; list_snapshots shows them. Nothing was changed.",
        )
    require_confirmation(
        ctx.confirmer,
        f"restore snapshot '{wanted[0].name}' on {copy.file_name} (replaces its current edits)",
        {"photo_id": copy.id, "snapshot": wanted[0].name},
    )
    restored = ctx.lightroom.apply_snapshot(copy.id, wanted[0].id)
    ctx.measurements.pop(copy.id, None)
    return Outcome(
        RestoreSnapshotResult(photo_id=copy.id, restored=restored),
        applied={"photo_id": copy.id, "snapshot": wanted[0].name},
    )


# -- reset ----------------------------------------------------------------------------------------

ResetScope = Literal["all", "masks", "spots", "crop", "transforms", "red_eye"]


class ResetDevelopInput(BaseModel):
    """Reset the working copy's develop edits: everything ('all'), or one group (local masks, spot
    removal, crop, geometry transforms, red-eye). A snapshot is saved first and the changed plain
    settings are returned, and `undo` restores those. It needs the person's go-ahead because it
    removes edits."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    scope: ResetScope = "all"


class ResetDevelopResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    result: ResetResult
    note: str = (
        "A snapshot was saved before the reset (see list_snapshots). `undo` puts back the plain "
        "settings that changed; masks and spots come back only through restore_snapshot."
    )


def reset_develop(ctx: ToolContext, args: ResetDevelopInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    require_confirmation(
        ctx.confirmer,
        f"reset '{args.scope}' develop edits on {copy.file_name} (a snapshot is saved first)",
        {"photo_id": copy.id, "scope": args.scope},
    )
    done = ctx.lightroom.reset_develop(copy.id, args.scope)
    ctx.measurements.pop(copy.id, None)
    restore = {k: v for k, v in done.before.items() if v is not None}
    return Outcome(
        ResetDevelopResult(photo_id=copy.id, working_copy_created=created, result=done),
        applied={"photo_id": copy.id, "scope": args.scope, "changed": sorted(done.after)},
        undo={"photo_id": copy.id, "settings": restore} if restore else None,
    )


# -- Lightroom's AI removal of distracting people ----------------------------------------------


class RemoveDistractingPeopleInput(BaseModel):
    """Use Lightroom's own detection to find distracting people in the background and remove
    them (the Remove tool's 'distracting people'). It changes what the photo shows, so it needs
    the person's go-ahead, and a snapshot is saved first. Look at the result afterwards."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)


class RemoveDistractingPeopleResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    result: DistractionsResult
    spots_added: int | None
    note: str = (
        "UNVERIFIED on this Lightroom version. Look at get_preview; if it removed the wrong "
        "people or nothing, use restore_snapshot with the snapshot named in the result."
    )


def remove_distracting_people(ctx: ToolContext, args: RemoveDistractingPeopleInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    require_confirmation(
        ctx.confirmer,
        f"let Lightroom remove distracting people from {copy.file_name} (snapshot saved first)",
        {"photo_id": copy.id},
    )
    done = ctx.lightroom.remove_distracting_people(copy.id)
    ctx.measurements.pop(copy.id, None)
    if done.retouch_after == done.retouch_before:
        raise DarkroomError(
            ErrorCode.ADOBE_FEATURE_UNAVAILABLE,
            "Lightroom removed nobody: the spot count did not change"
            + ("" if done.callback_called else " and its callback never fired")
            + ". Either no distracting people were found, or Lightroom could not reach Adobe "
            "Firefly, which generates the fill.",
            next_step="Check Lightroom for an error dialog ('We've encountered an issue...') and "
            "press OK. If it appeared, sign in to Adobe in Lightroom (Help > Sign In) and try "
            "again; the owner must do that. Otherwise use remove_objects on the person. Nothing "
            "was applied; the pre-edit snapshot is kept.",
            details={"snapshot": done.snapshot.name if done.snapshot else None},
        )
    added = (
        done.retouch_after - done.retouch_before
        if done.retouch_after is not None and done.retouch_before is not None
        else None
    )
    result = RemoveDistractingPeopleResult(
        photo_id=copy.id, working_copy_created=created, result=done, spots_added=added
    )
    return Outcome(result, applied={"photo_id": copy.id, "spots_added": added})


# -- reads ----------------------------------------------------------------------------------------


class GetRetouchStateInput(BaseModel):
    """Read the spot-removal and red-eye corrections already on a photo (count and details)."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)


class RetouchStateResult(ToolResult):
    state: RetouchState


def get_retouch_state(ctx: ToolContext, args: GetRetouchStateInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    return Outcome(RetouchStateResult(state=ctx.lightroom.get_retouch_state(target.id)))


class GetAiPanelStateInput(BaseModel):
    """Ask Lightroom what its AI panels report for a photo (Enhance, Reflection Removal, Remove
    panel, current tool). Raw answers, for finding out what this Lightroom version offers. It
    shows the photo in Develop, so leave Lightroom alone while it runs."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)


class AiPanelStateResult(ToolResult):
    state: AiPanelState


def get_ai_panel_state(ctx: ToolContext, args: GetAiPanelStateInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    return Outcome(AiPanelStateResult(state=ctx.lightroom.get_ai_panel_state(target.id)))


class ListKeywordsInput(BaseModel):
    """List the keywords in the catalog as paths ('Places/Japan/Kyoto'), at most 500."""

    model_config = ConfigDict(extra="forbid")


def list_keywords(ctx: ToolContext, args: ListKeywordsInput) -> Outcome:
    class KeywordsResult(ToolResult):
        keywords: KeywordList

    return Outcome(KeywordsResult(keywords=ctx.lightroom.list_keywords()))


# -- rotate ---------------------------------------------------------------------------------------


class RotatePhotoInput(BaseModel):
    """Rotate the working copy a quarter turn left or right (orientation, not the crop angle).
    Turn the other way to undo it."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    direction: Literal["left", "right"]


class RotateResultOut(ToolResult):
    photo_id: str
    working_copy_created: bool
    rotation: RotateResult


def rotate_photo(ctx: ToolContext, args: RotatePhotoInput) -> Outcome:
    copy, created = working_copy(ctx, args.photo_id)
    done = ctx.lightroom.rotate_photo(copy.id, args.direction)
    ctx.measurements.pop(copy.id, None)
    return Outcome(
        RotateResultOut(photo_id=copy.id, working_copy_created=created, rotation=done),
        applied={"photo_id": copy.id, "direction": args.direction},
    )


# -- descriptive metadata -------------------------------------------------------------------------


class SetPhotoMetadataInput(BaseModel):
    """Set a photo's title, caption, headline, copyright or creator, and/or its colour label
    (red, yellow, green, blue, purple or none). Catalog data only; pixels and develop settings are
    not touched. The earlier values are returned so they can be put back."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=2000)
    caption: str | None = Field(default=None, max_length=2000)
    headline: str | None = Field(default=None, max_length=2000)
    copyright: str | None = Field(default=None, max_length=2000)
    creator: str | None = Field(default=None, max_length=2000)
    color_label: Literal["red", "yellow", "green", "blue", "purple", "none"] | None = None

    @field_validator("title", "caption", "headline", "copyright", "creator")
    @classmethod
    def _no_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("text must not contain control characters")
        return value


class SetPhotoMetadataResult(ToolResult):
    change: MetadataChange


def set_photo_metadata(ctx: ToolContext, args: SetPhotoMetadataInput) -> Outcome:
    fields: dict[str, Any] = args.model_dump(exclude_none=True, exclude={"photo_id"})
    if not fields:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            "Nothing to change: give title, caption, headline, copyright, creator or color_label.",
        )
    photo = ctx.lightroom.get_photo(args.photo_id)
    change = ctx.lightroom.set_photo_metadata(photo.id, fields)
    return Outcome(
        SetPhotoMetadataResult(change=change),
        applied={"photo_id": photo.id, "fields": sorted(fields)},
    )
