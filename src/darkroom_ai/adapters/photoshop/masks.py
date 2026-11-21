"""Mask backend 1: Photoshop Select Subject, on the device only.

Implements imaging.masks.MaskProvider. Photoshop works on a COPY of the image inside its work
folder; the copy is closed without saving. Select Subject is refused unless Photoshop's
processing mode is "Device", because the cloud mode would upload the picture.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from darkroom_ai.adapters.photoshop.client import PhotoshopClient, unique_path
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.io import load_mask, save_mask_png
from darkroom_ai.imaging.masks import MaskResult, mask_metrics, remove_specks

DEVICE_MODE = "imageProcessingModeDevice"


def not_device_error(mode: str) -> DarkroomError:
    return DarkroomError(
        ErrorCode.GUARDRAIL_REJECTED,
        f"Photoshop's Select Subject mode is '{mode}', not Device. Refused: only on-device "
        "processing is allowed, nothing may be uploaded.",
        next_step="In Photoshop open Preferences > Image Processing and set 'Select Subject and "
        'Remove Background\' to Device, then retry. Or use masks.backend = "onnx".',
        details={"mode": mode},
    )


class PhotoshopSelectSubjectProvider:
    name = "photoshop-select-subject"

    def __init__(self, photoshop: PhotoshopClient) -> None:
        self._photoshop = photoshop

    def subject_mask(self, image_path: Path, out_path: Path) -> MaskResult:
        if out_path.exists():
            raise DarkroomError(
                ErrorCode.GUARDRAIL_REJECTED,
                f"Refusing to overwrite an existing file: {out_path}",
                next_step="Rename or remove that file yourself; files are never overwritten.",
            )
        photoshop = self._photoshop
        mode = photoshop.select_subject_mode()
        if mode != DEVICE_MODE:
            raise not_device_error(mode)

        working = photoshop.prepare_copy(image_path)
        raw_png = unique_path(working.with_name(f"{working.stem}_select_subject.png"))
        photoshop.open_document(working)
        try:
            photoshop.select_subject_mask(working, raw_png)
        finally:
            with contextlib.suppress(DarkroomError):
                photoshop.close_document(working)

        mask, _removed = remove_specks(load_mask(raw_png))
        save_mask_png(mask, out_path)
        # The PNG Photoshop writes is large (tens of MB) and only an intermediate: remove it and
        # the working copy now that the final mask is saved. Both are our own files.
        for leftover in (raw_png, working):
            with contextlib.suppress(OSError):
                leftover.unlink()
        return MaskResult(path=out_path, provider=self.name, metrics=mask_metrics(mask))
