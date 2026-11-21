"""Tool layer and guardrails, against in-memory fakes (no Adobe apps)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from darkroom_ai.config import Settings, parse_settings
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.masks import SyntheticMaskProvider
from darkroom_ai.interfaces import LightroomAdapter, PhotoshopAdapter
from darkroom_ai.safety.guardrails import Limit, clamp_change
from darkroom_ai.tools.background import resolve_background
from darkroom_ai.tools.context import ToolContext
from darkroom_ai.tools.registry import TOOLS, UNTESTED_TOOLS, ToolRegistry
from fakes import AllowAll, FakeLightroom, FakePhotoshop, InMemoryJournal

MASTER = "master-1"


def make_settings(tmp_path: Path, **sections: Any) -> Settings:
    data: dict[str, Any] = {
        "llm": {
            "active_profile": "m",
            "profiles": {"m": {"base_url": "http://127.0.0.1:1234/v1", "model": "x"}},
        },
        "backgrounds": {"dir": str(tmp_path / "backgrounds")},
        "masks": {"backend": "synthetic"},
    }
    data.update(sections)
    return parse_settings(data)


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        *,
        file_format: str = "JPG",
        confirmer: Any = None,
        with_untested: bool = False,
    ) -> None:
        self.tmp_path = tmp_path
        self.lightroom = FakeLightroom(tmp_path, file_format=file_format)
        self.photoshop = FakePhotoshop(tmp_path / "outputs" / "photoshop")
        self.journal = InMemoryJournal()
        lightroom: LightroomAdapter = self.lightroom
        photoshop: PhotoshopAdapter = self.photoshop
        kwargs: dict[str, Any] = {"confirmer": confirmer} if confirmer else {}
        self.context = ToolContext(
            settings=make_settings(tmp_path),
            lightroom=lightroom,
            journal=self.journal,
            photoshop=photoshop,
            masks=SyntheticMaskProvider(),
            **kwargs,
        )
        tools = TOOLS + UNTESTED_TOOLS if with_untested else TOOLS
        self.tools = ToolRegistry(self.context, tools)

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        return self.tools.call(name, arguments)

    def value(self, photo_id: str, key: str) -> Any:
        return self.lightroom.settings[photo_id][key]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def change(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in result["changes"] if c["name"] == name)


# -- registry -------------------------------------------------------------------------------------


def test_registry_lists_the_v1_tools_with_schemas(rig: Rig) -> None:
    assert rig.tools.names == (
        "get_photo_info",
        "analyze_photo",
        "adjust_lighting",
        "adjust_white_balance",
        "adjust_hsl",
        "crop",
        "replace_background",
        "undo",
    )
    assert "run_photoshop_script" not in rig.tools.names  # left out of v1 on purpose
    assert "apply_preset" not in rig.tools.names  # not exposed until tested live
    with pytest.raises(DarkroomError) as caught:
        rig.call("apply_preset", photo_id=MASTER, preset_name="Soft Warm")
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL
    schemas = rig.tools.schemas()
    json.dumps(schemas)  # must be plain JSON
    for schema in schemas:
        assert schema["description"]
        assert "photo_id" in schema["parameters"]["properties"]
        assert schema["parameters"]["additionalProperties"] is False


def test_unknown_tool_and_bad_arguments_are_reported_and_journaled(rig: Rig) -> None:
    with pytest.raises(DarkroomError) as caught:
        rig.call("delete_everything", photo_id=MASTER)
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL

    for arguments in (
        {"photo_id": MASTER},  # nothing requested
        {"photo_id": MASTER, "exposure": "a lot"},
        {"photo_id": MASTER, "exposure": 0.2, "magic": 1},  # unknown field
        {"exposure": 0.2},  # no photo
    ):
        with pytest.raises(DarkroomError) as caught:
            rig.tools.call("adjust_lighting", arguments)
        assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL
    errors = [e for e in rig.journal.entries() if e.status == "error"]
    assert len(errors) == 5
    assert rig.lightroom.calls == []  # nothing reached Lightroom


# -- virtual copies, journal ----------------------------------------------------------------------


def test_edit_on_a_master_goes_to_one_virtual_copy(rig: Rig) -> None:
    first = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.4)
    copy_id = first["photo_id"]
    assert first["working_copy_created"] is True
    assert copy_id != MASTER
    assert rig.value(copy_id, "Exposure2012") == pytest.approx(0.4)
    assert rig.value(MASTER, "Exposure2012") == 0  # the master is never edited

    second = rig.call("adjust_lighting", photo_id=MASTER, contrast=10)
    assert second["photo_id"] == copy_id
    assert second["working_copy_created"] is False
    assert rig.lightroom.calls.count("create_virtual_copy") == 1

    tools = [entry.tool for entry in rig.journal.entries()]
    assert tools == ["create_virtual_copy", "adjust_lighting", "adjust_lighting"]
    entry = rig.journal.entries()[1]
    assert entry.id == first["journal_id"]
    assert entry.args == {"photo_id": MASTER, "exposure": 0.4}
    assert entry.undo == {"photo_id": copy_id, "settings": {"Exposure2012": 0.0}}


def test_working_copy_is_reused_by_a_later_session(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.2)["photo_id"]
    later = ToolRegistry(
        ToolContext(settings=rig.context.settings, lightroom=rig.lightroom, journal=rig.journal)
    )
    again = later.call("adjust_lighting", {"photo_id": MASTER, "exposure": 0.1})
    assert again["photo_id"] == copy_id
    assert rig.lightroom.calls.count("create_virtual_copy") == 1


# -- clamping -------------------------------------------------------------------------------------


def test_per_pass_limit_reports_requested_and_applied(rig: Rig) -> None:
    result = rig.call("adjust_lighting", photo_id=MASTER, exposure=2.5, shadows=20)
    exposure = change(result, "exposure")
    assert exposure["requested_change"] == 2.5
    assert exposure["applied_change"] == 1.5
    assert exposure["limited_by"] == ["per_pass"]
    assert change(result, "shadows")["limited_by"] == []
    assert result["clamped"] is True
    assert "asked +2.5, applied +1.5" in result["note"]
    assert rig.value(result["photo_id"], "Exposure2012") == 1.5


def test_total_limit_against_the_original(rig: Rig) -> None:
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=1.5)["photo_id"]
    rig.call("adjust_lighting", photo_id=MASTER, exposure=1.5)
    third = rig.call("adjust_lighting", photo_id=MASTER, exposure=1.0)
    exposure = change(third, "exposure")
    assert exposure["original"] == 0
    assert exposure["before"] == 3.0
    assert exposure["applied_change"] == 0
    assert exposure["limited_by"] == ["total"]
    assert rig.value(copy_id, "Exposure2012") == 3.0
    # Moving back towards the original is always possible.
    back = rig.call("adjust_lighting", photo_id=MASTER, exposure=-1.0)
    assert change(back, "exposure")["limited_by"] == []
    assert rig.value(copy_id, "Exposure2012") == 2.0


def test_going_beyond_the_total_needs_confirmation(tmp_path: Path) -> None:
    denied = Rig(tmp_path / "a")
    denied.call("adjust_lighting", photo_id=MASTER, exposure=1.5)
    copy_id = denied.call("adjust_lighting", photo_id=MASTER, exposure=1.5)["photo_id"]
    with pytest.raises(DarkroomError) as caught:
        denied.call("adjust_lighting", photo_id=MASTER, exposure=1.0, allow_beyond_total=True)
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    assert denied.value(copy_id, "Exposure2012") == 3.0

    confirmer = AllowAll()
    allowed = Rig(tmp_path / "b", confirmer=confirmer)
    allowed.call("adjust_lighting", photo_id=MASTER, exposure=1.5)
    copy_id = allowed.call("adjust_lighting", photo_id=MASTER, exposure=1.5)["photo_id"]
    result = allowed.call("adjust_lighting", photo_id=MASTER, exposure=1.0, allow_beyond_total=True)
    assert change(result, "exposure")["after"] == 4.0
    assert confirmer.asked == ["go beyond the total edit limit for this photo"]
    # Without the flag the value may come back but not go further out.
    assert (
        change(allowed.call("adjust_lighting", photo_id=MASTER, exposure=0.5), "exposure")[
            "applied_change"
        ]
        == 0
    )
    assert allowed.value(copy_id, "Exposure2012") == 4.0


def test_slider_range_limit(rig: Rig) -> None:
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, contrast=50)["photo_id"]
    rig.call("adjust_lighting", photo_id=MASTER, contrast=40)
    result = rig.call("adjust_lighting", photo_id=MASTER, contrast=50)
    contrast = change(result, "contrast")
    assert contrast["applied_change"] == 10
    assert contrast["limited_by"] == ["range"]
    assert rig.value(copy_id, "Contrast2012") == 100


@pytest.mark.parametrize(
    ("current", "original", "requested", "allow", "after", "reasons"),
    [
        (0.0, 0.0, 0.5, False, 0.5, ()),
        (0.0, 0.0, 2.0, False, 1.5, ("per_pass",)),
        (2.5, 0.0, 1.0, False, 3.0, ("total",)),
        (2.5, 0.0, 1.0, True, 3.5, ()),
        (4.0, 0.0, 0.5, False, 4.0, ("total",)),  # already beyond: may not go further
        (4.0, 0.0, -0.5, False, 3.5, ()),  # ... but may come back
        (4.8, 0.0, 1.0, True, 5.0, ("range",)),
        (-2.5, 0.0, -1.5, False, -3.0, ("total",)),
    ],
)
def test_clamp_change(
    current: float, original: float, requested: float, allow: bool, after: float, reasons: tuple
) -> None:
    limit = Limit("exposure", "Exposure2012", -5.0, 5.0, per_pass=1.5, total=3.0)
    result = clamp_change(
        limit,
        current=current,
        original=original,
        requested_change=requested,
        allow_beyond_total=allow,
    )
    assert result.after == pytest.approx(after)
    assert result.limited_by == reasons
    assert result.applied_change == pytest.approx(after - current)
    assert result.requested_change == requested


# -- white balance, HSL, process version ----------------------------------------------------------


def test_white_balance_on_jpg_uses_the_relative_sliders(rig: Rig) -> None:
    result = rig.call("adjust_white_balance", photo_id=MASTER, temperature=60, tint=-10)
    temperature = change(result, "temperature")
    assert temperature["key"] == "IncrementalTemperature"
    assert temperature["applied_change"] == 30  # per-pass limit on the -100..100 scale
    assert change(result, "tint")["key"] == "IncrementalTint"
    assert rig.value(result["photo_id"], "IncrementalTemperature") == 30
    assert rig.value(result["photo_id"], "Temperature") == 5500  # Kelvin value untouched


def test_white_balance_on_raw_uses_kelvin(tmp_path: Path) -> None:
    rig = Rig(tmp_path, file_format="RAW")
    result = rig.call("adjust_white_balance", photo_id=MASTER, temperature=2500)
    temperature = change(result, "temperature")
    assert temperature["key"] == "Temperature"
    assert temperature["applied_change"] == 1000
    assert rig.value(result["photo_id"], "Temperature") == 6500
    assert rig.value(result["photo_id"], "IncrementalTemperature") == 0


def test_hsl_adjustments(rig: Rig) -> None:
    result = rig.call(
        "adjust_hsl",
        photo_id=MASTER,
        adjustments=[
            {"color": "orange", "saturation": -45, "luminance": 10},
            {"color": "blue", "hue": 5},
        ],
    )
    keys = {c["key"]: c["applied_change"] for c in result["changes"]}
    assert keys == {
        "SaturationAdjustmentOrange": -30,
        "LuminanceAdjustmentOrange": 10,
        "HueAdjustmentBlue": 5,
    }
    with pytest.raises(DarkroomError) as caught:
        rig.call(
            "adjust_hsl",
            photo_id=MASTER,
            adjustments=[{"color": "red", "hue": 1}, {"color": "red", "hue": 2}],
        )
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    with pytest.raises(DarkroomError) as caught:
        rig.call("adjust_hsl", photo_id=MASTER, adjustments=[{"color": "pink", "hue": 1}])
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL


def test_old_process_version_is_refused(rig: Rig) -> None:
    rig.lightroom.settings[MASTER]["ProcessVersion"] = "5.7"
    with pytest.raises(DarkroomError) as caught:
        rig.call("adjust_lighting", photo_id=MASTER, exposure=0.2)
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "process version" in caught.value.message
    assert "apply_develop_settings" not in rig.lightroom.calls


# -- crop, presets, undo --------------------------------------------------------------------------


def test_crop_and_its_minimum(rig: Rig) -> None:
    with pytest.raises(DarkroomError) as caught:
        rig.call("crop", photo_id=MASTER, left=0.4, top=0.4, right=0.6, bottom=0.6)
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "4%" in caught.value.message
    with pytest.raises(DarkroomError) as caught:
        rig.call("crop", photo_id=MASTER, left=0.6, top=0.1, right=0.4, bottom=0.9)
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL

    result = rig.call("crop", photo_id=MASTER, left=0.1, top=0.1, right=0.9, bottom=0.8, angle=2)
    copy_id = result["photo_id"]
    assert result["kept_fraction"] == pytest.approx(0.56)
    assert rig.value(copy_id, "CropRight") == 0.9
    assert rig.value(copy_id, "CropAngle") == 2
    assert rig.value(MASTER, "CropRight") == 1
    rig.call("undo", photo_id=MASTER)
    assert [rig.value(copy_id, k) for k in ("CropLeft", "CropRight", "CropAngle")] == [0, 1, 0]


def test_apply_preset(tmp_path: Path) -> None:
    rig = Rig(tmp_path, with_untested=True)
    result = rig.call("apply_preset", photo_id=MASTER, preset_name="soft warm")
    copy_id = result["photo_id"]
    assert result["preset"] == "Soft Warm"
    assert result["changed"] == {"Exposure2012": 0.4, "Contrast2012": 10}
    assert rig.value(MASTER, "Exposure2012") == 0
    rig.call("undo", photo_id=MASTER)
    assert rig.value(copy_id, "Exposure2012") == 0
    assert rig.value(copy_id, "Contrast2012") == 0


def test_preset_beyond_the_total_limit_is_taken_back(tmp_path: Path) -> None:
    rig = Rig(tmp_path, with_untested=True)
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.5)["photo_id"]
    with pytest.raises(DarkroomError) as caught:
        rig.call("apply_preset", photo_id=MASTER, preset_name="Hard Push")
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "taken back" in caught.value.message
    assert rig.value(copy_id, "Exposure2012") == 0.5  # exactly as before the preset


def test_unknown_and_ambiguous_presets(tmp_path: Path) -> None:
    rig = Rig(tmp_path, with_untested=True)
    with pytest.raises(DarkroomError) as caught:
        rig.call("apply_preset", photo_id=MASTER, preset_name="Soft")
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert caught.value.details["similar"] == ["Soft Warm"]
    with pytest.raises(DarkroomError) as caught:
        rig.call("apply_preset", photo_id=MASTER, preset_name="Twin")
    assert "Several presets" in caught.value.message
    assert "apply_preset" not in rig.lightroom.calls


def test_undo_last_all_and_nothing(rig: Rig) -> None:
    assert rig.call("undo", photo_id=MASTER)["note"] == "There is nothing to undo."
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.5, contrast=10)["photo_id"]
    rig.call("adjust_lighting", photo_id=MASTER, exposure=0.3)
    rig.call("adjust_white_balance", photo_id=MASTER, temperature=20)

    last = rig.call("undo", photo_id=MASTER)
    assert last["undone_steps"] == 1
    assert last["restored"] == {"IncrementalTemperature": 0}
    assert rig.value(copy_id, "Exposure2012") == pytest.approx(0.8)

    everything = rig.call("undo", photo_id=MASTER, scope="all")
    assert everything["undone_steps"] == 2
    assert rig.value(copy_id, "Exposure2012") == 0
    assert rig.value(copy_id, "Contrast2012") == 0
    assert rig.call("undo", photo_id=MASTER)["undone_steps"] == 0

    # After an undo, the original used for the total limit is still the first value.
    rig.call("adjust_lighting", photo_id=MASTER, exposure=1.5)
    rig.call("adjust_lighting", photo_id=MASTER, exposure=1.5)
    assert change(rig.call("adjust_lighting", photo_id=MASTER, exposure=1.0), "exposure")[
        "limited_by"
    ] == ["total"]


# -- read tools -----------------------------------------------------------------------------------


def test_get_photo_info(rig: Rig) -> None:
    before = rig.call("get_photo_info", photo_id=MASTER)
    assert before["working_copy_id"] is None
    assert before["photo"]["file_format"] == "JPG"
    assert before["settings"]["exposure"] == 0
    assert before["process_version"] == "15.4"
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.4)["photo_id"]
    after = rig.call("get_photo_info", photo_id=MASTER)
    assert after["working_copy_id"] == copy_id
    assert after["settings"]["exposure"] == pytest.approx(0.4)
    assert "create_virtual_copy" not in [e.tool for e in rig.journal.entries()[:1]]


def test_analyze_photo_reports_numbers_and_that_faces_are_unavailable(rig: Rig) -> None:
    result = rig.call("analyze_photo", photo_id=MASTER)
    assert result["analysis"]["width"] == 60
    assert result["analysis"]["tones"]["mean"] == pytest.approx(100 / 255, abs=0.01)
    assert result["faces_available"] is False
    assert result["faces"] is None
    assert "FACE_MODEL_MISSING" in result["faces_note"]
    assert Path(result["preview_path"]).is_file()
    assert rig.lightroom.calls.count("create_virtual_copy") == 0  # analysis creates no copy


# -- replace_background ---------------------------------------------------------------------------


def write_picture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), np.full((20, 30, 3), 128, np.uint8))
    return path


def test_replace_background_with_a_colour(rig: Rig) -> None:
    result = rig.call("replace_background", photo_id=MASTER, color=[200, 30, 30])
    assert result["working_copy_created"] is True
    assert result["mask_provider"] == "synthetic"
    assert result["mask"]["region_count"] == 1
    assert result["background"] == "colour (200, 30, 30)"
    for key in ("psd_path", "jpg_path", "mask_path"):
        path = Path(result[key])
        assert path.is_file()
        assert path.parent == rig.photoshop.work_dir  # results stay in the outputs folder
    names = [name for name, _ in rig.photoshop.calls]
    assert names == [
        "prepare_copy",
        "open_document",
        "apply_mask_from_png",
        "add_fill_layer_below",
        "save_psd",
        "save_jpg",
        "close_document",
    ]
    assert rig.photoshop.open == set()
    assert "export_tiff" in rig.lightroom.calls
    entry = rig.journal.entries()[-1]
    assert entry.tool == "replace_background"
    assert entry.applied is not None and len(entry.applied["outputs"]) == 2
    assert entry.undo is None  # only new files were made; nothing to undo in Lightroom


def test_replace_background_with_a_picture_from_the_folder(rig: Rig) -> None:
    write_picture(rig.tmp_path / "backgrounds" / "sky.jpg")
    result = rig.call("replace_background", photo_id=MASTER, image="sky.jpg")
    assert result["background"] == "image sky.jpg"
    layer_call = next(args for name, args in rig.photoshop.calls if name == "add_image_layer_below")
    assert layer_call[0].parent == rig.photoshop.work_dir  # a copy, not the file in the folder
    assert layer_call[0].name == "sky_copy.jpg"


@pytest.mark.parametrize(
    "name",
    [
        "../secret.jpg",
        "..\\secret.jpg",
        "sub/sky.jpg",
        "C:\\Users\\photo.jpg",
        "https://example.com/sky.jpg",
        "missing.jpg",
        "notes.txt",
        "fake.jpg",
        "big.png",
        " sky.jpg",
        "",
    ],
)
def test_background_must_be_a_real_picture_inside_the_folder(tmp_path: Path, name: str) -> None:
    folder = tmp_path / "backgrounds"
    write_picture(folder / "sky.jpg")
    write_picture(folder / "sub" / "sky.jpg")
    write_picture(tmp_path / "secret.jpg")
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    (folder / "fake.jpg").write_bytes(b"this is not a picture")
    big = np.random.default_rng(0).integers(0, 255, (900, 900, 3), dtype=np.uint8)
    assert cv2.imwrite(str(folder / "big.png"), big)  # > 1 MB of noise
    with pytest.raises(DarkroomError) as caught:
        resolve_background(folder, name, max_file_mb=1.0)
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert resolve_background(folder, "sky.jpg", max_file_mb=1.0) == (folder / "sky.jpg").resolve()


def test_replace_background_argument_rules_and_refusals(rig: Rig) -> None:
    for arguments in (
        {"photo_id": MASTER},
        {"photo_id": MASTER, "color": [1, 2, 3], "image": "sky.jpg"},
        {"photo_id": MASTER, "color": [300, 0, 0]},
    ):
        with pytest.raises(DarkroomError) as caught:
            rig.tools.call("replace_background", arguments)
        assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL
    with pytest.raises(DarkroomError) as caught:
        rig.call("replace_background", photo_id=MASTER, image="../secret.jpg")
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert rig.photoshop.calls == []
    assert "export_tiff" not in rig.lightroom.calls


def test_replace_background_needs_photoshop_and_a_mask_backend(tmp_path: Path) -> None:
    lightroom = FakeLightroom(tmp_path)
    tools = ToolRegistry(
        ToolContext(
            settings=make_settings(tmp_path), lightroom=lightroom, journal=InMemoryJournal()
        )
    )
    with pytest.raises(DarkroomError) as caught:
        tools.call("replace_background", {"photo_id": MASTER, "color": [0, 0, 0]})
    assert caught.value.code is ErrorCode.PS_NOT_REACHABLE
    assert lightroom.calls == []
