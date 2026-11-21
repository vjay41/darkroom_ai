"""`darkroom selftest`: check, tool by tool, that Lightroom and Photoshop still behave as expected.

Run it after any Adobe update (the first upgrade drill). Read-only by default; `--write` also
makes one virtual copy in the TEST catalog, changes Exposure by a tiny step, reads it back and puts
it back. The adapter refuses writes outside the configured catalog, so the user's main catalog is
never touched. Every probe reports pass / fail / skip with a plain next step.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from darkroom_ai.compat import assess_version, plugin_is_recent_enough
from darkroom_ai.config import GuardrailSettings
from darkroom_ai.errors import DarkroomError
from darkroom_ai.interfaces import LightroomAdapter, PhotoshopAdapter
from darkroom_ai.tools.develop_keys import CROP_KEYS, HSL_COLORS, HSL_PREFIX, lighting_limits

Status = Literal["pass", "fail", "warn", "skip"]
PROBE_STEP = 0.1  # Exposure change used by the write probe (EV)
PROBE_TOLERANCE = 0.02


class Probe(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: Status
    detail: str = ""
    next_step: str = ""


class SelftestReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    started: str
    lightroom_version: str | None = None
    photoshop_version: str | None = None
    wrote: bool = False
    probes: list[Probe] = Field(default_factory=list)

    @property
    def failed(self) -> list[Probe]:
        return [p for p in self.probes if p.status == "fail"]


def expected_keys(guardrails: GuardrailSettings) -> list[str]:
    """Every Develop key the tools rely on (the key map), for the drift check."""
    keys = {limit.key for limit in lighting_limits(guardrails).values()}
    keys.update(CROP_KEYS)
    for prefix in HSL_PREFIX.values():
        keys.update(f"{prefix}{color.capitalize()}" for color in HSL_COLORS)
    keys.update({"ProcessVersion", "IncrementalTemperature", "IncrementalTint"})
    # The masks key is deliberately not here: Lightroom leaves it out while a photo has no masks
    # (seen live, 21 Nov: the first selftest wrongly reported it as a renamed key).
    return sorted(keys)


def _attempt(name: str, action: Callable[[], Probe]) -> Probe:
    try:
        return action()
    except DarkroomError as exc:
        return Probe(
            name=name,
            status="fail",
            detail=f"[{exc.code.value}] {exc.message}",
            next_step=exc.next_step,
        )


def run_selftest(
    lightroom: LightroomAdapter,
    photoshop: PhotoshopAdapter | None,
    guardrails: GuardrailSettings,
    *,
    write: bool = False,
    photo_id: str | None = None,
) -> SelftestReport:
    probes: list[Probe] = []
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    lr_version: str | None = None
    ps_version: str | None = None
    writes_allowed = False

    # 1. Lightroom answers; version and plug-in.
    try:
        info = lightroom.ping()
    except DarkroomError as exc:
        probes.append(
            Probe(
                name="lightroom answers",
                status="fail",
                detail=f"[{exc.code.value}] {exc.message}",
                next_step=(
                    f"{exc.next_step} The plug-in serves one client at a time: if Hermes' "
                    "connector is running it may hold the connection, so run the check from "
                    "Hermes (tool run_selftest) instead of the command line."
                ),
            )
        )
        info = None
    if info is not None:
        lr_version, writes_allowed = info.lightroom_version, info.writes_allowed
        verdict = assess_version("lightroom", lr_version)
        probes.append(
            Probe(
                name="lightroom version",
                status="pass" if verdict.status == "tested" else "warn",
                detail=verdict.note,
            )
        )
        recent = plugin_is_recent_enough(info.plugin_version)
        probes.append(
            Probe(
                name="plug-in version",
                status="pass" if recent else "fail",
                detail=f"plug-in {info.plugin_version}",
                next_step=""
                if recent
                else "Run `darkroom lightroom setup`, then Reload Plug-in in Lightroom.",
            )
        )

    # 2. Photoshop (optional): version only; it is not started by the selftest.
    if photoshop is not None:
        try:
            ps_info = photoshop.info()
            ps_version = ps_info.version
            ps_verdict = assess_version("photoshop", ps_version)
            probes.append(
                Probe(
                    name="photoshop version",
                    status="pass" if ps_verdict.status == "tested" else "warn",
                    detail=ps_verdict.note,
                )
            )
        except DarkroomError as exc:
            probes.append(
                Probe(
                    name="photoshop answers",
                    status="warn",
                    detail=f"[{exc.code.value}] {exc.message}",
                    next_step=exc.next_step,
                )
            )

    if info is None:
        return SelftestReport(
            started=started, photoshop_version=ps_version, probes=probes, wrote=False
        )

    # 3. Pick a photo.
    chosen = photo_id
    if chosen is None:
        page = _attempt_value(lambda: lightroom.list_catalog(limit=1), probes, "read the catalog")
        if page is not None and page.photos:
            chosen = page.photos[0].id
            probes.append(
                Probe(name="read the catalog", status="pass", detail=f"{page.total} photos")
            )
        elif page is not None:
            probes.append(
                Probe(
                    name="read the catalog",
                    status="fail",
                    detail="The test catalog has no photos.",
                    next_step="Import a test photo into the test catalog, then run again.",
                )
            )
    if chosen is None:
        return SelftestReport(
            started=started,
            lightroom_version=lr_version,
            photoshop_version=ps_version,
            probes=probes,
        )
    target = chosen

    # 4. Key-map drift: every key the tools use must be readable.
    def drift() -> Probe:
        keys = expected_keys(guardrails)
        raw = lightroom.get_develop_settings(target, keys)
        missing = [k for k in keys if k not in raw or raw[k] is None]
        if missing:
            return Probe(
                name="develop key map",
                status="fail",
                detail=f"{len(missing)} of {len(keys)} keys not returned: {', '.join(missing)}",
                next_step=(
                    "Adobe may have renamed these settings. Update develop_keys.py and the "
                    "compatibility data, then run again."
                ),
            )
        return Probe(name="develop key map", status="pass", detail=f"{len(keys)} keys readable")

    probes.append(_attempt("develop key map", drift))

    # 5. Reads: metadata, masks, presets, preview.
    def read_metadata() -> Probe:
        meta = lightroom.get_metadata(target)
        return Probe(name="read metadata", status="pass", detail=meta.file_name)

    def read_masks(photo: str) -> Probe:
        lightroom.inspect_masks(photo)
        return Probe(name="read masks", status="pass")

    def read_presets() -> Probe:
        return Probe(
            name="list presets", status="pass", detail=f"{len(lightroom.list_presets())} presets"
        )

    def render_preview() -> Probe:
        preview = lightroom.export_preview(target, "selftest", 512)
        return Probe(name="render a preview", status="pass", detail=str(preview.path))

    for name, action in (
        ("read metadata", read_metadata),
        ("list presets", read_presets),
        ("render a preview", render_preview),
    ):
        probes.append(_attempt(name, action))

    # Masks are read on a virtual copy only (a master is never touched by the mask tools).
    if lightroom.get_photo(target).is_virtual_copy:
        probes.append(_attempt("read masks", lambda: read_masks(target)))

    # 6. Optional write probe on a virtual copy.
    wrote = False
    if not write:
        if not lightroom.get_photo(target).is_virtual_copy:
            probes.append(
                Probe(name="read masks", status="skip", detail="Needs a virtual copy: use --write.")
            )
        probes.append(
            Probe(
                name="write probe",
                status="skip",
                detail="Not run: add --write to test an edit on a virtual copy (test catalog).",
            )
        )
    elif not writes_allowed:
        probes.append(
            Probe(
                name="write probe",
                status="fail",
                detail="Edits are refused: the open catalog is not the configured test catalog.",
                next_step="Open the test catalog in Lightroom, then run again.",
            )
        )
    else:

        def write_probe() -> Probe:
            nonlocal wrote
            copy = lightroom.create_virtual_copy(target, f"darkroom_ai selftest {started}")
            wrote = True
            probes.append(_attempt("read masks", lambda: read_masks(copy.id)))
            before = float(
                lightroom.get_develop_settings(copy.id, ["Exposure2012"])["Exposure2012"]
            )
            lightroom.apply_develop_settings(
                copy.id, {"Exposure2012": before + PROBE_STEP}, "darkroom_ai: selftest"
            )
            after = float(lightroom.get_develop_settings(copy.id, ["Exposure2012"])["Exposure2012"])
            lightroom.apply_develop_settings(
                copy.id, {"Exposure2012": before}, "darkroom_ai: selftest restore"
            )
            restored = float(
                lightroom.get_develop_settings(copy.id, ["Exposure2012"])["Exposure2012"]
            )
            if abs(after - (before + PROBE_STEP)) > PROBE_TOLERANCE:
                return Probe(
                    name="write probe",
                    status="fail",
                    detail=f"Exposure was set to {before + PROBE_STEP:.2f} but reads {after:.2f}.",
                    next_step="Lightroom did not store the edit: check the plug-in log.",
                )
            if abs(restored - before) > PROBE_TOLERANCE:
                return Probe(
                    name="write probe",
                    status="fail",
                    detail=f"Exposure did not go back: {before:.2f}, now {restored:.2f}.",
                    next_step="Check the 'darkroom_ai selftest' virtual copy in the test catalog.",
                )
            return Probe(
                name="write probe",
                status="pass",
                detail=f"set {after:.2f}, restored {restored:.2f} on a virtual copy",
            )

        probes.append(_attempt("write probe", write_probe))

    return SelftestReport(
        started=started,
        lightroom_version=lr_version,
        photoshop_version=ps_version,
        wrote=wrote,
        probes=probes,
    )


def _attempt_value(action: Callable[[], Any], probes: list[Probe], name: str) -> Any:
    try:
        return action()
    except DarkroomError as exc:
        probes.append(
            Probe(
                name=name,
                status="fail",
                detail=f"[{exc.code.value}] {exc.message}",
                next_step=exc.next_step,
            )
        )
        return None


def format_report(report: SelftestReport) -> str:
    marks = {"pass": "ok  ", "fail": "FAIL", "warn": "warn", "skip": "skip"}
    lines = [
        f"darkroom selftest {report.started}",
        f"  Lightroom {report.lightroom_version or '?'}, "
        f"Photoshop {report.photoshop_version or '?'}",
    ]
    for probe in report.probes:
        lines.append(f"[{marks[probe.status]}] {probe.name}: {probe.detail}".rstrip(": "))
        if probe.next_step:
            lines.append(f"       next: {probe.next_step}")
    failed = len(report.failed)
    lines.append("RESULT: " + ("all probes passed" if not failed else f"{failed} probe(s) failed"))
    return "\n".join(lines)
