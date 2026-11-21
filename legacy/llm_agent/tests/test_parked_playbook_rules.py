"""PARKED tests (they import the parked agent code; valid only at the pre-mcp-pivot tag)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_guardrail_refusals_have_their_own_next_step(tmp_path: Path) -> None:
    from darkroom_ai.agent.loop import load_playbook
    from darkroom_ai.errors import DarkroomError

    with pytest.raises(DarkroomError) as caught:
        load_playbook("wedding")
    assert "--playbook" in caught.value.next_step



def test_raw_white_balance_is_stated_as_a_fact_without_an_order() -> None:
    """The earlier playbook line 'lighting BEFORE white balance' was read as a step to carry out
    and caused an unrequested lighting edit. It is now a fact in both modes, with no order."""
    from darkroom_ai.agent.loop import SYSTEM_FACTS, SYSTEM_RULES, load_playbook

    for name in ("portrait", "landscape"):
        text = load_playbook(name)
        assert "BEFORE white-balance" not in text and "RAW files:" not in text
    for text in (SYSTEM_RULES, SYSTEM_FACTS):
        assert "only after the copy's first edit" in text
        assert "request is refused, and the tool result says so" in text
        assert "lighting changes BEFORE" not in text and "lighting first" not in text.lower()


