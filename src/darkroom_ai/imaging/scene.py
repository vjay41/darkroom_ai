"""Measurements that help a skill find things in a picture: dark areas, odd-one-out blobs.

Everything here reports numbers and boxes (fractions of the picture, y from the top). Nothing
decides what to remove, relight or keep; that judgement belongs to the skill and the person.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

from darkroom_ai.imaging.analysis import luma
from darkroom_ai.imaging.io import Pixels

WORK_LONG_EDGE = 384


class Cell(BaseModel):
    model_config = ConfigDict(frozen=True)

    row: int
    col: int
    box: tuple[float, float, float, float]
    mean_luma: float
    p10: float
    p90: float
    zone: int  # 0 (black) .. 10 (white), from the mean luma
    shadows_clipped_pct: float
    highlights_clipped_pct: float


class DarkRegion(BaseModel):
    model_config = ConfigDict(frozen=True)

    box: tuple[float, float, float, float]
    area_pct: float
    mean_luma: float
    surround_luma: float  # mean luma in a ring around the region
    edge_contrast: float  # mean luma step across the region's border: high = hard edge
    touches_border: bool


class ZoneMap(BaseModel):
    model_config = ConfigDict(frozen=True)

    rows: int
    cols: int
    cells: tuple[Cell, ...]
    dark_regions: tuple[DarkRegion, ...]
    shadow_luma_threshold: float


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    box: tuple[float, float, float, float]
    area_pct: float
    centre: tuple[float, float]
    touches_border: bool
    mean_lightness: float  # CIELAB L*
    mean_chroma: float
    delta_e_vs_surround: float  # colour difference to the ring around it
    edge_density: float  # share of edge pixels inside the box
    saliency: float  # stand-out score, comparable only within one picture


def _small(pixels: Pixels) -> Pixels:
    height, width = pixels.shape[:2]
    scale = WORK_LONG_EDGE / max(height, width)
    if scale >= 1.0:
        return pixels
    resized = cv2.resize(pixels, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return np.asarray(resized, dtype=np.float32)


def _fraction_box(x: int, y: int, w: int, h: int, width: int, height: int) -> tuple[float, ...]:
    return (
        round(x / width, 4),
        round(y / height, 4),
        round((x + w) / width, 4),
        round((y + h) / height, 4),
    )


def zone_map(
    pixels: Pixels, rows: int = 6, cols: int = 8, shadow_luma: float = 0.25, max_regions: int = 6
) -> ZoneMap:
    small = _small(pixels)
    y = luma(small)
    height, width = y.shape
    cells: list[Cell] = []
    for r in range(rows):
        for c in range(cols):
            top, bottom = r * height // rows, (r + 1) * height // rows
            left, right = c * width // cols, (c + 1) * width // cols
            patch = y[top:bottom, left:right]
            p10, p90 = (float(v) for v in np.percentile(patch, [10, 90]))
            cells.append(
                Cell(
                    row=r,
                    col=c,
                    box=(
                        round(c / cols, 4),
                        round(r / rows, 4),
                        round((c + 1) / cols, 4),
                        round((r + 1) / rows, 4),
                    ),
                    mean_luma=round(float(patch.mean()), 4),
                    p10=round(p10, 4),
                    p90=round(p90, 4),
                    zone=round(float(patch.mean()) * 10),
                    shadows_clipped_pct=round(float((patch <= 0.02).mean()) * 100, 2),
                    highlights_clipped_pct=round(float((patch >= 0.98).mean()) * 100, 2),
                )
            )
    blurred = cv2.GaussianBlur(y, (0, 0), max(width, height) * 0.01)
    dark = (blurred < shadow_luma).astype(np.uint8)
    dark = np.asarray(cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    gradient = cv2.magnitude(
        cv2.Sobel(blurred, cv2.CV_32F, 1, 0), cv2.Sobel(blurred, cv2.CV_32F, 0, 1)
    )
    regions: list[DarkRegion] = []
    for index in range(1, count):
        x, yy, w, h, area = (int(v) for v in stats[index])
        if area < 0.005 * width * height:
            continue
        inside = labels == index
        mask8 = inside.astype(np.uint8)
        near = cv2.dilate(mask8, np.ones((9, 9), np.uint8)).astype(bool)
        far = cv2.dilate(mask8, np.ones((19, 19), np.uint8)).astype(bool)
        ring = far & ~near  # skip the soft edge itself
        border = cv2.dilate(inside.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(
            bool
        ) & ~cv2.erode(inside.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        regions.append(
            DarkRegion(
                box=_fraction_box(x, yy, w, h, width, height),
                area_pct=round(100.0 * area / (width * height), 2),
                mean_luma=round(float(y[inside].mean()), 4),
                surround_luma=round(float(y[ring].mean()) if ring.any() else 0.0, 4),
                edge_contrast=round(float(gradient[border].mean()) if border.any() else 0.0, 4),
                touches_border=bool(x == 0 or yy == 0 or x + w >= width or yy + h >= height),
            )
        )
    regions.sort(key=lambda region: -region.area_pct)
    return ZoneMap(
        rows=rows,
        cols=cols,
        cells=tuple(cells),
        dark_regions=tuple(regions[:max_regions]),
        shadow_luma_threshold=shadow_luma,
    )


def find_candidates(pixels: Pixels, max_items: int = 8) -> tuple[Candidate, ...]:
    """Small areas that stand out from their surroundings in colour or brightness."""
    small = _small(pixels)
    height, width = small.shape[:2]
    lab = cv2.cvtColor(np.clip(small, 0.0, 1.0), cv2.COLOR_RGB2Lab)
    local = cv2.GaussianBlur(lab, (0, 0), 1.5)
    around = cv2.GaussianBlur(lab, (0, 0), max(width, height) * 0.06)
    saliency = np.linalg.norm(local - around, axis=2)
    threshold = float(saliency.mean() + 2.0 * saliency.std())
    binary = (saliency > max(threshold, 8.0)).astype(np.uint8)
    binary = np.asarray(
        cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)), np.uint8
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    gray = np.clip(luma(small) * 255.0, 0, 255).astype(np.uint8)
    edges = cv2.Canny(gray, 60, 160) > 0
    chroma = (
        np.hypot(lab[..., 1] - 128.0, lab[..., 2] - 128.0)
        if lab.dtype != np.float32
        else np.hypot(lab[..., 1], lab[..., 2])
    )
    found: list[Candidate] = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        share = area / (width * height)
        if share < 0.0005 or share > 0.15:
            continue
        inside = labels == index
        ring = (
            cv2.dilate(inside.astype(np.uint8), np.ones((11, 11), np.uint8)).astype(bool) & ~inside
        )
        if not ring.any():
            continue
        delta = float(np.linalg.norm(lab[inside].mean(axis=0) - lab[ring].mean(axis=0)))
        found.append(
            Candidate(
                box=_fraction_box(x, y, w, h, width, height),
                area_pct=round(100.0 * share, 2),
                centre=(round((x + w / 2) / width, 3), round((y + h / 2) / height, 3)),
                touches_border=bool(x == 0 or y == 0 or x + w >= width or y + h >= height),
                mean_lightness=round(float(lab[..., 0][inside].mean()), 1),
                mean_chroma=round(float(chroma[inside].mean()), 1),
                delta_e_vs_surround=round(delta, 1),
                edge_density=round(float(edges[y : y + h, x : x + w].mean()), 3),
                saliency=round(float(saliency[inside].mean()) * (area**0.5), 1),
            )
        )
    found.sort(key=lambda item: -item.saliency)
    return tuple(found[:max_items])


def regions_mask(
    shape: tuple[int, int], regions: list[dict[str, Any]], expand_px: int
) -> np.ndarray:
    """Binary mask (uint8 0/255) from boxes/ellipses given as picture fractions."""
    height, width = shape
    mask = np.zeros((height, width), np.uint8)
    for region in regions:
        x0, y0, x1, y1 = region["box"]
        left, right = round(x0 * width), round(x1 * width)
        top, bottom = round(y0 * height), round(y1 * height)
        if region.get("shape") == "ellipse":
            cv2.ellipse(
                mask,
                ((left + right) // 2, (top + bottom) // 2),
                (max(1, (right - left) // 2), max(1, (bottom - top) // 2)),
                0,
                0,
                360,
                255,
                -1,
            )
        else:
            mask[top:bottom, left:right] = 255
    if expand_px > 0:
        size = 2 * expand_px + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        mask = np.asarray(cv2.dilate(mask, kernel), np.uint8)
    return mask


def inpaint(pixels: Pixels, mask: np.ndarray) -> Pixels:
    """Fill the masked area from its surroundings (OpenCV Telea). Only masked pixels change."""
    eight = np.clip(np.rint(pixels * 255.0), 0, 255).astype(np.uint8)
    radius = max(3, round(max(mask.shape) * 0.004))
    filled = cv2.inpaint(cv2.cvtColor(eight, cv2.COLOR_RGB2BGR), mask, radius, cv2.INPAINT_TELEA)
    filled_rgb = cv2.cvtColor(filled, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    out = pixels.copy()
    inside = mask > 0
    out[inside] = filled_rgb[inside]
    return np.asarray(out, dtype=np.float32)


def fix_red_eye(
    pixels: Pixels, boxes: list[tuple[float, float, float, float]]
) -> tuple[Pixels, list[float]]:
    """Darken strongly red pixels inside each eye box. Returns the picture and, per box, the share
    of the box's pixels that were changed."""
    out = pixels.copy()
    height, width = pixels.shape[:2]
    shares: list[float] = []
    for x0, y0, x1, y1 in boxes:
        left, right = round(x0 * width), max(round(x1 * width), round(x0 * width) + 1)
        top, bottom = round(y0 * height), max(round(y1 * height), round(y0 * height) + 1)
        patch = out[top:bottom, left:right]
        red, green, blue = patch[..., 0], patch[..., 1], patch[..., 2]
        reddish = (red > 0.25) & (red > 1.6 * np.maximum(green, blue))
        reddish = cv2.dilate(reddish.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        neutral = (green + blue) / 2.0 * 0.85
        patch[..., 0] = np.where(reddish, np.minimum(red, neutral), red)
        shares.append(round(float(reddish.mean()), 4))
    return np.asarray(out, dtype=np.float32), shares
