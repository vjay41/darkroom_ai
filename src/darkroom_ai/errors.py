"""Error taxonomy (PRD section 8).

Every user-facing failure is a DarkroomError with a stable code, a plain message and a next step.
There is no license logic anywhere: Adobe-related messages say "likely" and point the user to the
Adobe app; they never claim a certain diagnosis.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # Adobe apps (PRD s8)
    LR_NOT_REACHABLE = "LR_NOT_REACHABLE"
    PS_NOT_REACHABLE = "PS_NOT_REACHABLE"
    ADOBE_FEATURE_UNAVAILABLE = "ADOBE_FEATURE_UNAVAILABLE"
    ADOBE_VERSION_UNSUPPORTED = "ADOBE_VERSION_UNSUPPORTED"
    # Lightroom bridge plug-in
    LR_WRONG_CATALOG = "LR_WRONG_CATALOG"
    LR_COMMAND_FAILED = "LR_COMMAND_FAILED"
    # Photoshop adapter
    PS_COMMAND_FAILED = "PS_COMMAND_FAILED"
    # Local model server (PRD s8)
    LLM_NOT_REACHABLE = "LLM_NOT_REACHABLE"
    LLM_NO_VISION = "LLM_NO_VISION"
    LLM_BAD_TOOL_CALL = "LLM_BAD_TOOL_CALL"
    # darkroom_ai itself
    CONFIG_INVALID = "CONFIG_INVALID"
    NON_LOCAL_ENDPOINT = "NON_LOCAL_ENDPOINT"
    GUARDRAIL_REJECTED = "GUARDRAIL_REJECTED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    MASK_MODEL_MISSING = "MASK_MODEL_MISSING"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"


# code -> (default message, default next step)
_DEFAULTS: dict[ErrorCode, tuple[str, str]] = {
    ErrorCode.LR_NOT_REACHABLE: (
        "Lightroom Classic did not answer on its local plugin connection.",
        "Check that Lightroom Classic is open, the darkroom plugin is enabled in the Plug-in "
        "Manager, and no dialog is waiting (likely candidates: sign-in, trial or license "
        "prompts). Then retry.",
    ),
    ErrorCode.PS_NOT_REACHABLE: (
        "Photoshop did not answer in time or could not be started.",
        "Check that Photoshop is running and no dialog is waiting (likely candidates: sign-in, "
        "trial or license prompts, or an open modal dialog). Then retry.",
    ),
    ErrorCode.ADOBE_FEATURE_UNAVAILABLE: (
        "The Adobe app reports that a required feature is disabled.",
        "This is likely a license or trial issue inside the Adobe app. Fix it in the Adobe app, "
        "then retry.",
    ),
    ErrorCode.ADOBE_VERSION_UNSUPPORTED: (
        "The detected Adobe app version has not been tested with darkroom_ai.",
        "Compare the detected version with the tested versions in the README and use a tested "
        "version.",
    ),
    ErrorCode.LR_WRONG_CATALOG: (
        "Lightroom has a different catalog open than the one darkroom_ai is configured for.",
        "Open the configured catalog in Lightroom, or change lightroom.test_catalog and run "
        "`darkroom lightroom setup` again. Nothing was changed.",
    ),
    ErrorCode.LR_COMMAND_FAILED: (
        "Lightroom could not carry out the command.",
        "Read the message above; if it mentions the token, run `darkroom lightroom setup` and "
        "reload the plug-in in the Plug-in Manager.",
    ),
    ErrorCode.PS_COMMAND_FAILED: (
        "Photoshop could not carry out the command.",
        "Read the message above, check the document in Photoshop, then retry.",
    ),
    ErrorCode.LLM_NOT_REACHABLE: (
        "The local model server did not answer.",
        "Start the local model server (for example LM Studio's local server), load the model "
        "named in the active profile, and check base_url in darkroom.toml.",
    ),
    ErrorCode.LLM_NO_VISION: (
        "The active model did not accept an image.",
        "Load a model with its vision projector (mmproj) or switch to a profile with "
        "vision = true that passes `darkroom models test`.",
    ),
    ErrorCode.LLM_BAD_TOOL_CALL: (
        "The model returned a tool call that could not be used.",
        "Retry; if it keeps happening, change tool_mode for this profile or try another "
        "profile (`darkroom models test` compares them).",
    ),
    ErrorCode.CONFIG_INVALID: (
        "The configuration is not valid.",
        "Fix the listed settings in darkroom.toml (or darkroom.local.toml) and run "
        "`darkroom doctor`.",
    ),
    ErrorCode.NON_LOCAL_ENDPOINT: (
        "A configured address is not a local (loopback) address.",
        "darkroom_ai only talks to this computer. Use 127.0.0.1, localhost or ::1.",
    ),
    ErrorCode.GUARDRAIL_REJECTED: (
        "The requested edit is outside the allowed limits.",
        "Use a smaller change, or adjust the limits in the [guardrails] section of darkroom.toml.",
    ),
    ErrorCode.CONFIRMATION_REQUIRED: (
        "This action is destructive or affects many photos and needs your explicit go-ahead.",
        "Review the plan that was shown and confirm it to continue.",
    ),
    ErrorCode.MASK_MODEL_MISSING: (
        "The local mask model is not available.",
        "Install the ml extra (`make install-ml`) and set the path of your ONNX model file in "
        "darkroom.toml.",
    ),
    ErrorCode.TOOL_TIMEOUT: (
        "The operation did not finish in time.",
        "Check that the app involved is responsive and has no dialog open, then retry.",
    ),
}


class DarkroomError(Exception):
    """A failure with a stable code, a plain message and a next step for the user."""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        next_step: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        default_message, default_next_step = _DEFAULTS[code]
        self.code = code
        self.message = message or default_message
        self.next_step = next_step or default_next_step
        self.details: dict[str, Any] = dict(details or {})
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message} Next step: {self.next_step}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "next_step": self.next_step,
            "details": self.details,
        }
