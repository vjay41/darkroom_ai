"""replace_background: Lightroom 16-bit TIFF -> subject mask -> Photoshop -> new PSD + JPG.

The original photo and the Lightroom catalog are not changed (apart from the working virtual copy
that is created if none exists). The results are NEW files in the outputs folder; they are not
imported into the catalog.

Background source: a solid colour, or a picture from the ONE configured backgrounds folder.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from pathlib import Path
from typing import Self

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.masks import MaskMetrics
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult, working_copy

BACKGROUND_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff"})
FORBIDDEN_NAME_PARTS = ("/", "\\", ":", "..")


class ReplaceBackgroundInput(BaseModel):
    """Cut out the subject and put it on a new background. Give `color` or `image`, not both."""

    model_config = ConfigDict(extra="forbid")

    photo_id: str = Field(min_length=1)
    color: tuple[int, int, int] | None = Field(
        default=None, description="Background colour as red, green, blue (0-255)."
    )
    image: str | None = Field(
        default=None,
        description="File NAME of a picture in the configured backgrounds folder (no path).",
    )

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.color is None) == (self.image is None):
            raise ValueError("give exactly one of: color, image")
        if self.color is not None and not all(0 <= value <= 255 for value in self.color):
            raise ValueError("colour values must be 0-255")
        return self


OUTPUT_NOTE = (
    "The PSD and JPG were made from the virtual copy as it is now: they include its current "
    "edits and crop. Later edits or an undo in Lightroom do not change these files."
)


class ReplaceBackgroundResult(ToolResult):
    photo_id: str
    working_copy_created: bool
    psd_path: str
    jpg_path: str
    mask_path: str
    mask_provider: str
    mask: MaskMetrics
    background: str
    seconds: float
    note: str = OUTPUT_NOTE


def _refuse(message: str, **details: object) -> DarkroomError:
    return DarkroomError(
        ErrorCode.GUARDRAIL_REJECTED,
        message + " Nothing was changed.",
        next_step="Put the picture into the backgrounds folder set in darkroom.toml "
        "([backgrounds] dir) and give only its file name.",
        details=dict(details),
    )


def resolve_background(folder: Path, name: str, max_file_mb: float) -> Path:
    """Return the path of background `name` inside `folder`, or refuse.

    Only a plain file name is accepted: no URL, no path, nothing outside the folder. The file
    must be a real picture of an allowed type and not larger than `max_file_mb`.
    """
    if not name or any(part in name for part in FORBIDDEN_NAME_PARTS) or name != name.strip():
        raise _refuse(f"'{name}' is not a plain file name (no paths, no URLs).", name=name)
    root = folder.resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise _refuse(f"'{name}' is not directly inside the backgrounds folder.", name=name)
    if path.suffix.lower() not in BACKGROUND_EXTENSIONS:
        raise _refuse(f"'{name}' is not a JPG, PNG or TIFF picture.", name=name)
    if not path.is_file() or path.is_symlink():
        raise _refuse(f"There is no picture named '{name}' in {root}.", name=name)
    size_mb = path.stat().st_size / 1_000_000
    if size_mb > max_file_mb:
        raise _refuse(f"'{name}' is {size_mb:.0f} MB; the limit is {max_file_mb:g} MB.", name=name)
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        raise _refuse(f"'{name}' is not a readable picture.", name=name) from exc
    return path


def replace_background(ctx: ToolContext, args: ReplaceBackgroundInput) -> Outcome:
    if ctx.photoshop is None or ctx.masks is None:
        raise DarkroomError(
            ErrorCode.PS_NOT_REACHABLE,
            "Background replacement needs Photoshop and a mask backend, which are not set up here.",
        )
    background_file: Path | None = None
    if args.image is not None:
        background_file = resolve_background(
            ctx.settings.backgrounds.dir, args.image, ctx.settings.backgrounds.max_file_mb
        )

    started = time.monotonic()
    photoshop = ctx.photoshop
    copy, created = working_copy(ctx, args.photo_id)
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    tiff = ctx.lightroom.export_tiff(copy.id, f"background-{stamp}")

    working = photoshop.prepare_copy(tiff.path)
    mask_path = working.with_name(f"{working.stem}_mask.png")
    mask = ctx.masks.subject_mask(tiff.path, mask_path)
    if mask.metrics.is_blank:
        raise DarkroomError(
            ErrorCode.MASK_FAILED,
            "The mask is empty or covers everything, so there is no subject to cut out.",
            details={"coverage_pct": mask.metrics.coverage_pct},
        )

    photoshop.open_document(working)
    try:
        photoshop.apply_mask_from_png(working, mask.path, "subject")
        if background_file is not None:
            photoshop.add_image_layer_below(
                working, photoshop.prepare_copy(background_file), "background"
            )
            background = f"image {background_file.name}"
        else:
            assert args.color is not None
            photoshop.add_fill_layer_below(working, args.color, "background")
            background = f"colour {args.color}"
        psd = photoshop.save_psd(working)
        jpg = photoshop.save_jpg(working)
    finally:
        # The document may not have opened; nothing is saved either way.
        with contextlib.suppress(DarkroomError):
            photoshop.close_document(working)

    result = ReplaceBackgroundResult(
        photo_id=copy.id,
        working_copy_created=created,
        psd_path=str(psd),
        jpg_path=str(jpg),
        mask_path=str(mask.path),
        mask_provider=mask.provider,
        mask=mask.metrics,
        background=background,
        seconds=round(time.monotonic() - started, 1),
    )
    return Outcome(
        result,
        applied={
            "photo_id": copy.id,
            "outputs": [str(psd), str(jpg)],
            "mask_provider": mask.provider,
            "background": background,
        },
    )
