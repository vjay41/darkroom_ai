"""A simulated Lightroom: same interface as the real client, everything in memory.

No Adobe app is contacted and no real photo is read. Previews are flat grey images whose
brightness follows the simulated exposure, so analysis numbers react to edits.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, cast

import cv2
import numpy as np

from darkroom_ai.adapters.lightroom.models import (
    AiPanelState,
    AiRunResult,
    AutoAdjustment,
    BridgeStatus,
    CatalogInfo,
    CatalogPage,
    CollectionContents,
    CollectionCreated,
    CollectionDeleted,
    CollectionMembers,
    CollectionNode,
    CollectionPhoto,
    CommandList,
    DistractionsResult,
    ExportedTiff,
    FolderImport,
    FolderScan,
    FoundPhoto,
    FoundPhotos,
    KeywordCreated,
    KeywordEntry,
    KeywordList,
    LibraryChange,
    LibraryState,
    MaskCreation,
    MaskInspection,
    MaskOperation,
    MaskValues,
    MetadataChange,
    MetadataPresetApplied,
    PhotoInfo,
    PhotoMetadata,
    PingInfo,
    PresetChange,
    PresetInfo,
    PresetNames,
    PreviewFile,
    PreviewsBuilt,
    ResetResult,
    RetouchState,
    RotateResult,
    Selection,
    SettingsChange,
    Snapshot,
    SnapshotResult,
)
from darkroom_ai.compat import REQUIRED_PLUGIN_COMMANDS
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
    "ToneCurvePV2012": [0, 0, 255, 255],
    "ToneCurvePV2012Red": [0, 0, 255, 255],
    "ToneCurvePV2012Green": [0, 0, 255, 255],
    "ToneCurvePV2012Blue": [0, 0, 255, 255],
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
        ("Cropped Look", "preset-5", {"Contrast2012": 8, "CropLeft": 0.1}),
        ("Masked Look", "preset-6", {"MaskGroupBasedCorrections": [{"CorrectionName": "Sky"}]}),
    )

    def __init__(self, files_dir: Path, *, file_format: str = "JPG") -> None:
        self.files_dir = files_dir
        self.photos: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, dict[str, Any]] = {}
        self.library: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        # Collections by path ('Set/Sub/Name') -> photo ids; collection sets by path.
        self.collections: dict[str, list[str]] = {}
        self.collection_sets: set[str] = set()
        self.keywords: set[str] = set()
        # How many new photos scan_folder reports (and import_folder can add).
        self.importable = 3
        # Mask subtypes this simulated Lightroom does not create (as the real one with "people").
        self.refused_mask_subtypes: set[str] = set()
        # Photos that sit in a collapsed stack (Lightroom cannot show these in Develop).
        self.collapsed_stack_ids: set[str] = set()
        # The photo Lightroom shows in Develop; a new virtual copy is selected, as in Lightroom.
        self.selected_id = "master-1"
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
        self.library[photo_id] = {"rating": None, "pick_status": 0, "keywords": set()}

    def _info(self, photo_id: str) -> PhotoInfo:
        self.calls.append("get_photo")
        if photo_id not in self.photos:
            raise DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                "No photo with that id",
                details={"plugin_code": "PHOTO_NOT_FOUND"},
            )
        info = dict(self.photos[photo_id])
        if photo_id in self.collapsed_stack_ids:
            info.update(in_stack=True, stack_collapsed=True)
        return PhotoInfo.model_validate(info)

    def _copy_only(self, photo_id: str) -> None:
        if not self._info(photo_id).is_virtual_copy:
            raise DarkroomError(
                ErrorCode.GUARDRAIL_REJECTED,
                "Refused: develop edits are only made to virtual copies",
                details={"plugin_code": "NOT_A_VIRTUAL_COPY"},
            )

    # -- LightroomAdapter -----------------------------------------------------------------------

    def bridge_status(self) -> BridgeStatus:
        return BridgeStatus(instance="fake", plugin_version="0.11.0", uptime_s=1.0)

    def ping(self) -> PingInfo:
        return PingInfo(
            plugin_version="0.11.0",
            protocol=1,
            lightroom_version="15.2.1",
            catalog_path="D:\\cat\\Test.lrcat",
            configured_catalog="D:\\cat\\Test.lrcat",
            writes_allowed=True,
        )

    def list_commands(self) -> CommandList:
        self.calls.append("list_commands")
        return CommandList(
            plugin_version="0.9.4", read=tuple(sorted(REQUIRED_PLUGIN_COMMANDS)), write=()
        )

    def get_selection(self) -> Selection:
        shown = self._info(self.selected_id)
        return Selection(active=shown, photos=(shown,))

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
        min_rating: int | None = None,
        max_rating: int | None = None,
        flag: str | None = None,
        keyword: str | None = None,
        file_format: str | None = None,
        min_iso: int | None = None,
        max_iso: int | None = None,
        captured_from: str | None = None,
        captured_to: str | None = None,
    ) -> CatalogPage:
        self.calls.append("list_catalog")
        shooting = {
            "file_format": file_format,
            "min_iso": min_iso,
            "max_iso": max_iso,
            "captured_from": captured_from,
            "captured_to": captured_to,
        }
        wanted = (path_contains or "").lower()
        ids = [
            pid
            for pid, photo in self.photos.items()
            if (include_virtual_copies or not photo["is_virtual_copy"])
            and wanted in str(photo["path"]).lower()
            and self._passes_filters(pid, min_rating, max_rating, flag, keyword)
            and self._passes_shooting_filters(pid, shooting)
        ]
        window = ids[offset : offset + limit]
        photos = tuple(self.get_metadata(pid) for pid in window)
        used = tuple(
            name
            for name, value in (
                ("min_rating", min_rating),
                ("max_rating", max_rating),
                ("flag", flag),
                ("keyword", keyword),
                *shooting.items(),
            )
            if value is not None
        )
        return CatalogPage(
            total=len(ids),
            offset=offset,
            returned=len(photos),
            photos=photos,
            filters_applied=used,
        )

    def _passes_shooting_filters(self, photo_id: str, wanted: dict[str, Any]) -> bool:
        """File format, ISO and capture date; every simulated photo is ISO 200, shot 2026-01-15."""
        photo = self.photos[photo_id]
        iso, day = 200, "2026-01-15"
        if (
            wanted["file_format"]
            and str(photo["file_format"]).upper() != wanted["file_format"].upper()
        ):
            return False
        if wanted["min_iso"] is not None and iso < wanted["min_iso"]:
            return False
        if wanted["max_iso"] is not None and iso > wanted["max_iso"]:
            return False
        if wanted["captured_from"] and day < wanted["captured_from"]:
            return False
        return not (wanted["captured_to"] and day > wanted["captured_to"])

    def _passes_filters(
        self,
        photo_id: str,
        min_rating: int | None,
        max_rating: int | None,
        flag: str | None,
        keyword: str | None,
    ) -> bool:
        entry = self.library[photo_id]
        rating = entry["rating"] or 0
        if min_rating is not None and rating < min_rating:
            return False
        if max_rating is not None and rating > max_rating:
            return False
        if flag is not None and entry["pick_status"] != {"pick": 1, "none": 0, "reject": -1}[flag]:
            return False
        return keyword is None or keyword.lower() in {k.lower() for k in entry["keywords"]}

    def scan_folder(self, folder: str, recursive: bool = False) -> FolderScan:
        self.calls.append("scan_folder")
        return FolderScan(
            folder=folder,
            new_count=self.importable,
            new_files=(f"{folder}\\a.jpg",),
            already_in_catalog=1,
        )

    def import_folder(
        self, folder: str, recursive: bool = False, max_files: int = 200
    ) -> FolderImport:
        self.calls.append("import_folder")
        added = min(self.importable, max_files)
        paths = tuple(f"{folder}\\new_{n}.jpg" for n in range(added))
        return FolderImport(folder=folder, added=added, added_paths=paths, already_in_catalog=1)

    def create_mask(
        self, photo_id: str, mask_type: str, subtype: str | None = None
    ) -> MaskCreation:
        self.calls.append("create_mask")
        self._copy_only(photo_id)
        masks = list(self.settings[photo_id].get("MaskGroupBasedCorrections") or [])
        if subtype in self.refused_mask_subtypes:
            return MaskCreation(
                created=False, masks_before=len(masks), masks_after=len(masks), waited_s=25.0
            )
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

    def _snapshots(self, photo_id: str) -> list[tuple[Snapshot, dict[str, Any]]]:
        store = self.__dict__.setdefault("_snapshot_store", {})
        return cast(list[tuple[Snapshot, dict[str, Any]]], store.setdefault(photo_id, []))

    def list_snapshots(self, photo_id: str) -> tuple[Snapshot, ...]:
        return tuple(snap for snap, _ in self._snapshots(photo_id))

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
        self.library[copy_id] = {"rating": None, "pick_status": 0, "keywords": set()}
        self.selected_id = copy_id
        return PhotoInfo.model_validate(self.photos[copy_id])

    def _library_state(self, photo_id: str) -> LibraryState:
        entry = self.library[photo_id]
        return LibraryState(
            rating=entry["rating"],
            pick_status=entry["pick_status"],
            keywords=tuple(sorted(entry["keywords"], key=str.lower)),
        )

    def get_library_state(self, photo_id: str) -> LibraryState:
        self.calls.append("get_library_state")
        self._info(photo_id)
        return self._library_state(photo_id)

    def set_library_state(
        self,
        photo_id: str,
        *,
        rating: int | None = None,
        pick_status: int | None = None,
        add_keywords: Sequence[str] = (),
        remove_keywords: Sequence[str] = (),
    ) -> LibraryChange:
        self.calls.append("set_library_state")
        self._info(photo_id)
        before = self._library_state(photo_id)
        entry = self.library[photo_id]
        if rating is not None:
            entry["rating"] = rating or None
        if pick_status is not None:
            entry["pick_status"] = pick_status
        for name in add_keywords:
            if name.lower() not in {k.lower() for k in entry["keywords"]}:
                entry["keywords"].add(name)
        for name in remove_keywords:
            entry["keywords"] = {k for k in entry["keywords"] if k.lower() != name.lower()}
        return LibraryChange(before=before, after=self._library_state(photo_id))

    # -- catalog organisation ---------------------------------------------------------------

    @staticmethod
    def _clean_path(path: str) -> str:
        parts = [part.strip() for part in path.split("/") if part.strip()]
        if not parts:
            raise DarkroomError(ErrorCode.LR_COMMAND_FAILED, "The path is empty.")
        return "/".join(parts)

    def _need_set(self, path: str) -> None:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if parent and parent not in self.collection_sets:
            raise DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                f"The parent set does not exist: {parent}",
                details={"plugin_code": "COLLECTION_NOT_FOUND"},
            )

    def _need_collection(self, path: str) -> str:
        key = self._clean_path(path)
        if key not in self.collections:
            raise DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                f"No collection {key}",
                details={"plugin_code": "COLLECTION_NOT_FOUND"},
            )
        return key

    def catalog_info(self) -> CatalogInfo:
        self.calls.append("catalog_info")

        def node(path: str, kind: str) -> CollectionNode:
            name = path.rsplit("/", 1)[-1]
            if kind == "collection":
                return CollectionNode(name=name, kind=kind, photo_count=len(self.collections[path]))
            prefix = path + "/"
            kids = [
                node(p, "set")
                for p in sorted(self.collection_sets)
                if p.startswith(prefix) and "/" not in p[len(prefix) :]
            ] + [
                node(p, "collection")
                for p in sorted(self.collections)
                if p.startswith(prefix) and "/" not in p[len(prefix) :]
            ]
            return CollectionNode(name=name, kind=kind, children=tuple(kids))

        top = [node(p, "set") for p in sorted(self.collection_sets) if "/" not in p] + [
            node(p, "collection") for p in sorted(self.collections) if "/" not in p
        ]
        return CatalogInfo(
            catalog_path=self.ping().catalog_path,
            writes_allowed=True,
            total_photos=len(self.photos),
            virtual_copies=sum(1 for p in self.photos.values() if p["is_virtual_copy"]),
            keyword_count=len(self.keywords),
            collections=tuple(top),
        )

    def create_collection_set(self, path: str) -> CollectionCreated:
        self.calls.append("create_collection_set")
        key = self._clean_path(path)
        self._need_set(key)
        created = key not in self.collection_sets
        self.collection_sets.add(key)
        return CollectionCreated(path=key, created=created)

    def create_collection(self, path: str) -> CollectionCreated:
        self.calls.append("create_collection")
        key = self._clean_path(path)
        self._need_set(key)
        created = key not in self.collections
        self.collections.setdefault(key, [])
        return CollectionCreated(path=key, created=created, photo_count=len(self.collections[key]))

    def add_to_collection(self, collection: str, photo_ids: Sequence[str]) -> CollectionMembers:
        self.calls.append("add_to_collection")
        key = self._need_collection(collection)
        members = self.collections[key]
        added, already, missing = [], 0, []
        for photo_id in photo_ids:
            if photo_id not in self.photos:
                missing.append(photo_id)
            elif photo_id in members or photo_id in added:
                already += 1
            else:
                added.append(photo_id)
        members.extend(added)
        return CollectionMembers(
            collection=key,
            added_ids=tuple(added),
            already_in=already,
            missing_ids=tuple(missing),
            photo_count=len(members),
        )

    def remove_from_collection(
        self, collection: str, photo_ids: Sequence[str]
    ) -> CollectionMembers:
        self.calls.append("remove_from_collection")
        key = self._need_collection(collection)
        removed = [p for p in photo_ids if p in self.collections[key]]
        self.collections[key] = [p for p in self.collections[key] if p not in removed]
        return CollectionMembers(
            collection=key, removed_ids=tuple(removed), photo_count=len(self.collections[key])
        )

    def delete_collection(self, path: str, kind: str) -> CollectionDeleted:
        self.calls.append("delete_collection")
        key = self._clean_path(path)
        if kind == "collection":
            if key not in self.collections:
                return CollectionDeleted(deleted=False, path=key)
            if self.collections[key]:
                raise DarkroomError(
                    ErrorCode.LR_COMMAND_FAILED,
                    "Refused: the collection still has photos.",
                    details={"plugin_code": "NOT_EMPTY"},
                )
            del self.collections[key]
            return CollectionDeleted(deleted=True, path=key)
        if key not in self.collection_sets:
            return CollectionDeleted(deleted=False, path=key)
        inside = [p for p in (*self.collections, *self.collection_sets) if p.startswith(key + "/")]
        if inside:
            raise DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                "Refused: the set still contains collections or sets.",
                details={"plugin_code": "NOT_EMPTY"},
            )
        self.collection_sets.discard(key)
        return CollectionDeleted(deleted=True, path=key)

    def collection_photos(self, collection: str, limit: int = 100) -> CollectionContents:
        self.calls.append("collection_photos")
        key = self._need_collection(collection)
        ids = self.collections[key]
        shown = tuple(
            CollectionPhoto(id=i, file_name=self.photos[i]["file_name"]) for i in ids[:limit]
        )
        return CollectionContents(collection=key, total=len(ids), photos=shown)

    def create_keyword(self, path: str) -> KeywordCreated:
        self.calls.append("create_keyword")
        key = self._clean_path(path)
        created = []
        for name in key.split("/"):
            if name.lower() not in {k.lower() for k in self.keywords}:
                self.keywords.add(name)
                created.append(name)
        return KeywordCreated(path=key, created_levels=tuple(created))

    def create_snapshot(self, photo_id: str, name: str) -> SnapshotResult:
        self.calls.append("create_snapshot")
        store = self._snapshots(photo_id)
        snap = Snapshot(id=f"s{len(store) + 1}", name=name)
        store.append((snap, dict(self.settings[photo_id])))
        return SnapshotResult(created=True, snapshot=snap)

    def apply_snapshot(self, photo_id: str, snapshot_id: str) -> Snapshot:
        self.calls.append("apply_snapshot")
        self._copy_only(photo_id)
        for snap, saved in self._snapshots(photo_id):
            if snap.id == snapshot_id:
                self.settings[photo_id] = dict(saved)
                return snap
        raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, "No matching snapshot on this photo")

    RESET_KEYS: ClassVar[dict[str, tuple[str, ...]]] = {
        "crop": ("CropLeft", "CropTop", "CropRight", "CropBottom", "CropAngle"),
        "transforms": ("PerspectiveVertical", "PerspectiveHorizontal", "PerspectiveRotate"),
        "masks": ("MaskGroupBasedCorrections",),
        "spots": ("RetouchAreas",),
        "red_eye": ("RedEyeInfo",),
    }

    def reset_develop(self, photo_id: str, scope: str) -> ResetResult:
        self.calls.append("reset_develop")
        self._copy_only(photo_id)
        snapshot = self.create_snapshot(photo_id, f"darkroom_ai before reset {scope}").snapshot
        current = self.settings[photo_id]
        keep = {"ProcessVersion"}  # a reset never changes the process version
        keys = (
            [k for k in current if k not in keep]
            if scope == "all"
            else list(self.RESET_KEYS[scope])
        )
        before = {k: current[k] for k in keys if k in current}
        for key in before:
            del current[key]
        return ResetResult(
            photo_id=photo_id, scope=scope, snapshot=snapshot, before=before, after={}
        )

    def remove_distracting_people(self, photo_id: str) -> DistractionsResult:
        self.calls.append("remove_distracting_people")
        self._copy_only(photo_id)
        snapshot = self.create_snapshot(photo_id, "darkroom_ai before remove people").snapshot
        areas = list(self.settings[photo_id].get("RetouchAreas", []))
        added = [] if getattr(self, "people_blocked", False) else [{"SpotType": "removal"}]
        self.settings[photo_id]["RetouchAreas"] = [*areas, *added]
        return DistractionsResult(
            photo_id=photo_id,
            snapshot=snapshot,
            callback_called=True,
            retouch_before=len(areas),
            retouch_after=len(areas) + len(added),
        )

    def get_retouch_state(self, photo_id: str) -> RetouchState:
        self._info(photo_id)
        areas = list(self.settings[photo_id].get("RetouchAreas", []))
        eyes = list(self.settings[photo_id].get("RedEyeInfo", []))
        return RetouchState(
            photo_id=photo_id,
            spot_count=len(areas),
            spots=areas,
            red_eye_count=len(eyes),
            red_eye=eyes,
        )

    def get_ai_panel_state(self, photo_id: str) -> AiPanelState:
        self._info(photo_id)
        return AiPanelState(photo_id=photo_id, panels={"enhance": {"ok": True, "value": {}}})

    def rotate_photo(self, photo_id: str, direction: str) -> RotateResult:
        self.calls.append("rotate_photo")
        self._copy_only(photo_id)
        order = ["AB", "BC", "CD", "DA"]
        now = self.settings[photo_id].get("Orientation", "AB")
        step = 1 if direction == "right" else -1
        new = order[(order.index(now) + step) % 4]
        self.settings[photo_id]["Orientation"] = new
        return RotateResult(photo_id=photo_id, direction=direction, before=now, after=new)

    def set_photo_metadata(self, photo_id: str, fields: Mapping[str, str]) -> MetadataChange:
        self.calls.append("set_photo_metadata")
        self._info(photo_id)
        store = self.__dict__.setdefault("_metadata_store", {}).setdefault(photo_id, {})
        before = {k: store.get(k, "") for k in fields}
        store.update(fields)
        return MetadataChange(photo_id=photo_id, before=before, after={k: store[k] for k in fields})

    def find_photos(self, conditions: Sequence[Mapping[str, Any]], combine: str) -> FoundPhotos:
        self.calls.append("find_photos")
        found = [
            FoundPhoto(
                id=i, file_name=p["file_name"], is_virtual_copy=bool(p.get("is_virtual_copy"))
            )
            for i, p in self.photos.items()
        ]
        return FoundPhotos(photos=found, count=len(found))

    def create_smart_collection(
        self, path: str, conditions: Sequence[Mapping[str, Any]], combine: str
    ) -> CollectionCreated:
        self.calls.append("create_smart_collection")
        return CollectionCreated(path=path, created=True, photo_count=0)

    def build_smart_previews(self, photo_ids: Sequence[str]) -> PreviewsBuilt:
        self.calls.append("build_smart_previews")
        return PreviewsBuilt(requested=len(photo_ids))

    def list_metadata_presets(self) -> PresetNames:
        return PresetNames(presets=["Copyright 2026", "Wedding client"])

    def list_filename_presets(self) -> PresetNames:
        return PresetNames(presets=["Date - Sequence"])

    def apply_metadata_preset(self, photo_ids: Sequence[str], preset: str) -> MetadataPresetApplied:
        self.calls.append("apply_metadata_preset")
        if preset not in self.list_metadata_presets().presets:
            raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, "No metadata preset with that name")
        return MetadataPresetApplied(preset=preset, applied=len(photo_ids))

    def enhance(self, photo_id: str, feature: str, amount: int | None = None) -> AiRunResult:
        self.calls.append("enhance")
        self._copy_only(photo_id)
        snapshot = self.create_snapshot(photo_id, f"darkroom_ai before enhance {feature}").snapshot
        self.settings[photo_id][f"Enhance_{feature}"] = amount or 50
        return AiRunResult(
            photo_id=photo_id,
            snapshot=snapshot,
            callback_called=True,
            before={"denoiseState": False},
            after={"denoiseState": True, "denoiseAmount": amount or 50},
        )

    def reflection_removal(
        self, photo_id: str, amount: int | None = None, quality: str = "preview"
    ) -> AiRunResult:
        self.calls.append("reflection_removal")
        self._copy_only(photo_id)
        snapshot = self.create_snapshot(photo_id, "darkroom_ai before reflection removal").snapshot
        return AiRunResult(
            photo_id=photo_id,
            snapshot=snapshot,
            callback_called=True,
            before={"checkboxState": False, "quality": quality},
            after={"checkboxState": True, "amount": amount if amount is not None else 100},
        )

    def list_keywords(self) -> KeywordList:
        return KeywordList(
            keywords=[KeywordEntry(path="Places/Japan"), KeywordEntry(path="Family")]
        )

    def apply_develop_settings(
        self, photo_id: str, settings: Mapping[str, Any], history_name: str = "edit"
    ) -> SettingsChange:
        self.calls.append("apply_develop_settings")
        self._copy_only(photo_id)
        current = self.settings[photo_id]
        before = {key: current.get(key, 0) for key in settings}
        current.update(settings)
        return SettingsChange(before=before, after=dict(settings))

    def export_preview(
        self, photo_id: str, name: str, long_edge: int = 1024, quality: float = 0.85
    ) -> PreviewFile:
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

    def get_preset_settings(self, preset_uuid: str) -> dict[str, Any]:
        for _name, uuid, settings in self.PRESETS:
            if uuid == preset_uuid:
                return dict(settings)
        raise DarkroomError(ErrorCode.GUARDRAIL_REJECTED, "No develop preset with that id")

    def auto_adjust(
        self, photo_id: str, tone: bool = True, white_balance: bool = False
    ) -> AutoAdjustment:
        """Like Lightroom's Auto: values depend on the individual photo (here: its id and level)."""
        self.calls.append("auto_adjust")
        self._copy_only(photo_id)
        current = self.settings[photo_id]
        seed = sum(ord(c) for c in photo_id) % 7
        wanted: dict[str, Any] = {}
        ran: list[str] = []
        if tone:
            ran.append("setAutoTone")
            exposure = float(current.get("Exposure2012", 0.0))
            wanted = {
                "Exposure2012": round(seed * 0.4 - 1.0 - exposure * 0.5, 2),
                "Contrast2012": 5 + seed * 3,
                "Highlights2012": -20 - seed,
                "Shadows2012": 10 + seed * 2,
            }
        if white_balance:
            ran.append("setAutoWhiteBalance")
            wanted["IncrementalTemperature"] = seed * 4 - 10
        before = {k: current.get(k) for k, v in wanted.items() if current.get(k) != v}
        current.update(wanted)
        return AutoAdjustment(
            photo_id=photo_id, ran=ran, before=before, after={k: wanted[k] for k in before}
        )

    def apply_preset(self, photo_id: str, preset_uuid: str) -> PresetChange:
        self.calls.append("apply_preset")
        self._copy_only(photo_id)
        name, uuid, new = next(p for p in self.PRESETS if p[1] == preset_uuid)
        current = self.settings[photo_id]
        # Like the plug-in: a list-valued setting the photo did not have counts as an empty list.
        before = {
            k: current.get(k, [] if isinstance(new[k], list) else 0)
            for k in new
            if current.get(k, [] if isinstance(new[k], list) else 0) != new[k]
        }
        current.update(new)
        return PresetChange(
            preset=PresetInfo(name=name, uuid=uuid),
            before=before,
            after={k: new[k] for k in before},
        )
