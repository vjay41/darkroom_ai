"""`darkroom clean-outputs`: remove intermediate files, never final results.

Only files that match a known intermediate pattern inside the configured outputs and previews
folders are candidates. Final results (`*_darkroom.psd`, `*_darkroom.jpg`, also with `-2`, `-3`)
and anything not recognised are kept. Nothing is removed until the person agrees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from darkroom_ai.config import Settings

FINAL = re.compile(r"_darkroom(-\d+)?\.(psd|jpe?g)$", re.IGNORECASE)
WORKING = re.compile(r"_copy(_mask|_reference)?(-\d+)?\.(tiff?|jpe?g|png)$", re.IGNORECASE)
PICTURE = re.compile(r"\.(tiff?|jpe?g|png)$", re.IGNORECASE)


@dataclass
class CleanupPlan:
    remove: dict[str, list[Path]] = field(default_factory=dict)  # category -> files
    finals: list[Path] = field(default_factory=list)
    unknown: list[Path] = field(default_factory=list)

    @property
    def files(self) -> list[Path]:
        return [path for paths in self.remove.values() for path in paths]

    @property
    def megabytes(self) -> float:
        return sum(path.stat().st_size for path in self.files) / 1_000_000


def _files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.is_symlink())


def plan_cleanup(settings: Settings) -> CleanupPlan:
    """List what would be removed and what is kept. Reads only."""
    outputs = settings.paths.outputs_dir.resolve()
    plan = CleanupPlan()

    def add(category: str, paths: list[Path]) -> None:
        if paths:
            plan.remove[category] = paths

    add(
        "previews",
        [p for p in _files(settings.paths.previews_dir.resolve()) if PICTURE.search(p.name)],
    )
    add("hand-off TIFFs", [p for p in _files(outputs / "handoff") if PICTURE.search(p.name)])
    add("masks", [p for p in _files(outputs / "masks") if p.suffix.lower() == ".png"])
    working: list[Path] = []
    for path in _files(outputs / "photoshop"):
        if FINAL.search(path.name):
            plan.finals.append(path)
        elif WORKING.search(path.name):
            working.append(path)
        else:
            plan.unknown.append(path)
    add("Photoshop working copies and masks", working)
    return plan


def apply_cleanup(plan: CleanupPlan) -> int:
    """Remove the planned files. Call only after the person agreed. Returns the number removed."""
    removed = 0
    for path in plan.files:
        if FINAL.search(path.name) or not path.is_file() or path.is_symlink():
            continue  # belt and braces: a final result is never removed
        path.unlink()
        removed += 1
    return removed
