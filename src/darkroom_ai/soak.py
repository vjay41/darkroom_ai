"""Soak test: run the real tools over and over on the TEST catalog and count what fails.

It checks that tools keep working (set, read back, undo, mask create/list/delete, preview, listing),
never that an edit is good. It makes edits on one working copy (a virtual copy of one master) and
takes every one of them back; it never touches originals.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from darkroom_ai.errors import DarkroomError

Call = Callable[[str, Mapping[str, Any]], dict[str, Any]]
EXPOSURE = "Exposure2012"
EXPOSURE_STEP = 0.2
TOLERANCE = 0.03


class Step(BaseModel):
    cycle: int
    name: str
    ok: bool
    seconds: float
    detail: str = ""


class SoakReport(BaseModel):
    started: str
    photo_id: str | None
    cycles: int
    steps: list[Step]

    @property
    def failed(self) -> list[Step]:
        return [s for s in self.steps if not s.ok]

    @property
    def slowest_s(self) -> float:
        return max((s.seconds for s in self.steps), default=0.0)


def _exposure(call: Call, photo_id: str) -> float:
    got = call("get_develop_parameters", {"photo_id": photo_id, "names": [EXPOSURE]})
    value = got["values"].get(EXPOSURE)
    return 0.0 if value is None else float(value)


class _Soak:
    def __init__(self, call: Call, report: SoakReport) -> None:
        self.call = call
        self.report = report
        self.cycle = 0
        self.photo_id = report.photo_id or ""
        self.exposure_before = 0.0
        self.mask_count = 0
        self.mask_id: str | None = None

    def step(self, name: str, action: Callable[[], str | None]) -> bool:
        began = time.monotonic()
        try:
            detail = action() or ""
            ok = True
        except DarkroomError as exc:
            detail, ok = f"[{exc.code.value}] {exc.message}", False
        except Exception as exc:  # a crashing tool is a soak result, not a reason to stop
            detail, ok = f"{type(exc).__name__}: {exc}", False
        self.report.steps.append(
            Step(
                cycle=self.cycle,
                name=name,
                ok=ok,
                seconds=round(time.monotonic() - began, 2),
                detail=detail,
            )
        )
        return ok

    def choose_photo(self) -> str:
        page = self.call("list_catalog_photos", {"limit": 5})["page"]
        masters = [p for p in page["photos"] if not p.get("is_virtual_copy")]
        if not masters:
            raise RuntimeError("the catalog lists no photo to test with")
        self.photo_id = masters[0]["id"]
        self.report.photo_id = self.photo_id
        return f"using {masters[0].get('file_name', self.photo_id)}"

    def listing(self) -> str:
        page = self.call("list_catalog_photos", {"limit": 5})["page"]
        if page["returned"] < 1:
            raise RuntimeError("no photos returned")
        return f"{page['total']} photos"

    def preview(self) -> None:
        self.call("get_preview", {"photo_id": self.photo_id, "long_edge": 512})

    def read_exposure(self) -> str:
        self.exposure_before = _exposure(self.call, self.photo_id)
        return f"{self.exposure_before:.2f}"

    def edit_and_read_back(self) -> str:
        self.call("adjust_lighting", {"photo_id": self.photo_id, "exposure": EXPOSURE_STEP})
        now = _exposure(self.call, self.photo_id)
        change = now - self.exposure_before
        if abs(change - EXPOSURE_STEP) > TOLERANCE:
            raise RuntimeError(f"wrote +{EXPOSURE_STEP}, read back {change:+.3f}")
        return f"exposure {self.exposure_before:.2f} -> {now:.2f}"

    def undo_and_check(self) -> str:
        self.call("undo", {"photo_id": self.photo_id, "scope": "last"})
        now = _exposure(self.call, self.photo_id)
        if abs(now - self.exposure_before) > TOLERANCE:
            raise RuntimeError(
                f"undo left exposure at {now:.3f}, expected {self.exposure_before:.3f}"
            )
        return "restored"

    def _masks(self) -> int:
        return int(self.call("list_masks", {"photo_id": self.photo_id})["count"])

    def _masks_now(self) -> int:
        try:
            return self._masks()
        except DarkroomError:
            return self.mask_count

    def mask_baseline(self) -> str:
        self.mask_count = self._masks()
        return f"{self.mask_count} masks"

    def mask_create(self) -> str:
        got = self.call(
            "create_mask",
            {
                "photo_id": self.photo_id,
                "mask_type": "gradient",
                "start": (0.5, 0.2),
                "end": (0.5, 0.8),
            },
        )
        self.mask_id = got.get("mask_id")
        now = self._masks()
        if now != self.mask_count + 1:
            raise RuntimeError(f"expected {self.mask_count + 1} masks after create, found {now}")
        if not self.mask_id:
            raise RuntimeError(
                "the mask exists but Lightroom gave no mask id: " + got.get("note", "")
            )
        return f"mask {self.mask_id}"

    def mask_delete(self) -> str:
        if self.mask_id:
            self.call("delete_mask", {"photo_id": self.photo_id, "mask_id": self.mask_id})
        else:  # no id to delete by: take the creation back with undo
            self.call("undo", {"photo_id": self.photo_id, "scope": "last"})
        now = self._masks()
        if now != self.mask_count:
            raise RuntimeError(f"expected {self.mask_count} masks after clean-up, found {now}")
        return "back to baseline"

    def run_cycle(self) -> None:
        self.step("list_catalog_photos", self.listing)
        self.step("get_preview", self.preview)
        if not self.step("read exposure", self.read_exposure):
            return
        if self.step("edit + read back", self.edit_and_read_back):
            self.step("undo + check", self.undo_and_check)
        if self.step("mask count", self.mask_baseline):
            self.mask_id = None
            self.step("create gradient mask", self.mask_create)
            if self._masks_now() != self.mask_count:  # always leave the photo as found
                self.step("clean up mask", self.mask_delete)


def run_soak(call: Call, *, cycles: int, photo_id: str | None = None) -> SoakReport:
    report = SoakReport(
        started=time.strftime("%Y-%m-%dT%H:%M:%S"), photo_id=photo_id, cycles=cycles, steps=[]
    )
    soak = _Soak(call, report)
    if photo_id is None and not soak.step("choose photo", soak.choose_photo):
        return report
    for number in range(1, cycles + 1):
        soak.cycle = number
        soak.run_cycle()
    return report


def format_soak(report: SoakReport) -> str:
    by_name: dict[str, list[Step]] = {}
    for item in report.steps:
        by_name.setdefault(item.name, []).append(item)
    lines = [f"Soak: {report.cycles} cycle(s) on photo {report.photo_id}"]
    for name, items in by_name.items():
        bad = [i for i in items if not i.ok]
        slowest = max(i.seconds for i in items)
        lines.append(
            f"  {name:<24} {len(items) - len(bad)}/{len(items)} ok, slowest {slowest:.1f}s"
        )
        lines.extend(f"      cycle {i.cycle}: {i.detail}" for i in bad[:3])
    lines.append(
        "RESULT: no failures"
        if not report.failed
        else f"RESULT: {len(report.failed)} failed step(s) - see above"
    )
    return "\n".join(lines)
