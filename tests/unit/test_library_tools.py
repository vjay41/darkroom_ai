"""Library tools (rating, flag, keywords): virtual copies only, read back, undoable."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_tools import MASTER, Rig

from darkroom_ai.errors import DarkroomError, ErrorCode


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def make_copy(rig: Rig) -> str:
    """A working copy, made the way the model gets one: by editing the master."""
    return str(rig.call("adjust_lighting", photo_id=MASTER, exposure=0.1)["photo_id"])


def state(rig: Rig, photo_id: str) -> dict[str, Any]:
    return rig.call("get_library_state", photo_id=photo_id)


def test_new_copy_starts_unrated_unflagged_without_keywords(rig: Rig) -> None:
    copy = make_copy(rig)
    result = state(rig, copy)
    assert (result["rating"], result["flag"], result["keywords"]) == (None, "none", [])


def test_set_rating_flag_and_keywords_are_read_back(rig: Rig) -> None:
    copy = make_copy(rig)
    result = rig.call(
        "set_library_state",
        photo_id=copy,
        rating=4,
        flag="pick",
        add_keywords=["Wedding", " bride "],
    )
    assert result["verified"] is True
    assert result["problems"] == []
    assert result["before"]["rating"] is None
    assert result["after"]["rating"] == 4
    assert result["after"]["flag"] == "pick"
    assert result["after"]["keywords"] == ["bride", "Wedding"]
    assert state(rig, copy)["keywords"] == ["bride", "Wedding"]


def test_a_master_photo_is_refused_and_nothing_changes(rig: Rig) -> None:
    with pytest.raises(DarkroomError) as caught:
        rig.call("set_library_state", photo_id=MASTER, rating=5)
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "virtual copies" in str(caught.value)
    assert state(rig, MASTER)["rating"] is None


def test_a_master_with_a_working_copy_changes_the_copy_not_the_master(rig: Rig) -> None:
    copy = make_copy(rig)
    result = rig.call("set_library_state", photo_id=MASTER, rating=2)
    assert result["photo_id"] == copy
    assert state(rig, copy)["rating"] == 2
    assert state(rig, MASTER)["rating"] is None


def test_rating_zero_clears_and_keywords_match_case_insensitively(rig: Rig) -> None:
    copy = make_copy(rig)
    rig.call("set_library_state", photo_id=copy, rating=3, add_keywords=["Sunset"])
    result = rig.call(
        "set_library_state",
        photo_id=copy,
        rating=0,
        add_keywords=["sunset"],
        remove_keywords=["SUNSET"],
    )
    assert result["after"]["rating"] is None
    assert result["after"]["keywords"] == []


def test_a_change_that_changes_nothing_says_so_and_records_no_undo(rig: Rig) -> None:
    copy = make_copy(rig)
    result = rig.call("set_library_state", photo_id=copy, flag="none")
    assert "nothing changed" in result["note"]
    undone = rig.call("undo_library_change", photo_id=copy)
    assert undone["undone_steps"] == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {},  # nothing requested
        {"rating": 6},
        {"rating": -1},
        {"flag": "star"},
        {"add_keywords": [""]},
        {"add_keywords": ["x" * 65]},
        {"add_keywords": [f"k{i}" for i in range(21)]},
        {"surprise": True},
    ],
)
def test_bad_arguments_are_rejected_before_anything_is_sent(
    rig: Rig, arguments: dict[str, Any]
) -> None:
    copy = make_copy(rig)
    calls = len(rig.lightroom.calls)
    with pytest.raises(DarkroomError) as caught:
        rig.call("set_library_state", photo_id=copy, **arguments)
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL
    assert "set_library_state" not in rig.lightroom.calls[calls:]


def test_undo_last_and_all_restore_the_exact_previous_values(rig: Rig) -> None:
    copy = make_copy(rig)
    rig.call("set_library_state", photo_id=copy, rating=2, add_keywords=["a"])
    rig.call("set_library_state", photo_id=copy, rating=5, flag="reject", add_keywords=["b"])
    rig.call("set_library_state", photo_id=copy, remove_keywords=["a"])

    last = rig.call("undo_library_change", photo_id=copy)
    assert last["undone_steps"] == 1
    assert last["after"]["keywords"] == ["a", "b"]

    everything = rig.call("undo_library_change", photo_id=copy, scope="all")
    assert everything["undone_steps"] == 2
    assert (everything["after"]["rating"], everything["after"]["flag"]) == (None, "none")
    assert everything["after"]["keywords"] == []

    assert rig.call("undo_library_change", photo_id=copy)["undone_steps"] == 0


def test_library_undo_does_not_touch_develop_edits_and_develop_undo_ignores_library(
    rig: Rig,
) -> None:
    copy = make_copy(rig)
    rig.call("set_library_state", photo_id=copy, rating=3)
    assert rig.call("undo", photo_id=copy)["undone_steps"] == 1  # the exposure edit only
    assert state(rig, copy)["rating"] == 3
    rig.call("undo_library_change", photo_id=copy)
    assert state(rig, copy)["rating"] is None


def test_the_simulated_lightroom_refuses_library_writes_on_a_master(rig: Rig) -> None:
    with pytest.raises(DarkroomError) as caught:
        rig.lightroom.set_library_state(MASTER, rating=1)
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
