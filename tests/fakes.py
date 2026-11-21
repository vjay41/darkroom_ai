"""Fake collaborators for tests. They satisfy the Protocols in darkroom_ai.interfaces."""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from darkroom_ai.adapters.lightroom.models import (
    ExportedTiff,
    PhotoInfo,
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
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.safety.journal import JournalEntry, JournalStatus, new_entry


class InMemoryJournal:
    """ActionJournal that keeps entries in a list."""

    def __init__(self, session: str = "test-session") -> None:
        self.session = session
        self._entries: list[JournalEntry] = []

    def record(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        status: JournalStatus = "ok",
        applied: Mapping[str, Any] | None = None,
        undo: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> JournalEntry:
        entry = new_entry(
            self.session, tool, args, status=status, applied=applied, undo=undo, error=error
        )
        self._entries.append(entry)
        return entry

    def entries(self) -> list[JournalEntry]:
        return list(self._entries)


class AllowAll:
    """Confirmer that agrees to everything and remembers what it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        self.asked.append(action)
        return True


DEFAULT_SETTINGS: dict[str, Any] = {
    "ProcessVersion": "15.4",
    "Exposure2012": 0,
    "Contrast2012": 0,
    "Highlights2012": 0,
    "Shadows2012": 0,
    "Whites2012": 0,
    "Blacks2012": 0,
    "IncrementalTemperature": 0,
    "IncrementalTint": 0,
    "Temperature": 5500,
    "Tint": 0,
    "CropLeft": 0,
    "CropTop": 0,
    "CropRight": 1,
    "CropBottom": 1,
    "CropAngle": 0,
}


class FakeLightroom:
    """In-memory LightroomAdapter with the plug-in's rules (edits only on virtual copies)."""

    PRESETS: tuple[tuple[str, str, dict[str, Any]], ...] = (
        ("Soft Warm", "preset-1", {"Exposure2012": 0.4, "Contrast2012": 10}),
        ("Hard Push", "preset-2", {"Exposure2012": 4.5}),
        ("Twin", "preset-3", {"Contrast2012": 5}),
        ("Twin", "preset-4", {"Contrast2012": 6}),
    )

    def __init__(self, files_dir: Path, *, file_format: str = "JPG") -> None:
        self.files_dir = files_dir
        self.photos: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.add_photo("master-1", "TEST_0001.jpg", file_format=file_format)

    def add_photo(self, photo_id: str, file_name: str, *, file_format: str = "JPG") -> None:
        self.photos[photo_id] = {
            "id": photo_id,
            "file_name": file_name,
            "path": f"D:\\darkroom_test\\{file_name}",
            "file_format": file_format,
            "is_virtual_copy": False,
            "width": 6000,
            "height": 4000,
        }
        self.settings[photo_id] = dict(DEFAULT_SETTINGS)

    def _info(self, photo_id: str) -> PhotoInfo:
        self.calls.append("get_photo")
        if photo_id not in self.photos:
            raise DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                "No photo with that id",
                details={"plugin_code": "PHOTO_NOT_FOUND"},
            )
        return PhotoInfo.model_validate(self.photos[photo_id])

    def _copy_only(self, photo_id: str) -> None:
        if not self._info(photo_id).is_virtual_copy:
            raise DarkroomError(
                ErrorCode.GUARDRAIL_REJECTED,
                "Refused: develop edits are only made to virtual copies",
                details={"plugin_code": "NOT_A_VIRTUAL_COPY"},
            )

    # -- LightroomAdapter -----------------------------------------------------------------------

    def ping(self) -> PingInfo:
        return PingInfo(
            plugin_version="fake",
            protocol=1,
            lightroom_version="15.2.1",
            catalog_path="D:\\cat\\Test.lrcat",
            configured_catalog="D:\\cat\\Test.lrcat",
            writes_allowed=True,
        )

    def get_selection(self) -> Selection:
        return Selection(active=self._info("master-1"), photos=(self._info("master-1"),))

    def find_photo(self, path: str) -> PhotoInfo | None:
        for photo in self.photos.values():
            if photo["path"].lower() == path.lower() and not photo["is_virtual_copy"]:
                return PhotoInfo.model_validate(photo)
        return None

    def get_photo(self, photo_id: str) -> PhotoInfo:
        return self._info(photo_id)

    def get_develop_settings(
        self, photo_id: str, keys: Sequence[str] | None = None
    ) -> dict[str, Any]:
        self._info(photo_id)
        settings = self.settings[photo_id]
        if not keys:
            return dict(settings)
        return {key: settings.get(key, 0) for key in keys}

    def list_snapshots(self, photo_id: str) -> tuple[Snapshot, ...]:
        return ()

    def create_virtual_copy(self, photo_id: str, copy_name: str) -> PhotoInfo:
        self.calls.append("create_virtual_copy")
        master = self.photos[photo_id]
        copy_id = f"{photo_id}-copy{len(self.photos)}"
        self.photos[copy_id] = {
            **master,
            "id": copy_id,
            "is_virtual_copy": True,
            "master_id": photo_id,
            "copy_name": copy_name,
        }
        self.settings[copy_id] = dict(self.settings[photo_id])
        return PhotoInfo.model_validate(self.photos[copy_id])

    def create_snapshot(self, photo_id: str, name: str) -> SnapshotResult:
        return SnapshotResult(created=True, snapshot=Snapshot(id="s1", name=name))

    def apply_snapshot(self, photo_id: str, snapshot_id: str) -> Snapshot:
        return Snapshot(id=snapshot_id, name="snapshot")

    def apply_develop_settings(
        self, photo_id: str, settings: Mapping[str, Any], history_name: str = "edit"
    ) -> SettingsChange:
        self.calls.append("apply_develop_settings")
        self._copy_only(photo_id)
        current = self.settings[photo_id]
        before = {key: current.get(key, 0) for key in settings}
        current.update(settings)
        return SettingsChange(before=before, after=dict(settings))

    def export_preview(self, photo_id: str, name: str, long_edge: int = 1024) -> PreviewFile:
        self.calls.append("export_preview")
        self._info(photo_id)
        path = self.files_dir / "previews" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        level = 100 + round(float(self.settings[photo_id].get("Exposure2012", 0)) * 40)
        assert cv2.imwrite(str(path), np.full((40, 60, 3), max(0, min(255, level)), np.uint8))
        return PreviewFile(path=path, long_edge=long_edge)

    def export_tiff(self, photo_id: str, name: str, color_space: str = "sRGB") -> ExportedTiff:
        self.calls.append("export_tiff")
        self._copy_only(photo_id)
        path = self.files_dir / "handoff" / f"{name}.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(str(path), np.full((40, 60, 3), 30000, np.uint16))
        return ExportedTiff(path=path, bit_depth=16, color_space=color_space)

    def list_presets(self) -> tuple[PresetInfo, ...]:
        return tuple(PresetInfo(name=n, uuid=u, folder=f"Folder {u}") for n, u, _ in self.PRESETS)

    def apply_preset(self, photo_id: str, preset_uuid: str) -> PresetChange:
        self.calls.append("apply_preset")
        self._copy_only(photo_id)
        name, uuid, new = next(p for p in self.PRESETS if p[1] == preset_uuid)
        current = self.settings[photo_id]
        before = {k: current.get(k, 0) for k in new if current.get(k, 0) != new[k]}
        current.update(new)
        return PresetChange(
            preset=PresetInfo(name=name, uuid=uuid),
            before=before,
            after={k: new[k] for k in before},
        )


class FakePhotoshop:
    """PhotoshopAdapter that copies files and records what it was asked to do."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.open: set[Path] = set()

    def _note(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def _unique(self, path: Path) -> Path:
        candidate, counter = path, 2
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
            counter += 1
        return candidate

    def connect(self) -> PhotoshopInfo:
        return self.info()

    def info(self) -> PhotoshopInfo:
        return PhotoshopInfo(name="Adobe Photoshop", version="27.8.0", documents=len(self.open))

    def check_version(self) -> PhotoshopInfo:
        return self.info()

    def prepare_copy(self, source: Path) -> Path:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        target = self._unique(self.work_dir / f"{source.stem}_copy{source.suffix}")
        shutil.copy2(source, target)
        self._note("prepare_copy", source)
        return target

    def open_document(self, path: Path) -> DocumentInfo:
        self.open.add(path)
        self._note("open_document", path)
        return self.document_info(path)

    def run_script(
        self,
        code: str,
        *,
        document: Path | None = None,
        args: Sequence[str] | None = None,
        timeout_s: float | None = None,
    ) -> str:
        self._note("run_script", code)
        return ""

    def document_info(self, path: Path) -> DocumentInfo:
        return DocumentInfo(
            name=path.name, width=60, height=40, bits=16, mode="DocumentMode.RGB", layers=1
        )

    def add_layer(self, path: Path, name: str) -> int:
        self._note("add_layer", path, name)
        return 2

    def save_psd(self, path: Path, out_path: Path | None = None) -> Path:
        target = self._unique(out_path or path.with_name(f"{path.stem}_darkroom.psd"))
        target.write_bytes(b"8BPS")
        self._note("save_psd", target)
        return target

    def apply_mask_from_png(self, path: Path, mask_png: Path, layer_name: str = "subject") -> None:
        assert path in self.open, "the document must be open"
        assert mask_png.is_file()
        self._note("apply_mask_from_png", mask_png, layer_name)

    def add_fill_layer_below(
        self, path: Path, rgb: tuple[int, int, int], name: str = "background"
    ) -> None:
        self._note("add_fill_layer_below", rgb)

    def add_image_layer_below(self, path: Path, image: Path, name: str = "background") -> None:
        assert image.is_file() and image.parent == self.work_dir
        self._note("add_image_layer_below", image)

    def layers(self, path: Path) -> tuple[LayerInfo, ...]:
        return ()

    def save_jpg(self, path: Path, out_path: Path | None = None, quality: int = 10) -> Path:
        target = self._unique(out_path or path.with_name(f"{path.stem}_darkroom.jpg"))
        target.write_bytes(b"\xff\xd8jpg")
        self._note("save_jpg", target)
        return target

    def close_document(self, path: Path) -> None:
        self.open.discard(path)
        self._note("close_document", path)
