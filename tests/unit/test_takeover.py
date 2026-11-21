"""Taking over Lightroom politely: wait while the person is busy, ask, then retry."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools import takeover


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class Lightroom:
    """Active photo follows `script` (one entry per get_selection call; the last one repeats)."""

    def __init__(self, script: list[str | None]) -> None:
        self.script = script
        self.calls = 0

    def get_selection(self) -> Any:
        value = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return SimpleNamespace(active=SimpleNamespace(id=value) if value else None)


class Confirmer:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def confirm(self, action: str, details: Any) -> bool:
        self.asked.append(action)
        return self.answer


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(takeover, "_now", fake.now)
    monkeypatch.setattr(takeover, "_sleep", fake.sleep)
    monkeypatch.setattr(takeover, "STABLE_S", 4.0)
    return fake


def ctx(script: list[str | None], answer: bool = True) -> Any:
    return SimpleNamespace(lightroom=Lightroom(script), confirmer=Confirmer(answer))


def test_takes_over_straight_away_when_lightroom_already_shows_the_photo(clock: Clock) -> None:
    value, waited = takeover.run_with_takeover(ctx(["a"]), "a", lambda: "ok", what="x")
    assert (value, waited) == ("ok", 0.0)


def test_waits_for_a_busy_user_then_takes_over_on_its_own(clock: Clock) -> None:
    # the person keeps changing the selection for a while, then stops
    script = ["p1", "p2", "p3", "p4", "p4"]
    context = ctx(script)
    value, waited = takeover.run_with_takeover(context, "a", lambda: "ok", what="x")
    assert value == "ok"
    assert waited >= takeover.STABLE_S
    assert context.confirmer.asked == []  # no need to bother the person


def test_retries_when_lightroom_reports_busy_then_succeeds(clock: Clock) -> None:
    attempts = {"n": 0}

    def action() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise DarkroomError(ErrorCode.LR_BUSY_USER)
        return "done"

    value, _ = takeover.run_with_takeover(ctx(["a"]), "a", action, what="x")
    assert value == "done" and attempts["n"] == 3


def test_asks_the_person_after_the_automatic_wait_and_retries(clock: Clock) -> None:
    changing = [f"p{i}" for i in range(100)]  # never settles during the automatic wait
    context = ctx(changing)
    # after the person confirms, the selection settles
    original = context.confirmer.confirm

    def confirm(action: str, details: Any) -> bool:
        context.lightroom.script = ["calm"]
        context.lightroom.calls = 0
        return original(action, details)

    context.confirmer.confirm = confirm
    value, waited = takeover.run_with_takeover(context, "a", lambda: "ok", what="adjust mask")
    assert value == "ok" and waited >= takeover.AUTO_WAIT_S
    assert len(context.confirmer.asked) == 1 and "Lightroom is in use" in context.confirmer.asked[0]


def test_gives_up_with_lr_busy_user_when_the_person_declines(clock: Clock) -> None:
    context = ctx([f"p{i}" for i in range(100)], answer=False)
    with pytest.raises(DarkroomError) as caught:
        takeover.run_with_takeover(context, "a", lambda: "ok", what="x")
    assert caught.value.code is ErrorCode.LR_BUSY_USER


def test_other_errors_are_not_swallowed(clock: Clock) -> None:
    def action() -> str:
        raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, "nope")

    with pytest.raises(DarkroomError) as caught:
        takeover.run_with_takeover(ctx(["a"]), "a", action, what="x")
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
