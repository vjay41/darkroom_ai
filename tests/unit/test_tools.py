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
        "paths": {
            "notes_dir": str(tmp_path / "notes"),
            "outputs_dir": str(tmp_path / "outputs"),
            "previews_dir": str(tmp_path / "previews"),
            "exports_dir": str(tmp_path / "exports"),
        },
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


@pytest.fixture(autouse=True)
def no_real_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tools poll Lightroom for missing values; tests must not really sleep."""
    monkeypatch.setattr("darkroom_ai.tools.context._sleep", lambda _seconds: None)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


NOT_ABOUT_ONE_PHOTO = {
    "get_app_status",
    "get_capabilities",
    "run_selftest",
    "ensure_apps_running",
    "list_presets",
    "get_preset_notes",
    "save_preset_note",
    "get_selected_photos",
    "find_photo",
    "list_catalog_photos",
    "catalog_summary",
    "list_catalogs",
    "get_catalog_info",
    "request_catalog_access",
    "request_folder_access",
    "create_collection_set",
    "create_collection",
    "add_to_collection",
    "remove_from_collection",
    "get_collection_photos",
    "create_keyword",
    "undo_collection_change",
    "scan_folder",
    "import_folder",
    "list_develop_parameters",
    "analyze_batch",
    "measure_region",
    "measure_tilt",
    "auto_correct",
    "copy_develop_settings",
    "list_keywords",
    "batch_set_library_state",
    "batch_set_develop_parameters",
    "undo_batch",
}


def change(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in result["changes"] if c["name"] == name)


# -- registry -------------------------------------------------------------------------------------


def test_registry_lists_the_v1_tools_with_schemas(rig: Rig) -> None:
    assert rig.tools.names == (
        "get_app_status",
        "get_capabilities",
        "run_selftest",
        "ensure_apps_running",
        "get_photo_info",
        "list_catalog_photos",
        "catalog_summary",
        "list_catalogs",
        "get_catalog_info",
        "request_catalog_access",
        "request_folder_access",
        "create_collection_set",
        "create_collection",
        "add_to_collection",
        "remove_from_collection",
        "get_collection_photos",
        "create_keyword",
        "undo_collection_change",
        "scan_folder",
        "import_folder",
        "get_photo_metadata",
        "get_selected_photos",
        "find_photo",
        "get_preview",
        "analyze_photo",
        "analyze_batch",
        "measure_region",
        "plan_crop",
        "view_region",
        "zone_map",
        "find_candidates",
        "measure_tilt",
        "auto_correct",
        "adjust_lighting",
        "adjust_white_balance",
        "list_develop_parameters",
        "get_develop_parameters",
        "set_develop_parameters",
        "list_masks",
        "create_mask",
        "inspect_masks",
        "set_mask_adjustments",
        "invert_mask",
        "delete_mask",
        "adjust_hsl",
        "crop",
        "get_tone_curve",
        "adjust_tone_curve",
        "replace_background",
        "remove_objects",
        "fix_red_eye",
        "export_for_delivery",
        "list_snapshots",
        "get_retouch_state",
        "get_ai_panel_state",
        "list_keywords",
        "create_snapshot",
        "restore_snapshot",
        "reset_develop",
        "remove_distracting_people",
        "rotate_photo",
        "set_photo_metadata",
        "copy_develop_settings",
        "undo",
        "get_library_state",
        "set_library_state",
        "undo_library_change",
        "batch_set_library_state",
        "batch_set_develop_parameters",
        "undo_batch",
        "list_presets",
        "apply_preset",
        "get_preset",
        "try_presets",
        "get_preset_notes",
        "save_preset_note",
    )
    assert "run_photoshop_script" not in rig.tools.names  # left out of v1 on purpose
    schemas = rig.tools.schemas()
    json.dumps(schemas)  # must be plain JSON
    for schema in schemas:
        assert schema["description"]
        if schema["name"] not in NOT_ABOUT_ONE_PHOTO:
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
    assert rig.call("undo", photo_id=MASTER)["note"].startswith("There is nothing to undo in this")
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.5, contrast=10)["photo_id"]
    rig.call("adjust_lighting", photo_id=MASTER, exposure=0.3)
    rig.call("adjust_white_balance", photo_id=MASTER, temperature=20)

    last = rig.call("undo", photo_id=MASTER)
    assert last["undone_steps"] == 1
    assert last["restored"] == {"IncrementalTemperature": 0, "WhiteBalance": "As Shot"}
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


# -- undo is limited to the session; revert is for the person only --------------------------------


def test_undo_reaches_only_this_sessions_edits(rig: Rig) -> None:
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.5)["photo_id"]
    rig.journal.session = "later-session"
    nothing = rig.call("undo", photo_id=MASTER, scope="all")
    assert nothing["undone_steps"] == 0
    assert "darkroom revert" in nothing["note"]
    assert rig.value(copy_id, "Exposure2012") == 0.5

    rig.call("adjust_lighting", photo_id=MASTER, exposure=0.25)
    rig.call("adjust_lighting", photo_id=MASTER, shadows=10)
    assert rig.call("undo", photo_id=MASTER)["undone_steps"] == 1  # default: the last one
    assert rig.value(copy_id, "Shadows2012") == 0
    everything = rig.call("undo", photo_id=MASTER, scope="all")
    assert everything["undone_steps"] == 1
    assert rig.value(copy_id, "Exposure2012") == 0.5  # the earlier session's edit stays


def test_revert_goes_back_to_the_original_and_is_not_a_tool(rig: Rig) -> None:
    from darkroom_ai.tools.revert import apply_revert, plan_revert

    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.5)["photo_id"]
    rig.journal.session = "later-session"
    rig.call("adjust_lighting", photo_id=MASTER, exposure=0.25, shadows=10)
    plan = plan_revert(rig.context, MASTER)
    assert (len(plan.entries), plan.sessions) == (2, 2)
    assert plan.settings == {"Exposure2012": 0, "Shadows2012": 0}
    assert rig.value(copy_id, "Exposure2012") == 0.75  # planning changes nothing

    entry = apply_revert(rig.context, plan)
    assert entry.tool == "revert"
    assert rig.value(copy_id, "Exposure2012") == 0
    assert rig.value(copy_id, "Shadows2012") == 0
    assert plan_revert(rig.context, MASTER).entries == ()
    assert rig.call("undo", photo_id=MASTER, scope="all")["undone_steps"] == 0

    assert "revert" not in rig.tools.names
    with pytest.raises(DarkroomError) as caught:
        rig.call("revert", photo_id=MASTER)
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL


def test_list_presets_is_read_only_and_filtered(rig: Rig) -> None:
    before = len(rig.journal.entries())
    everything = rig.call("list_presets")
    assert everything["total"] == everything["matching"] == everything["returned"] > 0
    assert set(everything["presets"][0]) == {"name", "folder"}  # no internal ids
    name = everything["presets"][0]["name"]
    some = rig.call("list_presets", contains=name[:4].upper())
    assert 0 < some["matching"] <= everything["total"]
    one = rig.call("list_presets", limit=1)
    assert one["returned"] == 1
    if everything["total"] > 1:
        assert "more match" in one["note"]
    assert rig.call("list_presets", contains="no-such-preset-xyz")["matching"] == 0
    assert all(e.undo is None for e in rig.journal.entries()[before:])
    assert "create_virtual_copy" not in rig.lightroom.calls


def test_raw_white_balance_without_a_current_value_is_refused(tmp_path: Path) -> None:
    """RAW + 'As Shot': Lightroom reports no Kelvin value. Never treat that as 0."""
    rig = Rig(tmp_path, file_format="RAW")
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.1)["photo_id"]
    rig.lightroom.settings[copy_id]["Temperature"] = None
    rig.lightroom.settings[copy_id]["Tint"] = None
    with pytest.raises(DarkroomError) as caught:
        rig.call("adjust_white_balance", photo_id=MASTER, temperature=500)
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "no current value for temperature" in caught.value.message
    assert "Make a lighting edit on this photo first" in caught.value.next_step
    assert "waited 5 s" in caught.value.message
    assert rig.value(copy_id, "Temperature") is None  # nothing was written

    rig.lightroom.settings[copy_id]["Temperature"] = 5200
    rig.lightroom.settings[copy_id]["Tint"] = 8
    result = rig.call("adjust_white_balance", photo_id=MASTER, temperature=500, tint=-3)
    assert rig.value(copy_id, "Temperature") == 5700
    assert rig.value(copy_id, "Tint") == 5
    assert change(result, "temperature")["before"] == 5200


def test_white_balance_total_limit_raw(tmp_path: Path) -> None:
    rig = Rig(tmp_path, file_format="RAW")
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.1)["photo_id"]
    rig.lightroom.settings[copy_id]["Temperature"] = 4100
    rig.lightroom.settings[copy_id]["Tint"] = 9

    first = change(
        rig.call("adjust_white_balance", photo_id=MASTER, temperature=1000), "temperature"
    )
    assert (first["after"], first["limited_by"]) == (5100, [])
    second = change(
        rig.call("adjust_white_balance", photo_id=MASTER, temperature=1000), "temperature"
    )
    assert (second["after"], second["limited_by"]) == (6100, [])
    third = rig.call("adjust_white_balance", photo_id=MASTER, temperature=1000)
    blocked = change(third, "temperature")
    assert blocked["applied_change"] == 0 and blocked["limited_by"] == ["total"]
    assert blocked["original"] == 4100 and rig.value(copy_id, "Temperature") == 6100
    assert "total limit" in third["note"]
    # Moving back towards the original is always allowed.
    back = change(
        rig.call("adjust_white_balance", photo_id=MASTER, temperature=-500), "temperature"
    )
    assert back["after"] == 5600

    tint = change(rig.call("adjust_white_balance", photo_id=MASTER, tint=30), "tint")
    assert tint["after"] == 39
    tint = change(rig.call("adjust_white_balance", photo_id=MASTER, tint=30), "tint")
    assert (tint["after"], tint["limited_by"]) == (49, ["total"])  # 9 + 40

    # Beyond the total: only with a person's confirmation.
    with pytest.raises(DarkroomError) as caught:
        rig.call("adjust_white_balance", photo_id=MASTER, temperature=1000, allow_beyond_total=True)
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    assert rig.value(copy_id, "Temperature") == 5600


def test_white_balance_total_limit_jpg_and_confirmed_override(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    copy_id = rig.call("adjust_white_balance", photo_id=MASTER, temperature=30)["photo_id"]
    rig.call("adjust_white_balance", photo_id=MASTER, temperature=30)
    third = change(rig.call("adjust_white_balance", photo_id=MASTER, temperature=30), "temperature")
    assert third["applied_change"] == 0 and third["limited_by"] == ["total"]
    assert rig.value(copy_id, "IncrementalTemperature") == 60  # twice the per-pass limit
    beyond = rig.call(
        "adjust_white_balance", photo_id=MASTER, temperature=30, allow_beyond_total=True
    )
    assert change(beyond, "temperature")["after"] == 90  # a person agreed
    limits = rig.context.settings.guardrails
    assert (limits.wb_relative_total, limits.wb_kelvin_total, limits.wb_tint_raw_total) == (
        60,
        2000,
        40,
    )


def test_raw_white_balance_waits_read_only_for_the_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, file_format="RAW")
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.1)["photo_id"]
    rig.lightroom.settings[copy_id]["Temperature"] = None
    rig.lightroom.settings[copy_id]["Tint"] = None
    polls: list[float] = []

    def numbers_appear_after_two_polls(seconds: float) -> None:
        polls.append(seconds)
        if len(polls) == 2:  # Lightroom has worked the numbers out by now
            rig.lightroom.settings[copy_id]["Temperature"] = 4200
            rig.lightroom.settings[copy_id]["Tint"] = 4

    monkeypatch.setattr("darkroom_ai.tools.context._sleep", numbers_appear_after_two_polls)
    writes_before = rig.lightroom.calls.count("apply_develop_settings")
    result = rig.call("adjust_white_balance", photo_id=MASTER, temperature=500)
    assert polls == [0.5, 0.5]
    assert change(result, "temperature")["before"] == 4200
    assert rig.value(copy_id, "Temperature") == 4700
    # Exactly one write: the requested change. Nothing was written to force the numbers.
    assert rig.lightroom.calls.count("apply_develop_settings") == writes_before + 1


# -- JPG white balance is written together with its mode; read-back -------------------------------


def _capture_writes(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    writes: list[dict[str, Any]] = []
    real = rig.lightroom.apply_develop_settings

    def recording(photo_id: str, settings: Any, history_name: str = "edit") -> Any:
        writes.append(dict(settings))
        return real(photo_id, settings, history_name)

    monkeypatch.setattr(rig.lightroom, "apply_develop_settings", recording)
    return writes


def test_jpg_white_balance_is_never_written_without_its_mode(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes = _capture_writes(rig, monkeypatch)
    rig.call("adjust_white_balance", photo_id=MASTER, temperature=10)
    rig.call("adjust_white_balance", photo_id=MASTER, tint=-5)
    rig.call("adjust_white_balance", photo_id=MASTER, temperature=10, tint=5)
    rig.call("adjust_lighting", photo_id=MASTER, exposure=0.2)
    relative = [w for w in writes if "IncrementalTemperature" in w or "IncrementalTint" in w]
    assert len(relative) == 3
    for write in relative:
        assert write.get("WhiteBalance") == "Custom", write  # the rule this test protects
    assert "WhiteBalance" not in writes[-1]  # a lighting edit does not touch the mode


def test_white_balance_undo_restores_the_previous_mode_exactly(rig: Rig) -> None:
    first = rig.call("adjust_white_balance", photo_id=MASTER, temperature=10)
    copy_id = first["photo_id"]
    assert "WhiteBalance set to Custom (was As Shot)" in first["note"]
    assert rig.value(copy_id, "WhiteBalance") == "Custom"
    entry = rig.journal.entries()[-1]
    assert entry.undo is not None
    assert entry.undo["settings"] == {"IncrementalTemperature": 0, "WhiteBalance": "As Shot"}

    rig.call("adjust_white_balance", photo_id=MASTER, temperature=10)
    assert rig.journal.entries()[-1].undo["settings"]["WhiteBalance"] == "Custom"  # type: ignore[index]
    rig.call("undo", photo_id=MASTER)  # the second edit: mode stays Custom, value back to 10
    assert (rig.value(copy_id, "IncrementalTemperature"), rig.value(copy_id, "WhiteBalance")) == (
        10,
        "Custom",
    )
    rig.call("undo", photo_id=MASTER)  # the first edit: the PREVIOUS mode comes back
    assert (rig.value(copy_id, "IncrementalTemperature"), rig.value(copy_id, "WhiteBalance")) == (
        0,
        "As Shot",
    )


def test_previous_mode_is_restored_whatever_it_was_and_revert_uses_the_first(rig: Rig) -> None:
    from darkroom_ai.tools.revert import apply_revert, plan_revert

    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.1)["photo_id"]
    rig.lightroom.settings[copy_id]["WhiteBalance"] = "Auto"  # not "As Shot"
    rig.call("adjust_white_balance", photo_id=MASTER, temperature=10)
    rig.call("undo", photo_id=MASTER)
    assert rig.value(copy_id, "WhiteBalance") == "Auto"  # nothing is hard-coded

    rig.call("adjust_white_balance", photo_id=MASTER, temperature=10)
    rig.journal.session = "later-session"
    rig.call("adjust_white_balance", photo_id=MASTER, tint=5)
    plan = plan_revert(rig.context, MASTER)
    assert plan.settings["WhiteBalance"] == "Auto"  # the mode before darkroom_ai's first change
    apply_revert(rig.context, plan)
    assert rig.value(copy_id, "WhiteBalance") == "Auto"
    assert rig.value(copy_id, "IncrementalTemperature") == 0
    assert rig.value(copy_id, "IncrementalTint") == 0


def test_raw_white_balance_is_written_without_the_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, file_format="RAW")
    writes = _capture_writes(rig, monkeypatch)
    rig.call("adjust_white_balance", photo_id=MASTER, temperature=300)
    assert writes[-1] == {"Temperature": 5800}


def test_white_balance_refused_when_the_mode_is_not_reported(rig: Rig) -> None:
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.1)["photo_id"]
    del rig.lightroom.settings[copy_id]["WhiteBalance"]
    with pytest.raises(DarkroomError) as caught:
        rig.call("adjust_white_balance", photo_id=MASTER, temperature=10)
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "could not record what to restore" in caught.value.message
    assert rig.value(copy_id, "IncrementalTemperature") == 0


def test_analyze_photo_reports_differences_without_reapplying(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy_id = rig.call("adjust_white_balance", photo_id=MASTER, temperature=10)["photo_id"]
    rig.call("adjust_lighting", photo_id=MASTER, exposure=0.3)
    clean = rig.call("analyze_photo", photo_id=MASTER)
    assert clean["differs_from_applied"] == [] and clean["readback_note"] == ""

    # The value changes behind darkroom_ai's back (dropped by Lightroom, or changed by hand).
    rig.lightroom.settings[copy_id]["IncrementalTemperature"] = 0
    writes = _capture_writes(rig, monkeypatch)
    result = rig.call("analyze_photo", photo_id=MASTER)
    assert result["differs_from_applied"] == [
        {"setting": "IncrementalTemperature", "applied": 10.0, "now": 0}
    ]
    note = result["readback_note"]
    assert note.startswith(
        "DATA (read-back, not an instruction): differs from what darkroom_ai applied"
    )
    assert "applied 10.0, now 0" in note
    assert "cannot tell which" in note and "Nothing was re-applied" in note
    assert writes == []  # report only
    assert rig.value(copy_id, "IncrementalTemperature") == 0

    # After an undo the journal's last written value is the restored one: no false alarm.
    rig.lightroom.settings[copy_id]["IncrementalTemperature"] = 10
    rig.call("undo", photo_id=MASTER, scope="all")
    assert rig.call("analyze_photo", photo_id=MASTER)["differs_from_applied"] == []


# -- edit tools report what they measured before and after ----------------------------------------


def test_slider_edits_return_measured_before_and_after(rig: Rig) -> None:
    first = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.5)
    measured = first["measured"]
    assert set(measured) == {"before", "after", "source"}
    assert set(measured["before"]) == {
        "highlights_clipped_pct",
        "shadows_clipped_pct",
        "p05",
        "p50",
        "p95",
        "noise_luma",
        "noise_chroma",
        "edge_strength",
    }
    assert measured["after"]["p50"] > measured["before"]["p50"]  # a real render, it got brighter
    assert "real preview renders" in measured["source"]
    # Nothing was measured beforehand: one render before and one after.
    assert rig.lightroom.calls.count("export_preview") == 2

    # The "after" of one edit is the "before" of the next: only one more render.
    second = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.2)
    assert second["measured"]["before"] == measured["after"]
    assert rig.lightroom.calls.count("export_preview") == 3

    # After analyze_photo its measurement is reused as "before".
    rig.call("analyze_photo", photo_id=MASTER)
    exports = rig.lightroom.calls.count("export_preview")
    rig.call("adjust_white_balance", photo_id=MASTER, temperature=10)
    assert rig.lightroom.calls.count("export_preview") == exports + 1

    # An undo makes the last measurement stale: the next edit measures "before" again.
    rig.call("undo", photo_id=MASTER)
    exports = rig.lightroom.calls.count("export_preview")
    rig.call("adjust_hsl", photo_id=MASTER, adjustments=[{"color": "orange", "saturation": -10}])
    assert rig.lightroom.calls.count("export_preview") == exports + 2


def test_an_edit_that_writes_nothing_measures_nothing(rig: Rig) -> None:
    rig.call("adjust_lighting", photo_id=MASTER, exposure=1.5)
    rig.call("adjust_lighting", photo_id=MASTER, exposure=1.5)
    exports = rig.lightroom.calls.count("export_preview")
    blocked = rig.call(
        "adjust_lighting", photo_id=MASTER, exposure=1.0
    )  # the 3 EV total is reached
    assert change(blocked, "exposure")["applied_change"] == 0
    assert blocked["measured"] is None
    assert rig.lightroom.calls.count("export_preview") == exports


def test_crop_returns_measured_before_and_after(rig: Rig) -> None:
    result = rig.call("crop", photo_id=MASTER, left=0.1, top=0.1, right=0.9, bottom=0.9)
    measured = result["measured"]
    assert set(measured) == {"before", "after", "source"}
    assert set(measured["after"]) == set(measured["before"])
    # The "after" value is stored, so a following slider edit reuses it as its "before".
    exports = rig.lightroom.calls.count("export_preview")
    edit = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.2)
    assert edit["measured"]["before"] == measured["after"]
    assert rig.lightroom.calls.count("export_preview") == exports + 1


# -- "no change" is information, only for absolute settings ---------------------------------------


def test_identical_crop_is_reported_as_no_change_and_writes_nothing(rig: Rig) -> None:
    box = {"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.9}
    first = rig.call("crop", photo_id=MASTER, **box)
    assert first["no_change"] is False and first["measured"] is not None
    writes = rig.lightroom.calls.count("apply_develop_settings")
    exports = rig.lightroom.calls.count("export_preview")
    again = rig.call("crop", photo_id=MASTER, **box)  # accepted, never refused
    assert again["no_change"] is True
    assert again["note"] == "No change: the photo already has these values. Nothing was written."
    assert again["measured"] is None and again["before"] == again["after"]
    assert rig.lightroom.calls.count("apply_develop_settings") == writes
    assert rig.lightroom.calls.count("export_preview") == exports
    entry = rig.journal.entries()[-1]
    assert entry.tool == "crop" and entry.status == "ok" and entry.undo is None
    assert rig.call("undo", photo_id=MASTER)["undone_steps"] == 1  # only the real crop

    different = rig.call("crop", photo_id=MASTER, left=0.2, top=0.1, right=0.9, bottom=0.9)
    assert different["no_change"] is False


def test_a_repeated_relative_slider_call_is_a_new_request(rig: Rig) -> None:
    first = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.3)
    second = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.3)
    assert not first.get("no_change") and not second.get("no_change")
    assert change(second, "exposure")["after"] == pytest.approx(0.6)
    assert second["measured"] is not None


def test_selected_photos_and_find_photo_are_read_only(rig: Rig) -> None:
    selected = rig.call("get_selected_photos")
    assert "photos" in selected and "note" in selected
    missing = rig.call("find_photo", path="X:/not/in/catalog.jpg")
    assert missing["found"] is False and "import" in missing["note"]


def test_catalog_listing_and_metadata_are_facts_only(rig: Rig) -> None:
    rig.lightroom.add_photo("master-2", "TEST_0002.jpg")
    page = rig.call("list_catalog_photos", limit=1)["page"]
    assert page["total"] == 2 and page["returned"] == 1
    assert "offset 1" in rig.call("list_catalog_photos", limit=1)["note"]
    only = rig.call("list_catalog_photos", path_contains="0002")["page"]
    assert [p["file_name"] for p in only["photos"]] == ["TEST_0002.jpg"]
    meta = rig.call("get_photo_metadata", photo_id=MASTER)["metadata"]
    assert meta["camera_model"] and meta["iso"] and meta["lens"]


def test_generic_develop_parameters_are_bounded_and_refuse_unlisted_names(rig: Rig) -> None:
    listed = rig.call("list_develop_parameters", panel="detail")["parameters"]
    assert listed and all(entry["panel"] == "detail" for entry in listed)
    got = rig.call("set_develop_parameters", photo_id=MASTER, changes={"Clarity2012": 80})
    entry = change(got, "Clarity2012")
    assert entry["applied_change"] == 50.0 and got["clamped"] is True  # per-call limit: 25% of 200
    values = rig.call("get_develop_parameters", photo_id=got["photo_id"], names=["Clarity2012"])
    assert values["values"]["Clarity2012"] == 50.0
    with pytest.raises(DarkroomError) as caught:
        rig.call("set_develop_parameters", photo_id=MASTER, changes={"Exposure2012": 1})
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "adjust_lighting" in caught.value.next_step


def test_create_mask_checks_the_result_and_is_undoable(rig: Rig) -> None:
    assert rig.call("list_masks", photo_id=MASTER)["count"] == 0
    got = rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="subject")
    assert got["creation"]["created"] is True and len(got["masks"]) == 1
    assert rig.call("list_masks", photo_id=got["photo_id"])["count"] == 1
    undone = rig.call("undo", photo_id=got["photo_id"])
    assert undone["undone_steps"] == 1
    assert rig.call("list_masks", photo_id=got["photo_id"])["count"] == 0


def test_mask_in_a_collapsed_stack_is_refused_at_once_with_the_fix(rig: Rig) -> None:
    first = rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="subject")
    copy_id = first["photo_id"]
    rig.lightroom.collapsed_stack_ids.add(copy_id)
    info = rig.call("get_photo_info", photo_id=copy_id)
    assert info["photo"]["in_stack"] is True and info["photo"]["stack_collapsed"] is True
    with pytest.raises(DarkroomError) as caught:
        rig.call("create_mask", photo_id=copy_id, mask_type="aiSelection", subtype="sky")
    assert caught.value.code is ErrorCode.LR_BUSY_USER
    assert "Expand All Stacks" in caught.value.next_step
    assert rig.call("list_masks", photo_id=copy_id)["count"] == 1  # nothing was added
    rig.lightroom.collapsed_stack_ids.clear()
    assert rig.call("create_mask", photo_id=copy_id, mask_type="aiSelection", subtype="sky")


def test_create_mask_rejects_a_wrong_subtype(rig: Rig) -> None:
    with pytest.raises(DarkroomError) as caught:
        rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="sunset")
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL
    with pytest.raises(DarkroomError):
        rig.call("create_mask", photo_id=MASTER, mask_type="gradient", subtype="sky")


def test_mask_adjustments_are_bounded_per_call_and_undoable(rig: Rig) -> None:
    made = rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="sky")
    copy, mask_id = made["photo_id"], made["mask_id"]
    got = rig.call(
        "set_mask_adjustments",
        photo_id=copy,
        mask_id=mask_id,
        changes={"local_Exposure": -4.0, "local_Contrast": 80},
    )
    assert got["applied_change"]["local_Exposure"] == -1.5  # exposure step limit
    assert got["applied_change"]["local_Contrast"] == 50  # quarter of the -100..100 range
    assert rig.call("undo", photo_id=copy)["undone_steps"] == 1


def test_mask_adjustments_reject_unknown_names(rig: Rig) -> None:
    with pytest.raises(DarkroomError) as caught:
        rig.call("set_mask_adjustments", photo_id=MASTER, changes={"Exposure2012": 1})
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED


def test_invert_and_delete_mask(rig: Rig) -> None:
    made = rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="subject")
    copy, mask_id = made["photo_id"], made["mask_id"]
    assert rig.call("invert_mask", photo_id=copy, mask_id=mask_id)["operation"] == "invert"
    assert rig.call("delete_mask", photo_id=copy, mask_id=mask_id)["masks_after"] == 0
    assert rig.call("inspect_masks", photo_id=copy)["masks"] == []


def test_catalog_summary_counts_the_catalog(rig: Rig) -> None:
    got = rig.call("catalog_summary")
    assert got["total_photos"] == got["summarised"] >= 1 and got["truncated"] is False
    assert got["cameras"] == {"NIKON CORPORATION NIKON D7500": got["total_photos"]}
    assert got["iso_bands"] == {"up to 400": got["total_photos"]}


def test_a_small_import_runs_and_keeps_the_list_of_added_files(tmp_path: Path) -> None:
    rig = Rig(tmp_path)  # refuses every prompt: 3 photos is below the go-ahead threshold
    assert rig.call("scan_folder", folder="D:\\darkroom_test\\photos")["scan"]["new_count"] == 3
    got = rig.call("import_folder", folder="D:\\darkroom_test\\photos")
    assert got["imported"]["added"] == 3
    entry = [e for e in rig.journal.entries() if e.tool == "import_folder"][-1]
    assert entry.applied is not None and len(entry.applied["added_paths"]) == 3


def test_a_larger_import_needs_the_go_ahead_and_is_capped_at_50(tmp_path: Path) -> None:
    refused = Rig(tmp_path / "a")
    refused.lightroom.importable = 12
    with pytest.raises(DarkroomError) as caught:
        refused.call("import_folder", folder="D:\\darkroom_test\\photos")
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    assert "import_folder" not in refused.lightroom.calls

    confirmer = AllowAll()
    allowed = Rig(tmp_path / "b", confirmer=confirmer)
    allowed.lightroom.importable = 80
    got = allowed.call("import_folder", folder="D:\\darkroom_test\\photos")
    assert got["imported"]["added"] == 50
    assert "50 photos" in confirmer.asked[0]
    with pytest.raises(DarkroomError):
        allowed.call("import_folder", folder="D:\\darkroom_test\\photos", max_files=51)


def test_iso_band_reads_lightroom_text() -> None:
    from darkroom_ai.tools.catalog_tools import _iso_band

    assert [_iso_band(v) for v in ("ISO 200", "200", "ISO 1600", "ISO 12800", None, "n/a")] == [
        "up to 400",
        "up to 400",
        "401-1600",
        "above 6400",
        "unknown",
        "unknown",
    ]


def test_preset_that_changes_the_crop_is_taken_back(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.2)["photo_id"]
    with pytest.raises(DarkroomError) as caught:
        rig.call("apply_preset", photo_id=MASTER, preset_name="Cropped Look")
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert "CropLeft" in caught.value.message
    assert rig.value(copy_id, "CropLeft") == 0
    assert rig.value(copy_id, "Contrast2012") == 0


def test_undoing_a_preset_that_adds_masks_removes_them(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    got = rig.call("apply_preset", photo_id=MASTER, preset_name="Masked Look")
    copy_id = got["photo_id"]
    assert rig.call("list_masks", photo_id=copy_id)["count"] == 1
    assert "MaskGroupBasedCorrections" in got["before"]  # so that undo can empty it again
    rig.call("undo", photo_id=copy_id)
    assert rig.call("list_masks", photo_id=copy_id)["count"] == 0


def test_toggle_parameters_switch_fully_in_one_step() -> None:
    from darkroom_ai.config import GuardrailSettings
    from darkroom_ai.tools.develop_parameters import parameter_limit

    guard = GuardrailSettings()
    assert parameter_limit(guard, "LensProfileEnable").per_pass == 1.0
    assert parameter_limit(guard, "AutoLateralCA").per_pass == 1.0
    assert parameter_limit(guard, "Texture").per_pass == 50.0  # sliders keep the quarter-range step


# -- masks written as Lightroom's own stored data -------------------------------------------------


def test_gradient_mask_is_written_in_lightroom_format_and_undoable(rig: Rig) -> None:
    got = rig.call(
        "create_mask",
        photo_id=MASTER,
        mask_type="gradient",
        start=[0.5, 0.13],
        end=[0.5, 0.46],
    )
    part = (
        got["stored"]["CorrectionMasks"][0] if "CorrectionMasks" in got["stored"] else got["stored"]
    )
    assert part["What"] == "Mask/Gradient"
    assert (part["FullX"], part["FullY"], part["ZeroX"], part["ZeroY"]) == (0.5, 0.13, 0.5, 0.46)
    assert got["creation"]["created"] is True and got["mask_id"]
    assert rig.call("list_masks", photo_id=got["photo_id"])["count"] == 1
    assert rig.call("undo", photo_id=got["photo_id"])["undone_steps"] == 1
    assert rig.call("list_masks", photo_id=got["photo_id"])["count"] == 0


def test_radial_mask_box_comes_from_center_and_radius(rig: Rig) -> None:
    got = rig.call(
        "create_mask",
        photo_id=MASTER,
        mask_type="radialGradient",
        center=[0.25, 0.7],
        radius_x=0.15,
        radius_y=0.2,
    )
    part = got["stored"]
    assert part["What"] == "Mask/CircularGradient"
    assert (round(part["Left"], 3), round(part["Right"], 3)) == (0.1, 0.4)
    assert (round(part["Top"], 3), round(part["Bottom"], 3)) == (0.5, 0.9)
    assert part["Feather"] == 50 and part["Flipped"] is True and part["Version"] == 2


def test_luminance_range_mask_stores_the_four_values(rig: Rig) -> None:
    got = rig.call(
        "create_mask",
        photo_id=MASTER,
        mask_type="rangeMask",
        subtype="luminance",
        luminance_range=[0.2, 0.3, 0.7, 0.8],
    )
    stored = got["stored"]
    assert stored["What"] == "Mask/RangeMask"
    assert stored["CorrectionRangeMask"]["LumRange"] == "0.200000 0.300000 0.700000 0.800000"
    assert stored["CorrectionRangeMask"]["Type"] == 2


def test_written_masks_need_their_shape_and_reject_bad_shapes(rig: Rig) -> None:
    bad: list[dict[str, Any]] = [
        {"mask_type": "gradient", "start": [0.5, 0.1]},
        {"mask_type": "gradient", "start": [0.5, 0.1], "end": [0.5, 0.1]},
        {"mask_type": "gradient", "start": [0.5, 1.4], "end": [0.5, 0.2]},
        {"mask_type": "radialGradient", "center": [0.5, 0.5], "radius_x": 0.2},
        {"mask_type": "gradient", "start": [0.1, 0.1], "end": [0.2, 0.2], "feather": 10},
        {"mask_type": "rangeMask", "subtype": "luminance", "luminance_range": [0.5, 0.2, 0.7, 0.8]},
        {"mask_type": "rangeMask", "subtype": "luminance"},
    ]
    for arguments in bad:
        with pytest.raises(DarkroomError) as caught:
            rig.call("create_mask", photo_id=MASTER, **arguments)
        assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL, arguments


def test_a_written_mask_that_does_not_stick_is_reported_not_claimed(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rig.lightroom, "apply_develop_settings", lambda *a, **k: None)
    with pytest.raises(DarkroomError) as caught:
        rig.call(
            "create_mask", photo_id=MASTER, mask_type="gradient", start=[0.5, 0.1], end=[0.5, 0.4]
        )
    assert caught.value.code is ErrorCode.LR_COMMAND_FAILED
    assert "not in the photo's settings" in caught.value.message


def test_refused_mask_error_does_not_claim_the_type_is_unavailable(rig: Rig) -> None:
    rig.lightroom.refused_mask_subtypes.update({"color", "sky"})
    with pytest.raises(DarkroomError) as caught:
        rig.call("create_mask", photo_id=MASTER, mask_type="rangeMask", subtype="color")
    assert caught.value.code is ErrorCode.LR_COMMAND_FAILED
    assert "cannot be created automatically" in caught.value.next_step
    assert "does not prove" in caught.value.message
    with pytest.raises(DarkroomError) as sky:
        rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="sky")
    assert sky.value.code is ErrorCode.LR_COMMAND_FAILED


class FinishesThePeopleMask:
    """Stands in for the person who presses 'Create Mask' in Lightroom, then answers."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.asked: list[str] = []

    def confirm(self, action: str, details: Any) -> bool:
        self.asked.append(action)
        copy = details["photo_id"]
        settings = self.rig.lightroom.settings[copy]
        settings["MaskGroupBasedCorrections"] = [
            *(settings.get("MaskGroupBasedCorrections") or []),
            {"CorrectionName": "Person 2", "CorrectionID": "p2", "CorrectionMasks": []},
        ]
        return True


