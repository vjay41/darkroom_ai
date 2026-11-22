"""copy_develop_settings: carry chosen groups of one photo's look over to other photos.

This is the opposite of auto_correct: the SAME values go to every target, so it suits a shared look
(for example the colour grading of one wedding scene). Exposure and white balance depend on each
photo's light, so they are separate groups the caller must ask for. Crop and geometry are never
copied. Each target is edited on its own working copy, read back, journaled and undoable.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.safety.confirm import require_confirmation
from darkroom_ai.tools.context import (
    Outcome,
    ToolContext,
    ToolResult,
    check_process_version,
    resolve_copy,
    working_copy,
)
from darkroom_ai.tools.develop_keys import HSL_COLORS, HSL_PREFIX
from darkroom_ai.tools.develop_parameters import DEVELOP_PARAMETERS

TOOL_NAME = "copy_develop_settings"
Group = Literal[
    "white_balance",
    "tone",
    "presence",
    "hsl",
    "color_grading",
    "detail",
    "lens",
    "effects",
    "calibration",
]
WHITE_BALANCE = ("Temperature", "Tint", "IncrementalTemperature", "IncrementalTint")
TONE = (
    "Exposure2012",
    "Contrast2012",
    "Highlights2012",
    "Shadows2012",
    "Whites2012",
    "Blacks2012",
)
PANEL_OF_GROUP = {
    "presence": "basic",
    "color_grading": "color_grading",
    "detail": "detail",
    "lens": "lens",
    "effects": "effects",
    "calibration": "calibration",
}


def group_keys(group: str) -> tuple[str, ...]:
    if group == "white_balance":
        return WHITE_BALANCE
    if group == "tone":
        return TONE
    if group == "hsl":
        return tuple(
            f"{prefix}{color.capitalize()}"
            for prefix in HSL_PREFIX.values()
            for color in HSL_COLORS
        )
    panel = PANEL_OF_GROUP[group]
    return tuple(key for key, entry in DEVELOP_PARAMETERS.items() if entry[0] == panel)


class CopyDevelopSettingsInput(BaseModel):
    """Copy chosen groups of develop settings from one photo to up to 10 others, giving them all
    the SAME values (use auto_correct instead when each photo should get values of its own).
    Groups: white_balance, tone (exposure and tone sliders), presence, hsl, color_grading,
    detail, lens, effects, calibration. Crop, geometry and local masks are never copied. Targets
    are edited on their working copies; the person's go-ahead is required; `undo` and
    `undo_batch` take it back."""

    model_config = ConfigDict(extra="forbid")

    source_photo_id: str = Field(min_length=1)
    target_photo_ids: list[str] = Field(min_length=1, max_length=10)
    groups: list[Group] = Field(min_length=1)

    @field_validator("target_photo_ids")
    @classmethod
    def _unique(cls, ids: list[str]) -> list[str]:
        if len(set(ids)) != len(ids) or any(not i.strip() for i in ids):
            raise ValueError("list each target once")
        return ids


class CopyItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    photo_id: str
    status: Literal["ok", "failed", "not_run"]
    working_copy: str | None = None
    changed: dict[str, Any] = {}
    verified: bool | None = None
    problems: tuple[str, ...] = ()
    error: dict[str, Any] | None = None


class CopyDevelopSettingsResult(ToolResult):
    batch_id: str
    source: str
    copied_keys: list[str]
    items: tuple[CopyItem, ...]
    note: str = (
        "Every target got the same values. White balance and exposure depend on each photo's "
        "light: check the previews. undo_batch with this batch_id takes it back."
    )


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) < 1e-3
    return bool(a == b)


def copy_develop_settings(ctx: ToolContext, args: CopyDevelopSettingsInput) -> Outcome:
    limit = ctx.settings.guardrails.batch_develop_max
    if len(args.target_photo_ids) > limit:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"At most {limit} targets per call. Nothing was changed.",
            next_step=f"Split it into groups of up to {limit} photos.",
        )
    source = resolve_copy(ctx, args.source_photo_id)
    keys = sorted({key for group in args.groups for key in group_keys(group)})
    values = {
        k: v
        for k, v in ctx.lightroom.get_develop_settings(source.id, keys).items()
        if v is not None
    }
    if not values:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            "The source photo reports no values for those groups, so there is nothing to copy.",
        )
    targets = [resolve_copy(ctx, t) for t in args.target_photo_ids]
    if any(t.id == source.id or t.id == args.source_photo_id for t in targets):
        raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, "The source cannot also be a target.")
    require_confirmation(
        ctx.confirmer,
        f"copy {len(values)} develop settings ({', '.join(args.groups)}) from {source.file_name} "
        f"to {len(targets)} photos (the same values on each)",
        {"source": source.id, "targets": [t.file_name for t in targets], "groups": args.groups},
    )
    batch_id = uuid.uuid4().hex[:12]
    items: list[CopyItem] = []
    stopped = False
    for index, planned in enumerate(args.target_photo_ids):
        if stopped:
            items.append(CopyItem(photo_id=planned, status="not_run"))
            continue
        raw = {
            "photo_id": planned,
            "source": source.id,
            "groups": args.groups,
            "batch_id": batch_id,
            "batch_index": index,
        }
        try:
            copy, _created = working_copy(ctx, planned)
            check_process_version(ctx.lightroom.get_develop_settings(copy.id, ["ProcessVersion"]))
            previous = ctx.lightroom.get_develop_settings(copy.id, list(values))
            ctx.lightroom.apply_develop_settings(copy.id, values, "darkroom_ai: copy settings")
            now = ctx.lightroom.get_develop_settings(copy.id, list(values))
            problems = tuple(
                f"{k} reads {now.get(k)!r}, expected {v!r}"
                for k, v in values.items()
                if not _close(now.get(k), v)
            )
            restore = {k: v for k, v in previous.items() if v is not None}
            ctx.journal.record(
                TOOL_NAME,
                raw,
                applied={"photo_id": copy.id, "settings": values},
                undo={"photo_id": copy.id, "settings": restore},
            )
            ctx.measurements.pop(copy.id, None)
            items.append(
                CopyItem(
                    photo_id=planned,
                    status="ok",
                    working_copy=copy.id,
                    changed={k: {"before": previous.get(k), "after": v} for k, v in values.items()},
                    verified=not problems,
                    problems=problems,
                )
            )
        except DarkroomError as exc:
            ctx.journal.record(TOOL_NAME, raw, status="error", error=exc.to_dict())
            items.append(CopyItem(photo_id=planned, status="failed", error=exc.to_dict()))
            stopped = True
    result = CopyDevelopSettingsResult(
        batch_id=batch_id, source=source.id, copied_keys=sorted(values), items=tuple(items)
    )
    return Outcome(result, applied={"batch_id": batch_id, "targets": len(items)})
