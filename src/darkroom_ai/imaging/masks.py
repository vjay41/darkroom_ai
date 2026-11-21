"""Subject masks behind one interface (MaskProvider), plus numeric mask quality measures.

Backends:
- SyntheticMaskProvider: deterministic ellipse, for tests and dry runs. Not a real segmentation.
- OnnxMaskProvider: a user-provided ONNX model through onnxruntime on a chosen GPU. Only usable
  when a model path is configured and the `ml` extra is installed. Nothing is ever downloaded.
- PhotoshopSelectSubjectProvider lives in adapters/photoshop (it needs the Photoshop adapter).

Every provider writes a grayscale PNG of the image's pixel size (white = subject) and returns a
MaskResult; that PNG is what the Photoshop hand-off (`apply_mask_from_png`) consumes.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.io import Mask, Pixels, load_image, save_mask_png

MIN_REGION_FRACTION = 0.0005  # regions smaller than this share of the image are counted as specks


class MaskMetrics(BaseModel):
    """Numeric description of a mask (no viewing needed)."""

    model_config = ConfigDict(frozen=True)

    width: int
    height: int
    coverage_pct: float  # share of pixels above 50%
    soft_edge_pct: float  # share of pixels that are neither black nor white (5%..95%)
    region_count: int  # connected subject regions at least MIN_REGION_FRACTION of the image
    speck_count: int  # smaller regions (noise)
    largest_region_pct: float  # largest region as a share of the image
    # Edge raggedness of the largest region:
    compactness: float  # perimeter^2 / (4 pi area); 1.0 = circle, larger = more ragged or elongated
    hull_perimeter_ratio: float  # perimeter / convex-hull perimeter; 1.0 = smooth convex outline
    solidity: float  # area / convex-hull area; 1.0 = no dents
    is_blank: bool  # all black or all white


class MaskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    provider: str
    metrics: MaskMetrics


class MaskProvider(Protocol):
    """Produces a subject mask PNG for an image file."""

    name: str

    def subject_mask(self, image_path: Path, out_path: Path) -> MaskResult: ...


def mask_metrics(mask: Mask) -> MaskMetrics:
    height, width = mask.shape
    total = float(mask.size)
    binary = (mask > 0.5).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    areas = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, count)]
    big = [a for a in areas if a >= MIN_REGION_FRACTION * total]

    compactness = hull_ratio = solidity = 0.0
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if contours:
        largest = max(contours, key=cv2.contourArea)
        area = float(cv2.contourArea(largest))
        perimeter = float(cv2.arcLength(largest, True))
        hull = cv2.convexHull(largest)
        hull_area = float(cv2.contourArea(hull))
        hull_perimeter = float(cv2.arcLength(hull, True))
        if area > 0:
            compactness = perimeter * perimeter / (4.0 * np.pi * area)
        if hull_perimeter > 0:
            hull_ratio = perimeter / hull_perimeter
        if hull_area > 0:
            solidity = area / hull_area
    coverage = float(binary.mean())
    return MaskMetrics(
        width=width,
        height=height,
        coverage_pct=round(coverage * 100.0, 3),
        soft_edge_pct=round(float(((mask > 0.05) & (mask < 0.95)).mean()) * 100.0, 3),
        region_count=len(big),
        speck_count=len(areas) - len(big),
        largest_region_pct=round(max(areas, default=0) / total * 100.0, 3),
        compactness=round(compactness, 3),
        hull_perimeter_ratio=round(hull_ratio, 3),
        solidity=round(solidity, 3),
        is_blank=coverage in (0.0, 1.0),
    )


def remove_specks(mask: Mask, min_fraction: float = MIN_REGION_FRACTION) -> tuple[Mask, int]:
    """Black out subject regions smaller than `min_fraction` of the image.

    Returns the cleaned mask and how many regions were removed.
    """
    binary = (mask > 0.5).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    threshold = min_fraction * mask.size
    small = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] < threshold]
    if not small:
        return mask, 0
    cleaned = mask.copy()
    cleaned[np.isin(labels, small)] = 0.0
    return cleaned, len(small)


def _finish(mask: Mask, image_shape: tuple[int, ...], out_path: Path, provider: str) -> MaskResult:
    if mask.shape != tuple(image_shape[:2]):
        raise DarkroomError(
            ErrorCode.MASK_FAILED,
            f"The {provider} mask is {mask.shape[1]} x {mask.shape[0]} but the image is "
            f"{image_shape[1]} x {image_shape[0]}.",
        )
    path = save_mask_png(np.clip(mask, 0.0, 1.0).astype(np.float32), out_path)
    return MaskResult(path=path, provider=provider, metrics=mask_metrics(mask))


class SyntheticMaskProvider:
    """Deterministic soft ellipse in the middle of the frame. For tests and dry runs only."""

    name = "synthetic"

    def __init__(self, *, feather: float = 0.01) -> None:
        self._feather = feather

    def subject_mask(self, image_path: Path, out_path: Path) -> MaskResult:
        pixels = load_image(image_path)
        height, width = pixels.shape[:2]
        mask = np.zeros((height, width), dtype=np.float32)
        centre = (width // 2, height // 2)
        axes = (max(1, width // 4), max(1, height * 3 // 10))
        cv2.ellipse(mask, centre, axes, 0, 0, 360, 1.0, thickness=-1)
        radius = int(max(width, height) * self._feather)
        soft: Mask = mask
        if radius > 0:
            soft = cv2.GaussianBlur(mask, (0, 0), sigmaX=radius).astype(np.float32)
        return _finish(soft, pixels.shape, out_path, self.name)


def mask_model_missing(reason: str, model_path: Path | None) -> DarkroomError:
    return DarkroomError(
        ErrorCode.MASK_MODEL_MISSING,
        f"The ONNX mask model cannot be used: {reason}.",
        details={"model_path": str(model_path) if model_path else None},
    )


def _default_session(model_path: Path, gpu_index: int, allow_cpu: bool) -> Any:
    """Create an onnxruntime session on the given GPU. No silent CPU fall-back."""
    try:
        import onnxruntime
    except ImportError as exc:
        raise mask_model_missing(
            "onnxruntime is not installed (run `make install-ml`)", model_path
        ) from exc
    providers: list[Any] = [("CUDAExecutionProvider", {"device_id": gpu_index})]
    if allow_cpu:
        providers.append("CPUExecutionProvider")
    session = onnxruntime.InferenceSession(str(model_path), providers=providers)
    active = session.get_providers()
    if "CUDAExecutionProvider" not in active and not allow_cpu:
        raise DarkroomError(
            ErrorCode.MASK_FAILED,
            f"The mask model did not load on GPU {gpu_index} (active: {', '.join(active)}).",
            next_step="Check the NVIDIA driver and the CUDA libraries from `make install-ml`, or "
            "set masks.allow_cpu = true to accept slow CPU inference.",
            details={"providers": active, "gpu_index": gpu_index},
        )
    return session


class OnnxMaskProvider:
    """Salient-object / matting model: RGB image in, one-channel mask out.

    Pre-processing (resize to `input_size`, scale to 0..1, normalise with `mean`/`std`, NCHW) and
    the output handling (first output, squeezed, sigmoid if it is not already in 0..1, min-max
    rescale optional) cover the common background-removal models; the values come from config
    because they differ between models.
    """

    name = "onnx"

    def __init__(
        self,
        model_path: Path | None,
        *,
        input_size: tuple[int, int] = (1024, 1024),
        mean: tuple[float, float, float] = (0.485, 0.456, 0.406),
        std: tuple[float, float, float] = (0.229, 0.224, 0.225),
        gpu_index: int = 1,
        allow_cpu: bool = False,
        session_factory: Callable[[Path, int, bool], Any] = _default_session,
    ) -> None:
        if model_path is None:
            raise mask_model_missing("no model path is configured (masks.onnx_model)", None)
        if not model_path.is_file():
            raise mask_model_missing("the model file does not exist", model_path)
        self._model_path = model_path
        self._input_size = input_size
        self._mean = np.array(mean, dtype=np.float32)
        self._std = np.array(std, dtype=np.float32)
        self._gpu_index = gpu_index
        self._allow_cpu = allow_cpu
        self._session_factory = session_factory
        self._session: Any = None

    def _infer(self, pixels: Pixels) -> Mask:
        if self._session is None:
            self._session = self._session_factory(
                self._model_path, self._gpu_index, self._allow_cpu
            )
        height, width = pixels.shape[:2]
        resized = cv2.resize(pixels, self._input_size, interpolation=cv2.INTER_AREA)
        normalised = (resized - self._mean) / self._std
        tensor = normalised.transpose(2, 0, 1)[np.newaxis].astype(np.float32)
        input_name = self._session.get_inputs()[0].name
        output = np.asarray(self._session.run(None, {input_name: tensor})[0], dtype=np.float32)
        small = np.squeeze(output)
        if small.ndim != 2:
            raise DarkroomError(
                ErrorCode.MASK_FAILED,
                f"The mask model returned an output of shape {output.shape}; expected one channel.",
            )
        if small.min() < 0.0 or small.max() > 1.0:
            small = 1.0 / (1.0 + np.exp(-small))
        return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32)

    def subject_mask(self, image_path: Path, out_path: Path) -> MaskResult:
        pixels = load_image(image_path)
        return _finish(self._infer(pixels), pixels.shape, out_path, self.name)
