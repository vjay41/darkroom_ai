"""get_app_status / ensure_apps_running with fake programs (nothing is really started)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_tools import make_settings

from darkroom_ai.config import AppsSettings
from darkroom_ai.tools import app_status
from darkroom_ai.tools.context import ToolContext
from darkroom_ai.tools.registry import ToolRegistry
from fakes import FakeLightroom, FakePhotoshop, InMemoryJournal


class FakeProcesses:
    def __init__(self, running: dict[str, bool | None]) -> None:
        self.running = dict(running)
        self.started: list[tuple[Path, list[str]]] = []

    def is_running(self, image_name: str) -> bool | None:
        return self.running.get(image_name)

    def start(self, exe: Path, arguments: list[str] | None = None) -> None:
        self.started.append((exe, list(arguments or [])))
        self.running[exe.name] = True


def _registry(
    tmp_path: Path, processes: FakeProcesses, *, photoshop_exe: Path | None = None
) -> ToolRegistry:
    settings = make_settings(tmp_path)
    if photoshop_exe is not None:
        settings = settings.model_copy(
            update={"apps": AppsSettings(photoshop_exe=str(photoshop_exe))}
        )
    context = ToolContext(
        settings=settings,
        lightroom=FakeLightroom(tmp_path),
        journal=InMemoryJournal(),
        photoshop=FakePhotoshop(tmp_path / "outputs" / "photoshop"),
        processes=processes,
    )
    return ToolRegistry(context)


def test_status_reports_each_app_and_never_starts_anything(tmp_path: Path) -> None:
    processes = FakeProcesses({"Lightroom.exe": True, "Photoshop.exe": False})
    got = _registry(tmp_path, processes).call("get_app_status", {})
    assert got["lightroom"]["state"] == "ready"
    assert got["lightroom"]["facts"]["edits_allowed_in_this_catalog"] is True
    assert got["photoshop"]["state"] == "not_running"
    assert "ensure_apps_running" in got["photoshop"]["next_step"]
    assert got["all_ready"] is False and processes.started == []


def test_ensure_starts_only_a_missing_app_and_waits_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_status, "_sleep", lambda _s: None)
    exe = tmp_path / "Photoshop.exe"
    exe.write_bytes(b"")
    processes = FakeProcesses({"Lightroom.exe": True, "Photoshop.exe": False})
    registry = _registry(tmp_path, processes, photoshop_exe=exe)
    got: dict[str, Any] = registry.call("ensure_apps_running", {"app": "photoshop"})
    assert got["started"] is True and got["status"]["state"] == "ready"
    assert [path.name for path, _ in processes.started] == ["Photoshop.exe"]
    again = registry.call("ensure_apps_running", {"app": "photoshop"})
    assert again["started"] is False  # already running: never restarted


def test_missing_program_file_is_reported_not_started(tmp_path: Path) -> None:
    processes = FakeProcesses({"Lightroom.exe": False})
    got = _registry(tmp_path, processes).call("ensure_apps_running", {"app": "lightroom"})
    assert got["started"] is False and got["status"]["state"] == "not_installed"
    assert processes.started == []


def test_busy_bridge_is_reported_with_how_long_and_what_to_do(tmp_path: Path) -> None:
    from darkroom_ai.adapters.lightroom.models import BridgeStatus

    registry = _registry(tmp_path, FakeProcesses({"Lightroom.exe": True, "Photoshop.exe": False}))
    stuck = BridgeStatus(
        busy={"command": "create_mask", "running_s": 130.0, "budget_s": 90.0, "over_budget": True}
    )
    registry._context.lightroom.bridge_status = lambda: stuck  # type: ignore[method-assign]
    got = registry.call("get_app_status", {})["lightroom"]
    assert got["state"] == "busy" and "create_mask" in got["detail"]
    assert "dialog" in got["next_step"]
