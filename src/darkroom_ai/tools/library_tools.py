"""Library tools: star rating, pick/reject flag and keywords.

These change catalog metadata only (no pixels, no develop settings) and only on virtual copies,
the same rule as every other write. Each change is read back from Lightroom and journaled with the
values needed to take it back (`undo_library_change`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from darkroom_ai.adapters.lightroom.models import LibraryState, PhotoInfo
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.safety.journal import JournalEntry
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, resolve_copy

SET_TOOL = "set_library_state"
UNDO_TOOL = "undo_library_change"
BATCH_UNDO_TOOL = "undo_batch"  # a batch undo also takes back library changes
MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 64

Flag = Literal["pick", "reject", "none"]
FLAG_TO_STATUS: dict[str, int] = {"pick": 1, "none": 0, "reject": -1}
STATUS_TO_FLAG: dict[int, Flag] = {1: "pick", 0: "none", -1: "reject"}


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1, description="Catalog id of the photo or its working copy.")


def _flag(status: int) -> Flag:
    return STATUS_TO_FLAG.get(status, "none")


def _names_key(names: Sequence[str]) -> dict[str, str]:
    """Keyword names by lower-case key (Lightroom treats keyword names case-insensitively)."""
    return {name.lower(): name for name in names}


# -- get_library_state ----------------------------------------------------------------------------


class GetLibraryStateInput(_Input):
    """Read a photo's star rating, pick/reject flag and keywords. Changes nothing."""


class LibraryStateResult(ToolResult):
    photo_id: str
    rating: int | None
    flag: Flag
    keywords: tuple[str, ...]


def get_library_state(ctx: ToolContext, args: GetLibraryStateInput) -> Outcome:
    state = ctx.lightroom.get_library_state(args.photo_id)
    return Outcome(
        LibraryStateResult(
            photo_id=args.photo_id,
            rating=state.rating,
            flag=_flag(state.pick_status),
            keywords=state.keywords,
        )
    )


# -- set_library_state ----------------------------------------------------------------------------


class SetLibraryStateInput(_Input):
    """Set a virtual copy's star rating, pick/reject flag and keywords (catalog data only).

    rating is 0-5 (0 clears the rating); flag is "pick", "reject" or "none"; add_keywords and
    remove_keywords are plain names (an existing keyword of that name is reused, a new one is
    created at the top level of the keyword list). Only virtual copies are changed, never a
    master photo. The change is read back from Lightroom and can be taken back with
    undo_library_change.
    """

    rating: int | None = Field(default=None, ge=0, le=5)
    flag: Flag | None = None
    add_keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS)
    remove_keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS)

    @field_validator("add_keywords", "remove_keywords")
    @classmethod
    def _clean_names(cls, names: list[str]) -> list[str]:
        cleaned = [name.strip() for name in names]
        if any(not name or len(name) > MAX_KEYWORD_LENGTH for name in cleaned):
            raise ValueError(f"each keyword must be 1-{MAX_KEYWORD_LENGTH} characters")
        return cleaned

    @model_validator(mode="after")
    def _something_requested(self) -> Self:
        if (
            self.rating is None
            and self.flag is None
            and not self.add_keywords
            and not self.remove_keywords
        ):
            raise ValueError("give rating, flag, add_keywords or remove_keywords")
        return self


class SetLibraryStateResult(ToolResult):
    photo_id: str
    before: LibraryStateResult
    after: LibraryStateResult
    verified: bool
    problems: list[str] = Field(default_factory=list)
    note: str = ""


def target_copy(ctx: ToolContext, photo_id: str) -> PhotoInfo:
    """The virtual copy to change: the photo itself if it is one, else its existing working copy."""
    photo = resolve_copy(ctx, photo_id)
    if not photo.is_virtual_copy:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            "Library changes are only made to virtual copies, never to a master photo. "
            "Nothing was changed.",
            next_step="Use the id of the photo's virtual copy, or make a first edit with any "
            "adjust tool (that creates the working copy), then retry with its id.",
            details={"photo_id": photo_id},
        )
    return photo


def _shown(photo_id: str, state: LibraryState) -> LibraryStateResult:
    return LibraryStateResult(
        photo_id=photo_id,
        rating=state.rating,
        flag=_flag(state.pick_status),
        keywords=state.keywords,
    )


def _problems(args: SetLibraryStateInput, after: LibraryState) -> list[str]:
    """What Lightroom's read-back shows was NOT applied (an empty list means it all was)."""
    problems: list[str] = []
    if args.rating is not None and (after.rating or 0) != args.rating:
        problems.append(f"rating is {after.rating or 0}, not {args.rating}")
    if args.flag is not None and after.pick_status != FLAG_TO_STATUS[args.flag]:
        problems.append(f"flag is {_flag(after.pick_status)}, not {args.flag}")
    present = _names_key(after.keywords)
    problems += [
        f"keyword '{n}' was not added" for n in args.add_keywords if n.lower() not in present
    ]
    problems += [
        f"keyword '{n}' is still there" for n in args.remove_keywords if n.lower() in present
    ]
    return problems


