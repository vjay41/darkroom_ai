"""Batch writes: apply an approved change to several photos with one go-ahead, and take it back.

A batch is a plan first: every item is checked before anything is written (an invalid item means
nothing runs). The run then goes item by item through the same guarded handlers as the single-photo
tools, so every clamp, the virtual-copy rule and the read-back still apply. It stops at the first
failure or when the time budget is used up, and says which photos were done, which failed and which
were not run. Every step is journaled under one `batch_id`, so `undo_batch` can take the whole batch
back. Nothing here decides what an edit should be: the caller gives the values.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from darkroom_ai.adapters.lightroom.models import PhotoInfo
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.safety.confirm import require_confirmation
from darkroom_ai.safety.journal import JournalEntry
from darkroom_ai.tools import library_tools
from darkroom_ai.tools.context import (
    Outcome,
    ToolContext,
    ToolResult,
    check_process_version,
    resolve_copy,
)
from darkroom_ai.tools.lightroom_tools import (
    AdjustLightingInput,
    AdjustWhiteBalanceInput,
    SetDevelopParametersInput,
    adjust_lighting,
    adjust_white_balance,
    restore_values,
    set_develop_parameters,
)

LIBRARY_BATCH_TOOL = "batch_set_library_state"
DEVELOP_BATCH_TOOL = "batch_set_develop_parameters"
UNDO_BATCH_TOOL = "undo_batch"
HARD_MAX_PHOTOS = 25
UNDO_RECORDS = ("undo", "revert", library_tools.UNDO_TOOL, UNDO_BATCH_TOOL)
LIGHTING_FIELDS = ("exposure", "contrast", "highlights", "shadows", "whites", "blacks")

_clock: Callable[[], float] = time.monotonic  # replaced in tests

DRY_RUN_HELP = "Only check and show the plan; change nothing."

ItemStatus = Literal["planned", "invalid", "ok", "failed", "not_run"]


# -- inputs ---------------------------------------------------------------------------------------


class LibraryItem(library_tools.SetLibraryStateInput):
    """One photo and the rating, flag and keywords to set on it."""


class DevelopItem(BaseModel):
    """One photo and the changes to make to it. Values are CHANGES, as in the single-photo tools."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1, description="Catalog id of the photo or its working copy.")
    exposure: float | None = Field(default=None, description="Change in EV.")
    contrast: float | None = None
    highlights: float | None = None
    shadows: float | None = None
    whites: float | None = None
    blacks: float | None = None
    temperature: float | None = Field(
        default=None, description="JPG/TIFF: relative scale; RAW: change in Kelvin."
    )
    tint: float | None = None
    parameters: dict[str, float] = Field(
        default_factory=dict,
        max_length=10,
        description="Other develop settings by Lightroom name, as changes (as in "
        "set_develop_parameters).",
    )

    @model_validator(mode="after")
    def _something_requested(self) -> Self:
        names = (*LIGHTING_FIELDS, "temperature", "tint")
        if all(getattr(self, name) is None for name in names) and not self.parameters:
            raise ValueError("give at least one change for each photo")
        return self


def _no_duplicates(items: Sequence[Any]) -> None:
    seen: set[str] = set()
    for item in items:
        if item.photo_id in seen:
            raise ValueError(f"photo {item.photo_id} appears twice in the batch")
        seen.add(item.photo_id)


