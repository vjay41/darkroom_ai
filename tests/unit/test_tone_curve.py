"""Tone curve tools: guardrail limits, validation, undo (against the in-memory Lightroom)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_tools import MASTER, Rig

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.tone_curve import CurvePoint, limit_points

IDENTITY = [0, 0, 255, 255]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def pts(*pairs: tuple[float, float]) -> list[dict[str, float]]:
    return [{"x": x, "y": y} for x, y in pairs]


def ys(result: dict[str, Any], field: str = "applied") -> list[float]:
    return [p["y"] for p in result[field]]


def test_a_photo_without_a_curve_reads_as_a_straight_line(rig: Rig) -> None:
    reading = rig.call("get_tone_curve", photo_id=MASTER)
    assert reading["channel"] == "rgb"
    assert reading["points"] == pts((0, 0), (255, 255))


def test_a_gentle_s_curve_is_applied_as_asked_and_read_back(rig: Rig) -> None:
    result = rig.call(
        "adjust_tone_curve",
        photo_id=MASTER,
        points=pts((0, 0), (64, 50), (192, 205), (255, 255)),
    )
    assert result["clamped"] is False
    assert result["working_copy_created"] is True
    assert ys(result) == [0, 50, 205, 255]
    assert result["measured"]["source"]
    copy = result["photo_id"]
    assert rig.value(copy, "ToneCurvePV2012") == [0, 0, 64, 50, 192, 205, 255, 255]
    assert rig.call("get_tone_curve", photo_id=copy)["points"] == result["applied"]
    assert rig.value(MASTER, "ToneCurvePV2012") == IDENTITY  # the original is never touched


def test_a_big_move_is_limited_and_the_limit_is_reported(rig: Rig) -> None:
    result = rig.call(
        "adjust_tone_curve", photo_id=MASTER, points=pts((0, 0), (64, 200), (255, 255))
    )
    assert result["clamped"] is True
    assert ys(result) == [0, 96, 255]  # 64 + the per-pass limit of 32
    assert "Limited" in result["note"] and "asked 200" in result["note"]
    assert result["requested"][1]["y"] == 200


def test_the_total_limit_holds_across_passes(rig: Rig) -> None:
    first = rig.call(
        "adjust_tone_curve", photo_id=MASTER, points=pts((0, 0), (128, 160), (255, 255))
    )
    second = rig.call(
        "adjust_tone_curve", photo_id=first["photo_id"], points=pts((0, 0), (128, 250), (255, 255))
    )
    third = rig.call(
        "adjust_tone_curve", photo_id=first["photo_id"], points=pts((0, 0), (128, 250), (255, 255))
    )
    assert ys(first)[1] == 160
    assert ys(second)[1] == 192  # 128 + the total limit of 64
    assert third["no_change"] is True


def test_a_curve_never_folds_back(rig: Rig) -> None:
    result = rig.call(
        "adjust_tone_curve",
        photo_id=MASTER,
        points=pts((0, 0), (100, 120), (150, 100), (255, 255)),
    )
    values = ys(result)
    assert values == sorted(values)
    assert result["clamped"] is True


def test_the_same_curve_again_changes_nothing(rig: Rig) -> None:
    rig.call("adjust_tone_curve", photo_id=MASTER, points=pts((0, 0), (128, 140), (255, 255)))
    calls = len(rig.lightroom.calls)
    again = rig.call(
        "adjust_tone_curve", photo_id=MASTER, points=pts((0, 0), (128, 140), (255, 255))
    )
    assert again["no_change"] is True
    assert "apply_develop_settings" not in rig.lightroom.calls[calls:]


def test_a_colour_channel_changes_only_its_own_curve(rig: Rig) -> None:
    result = rig.call(
        "adjust_tone_curve",
        photo_id=MASTER,
        channel="red",
        points=pts((0, 0), (128, 140), (255, 255)),
    )
    copy = result["photo_id"]
    assert rig.value(copy, "ToneCurvePV2012Red") == [0, 0, 128, 140, 255, 255]
    assert rig.value(copy, "ToneCurvePV2012") == IDENTITY
    assert rig.value(copy, "ToneCurvePV2012Blue") == IDENTITY


def test_undo_puts_the_previous_curve_back(rig: Rig) -> None:
    result = rig.call(
        "adjust_tone_curve", photo_id=MASTER, points=pts((0, 0), (128, 150), (255, 255))
    )
    copy = result["photo_id"]
    undone = rig.call("undo", photo_id=copy)
    assert undone["undone_steps"] == 1
    assert rig.value(copy, "ToneCurvePV2012") == IDENTITY


@pytest.mark.parametrize(
    "points",
    [
        [{"x": 0, "y": 0}],  # too few
        pts((0, 0), (128, 128), (128, 140), (255, 255)),  # x does not increase
        pts((10, 10), (255, 255)),  # does not start at 0
        pts((0, 0), (200, 200)),  # does not end at 255
        pts((0, 0), (128, 300), (255, 255)),  # y out of range
        [{"x": i * 15, "y": i * 15} for i in range(18)],  # too many
        [{"x": 0, "y": 0, "z": 1}, {"x": 255, "y": 255}],  # unknown field
    ],
)
def test_malformed_curves_are_rejected_before_anything_changes(
    rig: Rig, points: list[dict[str, float]]
) -> None:
    calls = len(rig.lightroom.calls)
    with pytest.raises(DarkroomError) as caught:
        rig.call("adjust_tone_curve", photo_id=MASTER, points=points)
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL
    assert "create_virtual_copy" not in rig.lightroom.calls[calls:]


def test_limit_points_clamps_against_the_current_curve_and_the_straight_line() -> None:
    straight = [CurvePoint(x=0, y=0), CurvePoint(x=255, y=255)]
    wanted = [CurvePoint(x=0, y=0), CurvePoint(x=100, y=250), CurvePoint(x=255, y=255)]
    assert [p.y for p in limit_points(wanted, straight, 255.0, 64.0)] == [0, 164, 255]
    assert [p.y for p in limit_points(wanted, straight, 10.0, 64.0)] == [0, 110, 255]
