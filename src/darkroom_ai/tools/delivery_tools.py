"""export_for_delivery: a finished JPEG of a photo, as a NEW file in the exports folder."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.io import load_image
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, resolve_copy

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")
MAX_EDGE = 4096


class ExportForDeliveryInput(BaseModel):
    """Export a photo, with its current edits and crop, as an sRGB JPEG into the exports folder
    (darkroom.toml [paths] exports_dir) under a name you choose. It makes a new file and never
    overwrites; the catalog and the original are unchanged. Longest side up to 4096 px."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    name: str = Field(description="File name without folder, e.g. 'rahul-priya-001'.")
    long_edge: int = Field(default=2048, ge=256, le=MAX_EDGE)
    quality: float = Field(default=0.9, ge=0.5, le=1.0)

    @field_validator("name")
    @classmethod
    def _plain_name(cls, name: str) -> str:
        stem = name.removesuffix(".jpg").removesuffix(".jpeg")
        if not SAFE_NAME.match(stem) or ".." in stem:
            raise ValueError(
                "use letters, digits, spaces, dots, dashes and underscores only (no folders)"
            )
        return stem


class ExportResult(ToolResult):
    photo_id: str
    path: str
    width: int
    height: int
    megabytes: float
    note: str = (
        "sRGB JPEG rendered by Lightroom from the photo as it is now. No output sharpening or "
        "watermark is added. Later edits do not change this file."
    )


def _free_path(folder: Path, stem: str, suffix: str) -> Path:
    candidate, counter = folder / f"{stem}{suffix}", 2
    while candidate.exists():
        candidate = folder / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def export_for_delivery(ctx: ToolContext, args: ExportForDeliveryInput) -> Outcome:
    target = resolve_copy(ctx, args.photo_id)
    folder = Path(ctx.settings.paths.exports_dir)
    folder.mkdir(parents=True, exist_ok=True)
    rendered = ctx.lightroom.export_preview(
        target.id, f"deliver-{uuid.uuid4().hex[:12]}", args.long_edge, args.quality
    )
    if not rendered.path.is_file():
        raise DarkroomError(ErrorCode.LR_COMMAND_FAILED, "Lightroom did not produce the file.")
    destination = _free_path(folder, args.name, rendered.path.suffix.lower() or ".jpg")
    shutil.copy2(rendered.path, destination)
    pixels = load_image(destination)
    result = ExportResult(
        photo_id=target.id,
        path=str(destination),
        width=int(pixels.shape[1]),
        height=int(pixels.shape[0]),
        megabytes=round(destination.stat().st_size / 1_000_000, 2),
    )
    return Outcome(result, applied={"photo_id": target.id, "output": str(destination)})