class BatchSetLibraryStateInput(BaseModel):
    """Set rating, pick/reject flag and keywords on several virtual copies in one batch (up to 25).

    Each item names a photo and its values (same rules as set_library_state: virtual copies only,
    never a master). Everything is checked first; if any item is not allowed nothing runs. A batch
    of more than 5 photos needs the person's go-ahead, which this tool asks for itself. The run
    stops at the first failure and lists what was done, failed and not run. Use dry_run to see the
    plan without changing anything. Take a whole batch back with undo_batch.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[LibraryItem] = Field(min_length=1, max_length=HARD_MAX_PHOTOS)
    dry_run: bool = Field(default=False, description=DRY_RUN_HELP)

    @field_validator("items")
    @classmethod
    def _unique(cls, items: list[LibraryItem]) -> list[LibraryItem]:
        _no_duplicates(items)
        return items


class BatchSetDevelopParametersInput(BaseModel):
    """Edit several photos in one batch (up to 10), each with its own values, on virtual copies.

    Each item gives changes for exposure and tone, white balance and/or other develop settings;
    every value goes through the same limits as adjust_lighting, adjust_white_balance and
    set_develop_parameters, and a master gets one new working copy (the plan says how many will be
    created). Always needs the person's go-ahead, which this tool asks for itself. Everything is
    checked first; the run stops at the first failure and lists what was done, failed and not run.
    Use dry_run to see the plan without changing anything. Take a whole batch back with undo_batch
    (settings are restored; created copies stay in the catalog).
    """

    model_config = ConfigDict(extra="forbid")

    items: list[DevelopItem] = Field(min_length=1, max_length=HARD_MAX_PHOTOS)
    dry_run: bool = Field(default=False, description=DRY_RUN_HELP)

    @field_validator("items")
    @classmethod
    def _unique(cls, items: list[DevelopItem]) -> list[DevelopItem]:
        _no_duplicates(items)
        return items


class UndoBatchInput(BaseModel):
    """Take back every change of one batch made in THIS session, by its batch_id.

    Restores settings and library fields from the journal and reads them back. Virtual copies a
    batch created are not deleted (deleting is never done). Changes made to the same photos after
    the batch are not separated out: the oldest recorded values are put back.
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(min_length=1)


# -- results --------------------------------------------------------------------------------------


class BatchItemResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    photo_id: str
    status: ItemStatus
    file_name: str | None = None
    working_copy: str | None = None
    creates_copy: bool = False
    plan: str = ""
    steps: tuple[dict[str, Any], ...] = ()
    verified: bool | None = None
    problems: tuple[str, ...] = ()
    error: dict[str, Any] | None = None


class BatchResult(ToolResult):
    batch_id: str
    kind: Literal["library", "develop"]
    dry_run: bool
    requested: int
    ok: int = 0
    failed: int = 0
    not_run: int = 0
    invalid: int = 0
    copies_to_create: int = 0
    copies_created: int = 0
    stopped_because: str | None = None
    elapsed_s: float = 0.0
    items: tuple[BatchItemResult, ...]
    note: str = ""


# -- helpers --------------------------------------------------------------------------------------


def _error_info(exc: DarkroomError) -> dict[str, Any]:
    return {"code": exc.code.value, "message": exc.message, "next_step": exc.next_step}


def _describe_library(item: LibraryItem) -> str:
    parts: list[str] = []
    if item.rating is not None:
        parts.append(f"rating {item.rating}")
    if item.flag is not None:
        parts.append(f"flag {item.flag}")
    if item.add_keywords:
        parts.append("add keywords " + ", ".join(item.add_keywords))
    if item.remove_keywords:
        parts.append("remove keywords " + ", ".join(item.remove_keywords))
    return "; ".join(parts)


def _describe_develop(item: DevelopItem) -> str:
    parts = [
        f"{name} {getattr(item, name):+g}"
        for name in (*LIGHTING_FIELDS, "temperature", "tint")
        if getattr(item, name) is not None
    ]
    parts += [f"{key} {value:+g}" for key, value in item.parameters.items()]
    return ", ".join(parts)


def _develop_steps(item: DevelopItem) -> list[tuple[str, BaseModel, Callable[..., Outcome]]]:
    steps: list[tuple[str, BaseModel, Callable[..., Outcome]]] = []
    lighting = {n: getattr(item, n) for n in LIGHTING_FIELDS if getattr(item, n) is not None}
    if lighting:
        steps.append(
            (
                "adjust_lighting",
                AdjustLightingInput(photo_id=item.photo_id, **lighting),
                adjust_lighting,
            )
        )
    if item.temperature is not None or item.tint is not None:
        wb = AdjustWhiteBalanceInput(
            photo_id=item.photo_id, temperature=item.temperature, tint=item.tint
        )
        steps.append(("adjust_white_balance", wb, adjust_white_balance))
    if item.parameters:
        params = SetDevelopParametersInput(photo_id=item.photo_id, changes=item.parameters)
        steps.append(("set_develop_parameters", params, set_develop_parameters))
    return steps


def _record_step(
    ctx: ToolContext,
    tool: str,
    args: BaseModel,
    batch_id: str,
    index: int,
    outcome: Outcome | None,
    error: DarkroomError | None,
) -> str:
    """Journal one step of a batch under its batch_id (same tool name as the single-photo call)."""
    raw = {**args.model_dump(exclude_none=True), "batch_id": batch_id, "batch_index": index}
    if error is not None:
        return ctx.journal.record(tool, raw, status="error", error=error.to_dict()).id
    assert outcome is not None
    return ctx.journal.record(tool, raw, applied=outcome.applied, undo=outcome.undo).id


