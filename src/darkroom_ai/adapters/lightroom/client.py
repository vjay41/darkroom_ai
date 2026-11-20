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
    PhotoInfo,
    PingInfo,
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
        connect: Connector = open_tcp,
    ) -> None:
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
        )

    # -- connection ---------------------------------------------------------------------------

    def close(self) -> None:
        for connection in (self._requests, self._responses):
            if connection is not None:
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

    def _open(self, port: int, deadline: float) -> Connection:
        # After a client disconnects the plug-in needs a moment to listen again, so retry briefly.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no listener on port {port}")
            try:
                return self._connect(self._host, port, timeout_s=remaining)
            except ConnectionError:
                time.sleep(min(_CONNECT_RETRY_S, max(0.0, deadline - time.monotonic())))

    def _ensure_connected(self, deadline: float) -> tuple[Connection, Connection]:
        if self._requests is None or self._responses is None:
            self.close()
            self._responses = self._open(self._response_port, deadline)
            self._requests = self._open(self._request_port, deadline)
        return self._requests, self._responses

    # -- protocol -----------------------------------------------------------------------------

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
        deadline = time.monotonic() + timeout
        request_id = uuid.uuid4().hex
        request = {
            "id": request_id,
            "token": self._token,
            "command": command,
            "params": dict(params or {}),
        }
        line = (json.dumps(request, ensure_ascii=True) + "\n").encode("ascii")
        try:
            requests, responses = self._ensure_connected(deadline)
            requests.settimeout(max(0.01, deadline - time.monotonic()))
            requests.sendall(line)
            message = self._read_response(responses, request_id, deadline)
        except DarkroomError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            raise DarkroomError(
                ErrorCode.LR_NOT_REACHABLE,
                f"Lightroom did not answer '{command}' within {timeout:g} s ({exc}).",
                details={"command": command, "timeout_s": timeout},
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

    def probe(self) -> dict[str, Any]:
        return self.call("probe")

    def get_selection(self) -> Selection:
        return self._typed(Selection, "get_selection", self.call("get_selection"))

    def get_photo(self, photo_id: str) -> PhotoInfo:
        return self._typed(PhotoInfo, "get_photo", self.call("get_photo", {"photo_id": photo_id}))

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

    def apply_snapshot(self, photo_id: str, snapshot_id: str) -> Snapshot:
        data = self.call("apply_snapshot", {"photo_id": photo_id, "snapshot_id": snapshot_id})
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
