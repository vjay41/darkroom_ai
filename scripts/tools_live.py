"""Phase 5 live run: each tool once, on a virtual copy of ONE test photo, then undo everything.

Only photos under lightroom.test_photos_dir are accepted. The edits go to a virtual copy; the last
step takes all of them back and checks that the copy is at its starting values, that the master
photo's settings never changed, and that the original file is byte-identical.

Usage:  poetry run python scripts/tools_live.py --photo PATH [--preset NAME] [--background NAME]
        [--skip-photoshop]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

failures: list[str] = []
WATCHED = (
    "Exposure2012",
    "Shadows2012",
    "IncrementalTemperature",
    "SaturationAdjustmentOrange",
    "CropLeft",
    "CropRight",
    "CropTop",
    "CropBottom",
    "CropAngle",
)
OUTSIDE = "..\\outside.jpg"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def show(label: str, data: Any) -> None:
    print(f"  {label}: {json.dumps(data, default=str)[:600]}")


def change(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in result["changes"] if c["name"] == name)


def refused(tools: ToolRegistry, name: str, arguments: dict[str, Any]) -> bool:
    """True if the call is refused by a guardrail."""
    try:
        tools.call(name, arguments)
    except DarkroomError as exc:
        return exc.code is ErrorCode.GUARDRAIL_REJECTED
    return False


def run(args: argparse.Namespace) -> None:
    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    photo_hash = sha256(args.photo)

    context = build_tool_context(settings)
    tools = ToolRegistry(context)
    lightroom = context.lightroom
    master = lightroom.find_photo(str(args.photo))
    if master is None:
        raise SystemExit(f"{args.photo} is not in the open catalog.")
    photo = {"photo_id": master.id}
    print(f"tools: {', '.join(tools.names)}")
    print(f"photo: {master.file_name} ({master.file_format}), master id {master.id}")

    print("1. get_photo_info")
    info = tools.call("get_photo_info", photo)
    show("settings", info["settings"])
    start = lightroom.get_develop_settings(master.id, list(WATCHED))
    show("starting values", start)

    print("2. analyze_photo")
    analysis = tools.call("analyze_photo", photo)["analysis"]
    show("tones", {k: v for k, v in analysis["tones"].items() if k != "histogram"})
    show("clipping", analysis["clipping"])
    show("colour cast", analysis["color_cast"])
    mean_before = analysis["tones"]["mean"]

    print("3. adjust_lighting (+0.4 EV, shadows +20)")
    edit = tools.call("adjust_lighting", photo | {"exposure": 0.4, "shadows": 20})
    copy_id = edit["photo_id"]
    print(f"  working copy: {copy_id} (created: {edit['working_copy_created']})")
    check("edit went to a virtual copy", copy_id != master.id)
    check("exposure applied as asked", change(edit, "exposure")["applied_change"] == 0.4)
    mean_after = tools.call("analyze_photo", photo)["analysis"]["tones"]["mean"]
    print(f"  mean luma before / after: {mean_before} / {mean_after}")
    check("the preview got brighter", mean_after > mean_before + 0.005)

    print("   adjust_lighting again, asking for +2.5 EV (must be clamped)")
    exposure = change(tools.call("adjust_lighting", photo | {"exposure": 2.5}), "exposure")
    show("exposure", exposure)
    check("per-pass clamp reported", exposure["limited_by"] == ["per_pass"])
    asked, applied = exposure["requested_change"], exposure["applied_change"]
    check("requested 2.5, applied 1.5", asked == 2.5 and applied == 1.5)

    print("4. adjust_white_balance (temperature +15)")
    warmer = tools.call("adjust_white_balance", photo | {"temperature": 15})
    temperature = change(warmer, "temperature")
    show("temperature", temperature)
    check("JPG uses the relative slider", temperature["key"] == "IncrementalTemperature")

    print("5. adjust_hsl (orange saturation -10)")
    orange = {"color": "orange", "saturation": -10}
    hsl = tools.call("adjust_hsl", photo | {"adjustments": [orange]})
    show("orange", hsl["changes"][0])
    check("HSL applied", hsl["changes"][0]["applied_change"] == -10)

    print("6. crop (5% off each side)")
    box = {"left": 0.05, "top": 0.05, "right": 0.95, "bottom": 0.95}
    show("crop after", tools.call("crop", photo | box)["after"])
    now = lightroom.get_develop_settings(copy_id, ["CropLeft", "CropRight"])
    left, right = float(now["CropLeft"]), float(now["CropRight"])
    check("crop values read back", abs(left - 0.05) < 1e-3 and abs(right - 0.95) < 1e-3)
    tiny = {"left": 0.4, "top": 0.4, "right": 0.6, "bottom": 0.6}
    check("a crop below the minimum is refused", refused(tools, "crop", photo | tiny))

    print("7. apply_preset")
    print(f"  {len(lightroom.list_presets())} develop presets known to Lightroom")
    if args.preset:
        preset = tools.call("apply_preset", photo | {"preset_name": args.preset})
        print(f"  applied '{preset['preset']}', {preset['changed_count']} settings changed")
        check("preset applied to the working copy", preset["photo_id"] == copy_id)
    else:
        print("  skipped: no --preset given")

    if args.skip_photoshop:
        print("8. replace_background: skipped (--skip-photoshop)")
    else:
        print("8. replace_background (solid colour)")
        background = tools.call("replace_background", photo | {"color": [30, 60, 120]})
        for name in ("psd_path", "jpg_path", "mask_path", "mask_provider", "seconds"):
            print(f"  {name}: {background[name]}")
        show("mask", background["mask"])
        written = all(Path(background[k]).is_file() for k in ("psd_path", "jpg_path"))
        check("PSD and JPG written", written)
        if args.background:
            print("   replace_background (picture from the backgrounds folder)")
            picture = tools.call("replace_background", photo | {"image": args.background})
            check("picture background written", Path(picture["jpg_path"]).is_file())
        check(
            "a path outside the backgrounds folder is refused",
            refused(tools, "replace_background", photo | {"image": OUTSIDE}),
        )

    print("9. undo (all)")
    undone = tools.call("undo", photo | {"scope": "all"})
    print(f"  undone steps: {undone['undone_steps']}")
    end = lightroom.get_develop_settings(copy_id, list(WATCHED))
    show("values after undo", end)
    mismatched = {
        key: (end[key], start[key])
        for key in WATCHED
        if abs(float(end[key]) - float(start[key])) > 1e-3
    }
    check("the copy is back at its starting values", not mismatched)
    master_now = lightroom.get_develop_settings(master.id, list(WATCHED))
    check("the master photo's settings never changed", master_now == start)
    check("original file unchanged on disk", sha256(args.photo) == photo_hash)
    print(f"  journal entries in total: {len(list(context.journal.entries()))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photo", type=Path, required=True)
    parser.add_argument("--preset", default=None, help="exact name of a develop preset to apply")
    parser.add_argument("--background", default=None, help="file name in the backgrounds folder")
    parser.add_argument("--skip-photoshop", action="store_true")
    parser.add_argument("--config", type=Path, default=None)
    try:
        run(parser.parse_args())
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}", file=sys.stderr)
        print(f"details: {exc.details}", file=sys.stderr)
        return 2
    print("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
