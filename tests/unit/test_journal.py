from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from darkroom_ai.interfaces import ActionJournal
from darkroom_ai.safety.journal import JsonlJournal
from fakes import InMemoryJournal


def _use(journal: ActionJournal) -> None:
    """Code under test depends on the Protocol only; both implementations must fit it."""
    journal.record("adjust_lighting", {"exposure": 0.4}, undo={"snapshot": "before-1"})
    journal.record("crop", {"aspect": "4x5"}, status="error", error={"code": "TOOL_TIMEOUT"})


@pytest.mark.parametrize("kind", ["jsonl", "memory"])
def test_record_and_read_back(kind: str, tmp_path: Path) -> None:
    journal: ActionJournal = (
        JsonlJournal(tmp_path / "logs" / "journal.jsonl", session="s1")
        if kind == "jsonl"
        else InMemoryJournal(session="s1")
    )
    _use(journal)
    first, second = journal.entries()
    assert (first.tool, first.status, first.session) == ("adjust_lighting", "ok", "s1")
    assert first.args == {"exposure": 0.4}
    assert first.undo == {"snapshot": "before-1"}
    assert first.undoable
    assert second.status == "error"
    assert second.error == {"code": "TOOL_TIMEOUT"}
    assert not second.undoable
    assert first.id != second.id


def test_file_is_append_only_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    fixed = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
    JsonlJournal(path, session="a", clock=lambda: fixed).record("one", {})
    before = path.read_text(encoding="utf-8")
    JsonlJournal(path, session="b", clock=lambda: fixed).record("two", {"n": 1})
    after = path.read_text(encoding="utf-8")

    assert after.startswith(before)
    lines = after.splitlines()
    assert [json.loads(line)["tool"] for line in lines] == ["one", "two"]
    assert json.loads(lines[0])["ts"] == "2026-11-20T12:00:00.000+00:00"


def test_last_undoable(tmp_path: Path) -> None:
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    assert journal.entries() == []
    assert journal.last_undoable() is None
    journal.record("get_photo_info", {})
    target = journal.record("adjust_lighting", {"exposure": 0.3}, undo={"snapshot": "s"})
    journal.record("adjust_hsl", {}, status="error", undo={"snapshot": "t"})
    found = journal.last_undoable()
    assert found is not None
    assert found.id == target.id


def test_caller_changes_do_not_alter_entry(tmp_path: Path) -> None:
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    args = {"exposure": 0.3}
    entry = journal.record("adjust_lighting", args)
    args["exposure"] = 9.9
    assert entry.args == {"exposure": 0.3}


def test_corrupt_line_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    journal = JsonlJournal(path)
    journal.record("one", {})
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    with pytest.raises(ValueError, match="line 2"):
        journal.entries()


def test_concurrent_writes_keep_every_line(tmp_path: Path) -> None:
    journal = JsonlJournal(tmp_path / "journal.jsonl")

    def work(worker: int) -> None:
        for step in range(10):
            journal.record("tool", {"worker": worker, "step": step})

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(journal.entries()) == 40