def set_library_state(ctx: ToolContext, args: SetLibraryStateInput) -> Outcome:
    photo = target_copy(ctx, args.photo_id)
    change = ctx.lightroom.set_library_state(
        photo.id,
        rating=args.rating,
        pick_status=None if args.flag is None else FLAG_TO_STATUS[args.flag],
        add_keywords=args.add_keywords,
        remove_keywords=args.remove_keywords,
    )
    before, after = change.before, change.after
    problems = _problems(args, after)

    # What to put back, worked out from what Lightroom says happened (not from what was asked).
    undo_data: dict[str, object] = {}
    if (before.rating or 0) != (after.rating or 0):
        undo_data["rating"] = before.rating or 0
    if before.pick_status != after.pick_status:
        undo_data["pick_status"] = before.pick_status
    was, now = _names_key(before.keywords), _names_key(after.keywords)
    added = [now[key] for key in now if key not in was]
    removed = [was[key] for key in was if key not in now]
    if added:
        undo_data["remove_keywords"] = added
    if removed:
        undo_data["add_keywords"] = removed

    result = SetLibraryStateResult(
        photo_id=photo.id,
        before=_shown(photo.id, before),
        after=_shown(photo.id, after),
        verified=not problems,
        problems=problems,
        note="" if undo_data else "Lightroom already had these values: nothing changed.",
    )
    return Outcome(
        result,
        applied={"photo_id": photo.id, "before": before.model_dump(), "after": after.model_dump()},
        undo={"photo_id": photo.id, "library": undo_data} if undo_data else None,
    )


# -- undo_library_change --------------------------------------------------------------------------


class UndoLibraryChangeInput(_Input):
    """Take back darkroom_ai's library changes (rating, flag, keywords) made in THIS session.

    scope "last" takes back the most recent one; "all" takes back all of them on this photo.
    Keywords that were created are left in Lightroom's keyword list (only removed from the photo).
    """

    scope: Literal["last", "all"] = "last"


class UndoLibraryChangeResult(ToolResult):
    photo_id: str
    undone_steps: int
    after: LibraryStateResult | None = None
    note: str = ""


NOTHING_TO_UNDO = "There is no library change of this session to take back on this photo."


def pending_library_changes(ctx: ToolContext, photo_id: str) -> list[JournalEntry]:
    """This session's library changes on a photo that have not been taken back, oldest first."""
    entries = list(ctx.journal.entries())
    already = {
        str(entry_id)
        for entry in entries
        if entry.tool in (UNDO_TOOL, BATCH_UNDO_TOOL) and entry.status == "ok" and entry.applied
        for entry_id in entry.applied.get("undone", [])
    }
    return [
        entry
        for entry in entries
        if entry.tool == SET_TOOL
        and entry.undoable
        and entry.undo is not None
        and entry.undo.get("photo_id") == photo_id
        and entry.undo.get("library")
        and entry.session == ctx.journal.session
        and entry.id not in already
    ]


def restore_library_entries(
    ctx: ToolContext, photo: PhotoInfo, chosen: Sequence[JournalEntry]
) -> LibraryState:
    """Put back what `chosen` (oldest first) changed on a photo; returns the state read back."""
    current = ctx.lightroom.get_library_state(photo.id)
    rating: int | None = None
    pick: int | None = None
    keywords = _names_key(current.keywords)
    for entry in reversed(chosen):  # newest first, so the oldest "before" value wins
        assert entry.undo is not None
        library = entry.undo["library"]
        if "rating" in library:
            rating = int(library["rating"])
        if "pick_status" in library:
            pick = int(library["pick_status"])
        for name in library.get("remove_keywords", []):
            keywords.pop(name.lower(), None)
        for name in library.get("add_keywords", []):
            keywords.setdefault(name.lower(), name)
    now = _names_key(current.keywords)
    to_add = [keywords[key] for key in keywords if key not in now]
    to_remove = [now[key] for key in now if key not in keywords]
    if rating is not None and rating == (current.rating or 0):
        rating = None
    if pick is not None and pick == current.pick_status:
        pick = None
    if rating is None and pick is None and not to_add and not to_remove:
        after = current
    else:
        after = ctx.lightroom.set_library_state(
            photo.id,
            rating=rating,
            pick_status=pick,
            add_keywords=to_add,
            remove_keywords=to_remove,
        ).after
    return after


def undo_library_change(ctx: ToolContext, args: UndoLibraryChangeInput) -> Outcome:
    photo = target_copy(ctx, args.photo_id)
    pending = pending_library_changes(ctx, photo.id)
    if not pending:
        return Outcome(
            UndoLibraryChangeResult(photo_id=photo.id, undone_steps=0, note=NOTHING_TO_UNDO)
        )
    chosen = pending if args.scope == "all" else pending[-1:]

    after = restore_library_entries(ctx, photo, chosen)
    result = UndoLibraryChangeResult(
        photo_id=photo.id, undone_steps=len(chosen), after=_shown(photo.id, after)
    )
    return Outcome(result, applied={"photo_id": photo.id, "undone": [e.id for e in chosen]})


__all__ = [
    "GetLibraryStateInput",
    "SetLibraryStateInput",
    "UndoLibraryChangeInput",
    "get_library_state",
    "set_library_state",
    "undo_library_change",
]
