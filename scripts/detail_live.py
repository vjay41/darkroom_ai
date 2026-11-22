"""Live pilot of noise reduction and sharpening on one high-ISO photo of the test catalog.

Measures noise and edge strength on real preview renders, applies small passes (colour noise,
luminance noise, sharpening, then a local noise reduction in a shadow mask), re-measures after each,
then takes everything back and checks the develop settings are exactly as before. Writes go to a
virtual copy only; the master is checked unchanged.

Usage:  poetry run python scripts/detail_live.py
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

failures: list[str] = []
NOISE_FLOOR = 0.3  # the noise estimate stops falling about here (8-bit previews)
KEYS = [
    "ColorNoiseReduction",
    "LuminanceSmoothing",
    "Sharpness",
    "SharpenEdgeMasking",
]


class Approver:
    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        print(f"  go-ahead asked (yes): {action}")
        return True


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def run(args: argparse.Namespace) -> None:
    settings = load_config(args.config)
    context = build_tool_context(settings, confirmer=Approver())
    tools = ToolRegistry(context)
    lr = context.lightroom

    print("1. pick a high-ISO photo (read-only)")
    page = lr.list_catalog(0, 30, min_iso=args.min_iso, include_virtual_copies=False)
    wanted = "RAW" if args.raw else "JPG"
    jpgs = [p for p in page.photos if str(p.file_format).upper() == wanted]
    check("found a photo at or above the ISO", bool(page.photos))
    chosen = (jpgs or list(page.photos))[0]
    master = chosen.id
    print(f"  {chosen.file_name}, ISO {chosen.iso}, {chosen.file_format}")
    master_before = lr.get_develop_settings(master, KEYS)

    def numbers(label: str) -> dict[str, Any]:
        got = tools.call("analyze_photo", {"photo_id": master})
        detail = got["analysis"]["detail"]
        print(
            f"  {label}: noise_luma {detail['noise_luma']}, noise_chroma {detail['noise_chroma']}, "
            f"edge_strength {detail['edge_strength']}, flat {detail['flat_fraction']}"
        )
        return dict(detail)

    base = numbers("baseline")
    check("noise and edge numbers exist", base["noise_luma"] is not None)

    print("2. colour noise, then luminance noise (small passes)")
    first = tools.call(
        "set_develop_parameters", {"photo_id": master, "changes": {"ColorNoiseReduction": 20}}
    )
    copy = str(first["photo_id"])
    check("edit landed on a working copy", copy != master)
    after_colour = numbers("after colour noise")
    tools.call("set_develop_parameters", {"photo_id": copy, "changes": {"LuminanceSmoothing": 25}})
    after_luma = numbers("after luminance noise")
    check("luminance noise went down", after_luma["noise_luma"] < base["noise_luma"])
    check(
        "colour noise did not get worse",
        after_luma["noise_chroma"] <= base["noise_chroma"] * 1.02,
    )
    print(
        f"  INFO colour pass alone: chroma {base['noise_chroma']} -> {after_colour['noise_chroma']}"
    )

    print("3. sharpening with edge masking")
    tools.call(
        "set_develop_parameters",
        {"photo_id": copy, "changes": {"Sharpness": 20, "SharpenEdgeMasking": 40}},
    )
    sharp = numbers("after sharpening")
    check(
        "edge strength did not drop", sharp["edge_strength"] >= after_luma["edge_strength"] * 0.98
    )
    print(f"  INFO edges {after_luma['edge_strength']} -> {sharp['edge_strength']}")

    print("4. local noise reduction in a mask (full tonal range, so the effect is measurable)")
    mask = tools.call(
        "create_mask",
        {
            "photo_id": copy,
            "mask_type": "rangeMask",
            "subtype": "luminance",
            "luminance_range": [0, 0, 1, 1],
        },
    )
    mask_id = mask.get("mask_id")
    check("mask created", bool(mask_id) or bool(mask))
    tools.call(
        "set_mask_adjustments",
        {"photo_id": copy, "mask_id": mask_id, "changes": {"local_LuminanceNoise": 25}},
    )
    # Lightroom re-renders mask edits a few seconds late; measure after it settles.
    time.sleep(6)
    local = numbers("after local noise")
    print(f"  INFO noise_luma {sharp['noise_luma']} -> {local['noise_luma']}")
    listed = tools.call("list_masks", {"photo_id": copy})
    stored = listed["masks"][0]["local_adjustments"].get("LocalLuminanceNoise")
    check("local noise value is stored on the mask (read-back)", stored is not None and stored > 0)
    if sharp["noise_luma"] <= NOISE_FLOOR:
        # The estimate cannot go much lower than this on a clean render, so a further drop would
        # be invisible; say so instead of failing or claiming success.
        print(f"  [SKIP] noise is already at the measurement floor ({NOISE_FLOOR}); the local")
        print("         effect cannot be measured here; the mask value is checked by read-back")
    else:
        check("local noise reduction lowered the noise", local["noise_luma"] < sharp["noise_luma"])

    print("5. take everything back")
    undone = tools.call("undo", {"photo_id": copy, "scope": "all"})
    print(f"  undone steps: {undone['undone_steps']}")
    restored = lr.get_develop_settings(copy, KEYS)
    check(
        "develop values are back to the original",
        all(abs(float(restored[k]) - float(master_before[k])) < 1e-6 for k in KEYS),
    )
    check(
        "master develop values never changed",
        lr.get_develop_settings(master, KEYS) == master_before,
    )
    final = numbers("after undo")
    check(
        "noise is back to the baseline",
        abs(final["noise_luma"] - base["noise_luma"]) <= 0.15 * base["noise_luma"] + 0.05,
    )
    print("  INFO the working copy stays in the catalog (deleting is never done)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--min-iso", type=int, default=3200)
    parser.add_argument("--raw", action="store_true", help="use a RAW photo instead of a JPG")
    try:
        run(parser.parse_args())
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}", file=sys.stderr)
        return 2
    print("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
