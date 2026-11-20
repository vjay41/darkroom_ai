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
    PhotoInfo,
    PingInfo,
    PreviewFile,
    Selection,
    SettingsChange,
    Snapshot,
    SnapshotResult,
)
from darkroom_ai.adapters.photoshop.models import DocumentInfo, PhotoshopInfo
from darkroom_ai.safety.journal import JournalEntry, JournalStatus


class LightroomAdapter(Protocol):
    """What the tools need from Lightroom. Photos are addressed by their catalog UUID."""

    def ping(self) -> PingInfo: ...

    def get_selection(self) -> Selection: ...

    def find_photo(self, path: str) -> PhotoInfo | None: ...

    def get_photo(self, photo_id: str) -> PhotoInfo: ...

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


class PhotoshopAdapter(Protocol):
    """What the tools need from Photoshop. Works only on copies inside its work folder."""

    def connect(self) -> PhotoshopInfo: ...

    def info(self) -> PhotoshopInfo: ...

    def check_version(self) -> PhotoshopInfo: ...

    def prepare_copy(self, source: Path) -> Path: ...

    def open_document(self, path: Path) -> DocumentInfo: ...

    def run_script(
        self, code: str, *, document: Path | None = None, timeout_s: float | None = None
    ) -> str: ...

    def document_info(self, path: Path) -> DocumentInfo: ...

    def add_layer(self, path: Path, name: str) -> int: ...

    def save_psd(self, path: Path, out_path: Path | None = None) -> Path: ...

    def close_document(self, path: Path) -> None: ...


class ActionJournal(Protocol):
    """Append-only record of every action, with what is needed to undo it."""

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
