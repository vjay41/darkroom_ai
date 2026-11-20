"""Small Protocol interfaces used for dependency injection.

The agent and the tools receive their collaborators (Lightroom adapter, Photoshop adapter, LLM
client, imaging toolkit, journal) through constructors and depend only on these Protocols, so they
can be tested with fakes and without Adobe apps or a model.

Only the journal interface exists so far. The others are added in the phase that first verifies
their real behavior (Lightroom: phase 2, Photoshop: phase 3, imaging: phase 4, LLM: phase 6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from darkroom_ai.safety.journal import JournalEntry, JournalStatus


class ActionJournal(Protocol):
    """Append-only record of every action, with what is needed to undo it."""

    def record(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        status: JournalStatus = "ok",
        applied: Mapping[str, Any] | None = None,
        undo: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> JournalEntry: ...

    def entries(self) -> Sequence[JournalEntry]: ...
