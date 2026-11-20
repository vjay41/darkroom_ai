"""Looks at Photoshop's top-level windows (plain Win32 calls, no COM, nothing is changed).

Used to tell "Photoshop is not running" from "Photoshop is up but not answering", which is what
a start, sign-in, trial or other blocking screen looks like from outside.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from darkroom_ai.errors import DarkroomError, ErrorCode

PHOTOSHOP_EXE = "photoshop.exe"
MAIN_WINDOW_CLASS = "Photoshop"


@dataclass(frozen=True, slots=True)
class WindowInfo:
    title: str
    window_class: str

    @property
    def is_main(self) -> bool:
        return self.window_class == MAIN_WINDOW_CLASS


def _process_image_name(pid: int) -> str | None:
    import pywintypes
    import win32api
    import win32con
    import win32process

    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
        )
    except pywintypes.error:
        return None
    try:
        return os.path.basename(str(win32process.GetModuleFileNameEx(handle, 0))).lower()
    except pywintypes.error:
        return None
    finally:
        win32api.CloseHandle(handle)


def photoshop_windows() -> list[WindowInfo]:
    """Visible top-level windows that belong to a Photoshop process (empty if none)."""
    import win32gui
    import win32process

    names: dict[int, str | None] = {}
    found: list[WindowInfo] = []

    def visit(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid not in names:
            names[pid] = _process_image_name(pid)
        if names[pid] == PHOTOSHOP_EXE:
            found.append(WindowInfo(win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd)))
        return True

    win32gui.EnumWindows(visit, None)
    return found


class StartWatchdog:
    """Detects "Photoshop is on screen but does not answer" while a call is waiting.

    Called repeatedly while the worker waits. Once Photoshop has had a visible window for
    `grace_s` seconds without the call finishing, it returns a PS_NOT_REACHABLE error that names
    the open windows, instead of letting the caller wait for the full start deadline.
    """

    def __init__(
        self,
        grace_s: float,
        *,
        list_windows: Callable[[], list[WindowInfo]] = photoshop_windows,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._grace_s = grace_s
        self._list_windows = list_windows
        self._clock = clock
        self._first_seen: float | None = None

    def __call__(self) -> DarkroomError | None:
        windows = self._list_windows()
        if not windows:
            self._first_seen = None
            return None
        now = self._clock()
        if self._first_seen is None:
            self._first_seen = now
        if now - self._first_seen < self._grace_s:
            return None
        titles = [w.title for w in windows if w.title] or ["(untitled window)"]
        shown = ", ".join(repr(title) for title in titles)
        return DarkroomError(
            ErrorCode.PS_NOT_REACHABLE,
            f"Photoshop is open but has not answered for {self._grace_s:g} s. Open Photoshop "
            f"windows: {shown}. Likely a start, sign-in, trial or other dialog is waiting.",
            next_step="Bring Photoshop to the front, finish or close that screen, then retry.",
            details={"reason": "not_answering", "windows": titles},
        )
