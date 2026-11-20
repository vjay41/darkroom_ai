"""Remove caches and build output. Used by `make clean` and `make clean-all`.

Safety rules:
- Only an explicit list of cache/build names is ever removed; nothing else is touched.
- Directories in PROTECTED (the venv, git data, catalogs, photos, models, logs, outputs) are never
  entered or removed, and neither is any Lightroom catalog folder (*.lrdata, *.lrcat*).
- Symlinks and junctions are never followed or removed.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

PROTECTED = frozenset(
    {
        ".venv",
        "venv",
        ".git",
        ".idea",
        "test_catalog",
        "photos",
        "models",
        "logs",
        "outputs",
        "previews",
    }
)
CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
ROOT_DIRS = ("build",)
ROOT_DIRS_ALL = ("dist", "htmlcov")
ROOT_FILES = (".coverage",)


def _is_protected(name: str) -> bool:
    lowered = name.lower()
    return lowered in PROTECTED or lowered.endswith(".lrdata") or ".lrcat" in lowered


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def find_targets(root: Path, *, everything: bool = False) -> list[Path]:
    """Return the paths that would be removed under `root`."""
    found: list[Path] = []
    for current, dirnames, _filenames in os.walk(root, followlinks=False):
        here = Path(current)
        keep = []
        for name in sorted(dirnames):
            path = here / name
            if _is_protected(name) or _is_link(path):
                continue
            if name in CACHE_DIRS or name.endswith(".egg-info"):
                found.append(path)
                continue
            keep.append(name)
        dirnames[:] = keep

    root_dirs = ROOT_DIRS + (ROOT_DIRS_ALL if everything else ())
    for name in root_dirs:
        path = root / name
        if path.is_dir() and not _is_link(path):
            found.append(path)
    for name in ROOT_FILES:
        path = root / name
        if path.is_file() and not _is_link(path):
            found.append(path)
    return found


def clean(root: Path, *, everything: bool = False, dry_run: bool = False) -> list[Path]:
    targets = find_targets(root, everything=everything)
    for path in targets:
        if dry_run:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="also remove dist/ and htmlcov/")
    parser.add_argument("--dry-run", action="store_true", help="list what would be removed")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    removed = clean(root, everything=args.all, dry_run=args.dry_run)
    verb = "would remove" if args.dry_run else "removed"
    for path in removed:
        print(f"{verb}: {path.relative_to(root)}")
    print(f"{verb} {len(removed)} item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
