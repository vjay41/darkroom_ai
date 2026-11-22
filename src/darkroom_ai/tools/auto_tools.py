"""auto_correct: Lightroom's own Auto (Basic panel) run on each photo by itself.

Lightroom measures every photo and chooses its own exposure, contrast, highlights, shadows, whites
and blacks (and, if asked, white balance). Each photo is processed one at a time on its own virtual
copy, so the values always belong to that photo: nothing is copied from one photo to the next. The
edit goes through the normal guardrails (per-pass limits), is read back, measured before and after,
and journaled so `undo` and `undo_batch` take it back. Which photos get Auto, and what to refine
afterwards, is for the skill and the person to decide.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.safety.confirm import require_confirmation
from darkroom_ai.tools.context import (
    Outcome,
    ToolContext,
    ToolResult,
    check_process_version,
    measure,
    measurement_before,
    resolve_copy,
    working_copy,
)
from darkroom_ai.tools.develop_keys import lighting_limits, white_balance_limits

TOOL_NAME = "auto_correct"
HARD_MAX_PHOTOS = 25
WATCHED_KEYS = (
    "Exposure2012",
    "Contrast2012",
    "Highlights2012",
    "Shadows2012",
    "Whites2012",
    "Blacks2012",
    "Temperature",
    "Tint",
    "IncrementalTemperature",
    "IncrementalTint",
)

_clock: Callable[[], float] = time.monotonic  # replaced in tests

ItemStatus = Literal["planned", "invalid", "ok", "failed", "not_run"]


class AutoCorrectInput(BaseModel):
    """Run Lightroom's own Auto (the Auto button in the Basic panel) on each listed photo
    SEPARATELY: Lightroom looks at that photo alone and sets its exposure, contrast, highlights,
    shadows, whites and blacks. Set white_balance to also run Auto White Balance. Values are never
    copied between photos. It edits each photo's working copy (a virtual copy; originals are never
    changed), limits every setting to the normal per-pass change, and reports what Lightroom chose
    and what was kept, with measurements before and after. More than one photo needs the person's
    go-ahead. `undo` takes back one photo; `undo_batch` takes back the batch. Lightroom must stay
    still while this runs (it switches to the Develop module for each photo)."""

    model_config = ConfigDict(extra="forbid")

    photo_ids: list[str] = Field(min_length=1, max_length=HARD_MAX_PHOTOS)
    tone: bool = Field(default=True, description="Run Auto (exposure, contrast, tones).")
    white_balance: bool = Field(default=False, description="Also run Auto White Balance.")
    dry_run: bool = Field(default=False, description="Only show the plan; change nothing.")

    @field_validator("photo_ids")
    @classmethod
    def _no_blank_or_repeats(cls, ids: list[str]) -> list[str]:
        if any(not value.strip() for value in ids):
            raise ValueError("photo ids must not be empty")
        if len(set(ids)) != len(ids):
            raise ValueError("each photo may be listed once")
        return ids


class AutoItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    photo_id: str
    status: ItemStatus
    file_name: str | None = None
    working_copy: str | None = None
    creates_copy: bool = False
    chosen_by_lightroom: dict[str, Any] = Field(default_factory=dict)
    kept: dict[str, Any] = Field(default_factory=dict)
    limited: dict[str, dict[str, float]] = Field(default_factory=dict)
    before: dict[str, Any] = Field(default_factory=dict)
    measured: dict[str, Any] | None = None
    verified: bool | None = None
    note: str = ""
    problems: tuple[str, ...] = ()
    error: dict[str, Any] | None = None


class AutoCorrectResult(ToolResult):
    batch_id: str
    dry_run: bool
    requested: int
    ok: int = 0
    failed: int = 0
    not_run: int = 0
    invalid: int = 0
    items: tuple[AutoItem, ...]
    identical_across_photos: bool = False
    stopped_because: str | None = None
    elapsed_s: float = 0.0
    note: str = ""


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _close(a: Any, b: Any) -> bool:
    return abs(float(a) - float(b)) < 1e-3 if _number(a) and _number(b) else bool(a == b)


def _clamp_to_pass(
    ctx: ToolContext, file_format: str, before: dict[str, Any], chosen: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, float]]]:
    """Keep every Lightroom-chosen value within the per-pass limit of the guardrails."""
    limits = {
        limit.key: limit
        for limit in (
            *lighting_limits(ctx.settings.guardrails).values(),
            *white_balance_limits(ctx.settings.guardrails, file_format).values(),
        )
    }
    kept: dict[str, Any] = {}
    limited: dict[str, dict[str, float]] = {}
    for key, value in chosen.items():
        limit = limits.get(key)
        start = before.get(key)
        if limit is None or not _number(value):
            kept[key] = value
            continue
        if start is None:
            start = 0.0 if not limit.needs_current else None
        wanted = float(value)
        if start is not None:
            wanted = min(max(wanted, float(start) - limit.per_pass), float(start) + limit.per_pass)
        wanted = min(max(wanted, limit.minimum), limit.maximum)
        wanted = round(wanted, 2)
        kept[key] = wanted
        if not _close(wanted, value):
            limited[key] = {"lightroom_chose": float(value), "kept": wanted}
    return kept, limited


def _plan(ctx: ToolContext, photo_id: str) -> AutoItem:
    try:
        target = resolve_copy(ctx, photo_id)
        master = ctx.lightroom.get_photo(photo_id)
    except DarkroomError as exc:
        return AutoItem(photo_id=photo_id, status="invalid", error=exc.to_dict())
    creates = not target.is_virtual_copy
    return AutoItem(
        photo_id=photo_id,
        status="planned",
        file_name=master.file_name,
        working_copy=None if creates else target.id,
        creates_copy=creates,
    )


def _journal(
    ctx: ToolContext,
    args: dict[str, Any],
    outcome: tuple[dict[str, Any], dict[str, Any]] | None,
    error: DarkroomError | None,
) -> None:
    if error is not None:
        ctx.journal.record(TOOL_NAME, args, status="error", error=error.to_dict())
        return
    assert outcome is not None
    applied, undo = outcome
    ctx.journal.record(TOOL_NAME, args, applied=applied, undo=undo or None)


def _run_one(
    ctx: ToolContext, args: AutoCorrectInput, batch_id: str, index: int, planned: AutoItem
) -> AutoItem:
    raw = {
        "photo_id": planned.photo_id,
        "tone": args.tone,
        "white_balance": args.white_balance,
        "batch_id": batch_id,
        "batch_index": index,
    }
    try:
        copy, created = working_copy(ctx, planned.photo_id)
        check_process_version(ctx.lightroom.get_develop_settings(copy.id, ["ProcessVersion"]))
        known = ctx.lightroom.get_develop_settings(copy.id, list(WATCHED_KEYS))
        measured_before = measurement_before(ctx, copy.id, planned.photo_id, created)
        auto = ctx.lightroom.auto_adjust(copy.id, args.tone, args.white_balance)
        chosen = dict(auto.after)
        previous = {k: known.get(k, auto.before.get(k)) for k in chosen}
        kept, limited = _clamp_to_pass(ctx, copy.file_format, previous, chosen)
        if limited:
            ctx.lightroom.apply_develop_settings(
                copy.id, {k: kept[k] for k in limited}, "darkroom_ai: auto correct (limited)"
            )
        now = ctx.lightroom.get_develop_settings(copy.id, list(kept)) if kept else {}
        problems = [
            f"{key} reads {now.get(key)!r}, expected {value!r}"
            for key, value in kept.items()
            if not _close(now.get(key), value)
        ]
        restorable = {k: v for k, v in previous.items() if v is not None}
        missing = sorted(set(kept) - set(restorable))
        if missing:
            problems.append(
                "no earlier value known for " + ", ".join(missing) + ": undo cannot restore these"
            )
        measured_after = measure(ctx, copy.id)
        _journal(
            ctx,
            raw,
            (
                {"photo_id": copy.id, "settings": kept, "limited": limited},
                {"photo_id": copy.id, "settings": restorable} if restorable else {},
            ),
            None,
        )
        note = (
            "Lightroom's Auto found nothing to change: the photo already has the values it would "
            "choose."
            if not chosen
            else "Look at get_preview to judge it; refine with the adjust tools."
        )
        return planned.model_copy(
            update={
                "status": "ok",
                "working_copy": copy.id,
                "chosen_by_lightroom": chosen,
                "kept": kept,
                "limited": limited,
                "before": previous,
                "measured": {"before": measured_before, "after": measured_after},
                "verified": not problems,
                "problems": tuple(problems),
                "note": note,
            }
        )
    except DarkroomError as exc:
        _journal(ctx, raw, None, exc)
        return planned.model_copy(update={"status": "failed", "error": exc.to_dict()})


def _identical(items: Sequence[AutoItem]) -> bool:
    done = [i.kept for i in items if i.status == "ok" and i.kept]
    return len(done) > 1 and all(kept == done[0] for kept in done)


def auto_correct(ctx: ToolContext, args: AutoCorrectInput) -> Outcome:
    guard = ctx.settings.guardrails
    if len(args.photo_ids) > guard.batch_develop_max:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"Auto may run on at most {guard.batch_develop_max} photos per call; this has "
            f"{len(args.photo_ids)}. Nothing was changed.",
            next_step=f"Split it into groups of up to {guard.batch_develop_max} photos.",
        )
    batch_id = uuid.uuid4().hex[:12]
    plan = [_plan(ctx, photo_id) for photo_id in args.photo_ids]
    shared: dict[str, str] = {}
    for index, item in enumerate(plan):
        key = item.working_copy or item.photo_id
        if item.status != "invalid" and key in shared:
            plan[index] = item.model_copy(
                update={
                    "status": "invalid",
                    "error": {"message": f"same working copy as {shared[key]}"},
                }
            )
        elif item.status != "invalid":
            shared[key] = item.photo_id
    invalid = [i for i in plan if i.status == "invalid"]
    if invalid:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"{len(invalid)} of {len(plan)} photos cannot be used, so nothing was changed: "
            + " | ".join(
                f"{i.photo_id}: {(i.error or {}).get('message', 'not allowed')}" for i in invalid
            ),
            next_step="Fix or remove those photos and try again.",
        )
    if args.dry_run:
        result = AutoCorrectResult(
            batch_id=batch_id,
            dry_run=True,
            requested=len(plan),
            items=tuple(plan),
            note="Plan only: nothing was changed. Each photo would get Lightroom's Auto on its "
            "own, one after the other.",
        )
        return Outcome(result, applied={"batch_id": batch_id, "dry_run": True})
    if len(plan) > 1:
        require_confirmation(
            ctx.confirmer,
            f"run Lightroom's Auto on {len(plan)} photos, each on its own "
            f"({sum(1 for i in plan if i.creates_copy)} new virtual copies will be created; "
            "originals are never changed)",
            {"photos": [i.file_name or i.photo_id for i in plan]},
        )
    started = _clock()
    done: list[AutoItem] = []
    stopped: str | None = None
    for index, (planned) in enumerate(plan):
        if stopped is None and index > 0 and _clock() - started > guard.batch_develop_budget_s:
            stopped = f"the time budget of {guard.batch_develop_budget_s:g} s was used up"
        if stopped is not None:
            done.append(planned.model_copy(update={"status": "not_run"}))
            continue
        item = _run_one(ctx, args, batch_id, index, planned)
        done.append(item)
        if item.status == "failed":
            stopped = f"photo {item.photo_id} failed, so the rest were not run"
    count = {s: sum(1 for i in done if i.status == s) for s in ("ok", "failed", "not_run")}
    identical = _identical(done)
    note = (
        f"Stopped: {stopped}. Look at the failed item, then decide; do not retry in a loop. "
        if stopped
        else ""
    )
    note += "undo (one photo) or undo_batch with this batch_id takes the changes back."
    if identical:
        note += (
            " Every photo ended up with the same values: check the previews, because Auto is "
            "meant to give each photo its own."
        )
    result = AutoCorrectResult(
        batch_id=batch_id,
        dry_run=False,
        requested=len(plan),
        ok=count["ok"],
        failed=count["failed"],
        not_run=count["not_run"],
        items=tuple(done),
        identical_across_photos=identical,
        stopped_because=stopped,
        elapsed_s=round(_clock() - started, 2),
        note=note,
    )
    return Outcome(
        result,
        applied={"batch_id": batch_id, "ok": count["ok"], "failed": count["failed"]},
    )
