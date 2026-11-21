"""Compatibility data, get_capabilities and the selftest (all against fakes)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_tools import make_settings

from darkroom_ai.compat import (
    Compatibility,
    assess_version,
    load_compatibility,
    plugin_is_recent_enough,
)
from darkroom_ai.config import GuardrailSettings
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.selftest import expected_keys, format_report, run_selftest
from darkroom_ai.tools.context import ToolContext
from darkroom_ai.tools.registry import TOOLS, ToolRegistry
from fakes import AllowAll, FakeLightroom, FakePhotoshop, InMemoryJournal


def test_data_file_loads_and_every_tool_named_in_it_exists() -> None:
    data = load_compatibility()
    names = {tool.name for tool in TOOLS}
    for feature in data.features:
        assert set(feature.tools) <= names, feature.name


def test_version_judgement_warns_but_never_refuses() -> None:
    assert assess_version("lightroom", "15.2.1").status == "tested"
    assert assess_version("lightroom", "15.3.0").status == "untested"
    assert "15.2.x" in assess_version("lightroom", "16.0").note
    assert assess_version("photoshop", "27.8.0").status == "tested"
    assert assess_version("photoshop", "28.0").status == "untested"
    assert assess_version("photoshop", None).status == "unknown"
    # 15.20 is not 15.2
    assert assess_version("lightroom", "15.20").status == "untested"


def test_known_issue_is_reported_for_its_versions() -> None:
    data = Compatibility.model_validate(
        {
            "apps": {
                "lightroom": {
                    "baseline": "15.2.1",
                    "tested": ["15.2"],
                    "known_issues": [{"versions": ["15.3"], "text": "masks drift"}],
                },
                "photoshop": {"baseline": "27.8", "tested": ["27.8"]},
            },
            "plugin": {"min_version": "0.9.0"},
            "features": [],
        }
    )
    got = assess_version("lightroom", "15.3.1", data)
    assert got.status == "known_issue" and "masks drift" in got.note


def test_plugin_minimum_compares_numbers_not_text() -> None:
    assert plugin_is_recent_enough("0.9.1") and plugin_is_recent_enough("0.10.0")
    assert not plugin_is_recent_enough("0.8.9")


def _registry(tmp_path: Path) -> ToolRegistry:
    context = ToolContext(
        settings=make_settings(tmp_path),
        lightroom=FakeLightroom(tmp_path),
        journal=InMemoryJournal(),
        photoshop=FakePhotoshop(tmp_path / "outputs" / "photoshop"),
        processes=_Processes(True),
    )
    return ToolRegistry(context)


def test_get_capabilities_lists_what_is_not_verified(tmp_path: Path) -> None:
    got = _registry(tmp_path).call("get_capabilities", {})
    assert got["photoshop"]["status"] == "tested"
    assert got["plugin"]["version"] is not None
    flagged = " ".join(got["not_verified"])
    assert "RAW" in flagged and "People mask" in flagged and "Colour range" in flagged
    assert any(f["status"] == "verified" for f in got["features"])


def test_get_capabilities_survives_apps_that_do_not_answer(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    def down() -> None:
        raise DarkroomError(ErrorCode.LR_NOT_REACHABLE, "no answer")

    registry._context.lightroom.ping = down  # type: ignore[method-assign,assignment]
    got = registry.call("get_capabilities", {})
    assert got["lightroom"]["status"] == "unknown" and got["plugin"]["version"] is None


def test_selftest_read_only_passes_and_skips_the_write_probe(tmp_path: Path) -> None:
    report = run_selftest(FakeLightroom(tmp_path), FakePhotoshop(tmp_path), GuardrailSettings())
    by_name = {p.name: p for p in report.probes}
    assert not report.failed
    assert by_name["develop key map"].status == "pass"
    assert by_name["write probe"].status == "skip" and report.wrote is False
    assert "RESULT: all probes passed" in format_report(report)


def test_selftest_write_probe_uses_a_virtual_copy_and_restores(tmp_path: Path) -> None:
    lightroom = FakeLightroom(tmp_path)
    report = run_selftest(lightroom, None, GuardrailSettings(), write=True)
    probe = next(p for p in report.probes if p.name == "write probe")
    assert probe.status == "pass" and report.wrote is True


def test_selftest_reports_a_renamed_develop_key(tmp_path: Path) -> None:
    lightroom = FakeLightroom(tmp_path)
    original = lightroom.get_develop_settings

    def renamed(photo_id: str, keys: object = None) -> dict[str, object]:
        got = dict(original(photo_id, keys))  # type: ignore[arg-type]
        got.pop("Highlights2012", None)
        return got

    lightroom.get_develop_settings = renamed  # type: ignore[method-assign,assignment]
    report = run_selftest(lightroom, None, GuardrailSettings())
    probe = next(p for p in report.probes if p.name == "develop key map")
    assert probe.status == "fail" and "Highlights2012" in probe.detail
    assert "develop_keys.py" in probe.next_step


def test_selftest_stops_with_a_plain_error_when_lightroom_is_down(tmp_path: Path) -> None:
    lightroom = FakeLightroom(tmp_path)

    def down() -> None:
        raise DarkroomError(ErrorCode.LR_NOT_REACHABLE, "no answer")

    lightroom.ping = down  # type: ignore[method-assign,assignment]
    report = run_selftest(lightroom, None, GuardrailSettings())
    assert [p.status for p in report.probes] == ["fail"] and report.probes[0].next_step


def test_expected_keys_cover_the_key_map() -> None:
    keys = expected_keys(GuardrailSettings())
    assert {"Exposure2012", "CropLeft", "HueAdjustmentRed", "MaskGroupBasedCorrections"} <= set(
        keys
    )


def test_run_selftest_tool_reads_without_asking(tmp_path: Path) -> None:
    got = _registry(tmp_path).call("run_selftest", {})
    assert got["all_passed"] is True and got["wrote_a_virtual_copy"] is False
    assert {p["name"] for p in got["probes"]} >= {"develop key map", "write probe"}


def test_run_selftest_write_mode_needs_a_go_ahead(tmp_path: Path) -> None:
    with pytest.raises(DarkroomError) as caught:
        _registry(tmp_path).call("run_selftest", {"write": True})
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    context = ToolContext(
        settings=make_settings(tmp_path),
        lightroom=FakeLightroom(tmp_path),
        journal=InMemoryJournal(),
        confirmer=AllowAll(),
    )
    got = ToolRegistry(context).call("run_selftest", {"write": True})
    assert got["wrote_a_virtual_copy"] is True and got["all_passed"] is True


class _Processes:
    def __init__(self, running: bool | None) -> None:
        self.running = running

    def is_running(self, image_name: str) -> bool | None:
        return self.running

    def start(self, exe: Path, arguments: list[str] | None = None) -> None:
        raise AssertionError("a read-only tool must never start an app")


def test_capabilities_never_asks_a_photoshop_that_is_not_running(tmp_path: Path) -> None:
    class Exploding(FakePhotoshop):
        def info(self):  # type: ignore[no-untyped-def]
            raise AssertionError("Photoshop would have been started")

    for running in (False, None):
        context = ToolContext(
            settings=make_settings(tmp_path),
            lightroom=FakeLightroom(tmp_path),
            journal=InMemoryJournal(),
            photoshop=Exploding(tmp_path / "ps"),
            processes=_Processes(running),
        )
        got = ToolRegistry(context).call("get_capabilities", {})
        assert got["photoshop"]["status"] == "unknown"
    context = ToolContext(
        settings=make_settings(tmp_path),
        lightroom=FakeLightroom(tmp_path),
        journal=InMemoryJournal(),
        photoshop=FakePhotoshop(tmp_path / "ps"),
        processes=_Processes(True),
    )
    assert ToolRegistry(context).call("get_capabilities", {})["photoshop"]["status"] == "tested"
