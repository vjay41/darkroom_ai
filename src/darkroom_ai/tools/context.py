"""What the tools work with (all injected) and the shared rules every edit follows."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from darkroom_ai.adapters.lightroom.models import PhotoInfo
from darkroom_ai.config import Settings
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.analysis import ImageAnalysis, analyze
from darkroom_ai.imaging.faces import FaceDetector
from darkroom_ai.imaging.io import load_image
from darkroom_ai.imaging.masks import MaskProvider
from darkroom_ai.interfaces import (
    AccessStore,
    ActionJournal,
    LightroomAdapter,
    PhotoshopAdapter,
    ProcessControl,
)
from darkroom_ai.runtime import CodeGuard
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
    processes: ProcessControl | None = None
    # The owner's catalog / photo-folder allowlist (None: no way to read or change it here).
    access: AccessStore | None = None
    # Set by the MCP server only: tells whether this process runs older code than the files on disk.
    connector: CodeGuard | None = None
    # master photo id -> id of the virtual copy the tools work on
    working_copies: dict[str, str] = field(default_factory=dict)
    # photo id -> the last measurement taken of it (valid until the next write to that photo)
    measurements: dict[str, dict[str, Any]] = field(default_factory=dict)


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
    # Measured on real preview renders before and after this edit (None if nothing was written).
    measured: dict[str, Any] | None = None


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


# -- measurements -------------------------------------------------------------------------------

MEASURE_LONG_EDGE = 1024


def measured_summary(analysis: ImageAnalysis) -> dict[str, Any]:
    """The few tone numbers that show what an edit did (shares in percent, luma on 0..1)."""
    return {
        "highlights_clipped_pct": analysis.clipping.highlights_pct,
        "shadows_clipped_pct": analysis.clipping.shadows_pct,
        "p05": analysis.tones.p05,
        "p50": analysis.tones.p50,
        "p95": analysis.tones.p95,
    }


def measure(ctx: ToolContext, photo_id: str) -> dict[str, Any]:
    """Export a preview of the photo as it is now and measure it (a real render, no prediction)."""
    preview = ctx.lightroom.export_preview(
        photo_id, f"measure-{uuid.uuid4().hex[:12]}", MEASURE_LONG_EDGE
    )
    summary = measured_summary(analyze(load_image(preview.path)))
    ctx.measurements[photo_id] = summary
    return summary


MEASURED_SOURCE = "measured on real preview renders before and after this edit"


def measurement_before(
    ctx: ToolContext, copy_id: str, photo_id: str, created: bool
) -> dict[str, Any]:
    """What the photo measures before an edit: the last valid measurement, else measure now.

    A copy that was just created renders like its master, so the master's measurement counts.
    """
    known = ctx.measurements.get(copy_id) or (ctx.measurements.get(photo_id) if created else None)
    return known if known is not None else measure(ctx, copy_id)


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


# How long to wait (read-only polling) for Lightroom to report values it has not worked out yet.
WAIT_FOR_VALUES_S = 5.0
POLL_S = 0.5
_sleep = time.sleep


def _number(value: Any) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def apply_slider_changes(
    ctx: ToolContext,
    photo_id: str,
    requests: Sequence[tuple[Limit, float]],
    *,
    history_name: str,
    allow_beyond_total: bool = False,
    written_with: Mapping[str, Any] | None = None,
) -> Outcome:
    """Clamp and apply slider changes to the working copy of `photo_id`.

    Shared by all slider edit tools. `written_with` are settings that must go out in the same
    write whenever a slider value is written (e.g. the white-balance mode); their previous
    values are journaled, so undo and revert restore exactly what was there before.
    """
    if not requests:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            "No change was requested.",
            next_step="Give at least one value to change.",
        )
    copy, created = working_copy(ctx, photo_id)
    keys = [limit.key for limit, _ in requests]
    current = ctx.lightroom.get_develop_settings(copy.id, [*keys, "ProcessVersion"])
    check_process_version(current)

    def unknown_now() -> list[str]:
        return [
            limit.name
            for limit, _ in requests
            if limit.needs_current and not isinstance(current.get(limit.key), int | float)
        ]

    unknown = unknown_now()
    waited = 0.0
    while unknown and waited < WAIT_FOR_VALUES_S:
        # Lightroom reports a RAW copy's white-balance numbers about a second after the copy's
        # first edit. Wait for them with reads only; nothing is ever written to force them.
        _sleep(POLL_S)
        waited += POLL_S
        current = ctx.lightroom.get_develop_settings(copy.id, [*keys, "ProcessVersion"])
        unknown = unknown_now()
    if unknown:
        # Never guess: treating an unknown Kelvin value as 0 would set a wildly wrong value.
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"Lightroom reports no current value for {', '.join(unknown)} on this photo (a RAW "
            "file whose white balance is 'As Shot' has no fixed number until the copy has been "
            f"edited once), so a relative change cannot be worked out (waited {waited:g} s). "
            "Nothing was changed.",
            next_step="Make a lighting edit on this photo first (for example a small exposure "
            "change), then ask for the white-balance change again.",
            details={"settings": unknown, "photo_id": copy.id},
        )
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
    to_apply: dict[str, Any] = {c.key: c.after for c in changes if c.after != c.before}
    previous: dict[str, Any] = {c.key: c.before for c in changes if c.key in to_apply}
    extra_note = ""
    if to_apply and written_with:
        before_extra = ctx.lightroom.get_develop_settings(copy.id, list(written_with))
        missing = [key for key in written_with if not isinstance(before_extra.get(key), str)]
        if missing:
            # Without the previous value the edit could not be taken back exactly.
            raise DarkroomError(
                ErrorCode.GUARDRAIL_REJECTED,
                f"Lightroom reports no current value for {', '.join(missing)}, so darkroom_ai "
                "could not record what to restore on undo. Nothing was changed.",
                next_step="Check the photo in Lightroom's Develop module, then retry.",
                details={"settings": missing, "photo_id": copy.id},
            )
        for key, value in written_with.items():
            to_apply[key] = value
            previous[key] = before_extra[key]
            if before_extra[key] != value:
                extra_note = f"{key} set to {value} (was {before_extra[key]})."
    measured: dict[str, Any] | None = None
    if to_apply:
        # What the photo measured before this edit: the last measurement if it is still valid
        # (a copy that was just created renders like its master), otherwise measure now.
        before_measured = measurement_before(ctx, copy.id, photo_id, created)
        ctx.lightroom.apply_develop_settings(copy.id, to_apply, history_name)
        measured = {
            "before": before_measured,
            "after": measure(ctx, copy.id),
            "source": MEASURED_SOURCE,
        }

    clamped = [c for c in changes if c.clamped]
    note = ""
    if clamped:
        parts = [
            f"{c.name}: asked {c.requested_change:+g}, applied {c.applied_change:+g} "
            f"({', '.join(c.limited_by)} limit)"
            for c in clamped
        ]
        note = "Clamped - " + "; ".join(parts)
    if extra_note:
        note = f"{note} {extra_note}".strip()
    result = EditResult(
        photo_id=copy.id,
        working_copy_created=created,
        changes=tuple(changes),
        clamped=bool(clamped),
        note=note,
        measured=measured,
    )
    undo = {"photo_id": copy.id, "settings": previous} if to_apply else None
    return Outcome(result, applied={"photo_id": copy.id, "settings": to_apply}, undo=undo)
