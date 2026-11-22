"""Compatibility data: which Adobe versions and features darkroom_ai has been verified against.

The facts live in `compatibility.toml`. This module reads them and judges a detected version.
Judgements only WARN: an untested or known-issue version is never refused. A tool is blocked only
when it really fails (and `darkroom selftest` shows which ones do).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DATA_FILE = Path(__file__).with_name("compatibility.toml")

FeatureStatus = Literal["verified", "known_issue", "untested", "unavailable"]
VersionStatus = Literal["tested", "known_issue", "untested", "unknown"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class KnownIssue(_Frozen):
    versions: list[str]  # version prefixes the issue affects
    text: str


class AppCompat(_Frozen):
    baseline: str
    tested: list[str]
    known_issues: list[KnownIssue] = Field(default_factory=list)


class PluginCompat(_Frozen):
    min_version: str


class Feature(_Frozen):
    name: str
    tools: list[str]
    status: FeatureStatus
    verified_on: str = ""
    note: str = ""


class Compatibility(_Frozen):
    apps: dict[str, AppCompat]
    plugin: PluginCompat
    features: list[Feature]


class VersionAssessment(_Frozen):
    detected: str | None
    status: VersionStatus
    tested: list[str]
    baseline: str
    note: str


@lru_cache(maxsize=1)
def load_compatibility(path: Path = DATA_FILE) -> Compatibility:
    with path.open("rb") as handle:
        return Compatibility.model_validate(tomllib.load(handle))


def _starts(version: str, prefixes: list[str]) -> bool:
    return any(version == p or version.startswith(p + ".") for p in prefixes)


def assess_version(
    app: str, detected: str | None, data: Compatibility | None = None
) -> VersionAssessment:
    """Judge one app version. `detected` None (app not answering) gives status 'unknown'."""
    compat = (data or load_compatibility()).apps[app]
    name = "Lightroom Classic" if app == "lightroom" else "Photoshop"
    if not detected:
        return VersionAssessment(
            detected=None,
            status="unknown",
            tested=compat.tested,
            baseline=compat.baseline,
            note=f"{name}'s version is not known because it did not answer.",
        )
    for issue in compat.known_issues:
        if _starts(detected, issue.versions):
            return VersionAssessment(
                detected=detected,
                status="known_issue",
                tested=compat.tested,
                baseline=compat.baseline,
                note=f"{name} {detected}: {issue.text}",
            )
    if _starts(detected, compat.tested):
        return VersionAssessment(
            detected=detected,
            status="tested",
            tested=compat.tested,
            baseline=compat.baseline,
            note=f"{name} {detected} is a tested version.",
        )
    return VersionAssessment(
        detected=detected,
        status="untested",
        tested=compat.tested,
        baseline=compat.baseline,
        note=(
            f"{name} {detected} has not been tested with darkroom_ai (tested: "
            f"{', '.join(v + '.x' for v in compat.tested)}). It may work; check results "
            "carefully and run `darkroom selftest`."
        ),
    )


def _numbers(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def plugin_is_recent_enough(version: str, data: Compatibility | None = None) -> bool:
    minimum = (data or load_compatibility()).plugin.min_version
    return _numbers(version) >= _numbers(minimum)


# The plug-in <-> server contract. Bump EXPECTED_PROTOCOL together with PROTOCOL_VERSION in
# Commands.lua when a command's request or reply shape changes incompatibly.
EXPECTED_PROTOCOL = 1

# Commands the server sends that the plug-in running inside Lightroom must have registered.
# tests/unit/test_compat.py checks this list against Commands.lua, so it cannot drift silently.
REQUIRED_PLUGIN_COMMANDS: frozenset[str] = frozenset(
    {
        "apply_preset",
        "apply_settings",
        "add_to_collection",
        "apply_snapshot",
        "catalog_info",
        "collection_photos",
        "create_collection",
        "create_collection_set",
        "create_keyword",
        "delete_collection",
        "remove_from_collection",
        "create_mask",
        "create_snapshot",
        "create_virtual_copy",
        "export_preview",
        "export_tiff",
        "find_photo",
        "get_library_state",
        "get_metadata",
        "get_photo",
        "get_preset",
        "get_selection",
        "import_folder",
        "inspect_masks",
        "list_commands",
        "list_photos",
        "list_presets",
        "list_snapshots",
        "mask_operation",
        "ping",
        "probe",
        "scan_folder",
        "set_library_state",
        "selftest",
        "set_mask_values",
    }
)


def missing_plugin_commands(registered: Iterable[str]) -> list[str]:
    """Required commands the running plug-in does not have (empty when it is complete)."""
    return sorted(REQUIRED_PLUGIN_COMMANDS - set(registered))
