"""Hand-off spike: Lightroom 16-bit TIFF export -> Photoshop layer mask from a PNG -> PSD + JPG.

Copies only. In Lightroom a virtual copy of a test photo is exported; in Photoshop a copy of that
TIFF is opened. The mask is a synthetic soft ellipse made locally (no model).

Usage:  poetry run python scripts/handoff_spike.py --photo PATH [--tiff PATH]
  --photo  a test photo under lightroom.test_photos_dir
  --tiff   skip Lightroom and use this existing TIFF from outputs/handoff instead
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import time
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageFilter, ImageStat

from darkroom_ai.adapters.lightroom.bridge_file import load_bridge_file
from darkroom_ai.adapters.lightroom.client import LightroomClient
from darkroom_ai.adapters.photoshop.client import PhotoshopClient, unique_path
from darkroom_ai.config import Settings, load_config
from darkroom_ai.errors import DarkroomError

FILL_RGB = (200, 30, 30)
TIFF_BITS_PER_SAMPLE = 258
failures: list[str] = []
Image.MAX_IMAGE_PIXELS = None  # our own large test images


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def export_from_lightroom(settings: Settings, photo: Path, stamp: str) -> Path:
    """Virtual copy of the test photo -> 16-bit TIFF in outputs/handoff."""
    with LightroomClient.from_settings(settings, load_bridge_file()) as lightroom:
        info = lightroom.ping()
        print(f"  lightroom {info.lightroom_version}, writes allowed: {info.writes_allowed}")
        print(f"  plug-in photo folder rule: {info.photos_root}")
        check("plug-in enforces the test-photo folder", info.photos_root is not None)
        commands = lightroom.list_commands()
        print(f"  plug-in build {commands.plugin_version}")
        print(f"  read commands: {', '.join(commands.read)}")
        print(f"  write commands: {', '.join(commands.write)}")
        check("export_tiff is registered as a write command", "export_tiff" in commands.write)
        master = lightroom.find_photo(str(photo))
        if master is None:
            raise SystemExit(f"{photo} is not in the open catalog (import it with 'Add').")
        copy = lightroom.create_virtual_copy(master.id, f"darkroom handoff {stamp}")
        print(f"  virtual copy: {copy.id} ({copy.copy_name})")
        check("exporting from a virtual copy", copy.is_virtual_copy and copy.master_id == master.id)
        try:
            lightroom.export_tiff(master.id, f"handoff-{stamp}-master")
            check("export from a master photo is refused", False)
        except DarkroomError as exc:
            check(
                "export from a master photo is refused",
                exc.details.get("plugin_code") == "NOT_A_VIRTUAL_COPY",
            )
        started = time.monotonic()
        exported = lightroom.export_tiff(copy.id, f"handoff-{stamp}")
        print(f"  TIFF: {exported.path}")
        elapsed = time.monotonic() - started
        print(f"  export time: {elapsed:.1f} s (requested {exported.color_space})")
        try:
            lightroom.export_tiff(copy.id, f"handoff-{stamp}")
            check("a second export with the same name is refused", False)
        except DarkroomError as exc:
            check(
                "a second export with the same name is refused",
                exc.details.get("plugin_code") == "FILE_EXISTS",
            )
        return exported.path


def profile_name(icc: bytes | None) -> str:
    """Description of an embedded ICC profile, or a note that there is none."""
    if not icc:
        return "none embedded"
    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    return (ImageCms.getProfileDescription(profile) or "unnamed profile").strip()


def make_mask(size: tuple[int, int], target: Path) -> Path:
    """Soft white ellipse on black, same pixel size as the image (white = keep)."""
    width, height = size
    mask = Image.new("L", size, 0)
    box = (width * 0.25, height * 0.2, width * 0.75, height * 0.8)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(4, width // 200)))
    path = unique_path(target)
    mask.save(path)
    return path


def region_mean(path: Path, box: tuple[int, int, int, int]) -> tuple[float, float, float]:
    with Image.open(path) as image:
        stat = ImageStat.Stat(image.convert("RGB").crop(box))
    return (stat.mean[0], stat.mean[1], stat.mean[2])


def close_to(a: tuple[float, ...], b: tuple[float, ...], tolerance: float) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))


def run(settings: Settings, photo: Path, tiff: Path | None) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outputs = settings.paths.outputs_dir.resolve()
    photo_hash = sha256(photo)

    print("1. Lightroom export")
    if tiff is None:
        tiff = export_from_lightroom(settings, photo, stamp)
    else:
        print(f"  using existing TIFF (Lightroom skipped): {tiff}")
    check("TIFF is inside outputs", tiff.resolve().is_relative_to(outputs))
    with Image.open(tiff) as image:
        bits = tuple(image.tag_v2[TIFF_BITS_PER_SAMPLE])  # type: ignore[attr-defined]
        icc = image.info.get("icc_profile")
        size = image.size
    print(f"  TIFF header: {size[0]} x {size[1]}, bits per sample {bits}")
    print(f"  TIFF colour profile: {profile_name(icc)}")
    print(f"  TIFF size on disk: {tiff.stat().st_size / 1_000_000:.1f} MB")
    check("TIFF is 16 bit per channel", set(bits) == {16})
    tiff_hash = sha256(tiff)

    print("2. Photoshop")
    photoshop = PhotoshopClient.from_settings(settings)
    info = photoshop.connect()
    print(f"  photoshop {info.version}, open documents: {info.documents}")
    working = photoshop.prepare_copy(tiff)
    document = photoshop.open_document(working)
    print(f"  opened copy: {document}")
    check("Photoshop reports 16 bit", document.bits == 16)
    check("document size matches the TIFF", (document.width, document.height) == size)

    reference = photoshop.save_jpg(working, working.with_name(f"{working.stem}_reference.jpg"))
    mask = make_mask(size, working.with_name(f"{working.stem}_mask.png"))
    print(f"  mask: {mask}")
    photoshop.apply_mask_from_png(working, mask, "subject")
    photoshop.add_fill_layer_below(working, FILL_RGB, "background")
    layers = photoshop.layers(working)
    print(f"  layers (top to bottom): {[(layer.name, layer.has_mask) for layer in layers]}")
    check(
        "top layer has a mask, new background below",
        [(layer.name, layer.has_mask) for layer in layers]
        == [("subject", True), ("background", False)],
    )
    psd = photoshop.save_psd(working)
    jpg = photoshop.save_jpg(working)
    photoshop.close_document(working)
    print(f"  PSD: {psd} ({psd.stat().st_size} bytes)")
    print(f"  JPG: {jpg} ({jpg.stat().st_size} bytes)")
    check("PSD and JPG are new files inside outputs", all(p.is_file() for p in (psd, jpg)))

    print("3. Numeric checks")
    width, height = size
    centre = (width * 45 // 100, height * 45 // 100, width * 55 // 100, height * 55 // 100)
    corner = (0, 0, width // 20, height // 20)
    inside_after = region_mean(jpg, centre)
    inside_before = region_mean(reference, centre)
    outside_after = region_mean(jpg, corner)
    print(
        f"  centre before / after mask: {[round(v, 1) for v in inside_before]} / "
        f"{[round(v, 1) for v in inside_after]}"
    )
    print(f"  corner after mask: {[round(v, 1) for v in outside_after]} (fill {FILL_RGB})")
    check("inside the mask the picture is unchanged", close_to(inside_after, inside_before, 3.0))
    check("outside the mask the new background shows", close_to(outside_after, FILL_RGB, 6.0))
    check("TIFF from Lightroom unchanged on disk", sha256(tiff) == tiff_hash)
    check("test photo unchanged on disk", sha256(photo) == photo_hash)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photo", type=Path, required=True)
    parser.add_argument("--tiff", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    try:
        run(settings, args.photo, args.tiff)
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}", file=sys.stderr)
        print(f"details: {exc.details}", file=sys.stderr)
        return 2
    print("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
