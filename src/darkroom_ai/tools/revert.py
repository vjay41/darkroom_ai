"""Revert a photo's virtual copy to its values from before ANY darkroom_ai edit.

This is for the person at the keyboard (`darkroom revert`); it is deliberately NOT a tool, so
neither the model nor an MCP client can call it. The model's `undo` only reaches edits made in
its own session.

"Original" is taken from the journal: for every setting darkroom_ai ever changed on the copy, the
value it had before the first of those changes. Settings changed by hand in Lightroom are not
touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from darkroom_ai.adapters.lightroom.models import PhotoInfo
from darkroom_ai.safety.journal import JournalEntry
from darkroom_ai.tools.context import ToolContext, check_process_version, resolve_copy
from darkroom_ai.tools.lightroom_tools import pending_edits, restore_values

REVERT_TOOL = "revert"


@dataclass(frozen=True)
class RevertPlan:
    copy: PhotoInfo
    entries: tuple[JournalEntry, ...]
    settings: dict[str, Any]  # setting -> value to put back

    @property
    def sessions(self) -> int:
        return len({entry.session for entry in self.entries})


def plan_revert(ctx: ToolContext, photo_id: str) -> RevertPlan:
    """What a revert would do. Reads only."""
    copy = resolve_copy(ctx, photo_id)
    entries = tuple(pending_edits(ctx, copy.id, session=None))
    return RevertPlan(copy=copy, entries=entries, settings=restore_values(entries))


def apply_revert(ctx: ToolContext, plan: RevertPlan) -> JournalEntry:
    """Put the planned values back and record it. Call only after the person agreed."""
    check_process_version(ctx.lightroom.get_develop_settings(plan.copy.id, ["ProcessVersion"]))
    ctx.lightroom.apply_develop_settings(plan.copy.id, plan.settings, "darkroom_ai: revert")
    return ctx.journal.record(
        REVERT_TOOL,
        {"photo_id": plan.copy.id},
        applied={
            "photo_id": plan.copy.id,
            "undone": [entry.id for entry in plan.entries],
            "settings": plan.settings,
        },
    )
