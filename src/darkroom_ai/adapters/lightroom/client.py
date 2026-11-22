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
    CommandList,
    DistractionsResult,
    ExportedTiff,
    FolderImport,
    FolderScan,
    FoundPhotos,
    KeywordCreated,
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
from darkroom_ai.compat import EXPECTED_PROTOCOL
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


# Filters that need plug-in 0.12.0 (the rating, flag and keyword filters need 0.11.0).
NEWER_FILTERS = ("file_format", "min_iso", "max_iso", "captured_from", "captured_to")


LONG_READ_TIMEOUT_S = 60.0
LONG_READS = frozenset({"list_photos", "scan_folder", "catalog_info", "collection_photos"})


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
                hello = self._exchange(
                    self._requests, self._responses, "ping", {}, attempt_deadline
                )
                self._check_protocol(hello)
            except DarkroomError:
                raise
            except OSError:
                self.close()
                if time.monotonic() >= deadline:
                    raise
        return self._requests, self._responses

    @staticmethod
    def _check_protocol(hello: Any) -> None:
        """Refuse to talk to a plug-in whose message protocol differs from this server's."""
        protocol = hello.get("protocol") if isinstance(hello, dict) else None
        if protocol is not None and protocol != EXPECTED_PROTOCOL:
            raise DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                f"The Lightroom plug-in speaks protocol {protocol}; this darkroom_ai expects "
                f"{EXPECTED_PROTOCOL}. Nothing was sent to Lightroom.",
                next_step="git pull, run `darkroom lightroom setup`, then choose Reload "
                "Plug-in for darkroom in Lightroom's Plug-in Manager.",
                details={"plugin_protocol": protocol, "expected_protocol": EXPECTED_PROTOCOL},
            )

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
            # When this client stops waiting (Unix seconds): the plug-in skips requests that
            # reach it later, so a request that timed out is never run after the fact.
            "deadline": time.time() + max(0.0, deadline - time.monotonic()) + 1.0,
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
        timeout = timeout_s
        if timeout is None:
            # Reads that walk the whole catalog grow with its size; give them more time than a
            # single-photo call (the plug-in also stops itself at the client's deadline).
            timeout = (
                max(self._timeout_s, LONG_READ_TIMEOUT_S)
                if command in LONG_READS
                else self._timeout_s
            )
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
        details: dict[str, Any] = {"command": command, "plugin_code": code}
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
        if code in ("BRIDGE_RESTARTING", "BRIDGE_BUSY", "REQUEST_EXPIRED"):
            retry = error.get("retry_after_s") if isinstance(error, dict) else None
            wait = f"about {retry:g} s" if isinstance(retry, int | float) else "a few seconds"
            if isinstance(retry, int | float):
                details = {**details, "retry_after_s": retry}
            return DarkroomError(
                ErrorCode.LR_NOT_REACHABLE,
                f"The Lightroom plug-in did not run '{command}': {text or code} [{code}]",
                next_step=f"Nothing was executed. Wait {wait} (the plug-in may be restarting or "
                "busy with an earlier request), then call the tool again.",
                details=details,
            )
        if code == "COMMAND_TIMEOUT":
            return DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                f"Lightroom took too long for '{command}': {text or code} [{code}]",
                next_step="The step may still have partly happened. Check the photo's current "
                "state (for example list_masks or get_develop_parameters) before repeating it; "
                "if Lightroom shows a dialog, clear it first.",
                details=details,
            )
        if code == "MASK_PHOTO_NOT_SHOWN":
            return DarkroomError(
                ErrorCode.LR_BUSY_USER,
                text or None,
                next_step="Do not click in Lightroom while a mask tool runs. If the working copy "
                "is hidden in a collapsed stack (a virtual copy is stacked with its original), "
                "Lightroom cannot show it: in the Library module, with a folder or All "
                "Photographs as the source, choose Photo > Stacking > Expand All Stacks, then "
                "retry.",
                details=details,
            )
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

    def bridge_status(self) -> BridgeStatus:
        """The plug-in's health (queue, running command, counters); never waits in the queue."""
        return self._typed(BridgeStatus, "bridge_status", self.call("bridge_status"))

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
        params: dict[str, Any] = {
            "offset": offset,
            "limit": limit,
            "include_virtual_copies": include_virtual_copies,
        }
        if path_contains:
            params["path_contains"] = path_contains
        filters = {
            "min_rating": min_rating,
            "max_rating": max_rating,
            "flag": flag,
            "keyword": keyword,
            "file_format": file_format,
            "min_iso": min_iso,
            "max_iso": max_iso,
            "captured_from": captured_from,
            "captured_to": captured_to,
        }
        requested = {name: value for name, value in filters.items() if value is not None}
        params.update(requested)
        page = self._typed(CatalogPage, "list_photos", self.call("list_photos", params))
        ignored = sorted(set(requested) - set(page.filters_applied))
        if ignored:
            # An older plug-in skips filters it does not know and lists everything: never show that
            # as if it were filtered.
            newest = set(ignored) & set(NEWER_FILTERS)
            needs = "0.12.0" if newest else "0.11.0"
            raise DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                f"The Lightroom plug-in did not apply the filter(s) {', '.join(ignored)}: it is "
                f"older than {needs}. Nothing was listed.",
                next_step="Restart Lightroom so it loads the current plug-in, start the bridge "
                "(File > Plug-in Extras), then retry.",
                details={"ignored_filters": ignored},
            )
        return page

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

    def get_library_state(self, photo_id: str) -> LibraryState:
        data = self.call("get_library_state", {"photo_id": photo_id})
        return self._typed(LibraryState, "get_library_state", data)

    def set_library_state(
        self,
        photo_id: str,
        *,
        rating: int | None = None,
        pick_status: int | None = None,
        add_keywords: Sequence[str] = (),
        remove_keywords: Sequence[str] = (),
    ) -> LibraryChange:
        params: dict[str, Any] = {"photo_id": photo_id}
        if rating is not None:
            params["rating"] = rating
        if pick_status is not None:
            params["pick_status"] = pick_status
        if add_keywords:
            params["add_keywords"] = list(add_keywords)
        if remove_keywords:
            params["remove_keywords"] = list(remove_keywords)
        data = self.call("set_library_state", params)
        return self._typed(LibraryChange, "set_library_state", data)

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

    def export_preview(
        self, photo_id: str, name: str, long_edge: int = 1024, quality: float = 0.85
    ) -> PreviewFile:
        data = self.call(
            "export_preview",
            {"photo_id": photo_id, "name": name, "long_edge": long_edge, "quality": quality},
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

    def catalog_info(self) -> CatalogInfo:
        return self._typed(CatalogInfo, "catalog_info", self._new_command("catalog_info", {}))

    def create_collection_set(self, path: str) -> CollectionCreated:
        data = self._new_command("create_collection_set", {"path": path})
        return self._typed(CollectionCreated, "create_collection_set", data)

    def create_collection(self, path: str) -> CollectionCreated:
        data = self._new_command("create_collection", {"path": path})
        return self._typed(CollectionCreated, "create_collection", data)

    def add_to_collection(self, collection: str, photo_ids: Sequence[str]) -> CollectionMembers:
        params = {"collection": collection, "photo_ids": list(photo_ids)}
        data = self._new_command("add_to_collection", params)
        return self._typed(CollectionMembers, "add_to_collection", data)

    def remove_from_collection(
        self, collection: str, photo_ids: Sequence[str]
    ) -> CollectionMembers:
        params = {"collection": collection, "photo_ids": list(photo_ids)}
        data = self._new_command("remove_from_collection", params)
        return self._typed(CollectionMembers, "remove_from_collection", data)

    def delete_collection(self, path: str, kind: str) -> CollectionDeleted:
        data = self._new_command("delete_collection", {"path": path, "kind": kind})
        return self._typed(CollectionDeleted, "delete_collection", data)

    def collection_photos(self, collection: str, limit: int = 100) -> CollectionContents:
        params = {"collection": collection, "limit": limit}
        data = self._new_command("collection_photos", params)
        return self._typed(CollectionContents, "collection_photos", data)

    def create_keyword(self, path: str) -> KeywordCreated:
        data = self._new_command("create_keyword", {"path": path})
        return self._typed(KeywordCreated, "create_keyword", data)

    def _new_command(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        """Call a command added in plug-in 0.13.0; an older plug-in gets a clear error."""
        try:
            return self.call(command, params, timeout_s=self._export_timeout_s)
        except DarkroomError as exc:
            if exc.details.get("plugin_code") == "UNKNOWN_COMMAND":
                raise DarkroomError(
                    ErrorCode.LR_COMMAND_FAILED,
                    f"The Lightroom plug-in is older than 0.13.0 and has no '{command}'. "
                    "Nothing was changed.",
                    next_step="Quit Lightroom completely and start it again so it loads the "
                    'current plug-in, then File > Plug-in Extras > "Start darkroom_ai Bridge".',
                ) from exc
            raise

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

    def reset_develop(self, photo_id: str, scope: str) -> ResetResult:
        params = {"photo_id": photo_id, "scope": scope}
        data = self.call("reset_develop", params, timeout_s=self._export_timeout_s)
        return self._typed(ResetResult, "reset_develop", data)

    def _ai_call(self, command: str, params: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        """Run one of Lightroom's AI features. When Lightroom does not answer, an Adobe error
        dialog is the likely cause (these features can need Adobe's models, which this offline
        setup cannot download), so say that instead of a generic 'not reachable'."""
        try:
            return self.call(command, params, timeout_s=timeout_s)
        except DarkroomError as exc:
            if exc.code is not ErrorCode.LR_NOT_REACHABLE:
                raise
            raise DarkroomError(
                ErrorCode.ADOBE_FEATURE_UNAVAILABLE,
                f"Lightroom did not finish '{command}'. It is probably showing an error dialog "
                "(for example 'We've encountered an issue and can't complete your request'): "
                "this AI feature may need a model that Lightroom could not download offline.",
                next_step="Look at Lightroom, dismiss the dialog, and press Cancel in the open "
                "Develop panel. Nothing was applied; a snapshot of the earlier state was saved "
                "first. Whether to let Lightroom download the model is the owner's decision.",
                details={"command": command, "cause": exc.code.value},
            ) from exc

    def remove_distracting_people(self, photo_id: str) -> DistractionsResult:
        data = self._ai_call(
            "remove_distracting_people",
            {"photo_id": photo_id},
            max(self._export_timeout_s, 120.0),
        )
        return self._typed(DistractionsResult, "remove_distracting_people", data)

    def get_retouch_state(self, photo_id: str) -> RetouchState:
        data = self.call("get_retouch_state", {"photo_id": photo_id})
        return self._typed(RetouchState, "get_retouch_state", data)

    def get_ai_panel_state(self, photo_id: str) -> AiPanelState:
        data = self.call(
            "get_ai_panel_state", {"photo_id": photo_id}, timeout_s=self._export_timeout_s
        )
        return self._typed(AiPanelState, "get_ai_panel_state", data)

    def rotate_photo(self, photo_id: str, direction: str) -> RotateResult:
        data = self.call("rotate_photo", {"photo_id": photo_id, "direction": direction})
        return self._typed(RotateResult, "rotate_photo", data)

    def set_photo_metadata(self, photo_id: str, fields: Mapping[str, str]) -> MetadataChange:
        data = self.call("set_photo_metadata", {"photo_id": photo_id, **dict(fields)})
        return self._typed(MetadataChange, "set_photo_metadata", data)

    def list_keywords(self) -> KeywordList:
        return self._typed(KeywordList, "list_keywords", self.call("list_keywords", {}))

    def find_photos(self, conditions: Sequence[Mapping[str, Any]], combine: str) -> FoundPhotos:
        params = {"conditions": [dict(c) for c in conditions], "combine": combine}
        data = self._new_command("find_photos", params)
        return self._typed(FoundPhotos, "find_photos", data)

    def create_smart_collection(
        self, path: str, conditions: Sequence[Mapping[str, Any]], combine: str
    ) -> CollectionCreated:
        params = {"path": path, "conditions": [dict(c) for c in conditions], "combine": combine}
        data = self._new_command("create_smart_collection", params)
        return self._typed(CollectionCreated, "create_smart_collection", data)

    def build_smart_previews(self, photo_ids: Sequence[str]) -> PreviewsBuilt:
        data = self.call(
            "build_smart_previews",
            {"photo_ids": list(photo_ids)},
            timeout_s=max(self._export_timeout_s, 300.0),
        )
        return self._typed(PreviewsBuilt, "build_smart_previews", data)

    def list_metadata_presets(self) -> PresetNames:
        return self._typed(
            PresetNames, "list_metadata_presets", self.call("list_metadata_presets", {})
        )

    def list_filename_presets(self) -> PresetNames:
        return self._typed(
            PresetNames, "list_filename_presets", self.call("list_filename_presets", {})
        )

    def apply_metadata_preset(self, photo_ids: Sequence[str], preset: str) -> MetadataPresetApplied:
        params = {"photo_ids": list(photo_ids), "preset": preset}
        data = self.call("apply_metadata_preset", params, timeout_s=self._export_timeout_s)
        return self._typed(MetadataPresetApplied, "apply_metadata_preset", data)

    def enhance(self, photo_id: str, feature: str, amount: int | None = None) -> AiRunResult:
        params: dict[str, Any] = {"photo_id": photo_id, "feature": feature, "max_wait_s": 600}
        if amount is not None:
            params["amount"] = amount
        data = self._ai_call("enhance", params, 660.0)
        return self._typed(AiRunResult, "enhance", data)

    def reflection_removal(
        self, photo_id: str, amount: int | None = None, quality: str = "preview"
    ) -> AiRunResult:
        params: dict[str, Any] = {"photo_id": photo_id, "quality": quality, "max_wait_s": 300}
        if amount is not None:
            params["amount"] = amount
        data = self._ai_call("reflection_removal", params, 360.0)
        return self._typed(AiRunResult, "reflection_removal", data)

    def auto_adjust(
        self, photo_id: str, tone: bool = True, white_balance: bool = False
    ) -> AutoAdjustment:
        """Run Lightroom's own Auto Tone / Auto White Balance on one virtual copy."""
        params = {"photo_id": photo_id, "tone": tone, "white_balance": white_balance}
        data = self.call("auto_adjust", params, timeout_s=self._export_timeout_s)
        return self._typed(AutoAdjustment, "auto_adjust", data)

    def apply_preset(self, photo_id: str, preset_uuid: str) -> PresetChange:
        data = self.call("apply_preset", {"photo_id": photo_id, "preset_uuid": preset_uuid})
        return self._typed(PresetChange, "apply_preset", data)
