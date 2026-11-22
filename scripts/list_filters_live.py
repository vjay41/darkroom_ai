"""List-filter live run: the plug-in's filtered lists are compared with the same filters worked
out here from the unfiltered list, on the test catalog.

Only photos under lightroom.test_photos_dir are accepted. One bare virtual copy of the given photo
is created and given a rating, a pick flag and a keyword (then taken back), so every filter has
something to find. Nothing else is written.

Usage:  poetry run python scripts/list_filters_live.py --photo PATH
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from darkroom_ai.adapters.lightroom.models import PhotoMetadata
from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

failures: list[str] = []
KEYWORD = "darkroom-ai-filter-spike"
PAGE = 100


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def run(args: argparse.Namespace) -> None:
    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    if root is None or not args.photo.resolve().is_relative_to(root.resolve()):
        raise SystemExit(f"Refusing: {args.photo} is not under the test-photo folder ({root}).")
    context = build_tool_context(settings)
    tools = ToolRegistry(context)
    lightroom = context.lightroom
    master = lightroom.find_photo(str(args.photo))
    if master is None:
        raise SystemExit(f"{args.photo} is not in the open catalog.")

    def listing(**filters: Any) -> list[PhotoMetadata]:
        photos: list[PhotoMetadata] = []
        while True:
            page = lightroom.list_catalog(len(photos), PAGE, include_virtual_copies=True, **filters)
            photos += page.photos
            if not page.returned or len(photos) >= page.total:
                return photos

    print("1. the unfiltered list (the reference)")
    everything = listing()
    print(f"  {len(everything)} photos including virtual copies")
    check("the catalog is not empty", len(everything) > 0)
    check("every photo reports a pick status", all(p.pick_status is not None for p in everything))

    print("2. a virtual copy with rating 4, pick, and one keyword")
    copy = lightroom.create_virtual_copy(master.id, f"filter spike {time.strftime('%H%M%S')}")
    tools.call(
        "set_library_state",
        {"photo_id": copy.id, "rating": 4, "flag": "pick", "add_keywords": [KEYWORD]},
    )
    everything = listing()

    def ids(**filters: Any) -> set[str]:
        return {p.id for p in listing(**filters)}

    print("3. rating and flag filters equal the reference worked out here")
    for low, high in ((4, None), (None, 0), (1, 3), (5, None), (0, 5)):
        want = {
            p.id
            for p in everything
            if (low is None or (p.rating or 0) >= low) and (high is None or (p.rating or 0) <= high)
        }
        got = ids(min_rating=low, max_rating=high)
        check(f"min_rating={low} max_rating={high}: {len(got)} photos", got == want)
    for flag, status in (("pick", 1), ("none", 0), ("reject", -1)):
        want = {p.id for p in everything if p.pick_status == status}
        check(f"flag={flag}: {len(want)} photos", ids(flag=flag) == want)
    check("the new copy is among the 4-star picks", copy.id in ids(min_rating=4, flag="pick"))

    print("4. keyword")
    check("keyword finds exactly the copy", ids(keyword=KEYWORD.upper()) == {copy.id})
    check("keyword that does not exist finds nothing", ids(keyword="no-such-keyword-xyz") == set())

    print("5. filters combine, and the total counts matches only")
    page = lightroom.list_catalog(0, 1, include_virtual_copies=True, keyword=KEYWORD, min_rating=4)
    check("total 1 with the page size 1", (page.total, page.returned) == (1, 1))
    check(
        "the plug-in confirms the filters", set(page.filters_applied) == {"keyword", "min_rating"}
    )

    print("6. take the library changes back")
    undone = tools.call("undo_library_change", {"photo_id": copy.id, "scope": "all"})
    check("undone", undone["undone_steps"] == 1)
    check("the keyword no longer matches", ids(keyword=KEYWORD) == set())


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
