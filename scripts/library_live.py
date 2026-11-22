"""Library live run: rating, flag and keywords on a virtual copy of ONE test photo, then undo.

Only photos under lightroom.test_photos_dir are accepted. A bare virtual copy is created (no
develop edits); everything is set, read back, and taken back. Checks that the master photo's
library fields never changed, that a master is refused by the plug-in itself, and that the
original file is byte-identical. A keyword created by the run stays in the catalog's keyword list
(the SDK cannot delete keywords); it is named so it can be found and removed by hand.

Usage:  poetry run python scripts/library_live.py --photo PATH
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
KEYWORD = "darkroom-ai-library-spike"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def show(label: str, data: Any) -> None:
    print(f"  {label}: {json.dumps(data, default=str)[:600]}")


def fields(state: dict[str, Any]) -> tuple[Any, Any, Any]:
    return state["rating"], state["flag"], tuple(state["keywords"])


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
    print(f"photo: {master.file_name} ({master.file_format}), master id {master.id}")

    print("1. read the master's library state (it must not change during this run)")
    master_start = fields(tools.call("get_library_state", {"photo_id": master.id}))
    show("master", master_start)

    print("2. the plug-in itself refuses a library write to a master")
    try:
        lightroom.set_library_state(master.id, rating=1)
        check("master refused by the plug-in", False)
    except DarkroomError as exc:
        show("refusal", {"code": exc.code.value, "plugin_code": exc.details.get("plugin_code")})
        check(
            "master refused by the plug-in", exc.details.get("plugin_code") == "NOT_A_VIRTUAL_COPY"
        )

    print("3. a bare virtual copy (no develop edits)")
    copy = lightroom.create_virtual_copy(master.id, f"library spike {time.strftime('%H%M%S')}")
    copy_id = copy.id
    print(f"  copy: {copy_id}")
    photo = {"photo_id": copy_id}
    start = fields(tools.call("get_library_state", photo))
    show("copy starts at", start)

    print("4. set rating 4, flag pick, add one keyword")
    result = tools.call(
        "set_library_state", photo | {"rating": 4, "flag": "pick", "add_keywords": [KEYWORD]}
    )
    show("after", result["after"])
    check("verified by read-back", result["verified"] is True)
    check("rating 4", result["after"]["rating"] == 4)
    check("flag pick", result["after"]["flag"] == "pick")
    check("keyword added", result["after"]["keywords"] == [*start[2], KEYWORD])
    again = fields(tools.call("get_library_state", photo))
    check("a fresh read agrees", again == (4, "pick", tuple(sorted([*start[2], KEYWORD]))))

    print("5. the same keyword in other letters is reused, not added twice")
    twice = tools.call("set_library_state", photo | {"add_keywords": [KEYWORD.upper()]})
    show("keywords", twice["after"]["keywords"])
    check("still one keyword of that name", len(twice["after"]["keywords"]) == len(again[2]))

    print("6. reject, rating 2, remove the keyword")
    second = tools.call(
        "set_library_state", photo | {"rating": 2, "flag": "reject", "remove_keywords": [KEYWORD]}
    )
    show("after", second["after"])
    check("verified by read-back", second["verified"] is True)
    check("rating 2, rejected, keyword gone", fields(second["after"]) == (2, "reject", start[2]))

    print("7. rating 0 clears the rating, flag none clears the flag")
    cleared = tools.call("set_library_state", photo | {"rating": 0, "flag": "none"})
    check("unrated and unflagged", fields(cleared["after"])[:2] == (None, "none"))

    print("8. set again, then undo everything")
    tools.call(
        "set_library_state", photo | {"rating": 5, "flag": "pick", "add_keywords": [KEYWORD]}
    )
    undone = tools.call("undo_library_change", photo | {"scope": "all"})
    show("undone", {"steps": undone["undone_steps"], "after": undone["after"]})
    check("the copy is back at its starting values", fields(undone["after"]) == start)
    check("fresh read agrees", fields(tools.call("get_library_state", photo)) == start)

    print("9. the master and its file are untouched")
    master_end = fields(tools.call("get_library_state", {"photo_id": master.id}))
    check("master library fields unchanged", master_end == master_start)
    check("original file unchanged on disk", sha256(args.photo) == photo_hash)
    print(f"  journal entries in total: {len(list(context.journal.entries()))}")
    print(
        f"  note: the keyword '{KEYWORD}' stays in the keyword list (remove it by hand if wanted)"
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
