from __future__ import annotations

import pytest

from darkroom_ai.errors import DarkroomError, ErrorCode

PRD_SECTION_8_CODES = {
    "LR_NOT_REACHABLE",
    "PS_NOT_REACHABLE",
    "ADOBE_FEATURE_UNAVAILABLE",
    "ADOBE_VERSION_UNSUPPORTED",
    "LLM_NOT_REACHABLE",
    "LLM_NO_VISION",
    "LLM_BAD_TOOL_CALL",
}
ADOBE_CODES = [
    ErrorCode.LR_NOT_REACHABLE,
    ErrorCode.PS_NOT_REACHABLE,
    ErrorCode.ADOBE_FEATURE_UNAVAILABLE,
    ErrorCode.ADOBE_VERSION_UNSUPPORTED,
]
# Words that would claim a certain license diagnosis or hint at license handling.
FORBIDDEN_PHRASES = ["your license is", "license expired", "not licensed", "activate", "crack"]


def test_prd_codes_exist() -> None:
    assert {code.value for code in ErrorCode} >= PRD_SECTION_8_CODES


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_code_has_message_and_next_step(code: ErrorCode) -> None:
    error = DarkroomError(code)
    assert len(error.message) > 10
    assert len(error.next_step) > 10
    assert str(error).startswith(f"[{code.value}] ")
    assert "Next step:" in str(error)


@pytest.mark.parametrize("code", list(ErrorCode))
def test_messages_never_claim_a_license_diagnosis(code: ErrorCode) -> None:
    text = str(DarkroomError(code)).lower()
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in text
    if "license" in text:
        assert "likely" in text


def test_not_reachable_messages_mention_a_waiting_dialog() -> None:
    for code in (ErrorCode.LR_NOT_REACHABLE, ErrorCode.PS_NOT_REACHABLE):
        assert "dialog" in DarkroomError(code).next_step


def test_overrides_and_dict() -> None:
    error = DarkroomError(
        ErrorCode.ADOBE_VERSION_UNSUPPORTED,
        "Photoshop 99.0 detected; tested: 27.8.",
        next_step="Use a tested version.",
        details={"detected": "99.0", "tested": ["27.8"]},
    )
    assert error.to_dict() == {
        "code": "ADOBE_VERSION_UNSUPPORTED",
        "message": "Photoshop 99.0 detected; tested: 27.8.",
        "next_step": "Use a tested version.",
        "details": {"detected": "99.0", "tested": ["27.8"]},
    }
    assert isinstance(error, Exception)