def _limit_for(ctx: ToolContext, kind: str) -> tuple[int, float]:
    guard = ctx.settings.guardrails
    if kind == "library":
        return guard.batch_library_max, guard.batch_library_budget_s
    return guard.batch_develop_max, guard.batch_develop_budget_s


def _summary(
    batch_id: str,
    kind: Literal["library", "develop"],
    dry_run: bool,
    items: Sequence[BatchItemResult],
    *,
    stopped_because: str | None,
    elapsed_s: float,
    note: str,
) -> BatchResult:
    count = {
        s: sum(1 for i in items if i.status == s) for s in ("ok", "failed", "not_run", "invalid")
    }
    return BatchResult(
        batch_id=batch_id,
        kind=kind,
        dry_run=dry_run,
        requested=len(items),
        ok=count["ok"],
        failed=count["failed"],
        not_run=count["not_run"],
        invalid=count["invalid"],
        copies_to_create=sum(1 for i in items if i.creates_copy),
        copies_created=sum(1 for i in items if i.creates_copy and i.status == "ok"),
        stopped_because=stopped_because,
        elapsed_s=round(elapsed_s, 2),
        items=tuple(items),
        note=note,
    )


def _refuse_invalid(plan: Sequence[BatchItemResult]) -> None:
    bad = [item for item in plan if item.status == "invalid"]
    if not bad:
        return
    lines = [
        f"{item.photo_id}: {item.error['message'] if item.error else 'not allowed'}" for item in bad
    ]
    raise DarkroomError(
        ErrorCode.GUARDRAIL_REJECTED,
        f"{len(bad)} of {len(plan)} photos in the batch are not allowed, so nothing was changed: "
        + " | ".join(lines),
        next_step="Fix or remove those photos and send the batch again (dry_run shows the plan).",
        details={"invalid": [item.photo_id for item in bad]},
    )


def _check_size(ctx: ToolContext, kind: str, count: int) -> None:
    most, _budget = _limit_for(ctx, kind)
    if count > most:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"A {kind} batch may have at most {most} photos; this one has {count}. "
            "Nothing was changed.",
            next_step=f"Split it into batches of up to {most} photos.",
            details={"limit": most, "photos": count},
        )


# -- library batch --------------------------------------------------------------------------------


def _plan_library(ctx: ToolContext, item: LibraryItem) -> BatchItemResult:
    try:
        photo = library_tools.target_copy(ctx, item.photo_id)
    except DarkroomError as exc:
        return BatchItemResult(photo_id=item.photo_id, status="invalid", error=_error_info(exc))
    return BatchItemResult(
        photo_id=item.photo_id,
        status="planned",
        file_name=photo.file_name,
        working_copy=photo.id,
        plan=_describe_library(item),
    )


def _mark_shared_targets(plan: list[BatchItemResult]) -> list[BatchItemResult]:
    """Two items that resolve to the same working copy are refused (one item per copy)."""
    first: dict[str, str] = {}
    marked: list[BatchItemResult] = []
    for planned in plan:
        target = planned.working_copy
        if planned.status == "planned" and target is not None:
            if target in first:
                planned = planned.model_copy(
                    update={
                        "status": "invalid",
                        "error": {
                            "code": ErrorCode.GUARDRAIL_REJECTED.value,
                            "message": f"it resolves to the same working copy as {first[target]}",
                            "next_step": "Give each working copy once, with all its values in one "
                            "item.",
                        },
                    }
                )
            else:
                first[target] = planned.photo_id
        marked.append(planned)
    return marked


def _run_library_item(
    ctx: ToolContext, batch_id: str, index: int, item: LibraryItem, planned: BatchItemResult
) -> BatchItemResult:
    args = library_tools.SetLibraryStateInput(**item.model_dump())
    try:
        outcome = library_tools.set_library_state(ctx, args)
    except DarkroomError as exc:
        _record_step(ctx, library_tools.SET_TOOL, args, batch_id, index, None, exc)
        return planned.model_copy(update={"status": "failed", "error": _error_info(exc)})
    _record_step(ctx, library_tools.SET_TOOL, args, batch_id, index, outcome, None)
    result = outcome.result
    problems = tuple(getattr(result, "problems", ()))
    step = {"tool": library_tools.SET_TOOL, "after": result.model_dump(mode="json").get("after")}
    return planned.model_copy(
        update={
            "status": "ok" if not problems else "failed",
            "steps": (step,),
            "verified": not problems,
            "problems": problems,
        }
    )


