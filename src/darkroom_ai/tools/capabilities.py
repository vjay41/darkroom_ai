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
