"""Rehearsal live run over EVERY photo of the configured test-photo folder (a separate catalog).

Needs a catalog in which Lightroom has imported the folder (Add, nothing copied or moved) and
`lightroom.test_catalog` / `lightroom.test_photos_dir` pointing at them. Steps:
1. read-only over all photos: listing, formats, ISO and capture-date filters checked against the
   photos' own metadata, numeric measurement of every photo (analyze_batch, in chunks);
2. a develop batch on 10 photos (every RAW first), values chosen from the measured tones and kept
   small, which makes their working copies;
3. a library batch (rating, flag, keyword) on those working copies; both taken back with undo_batch.
All writes go to virtual copies. Every photo file is hashed before and after and must not change;
masters' develop settings and library state must not change. Virtual copies stay in the catalog.

Usage:  poetry run python scripts/rehearsal_live.py
"""

from __future__ import annotations

import argparse
import hashlib
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
KEYWORD = "darkroom-ai-rehearsal"
WATCH = ["Exposure2012", "Contrast2012", "Shadows2012"]
PAGE = 100


class Approver:
    def __init__(self) -> None:
        self.asked = 0

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        self.asked += 1
        print(f"  go-ahead asked (yes): {action}")
        return True


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digits(text: object) -> int | None:
    found = "".join(ch for ch in str(text or "") if ch.isdigit())
    return int(found) if found else None


