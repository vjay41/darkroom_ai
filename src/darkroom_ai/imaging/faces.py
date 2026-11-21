"""Face detection and skin-tone level.

Face analysis is only available with a user-provided face model (YuNet ONNX). Without one the
functions raise FACE_MODEL_MISSING; there is no colour-based guess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.analysis import luma
from darkroom_ai.imaging.io import Pixels


class FaceBox(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: int
    y: int
    width: int
    height: int
    score: float


class FaceReading(BaseModel):
    model_config = ConfigDict(frozen=True)

    box: FaceBox
    skin_luma_mean: float  # mean luma (0..1) of the central part of the face box
    skin_luma_p50: float


class FaceAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    detector: str
    faces: tuple[FaceReading, ...]


class FaceDetector(Protocol):
    name: str

    def detect(self, pixels: Pixels) -> list[FaceBox]: ...


def face_model_missing(model_path: Path | None = None) -> DarkroomError:
    where = f" ({model_path})" if model_path else ""
    return DarkroomError(
        ErrorCode.FACE_MODEL_MISSING,
        f"No face-detection model is available{where}.",
        details={"model_path": str(model_path) if model_path else None},
    )


class YuNetFaceDetector:
    """OpenCV FaceDetectorYN with a user-provided YuNet ONNX file. Nothing is downloaded."""

    name = "yunet"

    def __init__(self, model_path: Path, *, score_threshold: float = 0.7) -> None:
        if not model_path.is_file():
            raise face_model_missing(model_path)
        self._model_path = model_path
        self._score_threshold = score_threshold

    def detect(self, pixels: Pixels) -> list[FaceBox]:
        import cv2

        height, width = pixels.shape[:2]
        bgr = cv2.cvtColor(np.clip(pixels * 255.0, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        detector = cv2.FaceDetectorYN.create(
            str(self._model_path), "", (width, height), self._score_threshold
        )
        _, found = detector.detect(bgr)
        boxes = []
        for row in found if found is not None else []:
            x, y, w, h = (round(float(v)) for v in row[:4])
            boxes.append(FaceBox(x=x, y=y, width=w, height=h, score=round(float(row[-1]), 4)))
        return boxes


def analyze_faces(pixels: Pixels, detector: FaceDetector | None) -> FaceAnalysis:
    """Detect faces and measure each one's skin-tone level.

    Raises FACE_MODEL_MISSING when no detector is configured.
    """
    if detector is None:
        raise face_model_missing()
    y = luma(pixels)
    height, width = y.shape
    readings = []
    for box in detector.detect(pixels):
        # Central 50% of the box: mostly skin, little hair or background.
        x0 = max(0, box.x + box.width // 4)
        y0 = max(0, box.y + box.height // 4)
        x1 = min(width, box.x + box.width - box.width // 4)
        y1 = min(height, box.y + box.height - box.height // 4)
        if x1 <= x0 or y1 <= y0:
            continue
        patch = y[y0:y1, x0:x1]
        readings.append(
            FaceReading(
                box=box,
                skin_luma_mean=round(float(patch.mean()), 4),
                skin_luma_p50=round(float(np.median(patch)), 4),
            )
        )
    return FaceAnalysis(detector=detector.name, faces=tuple(readings))
