"""Catalog management: which catalogs exist, what the open one holds, and organising it.

Everything here is catalog data only: no pixels, no develop settings, no photo file is touched.
Hermes cannot create or switch catalogs (Lightroom's plug-in SDK has no call for it), so
`get_catalog_info` says how the owner does it. The owner alone decides which catalogs and photo
folders may be changed: `request_catalog_access` and `request_folder_access` show the full path
and wait for a Yes.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from darkroom_ai.adapters.lightroom.models import CatalogInfo
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.interfaces import AccessStore
from darkroom_ai.safety.confirm import require_confirmation
from darkroom_ai.safety.journal import JournalEntry
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult

BULK_CONFIRM_ABOVE = 10
MAX_PHOTOS_PER_CALL = 200
MAX_FOUND = 100
SEARCH_DEPTH = 2
SEARCH_SECONDS = 10.0
SKIP_DIRS = frozenset(
    {
        "$recycle.bin",
        "system volume information",
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "appdata",
        "node_modules",
        ".git",
        ".venv",
        "backups",
    }
)

CREATE_TOOLS = ("create_collection_set", "create_collection", "create_smart_collection")
ADD_TOOL = "add_to_collection"
REMOVE_TOOL = "remove_from_collection"
UNDO_TOOL = "undo_collection_change"
COLLECTION_TOOLS = (*CREATE_TOOLS, ADD_TOOL, REMOVE_TOOL)

SWITCH_HELP = (
    "Hermes cannot create or switch catalogs (Lightroom does not allow it from a plug-in). "
    "To open another catalog: in Lightroom choose File > Open Catalog..., pick the .lrcat file "
    "(Lightroom restarts), then File > Plug-in Extras > Start darkroom_ai Bridge. To make a new "
    "catalog: File > New Catalog..., name it and save it. Then call get_catalog_info to confirm "
    "which catalog is open and whether changes are allowed there."
)


def normalize(path: str) -> str:
    return path.replace("/", "\\").rstrip("\\").lower()


def _same(first: str, second: str) -> bool:
    return normalize(first) == normalize(second)


def _need_access(ctx: ToolContext) -> AccessStore:
    if ctx.access is None:
        raise DarkroomError(
            ErrorCode.CONFIG_INVALID,
            "The catalog allowlist is not available here.",
            next_step="Run `darkroom lightroom allow-catalog` yourself, or use the MCP server.",
        )
    return ctx.access


# -- list_catalogs --------------------------------------------------------------------------------


class ListCatalogsInput(BaseModel):
    """List the Lightroom catalogs (.lrcat files) found on this PC, say which one is open now and
    which ones darkroom_ai is allowed to change. Reads file names, sizes and dates only; it never
    opens a catalog. folder (optional) also searches one more folder, two levels deep."""

    model_config = ConfigDict(extra="forbid")

    folder: str | None = Field(default=None, description="One more folder to search.")


class CatalogFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    size_mb: float
    modified: str
    is_open: bool
    changes_allowed: bool


class ListCatalogsResult(ToolResult):
    open_catalog: str
    open_catalog_changes_allowed: bool
    searched: list[str]
    catalogs: list[CatalogFile]
    truncated: bool = False
    note: str = ""


def find_catalogs(
    folders: Sequence[Path], *, depth: int = SEARCH_DEPTH, limit: int = MAX_FOUND + 1
) -> list[Path]:
    """.lrcat files under the folders (a few levels deep), skipping system and backup folders."""
    found: dict[str, Path] = {}
    deadline = time.monotonic() + SEARCH_SECONDS

    def walk(folder: Path, level: int) -> None:
        if level > depth or len(found) >= limit or time.monotonic() > deadline:
            return
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_file() and entry.name.lower().endswith(".lrcat"):
                    found.setdefault(normalize(entry.path), Path(entry.path))
                elif entry.is_dir(follow_symlinks=False) and entry.name.lower() not in SKIP_DIRS:
                    walk(Path(entry.path), level + 1)
            except OSError:
                continue

    for folder in folders:
        walk(folder, 0)
    return sorted(found.values(), key=lambda p: str(p).lower())


def _default_search_folders(open_catalog: str, allowed: Sequence[str]) -> list[Path]:
    folders: list[Path] = []
    anchor = PureWindowsPath(open_catalog).anchor
    if anchor:
        folders.append(Path(anchor))
    folders += [Path(PureWindowsPath(c).parent) for c in allowed if PureWindowsPath(c).parent]
    folders.append(Path.home() / "Pictures")
    return folders


def list_catalogs(ctx: ToolContext, args: ListCatalogsInput) -> Outcome:
    ping = ctx.lightroom.ping()
    allowed = ctx.access.allowed_catalogs() if ctx.access else (ping.configured_catalog,)
    configured = list(ctx.settings.lightroom.catalog_search_dirs)
    folders = configured or _default_search_folders(ping.catalog_path, allowed)
    if args.folder:
        extra = Path(args.folder)
        if not extra.is_dir():
            raise DarkroomError(
                ErrorCode.GUARDRAIL_REJECTED,
                f"Not a folder: {args.folder}",
                next_step="Check the path.",
            )
        folders.append(extra)
    paths = find_catalogs(folders)
    truncated = len(paths) > MAX_FOUND
    entries: list[CatalogFile] = []
    for path in paths[:MAX_FOUND]:
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append(
            CatalogFile(
                path=str(path),
                size_mb=round(stat.st_size / 1_048_576, 1),
                modified=datetime.fromtimestamp(stat.st_mtime, UTC).strftime("%Y-%m-%d %H:%M"),
                is_open=_same(str(path), ping.catalog_path),
                changes_allowed=any(_same(str(path), a) for a in allowed),
            )
        )
    note = (
        "Only catalogs with changes_allowed true can be edited or organised. To allow another, "
        "call request_catalog_access: the owner sees the full path and answers Yes or No. "
        + SWITCH_HELP
    )
    return Outcome(
        ListCatalogsResult(
            open_catalog=ping.catalog_path,
            open_catalog_changes_allowed=ping.writes_allowed,
            searched=[str(f) for f in folders],
            catalogs=entries,
            truncated=truncated,
            note=note,
        )
    )


# -- get_catalog_info -----------------------------------------------------------------------------


class GetCatalogInfoInput(BaseModel):
    """Describe the catalog that is open in Lightroom now: how many photos, virtual copies and
    keywords, the collection tree with counts, the folder tree with counts, whether darkroom_ai may
    change it, and which catalogs and photo folders the owner has allowed. Read only. Call it
    first in any catalog session, and again after the owner opens another catalog."""

    model_config = ConfigDict(extra="forbid")


class GetCatalogInfoResult(ToolResult):
    catalog: CatalogInfo
    allowed_catalogs: list[str]
    allowed_photo_folders: list[str]
    note: str


def get_catalog_info(ctx: ToolContext, args: GetCatalogInfoInput) -> Outcome:
    info = ctx.lightroom.catalog_info()
    ping = ctx.lightroom.ping()
    allowed = list(ctx.access.allowed_catalogs()) if ctx.access else [ping.configured_catalog]
    roots = list(ctx.access.photos_roots()) if ctx.access else []
    note = (
        "Changes are allowed in this catalog."
        if info.writes_allowed
        else "This catalog is NOT on the allowlist: reading works, changes are refused. "
        "Ask the owner and call request_catalog_access, or ask them to open an allowed catalog."
    )
    return Outcome(
        GetCatalogInfoResult(
            catalog=info,
            allowed_catalogs=allowed,
            allowed_photo_folders=roots,
            note=note + " " + SWITCH_HELP,
        )
    )


# -- request_catalog_access / request_folder_access -----------------------------------------------


class RequestCatalogAccessInput(BaseModel):
    """Ask the OWNER to allow darkroom_ai to change a catalog (and optionally the photo folders it
    uses). The owner sees the full path and answers Yes or No; nothing is allowed on your say-so.
    The catalog must exist. Allowing a catalog does not open it: the owner still opens it in
    Lightroom (see get_catalog_info)."""

    model_config = ConfigDict(extra="forbid")

    catalog_path: str = Field(min_length=1, description="Full path of the .lrcat file.")
    photo_folders: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Folders whose photos may be edited or imported in that catalog.",
    )


class AccessResult(ToolResult):
    allowed_catalogs: list[str]
    allowed_photo_folders: list[str]
    changed: bool
    note: str


def _existing(path: str, *, folder: bool) -> str:
    target = Path(path)
    ok = target.is_dir() if folder else target.is_file() and target.suffix.lower() == ".lrcat"
    if not ok:
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"{'Folder' if folder else 'Catalog file (.lrcat)'} not found: {path}",
            next_step="Use list_catalogs to find the exact path, or ask the owner.",
        )
    return str(target)


def request_catalog_access(ctx: ToolContext, args: RequestCatalogAccessInput) -> Outcome:
    access = _need_access(ctx)
    catalog = _existing(args.catalog_path, folder=False)
    folders = [_existing(f, folder=True) for f in args.photo_folders]
    new_catalog = not any(_same(catalog, known) for known in access.allowed_catalogs())
    new_folders = [
        f for f in folders if not any(_same(f, known) for known in access.photos_roots())
    ]
    if not new_catalog and not new_folders:
        return Outcome(
            AccessResult(
                allowed_catalogs=list(access.allowed_catalogs()),
                allowed_photo_folders=list(access.photos_roots()),
                changed=False,
                note="Already allowed: nothing to do.",
            )
        )
    require_confirmation(
        ctx.confirmer,
        f"allow darkroom_ai to make changes (edits, imports, collections, keywords) in the "
        f"Lightroom catalog {catalog}",
        {"catalog": catalog, "new_catalog": new_catalog, "photo_folders": new_folders},
    )
    if new_catalog:
        access.allow_catalog(catalog)
    for folder in new_folders:
        access.allow_photos_folder(folder)
    return Outcome(
        AccessResult(
            allowed_catalogs=list(access.allowed_catalogs()),
            allowed_photo_folders=list(access.photos_roots()),
            changed=True,
            note="Allowed. If that catalog is not the open one, the owner opens it in Lightroom "
            "(File > Open Catalog) and starts the bridge. Then call get_catalog_info.",
        ),
        applied={"catalog": catalog, "photo_folders": new_folders},
    )


class RequestFolderAccessInput(BaseModel):
    """Ask the OWNER to allow darkroom_ai to import from and edit photos in a folder (for example
    the folder they just gave you). The owner sees the full path and answers Yes or No."""

    model_config = ConfigDict(extra="forbid")

    folder: str = Field(min_length=1, description="Full path of the folder.")


def request_folder_access(ctx: ToolContext, args: RequestFolderAccessInput) -> Outcome:
    access = _need_access(ctx)
    folder = _existing(args.folder, folder=True)
    if any(_same(folder, known) for known in access.photos_roots()):
        return Outcome(
            AccessResult(
                allowed_catalogs=list(access.allowed_catalogs()),
                allowed_photo_folders=list(access.photos_roots()),
                changed=False,
                note="Already allowed: nothing to do.",
            )
        )
    require_confirmation(
        ctx.confirmer,
        f"allow darkroom_ai to import and edit photos in the folder {folder} "
        "(files are never moved or changed)",
        {"folder": folder},
    )
    access.allow_photos_folder(folder)
    return Outcome(
        AccessResult(
            allowed_catalogs=list(access.allowed_catalogs()),
            allowed_photo_folders=list(access.photos_roots()),
            changed=True,
            note="Allowed. Next: scan_folder, then import_folder.",
        ),
        applied={"folder": folder},
    )


# -- collections ----------------------------------------------------------------------------------


class _PathInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1,
        max_length=300,
        description='Name with "/" between levels, for example "Trips/Japan 2026".',
    )


class CreateCollectionSetInput(_PathInput):
    """Create a collection set (a folder for collections) in the open catalog. The path's last
    name is the new set; the sets before it must exist. Safe to repeat: an existing set is
    reused. Undo with undo_collection_change."""


class CreateCollectionInput(_PathInput):
    """Create a collection in the open catalog. The path's last name is the new collection; the
    sets before it must exist (create_collection_set first). An existing collection is reused.
    Undo with undo_collection_change."""


class CollectionCreatedResult(ToolResult):
    path: str
    created: bool
    note: str = ""


def _create(ctx: ToolContext, path: str, kind: Literal["set", "collection"]) -> Outcome:
    made = (
        ctx.lightroom.create_collection_set(path)
        if kind == "set"
        else ctx.lightroom.create_collection(path)
    )
    result = CollectionCreatedResult(
        path=made.path,
        created=made.created,
        note="" if made.created else "It already existed: nothing changed.",
    )
    undo = {"collection_op": {"kind": "create", "type": kind, "path": made.path}}
    return Outcome(
        result,
        applied={"path": made.path, "created": made.created},
        undo=undo if made.created else None,
    )


def create_collection_set(ctx: ToolContext, args: CreateCollectionSetInput) -> Outcome:
    return _create(ctx, args.path, "set")


def create_collection(ctx: ToolContext, args: CreateCollectionInput) -> Outcome:
    return _create(ctx, args.path, "collection")


class _MembersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection: str = Field(min_length=1, description='Collection path such as "Trips/Japan".')
    photo_ids: list[str] = Field(min_length=1, max_length=MAX_PHOTOS_PER_CALL)


class AddToCollectionInput(_MembersInput):
    """Add photos (masters or virtual copies) to an existing collection. Photos already in it are
    skipped. More than 10 new photos is a bulk change: the owner is asked for a go-ahead first.
    Undo with undo_collection_change."""


class RemoveFromCollectionInput(_MembersInput):
    """Remove photos from a collection (the photos stay in the catalog). Undo with
    undo_collection_change."""


class CollectionMembersResult(ToolResult):
    collection: str
    changed_ids: list[str]
    already_in: int = 0
    missing_ids: list[str] = Field(default_factory=list)
    photo_count: int
    note: str = ""


def add_to_collection(ctx: ToolContext, args: AddToCollectionInput) -> Outcome:
    unique = list(dict.fromkeys(args.photo_ids))
    if len(unique) > BULK_CONFIRM_ABOVE:
        require_confirmation(
            ctx.confirmer,
            f"add {len(unique)} photos to the collection {args.collection}",
            {"collection": args.collection, "photos": len(unique)},
        )
    done = ctx.lightroom.add_to_collection(args.collection, unique)
    result = CollectionMembersResult(
        collection=done.collection,
        changed_ids=list(done.added_ids),
        already_in=done.already_in,
        missing_ids=list(done.missing_ids),
        photo_count=done.photo_count,
        note="" if done.added_ids else "Nothing was added (already there or not found).",
    )
    undo = (
        {"collection_op": {"kind": "add", "path": done.collection, "ids": list(done.added_ids)}}
        if done.added_ids
        else None
    )
    return Outcome(
        result, applied={"collection": done.collection, "added": len(done.added_ids)}, undo=undo
    )


def remove_from_collection(ctx: ToolContext, args: RemoveFromCollectionInput) -> Outcome:
    unique = list(dict.fromkeys(args.photo_ids))
    if len(unique) > BULK_CONFIRM_ABOVE:
        require_confirmation(
            ctx.confirmer,
            f"remove up to {len(unique)} photos from the collection {args.collection}",
            {"collection": args.collection, "photos": len(unique)},
        )
    done = ctx.lightroom.remove_from_collection(args.collection, unique)
    result = CollectionMembersResult(
        collection=done.collection,
        changed_ids=list(done.removed_ids),
        photo_count=done.photo_count,
        note="" if done.removed_ids else "Nothing was removed (none of them were in it).",
    )
    undo = (
        {
            "collection_op": {
                "kind": "remove",
                "path": done.collection,
                "ids": list(done.removed_ids),
            }
        }
        if done.removed_ids
        else None
    )
    return Outcome(
        result, applied={"collection": done.collection, "removed": len(done.removed_ids)}, undo=undo
    )


class GetCollectionPhotosInput(BaseModel):
    """List the photos in one collection (ids and file names, at most 200). Read only."""

    model_config = ConfigDict(extra="forbid")

    collection: str = Field(min_length=1)
    limit: int = Field(default=100, ge=1, le=MAX_PHOTOS_PER_CALL)


class CollectionPhotosResult(ToolResult):
    collection: str
    is_smart: bool
    total: int
    photos: list[dict[str, str | None]]


def get_collection_photos(ctx: ToolContext, args: GetCollectionPhotosInput) -> Outcome:
    got = ctx.lightroom.collection_photos(args.collection, args.limit)
    return Outcome(
        CollectionPhotosResult(
            collection=got.collection,
            is_smart=got.is_smart,
            total=got.total,
            photos=[{"id": p.id, "file_name": p.file_name} for p in got.photos],
        )
    )


class CreateKeywordInput(_PathInput):
    """Create a keyword, or a keyword hierarchy ("Places/Japan/Kyoto"), in the catalog. Existing
    levels are reused. To put keywords on photos use set_library_state or batch_set_library_state.
    A created keyword cannot be deleted by darkroom_ai (remove it in Lightroom if needed)."""


class KeywordCreatedResult(ToolResult):
    path: str
    created_levels: list[str]


def create_keyword(ctx: ToolContext, args: CreateKeywordInput) -> Outcome:
    made = ctx.lightroom.create_keyword(args.path)
    return Outcome(
        KeywordCreatedResult(path=made.path, created_levels=list(made.created_levels)),
        applied={"path": made.path, "created": list(made.created_levels)},
    )


# -- undo_collection_change -----------------------------------------------------------------------


class UndoCollectionChangeInput(BaseModel):
    """Take back darkroom_ai's collection changes made in THIS session (adds, removes, created
    collections and sets). scope "last" takes back the most recent one, "all" every one, newest
    first. A created collection or set is only deleted while it is empty."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["last", "all"] = "last"


