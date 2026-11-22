"""Stale-code guard and the registry of running connectors (no real processes involved)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from darkroom_ai.config import Settings
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.runtime import (
    CodeGuard,
    InstanceRecord,
    StaleWatchdog,
    code_hash,
    list_instances,
    register,
    stop_stale,
)
from darkroom_ai.tools.context import ToolContext
from darkroom_ai.tools.registry import TOOLS, ToolRegistry
from fakes import FakeLightroom, InMemoryJournal


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_package(tmp_path: Path) -> Path:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "a.py").write_text("x = 1\n", encoding="utf-8")
    return package


def test_code_hash_changes_with_the_source(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    first = code_hash(package)
    assert code_hash(package) == first
    (package / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert code_hash(package) != first


def test_guard_turns_stale_when_files_change_after_start(tmp_path: Path) -> None:
    package, clock = make_package(tmp_path), Clock()
    guard = CodeGuard(package, clock=clock, recheck_s=5.0)
    assert guard.stale is False and guard.warning() is None
    (package / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert guard.stale is False  # throttled: not looked at again yet
    clock.now = 6.0
    assert guard.stale is True
    assert "OLDER code" in (guard.warning() or "")
    assert guard.state().state == "stale"


class FakeControl:
    """Processes as (pid -> creation time); terminating removes them."""

    def __init__(self, running: dict[int, int], *, refuses: set[int] | None = None) -> None:
        self.running = dict(running)
        self.refuses = refuses or set()
        self.terminated: list[int] = []

    def create_time(self, pid: int) -> int | None:
        return self.running.get(pid)

    def terminate(self, pid: int) -> bool:
        if pid in self.refuses:
            return False
        self.terminated.append(pid)
        self.running.pop(pid, None)
        return True


def record(pid: int, version: str, created: int = 100) -> InstanceRecord:
    return InstanceRecord(pid=pid, created=created, started="t", version=version)


def test_only_running_connectors_with_different_code_are_stopped(tmp_path: Path) -> None:
    directory = tmp_path / "connectors"
    for item in (record(1, "old"), record(2, "new"), record(3, "old"), record(4, "old")):
        register(directory, item)
    # 3 is not running any more; 4's pid now belongs to a different process (reused pid)
    control = FakeControl({1: 100, 2: 100, 4: 999, 10: 5})
    stopped = stop_stale(directory, control, own_version="new", own_pid=10, sleep=lambda _s: None)
    assert [r.pid for r in stopped] == [1]
    assert control.terminated == [1]  # same code (2), dead (3) and reused pid (4) are untouched
    assert 4 not in control.terminated
    assert sorted(p.name for p in directory.glob("*.json")) == ["2.json"]


def test_an_empty_own_version_stops_every_connector(tmp_path: Path) -> None:
    directory = tmp_path / "connectors"
    for item in (record(1, "old"), record(2, "new")):
        register(directory, item)
    control = FakeControl({1: 100, 2: 100, 10: 5})
    stopped = stop_stale(directory, control, own_version="", own_pid=10, sleep=lambda _s: None)
    assert sorted(r.pid for r in stopped) == [1, 2]


def test_a_connector_that_cannot_be_stopped_stays_registered(tmp_path: Path) -> None:
    directory = tmp_path / "connectors"
    register(directory, record(1, "old"))
    control = FakeControl({1: 100}, refuses={1})
    assert stop_stale(directory, control, own_version="new", own_pid=10) == []
    assert [r.pid for r in list_instances(directory, control)] == [1]


def make_registry(tmp_path: Path, guard: CodeGuard | None, *, refuse: bool = True) -> ToolRegistry:
    settings = Settings.model_validate({"runtime": {"refuse_writes_when_stale": refuse}})
    context = ToolContext(
        settings=settings,
        lightroom=FakeLightroom(tmp_path),
        journal=InMemoryJournal(),
        connector=guard,
    )
    return ToolRegistry(context, TOOLS)


def stale_guard(tmp_path: Path) -> CodeGuard:
    package, clock = make_package(tmp_path), Clock()
    guard = CodeGuard(package, clock=clock, recheck_s=0.0)
    (package / "a.py").write_text("x = 2\n", encoding="utf-8")
    return guard


def test_stale_connector_refuses_writes_but_still_reads(tmp_path: Path) -> None:
    registry = make_registry(tmp_path, stale_guard(tmp_path))
    with pytest.raises(DarkroomError) as caught:
        registry.call("undo", {"photo_id": "x"})
    assert caught.value.code is ErrorCode.CONNECTOR_STALE
    assert "Nothing was changed" in caught.value.message
    status = registry.call("get_app_status", {})
    assert status["connector"]["state"] == "stale"
    assert "OLDER code" in status["connector_warning"]


def test_stale_warning_only_setting_lets_writes_through(tmp_path: Path) -> None:
    registry = make_registry(tmp_path, stale_guard(tmp_path), refuse=False)
    with pytest.raises(DarkroomError) as caught:
        registry.call("undo", {"photo_id": "x"})
    assert caught.value.code is not ErrorCode.CONNECTOR_STALE


def test_current_connector_adds_nothing_to_results(tmp_path: Path) -> None:
    guard = CodeGuard(make_package(tmp_path))
    registry = make_registry(tmp_path, guard)
    status: dict[str, Any] = registry.call("get_app_status", {})
    assert status["connector"]["state"] == "current"
    assert "connector_warning" not in status


# -- the stale watchdog ---------------------------------------------------------------------------


def watchdog_rig(
    tmp_path: Path, grace: float = 120, idle: float = 30
) -> tuple[Path, Clock, StaleWatchdog]:
    package = make_package(tmp_path)
    clock = Clock()
    guard = CodeGuard(package, clock=clock, recheck_s=0)
    return package, clock, StaleWatchdog(guard, grace_s=grace, idle_s=idle, clock=clock)


def test_a_current_connector_never_exits(tmp_path: Path) -> None:
    _, clock, dog = watchdog_rig(tmp_path)
    clock.now = 100000
    assert dog.should_exit() is False


def test_a_stale_idle_connector_exits_only_after_the_grace_period(tmp_path: Path) -> None:
    package, clock, dog = watchdog_rig(tmp_path)
    (package / "a.py").write_text("x = 2\n", encoding="utf-8")
    clock.now = 10
    assert dog.should_exit() is False  # just noticed
    clock.now = 129
    assert dog.should_exit() is False
    clock.now = 131
    assert dog.should_exit() is True


def test_a_running_or_recent_call_postpones_the_exit(tmp_path: Path) -> None:
    package, clock, dog = watchdog_rig(tmp_path)
    (package / "a.py").write_text("x = 2\n", encoding="utf-8")
    clock.now = 0
    dog.should_exit()
    clock.now = 500
    dog.call_started()
    assert dog.should_exit() is False  # a call is running
    clock.now = 510
    dog.call_finished()
    clock.now = 530
    assert dog.should_exit() is False  # finished only 20 s ago
    clock.now = 541
    assert dog.should_exit() is True


def test_reverting_the_edit_resets_the_grace_period(tmp_path: Path) -> None:
    package, clock, dog = watchdog_rig(tmp_path)
    (package / "a.py").write_text("x = 2\n", encoding="utf-8")
    clock.now = 0
    dog.should_exit()
    (package / "a.py").write_text("x = 1\n", encoding="utf-8")
    clock.now = 100
    assert dog.should_exit() is False  # current again
    (package / "a.py").write_text("x = 3\n", encoding="utf-8")
    clock.now = 101
    dog.should_exit()
    clock.now = 200
    assert dog.should_exit() is False  # only 99 s stale since the new edit


def test_zero_grace_turns_the_exit_off(tmp_path: Path) -> None:
    package, clock, dog = watchdog_rig(tmp_path, grace=0)
    (package / "a.py").write_text("x = 2\n", encoding="utf-8")
    clock.now = 100000
    assert dog.should_exit() is False


def test_the_watchdog_defaults_are_on() -> None:
    runtime = Settings().runtime
    assert (runtime.stale_exit_after_s, runtime.stale_exit_idle_s) == (120, 30)
