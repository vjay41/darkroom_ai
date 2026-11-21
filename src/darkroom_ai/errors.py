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
    LR_BUSY_USER = "LR_BUSY_USER"
    # Photoshop adapter
    PS_COMMAND_FAILED = "PS_COMMAND_FAILED"
    # Local model server (PRD s8)
    LLM_NOT_REACHABLE = "LLM_NOT_REACHABLE"
    LLM_NO_VISION = "LLM_NO_VISION"
    LLM_BAD_TOOL_CALL = "LLM_BAD_TOOL_CALL"
    LLM_REQUEST_FAILED = "LLM_REQUEST_FAILED"
    LLM_MODEL_NOT_LOADED = "LLM_MODEL_NOT_LOADED"
    # darkroom_ai itself
    CONFIG_INVALID = "CONFIG_INVALID"
    NON_LOCAL_ENDPOINT = "NON_LOCAL_ENDPOINT"
    GUARDRAIL_REJECTED = "GUARDRAIL_REJECTED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    MASK_MODEL_MISSING = "MASK_MODEL_MISSING"
    MASK_FAILED = "MASK_FAILED"
    FACE_MODEL_MISSING = "FACE_MODEL_MISSING"
    IMAGE_UNREADABLE = "IMAGE_UNREADABLE"


# code -> (default message, default next step)
_DEFAULTS: dict[ErrorCode, tuple[str, str]] = {
    ErrorCode.LR_BUSY_USER: (
        "Lightroom is being used by the person, so darkroom_ai could not take over its Develop "
        "view.",
        "Ask the person to stop clicking in Lightroom and leave it on any screen, then call the "
        "same tool again. darkroom_ai only takes over once Lightroom has been still for a few "
        "seconds.",
    ),
    ErrorCode.LR_NOT_REACHABLE: (
        "Lightroom Classic did not answer on its local plugin connection.",
        "Check that Lightroom Classic is open, the darkroom plugin is enabled in the Plug-in "
        'Manager, its connection is started (File > Plug-in Extras > "Start darkroom_ai '
        'Bridge"), and no dialog is waiting (likely candidates: sign-in, trial or license '
        "prompts). Then retry.",
    ),
    ErrorCode.PS_NOT_REACHABLE: (
        "Photoshop did not answer in time or could not be started.",
        "Check that Photoshop is running and no dialog is waiting (likely candidates: sign-in, "
        "trial or license prompts, or an open modal dialog). Then retry.",
    ),
    # Reserved (named in the project rules). Nothing raises it yet: no Adobe response has been
    # observed that clearly means "feature disabled".
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
    ErrorCode.LLM_REQUEST_FAILED: (
        "The local model server answered with an error.",
        "Read the message above. Check that the model named in the profile is downloaded and can "
        "be loaded in the local model server, then retry.",
    ),
    ErrorCode.LLM_MODEL_NOT_LOADED: (
        "The model of the active profile is not loaded in the local model server.",
        "Load the model in the local model server (for example in LM Studio), then retry. "
        "darkroom_ai does not ask the server to load models by itself; to allow that, set "
        "require_loaded = false under [llm] in darkroom.toml.",
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
        "The request is outside a safety rule.",
        "Read the message above; nothing was changed.",
    ),
    ErrorCode.CONFIRMATION_REQUIRED: (
        "This action needs your explicit go-ahead.",
        "Review the plan that was shown and confirm it to continue.",
    ),
    ErrorCode.MASK_MODEL_MISSING: (
        "The local mask model is not available.",
        "Install the ml extra (`make install-ml`) and set the path of your own ONNX model file "
        "in darkroom.toml. Nothing is downloaded automatically.",
    ),
    ErrorCode.MASK_FAILED: (
        "The subject mask could not be created.",
        "Read the message above; try another mask backend (masks.backend in darkroom.toml) or "
        "another photo.",
    ),
    ErrorCode.FACE_MODEL_MISSING: (
        "No face-detection model is available, so face and skin-tone analysis is switched off.",
        "Put a YuNet face-detection ONNX file on this computer and set its path as "
        "faces.model in darkroom.toml. Nothing is downloaded automatically.",
    ),
    ErrorCode.IMAGE_UNREADABLE: (
        "The image file could not be read.",
        "Check that the file exists and is a JPG, PNG, TIFF or supported RAW file.",
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
