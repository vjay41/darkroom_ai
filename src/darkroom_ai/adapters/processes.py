"""Look at, and start, the Adobe programs. Windows only; elsewhere every answer is "unknown".

Only the program files named in the settings are ever started, with no shell and no arguments from
the caller except the catalog path of the Lightroom catalog the settings allow.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASKLIST_TIMEOUT_S = 10.0


class WindowsProcesses:
    def is_running(self, image_name: str) -> bool | None:
        """True/False if the program has a running process; None if this cannot be told."""
        if sys.platform != "win32":
            return None
        try:
            done = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                timeout=TASKLIST_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return image_name.lower() in done.stdout.lower()

    def start(self, exe: Path, arguments: list[str] | None = None) -> None:
        """Start a program without waiting for it. The caller has already checked the path."""
        subprocess.Popen([str(exe), *(arguments or [])], close_fds=True)
