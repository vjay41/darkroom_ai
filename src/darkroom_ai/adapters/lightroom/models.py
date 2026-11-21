"""Typed results returned by the Lightroom plug-in."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Result(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class PingInfo(_Result):
    plugin_version: str
    protocol: int
    lightroom_version: str
    catalog_path: str
    configured_catalog: str
    writes_allowed: bool
    # When set, the plug-in refuses writes for photos whose files are outside this folder.
    photos_root: str | None = None


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
    has_develop_edits: bool | None = None
    # Metadata keys Lightroom refused to give (key -> reason); empty when everything was read.
    lookup_errors: dict[str, str] | None = None


class CatalogPage(_Result):
    """One page of photos from the open catalog (master photos unless virtual copies are asked)."""

    total: int
    offset: int
    returned: int
    photos: tuple[PhotoMetadata, ...]


class Selection(_Result):
    active: PhotoInfo | None = None
    photos: tuple[PhotoInfo, ...] = ()


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