def batch_set_library_state(ctx: ToolContext, args: BatchSetLibraryStateInput) -> Outcome:
    _check_size(ctx, "library", len(args.items))
    batch_id = uuid.uuid4().hex[:12]
    plan = _mark_shared_targets([_plan_library(ctx, item) for item in args.items])
    if args.dry_run:
        result = _summary(
            batch_id,
            "library",
            True,
            plan,
            stopped_because=None,
            elapsed_s=0.0,
            note="Plan only: nothing was changed. An invalid item would stop the real run "
            "before it starts.",
        )
        return Outcome(result, applied={"batch_id": batch_id, "dry_run": True})
    _refuse_invalid(plan)

    if len(args.items) > ctx.settings.guardrails.batch_library_confirm_above:
        require_confirmation(
            ctx.confirmer,
            f"change rating, flag or keywords on {len(args.items)} photos in one batch "
            "(virtual copies only; originals untouched; no new copies)",
            {
                "photos": len(args.items),
                "changes": [f"{p.file_name}: {p.plan}" for p in plan],
                "new_copies": 0,
            },
        )

    _most, budget = _limit_for(ctx, "library")
    started = _clock()
    done: list[BatchItemResult] = []
    stopped: str | None = None
    for index, (item, planned) in enumerate(zip(args.items, plan, strict=True)):
        if stopped is None and index > 0 and _clock() - started > budget:
            stopped = f"the time budget of {budget:g} s was used up"
        if stopped is not None:
            done.append(planned.model_copy(update={"status": "not_run"}))
            continue
        outcome = _run_library_item(ctx, batch_id, index, item, planned)
        done.append(outcome)
        if outcome.status == "failed":
            stopped = f"photo {item.photo_id} failed, so the rest were not run"
    return _finish(ctx, batch_id, "library", done, stopped, _clock() - started)


# -- develop batch --------------------------------------------------------------------------------


def _plan_develop(ctx: ToolContext, item: DevelopItem) -> tuple[BatchItemResult, str]:
    """The plan for one item and the key that identifies the working copy it will change."""
    try:
        photo = ctx.lightroom.get_photo(item.photo_id)
        copy = resolve_copy(ctx, item.photo_id)
    except DarkroomError as exc:
        return (
            BatchItemResult(photo_id=item.photo_id, status="invalid", error=_error_info(exc)),
            item.photo_id,
        )
    creates = not copy.is_virtual_copy
    return (
        BatchItemResult(
            photo_id=item.photo_id,
            status="planned",
            file_name=photo.file_name,
            working_copy=None if creates else copy.id,
            creates_copy=creates,
            plan=_describe_develop(item),
        ),
        copy.id,
    )


def _step_summary(tool: str, outcome: Outcome) -> dict[str, Any]:
    data = outcome.result.model_dump(mode="json")
    return {
        "tool": tool,
        "working_copy": data.get("photo_id"),
        "working_copy_created": data.get("working_copy_created"),
        "clamped": data.get("clamped"),
        "changes": [
            {k: c.get(k) for k in ("name", "requested_change", "applied_change", "before", "after")}
            for c in data.get("changes", [])
        ],
        "note": data.get("note", ""),
    }


def _run_develop_item(
    ctx: ToolContext, batch_id: str, index: int, item: DevelopItem, planned: BatchItemResult
) -> BatchItemResult:
    steps: list[dict[str, Any]] = []
    copy_id = planned.working_copy
    for tool, args, handler in _develop_steps(item):
        try:
            outcome = handler(ctx, args)
        except DarkroomError as exc:
            _record_step(ctx, tool, args, batch_id, index, None, exc)
            return planned.model_copy(
                update={
                    "status": "failed",
                    "steps": tuple(steps),
                    "working_copy": copy_id,
                    "error": _error_info(exc) | {"step": tool},
                }
            )
        _record_step(ctx, tool, args, batch_id, index, outcome, None)
        summary = _step_summary(tool, outcome)
        copy_id = str(summary["working_copy"] or copy_id)
        steps.append(summary)
    return planned.model_copy(
        update={"status": "ok", "steps": tuple(steps), "working_copy": copy_id}
    )


