"""Runs one Photoshop COM operation in a child process with a hard deadline.

COM calls into Photoshop block for as long as Photoshop wants (for example while a dialog is
open) and cannot be cancelled from the calling thread. Each operation therefore runs in its own
child process; if it has not answered by the deadline the child is killed and PS_NOT_REACHABLE is
raised. Photoshop itself is never killed.

With auto_start, the first call may launch Photoshop; use a long deadline for that call
(`PhotoshopClient.connect`).
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any, Protocol

from darkroom_ai.errors import DarkroomError, ErrorCode

_JOIN_GRACE_S = 2.0
_POLL_S = 0.5

# Called while waiting; returning an error ends the wait early with that error.
Watchdog = Callable[[], DarkroomError | None]


class OperationRunner(Protocol):
    def __call__(
        self,
        name: str,
        kwargs: dict[str, Any],
        *,
        timeout_s: float,
        watchdog: Watchdog | None = None,
    ) -> dict[str, Any]: ...


def _child(
    connection: Connection, prog_id: str, auto_start: bool, name: str, kwargs: dict[str, Any]
) -> None:
    try:
        from darkroom_ai.adapters.photoshop.com_ops import run_operation

        connection.send(("ok", run_operation(prog_id, name, kwargs, auto_start=auto_start)))
    except DarkroomError as exc:
        connection.send(("error", exc.to_dict()))
    except Exception as exc:
        connection.send(
            (
                "error",
                {
                    "code": ErrorCode.PS_COMMAND_FAILED.value,
                    "message": f"Photoshop operation '{name}' failed: {type(exc).__name__}: {exc}",
                    "next_step": None,
                    "details": {"operation": name},
                },
            )
        )
    finally:
        connection.close()


class ChildProcessRunner:
    """OperationRunner that uses a fresh child process per operation."""

    def __init__(self, prog_id: str, *, auto_start: bool = False) -> None:
        self._prog_id = prog_id
        self._auto_start = auto_start
        self._context = multiprocessing.get_context("spawn")

    def __call__(
        self,
        name: str,
        kwargs: dict[str, Any],
        *,
        timeout_s: float,
        watchdog: Watchdog | None = None,
    ) -> dict[str, Any]:
        parent, child = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_child,
            args=(child, self._prog_id, self._auto_start, name, kwargs),
            daemon=True,
        )
        process.start()
        child.close()
        try:
            deadline = time.monotonic() + timeout_s
            answered = False
            while not answered:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                answered = parent.poll(min(_POLL_S, remaining))
                if not answered and watchdog is not None:
                    problem = watchdog()
                    if problem is not None:
                        raise problem
            if not answered:
                raise DarkroomError(
                    ErrorCode.PS_NOT_REACHABLE,
                    f"Photoshop did not finish '{name}' within {timeout_s:g} s "
                    "(likely a dialog is waiting or Photoshop is busy).",
                    details={"reason": "timeout", "operation": name, "timeout_s": timeout_s},
                )
            try:
                status, payload = parent.recv()
            except EOFError as exc:
                raise DarkroomError(
                    ErrorCode.PS_NOT_REACHABLE,
                    f"The Photoshop worker for '{name}' ended without an answer.",
                    details={"reason": "worker_died", "operation": name},
                ) from exc
        finally:
            parent.close()
            if process.is_alive():
                process.terminate()
            process.join(_JOIN_GRACE_S)

        if status == "ok":
            return dict(payload)
        raise DarkroomError(
            ErrorCode(payload["code"]),
            payload.get("message"),
            next_step=payload.get("next_step"),
            details=payload.get("details"),
        )
