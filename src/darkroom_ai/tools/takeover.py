"""Taking over Lightroom's Develop view without fighting the person who is using it.

Mask tools only work while Lightroom's Develop module shows the photo, so darkroom_ai has to take
the view for a few seconds. If the person is clicking around in Lightroom at that moment, the
takeover would fail or, worse, yank the view from under them. So this waits, watching Lightroom's
selection (read only), and takes over once the selection has been still for a few seconds. If the
person stays busy it pauses and asks them (through the confirmer) to stop using Lightroom, then
tries again. Nothing is changed while waiting.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.context import ToolContext

_sleep = time.sleep
_now = time.monotonic

SAMPLE_S = 2.0  # how often Lightroom's selection is looked at while waiting
STABLE_S = 4.0  # the selection must not change for this long before we take over
AUTO_WAIT_S = 45.0  # how long to wait on our own before asking the person
ASK_ROUNDS = 1  # how many times the person is asked before giving up


class _Watch:
    """Decides whether Lightroom looks idle, from samples of the active photo."""

    def __init__(self, ctx: ToolContext, photo_id: str) -> None:
        self._ctx = ctx
        self._photo_id = photo_id
        self._last: str | None = None
        self._changed_at = _now()
        self._first = True

    def quiet(self) -> bool:
        selection = self._ctx.lightroom.get_selection()
        active = selection.active.id if selection.active else None
        now = _now()
        if self._first or active != self._last:
            self._changed_at = now
            self._first = False
            self._last = active
        if active == self._photo_id:
            return True  # already showing our photo
        return now - self._changed_at >= STABLE_S


def _busy_error(waited: float) -> DarkroomError:
    return DarkroomError(
        ErrorCode.LR_BUSY_USER,
        f"Lightroom stayed in use for {waited:g} s, so darkroom_ai did not take over the Develop "
        "view. Nothing was changed.",
    )


def run_with_takeover[T](
    ctx: ToolContext, photo_id: str, action: Callable[[], T], *, what: str
) -> tuple[T, float]:
    """Run `action` (a Lightroom call that needs the Develop view) once Lightroom is free.

    Returns the action's result and how many seconds were spent waiting. Waits while the person is
    using Lightroom, retries on its own, and after AUTO_WAIT_S asks the person to release it.
    """
    started = _now()
    watch = _Watch(ctx, photo_id)
    for round_number in range(ASK_ROUNDS + 1):
        deadline = _now() + AUTO_WAIT_S
        while True:
            if watch.quiet():
                try:
                    return action(), _now() - started
                except DarkroomError as exc:
                    if exc.code is not ErrorCode.LR_BUSY_USER:
                        raise
            if _now() >= deadline:
                break
            _sleep(SAMPLE_S)
        if round_number < ASK_ROUNDS:
            details: dict[str, Any] = {
                "photo_id": photo_id,
                "step": what,
                "how": "Please stop using Lightroom (no clicking) and answer proceed; darkroom_ai "
                "will then take over the Develop view for about 10 seconds.",
            }
            if not ctx.confirmer.confirm(
                f"Lightroom is in use, so I paused ({what}). Release Lightroom and I will retry",
                details,
            ):
                raise _busy_error(_now() - started)
            watch = _Watch(ctx, photo_id)
    raise _busy_error(_now() - started)


def takeover_note(waited_s: float) -> str:
    """A line for the tool result telling the person what happened."""
    if waited_s < 1:
        return ""
    return f" Lightroom was in use; waited {waited_s:.0f} s and then took over the Develop view."
