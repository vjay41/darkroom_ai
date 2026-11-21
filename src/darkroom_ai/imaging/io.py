"""Load images into float arrays and save masks.

Pixels are float32 RGB in 0..1, as encoded in the file (no colour management is applied; previews
exported by the Lightroom plug-in are sRGB).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from darkroom_ai.errors import DarkroomError, ErrorCode

Pixels = NDArray[np.float32]  # H x W x 3, RGB, 0..1
Mask = NDArray[np.float32]  # H x W, 0..1 (1 = subject)

RAW_EXTENSIONS = frozenset({".nef", ".cr2", ".cr3", ".arw", ".raf", ".rw2", ".orf", ".dng", ".pef"})


def _failed(path: Path, reason: str) -> DarkroomError:
    return DarkroomError(
        ErrorCode.IMAGE_UNREADABLE,
        f"Could not read the image {path}: {reason}",
        details={"path": str(path)},
    )


def _load_raw(path: Path) -> Pixels:
    import rawpy

    try:
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=16)
    except Exception as exc:
        raise _failed(path, f"{type(exc).__name__}: {exc}") from exc
    return (rgb.astype(np.float32) / 65535.0).astype(np.float32)


def load_image(path: Path) -> Pixels:
    """Load JPG, PNG, 8/16-bit TIFF or RAW as float32 RGB in 0..1."""
    if not path.is_file():
        raise _failed(path, "file not found")
    if path.suffix.lower() in RAW_EXTENSIONS:
        return _load_raw(path)
    # np.fromfile + imdecode instead of cv2.imread so that non-ASCII paths work on Windows.
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise _failed(path, "unsupported or damaged file")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        image = image[:, :, :3]
    scale = 65535.0 if image.dtype == np.uint16 else 255.0
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return (rgb.astype(np.float32) / scale).astype(np.float32)


def save_mask_png(mask: Mask, path: Path) -> Path:
    """Save a 0..1 mask as an 8-bit grayscale PNG. Refuses to overwrite an existing file."""
    if path.exists():
        raise DarkroomError(
            ErrorCode.GUARDRAIL_REJECTED,
            f"Refusing to overwrite an existing file: {path}",
            details={"path": str(path)},
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.clip(np.rint(mask * 255.0), 0, 255).astype(np.uint8)
    ok, encoded = cv2.imencode(".png", pixels)
    if not ok:
        raise DarkroomError(ErrorCode.IMAGE_UNREADABLE, f"Could not encode the mask for {path}")
    encoded.tofile(str(path))
    return path


def load_mask(path: Path) -> Mask:
    """Load a grayscale mask PNG as float32 in 0..1."""
    if not path.is_file():
        raise _failed(path, "file not found")
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise _failed(path, "unsupported or damaged file")
    return (image.astype(np.float32) / 255.0).astype(np.float32)
