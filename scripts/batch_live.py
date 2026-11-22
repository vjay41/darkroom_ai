"""Batch live run: a develop batch and a library batch over test photos, then undo_batch.

Only photos under lightroom.test_photos_dir are accepted (give two or three, a RAW among them to
cover RAW). Steps: a dry run (nothing created), a develop batch that is refused without a
go-ahead, the same batch with a go-ahead given by THIS script (the test stands in for the person),
a library batch on the new working copies, then undo_batch for both. Masters and their files must
be unchanged at the end. Working copies the batch creates stay in the test catalog (deleting is
never done), and so does the keyword created by the run.

Usage:  poetry run python scripts/batch_live.py --photo PATH [--photo PATH ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

failures: list[str] = []
KEYWORD = "darkroom-ai-batch-spike"
WATCH = ["Exposure2012", "Contrast2012", "Clarity2012", "Temperature", "Tint"]


class Recorder:
    """Stands in for the person in this test: approves, and remembers what it was asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, Mapping[str, Any]]] = []
        self.answer = True

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        self.asked.append((action, details))
        print(f"  go-ahead asked ({'yes' if self.answer else 'no'}): {action}")
        return self.answer


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def show(label: str, data: Any) -> None:
    print(f"  {label}: {json.dumps(data, default=str)[:900]}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> None:
    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    for path in args.photo:
        if root is None or not path.resolve().is_relative_to(root.resolve()):
            raise SystemExit(f"Refusing: {path} is not under the test-photo folder ({root}).")
    hashes = {path: sha256(path) for path in args.photo}

    approver = Recorder()
    context = build_tool_context(settings, confirmer=approver)
    tools = ToolRegistry(context)
    lightroom = context.lightroom

    masters = []
    for path in args.photo:
        found = lightroom.find_photo(str(path))
        if found is None:
            raise SystemExit(f"NOT_IN_CATALOG: {path}")
        masters.append(found)
    print("photos:", [(m.file_name, m.file_format) for m in masters])

    def values(photo_id: str) -> dict[str, Any]:
        return tools.call("get_develop_parameters", {"photo_id": photo_id, "names": WATCH})[
            "values"
        ]

    def master_values(photo_id: str) -> dict[str, Any]:
        """The master photo's own values, read straight from Lightroom (never its copy)."""
        got = lightroom.get_develop_settings(photo_id, WATCH)
        return {key: got.get(key) for key in WATCH}

    before = {m.id: values(m.id) for m in masters}  # what the batch edits, before it does
    masters_before = {m.id: master_values(m.id) for m in masters}
    total_before = lightroom.list_catalog(0, 1, include_virtual_copies=True).total

    def item(m: Any) -> dict[str, Any]:
        change: dict[str, Any] = {"photo_id": m.id, "exposure": 0.2, "contrast": 5}
        change["parameters"] = {"Clarity2012": 8}
        if str(m.file_format).upper() == "RAW":
            change["temperature"] = 150  # Kelvin on RAW
        return change

    items = [item(m) for m in masters]

    print("1. dry run: the plan, and nothing is created")
    plan = tools.call("batch_set_develop_parameters", {"items": items, "dry_run": True})
    show(
        "plan", [(i["file_name"], i["status"], i["creates_copy"], i["plan"]) for i in plan["items"]]
    )
    check("every item is plannable", all(i["status"] == "planned" for i in plan["items"]))
    check(
        "the catalog did not grow",
        lightroom.list_catalog(0, 1, include_virtual_copies=True).total == total_before,
    )

    print("2. the develop batch is refused when the person says no")
    approver.answer = False
    try:
        tools.call("batch_set_develop_parameters", {"items": items})
        check("refused without a go-ahead", False)
    except DarkroomError as exc:
        print(f"  refused with {exc.code.value}")
        check("refused without a go-ahead", exc.code is ErrorCode.CONFIRMATION_REQUIRED)
    approver.answer = True
    check(
        "the catalog did not grow",
        lightroom.list_catalog(0, 1, include_virtual_copies=True).total == total_before,
    )

    print("3. the develop batch with the go-ahead")
    done = tools.call("batch_set_develop_parameters", {"items": items})
    show(
        "summary", {k: done[k] for k in ("ok", "failed", "not_run", "copies_created", "elapsed_s")}
    )
    for result in done["items"]:
        show(result["file_name"], [(s["tool"], s["clamped"]) for s in result["steps"]])
    check("all items ok", done["ok"] == len(items) and done["failed"] == 0)
    check("the go-ahead was asked once for this batch", len(approver.asked) == 2)
    copies = [i["working_copy"] for i in done["items"]]
    check("every working copy is a different photo from its master", set(copies).isdisjoint(before))
    for master, copy in zip(masters, copies, strict=True):
        now = values(copy)
        check(
            f"{master.file_name}: exposure moved by 0.2",
            abs(now["Exposure2012"] - before[master.id]["Exposure2012"] - 0.2) < 0.06,
        )
        check(
            f"{master.file_name}: clarity moved",
            now["Clarity2012"] != before[master.id]["Clarity2012"],
        )
        check(
            f"{master.file_name}: master unchanged",
            master_values(master.id) == masters_before[master.id],
        )

    print("4. a library batch on the working copies (no go-ahead needed for a small batch)")
    asked_before = len(approver.asked)
    lib = tools.call(
        "batch_set_library_state",
        {
            "items": [
                {"photo_id": copies[0], "rating": 4, "flag": "pick", "add_keywords": [KEYWORD]},
                *[{"photo_id": c, "rating": 2} for c in copies[1:]],
            ]
        },
    )
    show("summary", {k: lib[k] for k in ("ok", "failed", "not_run", "elapsed_s")})
    check(
        "all library items ok and verified",
        lib["ok"] == len(copies) and all(i["verified"] for i in lib["items"]),
    )
    check("no go-ahead was needed", len(approver.asked) == asked_before)
    state = tools.call("get_library_state", {"photo_id": copies[0]})
    check(
        "first copy: rating 4, pick, keyword",
        (state["rating"], state["flag"], KEYWORD in state["keywords"]) == (4, "pick", True),
    )

    print("5. a master and its own working copy in one library batch are refused, nothing runs")
    try:
        tools.call(
            "batch_set_library_state",
            {
                "items": [
                    {"photo_id": copies[0], "rating": 1},
                    {"photo_id": masters[0].id, "rating": 1},
                ]
            },
        )
        check("refused: two items for one working copy", False)
    except DarkroomError as exc:
        check("refused: two items for one working copy", exc.code is ErrorCode.GUARDRAIL_REJECTED)
    state = tools.call("get_library_state", {"photo_id": copies[0]})
    check("nothing from the refused batch was written", state["rating"] == 4)

    print("6. undo_batch: library batch first, then the develop batch")
    undo_lib = tools.call("undo_batch", {"batch_id": lib["batch_id"]})
    show(
        "library undo",
        {
            "steps": undo_lib["undone_steps"],
            "verified": [p["verified"] for p in undo_lib["photos"]],
        },
    )
    check(
        "library undo verified",
        undo_lib["undone_steps"] >= len(copies) and all(p["verified"] for p in undo_lib["photos"]),
    )
    state = tools.call("get_library_state", {"photo_id": copies[0]})
    check(
        "first copy is back to unrated, unflagged, no keyword",
        (state["rating"], state["flag"], KEYWORD in state["keywords"]) == (None, "none", False),
    )
    undo_dev = tools.call("undo_batch", {"batch_id": done["batch_id"]})
    show(
        "develop undo",
        {
            "steps": undo_dev["undone_steps"],
            "verified": [p["verified"] for p in undo_dev["photos"]],
            "note": undo_dev["note"],
        },
    )
    check(
        "develop undo verified",
        undo_dev["undone_steps"] >= len(copies) and all(p["verified"] for p in undo_dev["photos"]),
    )
    for master, copy in zip(masters, copies, strict=True):
        check(
            f"{master.file_name}: copy back at its values from before the batch",
            values(copy) == before[master.id],
        )
    again = tools.call("undo_batch", {"batch_id": done["batch_id"]})
    check("a second undo has nothing left", again["undone_steps"] == 0)

    print("7. masters and files are untouched")
    for master, path in zip(masters, args.photo, strict=True):
        check(
            f"{master.file_name}: master develop values unchanged",
            master_values(master.id) == masters_before[master.id],
        )
        check(f"{master.file_name}: file unchanged on disk", sha256(path) == hashes[path])
    grew = lightroom.list_catalog(0, 1, include_virtual_copies=True).total - total_before
    print(f"  INFO catalog grew by {grew} virtual copies (kept; deleting is never done)")
    print(f"  journal entries in total: {len(list(context.journal.entries()))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photo", type=Path, action="append", required=True)
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
