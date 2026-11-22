"""Which code is this connector running, and is an older copy still running somewhere?

The MCP connector is a long-lived process. After the source changes, a process that was started
earlier keeps running the OLD code (and the old safety limits) and answers as if nothing changed.
This module makes that visible and stops it from doing harm:

- `CodeGuard` hashes the source files at start and again (throttled) while running. If they differ
  the connector is `stale`: it says so in get_app_status and in every result, and write tools are
  refused (configurable) until it is restarted.
- Every connector registers itself in a small file (process id + the process's creation time + its
  code hash). A newly started connector stops registered connectors that run DIFFERENT code. It
  never stops one with the same code (Hermes may start more than one connector), and a record only
  counts if the process id and creation time both match, so a reused process id is never touched.

Nothing here talks to the network. Windows only for process control; elsewhere nothing is stopped.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

STALE_CHECK_EVERY_S = 5.0
STOP_WAIT_S = 5.0
POLL_S = 0.2
RESTART_HINT = (
    "Restart the connector: toggle darkroom_ai off and on in Hermes' Capabilities (or quit and "
    "reopen Hermes), or run `darkroom connectors --stop-stale`."
)


def code_hash(package_dir: Path) -> str:
    """Short fingerprint of every Python source file of the package (names and contents)."""
    digest = hashlib.sha256()
    for file in sorted(package_dir.rglob("*.py")):
        digest.update(file.relative_to(package_dir).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class CodeState:
    state: Literal["current", "stale"]
    pid: int
    started_at: str
    running_code: str
    code_on_disk: str


class CodeGuard:
    def __init__(
        self,
        package_dir: Path,
        *,
        hasher: Callable[[Path], str] = code_hash,
        clock: Callable[[], float] = time.monotonic,
        recheck_s: float = STALE_CHECK_EVERY_S,
    ) -> None:
        self._package_dir = package_dir
        self._hasher = hasher
        self._clock = clock
        self._recheck_s = recheck_s
        self.pid = os.getpid()
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.version = hasher(package_dir)
        self._disk = self.version
        self._checked_at = clock()

    def on_disk(self) -> str:
        now = self._clock()
        if now - self._checked_at >= self._recheck_s:
            self._checked_at = now
            # A file may be mid-write: keep the last answer and look again later.
            with contextlib.suppress(OSError):
                self._disk = self._hasher(self._package_dir)
        return self._disk

    @property
    def stale(self) -> bool:
        return self.on_disk() != self.version

    def state(self) -> CodeState:
        return CodeState(
            state="stale" if self.stale else "current",
            pid=self.pid,
            started_at=self.started_at,
            running_code=self.version,
            code_on_disk=self._disk,
        )

    def warning(self) -> str | None:
        if not self.stale:
            return None
        return (
            "This connector is running OLDER code than the files on disk (it was started at "
            f"{self.started_at}). Results may come from old logic. {RESTART_HINT}"
        )


class StaleWatchdog:
    """Decides when a stale connector should end itself (it never acts by itself).

    It says "exit" only when the code has been older than the files on disk for `grace_s` without
    a break, no tool call is running, and the last call finished at least `idle_s` ago. A fresh
    start or an edit that is reverted resets it. `grace_s` 0 means never.
    """

    def __init__(
        self,
        guard: CodeGuard,
        *,
        grace_s: float,
        idle_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._guard = guard
        self._grace_s = grace_s
        self._idle_s = idle_s
        self._clock = clock
        self._stale_since: float | None = None
        self._last_activity = clock()
        self._running = 0

    def call_started(self) -> None:
        self._running += 1
        self._last_activity = self._clock()

    def call_finished(self) -> None:
        self._running = max(0, self._running - 1)
        self._last_activity = self._clock()

    def should_exit(self) -> bool:
        if self._grace_s <= 0:
            return False
        now = self._clock()
        if not self._guard.stale:
            self._stale_since = None
            return False
        if self._stale_since is None:
            self._stale_since = now
        return (
            now - self._stale_since >= self._grace_s
            and self._running == 0
            and now - self._last_activity >= self._idle_s
        )


# -- other connectors -----------------------------------------------------------------------------


class InstanceControl(Protocol):
    def create_time(self, pid: int) -> int | None:
        """The process's creation time, or None if no such process is running."""

    def terminate(self, pid: int) -> bool: ...


class WindowsInstanceControl:
    """Process look-up and stop through kernel32 (no extra packages, no shell)."""

    def create_time(self, pid: int) -> int | None:
        if sys.platform != "win32":
            return None
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        query_limited, synchronize = 0x1000, 0x100000
        handle = kernel32.OpenProcess(query_limited | synchronize, False, pid)
        if not handle:
            return None
        try:
            if kernel32.WaitForSingleObject(handle, 0) == 0:  # already exited
                return None
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return None
            return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
        finally:
            kernel32.CloseHandle(handle)

    def terminate(self, pid: int) -> bool:
        if sys.platform != "win32":
            return False
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)


@dataclass(frozen=True)
class InstanceRecord:
    pid: int
    created: int
    started: str
    version: str


def register(directory: Path, record: InstanceRecord) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.pid}.json"
    path.write_text(json.dumps(asdict(record)), encoding="utf-8")
    return path


def unregister(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def _read(path: Path) -> InstanceRecord | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return InstanceRecord(
            pid=int(data["pid"]),
            created=int(data["created"]),
            started=str(data["started"]),
            version=str(data["version"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def list_instances(directory: Path, control: InstanceControl) -> list[InstanceRecord]:
    """Registered connectors that are still running. Records of dead processes are removed."""
    alive: list[InstanceRecord] = []
    if not directory.is_dir():
        return alive
    for path in sorted(directory.glob("*.json")):
        record = _read(path)
        if record is None or control.create_time(record.pid) != record.created:
            unregister(path)
            continue
        alive.append(record)
    return alive


def stop_stale(
    directory: Path,
    control: InstanceControl,
    *,
    own_version: str,
    own_pid: int,
    wait_s: float = STOP_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> list[InstanceRecord]:
    """Stop running connectors whose code differs from `own_version`; return those stopped."""
    stopped: list[InstanceRecord] = []
    for record in list_instances(directory, control):
        if record.pid == own_pid or record.version == own_version:
            continue
        if not control.terminate(record.pid):
            continue
        waited = 0.0
        while control.create_time(record.pid) == record.created and waited < wait_s:
            sleep(POLL_S)
            waited += POLL_S
        unregister(directory / f"{record.pid}.json")
        stopped.append(record)
    return stopped
