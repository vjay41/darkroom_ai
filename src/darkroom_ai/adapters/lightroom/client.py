"""Client for the darkroom_ai Lightroom plug-in (newline-delimited JSON over two localhost sockets).

Every call has a deadline. A silent or missing plug-in raises LR_NOT_REACHABLE; a refusal because
the wrong catalog is open raises LR_WRONG_CATALOG; any other plug-in error raises LR_COMMAND_FAILED.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from types import TracebackType
from typing import Any, Self

from pydantic import BaseModel, ValidationError

from darkroom_ai.adapters.lightroom.bridge_file import BridgeFile
from darkroom_ai.adapters.lightroom.models import (
    CatalogPage,
    CommandList,
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
from darkroom_ai.config import Settings
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.net.loopback import Connection, open_tcp

Connector = Callable[..., Connection]

_RECV_BYTES = 65536
_CONNECT_RETRY_S = 0.25
_HANDSHAKE_S = 2.0
NO_LISTENER_NEXT_STEP = (
    "Open Lightroom Classic with the test catalog, then choose File > Plug-in Extras > "
    '"Start darkroom_ai Bridge". If that menu item is missing, add or enable the plug-in in '
    "File > Plug-in Manager (`darkroom lightroom setup` shows the folder). Then retry."
)
# A connect that takes longer than this means the port was not listening yet, i.e. the plug-in is
# in the middle of re-opening its two sockets and they would belong to different sessions.
_SLOW_CONNECT_S = 0.25
# Waits between connection attempts, as multiples of reconnect_delay_s. They grow so that retries
# cannot stay in step with the plug-in's own restart cycle (observed: a fixed 1 s retry never
# recovered, because the two ports come back about 0.5 s apart).
_BACKOFF = (1.0, 1.5, 2.2, 3.0)

# When each plug-in endpoint was last disconnected by ANY client object in this process.
_LAST_CLOSE: dict[tuple[str, int, int], float] = {}


class LightroomClient:
    def __init__(
        self,
        *,
        host: str,
        request_port: int,
        response_port: int,
        token: str,
        timeout_s: float = 10.0,
        export_timeout_s: float = 120.0,
        connect_timeout_s: float = 20.0,
        reconnect_delay_s: float = 1.0,
        connect: Connector = open_tcp,
    ) -> None:
        self._connect_timeout_s = connect_timeout_s
        self._saw_listener = False  # did any connect succeed during the current call?
        self._reconnect_delay_s = reconnect_delay_s
        self._endpoint = (host, request_port, response_port)
        self._host = host
        self._request_port = request_port
        self._response_port = response_port
        self._token = token
        self._timeout_s = timeout_s
        self._export_timeout_s = export_timeout_s
        self._connect = connect
        self._requests: Connection | None = None
        self._responses: Connection | None = None
        self._buffer = b""

    @classmethod
    def from_settings(cls, settings: Settings, bridge: BridgeFile) -> Self:
        return cls(
            host=settings.lightroom.host,
            request_port=bridge.request_port,
            response_port=bridge.response_port,
            token=bridge.token,
            timeout_s=settings.lightroom.timeout_s,
            export_timeout_s=settings.lightroom.export_timeout_s,
            connect_timeout_s=settings.lightroom.connect_timeout_s,
        )

    # -- connection ---------------------------------------------------------------------------

    def close(self) -> None:
        for connection in (self._responses, self._requests):
            if connection is not None:
                _LAST_CLOSE[self._endpoint] = time.monotonic()
                with contextlib.suppress(OSError):
                    connection.close()
        self._requests = None
        self._responses = None
        self._buffer = b""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _open(self, port: int, deadline: float) -> tuple[Connection, float]:
        """Connect to one port; returns the connection and how long the final connect took."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no listener on port {port}")
            started = time.monotonic()
            try:
                connection = self._connect(self._host, port, timeout_s=remaining)
                self._saw_listener = True
                return connection, time.monotonic() - started
            except ConnectionError:
                time.sleep(min(_CONNECT_RETRY_S, max(0.0, deadline - time.monotonic())))

    def _wait_after_last_close(self, factor: float, deadline: float) -> None:
        last = _LAST_CLOSE.get(self._endpoint)
        if last is None:
            return
        wait = last + self._reconnect_delay_s * factor - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, max(0.0, deadline - time.monotonic())))

    def _ensure_connected(self, deadline: float) -> tuple[Connection, Connection]:
        """Connect both sockets and prove the plug-in answers before any real command is sent.

        Observed on Lightroom 15.2.1: after a client disconnects, the plug-in closes and re-opens
        its two sockets, and they come back about half a second apart. A client that connects in
        that window gets one socket of the old session and one of the new and is dropped. So:
        - no connection is attempted within reconnect_delay_s of the last disconnect made by any
          client in this process;
        - an attempt whose connect was slow (the port was not listening yet) is abandoned;
        - a harmless `ping` is exchanged before any real command, so real commands are never
          re-sent;
        - failed attempts are retried with growing waits until the connect deadline.
        """
        attempt = 0
        while self._requests is None or self._responses is None:
            self.close()
            self._wait_after_last_close(_BACKOFF[min(attempt, len(_BACKOFF) - 1)], deadline)
            attempt += 1
            if time.monotonic() >= deadline:
                raise TimeoutError("no time left to connect")
            attempt_deadline = min(deadline, time.monotonic() + _HANDSHAKE_S)
            try:
                self._responses, first = self._open(self._response_port, attempt_deadline)
                self._requests, second = self._open(self._request_port, attempt_deadline)
                if max(first, second) > _SLOW_CONNECT_S:
                    raise ConnectionError("the plug-in is still re-opening its sockets")
                self._exchange(self._requests, self._responses, "ping", {}, attempt_deadline)
            except DarkroomError:
                raise
            except OSError:
                self.close()
                if time.monotonic() >= deadline:
                    raise
        return self._requests, self._responses

    # -- protocol -----------------------------------------------------------------------------

    def _exchange(
        self,
        requests: Connection,
        responses: Connection,
        command: str,
        params: Mapping[str, Any],
        deadline: float,
    ) -> Any:
        request_id = uuid.uuid4().hex
        request = {
            "id": request_id,
            "token": self._token,
            "command": command,
            "params": dict(params),
        }
        requests.settimeout(max(0.01, deadline - time.monotonic()))
        requests.sendall((json.dumps(request, ensure_ascii=True) + "\n").encode("ascii"))
        return self._read_response(responses, request_id, deadline)

    def _read_response(self, responses: Connection, request_id: str, deadline: float) -> Any:
        while True:
            while b"\n" in self._buffer:
                line, _, self._buffer = self._buffer.partition(b"\n")
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except ValueError as exc:
                    raise DarkroomError(
                        ErrorCode.LR_COMMAND_FAILED,
                        "The Lightroom plug-in sent a response that is not valid JSON.",
                    ) from exc
                if isinstance(message, dict) and message.get("id") in (request_id, None):
                    return message
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("no response before the deadline")
            responses.settimeout(remaining)
            chunk = responses.recv(_RECV_BYTES)
            if not chunk:
                raise ConnectionError("Lightroom closed the connection")
            self._buffer += chunk

    def call(
        self,
        command: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Send one command and return its result. Raises DarkroomError on any failure."""
        timeout = self._timeout_s if timeout_s is None else timeout_s
        self._saw_listener = self._requests is not None and self._responses is not None
        try:
            # Connecting has its own deadline; the command's deadline starts once connected.
            requests, responses = self._ensure_connected(time.monotonic() + self._connect_timeout_s)
            deadline = time.monotonic() + timeout
            message = self._exchange(requests, responses, command, params or {}, deadline)
        except DarkroomError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            if not self._saw_listener:
                raise DarkroomError(
                    ErrorCode.LR_NOT_REACHABLE,
                    f"Nothing is listening on the Lightroom plug-in's local ports "
                    f"({self._request_port} and {self._response_port}): Lightroom Classic is "
                    f"closed, or the darkroom_ai bridge is not started (waited "
                    f"{self._connect_timeout_s:g} s).",
                    next_step=NO_LISTENER_NEXT_STEP,
                    details={"command": command, "connect_timeout_s": self._connect_timeout_s},
                ) from exc
            raise DarkroomError(
                ErrorCode.LR_NOT_REACHABLE,
                f"Lightroom did not answer '{command}' in time ({exc}).",
                details={
                    "command": command,
                    "timeout_s": timeout,
                    "connect_timeout_s": self._connect_timeout_s,
                },
            ) from exc

        if message.get("ok") is True:
            result = message.get("result")
            return result if isinstance(result, dict) else {}
        raise self._error(command, message.get("error"))

    @staticmethod
    def _error(command: str, error: Any) -> DarkroomError:
        code = str(error.get("code", "UNKNOWN")) if isinstance(error, dict) else "UNKNOWN"
        text = str(error.get("message", "")) if isinstance(error, dict) else ""
        details = {"command": command, "plugin_code": code}
        if code == "WRONG_CATALOG":
            return DarkroomError(ErrorCode.LR_WRONG_CATALOG, text or None, details=details)
        if code in ("PHOTO_OUTSIDE_ROOT", "NOT_A_VIRTUAL_COPY", "FILE_EXISTS"):  # refusals
            return DarkroomError(
                ErrorCode.GUARDRAIL_REJECTED,
                text or None,
                next_step="Edits are only made to virtual copies of photos in the allowed folder. "
                "Create a virtual copy first, or use a photo from that folder. Nothing changed.",
                details=details,
            )
        if code == "MASK_PHOTO_NOT_SHOWN":
            return DarkroomError(ErrorCode.LR_BUSY_USER, text or None, details=details)
        if code == "UNAUTHORIZED":
            return DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                "The Lightroom plug-in rejected the token.",
                next_step="Run `darkroom lightroom setup`, then reload the plug-in in the "
                "Plug-in Manager.",
                details=details,
            )
        return DarkroomError(
            ErrorCode.LR_COMMAND_FAILED,
            f"Lightroom could not run '{command}': {text or code} [{code}]",
            details=details,
        )

    def _typed[T: BaseModel](self, model: type[T], command: str, data: Any) -> T:
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                f"The Lightroom plug-in returned an unexpected result for '{command}'.",
                details={"command": command, "problems": [e["msg"] for e in exc.errors()]},
            ) from exc

    # -- commands -----------------------------------------------------------------------------

    def ping(self) -> PingInfo:
        return self._typed(PingInfo, "ping", self.call("ping"))

    def selftest(self, value: Any) -> Any:
        """Round-trip a value through the plug-in's JSON decoder and encoder."""
        return self.call("selftest", {"value": value}).get("echo")

    def list_commands(self) -> CommandList:
        """Commands registered in the plug-in build that is running inside Lightroom."""
        return self._typed(CommandList, "list_commands", self.call("list_commands"))

    def probe(self) -> dict[str, Any]:
        return self.call("probe")

    def get_selection(self) -> Selection:
        return self._typed(Selection, "get_selection", self.call("get_selection"))

    def find_photo(self, path: str) -> PhotoInfo | None:
        """Look a photo up by its file path; None when it is not in the open catalog."""
        data = self.call("find_photo", {"path": path})
        if not data.get("found"):
            return None
        return self._typed(PhotoInfo, "find_photo", data.get("photo"))

    def get_photo(self, photo_id: str) -> PhotoInfo:
        return self._typed(PhotoInfo, "get_photo", self.call("get_photo", {"photo_id": photo_id}))

    def get_metadata(self, photo_id: str) -> PhotoMetadata:
        data = self.call("get_metadata", {"photo_id": photo_id})
        return self._typed(PhotoMetadata, "get_metadata", data)

    def list_catalog(
        self,
        offset: int = 0,
        limit: int = 50,
        *,
        include_virtual_copies: bool = False,
        path_contains: str | None = None,
    ) -> CatalogPage:
        params: dict[str, Any] = {
            "offset": offset,
            "limit": limit,
            "include_virtual_copies": include_virtual_copies,
        }
        if path_contains:
            params["path_contains"] = path_contains
        return self._typed(CatalogPage, "list_photos", self.call("list_photos", params))

    def get_develop_settings(
        self, photo_id: str, keys: Sequence[str] | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"photo_id": photo_id, "include_settings": True}
        if keys:
            params["keys"] = list(keys)
        settings = self.call("get_photo", params).get("settings")
        return settings if isinstance(settings, dict) else {}

    def list_snapshots(self, photo_id: str) -> tuple[Snapshot, ...]:
        data = self.call("list_snapshots", {"photo_id": photo_id}).get("snapshots") or []
        return tuple(self._typed(Snapshot, "list_snapshots", item) for item in data)

    def create_virtual_copy(self, photo_id: str, copy_name: str) -> PhotoInfo:
        data = self.call("create_virtual_copy", {"photo_id": photo_id, "copy_name": copy_name})
        return self._typed(PhotoInfo, "create_virtual_copy", data)

    def create_snapshot(self, photo_id: str, name: str) -> SnapshotResult:
        data = self.call("create_snapshot", {"photo_id": photo_id, "name": name})
        return self._typed(SnapshotResult, "create_snapshot", data)

    def apply_snapshot(
        self,
        photo_id: str,
        snapshot_id: str,
        *,
        id_kind: str | None = None,
        gate: bool | None = None,
    ) -> Snapshot:
        """Apply a snapshot. `id_kind` / `gate` select plug-in variants (spike use only)."""
        params: dict[str, Any] = {"photo_id": photo_id, "snapshot_id": snapshot_id}
        if id_kind is not None:
            params["id_kind"] = id_kind
        if gate is not None:
            params["gate"] = gate
        data = self.call("apply_snapshot", params)
        return self._typed(Snapshot, "apply_snapshot", data.get("applied"))

    def apply_develop_settings(
        self, photo_id: str, settings: Mapping[str, Any], history_name: str = "darkroom_ai edit"
    ) -> SettingsChange:
        data = self.call(
            "apply_settings",
            {"photo_id": photo_id, "settings": dict(settings), "history_name": history_name},
        )
        return self._typed(SettingsChange, "apply_settings", data)

    def export_preview(self, photo_id: str, name: str, long_edge: int = 1024) -> PreviewFile:
        data = self.call(
            "export_preview",
            {"photo_id": photo_id, "name": name, "long_edge": long_edge},
            timeout_s=self._export_timeout_s,
        )
        return self._typed(PreviewFile, "export_preview", data)

    def scan_folder(self, folder: str, recursive: bool = False) -> FolderScan:
        data = self.call("scan_folder", {"folder": folder, "recursive": recursive})
        return self._typed(FolderScan, "scan_folder", data)

    def import_folder(
        self, folder: str, recursive: bool = False, max_files: int = 200
    ) -> FolderImport:
        """Add photos from a folder to the catalog without moving them (a bulk change)."""
        params = {"folder": folder, "recursive": recursive, "max_files": max_files}
        data = self.call("import_folder", params, timeout_s=self._export_timeout_s)
        return self._typed(FolderImport, "import_folder", data)

    def create_mask(
        self, photo_id: str, mask_type: str, subtype: str | None = None
    ) -> MaskCreation:
        """Create a mask on a virtual copy (Lightroom switches to the Develop module for this)."""
        params: dict[str, Any] = {"photo_id": photo_id, "mask_type": mask_type}
        if subtype is not None:
            params["subtype"] = subtype
        data = self.call("create_mask", params, timeout_s=self._export_timeout_s)
        return self._typed(MaskCreation, "create_mask", data)

    def inspect_masks(self, photo_id: str) -> MaskInspection:
        data = self.call("inspect_masks", {"photo_id": photo_id}, timeout_s=self._export_timeout_s)
        return self._typed(MaskInspection, "inspect_masks", data)

    def set_mask_values(
        self, photo_id: str, mask_id: str | None, values: Mapping[str, Mapping[str, float]]
    ) -> MaskValues:
        """Change local adjustments inside one mask; each spec has change, min, max, max_step."""
        params: dict[str, Any] = {
            "photo_id": photo_id,
            "values": {k: dict(v) for k, v in values.items()},
        }
        if mask_id:
            params["mask_id"] = mask_id
        data = self.call("set_mask_values", params, timeout_s=self._export_timeout_s)
        return self._typed(MaskValues, "set_mask_values", data)

    def mask_operation(self, photo_id: str, mask_id: str, operation: str) -> MaskOperation:
        params = {"photo_id": photo_id, "mask_id": mask_id, "operation": operation}
        data = self.call("mask_operation", params, timeout_s=self._export_timeout_s)
        return self._typed(MaskOperation, "mask_operation", data)

    def export_tiff(self, photo_id: str, name: str, color_space: str = "sRGB") -> ExportedTiff:
        """Export a full-size 16-bit TIFF of a virtual copy into the hand-off folder."""
        data = self.call(
            "export_tiff",
            {"photo_id": photo_id, "name": name, "color_space": color_space},
            timeout_s=self._export_timeout_s,
        )
        return self._typed(ExportedTiff, "export_tiff", data)

    def list_presets(self) -> tuple[PresetInfo, ...]:
        data = self.call("list_presets").get("presets") or []
        return tuple(self._typed(PresetInfo, "list_presets", item) for item in data)

    def get_preset_settings(self, preset_uuid: str) -> dict[str, Any]:
        """The settings a preset would apply, without applying it (plug-in 0.4.0 or later)."""
        data = self.call("get_preset", {"preset_uuid": preset_uuid})
        settings = data.get("settings")
        return dict(settings) if isinstance(settings, dict) else {}

    def apply_preset(self, photo_id: str, preset_uuid: str) -> PresetChange:
        data = self.call("apply_preset", {"photo_id": photo_id, "preset_uuid": preset_uuid})
        return self._typed(PresetChange, "apply_preset", data)
