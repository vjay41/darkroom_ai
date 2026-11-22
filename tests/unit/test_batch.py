"""Batch writes: plan first, one go-ahead, stop at the first failure, one undo for the batch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_tools import MASTER, Rig, make_settings

from darkroom_ai.errors import DarkroomError, ErrorCode
from fakes import AllowAll


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def add_masters(rig: Rig, count: int) -> list[str]:
    """The test master plus extra masters; returns all master ids."""
    ids = [MASTER]
    for number in range(2, count + 1):
        photo_id = f"master-{number}"
        rig.lightroom.add_photo(photo_id, f"TEST_{number:04d}.jpg")
        ids.append(photo_id)
    return ids


def copies_for(rig: Rig, masters: list[str]) -> list[str]:
    """Working copies made the way the model gets them: by a first edit of each master."""
    return [
        str(rig.call("adjust_lighting", photo_id=master, exposure=0.1)["photo_id"])
        for master in masters
    ]


def library(rig: Rig, photo_id: str) -> tuple[Any, str, list[str]]:
    got = rig.call("get_library_state", photo_id=photo_id)
    return got["rating"], got["flag"], got["keywords"]


def steps_of(rig: Rig, batch_id: str) -> list[Any]:
    return [e for e in rig.journal.entries() if e.args.get("batch_id") == batch_id]


# -- library batches ------------------------------------------------------------------------------


def test_library_dry_run_shows_the_plan_and_changes_nothing(rig: Rig) -> None:
    copy = copies_for(rig, [MASTER])[0]
    result = rig.call(
        "batch_set_library_state",
        items=[{"photo_id": copy, "rating": 4, "flag": "pick"}],
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert [i["status"] for i in result["items"]] == ["planned"]
    assert result["items"][0]["plan"] == "rating 4; flag pick"
    assert library(rig, copy) == (None, "none", [])
    assert steps_of(rig, result["batch_id"]) == []


def test_a_small_library_batch_runs_with_different_values_per_photo(rig: Rig) -> None:
    first, second = copies_for(rig, add_masters(rig, 2))
    result = rig.call(
        "batch_set_library_state",
        items=[
            {"photo_id": first, "rating": 5, "add_keywords": ["keeper"]},
            {"photo_id": second, "flag": "reject"},
        ],
    )
    assert (result["ok"], result["failed"], result["not_run"]) == (2, 0, 0)
    assert result["stopped_because"] is None
    assert library(rig, first) == (5, "none", ["keeper"])
    assert library(rig, second) == (None, "reject", [])
    assert all(i["verified"] for i in result["items"])
    assert len(steps_of(rig, result["batch_id"])) == 2


def test_one_invalid_item_means_nothing_runs(rig: Rig) -> None:
    masters = add_masters(rig, 2)
    copy = copies_for(rig, [masters[0]])[0]
    with pytest.raises(DarkroomError) as caught:
        rig.call(
            "batch_set_library_state",
            items=[
                {"photo_id": copy, "rating": 3},
                {"photo_id": "no-such-photo", "rating": 3},
            ],
        )
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "nothing was changed" in str(caught.value)
    assert library(rig, copy)[0] is None


def test_the_plan_marks_the_invalid_item_in_a_dry_run(rig: Rig) -> None:
    masters = add_masters(rig, 2)
    copy = copies_for(rig, [masters[0]])[0]
    result = rig.call(
        "batch_set_library_state",
        items=[{"photo_id": copy, "rating": 3}, {"photo_id": "no-such-photo", "rating": 3}],
        dry_run=True,
    )
    assert [i["status"] for i in result["items"]] == ["planned", "invalid"]
    assert result["invalid"] == 1


def test_a_library_batch_over_the_threshold_needs_the_go_ahead(tmp_path: Path) -> None:
    rig = Rig(tmp_path)  # default confirmer refuses everything
    copies = copies_for(rig, add_masters(rig, 6))
    items = [{"photo_id": c, "rating": 2} for c in copies]
    with pytest.raises(DarkroomError) as caught:
        rig.call("batch_set_library_state", items=items)
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    assert all(library(rig, c)[0] is None for c in copies)

    allow = AllowAll()
    rig = Rig(tmp_path / "again", confirmer=allow)
    copies = copies_for(rig, add_masters(rig, 6))
    result = rig.call(
        "batch_set_library_state", items=[{"photo_id": c, "rating": 2} for c in copies]
    )
    assert result["ok"] == 6
    assert len(allow.asked) == 1 and "6 photos" in allow.asked[0]


def test_a_library_batch_at_or_below_the_threshold_asks_nobody(rig: Rig) -> None:
    copies = copies_for(rig, add_masters(rig, 5))
    result = rig.call(
        "batch_set_library_state", items=[{"photo_id": c, "flag": "pick"} for c in copies]
    )
    assert result["ok"] == 5


def test_the_run_stops_at_the_first_failure(rig: Rig) -> None:
    copies = copies_for(rig, add_masters(rig, 3))
    real = rig.lightroom.set_library_state
    calls: list[str] = []

    def flaky(photo_id: str, **kwargs: Any) -> Any:
        calls.append(photo_id)
        if len(calls) == 2:
            raise DarkroomError(ErrorCode.LR_COMMAND_FAILED, "Lightroom said no")
        return real(photo_id, **kwargs)

    rig.lightroom.set_library_state = flaky  # type: ignore[method-assign,assignment]
    result = rig.call(
        "batch_set_library_state", items=[{"photo_id": c, "rating": 4} for c in copies]
    )
    assert [i["status"] for i in result["items"]] == ["ok", "failed", "not_run"]
    assert result["items"][1]["error"]["code"] == "LR_COMMAND_FAILED"
    assert "photo" in result["stopped_because"] and "not run" in result["stopped_because"]
    assert calls == copies[:2]  # the third photo was never touched
    assert library(rig, copies[0])[0] == 4
    errors = [e for e in steps_of(rig, result["batch_id"]) if e.status == "error"]
    assert len(errors) == 1


def test_the_time_budget_stops_a_batch(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.context.settings = make_settings(rig.tmp_path, guardrails={"batch_library_budget_s": 1})
    ticks = iter([0.0, 0.5, 2.0, 3.0, 4.0, 5.0])
    monkeypatch.setattr("darkroom_ai.tools.batch._clock", lambda: next(ticks))
    copies = copies_for(rig, add_masters(rig, 3))
    result = rig.call(
        "batch_set_library_state", items=[{"photo_id": c, "rating": 1} for c in copies]
    )
    assert [i["status"] for i in result["items"]] == ["ok", "ok", "not_run"]
    assert "time budget" in result["stopped_because"]


def test_undo_batch_takes_a_library_batch_back_and_reads_it_back(rig: Rig) -> None:
    first, second = copies_for(rig, add_masters(rig, 2))
    rig.call("set_library_state", photo_id=first, rating=1)  # before the batch: must survive
    batch = rig.call(
        "batch_set_library_state",
        items=[
            {"photo_id": first, "rating": 5, "add_keywords": ["a"]},
            {"photo_id": second, "flag": "pick"},
        ],
    )
    undone = rig.call("undo_batch", batch_id=batch["batch_id"])
    assert undone["undone_steps"] == 2
    assert all(p["verified"] for p in undone["photos"])
    assert library(rig, first) == (1, "none", [])
    assert library(rig, second) == (None, "none", [])

    again = rig.call("undo_batch", batch_id=batch["batch_id"])
    assert again["undone_steps"] == 0
    assert "nothing left" in again["note"]
    single = rig.call("undo_library_change", photo_id=second, scope="all")
    assert single["undone_steps"] == 0  # the batch undo is known to the single-photo undo


def test_undo_batch_with_an_unknown_id_is_refused(rig: Rig) -> None:
    with pytest.raises(DarkroomError) as caught:
        rig.call("undo_batch", batch_id="nope")
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED


def test_a_batch_from_an_earlier_session_is_left_to_the_person(rig: Rig) -> None:
    (copy,) = copies_for(rig, [MASTER])
    batch = rig.call("batch_set_library_state", items=[{"photo_id": copy, "rating": 2}])
    rig.journal.session = "a-later-session"
    undone = rig.call("undo_batch", batch_id=batch["batch_id"])
    assert undone["undone_steps"] == 0
    assert "earlier session" in undone["note"]
    assert library(rig, copy)[0] == 2


def test_too_many_photos_are_refused_up_front(rig: Rig) -> None:
    items = [{"photo_id": f"p{n}", "rating": 1} for n in range(26)]
    with pytest.raises(DarkroomError) as caught:
        rig.call("batch_set_library_state", items=items)
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL
    with pytest.raises(DarkroomError) as caught:
        rig.call(
            "batch_set_develop_parameters",
            items=[{"photo_id": f"p{n}", "exposure": 0.1} for n in range(11)],
        )
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "at most 10" in str(caught.value)


def test_a_master_and_its_own_copy_are_two_photos_in_a_library_batch(rig: Rig) -> None:
    (copy,) = copies_for(rig, [MASTER])
    result = rig.call(
        "batch_set_library_state",
        items=[{"photo_id": copy, "rating": 1}, {"photo_id": MASTER, "rating": 2}],
    )
    assert result["ok"] == 2
    assert library(rig, copy)[0] == 1 and library(rig, MASTER)[0] == 2


def test_the_same_photo_twice_is_refused(rig: Rig) -> None:
    (copy,) = copies_for(rig, [MASTER])
    with pytest.raises(DarkroomError) as caught:
        rig.call(
            "batch_set_library_state",
            items=[{"photo_id": copy, "rating": 1}, {"photo_id": copy, "rating": 2}],
        )
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL


# -- develop batches ------------------------------------------------------------------------------


def test_a_develop_batch_always_needs_the_go_ahead(rig: Rig) -> None:
    with pytest.raises(DarkroomError) as caught:
        rig.call("batch_set_develop_parameters", items=[{"photo_id": MASTER, "exposure": 0.2}])
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    assert "create_virtual_copy" not in rig.lightroom.calls
    assert rig.value(MASTER, "Exposure2012") == 0


def test_a_develop_batch_edits_working_copies_and_never_the_masters(tmp_path: Path) -> None:
    allow = AllowAll()
    rig = Rig(tmp_path, confirmer=allow)
    masters = add_masters(rig, 2)
    result = rig.call(
        "batch_set_develop_parameters",
        items=[
            {"photo_id": masters[0], "exposure": 0.3, "highlights": -10},
            {"photo_id": masters[1], "exposure": -0.2, "parameters": {"Clarity2012": 10}},
        ],
    )
    assert (result["ok"], result["failed"], result["copies_created"]) == (2, 0, 2)
    assert len(allow.asked) == 1 and "2 new virtual copies" in allow.asked[0]
    first, second = (i["working_copy"] for i in result["items"])
    assert rig.value(first, "Exposure2012") == pytest.approx(0.3)
    assert rig.value(first, "Highlights2012") == -10
    assert rig.value(second, "Exposure2012") == pytest.approx(-0.2)
    assert rig.value(second, "Clarity2012") == 10
    for master in masters:
        assert rig.value(master, "Exposure2012") == 0
    assert all(rig.lightroom.photos[i]["is_virtual_copy"] for i in (first, second))
    assert len(steps_of(rig, result["batch_id"])) == 3  # lighting; lighting and a parameter


def test_values_are_still_clamped_inside_a_batch(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    result = rig.call("batch_set_develop_parameters", items=[{"photo_id": MASTER, "exposure": 5.0}])
    step = result["items"][0]["steps"][0]
    assert step["clamped"] is True
    assert step["changes"][0]["applied_change"] == pytest.approx(1.5)


def test_a_develop_dry_run_creates_nothing_and_says_how_many_copies(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    result = rig.call(
        "batch_set_develop_parameters",
        items=[{"photo_id": MASTER, "exposure": 0.2}],
        dry_run=True,
    )
    assert result["copies_to_create"] == 1
    assert result["items"][0]["creates_copy"] is True
    assert "create_virtual_copy" not in rig.lightroom.calls


def test_a_master_and_its_own_copy_in_one_batch_are_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    (copy,) = copies_for(rig, [MASTER])
    with pytest.raises(DarkroomError) as caught:
        rig.call(
            "batch_set_develop_parameters",
            items=[
                {"photo_id": MASTER, "exposure": 0.1},
                {"photo_id": copy, "contrast": 5},
            ],
        )
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "same working copy" in str(caught.value)


def test_a_failed_develop_item_stops_the_batch_and_undo_restores_the_rest(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    masters = add_masters(rig, 3)
    result = rig.call(
        "batch_set_develop_parameters",
        items=[
            {"photo_id": masters[0], "exposure": 0.4},
            {"photo_id": masters[1], "parameters": {"NotASetting": 5}},
            {"photo_id": masters[2], "exposure": 0.4},
        ],
    )
    assert [i["status"] for i in result["items"]] == ["ok", "failed", "not_run"]
    assert result["items"][1]["error"]["code"] == "GUARDRAIL_REJECTED"
    assert result["items"][1]["error"]["step"] == "set_develop_parameters"
    first = result["items"][0]["working_copy"]
    assert rig.value(first, "Exposure2012") == pytest.approx(0.4)

    undone = rig.call("undo_batch", batch_id=result["batch_id"])
    assert undone["undone_steps"] >= 1
    assert all(p["verified"] for p in undone["photos"])
    assert rig.value(first, "Exposure2012") == pytest.approx(0)
    assert "stay in the catalog" in undone["note"]
    nothing = rig.call("undo", photo_id=first, scope="all")
    assert nothing["undone_steps"] == 0  # already taken back by the batch undo
