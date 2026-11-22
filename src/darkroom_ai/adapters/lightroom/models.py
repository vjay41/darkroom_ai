"""Typed results returned by the Lightroom plug-in."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Result(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class PingInfo(_Result):
    plugin_version: str
    instance: str | None = None  # which run of the plug-in answered (changes on every reload)
    protocol: int
    lightroom_version: str
    catalog_path: str
    configured_catalog: str
    writes_allowed: bool
    # When set, the plug-in refuses writes for photos whose files are outside this folder.
    photos_root: str | None = None
    # Added in plug-in 0.13.0: every catalog and photo folder the owner has allowed.
    allowed_catalogs: tuple[str, ...] = ()
    photos_roots: tuple[str, ...] = ()


class Snapshot(_Result):
    id: str
    name: str
    global_id: str | None = None


class PhotoInfo(_Result):
    """A photo in the catalog. `id` is the photo's UUID; all commands address photos by it."""

    id: str
    local_id: int | None = None
    file_name: str
    path: str | None = None
    file_format: str
    is_virtual_copy: bool = False
    master_id: str | None = None
    copy_name: str | None = None
    width: int | None = None
    height: int | None = None
    in_stack: bool | None = None
    """True if the photo is in a stack in its folder; None if Lightroom did not report it."""
    stack_collapsed: bool | None = None
    """True if that stack is collapsed (the photo cannot be shown in Develop); None if unknown."""
    stack_note: str | None = None
    """Why the stack fields are None, when Lightroom did not report them."""
    snapshots: tuple[Snapshot, ...] = ()


class PhotoMetadata(_Result):
    """Facts about a photo from the catalog and its EXIF. Missing values are None (not guessed)."""

    id: str
    file_name: str
    path: str | None = None
    file_format: str
    is_virtual_copy: bool = False
    width: int | None = None
    height: int | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    lens: str | None = None
    focal_length: str | None = None
    aperture: str | None = None
    shutter_speed: str | None = None
    iso: str | None = None
    exposure_bias: str | None = None
    flash: str | None = None
    capture_time: str | None = None
    rating: int | None = None
    pick_status: int | None = None
    """1 = picked, 0 = no flag, -1 = rejected; None if Lightroom did not report it."""
    has_develop_edits: bool | None = None
    """Lightroom's own edit flag. It is True for EVERY virtual copy from the moment it is made, so
    it cannot tell a touched copy from a bare one; read the develop settings to see what changed."""
    # Metadata keys Lightroom refused to give (key -> reason); empty when everything was read.
    lookup_errors: dict[str, str] | None = None


class CatalogPage(_Result):
    """One page of photos from the open catalog (master photos unless virtual copies are asked)."""

    total: int
    offset: int
    returned: int
    photos: tuple[PhotoMetadata, ...]
    filters_applied: tuple[str, ...] = ()
    """Which requested filters the plug-in confirms it applied (an older plug-in applies none)."""


class BridgeStatus(_Result):
    """The plug-in's own view of its health. Answered at once, even while a command is stuck."""

    instance: str | None = None
    plugin_version: str | None = None
    uptime_s: float = 0.0
    waiting: int = 0
    # Set while a command runs: command, running_s, budget_s, over_budget.
    busy: dict[str, Any] | None = None
    handled: int = 0
    failed: int = 0
    last_error: str | None = None


class FolderScan(_Result):
    """What a folder would add to the catalog (read only)."""

    folder: str
    new_count: int
    new_files: tuple[str, ...] = ()  # the first few only
    already_in_catalog: int = 0
    unsupported_files: int = 0
    truncated: bool = False
    # New files whose file NAME is already in the catalog (maybe the same photo in another folder).
    same_name_count: int = 0
    same_name_files: tuple[str, ...] = ()  # the first few only
    same_name_checked: bool = False  # False: catalog too large for the check, or old plug-in


class ImportFailure(_Result):
    path: str
    error: str


class FolderImport(_Result):
    folder: str
    added: int
    added_paths: tuple[str, ...] = ()
    failed: tuple[ImportFailure, ...] = ()
    already_in_catalog: int = 0
    left_for_next_call: int = 0


class MaskCreation(_Result):
    """Outcome of asking Lightroom to create a mask (checked by counting masks before and after)."""

    created: bool
    masks_before: int | None = None
    masks_after: int | None = None
    waited_s: float = 0.0
    mask_id: str | None = None


class MaskInspection(_Result):
    """What Lightroom reports for the masks of a photo (shape passed through as received)."""

    masks: Any = Field(default_factory=list)
    selected_mask: str | None = None


class MaskValues(_Result):
    """Local adjustment values of one mask before and after a change."""

    mask_id: str
    before: dict[str, float] = Field(default_factory=dict)
    after: dict[str, float] = Field(default_factory=dict)


class MaskOperation(_Result):
    operation: str
    masks_before: int
    masks_after: int
    accepted: Any = None


