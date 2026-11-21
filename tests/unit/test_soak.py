"""The soak runner, against the simulated Lightroom."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from test_tools import Rig

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.soak import format_soak, run_soak


def _call(rig: Rig) -> Any:
    return lambda name, args: rig.tools.call(name, args)


def test_soak_passes_cycles_and_leaves_no_edit_or_mask_behind(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    report = run_soak(_call(rig), cycles=3)
    assert not report.failed, [s.detail for s in report.failed]
    names = {s.name for s in report.steps}
    assert {"edit + read back", "undo + check", "create gradient mask", "delete mask"} <= names
    assert "RESULT: no failures" in format_soak(report)
    copy = rig.context.working_copies["master-1"]
    assert rig.lightroom.settings[copy].get("Exposure2012", 0.0) == 0.0
    assert rig.call("list_masks", photo_id="master-1")["count"] == 0


def test_soak_reports_a_tool_that_fails_and_keeps_going(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    real = rig.tools.call

    def flaky(name: str, args: Any) -> Any:
        if name == "get_preview":
            raise DarkroomError(ErrorCode.LR_COMMAND_FAILED, "render failed")
        return real(name, args)

    report = run_soak(flaky, cycles=2)
    bad = report.failed
    assert [s.name for s in bad] == ["get_preview", "get_preview"]
    assert "render failed" in bad[0].detail
    assert any(s.name == "delete mask" and s.ok for s in report.steps)
    assert "failed step" in format_soak(report)


def test_soak_catches_an_undo_that_does_not_restore(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    real = rig.tools.call

    def lossy_undo(name: str, args: Any) -> Any:
        return {"undone_steps": 1} if name == "undo" else real(name, args)

    report = run_soak(lossy_undo, cycles=1)
    assert [s.name for s in report.failed] == ["undo + check"]
