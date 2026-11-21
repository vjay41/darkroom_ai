"""Select Subject spike: Photoshop mask backend on a COPY of a test photo (device mode only).

Reads Photoshop's Select Subject mode first and stops unless it is Device. Then runs the mask
provider, which opens a copy, runs Select Subject, writes the mask PNG and closes without saving.
Prints mask metrics only; no image is displayed.

Usage:  poetry run python scripts/select_subject_spike.py --photo PATH
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

from darkroom_ai.adapters.photoshop.client import PhotoshopClient, unique_path
from darkroom_ai.adapters.photoshop.masks import DEVICE_MODE, PhotoshopSelectSubjectProvider
from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.imaging.io import load_image

failures: list[str] = []


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photo", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")

    try:
        photoshop = PhotoshopClient.from_settings(settings)
        info = photoshop.connect()
        print(f"photoshop {info.version}, open documents: {info.documents}")
        mode = photoshop.select_subject_mode()
        print(f"Select Subject mode (read from Photoshop): {mode}")
        if mode != DEVICE_MODE:
            print("BLOCKED: the mode is not Device. Nothing was run.")
            return 3

        photo_hash = sha256(args.photo)
        height, width = load_image(args.photo).shape[:2]
        out = unique_path(
            settings.paths.outputs_dir.resolve() / "masks" / f"{args.photo.stem}_subject.png"
        )
        started = time.monotonic()
        result = PhotoshopSelectSubjectProvider(photoshop).subject_mask(args.photo, out)
        elapsed = time.monotonic() - started
        mode_after = photoshop.select_subject_mode()
        after = photoshop.info()
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}", file=sys.stderr)
        print(f"details: {exc.details}", file=sys.stderr)
        return 2

    metrics = result.metrics
    print(f"mask: {result.path} (provider {result.provider}, {elapsed:.1f} s)")
    print(f"  size: {metrics.width} x {metrics.height}")
    print(f"  coverage: {metrics.coverage_pct} % of the frame")
    print(f"  regions: {metrics.region_count} (plus {metrics.speck_count} specks)")
    print(f"  largest region: {metrics.largest_region_pct} % of the frame")
    print(f"  soft-edge pixels: {metrics.soft_edge_pct} %")
    print(
        f"  edge raggedness: compactness {metrics.compactness}, hull-perimeter ratio "
        f"{metrics.hull_perimeter_ratio}, solidity {metrics.solidity}"
    )
    check("mode still Device after the call", mode_after == DEVICE_MODE)
    check("mask has the image's pixel size", (metrics.width, metrics.height) == (width, height))
    check("mask is neither all black nor all white", not metrics.is_blank)
    check("working copy closed (document count unchanged)", after.documents == info.documents)
    check("test photo unchanged on disk", sha256(args.photo) == photo_hash)
    print("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