class Selection(_Result):
    active: PhotoInfo | None = None
    photos: tuple[PhotoInfo, ...] = ()


class LibraryState(_Result):
    """A photo's library fields: star rating, pick/reject flag and keywords (catalog data only)."""

    rating: int | None = None
    """Stars 1-5; None means unrated (the same as 0 when setting)."""
    pick_status: int = 0
    """1 = picked, 0 = no flag, -1 = rejected."""
    keywords: tuple[str, ...] = ()
    """Names of the keywords on this photo, sorted."""


class LibraryChange(_Result):
    """Library fields before and after a change, as Lightroom reports them."""

    before: LibraryState
    after: LibraryState


class SettingsChange(_Result):
    """Develop settings before and after an edit (only the keys that were sent)."""

    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)


class SnapshotResult(_Result):
    created: bool
    snapshot: Snapshot | None = None


class PreviewFile(_Result):
    path: Path
    long_edge: int


class ExportedTiff(_Result):
    path: Path
    bit_depth: int
    color_space: str


class CommandList(_Result):
    """Commands registered in the plug-in build that is running inside Lightroom."""

    plugin_version: str
    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()


class PresetInfo(_Result):
    name: str
    uuid: str
    folder: str = ""


class PresetChange(_Result):
    """A develop preset applied to a photo: every setting it changed, before and after."""

    preset: PresetInfo
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)


class AutoAdjustment(_Result):
    """What Lightroom's own Auto (tone and/or white balance) changed on ONE photo."""

    photo_id: str
    ran: list[str] = Field(default_factory=list)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)


class ResetResult(_Result):
    """A develop reset: which group, the save point taken first, and every plain setting changed."""

    photo_id: str
    scope: str
    snapshot: Snapshot | None = None
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)


class DistractionsResult(_Result):
    photo_id: str
    snapshot: Snapshot | None = None
    callback_called: bool = False
    retouch_before: int | None = None
    retouch_after: int | None = None
    waited_s: float = 0.0


class RetouchState(_Result):
    photo_id: str
    spot_count: int = 0
    spots: list[Any] = Field(default_factory=list)
    red_eye_count: int = 0
    red_eye: list[Any] = Field(default_factory=list)


class AiPanelState(_Result):
    photo_id: str
    panels: dict[str, Any] = Field(default_factory=dict)


class RotateResult(_Result):
    photo_id: str
    direction: str
    before: str | None = None
    after: str | None = None


class MetadataChange(_Result):
    photo_id: str
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)


class KeywordEntry(_Result):
    path: str
    synonyms: list[str] = Field(default_factory=list)


class KeywordList(_Result):
    keywords: list[KeywordEntry] = Field(default_factory=list)
    truncated: bool = False


class FoundPhoto(_Result):
    id: str
    file_name: str | None = None
    is_virtual_copy: bool = False


class FoundPhotos(_Result):
    photos: list[FoundPhoto] = Field(default_factory=list)
    count: int = 0
    truncated: bool = False


class PresetNames(_Result):
    presets: list[str] = Field(default_factory=list)


class PreviewsBuilt(_Result):
    requested: int = 0


class MetadataPresetApplied(_Result):
    preset: str
    applied: int = 0


class AiRunResult(_Result):
    """Lightroom's AI Enhance or Reflection Removal run: panel state before and after."""

    photo_id: str
    snapshot: Snapshot | None = None
    callback_called: bool = False
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    waited_s: float = 0.0


class CollectionNode(_Result):
    name: str
    kind: str  # "set" or "collection"
    photo_count: int | None = None
    is_smart: bool = False
    children: tuple[CollectionNode, ...] = ()


class FolderNode(_Result):
    name: str
    path: str
    photo_count: int = 0
    children: tuple[FolderNode, ...] = ()


class CatalogInfo(_Result):
    """What is in the open catalog (counts, collection tree, folder tree)."""

    catalog_path: str
    writes_allowed: bool
    total_photos: int
    virtual_copies: int = 0
    keyword_count: int = 0
    collections: tuple[CollectionNode, ...] = ()
    folders: tuple[FolderNode, ...] = ()
    truncated: bool = False


class CollectionCreated(_Result):
    path: str
    created: bool
    photo_count: int = 0


class CollectionMembers(_Result):
    collection: str
    added_ids: tuple[str, ...] = ()
    removed_ids: tuple[str, ...] = ()
    already_in: int = 0
    missing_ids: tuple[str, ...] = ()
    photo_count: int = 0


class CollectionDeleted(_Result):
    deleted: bool
    path: str


class CollectionPhoto(_Result):
    id: str
    file_name: str | None = None


class CollectionContents(_Result):
    collection: str
    is_smart: bool = False
    total: int
    photos: tuple[CollectionPhoto, ...] = ()


class KeywordCreated(_Result):
    path: str
    created_levels: tuple[str, ...] = ()