def run(args: argparse.Namespace) -> None:
    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    if root is None:
        raise SystemExit("Refusing: lightroom.test_photos_dir is not set.")
    approver = Approver()
    context = build_tool_context(settings, confirmer=approver)
    tools = ToolRegistry(context)
    lightroom = context.lightroom

    print("1. inventory (read-only)")
    photos: list[Any] = []
    while True:
        page = lightroom.list_catalog(len(photos), PAGE, include_virtual_copies=False)
        photos.extend(page.photos)
        if len(photos) >= page.total or not page.photos:
            break
    formats: dict[str, int] = {}
    for p in photos:
        formats[str(p.file_format).upper()] = formats.get(str(p.file_format).upper(), 0) + 1
    print(f"  {len(photos)} masters; formats {formats}")
    check("the catalog holds photos", len(photos) > 0)
    outside = [p.path for p in photos if not Path(p.path).resolve().is_relative_to(root.resolve())]
    check("every photo is under the test-photo folder", not outside)
    if outside:
        raise SystemExit(f"Refusing: photos outside {root}: {outside[:3]}")
    paths = {p.id: Path(p.path) for p in photos}
    missing = [str(f) for f in paths.values() if not f.is_file()]
    check("every catalog photo exists on disk", not missing)
    hashes = {pid: sha256(f) for pid, f in paths.items()}
    print(f"  hashed {len(hashes)} files")

    print("2. filters against the photos' own metadata")
    for fmt, count in formats.items():
        got = lightroom.list_catalog(0, 1, file_format=fmt.lower()).total
        check(f"file_format={fmt}: {count}", got == count)
    isos = [digits(p.iso) for p in photos]
    with_iso = [v for v in isos if v is not None]
    mid = sorted(with_iso)[len(with_iso) // 2] if with_iso else 0
    for low, high in ((None, mid), (mid + 1, None)):
        want = sum(
            1
            for v in isos
            if v is not None and (low is None or v >= low) and (high is None or v <= high)
        )
        got = lightroom.list_catalog(0, 1, min_iso=low, max_iso=high).total
        check(f"ISO {low}..{high}: {want}", got == want)
    dated = [p for p in photos if p.capture_time]
    seen = 0
    for year in range(1990, 2027):
        seen += lightroom.list_catalog(
            0, 1, captured_from=f"{year}-01-01", captured_to=f"{year}-12-31"
        ).total
    check(f"capture years add up to the {len(dated)} dated photos", seen == len(dated))

    print("3. numbers for every photo (analyze_batch, 25 at a time)")
    numbers: dict[str, Any] = {}
    began = time.monotonic()
    ids = [p.id for p in photos]
    todo = list(ids)
    while todo:
        chunk, todo = todo[:25], todo[25:]
        answer = tools.call("analyze_batch", {"photo_ids": chunk, "time_budget_s": 300})
        for r in answer["results"]:
            numbers[r["photo_id"]] = r
        todo = list(answer.get("not_processed", [])) + todo if answer.get("not_processed") else todo
        if answer.get("not_processed") and len(answer["results"]) == 0:
            break
    bad = [r for r in numbers.values() if r.get("error")]
    print(f"  measured {len(numbers)} in {time.monotonic() - began:.0f} s; errors {len(bad)}")
    check("every photo was measured without error", len(numbers) == len(ids) and not bad)
    clipped = sorted(numbers.values(), key=lambda r: -(r.get("highlights_clipped_pct") or 0))[:3]
    print(
        "  most clipped highlights:",
        [(r["file_name"], r["highlights_clipped_pct"]) for r in clipped],
    )

    def master_state(pid: str) -> tuple[dict[str, Any], Any]:
        develop = lightroom.get_develop_settings(pid, WATCH)
        state = tools.call("get_library_state", {"photo_id": pid})
        return {k: develop.get(k) for k in WATCH}, (
            state["rating"],
            state["flag"],
            tuple(sorted(state["keywords"])),
        )

    raws = [p for p in photos if str(p.file_format).upper() in {"RAW", "DNG"}]
    jpgs = [p for p in photos if p not in raws]
    dev_pick = (raws + jpgs)[:10]
    before = {p.id: master_state(p.id) for p in dev_pick}
    total_before = lightroom.list_catalog(0, 1, include_virtual_copies=True).total

    print(f"4. develop batch on {len(dev_pick)} photos (values from measured tones)")
    dev_items: list[dict[str, Any]] = []
    for p in dev_pick:
        median = numbers[p.id].get("median") or 0.45
        item: dict[str, Any] = {"photo_id": p.id, "shadows": 5, "contrast": 3}
        if median < 0.30:
            item["exposure"] = 0.3
        elif median > 0.65:
            item["exposure"] = -0.3
        dev_items.append(item)
    dev = tools.call("batch_set_develop_parameters", {"items": dev_items})
    print(
        f"  ok {dev['ok']} failed {dev['failed']} not_run {dev['not_run']} "
        f"copies {dev['copies_created']}"
    )
    check("all develop items ok", dev["ok"] == len(dev_items) and dev["failed"] == 0)
    check("nothing was clamped", all(not s["clamped"] for i in dev["items"] for s in i["steps"]))
    copies = [i["working_copy"] for i in dev["items"]]

    print(f"5. library batch on those {len(copies)} working copies")
    items = [{"photo_id": c, "rating": 3, "add_keywords": [KEYWORD]} for c in copies]
    items[0]["flag"] = "pick"
    asked = approver.asked
    lib = tools.call("batch_set_library_state", {"items": items})
    print(f"  ok {lib['ok']} failed {lib['failed']} in {lib['elapsed_s']} s")
    check(
        "all library items ok and verified",
        lib["ok"] == len(items) and all(i["verified"] for i in lib["items"]),
    )
    check("a batch above the limit asked for a go-ahead", approver.asked > asked)

    print("   undo: library batch first, then the develop batch")
    undo = tools.call("undo_batch", {"batch_id": lib["batch_id"]})
    check(
        "library undo verified",
        all(p["verified"] for p in undo["photos"]) and undo["undone_steps"] >= len(items),
    )
    undo = tools.call("undo_batch", {"batch_id": dev["batch_id"]})
    check(
        "develop undo verified",
        all(p["verified"] for p in undo["photos"]) and undo["undone_steps"] >= len(dev_items),
    )

    print("6. nothing original changed")
    for pid, state in before.items():
        if master_state(pid) != state:
            check(f"master {pid} unchanged", False)
    check(
        "masters' develop and library state unchanged",
        not [f for f in failures if f.startswith("master ")],
    )
    changed = [str(f) for pid, f in paths.items() if sha256(f) != hashes[pid]]
    check(f"all {len(paths)} photo files unchanged on disk", not changed)
    grew = lightroom.list_catalog(0, 1, include_virtual_copies=True).total - total_before
    print(f"  INFO catalog grew by {grew} virtual copies (kept; deleting is never done)")


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
