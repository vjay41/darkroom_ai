"""What the tools work with (all injected) and the shared rules every edit follows."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from darkroom_ai.adapters.lightroom.models import PhotoInfo
from darkroom_ai.config import Settings
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.faces import FaceDetector
from darkroom_ai.imaging.masks import MaskProvider
from darkroom_ai.interfaces import ActionJournal, LightroomAdapter, PhotoshopAdapter
from darkroom_ai.safety.confirm import Confirmer, DenyAll, require_confirmation
from darkroom_ai.safety.guardrails import ClampedChange, Limit, clamp_change
from darkroom_ai.tools.develop_keys import MIN_PROCESS_VERSION

VIRTUAL_COPY_TOOL = "create_virtual_copy"


@dataclass
class ToolContext:
    settings: Settings
    lightroom: LightroomAdapter
    journal: ActionJournal
    confirmer: Confirmer = field(default_factory=DenyAll)
    photoshop: PhotoshopAdapter | None = None
    masks: MaskProvider | None = None
    faces: FaceDetector | None = None
    # master photo id -> id of the virtual copy the tools work on
    working_copies: dict[str, str] = field(default_factory=dict)


class ToolResult(BaseModel):
    """Base of every tool result. `journal_id` identifies the journal entry of the call."""

    model_config = ConfigDict(frozen=True)

    journal_id: str | None = None


@dataclass(frozen=True)
class Outcome:
    """What a tool handler returns: the result plus what to record in the journal."""

    result: ToolResult
    applied: Mapping[str, Any] | None = None
    undo: Mapping[str, Any] | None = None


class EditResult(ToolResult):
    """Result of a slider edit. Compare `requested_change` and `applied_change` per entry."""

    photo_id: str  # the virtual copy that was edited (use this id from now on)
    working_copy_created: bool
    changes: tuple[ClampedChange, ...]
    clamped: bool
    note: str = ""


# -- working copies -----------------------------------------------------------------------------


def _copy_from_journal(ctx: ToolContext, master_id: str) -> PhotoInfo | None:
    """Reuse the virtual copy an earlier session made for this master, if it still exists."""
    for entry in reversed(list(ctx.journal.entries())):
        if entry.tool != VIRTUAL_COPY_TOOL or entry.status != "ok" or not entry.applied:
            continue
        if entry.args.get("master_id") != master_id:
            continue
        try:
            copy = ctx.lightroom.get_photo(str(entry.applied.get("photo_id")))
        except DarkroomError:
            return None
        return copy if copy.is_virtual_copy and copy.master_id == master_id else None
    return None


def working_copy(ctx: ToolContext, photo_id: str) -> tuple[PhotoInfo, bool]:
    """Return the virtual copy to edit for `photo_id` and whether it was just created.

    Edits never go to a master photo: a master gets one virtual copy, which is reused.
    """
    photo = ctx.lightroom.get_photo(photo_id)
    if photo.is_virtual_copy:
        return photo, False
    known = ctx.working_copies.get(photo_id)
    if known is not None:
        return ctx.lightroom.get_photo(known), False
    existing = _copy_from_journal(ctx, photo_id)
    if existing is not None:
        ctx.working_copies[photo_id] = existing.id
        return existing, False
    name = f"darkroom_ai {time.strftime('%Y%m%d-%H%M%S')}"
    copy = ctx.lightroom.create_virtual_copy(photo_id, name)
    ctx.working_copies[photo_id] = copy.id
    ctx.journal.record(
        VIRTUAL_COPY_TOOL,
        {"master_id": photo_id, "copy_name": name},
        applied={"photo_id": copy.id, "copy_name": copy.copy_name},
    )
    return copy, True


def resolve_copy(ctx: ToolContext, photo_id: str) -> PhotoInfo:
    """Like working_copy, but never creates one: returns the photo itself if no copy exists."""
    photo = ctx.lightroom.get_photo(photo_id)
    if photo.is_virtual_copy:
        return photo
    known = ctx.working_copies.get(photo_id)
    if known is not None:
        return ctx.lightroom.get_photo(known)
    return _copy_from_journal(ctx, photo_id) or photo


# -- originals and slider edits -----------------------------------------------------------------


def original_values(ctx: ToolContext, photo_id: str, current: Mapping[str, Any]) -> dict[str, Any]:
    """Each setting's value before darkroom_ai first changed it on this photo.

    Taken from the journal (the first recorded "before" value per setting); settings that were
    never changed are at their original value now.
    """
    originals: dict[str, Any] = {}
    for entry in ctx.journal.entries():
        undo = entry.undo
        if entry.status != "ok" or not undo or undo.get("photo_id") != photo_id:
            continue
        for key, value in (undo.get("settings") or {}).items():
            originals.setdefault(key, value)
    return {key: originals.get(key, value) for key, value in current.items()}


def check_process_version(settings: Mapping[str, Any]) -> None:
    version = str(settings.get("ProcessVersion", ""))
    try:
        number = float(version)
    except ValueError:
        number = 0.0
    if number < MIN_PROCESS_VERSION:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"This photo uses the old process version '{version}', whose sliders have different "
            "names. Nothing was changed.",
            next_step="In Lightroom's Develop module, update the photo to the current process "
            "version (Calibration panel), then retry.",
            details={"process_version": version},
        )


def _number(value: Any) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def apply_slider_changes(
    ctx: ToolContext,
    photo_id: str,
    requests: Sequence[tuple[Limit, float]],
    *,
    history_name: str,
    allow_beyond_total: bool = False,
) -> Outcome:
    """Clamp and apply slider changes to the working copy of `photo_id`.

    Shared by all slider edit tools.
    """
    if not requests:
        raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, "No change was requested.")
    copy, created = working_copy(ctx, photo_id)
    keys = [limit.key for limit, _ in requests]
    current = ctx.lightroom.get_develop_settings(copy.id, [*keys, "ProcessVersion"])
    check_process_version(current)
    originals = original_values(ctx, copy.id, {key: current.get(key) for key in keys})

    if allow_beyond_total:
        require_confirmation(
            ctx.confirmer,
            "go beyond the total edit limit for this photo",
            {"photo_id": copy.id, "settings": keys},
        )

    changes = [
        clamp_change(
            limit,
            current=_number(current.get(limit.key)),
            original=_number(originals.get(limit.key)),
            requested_change=change,
            allow_beyond_total=allow_beyond_total,
        )
        for limit, change in requests
    ]
    to_apply = {c.key: c.after for c in changes if c.after != c.before}
    if to_apply:
        ctx.lightroom.apply_develop_settings(copy.id, to_apply, history_name)

    clamped = [c for c in changes if c.clamped]
    note = ""
    if clamped:
        parts = [
            f"{c.name}: asked {c.requested_change:+g}, applied {c.applied_change:+g} "
            f"({', '.join(c.limited_by)} limit)"
            for c in clamped
        ]
        note = "Clamped - " + "; ".join(parts)
    result = EditResult(
        photo_id=copy.id,
        working_copy_created=created,
        changes=tuple(changes),
        clamped=bool(clamped),
        note=note,
    )
    undo = (
        {"photo_id": copy.id, "settings": {c.key: c.before for c in changes if c.key in to_apply}}
        if to_apply
        else None
    )
    return Outcome(result, applied={"photo_id": copy.id, "settings": to_apply}, undo=undo)
