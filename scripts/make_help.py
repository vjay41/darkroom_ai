"""Print the Makefile targets and their `##` descriptions (used by `make help`)."""

from __future__ import annotations

import re
from pathlib import Path

_TARGET = re.compile(r"^([A-Za-z0-9_-]+):[^#]*##\s*(.+)$")


def targets(makefile: Path) -> list[tuple[str, str]]:
    found = []
    for line in makefile.read_text(encoding="utf-8").splitlines():
        match = _TARGET.match(line)
        if match:
            found.append((match.group(1), match.group(2).strip()))
    return found


def main() -> int:
    makefile = Path(__file__).resolve().parent.parent / "Makefile"
    rows = targets(makefile)
    width = max(len(name) for name, _ in rows)
    print("Usage: make <target>\n")
    for name, text in rows:
        print(f"  {name.ljust(width)}  {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
