"""Confirmation for actions that need the user's explicit go-ahead.

The model can never confirm for itself: tools ask a Confirmer, which is supplied by the program
around the agent (the CLI asks the person). The default refuses everything.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from darkroom_ai.errors import DarkroomError, ErrorCode


class Confirmer(Protocol):
    def confirm(self, action: str, details: Mapping[str, Any]) -> bool: ...


class DenyAll:
    """Refuses every request. Used when no person can be asked."""

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        return False


def require_confirmation(confirmer: Confirmer, action: str, details: Mapping[str, Any]) -> None:
    """Raise CONFIRMATION_REQUIRED unless the confirmer agrees."""
    if not confirmer.confirm(action, details):
        raise DarkroomError(
            ErrorCode.CONFIRMATION_REQUIRED,
            f"This needs your explicit go-ahead: {action}.",
            details=dict(details),
        )
