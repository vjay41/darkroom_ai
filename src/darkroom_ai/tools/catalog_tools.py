"""Catalog-wide inventory: summarise the whole catalog, and bring a folder of photos into it.

The summary only reads. Importing is a bulk change, so it needs the person's go-ahead, and it adds
photos "without moving": nothing is copied, moved or changed on disk.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import PureWindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from darkroom_ai.adapters.lightroom.models import FolderImport, FolderScan, PhotoMetadata
from darkroom_ai.safety.confirm import require_confirmation
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult

PAGE = 100
DEFAULT_MAX_PHOTOS = 2000
ISO_BANDS = ((400, "up to 400"), (1600, "401-1600"), (6400, "1601-6400"))
YEAR = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")


def _iso_band(iso: str | None) -> str:
    # Lightroom formats ISO as text such as "ISO 200", so take the first number in it.
    found = re.search(r"\d+(?:\.\d+)?", str(iso or ""))
    if not found:
        return "unknown"
    value = int(float(found.group()))
    for limit, label in ISO_BANDS:
        if value <= limit:
            return label
    return "above 6400"


def _top(counter: Counter[str], size: int) -> dict[str, int]:
    return dict(counter.most_common(size))


class CatalogSummaryInput(BaseModel):
    """Summarise the whole catalog: how many photos, and counts by camera, lens, ISO band, file
    format, star rating, capture year and folder. Read only. Use it to understand a shoot or the
    library before editing, then list_catalog_photos to look at specific photos."""

    model_config = ConfigDict(extra="forbid")

    max_photos: int = Field(default=DEFAULT_MAX_PHOTOS, ge=50, le=10000)
    top: int = Field(default=10, ge=3, le=30, description="How many entries to list per group.")
    path_contains: str | None = Field(
        default=None, description="Only photos whose file path contains this text (a folder)."
    )


class CatalogSummaryResult(ToolResult):
    total_photos: int
    summarised: int
    truncated: bool
    edited_photos: int
    cameras: dict[str, int]
    lenses: dict[str, int]
    iso_bands: dict[str, int]
    file_formats: dict[str, int]
    ratings: dict[str, int]
    capture_years: dict[str, int]
    folders: dict[str, int]
    note: str = ""


def _summarise(photos: Iterable[PhotoMetadata], top: int) -> dict[str, Any]:
    cameras: Counter[str] = Counter()
    lenses: Counter[str] = Counter()
    iso: Counter[str] = Counter()
    formats: Counter[str] = Counter()
    ratings: Counter[str] = Counter()
    years: Counter[str] = Counter()
    folders: Counter[str] = Counter()
    edited = 0
    for photo in photos:
        camera = " ".join(part for part in (photo.camera_make, photo.camera_model) if part)
        cameras[camera or "unknown"] += 1
        lenses[photo.lens or "unknown"] += 1
        iso[_iso_band(photo.iso)] += 1
        formats[photo.file_format or "unknown"] += 1
        ratings[str(photo.rating) if photo.rating else "unrated"] += 1
        match = YEAR.search(photo.capture_time or "")
        years[match.group(1) if match else "unknown"] += 1
        folders[str(PureWindowsPath(photo.path).parent) if photo.path else "unknown"] += 1
        edited += 1 if photo.has_develop_edits else 0
    return {
        "edited_photos": edited,
        "cameras": _top(cameras, top),
        "lenses": _top(lenses, top),
        "iso_bands": dict(iso),
        "file_formats": dict(formats),
        "ratings": dict(sorted(ratings.items())),
        "capture_years": dict(sorted(years.items())),
        "folders": _top(folders, top),
    }


def catalog_summary(ctx: ToolContext, args: CatalogSummaryInput) -> Outcome:
    seen: list[PhotoMetadata] = []
    total = 0
    offset = 0
    while len(seen) < args.max_photos:
        page = ctx.lightroom.list_catalog(
            offset, min(PAGE, args.max_photos - len(seen)), path_contains=args.path_contains
        )
        total = page.total
        seen.extend(page.photos)
        offset += page.returned
        if page.returned == 0 or offset >= total:
            break
    truncated = len(seen) < total
    note = (
        f"Only the first {len(seen)} of {total} photos (sorted by path) were summarised; raise "
        "max_photos or narrow with path_contains."
        if truncated
        else "Virtual copies are not counted. Counts come from Lightroom's own metadata."
    )
    return Outcome(
        CatalogSummaryResult(
            total_photos=total,
            summarised=len(seen),
            truncated=truncated,
            note=note,
            **_summarise(seen, args.top),
        )
    )


class FolderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder: str = Field(
        min_length=1, description="Absolute folder path, inside the allowed folder."
    )
    recursive: bool = Field(default=False, description="Include sub-folders.")


class ScanFolderInput(FolderInput):
    """Say what importing a folder would add to the catalog (new photos, already-present photos,
    unsupported files) without changing anything. Always do this before import_folder."""


class ScanFolderResult(ToolResult):
    scan: FolderScan


def scan_folder(ctx: ToolContext, args: ScanFolderInput) -> Outcome:
    return Outcome(ScanFolderResult(scan=ctx.lightroom.scan_folder(args.folder, args.recursive)))


class ImportFolderInput(FolderInput):
    """Add the photos in a folder to the catalog WITHOUT moving, copying or changing any file.
    This is a bulk change: the person is asked for a go-ahead showing how many photos will be
    added, so call scan_folder first and tell them. Adds at most max_files new photos per call;
    call again for the rest. It cannot be undone with the undo tool (remove the photos in
    Lightroom if needed)."""

    max_files: int = Field(default=200, ge=1, le=500)


class ImportFolderResult(ToolResult):
    imported: FolderImport
    note: str = ""


def import_folder(ctx: ToolContext, args: ImportFolderInput) -> Outcome:
    scan = ctx.lightroom.scan_folder(args.folder, args.recursive)
    count = min(scan.new_count, args.max_files)
    if count == 0:
        note = "Nothing to import: every supported photo in the folder is already in the catalog."
        empty = FolderImport(
            folder=args.folder, added=0, already_in_catalog=scan.already_in_catalog
        )
        return Outcome(ImportFolderResult(imported=empty, note=note))
    require_confirmation(
        ctx.confirmer,
        f"add {count} photos from {args.folder} to the catalog (files are not moved or changed)",
        {"folder": args.folder, "photos_to_add": count, "recursive": args.recursive},
    )
    done = ctx.lightroom.import_folder(args.folder, args.recursive, args.max_files)
    note = (
        f"{done.left_for_next_call} more new photos remain: call import_folder again."
        if done.left_for_next_call
        else "Done. Use catalog_summary or list_catalog_photos to look at what was added."
    )
    return Outcome(
        ImportFolderResult(imported=done, note=note),
        applied={"folder": args.folder, "added": done.added},
    )