def test_people_mask_is_handed_to_the_person_and_then_checked(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    person = FinishesThePeopleMask(rig)
    rig.context.confirmer = person  # type: ignore[assignment]
    got = rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="people")
    assert got["creation"]["created"] is True and len(got["masks"]) == 1
    assert "Create Mask" in person.asked[0]
    assert got["mask_id"] == "p2"
    assert "The person made the people mask" in got["note"]
    assert "create_mask" not in rig.lightroom.calls  # Lightroom's own call is not used for people


def test_people_mask_declined_changes_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(DarkroomError) as caught:
        rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="people")
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    assert rig.call("list_masks", photo_id=MASTER)["count"] == 0


class SaysYesButDoesNothing:
    def confirm(self, action: str, details: Any) -> bool:
        return True


def test_people_mask_that_never_appears_says_what_to_do(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.context.confirmer = SaysYesButDoesNothing()  # type: ignore[assignment]
    with pytest.raises(DarkroomError) as caught:
        rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="people")
    assert caught.value.code is ErrorCode.LR_COMMAND_FAILED
    assert "Create Mask" in caught.value.next_step


class RecordsQuestions:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def confirm(self, action: str, details: Any) -> bool:
        self.asked.append(action)
        return True


def test_people_mask_is_refused_when_lightroom_shows_another_photo(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    asked = RecordsQuestions()
    rig.context.confirmer = asked  # type: ignore[assignment]
    rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="subject")
    rig.lightroom.selected_id = MASTER  # the person is looking at the master, not the copy
    with pytest.raises(DarkroomError) as caught:
        rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="people")
    assert caught.value.code is ErrorCode.LR_COMMAND_FAILED
    assert "not the working copy" in caught.value.message
    assert "select the working copy" in caught.value.next_step
    assert asked.asked == []  # nobody was asked to do anything
    assert rig.call("list_masks", photo_id=MASTER)["count"] == 1  # the earlier subject mask only


