"""A simulated Lightroom: same interface as the real client, everything in memory.

No Adobe app is contacted and no real photo is read. Previews are flat grey images whose
brightness follows the simulated exposure, so analysis numbers react to edits.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from darkroom_ai.adapters.lightroom.models import (
    CatalogPage,
    ExportedTiff,
    FolderImport,
    FolderScan,
    MaskCreation,
    MaskInspection,
    MaskOperation,
    MaskValues,
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
from darkroom_ai.errors import DarkroomError, ErrorCode

DEFAULT_SETTINGS: dict[str, Any] = {
    "ProcessVersion": "15.4",
    "Exposure2012": 0,
    "Contrast2012": 0,
    "Highlights2012": 0,
    "Shadows2012": 0,
    "Whites2012": 0,
    "Blacks2012": 0,
    "WhiteBalance": "As Shot",
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


class SimulatedLightroom:
    """In-memory stand-in for Lightroom with the plug-in's rules (edits only on virtual copies).

    Used by the unit tests and by `darkroom models test`, so that a model's tool calls can be
    exercised without touching Lightroom or any real photo.
    """

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

    def get_metadata(self, photo_id: str) -> PhotoMetadata:
        self.calls.append("get_metadata")
        info = self._info(photo_id)
        return PhotoMetadata(
            id=info.id,
            file_name=info.file_name,
            path=info.path,
            file_format=info.file_format,
            is_virtual_copy=info.is_virtual_copy,
            width=info.width,
            height=info.height,
            camera_make="NIKON CORPORATION",
            camera_model="NIKON D7500",
            lens="18.0-140.0 mm f/3.5-5.6",
            focal_length="35 mm",
            aperture="f / 5.6",
            shutter_speed="1/250 sec",
            iso="200",
        )

    def list_catalog(
        self,
        offset: int = 0,
        limit: int = 50,
        *,
        include_virtual_copies: bool = False,
        path_contains: str | None = None,
    ) -> CatalogPage:
        self.calls.append("list_catalog")
        wanted = (path_contains or "").lower()
        ids = [
            pid
            for pid, photo in self.photos.items()
            if (include_virtual_copies or not photo["is_virtual_copy"])
            and wanted in str(photo["path"]).lower()
        ]
        window = ids[offset : offset + limit]
        photos = tuple(self.get_metadata(pid) for pid in window)
        return CatalogPage(total=len(ids), offset=offset, returned=len(photos), photos=photos)

    def scan_folder(self, folder: str, recursive: bool = False) -> FolderScan:
        self.calls.append("scan_folder")
        return FolderScan(
            folder=folder, new_count=3, new_files=(f"{folder}\\a.jpg",), already_in_catalog=1
        )

    def import_folder(
        self, folder: str, recursive: bool = False, max_files: int = 200
    ) -> FolderImport:
        self.calls.append("import_folder")
        return FolderImport(folder=folder, added=min(3, max_files), already_in_catalog=1)

    def create_mask(
        self, photo_id: str, mask_type: str, subtype: str | None = None
    ) -> MaskCreation:
        self.calls.append("create_mask")
        self._copy_only(photo_id)
        masks = list(self.settings[photo_id].get("MaskGroupBasedCorrections") or [])
        mask_id = f"mask-{len(masks) + 1}"
        masks.append(
            {
                "CorrectionName": f"{mask_type} {subtype or ''}".strip(),
                "CorrectionID": mask_id,
                "CorrectionMasks": [],
            }
        )
        self.settings[photo_id]["MaskGroupBasedCorrections"] = masks
        return MaskCreation(
            created=True, masks_before=len(masks) - 1, masks_after=len(masks), mask_id=mask_id
        )

    def _mask(self, photo_id: str, mask_id: str | None) -> dict[str, Any]:
        self._copy_only(photo_id)
        masks = self.settings[photo_id].get("MaskGroupBasedCorrections") or []
        for mask in masks:
            if mask_id is None or mask.get("CorrectionID") == mask_id:
                return mask  # type: ignore[no-any-return]
        raise DarkroomError(ErrorCode.ADOBE_FEATURE_UNAVAILABLE, f"no mask {mask_id}")

    def inspect_masks(self, photo_id: str) -> MaskInspection:
        self.calls.append("inspect_masks")
        self._copy_only(photo_id)
        masks = self.settings[photo_id].get("MaskGroupBasedCorrections") or []
        return MaskInspection(
            masks=[{"maskId": m.get("CorrectionID")} for m in masks],
            selected_mask=masks[-1].get("CorrectionID") if masks else None,
        )

    def set_mask_values(
        self, photo_id: str, mask_id: str | None, values: Mapping[str, Mapping[str, float]]
    ) -> MaskValues:
        self.calls.append("set_mask_values")
        mask = self._mask(photo_id, mask_id)
        before: dict[str, float] = {}
        after: dict[str, float] = {}
        for key, spec in values.items():
            current = float(mask.get(key, 0.0))
            step = spec.get("max_step", abs(spec["change"]))
            change = max(-step, min(step, spec["change"]))
            target = max(spec["min"], min(spec["max"], current + change))
            before[key] = current
            mask[key] = target
            after[key] = target
        return MaskValues(mask_id=str(mask["CorrectionID"]), before=before, after=after)

    def mask_operation(self, photo_id: str, mask_id: str, operation: str) -> MaskOperation:
        self.calls.append("mask_operation")
        mask = self._mask(photo_id, mask_id)
        masks = self.settings[photo_id]["MaskGroupBasedCorrections"]
        if operation == "delete":
            self.settings[photo_id]["MaskGroupBasedCorrections"] = [
                m for m in masks if m is not mask
            ]
        else:
            mask["Inverted"] = not mask.get("Inverted", False)
        count = len(self.settings[photo_id]["MaskGroupBasedCorrections"])
        return MaskOperation(
            operation=operation, masks_before=len(masks), masks_after=count, accepted=True
        )

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
