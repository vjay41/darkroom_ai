"""RAW live run: develop writes, Kelvin white balance, lens toggles, library fields and undo on a
virtual copy of ONE RAW test photo.

Only photos under lightroom.test_photos_dir are accepted. The master and the file on disk must be
unchanged at the end, and every change must be taken back by undo. Prints what Lightroom reports
for the lens profile (it is only named when Lightroom finds one for the lens).

Usage:  poetry run python scripts/raw_live.py --photo PATH
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

failures: list[str] = []
KEYWORD = "darkroom-ai-raw-spike"
NAMES = [
    "ProcessVersion",
    "Exposure2012",
    "Temperature",
    "Tint",
    "Highlights2012",
    "Clarity2012",
    "LensProfileEnable",
    "LensProfileName",
    "AutoLateralCA",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def show(label: str, data: Any) -> None:
    print(f"  {label}: {json.dumps(data, default=str)[:700]}")


def run(args: argparse.Namespace) -> None:
    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    file_hash = sha256(args.photo)

    context = build_tool_context(settings)
    tools = ToolRegistry(context)
    lightroom = context.lightroom
    master = lightroom.find_photo(str(args.photo))
    if master is None:
        raise SystemExit(f"NOT_IN_CATALOG: {args.photo} is not in the open catalog.")
    print(f"photo: {master.file_name} ({master.file_format}), master id {master.id}")
    check("the file is a RAW format", str(master.file_format).upper() in {"RAW", "DNG"})

    print("1. master state before (must be identical at the end)")
    master_before = tools.call("get_develop_parameters", {"photo_id": master.id, "names": NAMES})
    show("master", master_before["values"])
    meta = tools.call("get_photo_metadata", {"photo_id": master.id})["metadata"]
    keys = ("camera_make", "camera_model", "lens", "focal_length", "iso", "file_format")
    show("metadata", {k: meta.get(k) for k in keys})
    show("lookup_errors", meta.get("lookup_errors"))

    print("2. virtual copy")
    copy = lightroom.create_virtual_copy(master.id, f"raw spike {time.strftime('%H%M%S')}")
    photo = {"photo_id": copy.id}
    start = tools.call("get_develop_parameters", photo | {"names": NAMES})["values"]
    show("copy starts at", start)

    def read() -> dict[str, Any]:
        return tools.call("get_develop_parameters", photo | {"names": NAMES})["values"]

    print("3. exposure +0.3 EV")
    show("result", tools.call("adjust_lighting", photo | {"exposure": 0.3}))
    now = read()
    base = start["Exposure2012"] or 0.0
    check("exposure moved by about +0.3", abs((now["Exposure2012"] or 0.0) - (base + 0.3)) < 0.06)

    print("4. white balance: temperature +200 K (RAW uses Kelvin)")
    wb = tools.call("adjust_white_balance", photo | {"temperature": 200})
    show("result", wb)
    now2 = read()
    check("temperature changed", now2["Temperature"] != start["Temperature"])
    show("temperature", {"before": start["Temperature"], "after": now2["Temperature"]})

    print("5. highlights through adjust_lighting, a plain slider through the generic tool")
    tools.call("adjust_lighting", photo | {"highlights": -10})
    check("highlights moved", read()["Highlights2012"] != start["Highlights2012"])
    tools.call("set_develop_parameters", photo | {"changes": {"Clarity2012": 10}})
    check("clarity moved", read()["Clarity2012"] != start["Clarity2012"])

    print("6. lens profile and chromatic aberration")
    tools.call("set_develop_parameters", photo | {"changes": {"LensProfileEnable": 1}})
    tools.call("set_develop_parameters", photo | {"changes": {"AutoLateralCA": 1}})
    lens = read()
    show("lens", {k: lens[k] for k in ("LensProfileEnable", "LensProfileName", "AutoLateralCA")})
    check("lens profile switch is on", lens["LensProfileEnable"] == 1)
    check("auto lateral CA is on", lens["AutoLateralCA"] == 1)
    print(f"  INFO lens profile resolved by Lightroom: {lens['LensProfileName']!r}")

    print("7. library fields on the RAW copy")
    lib = tools.call(
        "set_library_state", photo | {"rating": 3, "flag": "pick", "add_keywords": [KEYWORD]}
    )
    check("library write verified by read-back", lib["verified"] is True)
    tools.call("undo_library_change", photo | {"scope": "all"})

    print("8. tone curve is readable")
    curve = tools.call("get_tone_curve", photo)
    show("curve keys", sorted(curve)[:8])

    print("9. undo everything")
    undone = tools.call("undo", photo | {"scope": "all"})
    show("undone", {"steps": undone["undone_steps"]})
    end = read()
    for name in (
        "Exposure2012",
        "Temperature",
        "Highlights2012",
        "Clarity2012",
        "LensProfileEnable",
    ):
        check(f"{name} back to its start", end[name] == start[name])

    print("10. the master and its file are untouched")
    master_after = tools.call("get_develop_parameters", {"photo_id": master.id, "names": NAMES})
    check("master develop settings unchanged", master_after["values"] == master_before["values"])
    check("original file unchanged on disk", sha256(args.photo) == file_hash)
    print(f"  journal entries in total: {len(list(context.journal.entries()))}")


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