def batch_set_develop_parameters(ctx: ToolContext, args: BatchSetDevelopParametersInput) -> Outcome:
    _check_size(ctx, "develop", len(args.items))
    batch_id = uuid.uuid4().hex[:12]
    plan: list[BatchItemResult] = []
    targets: dict[str, str] = {}
    for item in args.items:
        planned, key = _plan_develop(ctx, item)
        if planned.status != "invalid" and key in targets:
            planned = planned.model_copy(
                update={
                    "status": "invalid",
                    "error": {
                        "code": ErrorCode.GUARDRAIL_REJECTED.value,
                        "message": f"it resolves to the same working copy as {targets[key]}",
                        "next_step": "Give each working copy once, with all its changes in one "
                        "item.",
                    },
                }
            )
        elif planned.status != "invalid":
            targets[key] = item.photo_id
        plan.append(planned)
    if args.dry_run:
        result = _summary(
            batch_id,
            "develop",
            True,
            plan,
            stopped_because=None,
            elapsed_s=0.0,
            note="Plan only: nothing was changed. Each value is still limited per pass and in "
            "total when applied.",
        )
        return Outcome(result, applied={"batch_id": batch_id, "dry_run": True})
    _refuse_invalid(plan)

    new_copies = sum(1 for p in plan if p.creates_copy)
    require_confirmation(
        ctx.confirmer,
        f"edit {len(args.items)} photos in one batch ({new_copies} new virtual copies will be "
        "created; originals are never changed)",
        {
            "photos": len(args.items),
            "changes": [f"{p.file_name}: {p.plan}" for p in plan],
            "new_copies": new_copies,
        },
    )

    _most, budget = _limit_for(ctx, "develop")
    started = _clock()
    done: list[BatchItemResult] = []
    stopped: str | None = None
    for index, (item, planned) in enumerate(zip(args.items, plan, strict=True)):
        if stopped is None and index > 0 and _clock() - started > budget:
            stopped = f"the time budget of {budget:g} s was used up"
        if stopped is not None:
            done.append(planned.model_copy(update={"status": "not_run"}))
            continue
        outcome = _run_develop_item(ctx, batch_id, index, item, planned)
        done.append(outcome)
        if outcome.status == "failed":
            stopped = f"photo {item.photo_id} failed, so the rest were not run"
    return _finish(ctx, batch_id, "develop", done, stopped, _clock() - started)


def _finish(
    ctx: ToolContext,
    batch_id: str,
    kind: Literal["library", "develop"],
    items: Sequence[BatchItemResult],
    stopped: str | None,
    elapsed_s: float,
) -> Outcome:
    note = (
        f"Stopped: {stopped}. Look at the failed item, then decide; do not retry in a loop. "
        f"undo_batch with this batch_id takes back what was done."
        if stopped
        else "Done. undo_batch with this batch_id takes the whole batch back."
    )
    result = _summary(
        batch_id, kind, False, items, stopped_because=stopped, elapsed_s=elapsed_s, note=note
    )
    return Outcome(
        result,
        applied={
            "batch_id": batch_id,
            "kind": kind,
            "ok": result.ok,
            "failed": result.failed,
            "not_run": result.not_run,
            "stopped_because": stopped,
        },
    )


# -- undo_batch -----------------------------------------------------------------------------------


class UndoBatchPhoto(BaseModel):
    model_config = ConfigDict(frozen=True)

    photo_id: str
    kind: Literal["library", "develop"]
    steps: int
    verified: bool
    problems: tuple[str, ...] = ()


class UndoBatchResult(ToolResult):
    batch_id: str
    undone_steps: int
    photos: tuple[UndoBatchPhoto, ...] = ()
    note: str = ""


def _already_undone(ctx: ToolContext) -> set[str]:
    return {
        str(entry_id)
        for entry in ctx.journal.entries()
        if entry.tool in UNDO_RECORDS and entry.status == "ok" and entry.applied
        for entry_id in entry.applied.get("undone", [])
    }


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) < 1e-3
    return bool(a == b)