def test_list_masks_explains_the_stored_scale(rig: Rig) -> None:
    rig.call("create_mask", photo_id=MASTER, mask_type="aiSelection", subtype="subject")
    assert "slider EV / 4" in rig.call("list_masks", photo_id=MASTER)["note"]


def test_list_develop_parameters_names_the_readable_lens_profile_settings(rig: Rig) -> None:
    listed = rig.call("list_develop_parameters")
    assert "LensProfileName" in listed["readable_text_settings"]


# -- analyze_batch --------------------------------------------------------------------------------


def test_analyze_batch_returns_numbers_for_many_photos_and_changes_nothing(rig: Rig) -> None:
    result = rig.call("analyze_batch", photo_ids=[MASTER, MASTER, "no-such-photo"])
    assert [r["photo_id"] for r in result["results"]] == [
        MASTER,
        "no-such-photo",
    ]  # repeats dropped
    good, bad = result["results"]
    assert good["median"] == pytest.approx(100 / 255, abs=0.01)
    assert good["error"] is None and good["measured_id"] == MASTER
    assert bad["error"] is not None and bad["median"] is None
    assert result["not_processed"] == []
    assert result["long_edge"] == 512
    assert result["elapsed_s"] >= 0
    assert rig.lightroom.calls.count("create_virtual_copy") == 0


