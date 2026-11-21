"""Asks the person for a go-ahead through the MCP client (elicitation).

The model can never confirm for itself: the question goes to the client application (Hermes), which
shows it to the person. Anything other than an explicit "accept" with `proceed` ticked refuses: a
decline, a cancel, a timeout, a client that cannot ask, or an unattended run.
"""

from __future__ import annotations

import contextvars
import json
import sys
from collections.abc import Mapping
from typing import Any

import anyio
import anyio.from_thread
import mcp.types as types
from mcp.server.session import ServerSession

CONFIRM_TIMEOUT_S = 240.0  # keep below the client's own wait (Hermes: 300 s)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proceed": {
            "type": "boolean",
            "title": "Proceed",
            "description": "Tick to allow this action.",
        }
    },
    "required": ["proceed"],
}


def _approved(action: str, content: Mapping[str, Any] | None) -> bool:
    """Only an explicit accept from the client counts.

    Some clients (Hermes) show a plain allow/deny prompt and answer "accept" without returning the
    form field, so a missing `proceed` is taken as the accept it is; an explicit `proceed: false`,
    a decline, a cancel or anything else refuses.
    """
    if action != "accept":
        return False
    if content and "proceed" in content:
        return bool(content["proceed"])
    return True


class ElicitationConfirmer:
    """A Confirmer that asks the connected MCP client. Bind it per request with `bind`."""

    def __init__(self, timeout_s: float = CONFIRM_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s
        self._current: contextvars.ContextVar[tuple[ServerSession, Any] | None] = (
            contextvars.ContextVar("darkroom_elicitation", default=None)
        )

    def bind(self, session: ServerSession, request_id: Any) -> contextvars.Token[Any]:
        return self._current.set((session, request_id))

    def unbind(self, token: contextvars.Token[Any]) -> None:
        self._current.reset(token)

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        """Called from the tool's worker thread; blocks until the person answers."""
        bound = self._current.get()
        if bound is None:
            return False
        session, request_id = bound
        if not session.check_client_capability(
            types.ClientCapabilities(elicitation=types.ElicitationCapability(form={}))
        ):
            return False
        message = (
            f"Darkroom needs your go-ahead: {action}.\n{json.dumps(dict(details), default=str)}"
        )

        async def ask() -> bool:
            with anyio.fail_after(self._timeout_s):
                result = await session.elicit_form(
                    message=message, requested_schema=_SCHEMA, related_request_id=request_id
                )
            approved = _approved(result.action, result.content)
            print(
                f"darkroom_ai: confirmation answer action={result.action} "
                f"content_keys={sorted(result.content or {})} approved={approved}",
                file=sys.stderr,
            )
            return approved

        try:
            return anyio.from_thread.run(ask)
        except Exception as exc:  # timeout, closed connection, malformed answer: refuse
            print(
                f"darkroom_ai: confirmation refused ({type(exc).__name__}: {exc})", file=sys.stderr
            )
            return False
