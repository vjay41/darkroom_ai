"""Action journal: an append-only JSONL file with one line per action.

Each entry records what was asked (`args`), what was really applied after guardrails (`applied`),
and what is needed to reverse it (`undo`, e.g. a snapshot name). The file is only ever appended to.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

JournalStatus = Literal["ok", "error"]


@dataclass(frozen=True, slots=True)
class JournalEntry:
    id: str
    ts: str
    session: str
    tool: str
    status: JournalStatus
    args: dict[str, Any]
    applied: dict[str, Any] | None = None
    undo: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def undoable(self) -> bool:
        return self.status == "ok" and self.undo is not None


def new_entry(
    session: str,
    tool: str,
    args: Mapping[str, Any],
    *,
    status: JournalStatus = "ok",
    applied: Mapping[str, Any] | None = None,
    undo: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> JournalEntry:
    return JournalEntry(
        id=uuid.uuid4().hex,
        ts=(now or datetime.now(UTC)).isoformat(timespec="milliseconds"),
        session=session,
        tool=tool,
        status=status,
        args=dict(args),
        applied=None if applied is None else dict(applied),
        undo=None if undo is None else dict(undo),
        error=None if error is None else dict(error),
    )


class JsonlJournal:
    """Journal stored as JSON lines. Safe to share between threads of one process."""

    def __init__(
        self,
        path: Path,
        *,
        session: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = path
        self.session = session or uuid.uuid4().hex
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

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
            self.session,
            tool,
            args,
            status=status,
            applied=applied,
            undo=undo,
            error=error,
            now=self._clock(),
        )
        line = json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return entry

    def entries(self) -> list[JournalEntry]:
        if not self.path.is_file():
            return []
        result: list[JournalEntry] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    result.append(JournalEntry(**json.loads(line)))
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"{self.path}: line {number} is not a journal entry") from exc
        return result

    def last_undoable(self) -> JournalEntry | None:
        """The most recent successful entry that carries undo information, if any."""
        for entry in reversed(self.entries()):
            if entry.undoable:
                return entry
        return None


class MemoryJournal:
    """Journal that keeps its entries in memory only (simulated runs and tests)."""

    def __init__(self, session: str | None = None) -> None:
        self.session = session or uuid.uuid4().hex
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
