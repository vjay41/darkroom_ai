"""A fake Lightroom plug-in for tests: same wire protocol as Bridge.lua, in-memory catalog.

Listens on two 127.0.0.1 ports (requests in, responses out) and mirrors the plug-in's rules:
token check, write refusal on the wrong catalog, photos addressed by UUID.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

WRITE_COMMANDS = {
    "create_virtual_copy",
    "create_snapshot",
    "apply_snapshot",
    "apply_settings",
    "export_preview",
    "export_tiff",
    "apply_preset",
    "auto_adjust",
    "reset_develop",
    "remove_distracting_people",
    "rotate_photo",
    "set_photo_metadata",
    "create_mask",
    "import_folder",
    "set_mask_values",
    "mask_operation",
    "set_library_state",
    "create_collection",
    "create_collection_set",
    "add_to_collection",
    "remove_from_collection",
    "delete_collection",
    "create_keyword",
}


class PluginError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class FakePhoto:
    id: str
    file_name: str
    file_format: str = "JPG"
    is_virtual_copy: bool = False
    master_id: str | None = None
    copy_name: str | None = None
    settings: dict[str, Any] = field(default_factory=lambda: {"Exposure": 0.0, "Contrast": 0})
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    rating: int | None = None
    pick_status: int = 0
    keywords: list[str] = field(default_factory=list)
    iso: int | None = 200
    captured: str | None = "2026-01-15"

    def summary(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "local_id": abs(hash(self.id)) % 100000,
            "file_name": self.file_name,
            "path": f"D:\\photos\\{self.file_name}",
            "file_format": self.file_format,
            "is_virtual_copy": self.is_virtual_copy,
            "width": 6000,
            "height": 4000,
            "snapshots": [
                {"id": s["id"], "name": s["name"], "global_id": s["id"]} for s in self.snapshots
            ],
        }
        if self.master_id:
            data["master_id"] = self.master_id
        if self.copy_name:
            data["copy_name"] = self.copy_name
        return data


class FakeLightroomPlugin:
    def __init__(
        self,
        *,
        token: str = "t" * 64,
        catalog_path: str = "D:\\cat\\Test.lrcat",
        configured_catalog: str | None = None,
        silent: bool = False,
        garbage: bool = False,
        photos_root: str | None = None,
    ) -> None:
        self.photos_root = photos_root
        self.token = token
        self.catalog_path = catalog_path
        self.configured_catalog = configured_catalog or catalog_path
        self.silent = silent
        self.garbage = garbage
        self.photos: dict[str, FakePhoto] = {"uuid-1": FakePhoto("uuid-1", "DSC_0001.jpg")}
        self.selected: list[str] = ["uuid-1"]
        self.collections: dict[str, list[str]] = {}
        self.commands: list[str] = []
        self.deadlines: list[Any] = []
        self.exports: list[dict[str, Any]] = []
        self.tiffs: list[str] = []
        self._stop = threading.Event()
        self._request_listener = self._listen()
        self._response_listener = self._listen()
        self.request_port: int = self._request_listener.getsockname()[1]
        self.response_port: int = self._response_listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    @staticmethod
    def _listen() -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.1)
        return listener

    def __enter__(self) -> FakeLightroomPlugin:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._request_listener.close()
        self._response_listener.close()

    @property
    def writes_allowed(self) -> bool:
        return self.catalog_path.lower() == self.configured_catalog.lower()

    # -- wire ---------------------------------------------------------------------------------

    def _accept(self, listener: socket.socket) -> socket.socket | None:
        while not self._stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            connection.settimeout(0.1)
            return connection
        return None

    def _serve(self) -> None:
        while not self._stop.is_set():
            responses = self._accept(self._response_listener)
            requests = self._accept(self._request_listener)
            if responses is None or requests is None:
                break
            with responses, requests:
                self._session(requests, responses)

    def _session(self, requests: socket.socket, responses: socket.socket) -> None:
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = requests.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                if self.silent:
                    continue
                if self.garbage:
                    responses.sendall(b"this is not json\n")
                    continue
                reply = self._handle(json.loads(line))
                try:
                    responses.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                except OSError:
                    return

    # -- commands -----------------------------------------------------------------------------

    def _handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        command = request.get("command", "")
        self.commands.append(command)
        self.deadlines.append(request.get("deadline"))
        try:
            if (
                isinstance(request.get("deadline"), int | float)
                and request["deadline"] < time.time()
            ):
                raise PluginError(
                    "REQUEST_EXPIRED", "The client stopped waiting; nothing was executed"
                )
            if request.get("token") != self.token:
                raise PluginError("UNAUTHORIZED", "Missing or wrong token")
            handler = getattr(self, f"_cmd_{command}", None)
            if handler is None:
                raise PluginError("UNKNOWN_COMMAND", f"Unknown command: {command}")
            if command in WRITE_COMMANDS and not self.writes_allowed:
                raise PluginError(
                    "WRONG_CATALOG",
                    f"Refused: the open catalog ({self.catalog_path}) is not the configured "
                    f"catalog ({self.configured_catalog})",
                )
            return {"id": request_id, "ok": True, "result": handler(request.get("params") or {})}
        except PluginError as exc:
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            }

    def _photo(self, params: dict[str, Any]) -> FakePhoto:
        photo = self.photos.get(params.get("photo_id", ""))
        if photo is None:
            raise PluginError("PHOTO_NOT_FOUND", "No photo with that id in the open catalog")
        return photo

    def _writable(self, params: dict[str, Any], *, needs_copy: bool = False) -> FakePhoto:
        photo = self._photo(params)
        if self.photos_root:
            prefix = self.photos_root.lower().rstrip("\\") + "\\"
            if not photo.summary()["path"].lower().startswith(prefix):
                raise PluginError("PHOTO_OUTSIDE_ROOT", "Refused: not under the allowed folder")
        if needs_copy and not photo.is_virtual_copy:
            raise PluginError("NOT_A_VIRTUAL_COPY", "Refused: only virtual copies are edited")
        return photo

    def _cmd_ping(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "plugin_version": "0.2.0",
            "protocol": 1,
            "lightroom_version": "15.2.1",
            "catalog_path": self.catalog_path,
            "configured_catalog": self.configured_catalog,
            "writes_allowed": self.writes_allowed,
        }

    def _cmd_bridge_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"instance": "fake-1", "plugin_version": "fake", "uptime_s": 2.0, "waiting": 0}

    def _cmd_list_commands(self, _: dict[str, Any]) -> dict[str, Any]:
        # bridge_status is answered by Bridge.lua itself (never queued), not by Commands.lua.
        names = sorted(
            name[5:]
            for name in dir(self)
            if name.startswith("_cmd_") and name != "_cmd_bridge_status"
        )
        return {
            "plugin_version": "0.4.0",
            "read": [name for name in names if name not in WRITE_COMMANDS],
            "write": [name for name in names if name in WRITE_COMMANDS],
        }

    def _cmd_selftest(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params.get("value")}

    def _cmd_get_selection(self, _: dict[str, Any]) -> dict[str, Any]:
        photos = [self.photos[i].summary() for i in self.selected]
        return {"active": photos[0], "photos": photos} if photos else {"photos": []}

    def _metadata(self, photo: FakePhoto) -> dict[str, Any]:
        info = photo.summary()
        return {
            "id": info["id"],
            "file_name": info["file_name"],
            "path": info["path"],
            "file_format": info["file_format"],
            "is_virtual_copy": info["is_virtual_copy"],
            "camera_model": "NIKON D7500",
            "iso": "200",
        }

    def _cmd_get_metadata(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._metadata(self._photo(params))

    def _cmd_list_photos(self, params: dict[str, Any]) -> dict[str, Any]:
        wanted = str(params.get("path_contains", "")).lower()
        status = {"pick": 1, "none": 0, "reject": -1}
        chosen = [
            photo
            for photo in self.photos.values()
            if (params.get("include_virtual_copies") or not photo.is_virtual_copy)
            and wanted in photo.summary()["path"].lower()
            and (photo.rating or 0) >= params.get("min_rating", 0)
            and (photo.rating or 0) <= params.get("max_rating", 5)
            and ("flag" not in params or photo.pick_status == status[params["flag"]])
            and (
                "file_format" not in params
                or photo.file_format.upper() == str(params["file_format"]).upper()
            )
            and (photo.iso is not None or not ({"min_iso", "max_iso"} & params.keys()))
            and (photo.iso or 0) >= params.get("min_iso", 0)
            and (photo.iso or 0) <= params.get("max_iso", 10**9)
            and (
                photo.captured is not None or not ({"captured_from", "captured_to"} & params.keys())
            )
            and (photo.captured or "") >= params.get("captured_from", "")
            and (photo.captured or "9999") <= params.get("captured_to", "9999-99-99")
            and (
                "keyword" not in params
                or params["keyword"].lower() in {k.lower() for k in photo.keywords}
            )
        ]
        applied = [
            n
            for n in (
                "min_rating",
                "max_rating",
                "flag",
                "keyword",
                "file_format",
                "min_iso",
                "max_iso",
                "captured_from",
                "captured_to",
            )
            if n in params
        ]
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 50))
        window = chosen[offset : offset + limit]
        return {
            "total": len(chosen),
            "offset": offset,
            "returned": len(window),
            "photos": [self._metadata(p) for p in window],
            "filters_applied": applied,
        }

    def _cmd_create_mask(self, params: dict[str, Any]) -> dict[str, Any]:
        self._photo(params)
        masks = self.__dict__.setdefault("_masks", {}).setdefault(params["photo_id"], {})
        masks[f"m{len(masks) + 1}"] = {}
        return {
            "created": True,
            "masks_before": len(masks) - 1,
            "masks_after": len(masks),
            "waited_s": 0.0,
            "mask_id": f"m{len(masks)}",
        }

    def _cmd_inspect_masks(self, params: dict[str, Any]) -> dict[str, Any]:
        self._photo(params)
        masks = self.__dict__.setdefault("_masks", {}).get(params["photo_id"], {})
        return {
            "masks": [{"maskId": key} for key in masks],
            "selected_mask": next(iter(masks), None),
        }

    def _cmd_set_mask_values(self, params: dict[str, Any]) -> dict[str, Any]:
        self._photo(params)
        masks = self.__dict__.setdefault("_masks", {}).get(params["photo_id"], {})
        mask_id = params.get("mask_id") or next(iter(masks), None)
        if mask_id not in masks:
            raise PluginError("MASK_NOT_FOUND", "No mask is selected in Lightroom")
        before, after = {}, {}
        for key, spec in params["values"].items():
            current = float(masks[mask_id].get(key, 0.0))
            step = spec.get("max_step", abs(spec["change"]))
            change = max(-step, min(step, spec["change"]))
            masks[mask_id][key] = max(spec["min"], min(spec["max"], current + change))
            before[key], after[key] = current, masks[mask_id][key]
        return {"mask_id": mask_id, "before": before, "after": after}

    def _cmd_mask_operation(self, params: dict[str, Any]) -> dict[str, Any]:
        self._photo(params)
        masks = self.__dict__.setdefault("_masks", {}).get(params["photo_id"], {})
        if params["mask_id"] not in masks:
            raise PluginError("MASK_OPERATION_FAILED", "unknown mask")
        before = len(masks)
        if params["operation"] == "delete":
            del masks[params["mask_id"]]
        return {
            "operation": params["operation"],
            "masks_before": before,
            "masks_after": len(masks),
            "accepted": True,
        }

    def _cmd_scan_folder(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "folder": params["folder"],
            "new_count": 2,
            "new_files": ["a.jpg", "b.jpg"],
            "already_in_catalog": 1,
            "unsupported_files": 0,
            "truncated": False,
        }

    def _cmd_import_folder(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"folder": params["folder"], "added": 2, "failed": [], "already_in_catalog": 1}

    def _cmd_find_photo(self, params: dict[str, Any]) -> dict[str, Any]:
        wanted = str(params.get("path", "")).lower()
        for photo in self.photos.values():
            if photo.summary()["path"].lower() == wanted and not photo.is_virtual_copy:
                return {"found": True, "photo": photo.summary()}
        return {"found": False}

    def _cmd_get_photo(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._photo(params)
        result = photo.summary()
        if params.get("include_settings"):
            keys = params.get("keys") or list(photo.settings)
            result["settings"] = {k: photo.settings[k] for k in keys if k in photo.settings}
        return result

    def _cmd_list_snapshots(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"snapshots": self._photo(params).summary()["snapshots"]}

    def _cmd_create_virtual_copy(self, params: dict[str, Any]) -> dict[str, Any]:
        master = self._writable(params)
        copy = FakePhoto(
            id=f"{master.id}-vc{len(self.photos)}",
            file_name=master.file_name,
            file_format=master.file_format,
            is_virtual_copy=True,
            master_id=master.id,
            copy_name=params.get("copy_name"),
            settings=dict(master.settings),
        )
        self.photos[copy.id] = copy
        return copy.summary()

    def _cmd_create_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params)
        name = params.get("name")
        if not name:
            raise PluginError("INVALID_PARAMS", "name is required")
        existing = next((s for s in photo.snapshots if s["name"] == name), None)
        if existing:
            return {"created": False, "snapshot": {"id": existing["id"], "name": name}}
        snapshot = {
            "id": f"snap-{len(photo.snapshots) + 1}",
            "name": name,
            "settings": dict(photo.settings),
        }
        photo.snapshots.append(snapshot)
        return {"created": True, "snapshot": {"id": snapshot["id"], "name": name}}

    def _cmd_apply_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params, needs_copy=True)
        snapshot = next((s for s in photo.snapshots if s["id"] == params.get("snapshot_id")), None)
        if snapshot is None:
            raise PluginError("SNAPSHOT_NOT_FOUND", "No matching snapshot on this photo")
        photo.settings = dict(snapshot["settings"])
        return {"applied": {"id": snapshot["id"], "name": snapshot["name"]}}

    def _cmd_apply_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params, needs_copy=True)
        settings = params.get("settings")
        if not isinstance(settings, dict) or not settings:
            raise PluginError("INVALID_PARAMS", "settings must be a non-empty object")
        before = {k: photo.settings.get(k) for k in settings}
        photo.settings.update(settings)
        return {"before": before, "after": {k: photo.settings[k] for k in settings}}

    @staticmethod
    def _library(photo: FakePhoto) -> dict[str, Any]:
        return {
            "rating": photo.rating,
            "pick_status": photo.pick_status,
            "keywords": sorted(photo.keywords),
        }

    def _cmd_get_library_state(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._photo(params)
        return {"id": photo.id, **self._library(photo)}

    def _cmd_set_library_state(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params)
        before = self._library(photo)
        if "rating" in params:
            photo.rating = params["rating"] or None
        if "pick_status" in params:
            photo.pick_status = params["pick_status"]
        for name in params.get("add_keywords", []):
            if name.lower() not in {k.lower() for k in photo.keywords}:
                photo.keywords.append(name)
        for name in params.get("remove_keywords", []):
            photo.keywords = [k for k in photo.keywords if k.lower() != name.lower()]
        return {"before": before, "after": self._library(photo)}

    def _cmd_create_collection(self, params: dict[str, Any]) -> dict[str, Any]:
        path = params["path"]
        created = path not in self.collections
        self.collections.setdefault(path, [])
        return {"path": path, "created": created, "photo_count": len(self.collections[path])}

    def _cmd_create_collection_set(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"path": params["path"], "created": True}

    def _cmd_remove_from_collection(self, params: dict[str, Any]) -> dict[str, Any]:
        members = self.collections.get(params["collection"], [])
        removed = [i for i in params["photo_ids"] if i in members]
        self.collections[params["collection"]] = [i for i in members if i not in removed]
        return {
            "collection": params["collection"],
            "removed_ids": removed,
            "photo_count": len(self.collections[params["collection"]]),
        }

    def _cmd_delete_collection(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "deleted": self.collections.pop(params["path"], None) is not None,
            "path": params["path"],
        }

    def _cmd_collection_photos(self, params: dict[str, Any]) -> dict[str, Any]:
        members = self.collections.get(params["collection"], [])
        return {
            "collection": params["collection"],
            "is_smart": False,
            "total": len(members),
            "photos": [{"id": i, "file_name": "x.jpg"} for i in members],
        }

    def _cmd_create_keyword(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"path": params["path"], "created_levels": params["path"].split("/")}

    def _cmd_catalog_info(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "catalog_path": self.catalog_path,
            "writes_allowed": self.writes_allowed,
            "total_photos": len(self.photos),
            "collections": [
                {"name": name, "kind": "collection", "photo_count": len(ids)}
                for name, ids in self.collections.items()
            ],
            "folders": [],
        }

    def _cmd_add_to_collection(self, params: dict[str, Any]) -> dict[str, Any]:
        members = self.collections.get(params["collection"])
        if members is None:
            raise PluginError("COLLECTION_NOT_FOUND", "No such collection")
        added = [i for i in params["photo_ids"] if i in self.photos and i not in members]
        members.extend(added)
        return {
            "collection": params["collection"],
            "added_ids": added,
            "already_in": 0,
            "missing_ids": [],
            "photo_count": len(members),
        }

    def _cmd_export_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        self._writable(params)
        self.exports.append(params)
        return {
            "path": f"D:\\previews\\{params['name']}.jpg",
            "long_edge": params.get("long_edge", 1024),
        }

    def _cmd_export_tiff(self, params: dict[str, Any]) -> dict[str, Any]:
        self._writable(params, needs_copy=True)
        path = f"D:\\outputs\\handoff\\{params['name']}.tif"
        if path in self.tiffs:
            raise PluginError("FILE_EXISTS", "Refused: a file with this name already exists")
        self.tiffs.append(path)
        return {"path": path, "bit_depth": 16, "color_space": params.get("color_space", "sRGB")}

    PRESETS = (
        {"name": "Soft Warm", "uuid": "preset-1", "folder": "User Presets"},
        {"name": "Hard Push", "uuid": "preset-2", "folder": "User Presets"},
    )
    PRESET_SETTINGS: dict[str, dict[str, Any]] = {  # noqa: RUF012
        "preset-1": {"Exposure": 0.4, "Contrast": 10},
        "preset-2": {"Exposure": 4.5},
    }

    def _cmd_list_presets(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"presets": list(self.PRESETS)}

    def _cmd_get_preset(self, params: dict[str, Any]) -> dict[str, Any]:
        preset = next((p for p in self.PRESETS if p["uuid"] == params.get("preset_uuid")), None)
        if preset is None:
            raise PluginError("PRESET_NOT_FOUND", "No develop preset with that id")
        return {**preset, "settings": dict(self.PRESET_SETTINGS[preset["uuid"]])}

    def _cmd_reset_develop(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params, needs_copy=True)
        if params.get("scope") not in ("all", "masks", "spots", "crop", "transforms", "red_eye"):
            raise PluginError("INVALID_PARAMS", "scope must be all, masks, spots, crop, ...")
        before = dict(photo.settings) if params["scope"] == "all" else {}
        if params["scope"] == "all":
            photo.settings.clear()
        return {
            "photo_id": photo.id,
            "scope": params["scope"],
            "snapshot": {"id": "s1", "name": "darkroom_ai before reset"},
            "before": before,
            "after": {},
        }

    def _cmd_remove_distracting_people(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params, needs_copy=True)
        return {
            "photo_id": photo.id,
            "snapshot": {"id": "s1", "name": "darkroom_ai before remove people"},
            "callback_called": True,
            "retouch_before": 0,
            "retouch_after": 1,
            "waited_s": 1.0,
        }

    def _cmd_get_retouch_state(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._photo(params)
        return {"photo_id": photo.id, "spot_count": 0, "spots": [], "red_eye_count": 0}

    def _cmd_get_ai_panel_state(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._photo(params)
        return {"photo_id": photo.id, "panels": {"enhance": {"ok": True, "value": {}}}}

    def _cmd_rotate_photo(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params, needs_copy=True)
        if params.get("direction") not in ("left", "right"):
            raise PluginError("INVALID_PARAMS", "direction must be left or right")
        return {
            "photo_id": photo.id,
            "direction": params["direction"],
            "before": "AB",
            "after": "BC",
        }

    def _cmd_set_photo_metadata(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params)
        fields = {k: v for k, v in params.items() if k not in ("photo_id",)}
        if not fields:
            raise PluginError("INVALID_PARAMS", "nothing to change")
        return {"photo_id": photo.id, "before": {k: "" for k in fields}, "after": fields}

    def _cmd_list_keywords(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"keywords": [{"path": "Family", "synonyms": []}], "truncated": False}

    def _cmd_auto_adjust(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params, needs_copy=True)
        tone, balance = params.get("tone", True), params.get("white_balance", False)
        if not tone and not balance:
            raise PluginError("INVALID_PARAMS", "ask for tone, white_balance or both")
        seed = sum(ord(c) for c in photo.id) % 7
        wanted: dict[str, Any] = {}
        if tone:
            wanted.update({"Exposure2012": round(seed * 0.4 - 1.0, 2), "Contrast2012": 5 + seed})
        if balance:
            wanted["Temperature"] = 5000 + seed * 100
        before = {k: photo.settings.get(k) for k, v in wanted.items() if photo.settings.get(k) != v}
        photo.settings.update(wanted)
        return {
            "photo_id": photo.id,
            "ran": (["setAutoTone"] if tone else []) + (["setAutoWhiteBalance"] if balance else []),
            "before": before,
            "after": {k: wanted[k] for k in before},
        }

    def _cmd_apply_preset(self, params: dict[str, Any]) -> dict[str, Any]:
        photo = self._writable(params, needs_copy=True)
        preset = next((p for p in self.PRESETS if p["uuid"] == params.get("preset_uuid")), None)
        if preset is None:
            raise PluginError("PRESET_NOT_FOUND", "No develop preset with that id")
        new = self.PRESET_SETTINGS[preset["uuid"]]
        before = {k: photo.settings.get(k) for k in new if photo.settings.get(k) != new[k]}
        photo.settings.update(new)
        return {"preset": preset, "before": before, "after": {k: new[k] for k in before}}
