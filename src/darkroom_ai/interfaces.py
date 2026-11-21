"""Small Protocol interfaces used for dependency injection.

The agent and the tools receive their collaborators (Lightroom adapter, Photoshop adapter, LLM
client, imaging toolkit, journal) through constructors and depend only on these Protocols, so they
can be tested with fakes and without Adobe apps or a model.

Interfaces are added in the phase that first verifies their real behavior
(Photoshop: phase 3, imaging: phase 4, LLM: phase 6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from darkroom_ai.adapters.lightroom.models import (
    CatalogPage,
    ExportedTiff,
    MaskCreation,
    PhotoInfo,
    PhotoMetadata,
    PingInfo,
    PresetChange,
    PresetInfo,
    PreviewFile,
    Selection,
    SettingsChange,
    Snapshot,
    SnapshotResult,
)
from darkroom_ai.adapters.photoshop.models import DocumentInfo, LayerInfo, PhotoshopInfo
from darkroom_ai.imaging.faces import FaceDetector
from darkroom_ai.imaging.masks import MaskProvider
from darkroom_ai.safety.journal import JournalEntry, JournalStatus

# Imaging interfaces are defined next to their implementations and re-exported here.
__all__ = [
    "ActionJournal",
    "FaceDetector",
    "LightroomAdapter",
    "MaskProvider",
    "PhotoshopAdapter",
    "ProcessControl",
]


class ProcessControl(Protocol):
    """Is an Adobe program running, and start it. Used only for app readiness."""

    def is_running(self, image_name: str) -> bool | None: ...

    def start(self, exe: Path, arguments: list[str] | None = None) -> None: ...


class LightroomAdapter(Protocol):
    """What the tools need from Lightroom. Photos are addressed by their catalog UUID."""

    def ping(self) -> PingInfo: ...

    def get_selection(self) -> Selection: ...

    def find_photo(self, path: str) -> PhotoInfo | None: ...

    def get_photo(self, photo_id: str) -> PhotoInfo: ...

    def get_metadata(self, photo_id: str) -> PhotoMetadata: ...

    def list_catalog(
        self,
        offset: int = 0,
        limit: int = 50,
        *,
        include_virtual_copies: bool = False,
        path_contains: str | None = None,
    ) -> CatalogPage: ...

    def get_develop_settings(
        self, photo_id: str, keys: Sequence[str] | None = None
    ) -> dict[str, Any]: ...

    def list_snapshots(self, photo_id: str) -> tuple[Snapshot, ...]: ...

    def create_virtual_copy(self, photo_id: str, copy_name: str) -> PhotoInfo: ...

    def create_snapshot(self, photo_id: str, name: str) -> SnapshotResult: ...

    def apply_snapshot(self, photo_id: str, snapshot_id: str) -> Snapshot: ...

    def apply_develop_settings(
        self, photo_id: str, settings: Mapping[str, Any], history_name: str = ...
    ) -> SettingsChange: ...

    def export_preview(self, photo_id: str, name: str, long_edge: int = ...) -> PreviewFile: ...

    def export_tiff(self, photo_id: str, name: str, color_space: str = ...) -> ExportedTiff: ...

    def create_mask(
        self, photo_id: str, mask_type: str, subtype: str | None = None
    ) -> MaskCreation: ...

    def list_presets(self) -> tuple[PresetInfo, ...]: ...

    def apply_preset(self, photo_id: str, preset_uuid: str) -> PresetChange: ...


class PhotoshopAdapter(Protocol):
    """What the tools need from Photoshop. Works only on copies inside its work folder."""

    def connect(self) -> PhotoshopInfo: ...

    def info(self) -> PhotoshopInfo: ...

    def check_version(self) -> PhotoshopInfo: ...

    def prepare_copy(self, source: Path) -> Path: ...

    def open_document(self, path: Path) -> DocumentInfo: ...

    def run_script(
        self,
        code: str,
        *,
        document: Path | None = None,
        args: Sequence[str] | None = None,
        timeout_s: float | None = None,
    ) -> str: ...

    def document_info(self, path: Path) -> DocumentInfo: ...

    def add_layer(self, path: Path, name: str) -> int: ...

    def save_psd(self, path: Path, out_path: Path | None = None) -> Path: ...

    def apply_mask_from_png(self, path: Path, mask_png: Path, layer_name: str = ...) -> None: ...

    def add_fill_layer_below(
        self, path: Path, rgb: tuple[int, int, int], name: str = ...
    ) -> None: ...

    def add_image_layer_below(self, path: Path, image: Path, name: str = ...) -> None: ...

    def layers(self, path: Path) -> tuple[LayerInfo, ...]: ...

    def save_jpg(self, path: Path, out_path: Path | None = None, quality: int = ...) -> Path: ...

    def close_document(self, path: Path) -> None: ...


class ActionJournal(Protocol):
    """Append-only record of every action, with what is needed to undo it."""

    @property
    def session(self) -> str:
        """Identifier of the current run; every entry it records carries it."""
        ...

    def record(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        status: JournalStatus = "ok",
        applied: Mapping[str, Any] | None = None,
        undo: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> JournalEntry: ...

    def entries(self) -> Sequence[JournalEntry]: ...
