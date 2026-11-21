"""Prepares a picture for the LOCAL model: downscaled JPEG as a data URL inside the request.

The picture is never written anywhere else and never leaves this computer: the request goes to
the profile's loopback address only.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from darkroom_ai.errors import DarkroomError, ErrorCode

JPEG_QUALITY = 85


@dataclass(frozen=True)
class PreparedImage:
    width: int
    height: int
    jpeg_bytes: int
    data_url: str

    def part(self) -> dict[str, Any]:
        """The message part in OpenAI chat format."""
        return {"type": "image_url", "image_url": {"url": self.data_url}}

    def describe(self) -> str:
        return f"JPEG {self.width} x {self.height}, {self.jpeg_bytes / 1000:.0f} kB"


def prepare_pixels(bgr: np.ndarray, max_px: int) -> PreparedImage:
    """Downscale so the long edge is at most `max_px` and encode as JPEG."""
    height, width = bgr.shape[:2]
    scale = min(1.0, max_px / max(height, width))
    if scale < 1.0:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        bgr = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise DarkroomError(ErrorCode.IMAGE_UNREADABLE, "Could not encode the preview as JPEG.")
    data = encoded.tobytes()
    return PreparedImage(
        width=int(bgr.shape[1]),
        height=int(bgr.shape[0]),
        jpeg_bytes=len(data),
        data_url="data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"),
    )


def prepare_image(path: Path, max_px: int) -> PreparedImage:
    """Load a preview file and prepare it for the local model."""
    if not path.is_file():
        raise DarkroomError(ErrorCode.IMAGE_UNREADABLE, f"Preview not found: {path}")
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise DarkroomError(ErrorCode.IMAGE_UNREADABLE, f"Could not read the preview: {path}")
    return prepare_pixels(image, max_px)
