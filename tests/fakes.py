"""Fake collaborators for tests. They satisfy the Protocols in darkroom_ai.interfaces."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from darkroom_ai.safety.journal import JournalEntry, JournalStatus, new_entry


class InMemoryJournal:
    """ActionJournal that keeps entries in a list."""

    def __init__(self, session: str = "test-session") -> None:
        self.session = session
        self._entries: list[JournalEntry] = []

    def record(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        status: JournalStatus = "ok",
        applied: Mapping[str, Any] | None = None,
        undo: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> JournalEntry:
        entry = new_entry(
            self.session, tool, args, status=status, applied=applied, undo=undo, error=error
        )
        self._entries.append(entry)
        return entry

    def entries(self) -> list[JournalEntry]:
        return list(self._entries)