class UndoCollectionChangeResult(ToolResult):
    undone_steps: int
    notes: list[str] = Field(default_factory=list)


def pending_collection_changes(ctx: ToolContext) -> list[JournalEntry]:
    entries = list(ctx.journal.entries())
    already = {
        str(entry_id)
        for entry in entries
        if entry.tool == UNDO_TOOL and entry.status == "ok" and entry.applied
        for entry_id in entry.applied.get("undone", [])
    }
    return [
        entry
        for entry in entries
        if entry.tool in COLLECTION_TOOLS
        and entry.undoable
        and entry.undo is not None
        and entry.undo.get("collection_op")
        and entry.session == ctx.journal.session
        and entry.id not in already
    ]


def undo_collection_change(ctx: ToolContext, args: UndoCollectionChangeInput) -> Outcome:
    pending = pending_collection_changes(ctx)
    if not pending:
        return Outcome(
            UndoCollectionChangeResult(
                undone_steps=0,
                notes=["There is no collection change of this session to take back."],
            )
        )
    chosen = list(reversed(pending)) if args.scope == "all" else pending[-1:]
    done: list[str] = []
    notes: list[str] = []
    for entry in chosen:
        assert entry.undo is not None
        op = entry.undo["collection_op"]
        kind, path = op["kind"], op["path"]
        if kind == "add":
            ctx.lightroom.remove_from_collection(path, op["ids"])
        elif kind == "remove":
            ctx.lightroom.add_to_collection(path, op["ids"])
        else:
            deleted = ctx.lightroom.delete_collection(path, op["type"])
            if not deleted.deleted:
                notes.append(f"{path} was already gone.")
        done.append(entry.id)
    return Outcome(
        UndoCollectionChangeResult(undone_steps=len(done), notes=notes),
        applied={"undone": done},
    )


__all__ = [
    "AddToCollectionInput",
    "CreateCollectionInput",
    "CreateCollectionSetInput",
    "CreateKeywordInput",
    "GetCatalogInfoInput",
    "GetCollectionPhotosInput",
    "ListCatalogsInput",
    "RemoveFromCollectionInput",
    "RequestCatalogAccessInput",
    "RequestFolderAccessInput",
    "UndoCollectionChangeInput",
]
