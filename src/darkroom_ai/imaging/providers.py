"""Builds the configured mask provider and face detector from settings.

Mask backends (masks.backend in darkroom.toml):
- "photoshop": Photoshop Select Subject on the device (default). Supplied by the caller, because
  it needs the Photoshop adapter.
- "onnx": a user-provided ONNX model (masks.onnx_model). Never downloaded.
- "synthetic": a fixed ellipse, for tests and dry runs only.
"""

from __future__ import annotations

from collections.abc import Callable

from darkroom_ai.config import Settings
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.faces import FaceDetector, YuNetFaceDetector
from darkroom_ai.imaging.masks import MaskProvider, OnnxMaskProvider, SyntheticMaskProvider


def build_mask_provider(
    settings: Settings, *, photoshop: Callable[[], MaskProvider] | None = None
) -> MaskProvider:
    """Return the mask provider selected by masks.backend."""
    masks = settings.masks
    if masks.backend == "synthetic":
        return SyntheticMaskProvider()
    if masks.backend == "onnx":
        return OnnxMaskProvider(
            masks.onnx_model,
            input_size=(masks.onnx_input_width, masks.onnx_input_height),
            mean=masks.onnx_mean,
            std=masks.onnx_std,
            gpu_index=masks.gpu_index,
            allow_cpu=masks.allow_cpu,
        )
    if photoshop is None:
        raise DarkroomError(
            ErrorCode.MASK_FAILED,
            "The Photoshop mask backend is selected but is not available here.",
            next_step='Use masks.backend = "onnx" with a model file, or run from a command that '
            "connects to Photoshop.",
        )
    return photoshop()


def build_face_detector(settings: Settings) -> FaceDetector | None:
    """YuNet detector when faces.model is set; otherwise None (face analysis is unavailable)."""
    if settings.faces.model is None:
        return None
    return YuNetFaceDetector(settings.faces.model, score_threshold=settings.faces.score_threshold)
