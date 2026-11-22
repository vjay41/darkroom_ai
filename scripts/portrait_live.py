"""Live pilot of skin-tone correction and an S-curve on one photo of the TEST catalog.

Step A (find a portrait):   poetry run python scripts/portrait_live.py --list 12
    exports small previews of the first photos to outputs/portrait_candidates/ and prints their ids.
Step B (run the pilot):     poetry run python scripts/portrait_live.py --photo-id ID \
        --skin x0,y0,x1,y1 --neutral x0,y0,x1,y1
    boxes are fractions of the picture (y from the top). The pilot measures skin and a neutral patch
    on real renders, applies small passes (white balance, HSL, a local mask, then an S-curve),
    re-measures after each, takes everything back with undo and checks the develop settings are
    exactly as before. Writes go to a virtual copy only; the master is checked unchanged.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from darkroom_ai.config import load_config
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

failures: list[str] = []
KEYS = [
    "IncrementalTemperature",
    "IncrementalTint",
    "Temperature",
    "Tint",
    "WhiteBalance",
    "SaturationAdjustmentOrange",
    "LuminanceAdjustmentOrange",
    "HueAdjustmentOrange",
    "ToneCurvePV2012",
]
RENDER_WAIT_S = 6


class Approver:
    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        print(f"  go-ahead asked (yes): {action}")
        return True


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def box(text: str) -> list[float]:
    values = [float(v) for v in text.split(",")]
    if len(values) != 4:
        raise SystemExit(f"a box is x0,y0,x1,y1 (got {text!r})")
    return values


def list_candidates(args: argparse.Namespace) -> None:
    context = build_tool_context(load_config(args.config), confirmer=Approver())
    lr = context.lightroom
    out = Path("outputs/portrait_candidates")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    page = lr.list_catalog(0, args.list, include_virtual_copies=False)
    for photo in page.photos:
        try:
            preview = lr.export_preview(photo.id, f"cand-{photo.id[:8]}", 384)
            target = out / f"{photo.id[:8]}_{photo.file_name}.jpg"
            shutil.copy(preview.path, target)
            print(
                f"{photo.id}  {photo.file_name}  {photo.file_format}  ISO {photo.iso}  -> {target}"
            )
        except Exception as exc:  # keep listing the rest
            print(f"{photo.id}  {photo.file_name}  preview failed: {exc}")


def run(args: argparse.Namespace) -> None:
    context = build_tool_context(load_config(args.config), confirmer=Approver())
    tools = ToolRegistry(context)
    lr = context.lightroom
    master = args.photo_id
    skin, neutral = box(args.skin), box(args.neutral) if args.neutral else None
    info = lr.get_photo(master)
    print(f"photo: {info.file_name} ({info.file_format})")
    master_before = lr.get_develop_settings(master, KEYS)

    regions = [{"name": "skin", "box": skin}]
    if neutral:
        regions.append({"name": "neutral", "box": neutral})

    def measure(photo_id: str, label: str) -> dict[str, Any]:
        got = tools.call("measure_region", {"photo_id": photo_id, "regions": regions})
        read = {r["name"]: r for r in got["readings"]}
        text = ", ".join(
            f"{n}: L {r['lightness']} hue {r['hue_deg']} chroma {r['chroma']} rgb {r['rgb']}"
            for n, r in read.items()
        )
        print(f"  {label}: {text}")
        return read

    print("1. baseline on real renders (read-only)")
    base = measure(master, "baseline")
    check("skin patch has measurable colour", base["skin"]["chroma"] > 3)

    print("2. white balance: warmer")
    # JPG: relative -100..100 scale; RAW: a change in Kelvin.
    warm_step = 10 if str(info.file_format).upper() == "JPG" else 400
    first = tools.call("adjust_white_balance", {"photo_id": master, "temperature": warm_step})
    copy = str(first["photo_id"])
    check("edit landed on a working copy", copy != master)
    warm = measure(copy, "after warmer")
    check("skin hue rose (more yellow)", warm["skin"]["hue_deg"] > base["skin"]["hue_deg"] + 0.5)
    if neutral:
        check(
            "neutral patch moved too (it is a global edit)",
            abs(warm["neutral"]["hue_deg"] - base["neutral"]["hue_deg"]) > 0.3
            or abs(warm["neutral"]["chroma"] - base["neutral"]["chroma"]) > 0.3,
        )
    tools.call("undo", {"photo_id": copy, "scope": "last"})
    again = measure(copy, "after undoing the warm pass")
    check(
        "undo of one pass restored the skin colour",
        abs(again["skin"]["hue_deg"] - base["skin"]["hue_deg"]) < 0.8,
    )

    print("3. HSL: orange saturation down, orange luminance up")
    tools.call(
        "adjust_hsl", {"photo_id": copy, "adjustments": [{"color": "orange", "saturation": -15}]}
    )
    desat = measure(copy, "after orange saturation down")
    check("skin chroma dropped", desat["skin"]["chroma"] < base["skin"]["chroma"] - 0.3)
    tools.call(
        "adjust_hsl", {"photo_id": copy, "adjustments": [{"color": "orange", "luminance": 12}]}
    )
    bright = measure(copy, "after orange luminance up")
    check("skin got lighter", bright["skin"]["lightness"] > desat["skin"]["lightness"] + 0.2)

    print("4. local mask on the skin: cooler inside the mask only")
    cx, cy = (skin[0] + skin[2]) / 2, (skin[1] + skin[3]) / 2
    rx, ry = max((skin[2] - skin[0]) * 1.2, 0.04), max((skin[3] - skin[1]) * 1.2, 0.04)
    mask = tools.call(
        "create_mask",
        {
            "photo_id": copy,
            "mask_type": "radialGradient",
            "center": [cx, cy],
            "radius_x": min(rx, 1.0),
            "radius_y": min(ry, 1.0),
            "feather": 20,
        },
    )
    mask_id = mask.get("mask_id")
    check("mask created", bool(mask_id))
    before_mask = measure(copy, "before local change")
    tools.call(
        "set_mask_adjustments",
        {"photo_id": copy, "mask_id": mask_id, "changes": {"local_Temperature": -25}},
    )
    time.sleep(RENDER_WAIT_S)
    local = measure(copy, "after local cooler")
    check(
        "skin hue fell inside the mask",
        local["skin"]["hue_deg"] < before_mask["skin"]["hue_deg"] - 0.3,
    )
    if neutral:
        check(
            "neutral patch outside the mask unchanged",
            abs(local["neutral"]["hue_deg"] - before_mask["neutral"]["hue_deg"]) < 1.0,
        )

    print("5. S-curve on the combined curve")
    curve = tools.call("get_tone_curve", {"photo_id": copy, "channel": "rgb"})
    pts = curve["points"]
    print(f"  current curve: {[(p['x'], p['y']) for p in pts]}")
    tones0 = tools.call("analyze_photo", {"photo_id": copy})["analysis"]["tones"]
    s_curve = [{"x": 0, "y": 0}, {"x": 64, "y": 54}, {"x": 192, "y": 202}, {"x": 255, "y": 255}]
    result = tools.call(
        "adjust_tone_curve", {"photo_id": copy, "channel": "rgb", "points": s_curve}
    )
    print(f"  applied: {[(p['x'], p['y']) for p in result['applied']]}  note: {result['note']}")
    time.sleep(2)
    tones1 = tools.call("analyze_photo", {"photo_id": copy})["analysis"]["tones"]
    spread0, spread1 = tones0["p95"] - tones0["p05"], tones1["p95"] - tones1["p05"]
    print(f"  tone spread p95-p05: {spread0:.4f} -> {spread1:.4f}")
    check("S-curve widened the tone spread", spread1 > spread0 + 0.002)
    flat_line = [{"x": 0, "y": 0}, {"x": 255, "y": 255}]
    back = tools.call(
        "adjust_tone_curve", {"photo_id": copy, "channel": "rgb", "points": flat_line}
    )
    print(f"  back toward a straight line: {[(p['x'], p['y']) for p in back['applied']]}")

    print("6. take everything back")
    undone = tools.call("undo", {"photo_id": copy, "scope": "all"})
    print(f"  undone steps: {undone['undone_steps']}")
    restored = lr.get_develop_settings(copy, KEYS)
    # Lightroom reports some keys (RAW Temperature, Tint) only once they have been touched; a key
    # the master does not report cannot be compared, and the render check below covers it.
    comparable = [k for k in KEYS if master_before.get(k) is not None]
    for key in comparable:
        if restored.get(key) != master_before.get(key):
            print(f"  DIFF {key}: {master_before.get(key)!r} -> {restored.get(key)!r}")
    check(
        "develop values are back to the original",
        all(restored.get(k) == master_before.get(k) for k in comparable),
    )
    check(
        "master develop values never changed",
        lr.get_develop_settings(master, KEYS) == master_before,
    )
    time.sleep(2)
    final = measure(master, "master now")
    check(
        "skin colour is back to the baseline",
        abs(final["skin"]["hue_deg"] - base["skin"]["hue_deg"]) < 0.8
        and abs(final["skin"]["chroma"] - base["skin"]["chroma"]) < 1.0,
    )
    print("  INFO the working copy stays in the catalog (deleting is never done)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--list", type=int, default=0, help="export N candidate previews and stop")
    parser.add_argument("--photo-id")
    parser.add_argument("--skin", help="x0,y0,x1,y1 fractions of a clean skin patch")
    parser.add_argument("--neutral", help="x0,y0,x1,y1 fractions of a neutral patch (optional)")
    args = parser.parse_args()
    if args.list:
        list_candidates(args)
        return 0
    if not args.photo_id or not args.skin:
        parser.error("give --photo-id and --skin (or --list N first)")
    run(args)
    print("ALL CHECKS PASSED" if not failures else f"FAILED: {len(failures)} check(s): {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
