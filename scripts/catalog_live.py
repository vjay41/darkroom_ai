"""Live end-to-end test of catalog management on the configured (rehearsal) catalog.

Checks, against the real Lightroom bridge: the catalog overview and catalog search, the owner's
folder allowlist (refused first, then allowed after an approval), scan with same-name hint, a small
import (files never change), collections and sets, keywords, rating/flag/keywords on a MASTER, a
bulk go-ahead, and taking every change back that can be taken back.

It writes only into the configured test catalog and a scratch folder `import_test_<time>` beside it.
Imported photos and created keywords stay in the catalog (they cannot be removed by darkroom_ai).

Usage:  poetry run python scripts/catalog_live.py
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

failures: list[str] = []
STAMP = time.strftime("%Y%m%d-%H%M%S")
SET = f"DarkroomLiveTest-{STAMP}"
COLLECTION = f"{SET}/Imported"
BULK = f"{SET}/Bulk"
KEYWORD_PATH = "DarkroomLiveTest/Live"


class Approver:
    def __init__(self) -> None:
        self.asked: list[str] = []
        self.yes = True

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        self.asked.append(action)
        print(f"  go-ahead asked ({'yes' if self.yes else 'no'}): {action}")
        return self.yes


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def refused(tools: ToolRegistry, name: str, code: ErrorCode, **args: Any) -> bool:
    try:
        tools.call(name, args)
    except DarkroomError as exc:
        return exc.code is code
    return False


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> None:
    settings = load_config(args.config)
    catalog = settings.lightroom.test_catalog
    root = settings.lightroom.test_photos_dir
    if catalog is None or root is None:
        raise SystemExit("Refusing: lightroom.test_catalog / test_photos_dir are not set.")
    approver = Approver()
    context = build_tool_context(settings, confirmer=approver)
    tools = ToolRegistry(context)
    lr = context.lightroom

    print("0. clean up leftovers of an earlier interrupted run")
    for leftover in ("DarkroomLiveTest/Imported", "DarkroomLiveTest/Bulk"):
        try:
            members = lr.collection_photos(leftover, 200)
            if members.total:
                lr.remove_from_collection(leftover, [p.id for p in members.photos])
            lr.delete_collection(leftover, "collection")
            print(f"  removed {leftover}")
        except DarkroomError:
            pass
    try:
        if lr.delete_collection("DarkroomLiveTest", "set").deleted:
            print("  removed set DarkroomLiveTest")
    except DarkroomError:
        pass

    print("1. overview and catalog search (read-only)")
    info = tools.call("get_catalog_info", {})
    check(
        "open catalog is the test catalog",
        info["catalog"]["catalog_path"].lower() == str(catalog).lower(),
    )
    check("changes are allowed there", info["catalog"]["writes_allowed"] is True)
    total_before = info["catalog"]["total_photos"]
    print(f"  {total_before} photos, {info['catalog']['virtual_copies']} virtual copies")
    found = tools.call("list_catalogs", {})
    mine = [c for c in found["catalogs"] if c["path"].lower() == str(catalog).lower()]
    check("list_catalogs finds the open catalog", len(mine) == 1 and mine[0]["is_open"])
    check("...marked as allowed", bool(mine) and mine[0]["changes_allowed"])
    others = [c["path"] for c in found["catalogs"] if not c["is_open"]]
    print(f"  {len(found['catalogs'])} catalog file(s) found; other names: {len(others)}")
    check(
        "an unknown catalog path is refused",
        refused(
            tools,
            "request_catalog_access",
            ErrorCode.GUARDRAIL_REJECTED,
            catalog_path=str(catalog) + ".missing.lrcat",
        ),
    )
    again = tools.call("request_catalog_access", {"catalog_path": str(catalog)})
    check("allowing the already-allowed catalog changes nothing", again["changed"] is False)

    print("2. the owner's folder allowlist")
    scratch = Path(str(catalog)).parent / f"import_test_{STAMP}"
    scratch.mkdir(exist_ok=True)
    sources = (
        sorted(p for p in root.glob("*.JPG") if p.is_file())[:4] or sorted(root.glob("*.jpg"))[:4]
    )
    check("found source photos to copy", len(sources) == 4)
    made: list[Path] = []
    for number, source in enumerate(sources[:3], start=1):
        target = scratch / f"live_import_{STAMP}_{number}.jpg"
        shutil.copyfile(source, target)
        made.append(target)
    same_name = scratch / sources[3].name
    shutil.copyfile(sources[3], same_name)
    made.append(same_name)
    hashes = {p: sha(p) for p in made}
    check(
        "a folder outside the allowed folders is refused",
        refused(tools, "scan_folder", ErrorCode.GUARDRAIL_REJECTED, folder=str(scratch)),
    )
    approver.yes = False
    check(
        "the owner can say No",
        refused(
            tools, "request_folder_access", ErrorCode.CONFIRMATION_REQUIRED, folder=str(scratch)
        ),
    )
    check(
        "...and it stays refused",
        refused(tools, "scan_folder", ErrorCode.GUARDRAIL_REJECTED, folder=str(scratch)),
    )
    approver.yes = True
    granted = tools.call("request_folder_access", {"folder": str(scratch)})
    check("the owner can say Yes", granted["changed"] is True)
    ping = lr.ping()
    check(
        "the plug-in sees the new folder without a restart",
        any(r.lower() == str(scratch).lower() for r in ping.photos_roots),
    )

    print("3. scan and import")
    scan = tools.call("scan_folder", {"folder": str(scratch)})["scan"]
    print(
        f"  new {scan['new_count']}, already {scan['already_in_catalog']}, "
        f"same-name {scan['same_name_count']}"
    )
    check("4 new photos", scan["new_count"] == 4)
    check(
        "the same-name photo is flagged", scan["same_name_count"] == 1 and scan["same_name_checked"]
    )
    done = tools.call("import_folder", {"folder": str(scratch)})["imported"]
    check(
        "4 photos added, no go-ahead needed", done["added"] == 4 and len(done["added_paths"]) == 4
    )
    check("files unchanged on disk", all(sha(p) == h for p, h in hashes.items()))
    check(
        "scanning again finds nothing new",
        tools.call("scan_folder", {"folder": str(scratch)})["scan"]["new_count"] == 0,
    )
    info2 = tools.call("get_catalog_info", {})
    check("catalog grew by 4", info2["catalog"]["total_photos"] == total_before + 4)
    imported = [lr.find_photo(str(p)) for p in made]
    check("every imported photo is found by path", all(p is not None for p in imported))
    ids = [p.id for p in imported if p is not None]

    print("4. collections and keywords")
    check("set created", tools.call("create_collection_set", {"path": SET})["created"] is True)
    check(
        "collection created",
        tools.call("create_collection", {"path": COLLECTION})["created"] is True,
    )
    check("set is reused", tools.call("create_collection_set", {"path": SET})["created"] is False)
    added = tools.call("add_to_collection", {"collection": COLLECTION, "photo_ids": ids})
    check(
        "4 photos added to the collection",
        len(added["changed_ids"]) == 4 and added["photo_count"] == 4,
    )
    listed = tools.call("get_collection_photos", {"collection": COLLECTION})
    check(
        "collection lists them",
        listed["total"] == 4 and {p["id"] for p in listed["photos"]} == set(ids),
    )
    tree = tools.call("get_catalog_info", {})["catalog"]["collections"]
    node = next((n for n in tree if n["name"] == SET), None)
    check(
        "overview shows set with 4 photos",
        node is not None and node["children"][0]["photo_count"] == 4,
    )
    asked = len(approver.asked)
    tools.call("create_collection", {"path": BULK})
    page = lr.list_catalog(0, 14, include_virtual_copies=False)
    bulk_ids = [p.id for p in page.photos][:12]
    bulk = tools.call("add_to_collection", {"collection": BULK, "photo_ids": bulk_ids})
    check(
        "12 photos needed a go-ahead",
        len(approver.asked) == asked + 1 and len(bulk["changed_ids"]) == 12,
    )
    kw = tools.call("create_keyword", {"path": KEYWORD_PATH})
    check(
        "keyword hierarchy created",
        kw["created_levels"][-1] == "Live" or kw["created_levels"] == [],
    )

    print("5. rating, flag and keywords on a MASTER")
    master = ids[0]
    check("it is a master", not lr.get_photo(master).is_virtual_copy)
    got = tools.call(
        "set_library_state",
        {"photo_id": master, "rating": 3, "flag": "pick", "add_keywords": ["live-test"]},
    )
    check("master changed and read back", got["verified"] and got["photo_id"] == master)
    check("state is as asked", tools.call("get_library_state", {"photo_id": master})["rating"] == 3)
    tools.call("undo_library_change", {"photo_id": master})
    back = tools.call("get_library_state", {"photo_id": master})
    check(
        "undo restored the master",
        back["rating"] is None and back["flag"] == "none" and "live-test" not in back["keywords"],
    )
    copies_now = tools.call("get_catalog_info", {})["catalog"]["virtual_copies"]
    check(
        "no working copy was made for the master", copies_now == info["catalog"]["virtual_copies"]
    )

    print("6. edit what is already in the catalog")
    edit_id = ids[1]
    measured = tools.call("analyze_photo", {"photo_id": edit_id})
    check("analysis returned numbers", bool(measured))
    edit = tools.call("adjust_lighting", {"photo_id": edit_id, "exposure": 0.1})
    check("a small edit landed on a working copy", edit["photo_id"] != edit_id)
    tools.call("undo", {"photo_id": edit["photo_id"], "scope": "all"})

    print("7. take back the collection changes")
    undone = tools.call("undo_collection_change", {"scope": "all"})
    check("collection steps undone", undone["undone_steps"] >= 5)
    tree = tools.call("get_catalog_info", {})["catalog"]["collections"]
    check("set and collections are gone", all(n["name"] != SET for n in tree))
    check("imported files still unchanged", all(sha(p) == h for p, h in hashes.items()))
    print(f"  INFO imported photos and keyword '{KEYWORD_PATH}' stay in the test catalog")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=None)
    try:
        run(parser.parse_args())
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}\nNext step: {exc.next_step}", file=sys.stderr)
        return 2
    print("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
