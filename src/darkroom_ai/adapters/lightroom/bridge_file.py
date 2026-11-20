"""bridge.json: the small settings file shared between darkroom_ai and the Lightroom plug-in.

It lives in the plug-in folder (git-ignored) and holds the per-request token, the two localhost
ports, the one catalog the plug-in may write to, and the folder previews are exported into.
"""

from __future__ import annotations

import json
import os
import secrets
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
    if path.is_file() and not rotate_token:
        try:
            token = load_bridge_file(folder).token
        except DarkroomError:
            token = None
    bridge = BridgeFile(
        token=token or secrets.token_hex(TOKEN_BYTES),
        catalog_path=str(catalog),
        preview_dir=str(settings.paths.previews_dir.resolve()),
        request_port=settings.lightroom.request_port,
        response_port=settings.lightroom.response_port,
        photos_root=(
            str(settings.lightroom.test_photos_dir) if settings.lightroom.test_photos_dir else None
        ),
    )
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(bridge.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return bridge