def _undo_develop(
    ctx: ToolContext, photo_id: str, chosen: Sequence[JournalEntry]
) -> UndoBatchPhoto:
    restore = restore_values(chosen)
    check_process_version(ctx.lightroom.get_develop_settings(photo_id, ["ProcessVersion"]))
    ctx.lightroom.apply_develop_settings(photo_id, restore, "darkroom_ai: undo batch")
    ctx.measurements.pop(photo_id, None)
    now = ctx.lightroom.get_develop_settings(photo_id, list(restore))
    problems = tuple(
        f"{key} reads {now.get(key)!r}, expected {value!r}"
        for key, value in restore.items()
        if not _same(now.get(key), value)
    )
    return UndoBatchPhoto(
        photo_id=photo_id,
        kind="develop",
        steps=len(chosen),
        verified=not problems,
        problems=problems,
    )


def _undo_library(
    ctx: ToolContext, photo: PhotoInfo, chosen: Sequence[JournalEntry]
) -> UndoBatchPhoto:
    after = library_tools.restore_library_entries(ctx, photo, chosen)
    expected: dict[str, Any] = {}
    for entry in reversed(chosen):
        assert entry.undo is not None
        expected.update(
            {k: v for k, v in entry.undo["library"].items() if k in ("rating", "pick_status")}
        )
    problems: list[str] = []
    if "rating" in expected and (after.rating or 0) != int(expected["rating"]):
        problems.append(f"rating reads {after.rating or 0}, expected {expected['rating']}")
    if "pick_status" in expected and after.pick_status != int(expected["pick_status"]):
        problems.append(f"flag reads {after.pick_status}, expected {expected['pick_status']}")
    return UndoBatchPhoto(
        photo_id=photo.id,
        kind="library",
        steps=len(chosen),
        verified=not problems,
        problems=tuple(problems),
    )


def undo_batch(ctx: ToolContext, args: UndoBatchInput) -> Outcome:
    members = [e for e in ctx.journal.entries() if e.args.get("batch_id") == args.batch_id]
    if not members:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"There is no batch with id {args.batch_id} in the journal.",
            next_step="Use the batch_id from the batch result.",
        )
    done = _already_undone(ctx)
    mine = [e for e in members if e.undoable and e.id not in done]
    current = [e for e in mine if e.session == ctx.journal.session]
    if not current:
        note = (
            "This batch was made in an earlier session; only the person can take it back, "
            "with the command `darkroom revert`."
            if mine
            else "There is nothing left to take back in this batch."
        )
        return Outcome(UndoBatchResult(batch_id=args.batch_id, undone_steps=0, note=note))

    results: list[UndoBatchPhoto] = []
    restored: list[str] = []
    by_photo: dict[str, list[JournalEntry]] = {}
    for entry in current:
        assert entry.undo is not None
        by_photo.setdefault(str(entry.undo["photo_id"]), []).append(entry)
    for photo_id, entries in by_photo.items():
        library = [e for e in entries if e.undo and e.undo.get("library")]
        develop = [e for e in entries if e.undo and e.undo.get("settings")]
        try:
            if develop:
                results.append(_undo_develop(ctx, photo_id, develop))
                restored += [e.id for e in develop]
            if library:
                photo = ctx.lightroom.get_photo(photo_id)
                results.append(_undo_library(ctx, photo, library))
                restored += [e.id for e in library]
        except DarkroomError as exc:
            results.append(
                UndoBatchPhoto(
                    photo_id=photo_id,
                    kind="develop" if develop else "library",
                    steps=0,
                    verified=False,
                    problems=(f"{exc.code.value}: {exc.message}",),
                )
            )
    all_ok = all(r.verified for r in results)
    note = (
        "The batch was taken back and read back."
        if all_ok
        else "Some steps could not be confirmed: look at the problems and check those photos "
        "in Lightroom."
    )
    if any(e.tool == "adjust_lighting" or e.tool == "adjust_white_balance" for e in current):
        note += " Any virtual copies the batch created stay in the catalog."
    return Outcome(
        UndoBatchResult(
            batch_id=args.batch_id,
            undone_steps=len(restored),
            photos=tuple(results),
            note=note,
        ),
        applied={"batch_id": args.batch_id, "undone": restored},
    )


__all__ = [
    "BatchSetDevelopParametersInput",
    "BatchSetLibraryStateInput",
    "UndoBatchInput",
    "batch_set_develop_parameters",
    "batch_set_library_state",
    "undo_batch",
]
