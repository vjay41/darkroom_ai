"""Numeric analysis of a preview: tones, clipping and colour cast.

Everything is computed on the encoded (gamma) RGB values of the preview, 0..1:
- luma = 0.2126 R + 0.7152 G + 0.0722 B
- highlight clipping: a pixel counts when any channel is >= 254/255
- shadow clipping: a pixel counts when all channels are <= 1/255
- detail: noise is estimated on the flattest quarter of the picture (smallest edge strength, away
  from pure black and white) as a robust spread of the second-difference response, in 8-bit levels,
  for luma and for chroma (R-G and B-G). The flattest pixels read a little low, never high. Edge
  strength is the 99th percentile of the luma gradient (0..1 per pixel). Both are measured on the
  preview (about 1024 px), so absolute values understate noise at full size: use them to compare
  before and after, not as a verdict.
- colour cast: measured on near-neutral mid-tone pixels (low saturation, luma 0.2..0.8), where the
  three channels should be equal. `blue_yellow` > 0 means too yellow/warm (R+G above B),
  `green_magenta` > 0 means too magenta (R+B above G). Values are in 0..1 channel units.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from darkroom_ai.imaging.io import Pixels

HIGH_CLIP = 254.0 / 255.0
LOW_CLIP = 1.0 / 255.0
HISTOGRAM_BINS = 32
NEUTRAL_MAX_SATURATION = 0.12
NEUTRAL_LUMA_RANGE = (0.2, 0.8)
MIN_NEUTRAL_FRACTION = 0.01
CAST_NOTICEABLE = 0.02


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True)


class Tones(_Result):
    mean: float
    p01: float
    p05: float
    p50: float
    p95: float
    p99: float
    histogram: tuple[float, ...]  # fraction of pixels per luma bin, dark to bright


class Clipping(_Result):
    highlights_pct: float
    shadows_pct: float
    red_high_pct: float
    green_high_pct: float
    blue_high_pct: float


class ColorCast(_Result):
    """Colour cast measured on near-neutral mid-tone pixels.

    When there are too few such pixels the cast is NOT MEASURABLE: the three numbers are None
    (never 0.0, which would read as "neutral") and `reason` says why.
    """

    measurable: bool
    blue_yellow: float | None
    green_magenta: float | None
    strength: float | None
    neutral_fraction: float
    confidence: str  # "ok" | "low" (too few near-neutral pixels to judge)
    description: str
    reason: str = ""


class Detail(_Result):
    noise_luma: float | None  # 8-bit levels in flat areas (None: no flat area to measure)
    noise_chroma: float | None
    edge_strength: float  # p99 of the luma gradient, 0..1 per pixel
    flat_fraction: float  # share of the picture used for the noise estimate


class ImageAnalysis(_Result):
    width: int
    height: int
    tones: Tones
    clipping: Clipping
    color_cast: ColorCast
    detail: Detail


def luma(pixels: Pixels) -> NDArray[np.float32]:
    weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    return np.asarray(pixels @ weights, dtype=np.float32)


def _pct(mask: np.ndarray) -> float:
    return round(float(mask.mean()) * 100.0, 3)


def _tones(y: np.ndarray) -> Tones:
    p01, p05, p50, p95, p99 = (float(v) for v in np.percentile(y, [1, 5, 50, 95, 99]))
    counts, _ = np.histogram(y, bins=HISTOGRAM_BINS, range=(0.0, 1.0))
    histogram = tuple(round(float(c) / y.size, 5) for c in counts)
    return Tones(
        mean=round(float(y.mean()), 4),
        p01=round(p01, 4),
        p05=round(p05, 4),
        p50=round(p50, 4),
        p95=round(p95, 4),
        p99=round(p99, 4),
        histogram=histogram,
    )


def _clipping(pixels: Pixels) -> Clipping:
    high = pixels >= HIGH_CLIP
    return Clipping(
        highlights_pct=_pct(high.any(axis=-1)),
        shadows_pct=_pct((pixels <= LOW_CLIP).all(axis=-1)),
        red_high_pct=_pct(high[..., 0]),
        green_high_pct=_pct(high[..., 1]),
        blue_high_pct=_pct(high[..., 2]),
    )


def _describe_cast(blue_yellow: float, green_magenta: float, confidence: str) -> str:
    if confidence == "low":
        return "not measurable: not enough near-neutral areas to judge a colour cast"
    parts = []
    if abs(blue_yellow) >= CAST_NOTICEABLE:
        parts.append("yellow/warm" if blue_yellow > 0 else "blue/cool")
    if abs(green_magenta) >= CAST_NOTICEABLE:
        parts.append("magenta" if green_magenta > 0 else "green")
    return "cast towards " + " and ".join(parts) if parts else "no noticeable cast"


def _color_cast(pixels: Pixels, y: np.ndarray) -> ColorCast:
    saturation = pixels.max(axis=-1) - pixels.min(axis=-1)
    neutral = (
        (saturation <= NEUTRAL_MAX_SATURATION)
        & (y >= NEUTRAL_LUMA_RANGE[0])
        & (y <= NEUTRAL_LUMA_RANGE[1])
    )
    fraction = float(neutral.mean())
    if fraction < MIN_NEUTRAL_FRACTION:
        return ColorCast(
            measurable=False,
            blue_yellow=None,
            green_magenta=None,
            strength=None,
            neutral_fraction=round(fraction, 4),
            confidence="low",
            description=_describe_cast(0.0, 0.0, "low"),
            reason=(
                f"only {fraction * 100:.2f} % of the picture is near-neutral mid-tone; at least "
                f"{MIN_NEUTRAL_FRACTION * 100:g} % is needed. Unknown is not the same as neutral."
            ),
        )
    red, green, blue = (float(pixels[..., i][neutral].mean()) for i in range(3))
    blue_yellow = (red + green) / 2.0 - blue
    green_magenta = (red + blue) / 2.0 - green
    return ColorCast(
        measurable=True,
        blue_yellow=round(blue_yellow, 4),
        green_magenta=round(green_magenta, 4),
        strength=round(float(np.hypot(blue_yellow, green_magenta)), 4),
        neutral_fraction=round(fraction, 4),
        confidence="ok",
        description=_describe_cast(blue_yellow, green_magenta, "ok"),
    )


_LAPLACE_NOISE = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
_NOISE_NORM = 6.0  # the mask's response to white noise has a spread of 6 x sigma
FLAT_SHARE = 0.25
MIN_FLAT_PIXELS = 400


def _noise(plane: NDArray[np.float32], flat: NDArray[np.bool_]) -> float:
    import cv2

    response = cv2.filter2D(plane, cv2.CV_32F, _LAPLACE_NOISE, borderType=cv2.BORDER_REFLECT)
    values = response[flat]
    spread = 1.4826 * float(np.median(np.abs(values - np.median(values))))
    return round(spread / _NOISE_NORM * 255.0, 3)


def _detail(pixels: Pixels, y: NDArray[np.float32]) -> Detail:
    import cv2

    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    gradient = np.sqrt(gx * gx + gy * gy)
    edge = round(float(np.percentile(gradient, 99)), 5)
    usable = (y > 0.04) & (y < 0.96)
    if int(usable.sum()) < MIN_FLAT_PIXELS:
        return Detail(noise_luma=None, noise_chroma=None, edge_strength=edge, flat_fraction=0.0)
    limit = float(np.percentile(gradient[usable], FLAT_SHARE * 100.0))
    flat = usable & (gradient <= limit)
    if int(flat.sum()) < MIN_FLAT_PIXELS:
        return Detail(noise_luma=None, noise_chroma=None, edge_strength=edge, flat_fraction=0.0)
    chroma = max(
        _noise(np.ascontiguousarray(pixels[..., 0] - pixels[..., 1]), flat),
        _noise(np.ascontiguousarray(pixels[..., 2] - pixels[..., 1]), flat),
    )
    return Detail(
        noise_luma=_noise(y, flat),
        noise_chroma=chroma,
        edge_strength=edge,
        flat_fraction=round(float(flat.mean()), 3),
    )


def analyze(pixels: Pixels) -> ImageAnalysis:
    """Measure tones, clipping and colour cast of an RGB image (float32, 0..1)."""
    if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.size == 0:
        raise ValueError("analyze() needs a non-empty H x W x 3 array")
    y = luma(pixels)
    return ImageAnalysis(
        width=int(pixels.shape[1]),
        height=int(pixels.shape[0]),
        tones=_tones(y),
        clipping=_clipping(pixels),
        color_cast=_color_cast(pixels, y),
        detail=_detail(pixels, y),
    )