def test_analyze_batch_stops_after_the_time_budget(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0])
    monkeypatch.setattr("darkroom_ai.tools.lightroom_tools.time.monotonic", lambda: next(ticks))
    result = rig.call("analyze_batch", photo_ids=[MASTER, "b", "c"], time_budget_s=1)
    assert len(result["results"]) == 1
    assert result["not_processed"] == ["b", "c"]
    assert "Call again with not_processed" in result["note"]


def test_analyze_batch_stops_after_repeated_failures(rig: Rig) -> None:
    result = rig.call("analyze_batch", photo_ids=["x1", "x2", "x3", "x4", "x5"])
    assert len(result["results"]) == 3
    assert result["not_processed"] == ["x4", "x5"]
    assert "in a row failed" in result["note"]


def test_analyze_batch_refuses_more_than_the_limit(rig: Rig) -> None:
    with pytest.raises(Exception, match=r"25"):
        rig.call("analyze_batch", photo_ids=[f"p{i}" for i in range(26)])


# -- measure_region -------------------------------------------------------------------------------


def test_measure_region_reads_boxes_without_changing_anything(rig: Rig) -> None:
    result = rig.call(
        "measure_region",
        photo_id=MASTER,
        regions=[
            {"name": "left", "box": [0.0, 0.0, 0.5, 1.0]},
            {"name": "right", "box": [0.5, 0.0, 1.0, 1.0]},
        ],
    )
    assert [r["name"] for r in result["readings"]] == ["left", "right"]
    assert result["readings"][0]["chroma"] < 1.0  # the test picture is flat grey
    assert result["spread"]["hue_range_deg"] >= 0
    assert Path(result["preview_path"]).is_file()
    assert rig.lightroom.calls.count("create_virtual_copy") == 0


