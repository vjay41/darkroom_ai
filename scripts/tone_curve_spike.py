"""Tone-curve spike: can the SDK read and write ToneCurvePV2012 (and the colour curves) on a copy?

Only photos under lightroom.test_photos_dir are accepted. A bare virtual copy is created; a gentle
curve is applied, read back, and the original value is put back. The master is never touched.

Usage:  poetry run python scripts/tone_curve_spike.py --photo PATH
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

failures: list[str] = []
KEYS = ["ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue"]
GENTLE_S = [0, 0, 64, 58, 192, 198, 255, 255]
RED_LIFT = [0, 0, 128, 134, 255, 255]


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def show(label: str, data: object) -> None:
    print(f"  {label}: {json.dumps(data, default=str)[:400]}")


def numbers(value: object) -> list[float] | None:
    if isinstance(value, list) and all(isinstance(v, int | float) for v in value):
        return [float(v) for v in value]
    return None


def run(args: argparse.Namespace) -> None:
    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    context = build_tool_context(settings)
    lightroom = context.lightroom
    master = lightroom.find_photo(str(args.photo))
    if master is None:
        raise SystemExit(f"{args.photo} is not in the open catalog.")
    master_start = lightroom.get_develop_settings(master.id, KEYS)
    copy = lightroom.create_virtual_copy(master.id, f"tone curve spike {time.strftime('%H%M%S')}")
    print(f"photo {master.file_name}, copy {copy.id}")

    print("1. read the curves of the new copy")
    start = lightroom.get_develop_settings(copy.id, KEYS)
    show("start", start)
    check("main curve is a list of numbers", numbers(start.get("ToneCurvePV2012")) is not None)

    print("2. apply a gentle S-curve to the main curve")
    change = lightroom.apply_develop_settings(copy.id, {"ToneCurvePV2012": GENTLE_S}, "spike curve")
    show("after", change.after)
    now = numbers(change.after.get("ToneCurvePV2012"))
    check("the main curve reads back as written", now == [float(v) for v in GENTLE_S])
    fresh = lightroom.get_develop_settings(copy.id, ["ToneCurvePV2012"])
    check("a fresh read agrees", numbers(fresh.get("ToneCurvePV2012")) == now)

    print("3. apply a red-channel curve")
    red = lightroom.apply_develop_settings(copy.id, {"ToneCurvePV2012Red": RED_LIFT}, "spike red")
    check(
        "the red curve reads back as written",
        numbers(red.after.get("ToneCurvePV2012Red")) == [float(v) for v in RED_LIFT],
    )

    print("4. put the original curves back")
    restore = {key: start[key] for key in KEYS if key in start}
    lightroom.apply_develop_settings(copy.id, restore, "spike restore")
    end = lightroom.get_develop_settings(copy.id, KEYS)
    show("end", end)
    check("the copy is back at its starting curves", end == start)

    print("5. the tools: get_tone_curve, adjust_tone_curve (within limits and beyond), undo")
    tools = ToolRegistry(context)
    master_arg = {"photo_id": master.id}
    s_curve = [{"x": 0, "y": 0}, {"x": 64, "y": 52}, {"x": 192, "y": 204}, {"x": 255, "y": 255}]
    done = tools.call("adjust_tone_curve", master_arg | {"points": s_curve})
    working = done["photo_id"]
    show("applied", done["applied"])
    check("working copy used, not the master", working != master.id)
    check("applied as asked, not limited", done["clamped"] is False)
    check("measured before and after", bool(done["measured"]))
    read = tools.call("get_tone_curve", {"photo_id": working})["points"]
    check("get_tone_curve agrees", read == done["applied"])
    big = [{"x": 0, "y": 0}, {"x": 128, "y": 250}, {"x": 255, "y": 255}]
    limited = tools.call("adjust_tone_curve", {"photo_id": working, "points": big})
    show("limited", limited["applied"])
    check("a big move is limited and reported", limited["clamped"] and "Limited" in limited["note"])
    green = [{"x": 0, "y": 0}, {"x": 128, "y": 136}, {"x": 255, "y": 255}]
    tools.call("adjust_tone_curve", {"photo_id": working, "channel": "green", "points": green})
    undone = tools.call("undo", {"photo_id": working, "scope": "all"})
    check("undo took the three steps back", undone["undone_steps"] == 3)
    after_undo = lightroom.get_develop_settings(working, KEYS)
    check("the copy is back at its starting curves", after_undo == start)

    print("6. the master is untouched")
    check(
        "master curves unchanged", lightroom.get_develop_settings(master.id, KEYS) == master_start
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photo", type=Path, required=True)
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
