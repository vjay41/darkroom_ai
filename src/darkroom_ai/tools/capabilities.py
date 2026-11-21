"""get_capabilities: what is installed, and what has (and has not) been verified against it."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from darkroom_ai.compat import (
    Feature,
    VersionAssessment,
    assess_version,
    load_compatibility,
    plugin_is_recent_enough,
)
from darkroom_ai.errors import DarkroomError
from darkroom_ai.safety.confirm import require_confirmation
from darkroom_ai.selftest import Probe, run_selftest
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult


class GetCapabilitiesInput(BaseModel):
    """Report the installed Lightroom Classic, plug-in and Photoshop versions, whether each is a
    version darkroom_ai was tested with, and which features are verified, untested, have a known
    issue or are unavailable. Reads only. Call it at the start of a session and before relying on
    an untested feature (for example RAW edits or people masks); tell Vijay about anything that is
    not 'verified' before you use it. An untested version is a warning, never a block."""

    model_config = ConfigDict(extra="forbid")


class PluginState(BaseModel):
    version: str | None
    recent_enough: bool | None
    note: str


class GetCapabilitiesResult(ToolResult):
    lightroom: VersionAssessment
    photoshop: VersionAssessment
    plugin: PluginState
    features: list[Feature]
    not_verified: list[str]
    advice: str


def _lightroom(ctx: ToolContext) -> tuple[str | None, str | None]:
    try:
        info = ctx.lightroom.ping()
    except DarkroomError:
        return None, None
    return info.lightroom_version, info.plugin_version


def _photoshop(ctx: ToolContext) -> str | None:
    if ctx.photoshop is None:
        return None
    try:
        return ctx.photoshop.info().version
    except DarkroomError:
        return None


def get_capabilities(ctx: ToolContext, args: GetCapabilitiesInput) -> Outcome:
    data = load_compatibility()
    lr_version, plugin_version = _lightroom(ctx)
    plugin = PluginState(
        version=plugin_version,
        recent_enough=None
        if plugin_version is None
        else plugin_is_recent_enough(plugin_version, data),
        note=(
            "The plug-in did not answer."
            if plugin_version is None
            else (
                "The plug-in version is recent enough."
                if plugin_is_recent_enough(plugin_version, data)
                else f"The plug-in is older than {data.plugin.min_version}: update and reload it "
                "(darkroom lightroom setup, then Reload Plug-in in Lightroom)."
            )
        ),
    )
    lightroom = assess_version("lightroom", lr_version, data)
    photoshop = assess_version("photoshop", _photoshop(ctx), data)
    risky = [f for f in data.features if f.status != "verified"]
    not_verified = [f"{f.name}: {f.status}" + (f" - {f.note}" if f.note else "") for f in risky]
    warn = [a.note for a in (lightroom, photoshop) if a.status in ("untested", "known_issue")]
    advice = " ".join(warn) or "Versions match what was tested."
    return Outcome(
        GetCapabilitiesResult(
            lightroom=lightroom,
            photoshop=photoshop,
            plugin=plugin,
            features=data.features,
            not_verified=not_verified,
            advice=advice,
        )
    )


class RunSelftestInput(BaseModel):
    """Check, probe by probe, that the installed Lightroom Classic (and Photoshop, if it is
    already running) still behaves as darkroom_ai expects: versions, catalog read, every Develop
    key the tools use (drift check), metadata, presets, preview. Run it after an Adobe update and
    report every probe. Reads only by default. With write=true it also makes ONE virtual copy in
    the test catalog, changes Exposure by +0.1 EV, reads it back and puts it back; that asks
    Vijay first. A failed probe means the matching tools may not work: say which."""

    model_config = ConfigDict(extra="forbid")

    write: bool = False


class RunSelftestResult(ToolResult):
    lightroom_version: str | None
    photoshop_version: str | None
    wrote_a_virtual_copy: bool
    all_passed: bool
    probes: list[Probe]


def run_selftest_tool(ctx: ToolContext, args: RunSelftestInput) -> Outcome:
    if args.write:
        require_confirmation(
            ctx.confirmer,
            "run the write self-test: make one virtual copy in the test catalog, change Exposure "
            "by +0.1 EV and put it back",
            {"catalog": "test catalog only", "originals": "untouched"},
        )
    photoshop = ctx.photoshop
    if ctx.processes is not None and ctx.processes.is_running(ctx.settings.apps.photoshop_process):
        pass
    else:
        photoshop = None  # the selftest never starts Photoshop
    report = run_selftest(ctx.lightroom, photoshop, ctx.settings.guardrails, write=args.write)
    return Outcome(
        RunSelftestResult(
            lightroom_version=report.lightroom_version,
            photoshop_version=report.photoshop_version,
            wrote_a_virtual_copy=report.wrote,
            all_passed=not report.failed,
            probes=report.probes,
        )
    )
