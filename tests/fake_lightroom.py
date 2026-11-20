"""A fake Lightroom plug-in for tests: same wire protocol as Bridge.lua, in-memory catalog.

Listens on two 127.0.0.1 ports (requests in, responses out) and mirrors the plug-in's rules:
token check, write refusal on the wrong catalog, photos addressed by UUID.
"""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass, field
from typing import Any

WRITE_COMMANDS = {
    "create_virtual_copy",
    "create_snapshot",
    "apply_snapshot",
    "apply_settings",
    "export_preview",
    "export_tiff",
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
        self.commands: list[str] = []
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
        try:
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
            "plugin_version": "0.1.0",
            "protocol": 1,
            "lightroom_version": "15.2.1",
            "catalog_path": self.catalog_path,
            "configured_catalog": self.configured_catalog,
            "writes_allowed": self.writes_allowed,
        }

    def _cmd_selftest(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params.get("value")}

    def _cmd_get_selection(self, _: dict[str, Any]) -> dict[str, Any]:
        photos = [self.photos[i].summary() for i in self.selected]
        return {"active": photos[0], "photos": photos} if photos else {"photos": []}

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
