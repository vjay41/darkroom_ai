"""bridge.json: the small settings file shared between darkroom_ai and the Lightroom plug-in.

It lives in the plug-in folder (git-ignored) and holds the per-request token, the two localhost
ports, the one catalog the plug-in may write to, and the folder previews are exported into.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from darkroom_ai.adapters.lightroom import plugin_dir
from darkroom_ai.config import Settings
from darkroom_ai.errors import DarkroomError, ErrorCode

BRIDGE_FILE_NAME = "bridge.json"
TOKEN_BYTES = 32


class BridgeFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(min_length=32)
    catalog_path: str = Field(min_length=1)
    preview_dir: str = Field(min_length=1)
    request_port: int = Field(ge=1024, le=65535)
    response_port: int = Field(ge=1024, le=65535)
    # Optional: the plug-in refuses writes for photos whose files are outside this folder.
    photos_root: str | None = None
    # Folder the plug-in writes hand-off TIFFs into (under the outputs folder).
    handoff_dir: str | None = None
    # Catalogs and photo folders the owner has added on top of catalog_path / photos_root.
    allowed_catalogs: tuple[str, ...] = ()
    photos_roots: tuple[str, ...] = ()


def bridge_path(folder: Path | None = None) -> Path:
    return (folder or plugin_dir()) / BRIDGE_FILE_NAME


def load_bridge_file(folder: Path | None = None) -> BridgeFile:
    path = bridge_path(folder)
    try:
        return BridgeFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise DarkroomError(
            ErrorCode.CONFIG_INVALID,
            f"Lightroom bridge file not found: {path}",
            next_step="Run `darkroom lightroom setup`, then reload the plug-in in Lightroom.",
            details={"path": str(path)},
        ) from exc
    except (ValueError, ValidationError) as exc:
        raise DarkroomError(
            ErrorCode.CONFIG_INVALID,
            f"Lightroom bridge file is not valid: {path}",
            next_step="Run `darkroom lightroom setup --rotate-token` to rewrite it, then reload "
            "the plug-in in Lightroom.",
            details={"path": str(path)},
        ) from exc


def _same_path(first: str, second: str) -> bool:
    def norm(value: str) -> str:
        return value.replace("/", "\\").rstrip("\\").lower()

    return norm(first) == norm(second)


def _merged(existing: tuple[str, ...], extra: Sequence[str]) -> tuple[str, ...]:
    result = list(existing)
    for item in extra:
        if not any(_same_path(item, known) for known in result):
            result.append(item)
    return tuple(result)


def _save(bridge: BridgeFile, folder: Path | None) -> None:
    path = bridge_path(folder)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(bridge.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class BridgeAccessStore:
    """The allowlist, kept in bridge.json (read by the Lightroom plug-in on every request)."""

    def __init__(self, folder: Path | None = None) -> None:
        self._folder = folder

    def allowed_catalogs(self) -> tuple[str, ...]:
        bridge = load_bridge_file(self._folder)
        return _merged((bridge.catalog_path,), bridge.allowed_catalogs)

    def photos_roots(self) -> tuple[str, ...]:
        bridge = load_bridge_file(self._folder)
        first = (bridge.photos_root,) if bridge.photos_root else ()
        return _merged(first, bridge.photos_roots)

    def allow_catalog(self, catalog: str) -> None:
        bridge = load_bridge_file(self._folder)
        updated = bridge.model_copy(
            update={"allowed_catalogs": _merged(bridge.allowed_catalogs, [catalog])}
        )
        _save(updated, self._folder)

    def allow_photos_folder(self, folder: str) -> None:
        bridge = load_bridge_file(self._folder)
        updated = bridge.model_copy(update={"photos_roots": _merged(bridge.photos_roots, [folder])})
        _save(updated, self._folder)


def write_bridge_file(
    settings: Settings, *, folder: Path | None = None, rotate_token: bool = False
) -> BridgeFile:
    """Create or update bridge.json from the settings. The token is kept unless rotate_token."""
    catalog = settings.lightroom.test_catalog
    if catalog is None:
        raise DarkroomError(
            ErrorCode.CONFIG_INVALID,
            "lightroom.test_catalog is not set.",
            next_step="Set [lightroom] test_catalog in darkroom.local.toml to the .lrcat file "
            "darkroom_ai may edit, then run `darkroom lightroom setup` again.",
        )
    path = bridge_path(folder)
    token = None
    kept_catalogs: tuple[str, ...] = ()
    kept_roots: tuple[str, ...] = ()
    if path.is_file():
        try:
            previous = load_bridge_file(folder)
            kept_catalogs, kept_roots = previous.allowed_catalogs, previous.photos_roots
            if not rotate_token:
                token = previous.token
        except DarkroomError:
            token = None
    bridge = BridgeFile(
        token=token or secrets.token_hex(TOKEN_BYTES),
        catalog_path=str(catalog),
        preview_dir=str(settings.paths.previews_dir.resolve()),
        request_port=settings.lightroom.request_port,
        response_port=settings.lightroom.response_port,
        handoff_dir=str(settings.paths.outputs_dir.resolve() / "handoff"),
        allowed_catalogs=_merged(
            kept_catalogs, [str(c) for c in settings.lightroom.allowed_catalogs]
        ),
        photos_roots=_merged(kept_roots, [str(r) for r in settings.lightroom.photos_roots]),
        photos_root=(
            str(settings.lightroom.test_photos_dir) if settings.lightroom.test_photos_dir else None
        ),
    )
    _save(bridge, folder)
    return bridge