def test_measure_tilt_reads_without_changing_anything(rig: Rig) -> None:
    result = rig.call("measure_tilt", photo_id=MASTER)
    assert result["reading"]["horizontal"] is None  # the flat test picture has no lines
    assert Path(result["preview_path"]).is_file()
    assert rig.lightroom.calls.count("create_virtual_copy") == 0


def test_measure_region_rejects_duplicate_names_and_bad_boxes(rig: Rig) -> None:
    box = [0.0, 0.0, 1.0, 1.0]
    with pytest.raises(DarkroomError):
        rig.call(
            "measure_region",
            photo_id=MASTER,
            regions=[{"name": "a", "box": box}, {"name": "a", "box": box}],
        )
    with pytest.raises(DarkroomError):
        rig.call(
            "measure_region", photo_id=MASTER, regions=[{"name": "a", "box": [0.6, 0, 0.4, 1]}]
        )


# -- presets: get_preset, try_presets, notes ------------------------------------------------------


def test_get_preset_shows_what_a_preset_would_write_without_applying_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    result = rig.call("get_preset", preset_name="Cropped Look", photo_id=MASTER)
    assert result["total_settings"] == 2 and "CropLeft" in result["touches_framing"]
    lines = [line for group in result["groups"].values() for line in group]
    assert {line["setting"] for line in lines} == {"Contrast2012", "CropLeft"}
    assert rig.lightroom.calls.count("apply_preset") == 0
    assert rig.lightroom.calls.count("create_virtual_copy") == 0
    masked = rig.call("get_preset", preset_name="Masked Look")
    assert masked["masks"] == ["Sky"] and "local adjustments (masks)" in masked["groups"]


