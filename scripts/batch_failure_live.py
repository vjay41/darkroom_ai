"""Batch live run of the two stop rules: a failure part-way through, and the time budget.

Only photos under lightroom.test_photos_dir are accepted (give three). The photos and Lightroom are
real. Two things are staged by this script, because they cannot be caused on demand:
- the failure: the second Lightroom write is made to raise (a stand-in for Lightroom refusing it);
- the time budget: the batch module's clock jumps 700 s on every reading.
Checks: the first photo is edited, the failing photo is listed as failed, the rest as not_run and
untouched, then undo_batch restores the first photo (read-back verified). Masters and files are
unchanged at the end. Working copies stay in the test catalog (deleting is never done).

Usage:  poetry run python scripts/batch_failure_live.py --photo A --photo B --photo C
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools import batch
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

failures: list[str] = []
WATCH = ["Exposure2012", "Contrast2012", "Shadows2012"]


class Approver:
    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        print(f"  go-ahead asked (yes): {action}")
        return True


class FailingWrite:
    """The real Lightroom client, except that the Nth develop write raises."""

    def __init__(self, real: Any, fail_on: int) -> None:
        self._real = real
        self._fail_on = fail_on
        self.writes = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def apply_develop_settings(self, *args: Any, **kwargs: Any) -> Any:
        self.writes += 1
        if self.writes == self._fail_on:
            raise DarkroomError(
                ErrorCode.LR_COMMAND_FAILED,
                "Staged failure: Lightroom refused this write (by the test script).",
                next_step="None: this is a test.",
            )
        return self._real.apply_develop_settings(*args, **kwargs)


def jumping_clock() -> Callable[[], float]:
    ticks = iter(range(700, 10**6, 700))
    return lambda: float(next(ticks))


def check(name: str, passed: bool) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if not passed:
        failures.append(name)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> None:
    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    if len(args.photo) != 3:
        raise SystemExit("Give exactly three --photo files.")
    for path in args.photo:
        if root is None or not path.resolve().is_relative_to(root.resolve()):
            raise SystemExit(f"Refusing: {path} is not under the test-photo folder ({root}).")
    hashes = {path: sha256(path) for path in args.photo}

    context = build_tool_context(settings, confirmer=Approver())
    real = context.lightroom
    tools = ToolRegistry(context)
    masters = []
    for path in args.photo:
        found = real.find_photo(str(path))
        if found is None:
            raise SystemExit(f"NOT_IN_CATALOG: {path}")
        masters.append(found)
    print("photos:", [(m.file_name, m.file_format) for m in masters])

    def values(photo_id: str) -> dict[str, Any]:
        found: dict[str, Any] = tools.call(
            "get_develop_parameters", {"photo_id": photo_id, "names": WATCH}
        )["values"]
        return found

    def master_values(photo_id: str) -> dict[str, Any]:
        got = real.get_develop_settings(photo_id, WATCH)
        return {key: got.get(key) for key in WATCH}

    masters_before = {m.id: master_values(m.id) for m in masters}
    items: list[dict[str, Any]] = [
        {"photo_id": m.id, "exposure": 0.2, "contrast": 5, "shadows": 5} for m in masters
    ]

    for label in ("failure", "budget"):
        print(f"\n== {label} part-way through ==")
        before = {i["photo_id"]: values(i["photo_id"]) for i in items}
        copies_before = real.list_catalog(0, 1, include_virtual_copies=True).total
        if label == "failure":
            context.lightroom = FailingWrite(real, fail_on=2)
        else:
            context.lightroom = real
            batch._clock = jumping_clock()
        done = tools.call("batch_set_develop_parameters", {"items": items})
        context.lightroom = real
        batch._clock = time.monotonic
        statuses = [i["status"] for i in done["items"]]
        print(f"  statuses: {statuses}; stopped_because: {done.get('stopped_because')}")
        check(
            "first photo ok, second and third not ok",
            statuses[0] == "ok" and "ok" not in statuses[1:],
        )
        if label == "failure":
            check("statuses are ok, failed, not_run", statuses == ["ok", "failed", "not_run"])
            check("the failed item carries the error", bool(done["items"][1]["error"]))
        else:
            check("statuses are ok, not_run, not_run", statuses == ["ok", "not_run", "not_run"])
        check("the summary says why it stopped", bool(done.get("stopped_because")))
        check(
            "the first photo's working copy has the new values",
            values(done["items"][0]["working_copy"])["Exposure2012"] is not None
            and values(done["items"][0]["working_copy"]) != before[items[0]["photo_id"]],
        )
        for result in done["items"]:
            if result["status"] == "not_run":
                check(
                    f"{result['file_name']}: not_run photo left alone",
                    values(result["photo_id"]) == before[result["photo_id"]],
                )
        grew = real.list_catalog(0, 1, include_virtual_copies=True).total - copies_before
        print(f"  INFO catalog grew by {grew} virtual copies")
        check("not_run photos created no copy", grew <= 2)

        undo = tools.call("undo_batch", {"batch_id": done["batch_id"]})
        print(
            f"  undo: steps {undo['undone_steps']}, "
            f"verified {[p['verified'] for p in undo['photos']]}"
        )
        check(
            "undo restored only what was done and read back verified",
            undo["undone_steps"] >= 1 and all(p["verified"] for p in undo["photos"]),
        )
        check(
            "first photo is back at its values from before",
            values(done["items"][0]["working_copy"]) == before[items[0]["photo_id"]],
        )
        for master in masters:
            check(
                f"{master.file_name}: master unchanged",
                master_values(master.id) == masters_before[master.id],
            )

    for path in args.photo:
        check(f"{path.name}: file unchanged on disk", sha256(path) == hashes[path])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photo", type=Path, action="append", required=True)
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