def test_get_preset_refuses_unknown_and_ambiguous_names(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(DarkroomError):
        rig.call("get_preset", preset_name="No Such Look")
    with pytest.raises(DarkroomError):
        rig.call("get_preset", preset_name="Twin")  # exists in two folders
    assert rig.call("get_preset", preset_name="Twin", folder="Folder preset-3")["name"] == "Twin"


def test_try_presets_measures_each_and_leaves_the_photo_as_it_was(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    before = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.0).get("photo_id")
    copy_id = before or MASTER
    result = rig.call(
        "try_presets", photo_id=MASTER, preset_names=["Soft Warm", "Hard Push", "Cropped Look"]
    )
    by_name = {t["preset"]: t for t in result["trials"]}
    assert by_name["Soft Warm"]["status"] == "measured"
    assert by_name["Soft Warm"]["preview_path"] and "tone_spread" in by_name["Soft Warm"]["numbers"]
    assert by_name["Hard Push"]["status"] == "refused"  # beyond the exposure limit
    assert by_name["Cropped Look"]["status"] == "refused"  # changes framing
    assert result["restored"] is True
    assert rig.value(result["photo_id"], "Exposure2012") == rig.value(copy_id, "Exposure2012") == 0
    assert rig.value(result["photo_id"], "Contrast2012") == 0
    assert rig.value(MASTER, "Contrast2012") == 0


def test_try_presets_rejects_duplicates(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(DarkroomError):
        rig.call("try_presets", photo_id=MASTER, preset_names=["Soft Warm", "soft warm"])


def test_preset_notes_are_saved_found_and_listed_against_lightroom(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    empty = rig.call("get_preset_notes")
    assert empty["notes"] == [] and empty["without_notes"] == empty["presets_in_lightroom"] > 0
    saved = rig.call(
        "save_preset_note",
        preset_name="soft warm",
        character=["warm", "soft"],
        best_for=["indoor ceremonies"],
        avoid_for=["neutral product shots"],
        strength="moderate",
        comment="Lifts mids gently.",
        evidence={"tone_spread": 0.02},
    )
    assert saved["saved"]["name"] == "Soft Warm" and saved["notes_total"] == 1
    found = rig.call("get_preset_notes", contains="ceremon")
    assert [n["name"] for n in found["notes"]] == ["Soft Warm"]
    assert found["without_notes"] == found["presets_in_lightroom"] - 1
    with pytest.raises(DarkroomError):
        rig.call("save_preset_note", preset_name="Twin")  # two folders: needs the folder
    with pytest.raises(DarkroomError):
        rig.call("save_preset_note", preset_name="Not A Preset")


# -- crop planning, looking closer, scene numbers ---------------------------------------------


def test_plan_crop_returns_arguments_for_the_crop_tool(rig: Rig) -> None:
    result = rig.call("plan_crop", photo_id=MASTER, aspect="4:5", angle=3.0)
    assert result["plan"]["fits_inside"] is True
    assert result["plan"]["aspect"] == 0.8
    applied = rig.call("crop", photo_id=MASTER, **result["crop_arguments"])
    assert applied["kept_fraction"] == pytest.approx(result["plan"]["kept_fraction"], abs=1e-3)
    with pytest.raises(DarkroomError):
        rig.call("plan_crop", photo_id=MASTER, aspect="wide")


def test_plan_crop_changes_nothing(rig: Rig) -> None:
    rig.call("plan_crop", photo_id=MASTER, angle=2.0, anchor=[0.8, 0.5])
    assert rig.lightroom.calls.count("create_virtual_copy") == 0
    assert "apply_develop_settings" not in rig.lightroom.calls


def test_view_region_writes_a_closer_picture(rig: Rig) -> None:
    result = rig.call("view_region", photo_id=MASTER, box=[0.2, 0.2, 0.6, 0.7], long_edge=400)
    assert Path(result["path"]).is_file()
    assert max(result["size"]) == 400
    assert rig.lightroom.calls.count("create_virtual_copy") == 0


def test_zone_map_and_candidates_read_only(rig: Rig) -> None:
    zones = rig.call("zone_map", photo_id=MASTER, rows=3, cols=4)
    assert len(zones["map"]["cells"]) == 12
    cands = rig.call("find_candidates", photo_id=MASTER)
    assert cands["candidates"] == []  # the flat test picture has nothing that stands out
    assert rig.lightroom.calls.count("create_virtual_copy") == 0


# -- remove_objects / fix_red_eye / export_for_delivery ------------------------------------------

SPOT = {"name": "dust", "box": [0.45, 0.45, 0.5, 0.5], "shape": "ellipse"}


def test_remove_objects_inpaint_makes_new_files_only(rig: Rig) -> None:
    result = rig.call("remove_objects", photo_id=MASTER, regions=[SPOT], method="inpaint")
    assert result["method"] == "inpaint"
    assert Path(result["jpg_path"]).is_file() and Path(result["tiff_path"]).is_file()
    assert result["psd_path"] is None
    assert Path(result["before_after_path"]).is_file()
    assert rig.photoshop.calls == []
    assert "apply_develop_settings" not in rig.lightroom.calls


def test_remove_objects_content_aware_uses_photoshop_on_a_copy(rig: Rig) -> None:
    result = rig.call("remove_objects", photo_id=MASTER, regions=[SPOT])  # auto -> Photoshop
    assert result["method"] == "content_aware"
    assert Path(result["psd_path"]).is_file() and Path(result["jpg_path"]).is_file()
    names = [name for name, _ in rig.photoshop.calls]
    assert names.index("open_document") < names.index("fill_selection_from_mask")
    assert names[-1] == "close_document" and rig.photoshop.open == set()


def test_remove_objects_refuses_big_areas_and_bad_input(rig: Rig) -> None:
    big = {"name": "wall", "box": [0.0, 0.0, 0.5, 0.5]}
    with pytest.raises(DarkroomError, match="limit"):
        rig.call("remove_objects", photo_id=MASTER, regions=[big], method="inpaint")
    assert rig.photoshop.calls == []
    with pytest.raises(DarkroomError, match="different"):
        rig.call("remove_objects", photo_id=MASTER, regions=[SPOT, SPOT])
    with pytest.raises(DarkroomError):
        rig.call(
            "remove_objects", photo_id=MASTER, regions=[{"name": "x", "box": [0.5, 0, 0.4, 1]}]
        )


def test_remove_objects_without_photoshop_falls_back_or_explains(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.context.photoshop = None
    assert rig.call("remove_objects", photo_id=MASTER, regions=[SPOT])["method"] == "inpaint"
    with pytest.raises(DarkroomError) as caught:
        rig.call("remove_objects", photo_id=MASTER, regions=[SPOT], method="content_aware")
    assert caught.value.code.value == "PS_NOT_REACHABLE"


def test_fix_red_eye_writes_new_files(rig: Rig) -> None:
    result = rig.call("fix_red_eye", photo_id=MASTER, eyes=[{"box": [0.3, 0.3, 0.4, 0.4]}])
    assert Path(result["jpg_path"]).is_file() and Path(result["tiff_path"]).is_file()
    assert result["fixed_share_per_eye"] == [0.0]  # the flat picture has nothing red
    with pytest.raises(DarkroomError):
        rig.call("fix_red_eye", photo_id=MASTER, eyes=[])


def test_export_for_delivery_names_and_never_overwrites(rig: Rig) -> None:
    def deliver(**arguments: Any) -> dict[str, Any]:
        return rig.tools.call("export_for_delivery", {"photo_id": MASTER, **arguments})

    first = deliver(name="wedding-001", long_edge=1024)
    second = deliver(name="wedding-001.jpg")
    assert first["path"] != second["path"]
    assert Path(first["path"]).parent == rig.tmp_path / "exports"
    assert Path(first["path"]).is_file() and Path(second["path"]).is_file()
    for bad in ("../x", "a/b", "", ".hidden", "c:\\x"):
        with pytest.raises(DarkroomError):
            deliver(name=bad)
    with pytest.raises(DarkroomError):
        deliver(name="ok", long_edge=9000)


# -- auto_correct (Lightroom's own Auto, per photo) -------------------------------------------


def _second_photo(rig: Rig) -> str:
    rig.lightroom.add_photo("master-2", "TEST_0002.jpg")
    return "master-2"


def test_auto_correct_runs_on_one_photo_and_undo_restores(rig: Rig) -> None:
    result = rig.call("auto_correct", photo_ids=[MASTER], white_balance=True)
    item = result["items"][0]
    copy_id = item["working_copy"]
    assert result["ok"] == 1 and item["status"] == "ok" and item["verified"] is True
    assert set(item["kept"]) >= {"Exposure2012", "Contrast2012", "IncrementalTemperature"}
    assert rig.value(copy_id, "Exposure2012") == item["kept"]["Exposure2012"]
    assert item["measured"]["before"] and item["measured"]["after"]
    assert rig.lightroom.calls.count("auto_adjust") == 1
    rig.call("undo", photo_id=copy_id)
    assert rig.value(copy_id, "Exposure2012") == 0


def test_auto_correct_treats_every_photo_on_its_own(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    other = _second_photo(rig)
    result = rig.call("auto_correct", photo_ids=[MASTER, other])
    assert result["ok"] == 2
    assert rig.lightroom.calls.count("auto_adjust") == 2  # one Lightroom Auto per photo
    first, second = (item["kept"] for item in result["items"])
    assert first != second and result["identical_across_photos"] is False
    assert rig.context.confirmer.asked and "Auto" in rig.context.confirmer.asked[0]
    undone = rig.call("undo_batch", batch_id=result["batch_id"])
    assert undone["undone_steps"] == 2


def test_auto_correct_bulk_needs_a_go_ahead_and_checks_input(tmp_path: Path) -> None:
    rig = Rig(tmp_path)  # the default confirmer refuses
    other = _second_photo(rig)
    with pytest.raises(DarkroomError) as caught:
        rig.call("auto_correct", photo_ids=[MASTER, other])
    assert caught.value.code.value == "CONFIRMATION_REQUIRED"
    assert "auto_adjust" not in rig.lightroom.calls
    with pytest.raises(DarkroomError):
        rig.call("auto_correct", photo_ids=[MASTER, MASTER])
    with pytest.raises(DarkroomError):
        rig.call("auto_correct", photo_ids=[f"p{i}" for i in range(11)])
    plan = rig.call("auto_correct", photo_ids=[MASTER], dry_run=True)
    assert plan["dry_run"] is True and "auto_adjust" not in rig.lightroom.calls


def test_auto_correct_keeps_lightroom_choices_within_the_per_pass_limit(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.context.settings = make_settings(
        tmp_path, guardrails={"exposure_ev_per_pass": 0.1, "tone_per_pass": 4}
    )
    result = rig.call("auto_correct", photo_ids=[MASTER])
    item = result["items"][0]
    assert item["limited"]["Exposure2012"]["kept"] == pytest.approx(0.1) or item["limited"][
        "Exposure2012"
    ]["kept"] == pytest.approx(-0.1)
    assert abs(rig.value(item["working_copy"], "Exposure2012")) <= 0.1 + 1e-9
    assert abs(rig.value(item["working_copy"], "Contrast2012")) <= 4 + 1e-9
    assert item["chosen_by_lightroom"]["Contrast2012"] != item["kept"]["Contrast2012"]


# -- snapshots, reset, people removal, rotate, metadata, keywords, copy settings ------------------


def test_snapshot_tools_and_restore_need_a_go_ahead(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    made = rig.tools.call("create_snapshot", {"photo_id": MASTER, "name": "before look"})
    copy_id = made["photo_id"]
    rig.call("set_develop_parameters", photo_id=copy_id, changes={"Texture": 30})
    listing = rig.call("list_snapshots", photo_id=copy_id)
    assert [s["name"] for s in listing["snapshots"]] == ["before look"]
    rig.tools.call("restore_snapshot", {"photo_id": copy_id, "name": "before look"})
    assert rig.lightroom.settings[copy_id].get("Texture", 0) == 0
    with pytest.raises(DarkroomError):
        rig.tools.call(
            "restore_snapshot", {"photo_id": _second_photo(rig), "name": "before look"}
        )  # an original with no working copy
    with pytest.raises(DarkroomError):
        rig.call("restore_snapshot", photo_id=copy_id)
    with pytest.raises(DarkroomError):
        rig.tools.call("restore_snapshot", {"photo_id": copy_id, "name": "missing"})


def test_restore_snapshot_refused_without_go_ahead(rig: Rig) -> None:
    copy_id = rig.tools.call("create_snapshot", {"photo_id": MASTER, "name": "a"})["photo_id"]
    with pytest.raises(DarkroomError) as caught:
        rig.tools.call("restore_snapshot", {"photo_id": copy_id, "name": "a"})
    assert caught.value.code.value == "CONFIRMATION_REQUIRED"


def test_reset_develop_saves_a_snapshot_and_undo_restores(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    copy_id = rig.call("adjust_lighting", photo_id=MASTER, exposure=0.5)["photo_id"]
    result = rig.call("reset_develop", photo_id=copy_id, scope="all")
    assert result["result"]["snapshot"]["name"].startswith("darkroom_ai before reset")
    assert "Exposure2012" in result["result"]["before"]
    rig.call("undo", photo_id=copy_id)
    assert rig.value(copy_id, "Exposure2012") == pytest.approx(0.5)
    with pytest.raises(DarkroomError):
        rig.call("reset_develop", photo_id=copy_id, scope="everything")


def test_reset_develop_and_people_removal_refused_without_go_ahead(rig: Rig) -> None:
    for name, extra in (("reset_develop", {"scope": "masks"}), ("remove_distracting_people", {})):
        with pytest.raises(DarkroomError) as caught:
            rig.call(name, photo_id=MASTER, **extra)
        assert caught.value.code.value == "CONFIRMATION_REQUIRED"
    assert "reset_develop" not in rig.lightroom.calls
    assert "remove_distracting_people" not in rig.lightroom.calls


def test_remove_distracting_people_reports_spots_added(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    result = rig.call("remove_distracting_people", photo_id=MASTER)
    assert result["spots_added"] == 1 and result["result"]["snapshot"]
    state = rig.call("get_retouch_state", photo_id=result["photo_id"])
    assert state["state"]["spot_count"] == 1


def test_rotate_photo_turns_the_working_copy(rig: Rig) -> None:
    right = rig.call("rotate_photo", photo_id=MASTER, direction="right")
    left = rig.call("rotate_photo", photo_id=right["photo_id"], direction="left")
    assert right["rotation"]["after"] != right["rotation"]["before"]
    assert left["rotation"]["after"] == right["rotation"]["before"]
    with pytest.raises(DarkroomError):
        rig.call("rotate_photo", photo_id=MASTER, direction="up")


def test_set_photo_metadata_and_keywords(rig: Rig) -> None:
    result = rig.call("set_photo_metadata", photo_id=MASTER, title="Bride", color_label="red")
    assert result["change"]["after"] == {"title": "Bride", "color_label": "red"}
    with pytest.raises(DarkroomError):
        rig.call("set_photo_metadata", photo_id=MASTER)
    with pytest.raises(DarkroomError):
        rig.call("set_photo_metadata", photo_id=MASTER, color_label="pink")
    assert rig.call("list_keywords")["keywords"]["keywords"][0]["path"]
    assert rig.call("get_ai_panel_state", photo_id=MASTER)["state"]["panels"]


def test_copy_develop_settings_gives_every_target_the_same_values(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    other = _second_photo(rig)
    source = rig.call("adjust_lighting", photo_id=MASTER, contrast=20)["photo_id"]
    result = rig.call(
        "copy_develop_settings",
        source_photo_id=source,
        target_photo_ids=[other],
        groups=["tone"],
    )
    item = result["items"][0]
    assert item["status"] == "ok" and item["verified"] is True
    assert rig.value(item["working_copy"], "Contrast2012") == 20
    assert "CropLeft" not in result["copied_keys"]
    rig.call("undo_batch", batch_id=result["batch_id"])
    assert rig.value(item["working_copy"], "Contrast2012") == 0
    with pytest.raises(DarkroomError):
        rig.call(
            "copy_develop_settings",
            source_photo_id=source,
            target_photo_ids=[source],
            groups=["tone"],
        )


def test_copy_develop_settings_refused_without_go_ahead(rig: Rig) -> None:
    other = _second_photo(rig)
    with pytest.raises(DarkroomError) as caught:
        rig.call(
            "copy_develop_settings",
            source_photo_id=MASTER,
            target_photo_ids=[other],
            groups=["tone"],
        )
    assert caught.value.code.value == "CONFIRMATION_REQUIRED"
